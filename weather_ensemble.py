"""
weather_ensemble.py — GFS 31-member ensemble forecast for weatherno leg.

Ported from suislanchez/polymarket-kalshi-weather-bot (weather.py).
Uses Open-Meteo Ensemble API (free, no API key).

Replaces the static price-threshold in weatherno with a real model probability:
  edge = model_prob - market_yes_price
  Buy NO when market_yes_price > model_prob + min_edge
"""

import time
import json
import os
import statistics
import urllib.request
import urllib.parse
import logging
from datetime import date, datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

CACHE_FILE = os.path.expanduser("~/Documents/polymarket/scalp_lab_cache.json")
CACHE_TTL  = 900   # 15 min — forecasts don't change fast

# lat/lon for cities mentioned in Polymarket weather markets
CITY_CONFIG = {
    "nyc":         {"name": "New York",    "lat": 40.7128, "lon": -74.0060},
    "new york":    {"name": "New York",    "lat": 40.7128, "lon": -74.0060},
    "chicago":     {"name": "Chicago",     "lat": 41.8781, "lon": -87.6298},
    "miami":       {"name": "Miami",       "lat": 25.7617, "lon": -80.1918},
    "los angeles": {"name": "Los Angeles", "lat": 34.0522, "lon": -118.2437},
    "la ":         {"name": "Los Angeles", "lat": 34.0522, "lon": -118.2437},
    "dallas":      {"name": "Dallas",      "lat": 32.7767, "lon": -96.7970},
    "houston":     {"name": "Houston",     "lat": 29.7604, "lon": -95.3698},
    "phoenix":     {"name": "Phoenix",     "lat": 33.4484, "lon": -112.0740},
    "seattle":     {"name": "Seattle",     "lat": 47.6062, "lon": -122.3321},
    "denver":      {"name": "Denver",      "lat": 39.7392, "lon": -104.9903},
    "atlanta":     {"name": "Atlanta",     "lat": 33.7490, "lon": -84.3880},
    "boston":      {"name": "Boston",      "lat": 42.3601, "lon": -71.0589},
    "las vegas":   {"name": "Las Vegas",   "lat": 36.1699, "lon": -115.1398},
}


def _load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass


def fetch_ensemble(lat: float, lon: float, target_date: date) -> Optional[dict]:
    """
    Calls Open-Meteo Ensemble API (GFS seamless, 31 members).
    Returns {member_highs: [...], member_lows: [...], mean_high, std_high,
             mean_low, std_low, num_members} or None.
    Cached for CACHE_TTL seconds.
    """
    cache = _load_cache()
    cache_key = f"ensemble_{lat:.2f}_{lon:.2f}_{target_date.isoformat()}"
    entry = cache.get(cache_key, {})
    if entry and time.time() - entry.get("ts", 0) < CACHE_TTL:
        return entry["data"]

    params = urllib.parse.urlencode({
        "latitude":         lat,
        "longitude":        lon,
        "daily":            "temperature_2m_max,temperature_2m_min",
        "temperature_unit": "fahrenheit",
        "start_date":       target_date.isoformat(),
        "end_date":         target_date.isoformat(),
        "models":           "gfs_seamless",
    })
    url = f"https://ensemble-api.open-meteo.com/v1/ensemble?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "scalp_lab/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())

        daily = data.get("daily", {})
        member_highs, member_lows = [], []
        for key, values in daily.items():
            if not isinstance(values, list) or not values:
                continue
            val = values[0]
            if val is None:
                continue
            if "temperature_2m_max" in key:
                member_highs.append(float(val))
            elif "temperature_2m_min" in key:
                member_lows.append(float(val))

        if not member_highs:
            return None

        result = {
            "member_highs": member_highs,
            "member_lows":  member_lows,
            "mean_high":    statistics.mean(member_highs),
            "std_high":     statistics.stdev(member_highs) if len(member_highs) > 1 else 0.0,
            "mean_low":     statistics.mean(member_lows) if member_lows else 0.0,
            "std_low":      statistics.stdev(member_lows) if len(member_lows) > 1 else 0.0,
            "num_members":  len(member_highs),
        }
        cache[cache_key] = {"ts": time.time(), "data": result}
        _save_cache(cache)
        return result

    except Exception as e:
        log.warning(f"weather_ensemble fetch error: {e}")
        return None


