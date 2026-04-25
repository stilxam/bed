# bunq Travel Buddy

A multimodal travel finance app for bunq users. Point your camera at a price tag, say a price out loud, or photograph a restaurant menu — the app extracts the amount, converts it to EUR using live exchange rates with bunq's exact fee model, and tracks it against your trip budget in real time.

---

## What it does

You're abroad and see a price in an unfamiliar currency. Instead of guessing the conversion, open bunq Travel Buddy, snap a photo or speak the price. Within seconds you know exactly what it costs in euros — including the real bunq fee for your payment method — and whether it fits your trip budget.

**Five things it solves:**

1. **Instant price conversion** — Camera, file upload, or voice. Handles any currency, any language.
2. **Exact bunq fees** — Separate fee models for card payments (ZeroFX) and transfers (Wise), not rough estimates.
3. **Live account balance** — Your actual bunq balance is always visible in the sidebar.
4. **Trip budget tracking** — Set a EUR budget for a trip. Tag bunq payments to the trip. See what's left.
5. **Local recommendations** — AI-powered suggestions for restaurants, activities, and events nearby, filtered to your remaining budget.

---

## Features

### Price extraction
- **Camera / photo upload** — Claude vision reads the price off any image: price tags, receipts, menus, handwritten signs.
- **Voice input** — Speak the price in any of 40+ languages. AWS Transcribe detects the language automatically; Amazon Nova Lite on Bedrock extracts the amount and currency from the transcript.
- **Menu extraction** — Photograph a menu and get every item with its price translated to English.
- **Disambiguation** — If the currency is ambiguous (e.g. "$"), geolocation resolves it automatically. A disambiguation widget appears only when geolocation also can't determine the currency.
- **Voice readout** — The converted EUR amount is read back via text-to-speech.

### Currency conversion
- Card payments use the live Mastercard exchange rate with a 0.5% ZeroFX surcharge.
- Transfers use the Wise mid-market rate with ~0.65% service fee and a 0.30% weekend markup.
- Falls back to ECB rates (via Frankfurter API) when primary rate sources are unavailable.
- EUR inputs pass through with zero fees and no API call.

### Trip management
- Create named trips with a EUR budget and optional travel dates.
- Set one trip as active — it appears in the sidebar on every page.
- Edit budget or delete trips from the **My Trips** page.

### Dashboard
- **Budget tracker** — Total budget, amount spent, amount remaining, and a visual progress bar.
- **Per-day breakdown** — Daily average spend and estimated days remaining at current rate.
- **Tagged payments** — Browse bunq payments associated with the trip; tag or untag individual payments.
- **Nearby recommendations** — Restaurants, cafés, bars, activities, and cultural sites near your location, ranked by relevance to your budget and the time of day. Powered by Google Maps and Claude.
- **Events tab** — Upcoming events near you via Ticketmaster, filtered to your destination.

### bunq integration
- Live balance and account list from the bunq API.
- Tag bunq payments to trips (stored locally).
- Sandbox and production modes detected automatically from the API key prefix.

---

## User journey

```
Open app
│
├── Sidebar shows bunq balance, your location, active trip, and payment type selector
│
├── Take a photo / upload image / record voice
│   │
│   └── Price extracted → converted to EUR → result card + voice readout
│
├── My Trips page
│   └── Create trip (name, budget) → set as active
│
└── Dashboard page
    ├── Budget overview (spent / remaining)
    ├── Tag bunq payments to the trip
    ├── Nearby recommendations filtered to budget
    └── Upcoming events tab
```

---

---

## Architecture

```
┌────────────────────────────────────────────────────────┐
│            app.py  (Streamlit)  /  server.py (FastAPI) │  ← Entry points
│  Sidebar: bunq balance · geo · payment type · trip     │
│  Input:   📷 Photo  |  🖼️ Upload  |  🎤 Voice          │
└──────────┬──────────────────────────┬──────────────────┘
           │                          │
           ▼                          ▼
    extractor.py              auto_voice_to_eur_aws.py
    Claude Haiku → Sonnet     sounddevice → S3 → Transcribe → Nova Lite
    Typed result union        Monetary extraction from transcript
           │                          │
           │        geo.py (IP geolocation → ISO currency, cached per process)
           │                          │
           └───────────┬──────────────┘
                       ▼
              fx_converter.py
              Card     → Mastercard Exchange Rates API + 0.5% ZeroFX
              Transfer → Wise /v1/rates + ~0.65% fee (+ 0.30% weekends)
              Fallback → ECB / Frankfurter API
                       │
                       ▼
              Result card  +  gTTS readout
                       │
              payment_tags.py (SQLite)
              trips.py (JSON)
              bunq_balance.py (live)
```

`main.py` uses the same model and FX stack as `app.py` without Streamlit — plain terminal mode with a Tkinter result window.

---

## Modules

