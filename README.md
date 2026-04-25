# bunq Travel Buddy — Price Extraction & Trip Budget Tracker

A multimodal travel finance app. Point your camera at a price tag or say a price out loud — it extracts the amount, converts it to EUR using live Mastercard or Wise exchange rates with bunq's ZeroFX fee model, and tracks it against your trip budget.

---

## Idea

You're abroad and see a price in a foreign currency. Instead of mentally converting, open the app, snap a photo or say the price out loud. The pipeline extracts the amount, fetches the real exchange rate for your payment method, adds the exact bunq fees, and tells you what it costs in euros. Your live bunq balance and trip budget are always visible in the sidebar.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   app.py  (Streamlit)                │  ← Primary interface
│  Sidebar: bunq balance · geo · payment type · trip   │
│  Input:   📷 Photo  |  🖼️ Upload  |  🎤 Voice         │
└──────────┬──────────────────────────┬───────────────┘
           │                          │
           ▼                          ▼
    extractor.py              auto_voice_to_eur_aws.py
    (Claude vision)           (AWS Transcribe + Bedrock)
           │                          │
           │   geo.py (IP → currency fallback for both)
           │                          │
           └────────────┬─────────────┘
                        ▼
               fx_converter.py
               Card  → Mastercard live rate + 0.5% ZeroFX
               Transfer → Wise mid-market + ~0.65% fee
               Fallback → ECB / Frankfurter API
                        │
                        ▼
               Result card  +  gTTS readout
