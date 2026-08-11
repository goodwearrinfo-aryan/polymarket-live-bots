# Keyless / Free API Reference

Curated for the paper-trading bot's "find the keyless path" rule. Sourced from
[public-apis/public-apis](https://github.com/public-apis/public-apis) plus the
no-key endpoints already proven in this project (BRAIN/memory).

**Legend:** `[IN USE]` = the bot already consumes it · `[NEW]` = catalog
candidate worth wiring · `verify` = confirm current key/limit policy before trusting.

> Honesty note: APIs change auth policy. "Keyless" below means *no key as of last
> known check*. Anything tagged `verify` may have moved to a free-key tier.

---

## 1. Crypto / spot price & market data (TRUE keyless)
| API | Use | Endpoint | Notes |
|-----|-----|----------|-------|
| **CoinGecko** `[IN USE]` | prices, 24h change (diverg leg) | `https://api.coingecko.com/api/v3` | free tier no key; rate-limited (~10–30/min) |
| **Binance** `[IN USE]` | klines/candles (candle logger) | `https://api.binance.com/api/v3/klines` | public market data, no key; ccxt fallback |
| **Deribit** `[IN USE]` | options IV / skew (deribit_vol.py) | `https://www.deribit.com/api/v2` (public/) | VRP/skew keyless |
| **Coinpaprika** `[NEW]` | prices, volume, market detail | `https://api.coinpaprika.com/v1` | keyless free tier |
| **CoinCap** `[NEW] verify` | real-time price/market | `https://api.coincap.io/v2` | was no-key; may now want a free key |
| **alternative.me** `[IN USE]` | Fear & Greed index (feargreed leg) | `https://api.alternative.me/fng/` | keyless, cache 1h |

## 2. Prediction markets (TRUE keyless reads)
| API | Use | Endpoint | Notes |
|-----|-----|----------|-------|
| **Polymarket Gamma** `[IN USE]` | market metadata | `https://gamma-api.polymarket.com` | keyless; drops resolved markets |
| **Polymarket CLOB** `[IN USE]` | prices incl. resolved | `https://clob.polymarket.com` | keyless; prices what gamma drops |
| **Kalshi** `[IN USE]` `verify` | cross-venue compare | `https://api.elections.kalshi.com` | public reads ok; some need login |

## 3. Finance / macro (TRUE keyless)
| API | Use | Endpoint | Notes |
|-----|-----|----------|-------|
| **SEC EDGAR** `[NEW]` | US filings / fundamentals | `https://data.sec.gov` | keyless but REQUIRES a `User-Agent` header |
| **OpenFIGI** `[NEW]` | Bloomberg symbology | `https://api.openfigi.com/v3` | keyless; key only raises limits |
| **econdb** `[NEW]` | global macro series | `https://www.econdb.com/api` | keyless |
| **Frankfurter** `[NEW]` | ECB FX rates | `https://api.frankfurter.app` | keyless, no limits stated |

## 4. Settled-data feeds (objective resolution — the only edge type that beat this market)
| API | Use | Endpoint | Notes |
|-----|-----|----------|-------|
| **IMF PortWatch** `[IN USE]` | chokepoint traffic (Hormuz NO) | `https://portwatch.imf.org` (ArcGIS) | keyless; first data-confirmed edge |
| **BLS** `[IN USE]` | CPI / jobs (data gate) | `https://api.bls.gov/publicAPI/v2` | v1 keyless; v2 optional free key |
| **ESPN (hidden)** `[IN USE]` | all-sports results | `https://site.api.espn.com/apis/site/v2/sports` | keyless results feed |
| **Open-Meteo** `[NEW]` | weather (weather legs) | `https://api.open-meteo.com/v1/forecast` | fully keyless, generous |
| **USGS** `[NEW]` | earthquakes | `https://earthquake.usgs.gov/fdsnws/event/1/` | keyless |
| **Nager.Date** `[NEW]` | public holidays 90+ countries | `https://date.nager.at/api` | keyless |

## 5. News / attention (TRUE keyless, used by attention legs)
| API | Use | Endpoint | Notes |
|-----|-----|----------|-------|
| **GDELT** `[IN USE]` | news catalyst drift (newsmove) | `https://api.gdeltproject.org/api/v2` | keyless |
| **Wikipedia pageviews** `[IN USE]` | wikivol leg | `https://wikimedia.org/api/rest_v1/metrics/pageviews` | keyless |
| **Reddit** `[IN USE]` | redditbuzz leg | `https://www.reddit.com/r/<sub>/.json` | keyless, rate-limited; set User-Agent |
| **YouTube search scrape** `[IN USE]` | ytbuzz leg | (HTML scrape, no API) | no key |
| **hermes RSS** `[IN USE]` | catalyst feed | `http://localhost:48580` | local keyless RSS |

## 6. AI / LLM inference (keyless, already wired)
| Path | Use | How | Notes |
|------|-----|-----|-------|
| **Local Ollama** `[IN USE]` | thrift-agent free inference | `ollama run llama3.2:3b "..." 2>/dev/null` or `http://localhost:11434/api/generate` | $0, offline, private floor |
| **NVIDIA NIM** `[IN USE]` | strong free LLM (70B) | via `llm_client.py` (LLM_PROVIDER=nvidia) | free build.nvidia.com tier; needs free `NVIDIA_API_KEY` |
| **lmarena** `[IN USE]` | AI leaderboard resolution | scrape | keyless |

---

## Free but NEEDS a free key (NOT keyless — register once)
- **FRED** (St. Louis Fed macro) — `https://api.stlouisfed.org/fred` — free key required.
- **Alpha Vantage / FMP** — equity fundamentals — free key, tight limits (the stock agents avoid these via yfinance).
- **NVIDIA NIM** — free but a `NVIDIA_API_KEY` is needed for the cloud tier (Ollama is the keyless fallback).

## Catalog sources to re-scan periodically
- https://github.com/public-apis/public-apis — the canonical list (filter Auth = No).
- https://github.com/marcelscruz/public-apis — actively-maintained fork.
- https://github.com/public-api-lists/public-api-lists — alternative list.

_Last curated: 2026-06-20._
