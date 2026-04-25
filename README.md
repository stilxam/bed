# bunq Travel Buddy

A multimodal travel finance app for bunq users. Point your camera at a price tag, say a price out loud, or photograph a restaurant menu — the app extracts the amount, converts it to EUR using live exchange rates with bunq's exact fee model, and tracks it against your trip budget in real time.

---

## What it does

You're abroad and see a price in an unfamiliar currency. Instead of guessing the conversion, open bunq Travel Buddy, snap a photo or speak the price. Within seconds you know exactly what it costs in euros — including the real bunq fee for your payment method — and whether it fits your trip budget.

**Five things it solves:**

1. **Instant price conversion** — Camera, file upload, or voice. Handles any currency, any language.
2. **Exact bunq fees** — Separate fee models for card payments (ZeroFX) and transfers (Wise), not rough estimates.
3. **Live account balance** — Your actual bunq balance is always visible.
4. **Trip budget tracking** — Set a EUR budget for a trip. Tag bunq payments to the trip. See what's left.
5. **Local recommendations** — AI-powered suggestions for restaurants, activities, and events nearby, filtered to your remaining budget.

---

## Features

### Price extraction
- **Camera / photo upload** — Claude vision reads the price off any image: price tags, receipts, menus, handwritten signs.
- **Voice input** — Speak the price in any of 40+ languages. AWS Transcribe detects the language automatically; Amazon Nova Lite on Bedrock extracts the amount and currency from the transcript.
- **Menu extraction** — Photograph a menu and get every item with its price translated to English and converted to EUR.
- **Disambiguation** — If the currency is ambiguous (e.g. "$"), geolocation resolves it automatically.

### Currency conversion
- Card payments use the live Mastercard exchange rate with a 0.5% ZeroFX surcharge.
- Transfers use the Wise mid-market rate with ~0.65% service fee and a 0.30% weekend markup.
- Falls back to ECB rates (via Frankfurter API) when primary rate sources are unavailable.
- EUR inputs pass through with zero fees and no API call.

### Trip management
- Create named trips with a EUR budget and optional travel dates.
- Set a trip's destination currency to unlock destination-aware recommendations and events.
- Edit budget or delete trips from the Trips view.

### Dashboard
- **Budget tracker** — Total budget, amount spent, amount remaining.
- **Tagged payments** — Browse bunq payments associated with the trip.
- **Nearby recommendations** — Restaurants, cafes, bars, activities, and cultural sites near your location, ranked by relevance to your budget and the time of day. Powered by Google Maps and Claude.
- **Events tab** — Upcoming events near you via Ticketmaster, filtered to your destination.

### bunq integration
- Live balance and account list from the bunq API.
- Tag bunq payments to trips (stored locally in SQLite).
- Sandbox and production modes detected automatically from the API key prefix.

---

## Running

```bash
uvicorn server:app --reload --port 8000
```

Opens the web UI at `http://localhost:8000`. OpenAPI docs at `http://localhost:8000/api/docs`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│              static/index.html  (SPA)                   │  <- UI
│  Input: Photo | Upload | Voice   Tabs: Home / Explore   │
└──────────┬──────────────────────────┬───────────────────┘
           │ POST /api/scan           │ POST /api/voice
           │ POST /api/menu           │
           ▼                          ▼
    extractor.py              auto_voice_to_eur_aws.py
    Claude Haiku → Sonnet     WAV → S3 → Transcribe → Nova Lite
    Typed result union        Monetary extraction from transcript
           │                          │
           │     geo.py  (IP geolocation → ISO currency, cached)
           │                          │
           └───────────┬──────────────┘
                       ▼
              fx_converter.py
              Card     → Mastercard Exchange Rates API + 0.5% ZeroFX
              Transfer → Wise /v1/rates + ~0.65% fee (+ 0.30% weekends)
              Fallback → ECB / Frankfurter API
                       │
              payment_tags.py (SQLite)
              trips.py (JSON)
              bunq_balance.py (live)