```

CLI entry point (`main.py`) uses the same models and FX layer without Streamlit.

---

## Modules

| File | Role |
|---|---|
| `app.py` | Streamlit app. Primary interface — camera/upload/voice input, result cards, disambiguation widgets, TTS. |
| `pages/Trips.py` | Trip management page. Create, edit, delete, and set the active trip. |
| `trips.py` | Trip data model (`id`, `name`, `budget_eur`) and JSON persistence. |
| `main.py` | Terminal control centre. CLI entry point for the full pipeline. |
| `extractor.py` | Visual model. Sends image to Claude Haiku (escalates to Sonnet on ambiguity), returns typed price + currency. |
| `auto_voice_to_eur_aws.py` | Audio model. Records mic → S3 → Amazon Transcribe (auto language detection) → Nova Lite extracts amount + currency. |
| `model_interface.py` | Adapter layer. Converts model output to `list[tuple[float, str]]`. Handles disambiguation via terminal prompts (CLI only). |
| `fx_converter.py` | FX engine. Card: Mastercard API (OAuth 1.0a RSA). Transfer: Wise API. Fallback: ECB/Frankfurter. Typed `FXResult` output. |
| `bunq_balance.py` | Fetches live account balance(s) from bunq API. Cached 60 s in Streamlit. |
| `geo.py` | IP geolocation (ip-api.com). Resolves currency when models return an amount without one. Module-level cache — one call per process. |
| `api_keys.py` | Centralised secret management. Loads `.env`, falls back to shell environment. |
| `hyperparams.py` | All tunable knobs — TTS language, recording duration, AWS settings, Mastercard sandbox flag, Wise fee rate. |
| `call_fx_converter.py` | Thin programmatic wrapper around `fx_converter` for direct use. |
| `number_to_output.py` | Tkinter display card + gTTS (CLI use; superseded by Streamlit in the app). |

---

## Running

### Streamlit app (primary)

```bash
streamlit run app.py
```

Opens the phone-like web UI. Camera, upload, and voice input available. Sidebar shows bunq balance, geolocation, payment type selector, and active trip.

### Terminal pipeline (CLI)

```bash
python main.py
```

Prompts for mode (visual / audio), payment type, and opens a Tkinter result window.

---

## Configuration

### API keys — `.env`

```
ANTHROPIC_API_KEY=           # Claude visual extractor
BUNQ_API_KEY=                # bunq banking API (sandbox_ prefix → sandbox mode)
AWS_ACCESS_KEY_ID=           # Amazon Transcribe + Bedrock + S3
AWS_SECRET_ACCESS_KEY=
AWS_SESSION_TOKEN=           # Only for temporary / STS credentials
AWS_DEFAULT_REGION=us-east-1
MASTERCARD_CONSUMER_KEY=     # Mastercard Exchange Rates API
MASTERCARD_PRIVATE_KEY_PATH= # Path to PEM private key (exported from .p12)
WISE_API_KEY=                # Wise Platform API — Sandbox or Live
```

Mastercard and Wise keys are optional — the app falls back to ECB/Frankfurter rates when they are absent.

### Hyperparameters — `hyperparams.py`

```python
TTS_LANGUAGE = "fr"                   # Language for voice readout
RECORD_SECONDS = 8                    # Mic recording duration (CLI)
AWS_S3_BUCKET = "your-bucket"         # S3 bucket for audio uploads
TRANSCRIBE_LANGUAGE_OPTIONS = []      # Empty = auto-detect all languages
TRANSCRIBE_MULTI_LANGUAGE = False
MASTERCARD_USE_SANDBOX = True         # False → Mastercard production
WISE_SERVICE_FEE_RATE = 0.0065        # ~0.65% Wise service fee
DEFAULT_ISO_COUNTRY_CODE = None       # Fallback if geo API is unavailable
```

---

## FX / Fee model

### Card payments (bunq ZeroFX)

- **Rate source:** Mastercard Exchange Rates API (OAuth 1.0a, RSA-SHA256 signed).
- **Fee:** 0.50% ZeroFX surcharge applied to the converted EUR amount.
- **No weekend markup** — ZeroFX rate is locked at card swipe time.
- **Fallback:** ECB mid-market rate (Frankfurter API, free, no key) if Mastercard is unavailable.

### Transfers (Wise)

- **Rate source:** Wise `/v1/rates` API (Bearer token).
- **Fee:** `WISE_SERVICE_FEE_RATE` (~0.65%) + 0.30% additional on weekends (bunq FX account conversion markup).
- **Fallback:** ECB mid-market rate if Wise is unavailable.

EUR inputs pass through with zero fees and no API call.

---

## Visual model detail

`extractor.py` uses a two-model strategy:

1. **Claude Haiku** makes the initial extraction (fast, cheap).
2. If confidence is low on amount or currency, **Claude Sonnet** retries.

Output is a typed union: `SuccessResult | AmountAmbiguousResult | CurrencyAmbiguousResult | FailureResult`.

When currency is ambiguous, `geo.py` resolves it automatically from the user's IP location. If geolocation also fails, the app shows a disambiguation widget.

---

## Audio model detail

`auto_voice_to_eur_aws.py` is a self-contained AWS pipeline:

1. **sounddevice** records raw PCM from the microphone.
2. WAV is uploaded to S3.
3. **Amazon Transcribe** transcribes with automatic language detection (40+ languages). `TRANSCRIBE_LANGUAGE_OPTIONS` improves accuracy for known languages.
4. **Amazon Nova Lite** (Bedrock) extracts the monetary amount and ISO currency code from the transcript.
5. Missing currency is resolved via `geo.py` before FX conversion.

The local WAV file is deleted after upload.

---

## Trips

Users can create named trips with an EUR budget from the **My Trips** page (`pages/Trips.py`). The active trip is shown in the sidebar of every page. Trip data is stored in `trips.json` at the project root.

Each trip has:
- **name** — user-facing display label
- **id** — UUID hex, auto-generated
- **budget_eur** — float, editable after creation

---

## bunq Integration

- **Balance** — `bunq_balance.py` reads the primary account balance via `MonetaryAccountBank`. All active accounts are shown in the sidebar expander.
- **Authentication** — handled by `hackathon_toolkit/bunq_client.py`. Three-step RSA auth (installation → device → session). Context cached in `bunq_context.json`.
- **Sandbox detection** — automatic: `BUNQ_API_KEY` starting with `sandbox_` routes to the sandbox.

---

## Project structure

```
hackathon_main/
├── app.py                      # Streamlit app — primary interface
├── pages/
│   └── Trips.py                # Trip management page
├── trips.py                    # Trip model + JSON persistence
├── main.py                     # CLI entry point
├── model_interface.py          # Model output → [(amount, currency)]
├── extractor.py                # Visual model (Claude)
├── auto_voice_to_eur_aws.py    # Audio model (AWS)
├── fx_converter.py             # FX conversion + fee model
├── bunq_balance.py             # Live bunq balance
├── geo.py                      # IP geolocation → currency
├── call_fx_converter.py        # Programmatic FX wrapper
├── number_to_output.py         # Tkinter display + TTS (CLI)
├── api_keys.py                 # Centralised secret loading
├── hyperparams.py              # All tunable parameters
├── gui.py                      # Standalone Gradio UI (template, unused)
├── trips.json                  # Persisted trip data (auto-created)
├── bunq_context.json           # Cached bunq session (auto-created)
├── .env                        # API keys (not committed)
├── pyproject.toml
└── hackathon_toolkit/          # bunq API tutorial scripts (reference)
    ├── bunq_client.py
    ├── 01_authentication.py
    └── ...
```

---

## Future work

- **Trip spending tracker** — log each converted price against the active trip budget and show remaining balance.
- **bunq payment trigger** — initiate a bunq payment directly from the pipeline after conversion, using `hackathon_toolkit/bunq_client.py`.
- **Batch / multi-price extraction** — the pipeline already accepts `list[tuple[float, str]]`; multi-item receipts need one change in `extractor.py`.
- **Streaming audio** — replace fixed-duration recording with voice-activity detection so recording stops automatically.
- **Push notifications** — bunq webhook callbacks when a payment is made abroad, feeding the trip tracker automatically.