| File | Role |
|---|---|
| `app.py` | Primary Streamlit interface. Camera/upload/voice input, result cards, disambiguation widgets, sidebar. |
| `server.py` | FastAPI REST backend + static SPA (`/static/index.html`). Endpoints: `/api/scan`, `/api/geo`, `/api/balance`, `/api/trips/*`. |
| `pages/Trips.py` | Trip management page. Create, edit, delete, activate trips. |
| `pages/Dashboard.py` | Budget tracker, tagged payments, nearby recommendations (Google Maps + Claude), events (Ticketmaster). |
| `main.py` | CLI entry point. Interactive prompts for mode/payment type; Tkinter result window. |
| `extractor.py` | Visual model. Claude Haiku (fast) escalates to Sonnet on low-confidence results. Returns typed union. |
| `auto_voice_to_eur_aws.py` | Audio pipeline. Records PCM → uploads WAV to S3 → Transcribe (40+ languages) → Nova Lite extracts amount/currency. |
| `model_interface.py` | Adapter. Converts model output to `list[tuple[float, str]]`. CLI disambiguation via terminal prompts. |
| `fx_converter.py` | FX engine. Mastercard (OAuth 1.0a RSA), Wise (Bearer), ECB fallback. Returns typed `FXResult`. |
| `bunq_balance.py` | Live bunq account balance and payment list. 60-second Streamlit cache. Note posting to payments. |
| `trips.py` | Trip CRUD. UUID-based IDs, JSON persistence in `trips.json`. |
| `payment_tags.py` | SQLite-backed trip-to-payment association with spending snapshots. |
| `geo.py` | IP geolocation via ip-api.com. Country code → ISO currency code lookup. Module-level cache. |
| `api_keys.py` | Centralised secret loading. Reads `.env`, falls back to shell environment. |
| `hyperparams.py` | All tunable parameters — TTS language, recording duration, AWS settings, Mastercard sandbox flag, Wise fee rate. |
| `call_fx_converter.py` | Thin programmatic wrapper around `fx_converter` for direct use. |
| `number_to_output.py` | Tkinter result card + gTTS (CLI mode). |

---

## Running

### Streamlit app (primary)

```bash
streamlit run app.py
```

Opens the phone-like web UI at `http://localhost:8501`. Camera, upload, and voice input available. Sidebar shows bunq balance, geolocation, payment type selector, and active trip.

### FastAPI server + SPA

```bash
uvicorn server:app --reload --port 8000
```

Serves the static SPA from `/static/index.html`. OpenAPI docs at `http://localhost:8000/api/docs`.

### Terminal pipeline

```bash
python main.py
```

Prompts for mode (visual / audio) and payment type, then opens a Tkinter result window.

---

## Configuration

### API keys — `.env`

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
TTS_LANGUAGE = "fr"                   # Language for voice readout
RECORD_SECONDS = 8                    # Mic recording duration (CLI)
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

When currency is ambiguous, `geo.py` resolves it from the user's IP. If geolocation also fails, a disambiguation widget appears in the UI.

---

## Audio model

`auto_voice_to_eur_aws.py` is a self-contained AWS pipeline:

1. **sounddevice** records raw PCM from the microphone.
2. WAV is uploaded to S3.
3. **Amazon Transcribe** transcribes with automatic language detection (40+ languages). Set `TRANSCRIBE_LANGUAGE_OPTIONS` to improve accuracy for known languages.
4. **Amazon Nova Lite** (Bedrock) extracts the monetary amount and ISO currency code from the transcript.
5. Missing currency is resolved via `geo.py` before FX conversion.

The local WAV file is deleted after upload.

---

## Trips and payment tracking

Users create trips from the **My Trips** page. Each trip has a `name`, UUID `id`, and `budget_eur`. Trip metadata is stored in `trips.json`.

`payment_tags.py` links bunq payment IDs to trip IDs in SQLite (`payment_tags.db`), with a spending snapshot for each tagged payment. The Dashboard aggregates these to compute total spend and remaining budget without re-fetching from bunq on every render.

---

## bunq integration

- **Authentication** — `hackathon_toolkit/bunq_client.py` handles the three-step RSA auth flow (installation → device → session). Session context is cached in `bunq_context.json`.
- **Balance** — `bunq_balance.py` reads all active accounts via `MonetaryAccountBank`. Primary balance is shown in the sidebar; full account list in an expander. Cache TTL: 60 seconds.
- **Payments** — Payment list with date filtering. Individual payments can be tagged to trips and annotated with a note.
- **Sandbox detection** — Automatic: `BUNQ_API_KEY` starting with `sandbox_` routes all calls to the bunq sandbox.

---

## Project structure

```
hackathon_main/
├── app.py                      # Streamlit app — primary interface
├── server.py                   # FastAPI REST backend + static SPA
├── main.py                     # CLI entry point
├── pages/
│   ├── Trips.py                # Trip management page
│   └── Dashboard.py            # Budget tracker, recommendations, events
├── trips.py                    # Trip model + JSON persistence
├── payment_tags.py             # SQLite trip-to-payment mapping
├── model_interface.py          # Model output → [(amount, currency)]
├── extractor.py                # Visual model (Claude)
├── auto_voice_to_eur_aws.py    # Audio model (AWS)
├── fx_converter.py             # FX conversion + fee model
├── bunq_balance.py             # Live bunq balance + payments
├── geo.py                      # IP geolocation → currency
├── geo1.py                     # Recommendation engine (Maps + Claude)
├── call_fx_converter.py        # Programmatic FX wrapper
├── number_to_output.py         # Tkinter display + TTS (CLI)
├── api_keys.py                 # Centralised secret loading
├── hyperparams.py              # All tunable parameters
├── static/
│   └── index.html              # SPA for FastAPI server
├── trips.json                  # Persisted trip data (auto-created)
├── payment_tags.db             # SQLite spending data (auto-created)
├── bunq_context.json           # Cached bunq session (auto-created)
├── .env                        # API keys (not committed)
├── pyproject.toml
├── requirements.txt
├── flake.nix                   # Nix reproducible dev environment
└── hackathon_toolkit/          # bunq API reference scripts
    ├── bunq_client.py
    └── ...
```