```

`server.py` is the single entry point — it hosts the REST API and serves the SPA.

---

## Modules

| File | Role |
|---|---|
| `server.py` | FastAPI backend. All REST endpoints. Serves `static/index.html`. |
| `static/index.html` | Single-page app. Camera/upload/voice input, trip management, dashboard, explore. |
| `extractor.py` | Visual model. Claude Haiku (fast) escalates to Sonnet on low-confidence results. Returns typed union. |
| `auto_voice_to_eur_aws.py` | Audio pipeline. Records/uploads WAV → S3 → Transcribe (40+ languages) → Nova Lite extracts amount/currency. |
| `fx_converter.py` | FX engine. Mastercard (OAuth 1.0a RSA), Wise (Bearer), ECB fallback. Returns typed `FXResult`. |
| `bunq_balance.py` | Live bunq account balance and payment list. |
| `trips.py` | Trip CRUD. UUID-based IDs, JSON persistence in `trips.json`. |
| `payment_tags.py` | SQLite-backed trip-to-payment association with spending snapshots. |
| `geo.py` | IP geolocation via ip-api.com. Country code → ISO currency code lookup. Module-level cache. |
| `recommendations.py` | Claude + Google Maps place recommendations, filtered by budget and time of day. |
| `events_around.py` | Ticketmaster event discovery helpers. |
| `api_keys.py` | Centralised secret loading. Reads `.env`, falls back to shell environment. |
| `hyperparams.py` | All tunable parameters — AWS settings, Mastercard sandbox flag, Wise fee rate. |
| `setup_sandbox.py` | bunq sandbox environment setup script. |

---

## Configuration

### API keys — `.env`

Copy `.env.example` to `.env` and fill in your keys:

```
ANTHROPIC_API_KEY=           # Claude visual extractor
BUNQ_API_KEY=                # sandbox_ prefix → sandbox mode automatically
AWS_ACCESS_KEY_ID=           # Transcribe + Bedrock Nova Lite + S3
AWS_SECRET_ACCESS_KEY=
AWS_SESSION_TOKEN=           # Only for temporary / STS credentials
AWS_DEFAULT_REGION=us-east-1
MASTERCARD_CONSUMER_KEY=     # Mastercard Exchange Rates API (optional)
MASTERCARD_PRIVATE_KEY_PATH= # Path to PEM private key (from .p12)
WISE_API_KEY=                # Wise Platform API — sandbox or live (optional)
GOOGLE_MAPS_API_KEY=         # Place search for recommendations
TICKETMASTER_API_KEY=        # Event discovery
```

Mastercard and Wise are optional — the app falls back to ECB rates when absent.

### Hyperparameters — `hyperparams.py`

```python
AWS_S3_BUCKET = "your-bucket"         # S3 bucket for audio uploads
TRANSCRIBE_LANGUAGE_OPTIONS = []      # Empty = auto-detect all 40+ languages
MASTERCARD_USE_SANDBOX = True         # False → Mastercard production endpoint
WISE_SERVICE_FEE_RATE = 0.0065        # ~0.65% Wise service fee
DEFAULT_ISO_COUNTRY_CODE = None       # Fallback if geo API unavailable
```

---

## FX / fee model

### Card payments (bunq ZeroFX)

- **Rate source:** Mastercard Exchange Rates API (OAuth 1.0a, RSA-SHA256 signed).
- **Fee:** 0.50% ZeroFX surcharge on the converted EUR amount.
- **No weekend markup** — ZeroFX rate is locked at card swipe time.
- **Fallback:** ECB mid-market rate via Frankfurter API (free, no key needed).

### Transfers (Wise)

- **Rate source:** Wise `/v1/rates` (Bearer token).
- **Fee:** `WISE_SERVICE_FEE_RATE` (~0.65%) + 0.30% additional on weekends.
- **Fallback:** ECB mid-market rate if Wise is unavailable.

EUR inputs pass through unchanged.

---

## Visual model

`extractor.py` uses a two-model strategy:

1. **Claude Haiku** makes the initial extraction (fast, cheap).
2. If the result has low confidence on amount or currency, **Claude Sonnet** retries.

Output is a typed union: `SuccessResult | AmountAmbiguousResult | CurrencyAmbiguousResult | FailureResult`.

When currency is ambiguous, `geo.py` resolves it from the user's IP.

---

## Audio model

`auto_voice_to_eur_aws.py` is a self-contained AWS pipeline:

1. Audio is uploaded to S3.
2. **Amazon Transcribe** transcribes with automatic language detection (40+ languages). Set `TRANSCRIBE_LANGUAGE_OPTIONS` to improve accuracy for known languages.
3. **Amazon Nova Lite** (Bedrock) extracts the monetary amount and ISO currency code from the transcript.
4. Missing currency is resolved via `geo.py` before FX conversion.

---

## Trips and payment tracking

Users create trips with a name, EUR budget, and optional destination currency. Trip metadata is stored in `trips.json`.

`payment_tags.py` links bunq payment IDs to trip IDs in SQLite (`payment_tags.db`), with a spending snapshot for each tagged payment. The dashboard aggregates these to compute total spend and remaining budget without re-fetching from bunq on every render.

---

## bunq integration

- **Authentication** — `hackathon_toolkit/bunq_client.py` handles the three-step RSA auth flow (installation → device → session). Session context is cached in `bunq_context.json`.
- **Balance** — `bunq_balance.py` reads all active accounts via `MonetaryAccountBank`.
- **Payments** — Payment list with date filtering. Individual payments can be tagged to trips.
- **Sandbox detection** — Automatic: `BUNQ_API_KEY` starting with `sandbox_` routes all calls to the bunq sandbox.

---

## Project structure

```
.
├── server.py                   # FastAPI backend + REST API
├── static/
│   └── index.html              # Single-page app (SPA)
├── extractor.py                # Visual model (Claude)
├── auto_voice_to_eur_aws.py    # Audio model (AWS)
├── fx_converter.py             # FX conversion + fee model
├── bunq_balance.py             # Live bunq balance + payments
├── recommendations.py          # AI place recommendations (Maps + Claude)
├── events_around.py            # Ticketmaster event discovery
├── trips.py                    # Trip model + JSON persistence
├── payment_tags.py             # SQLite trip-to-payment mapping
├── geo.py                      # IP geolocation → currency
├── api_keys.py                 # Centralised secret loading
├── hyperparams.py              # All tunable parameters
├── setup_sandbox.py            # bunq sandbox setup
├── trips.json                  # Persisted trip data (auto-created)
├── payment_tags.db             # SQLite spending data (auto-created)
├── bunq_context.json           # Cached bunq session (auto-created)
├── .env                        # API keys (not committed)
├── .env.example                # API key template
├── pyproject.toml
├── requirements.txt
├── flake.nix                   # Nix reproducible dev environment
└── hackathon_toolkit/          # bunq API reference scripts
```
