# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**bunq Travel Buddy** — a multimodal travel finance app. Point a camera at a price tag or say a price aloud; the app extracts the amount, converts it to EUR using live Mastercard or Wise rates with bunq's ZeroFX fee model, and tracks it against a trip budget.

## Running the app

```bash
# Primary interface (Streamlit web UI)
streamlit run app.py

# Terminal/CLI pipeline (Tkinter result window, no Streamlit)
python main.py
```

## Environment

This project uses a Nix flake (`flake.nix`) that builds an FHS environment with Python 3.14 and `uv`. Enter the dev shell with `nix develop`, which auto-creates `.venv` and activates it.

Install dependencies:
```bash
uv pip install -r requirements.txt
```

`ruff` is available in the Nix environment for linting.

## Required `.env` keys

```
ANTHROPIC_API_KEY=           # Claude visual extractor
BUNQ_API_KEY=                # sandbox_ prefix → sandbox mode
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=us-east-1
MASTERCARD_CONSUMER_KEY=     # optional — falls back to ECB rates
MASTERCARD_PRIVATE_KEY_PATH= # path to PEM private key
WISE_API_KEY=                # optional — falls back to ECB rates
```

## Architecture

The pipeline has two extraction paths that feed into a shared FX layer:

**Visual path:** `app.py` / `main.py` → `extractor.py` (Claude Haiku, escalates to Sonnet on ambiguity) → `fx_converter.py`

**Audio path:** `app.py` / `main.py` → `auto_voice_to_eur_aws.py` (sounddevice → S3 → Amazon Transcribe → Bedrock Nova Lite) → `fx_converter.py`

Both paths use `geo.py` (IP geolocation via ip-api.com) to resolve missing currency codes before FX conversion.

**FX layer (`fx_converter.py`):**
- Card: Mastercard Exchange Rates API (OAuth 1.0a, RSA-SHA256) + 0.5% ZeroFX fee
- Transfer: Wise `/v1/rates` + ~0.65% service fee (+0.30% on weekends)
- Fallback: ECB/Frankfurter API (free, no key needed)
- Returns typed `FXResult` dataclass

**Disambiguation:** `extractor.py` returns a union type — `SuccessResult | AmountAmbiguousResult | CurrencyAmbiguousResult | FailureResult`. In the Streamlit app, ambiguous cases show inline widgets. In the CLI, `model_interface.py` handles disambiguation via `input()` prompts.

**Trips:** `trips.py` provides a `Trip` dataclass (id, name, budget_eur, default_currency, start_date, end_date) with JSON persistence to `trips.json`. `pages/Trips.py` is the Streamlit multi-page route for CRUD. The active trip is stored in `st.session_state["active_trip_id"]`; scanning is gated until a trip is selected.

**bunq auth:** `hackathon_toolkit/bunq_client.py` handles three-step RSA auth (installation → device → session). Session context is cached in `bunq_context.json`. `BUNQ_API_KEY` starting with `sandbox_` auto-routes to sandbox.

## Key tunable parameters (`hyperparams.py`)

- `TTS_LANGUAGE` — gTTS language code for voice readout
- `RECORD_SECONDS` — mic recording duration (CLI only)
- `AWS_S3_BUCKET` / `AWS_S3_PREFIX` — S3 location for audio uploads
- `MASTERCARD_USE_SANDBOX` — True for sandbox, False for production
- `WISE_SERVICE_FEE_RATE` — default 0.0065
- `DEFAULT_ISO_COUNTRY_CODE` — fallback if geolocation is unavailable

## Auto-generated files (do not commit)

- `trips.json` — persisted trip data
- `bunq_context.json` — cached bunq session
- `streamlit_uploads/` — temporary upload directory