def _parse_threshold(question: str) -> Optional[float]:
    """
    Extract temperature threshold from a Polymarket question.
    Handles: "above 75°F", "below 90 degrees", "exceed 80F", "reach 95 fahrenheit"
    Returns float in Fahrenheit, or None.
    """
    import re
    q = question.lower()
    # Convert Celsius mentions to Fahrenheit
    m = re.search(r'(\d+(?:\.\d+)?)\s*°?c\b', q)
    if m:
        return float(m.group(1)) * 9 / 5 + 32
    # Fahrenheit
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:°f|f\b|degrees|fahrenheit)', q)
    if m:
        return float(m.group(1))
    # Bare number after "above", "below", "exceed", "reach", "over", "under"
    m = re.search(r'(?:above|below|exceed|reach|over|under|hit)\s+(\d+(?:\.\d+)?)', q)
    if m:
        return float(m.group(1))
    return None


def _parse_direction(question: str) -> str:
    """Returns 'above' or 'below' based on question wording."""
    q = question.lower()
    if any(k in q for k in ("above", "exceed", "over", "at least", "reach", "high")):
        return "above"
    return "below"


def _parse_date(question: str) -> date:
    """
    Try to extract a date from the question.
    Falls back to tomorrow if nothing found.
    """
    import re
    q = question.lower()
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    # "June 15", "July 4th", etc.
    for name, num in months.items():
        m = re.search(rf'{name}\s+(\d{{1,2}})', q)
        if m:
            day = int(m.group(1))
            year = date.today().year
            try:
                d = date(year, num, day)
                if d < date.today():
                    d = date(year + 1, num, day)
                return d
            except ValueError:
                pass
    return date.today() + timedelta(days=1)


def weatherno_signal(market_question: str, market_yes_price: float,
                     min_edge: float = 0.06) -> Optional[dict]:
    """
    Main entry point for scalp_lab weatherno leg.

    Returns {side, model_prob, edge, confidence, reason} if a signal exists,
    or None if no city match / no forecast / edge too small.

    min_edge: minimum model_prob vs market_price gap to fire.
    """
    q = market_question.lower()

    # Find city
    city_key = None
    city_cfg = None
    for key, cfg in CITY_CONFIG.items():
        if key in q:
            city_key = key
            city_cfg = cfg
            break
    if not city_cfg:
        return None

    threshold_f = _parse_threshold(market_question)
    if threshold_f is None:
        return None

    direction = _parse_direction(market_question)
    target_dt = _parse_date(market_question)

    forecast = fetch_ensemble(city_cfg["lat"], city_cfg["lon"], target_dt)
    if not forecast:
        return None

    # Compute model probability
    highs = forecast["member_highs"]
    if direction == "above":
        above = sum(1 for h in highs if h > threshold_f)
        model_prob = above / len(highs)
    else:
        below = sum(1 for h in highs if h < threshold_f)
        model_prob = below / len(highs)

    # Clip extremes — ensemble can be unanimous but don't bet 100%
    model_prob = max(0.05, min(0.95, model_prob))

    edge = market_yes_price - model_prob   # positive = market overprices YES → buy NO

    if edge < min_edge:
        return None

    # Ensemble agreement (confidence)
    above_count = sum(1 for h in highs if h > threshold_f)
    agreement = max(above_count, len(highs) - above_count) / len(highs)

    return {
        "side":       "NO",
        "model_prob": round(model_prob, 3),
        "edge":       round(edge, 3),
        "confidence": round(agreement, 3),
        "reason":     (f"ensemble {forecast['num_members']}m: "
                       f"{city_cfg['name']} high {direction} {threshold_f:.0f}F on {target_dt} | "
                       f"model={model_prob:.0%} mkt={market_yes_price:.0%} edge={edge:+.0%}"),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    tests = [
        ("Will the high temperature in NYC be above 85°F on June 20?",  0.72),
        ("Will Chicago temperatures exceed 90 degrees on July 4th?",    0.65),
        ("Will Miami high reach 95F on August 1?",                      0.58),
    ]
    for q, yes_price in tests:
        sig = weatherno_signal(q, yes_price)
        print(f"{q[:60]}")
        print(f"  → {sig}")
        print()
