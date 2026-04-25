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
- Set one trip as active — it appears across every page of the SPA.
- Edit budget or delete trips from the trips view.

### Dashboard
- **Budget tracker** — Total budget, amount spent, amount remaining, and a visual progress bar.
- **Per-day breakdown** — Daily average spend and estimated days remaining at current rate.
- **Tagged payments** — Browse bunq payments associated with the trip; tag or untag individual payments.
- **Card Activity** — Settled Mastercard transactions with the actual FX rates used at swipe time.
- **Budget Guard** — Set a daily card spending limit on your bunq card directly from the app.
- **BunqMe Split** — Generate a shareable bunq.me link to split a travel expense.
- **Nearby recommendations** — Restaurants, cafés, bars, activities, and cultural sites near your location, ranked by relevance to your budget and the time of day. Powered by Google Maps and Claude.
- **Events tab** — Upcoming events near you (or at your trip's destination) via Ticketmaster.

### bunq integration
- Live balance and account list from the bunq API.
- Tag bunq payments to trips (stored locally in SQLite).
- Sandbox and production modes detected automatically from the API key prefix.
- Optional webhook receiver auto-syncs card transactions in real time.

---

## Note on payments appearing in the app

The Payment Log, Card Activity, and tagged-payments views all read from the bunq API using `BUNQ_API_KEY`. Payments will only show up once your bunq Travel Buddy / Bunqmate app is **registered with the bunq API ecosystem** and an active API key is in place.

This was validated end-to-end internally using the bunq sandbox. If you're a judge or reviewer reproducing the demo, the fastest path is the sandbox — see the next two scripts.

### Quick connection check

```bash
python connect_bunq.py
```

`connect_bunq.py` is a tiny script that:
1. Loads `BUNQ_API_KEY` from `.env` (or creates a fresh sandbox user if missing).
2. Authenticates against bunq (sandbox or production based on the key prefix).
3. Prints your IBAN, current balance, and the last 5 payments.

If those payments print, the dashboard's Payment Log will display them too.

### Seed sandbox data for the demo

```bash
python setup_sandbox.py            # reuse the sandbox key in .env if present
python setup_sandbox.py --fresh    # force a brand-new sandbox user
```

`setup_sandbox.py` creates (or reuses) a sandbox user, funds it via `sugardaddy@bunq.com`, and writes 20 realistic Tokyo travel payments so every dashboard tab has data immediately.

A smaller, faster seeder is also available:

```bash
python seed_transactions.py        # 15 small EUR debits, ~30 seconds
```

### Going live

For production, register the app in the bunq developer portal, generate a non-sandbox API key, and replace `BUNQ_API_KEY` in `.env`. The auto-detection in `bunq_balance.py` switches all calls to the production endpoint without any other code changes.

---

## User journey

```
Open app
│
├── Sidebar / header shows bunq balance, location, active trip, payment type selector
│
├── Take a photo / upload image / record voice
│   │
│   └── Price extracted → converted to EUR → result card + voice readout
│
├── Trips view
│   └── Create trip (name, budget, dates) → set as active
│
└── Dashboard view
    ├── Budget overview (spent / remaining)
    ├── Tag bunq payments to the trip
    ├── Card Activity (settled Mastercard transactions)
    ├── Budget Guard (set daily card limit)
    ├── BunqMe Split (generate share link)
    ├── Nearby recommendations filtered to budget
    └── Upcoming events tab
```

---

## Architecture

```
┌────────────────────────────────────────────────────────┐
│              server.py  (FastAPI + static SPA)         │  ← Entry point
│  Header: bunq balance · geo · payment type · trip      │
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
              Result card  +  TTS readout
                       │
              payment_tags.py  (SQLite — trip ↔ payment links)
              trips.py         (JSON  — trip definitions)
              bunq_balance.py  (live  — accounts, payments, cards, BunqMe)
```

---

## Modules

| File | Role |
|---|---|
| `server.py` | FastAPI REST backend + static SPA (`/static/index.html`). Endpoints: `/api/scan`, `/api/menu`, `/api/voice`, `/api/convert`, `/api/geo`, `/api/balance`, `/api/trips/*`, `/api/events`, `/api/recommendations`, `/api/card-limit`, `/api/split`, `/api/bunq/webhook`. |
| `static/index.html` | Single-page front-end consumed by `server.py`. Camera/upload/voice input, result cards, trips, dashboard, recommendations, events. |
| `extractor.py` | Visual model. Claude Haiku (fast) escalates to Sonnet on low-confidence results. Returns typed union: `SuccessResult \| AmountAmbiguousResult \| CurrencyAmbiguousResult \| FailureResult`. Also `extract_menu_from_image`. |
| `auto_voice_to_eur_aws.py` | Audio pipeline. Records PCM → uploads WAV to S3 → AWS Transcribe (40+ languages) → Bedrock Nova Lite extracts amount/currency. |
| `fx_converter.py` | FX engine. Mastercard (OAuth 1.0a RSA), Wise (Bearer), ECB fallback. Returns typed `FXResult`. |
| `bunq_balance.py` | bunq API surface used by the app: balance, accounts, payments, Mastercard actions, cards, daily-limit setter, BunqMe link creation, payment-note posting. |
| `trips.py` | Trip CRUD. UUID-based IDs, JSON persistence in `trips.json`. |
| `payment_tags.py` | SQLite-backed trip-to-payment association with snapshotted payment metadata. |
| `geo.py` | IP geolocation via ip-api.com. Country code → ISO currency code lookup. Module-level cache. |
| `recommendations.py` | Google Maps + Claude recommendation engine (`get_nearby_recommendations`, `GeoContext`, `BudgetInfo`). |
| `events_around.py` | Ticketmaster Discovery integration helpers. |
| `api_keys.py` | Centralised secret loading. Reads `.env`, falls back to shell environment. |
| `hyperparams.py` | All tunable parameters — TTS language, recording duration, AWS settings, Mastercard sandbox flag, Wise fee rate. |
| `setup_sandbox.py` | Bootstrap a sandbox user, fund it, and create 20 realistic travel payments. |
| `seed_transactions.py` | Lightweight alternative — 15 small EUR debits to populate the Payment Log quickly. |
| `connect_bunq.py` | Tiny connection-verification script. Authenticates and lists 5 recent payments. |
| `hackathon_toolkit/bunq_client.py` | Three-step bunq RSA auth client (installation → device → session) with context caching. |
| `hackathon_toolkit/0*_*.py` | bunq API tutorial scripts (auth, accounts, payments, requests, BunqMe, transactions, callbacks). |

---

## Running

### FastAPI server + SPA (primary)

```bash
uvicorn server:app --reload --port 8000
```

Opens the app at `http://localhost:8000/`. Camera, upload, and voice input available. Header shows bunq balance, geolocation, payment type selector, and active trip. OpenAPI docs at `http://localhost:8000/api/docs`.

You can also run the server directly:

```bash
python server.py
```

### Sandbox setup (first-time)

```bash
# 1. Verify connectivity / create a sandbox user
python connect_bunq.py

# 2. Seed the sandbox with realistic travel payments
python setup_sandbox.py

# 3. Start the app
uvicorn server:app --reload --port 8000
```

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
RECORD_SECONDS = 8                    # Mic recording duration
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

When currency is ambiguous, `geo.py` resolves it from the user's IP. If geolocation also fails, a disambiguation prompt appears in the UI.

A separate `extract_menu_from_image` path returns every priced line on a menu, with translated names and a single inferred currency for batch FX conversion.

---

## Audio model

`auto_voice_to_eur_aws.py` is a self-contained AWS pipeline:

1. **sounddevice** records raw PCM from the microphone (or the front-end uploads a WebM blob).
2. WAV / WebM is uploaded to S3.
3. **Amazon Transcribe** transcribes with automatic language detection (40+ languages). Set `TRANSCRIBE_LANGUAGE_OPTIONS` to improve accuracy for known languages.
4. **Amazon Nova Lite** (Bedrock) extracts the monetary amount and ISO currency code from the transcript.
5. Missing currency is resolved via `geo.py` before FX conversion.

The local audio file is deleted after upload.

---

## Trips and payment tracking

Users create trips from the trips view. Each trip has a `name`, UUID `id`, `budget_eur`, and optional `start_date`, `end_date`, `default_currency`, `default_own_currency`. Trip metadata is stored in `trips.json`.

`payment_tags.py` links bunq payment IDs to trip IDs in SQLite (`payment_tags.db`), with a spending snapshot for each tagged payment so tagged rows remain readable without re-fetching from bunq.

The dashboard endpoint (`/api/trips/{trip_id}/dashboard`) aggregates payments via `bunq_balance.get_payments` and falls back to the most recent payments if the trip's date range yields no rows — useful in the sandbox where all transactions are usually created on the same day.

---

## bunq integration

- **Authentication** — `hackathon_toolkit/bunq_client.py` handles the three-step RSA auth flow (installation → device → session). Session context is cached in `bunq_context.json`.
- **Balance** — `bunq_balance.py` reads all active accounts via `MonetaryAccountBank`. Primary balance shown in the header; full account list available in the dashboard.
- **Payments** — `get_payments` with date filtering. Individual payments can be tagged to trips and annotated with a bunq note (`push_note_text`).
- **Cards** — `get_cards` lists active cards; `set_card_daily_limit` updates the daily spend limit (Budget Guard).
- **BunqMe** — `create_bunqme_link` generates a shareable bunq.me URL for splitting expenses.
- **Webhooks** — `/api/bunq/webhook` receives `CARD_TRANSACTION_SUCCESSFUL` and `PAYMENT` events, verifies the bunq server signature against the cached server public key, and stores them in `card_events.db`. Register the receiver via `POST /api/bunq/setup-webhook`.
- **Sandbox detection** — Automatic: `BUNQ_API_KEY` starting with `sandbox_` routes all calls to the bunq sandbox.

---

## Project structure

```
hackathon_main/
├── server.py                   # FastAPI REST backend + static SPA — primary entry point
├── extractor.py                # Visual model (Claude)
├── auto_voice_to_eur_aws.py    # Audio model (AWS)
├── fx_converter.py             # FX conversion + fee model
├── bunq_balance.py             # bunq API surface (balance, payments, cards, BunqMe)
├── trips.py                    # Trip model + JSON persistence
├── payment_tags.py             # SQLite trip-to-payment mapping
├── geo.py                      # IP geolocation → currency
├── recommendations.py          # Google Maps + Claude recommendation engine
├── events_around.py            # Ticketmaster Discovery helpers
├── api_keys.py                 # Centralised secret loading
├── hyperparams.py              # All tunable parameters
├── connect_bunq.py             # Tiny bunq connectivity check
├── setup_sandbox.py            # Seed sandbox: user + funding + 20 travel payments
├── seed_transactions.py        # Lightweight 15-payment seeder
├── static/
│   └── index.html              # SPA front-end served by server.py
├── trips.json                  # Persisted trip data (auto-created)
├── payment_tags.db             # SQLite trip-tag store (auto-created)
├── card_events.db              # SQLite webhook event store (auto-created)
├── bunq_context.json           # Cached bunq session (auto-created)
├── .env                        # API keys (not committed)
├── pyproject.toml
├── requirements.txt
├── flake.nix                   # Nix reproducible dev environment
└── hackathon_toolkit/          # bunq API reference scripts + auth client
    ├── bunq_client.py
    ├── 01_authentication.py
    ├── 02_create_monetary_account.py
    ├── 03_list_monetary_accounts.py
    ├── 03_make_payment.py
    ├── 04_request_money.py
    ├── 05_create_bunqme_link.py
    ├── 06_list_transactions.py
    └── 07_setup_callbacks.py
```

