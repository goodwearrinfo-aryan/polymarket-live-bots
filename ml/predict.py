#!/usr/bin/env python3
"""
ml/predict.py — score a LIVE Polymarket market with the trained model.

Returns P(probability moves >5% in the next 24h). Importable by the bot later:
    from ml.predict import score_market
    p = score_market(condition_id)   # float 0..1, or None if unavailable

CLI: python3 predict.py <condition_id>
Fail-soft: returns None on any error (missing model, network, etc.).
"""
import os, sys, json, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import features as F  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
MODEL = os.path.join(HERE, "model.pkl")
UA = "polymarket-ml-predict/1.0"
_model = None


def _get(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _load_model():
    global _model
    if _model is None:
        try:
            import pickle
            with open(MODEL, "rb") as f:
                _model = pickle.load(f)["model"]
        except Exception as e:
            sys.stderr.write(f"model load failed: {e}\n")
            _model = False
    return _model or None


def _hist(token_id):
    d = _get(f"{CLOB}/prices-history", {"market": token_id, "interval": "max", "fidelity": 60})  # MATCH training (was "1w" — train/serve skew)
    hist = (d or {}).get("history", []) if isinstance(d, dict) else []
    return sorted([(int(p["t"]), float(p["p"])) for p in hist if "t" in p and "p" in p])


def score_market(condition_id, news_sentiment=0.0):
    model = _load_model()
    if not model:
        return None
    mkts = _get(f"{GAMMA}/markets", {"condition_ids": condition_id})
    mkt = (mkts[0] if isinstance(mkts, list) and mkts else
           (mkts.get("markets", [{}])[0] if isinstance(mkts, dict) else None))
    if not mkt:
        return None
    try:
        tokens = mkt.get("clobTokenIds")
        tokens = json.loads(tokens) if isinstance(tokens, str) else tokens
        token = str(tokens[0])
    except Exception:
        return None
    series = _hist(token)
    if len(series) < 6:
        return None
    from datetime import datetime
    try:
        resolve = int(datetime.fromisoformat(str(mkt.get("endDate")).replace("Z", "+00:00")).timestamp())
    except Exception:
        resolve = int(time.time()) + 24 * 3600
    vol = float(mkt.get("volumeNum") or mkt.get("volume") or 0)
    liq = float(mkt.get("liquidityNum") or mkt.get("liquidity") or 0)
    feats = F.compute(series, int(time.time()), resolve, vol, liq, news_sentiment)
    if not feats:
        return None
    return float(model.predict_proba([F.row_vector(feats)])[0][1])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: predict.py <condition_id>")
    p = score_market(sys.argv[1])
    print(f"P(move>5% in 24h) = {p:.3f}" if p is not None else "unavailable (model trained? network?)")
