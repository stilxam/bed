# bunq FX Hackathon — Price Extraction Pipeline

A multimodal pipeline that extracts prices from the real world (images or voice) and instantly converts them to EUR using bunq's live exchange rates and ZeroFX fee model.

---

## Idea

The core problem: you see a price tag or hear a price spoken in a foreign currency and want to know immediately what it costs in euros, including all bunq fees. Instead of mentally converting, you point your camera or say the price out loud. The pipeline does the rest — extract, convert, display, and read it aloud.

---

## Architecture

```
User input
    │
    ▼
┌─────────────────────────────────────────┐
│              main.py                    │  ← Terminal control centre
│  Choose: [1] Visual  [2] Audio  [q] Quit│
└──────────┬──────────────────┬───────────┘
           │                  │
           ▼                  ▼
  extractor.py        auto_voice_to_eur_aws.py
  (Claude vision)     (AWS Transcribe + Bedrock)
           │                  │
           └────────┬─────────┘
                    ▼
           model_interface.py
           Normalise to [(amount, currency)]
                    │
                    ▼
           fx_converter.py
           Live ECB rates via Frankfurter API
           + bunq ZeroFX fee model (0.50% + 0.30% weekend)
                    │
                    ▼
           number_to_output.py
           Tkinter display card + gTTS voice readout
```

### Modules

| File | Role |
|---|---|
| `main.py` | Terminal control centre. Entry point for the full pipeline. |
| `extractor.py` | Visual model. Sends an image to Claude Haiku (escalates to Sonnet on ambiguity) and returns a structured price + currency. |
| `auto_voice_to_eur_aws.py` | Audio model. Records mic → uploads WAV to S3 → Amazon Transcribe (auto language detection) → Amazon Nova Lite extracts amount + currency. |
| `model_interface.py` | Adapter layer. Converts the output of either model into the canonical `list[tuple[float, str]]` format. Handles ambiguous results interactively. |
| `fx_converter.py` | FX conversion engine. Fetches live ECB mid-market rates from Frankfurter API (5-min cache), applies bunq ZeroFX fees, returns typed `FXResult` dataclasses. |
| `call_fx_converter.py` | Thin programmatic wrapper around `fx_converter` for direct use without the full pipeline. |
| `number_to_output.py` | Output layer. Tkinter window showing price cards with rate and fee breakdown. Speak button reads results aloud via gTTS. |
| `api_keys.py` | Centralised secret management. Loads from `.env`, falls back to shell environment. Single place to add any new API. |
| `hyperparams.py` | All tunable knobs in one file — TTS language, recording duration, AWS settings, Transcribe language hints, geolocation defaults. |
| `gui.py` | Standalone Gradio web UI (from hackathon template). Not wired into the main pipeline; kept for reference. |

### Data flow in detail

1. **main.py** prompts the user to choose visual or audio mode.
2. The selected model runs and returns its raw output.
3. **model_interface.py** normalises the output to `[(amount, currency)]`. If the extraction was ambiguous (unknown amount or currency) it prompts the user in the terminal to resolve it.
4. **fx_converter.py** receives the list of tuples and converts each to EUR, fetching live rates and applying bunq's fee structure. Returns `list[FXResult]`.
5. **number_to_output.py** takes the `FXResult` list and renders a Tkinter card view. The Speak button uses gTTS to read out each price in the configured language.

---

## Configuration

### API keys — `.env`

Copy the template and fill in your keys:

```
ANTHROPIC_API_KEY=       # Claude visual extractor
BUNQ_API_KEY=            # bunq banking API
AWS_ACCESS_KEY_ID=       # Amazon Transcribe + Bedrock + S3
AWS_SECRET_ACCESS_KEY=
AWS_SESSION_TOKEN=       # Only for temporary/STS credentials
AWS_DEFAULT_REGION=us-east-1
```

### Hyperparameters — `hyperparams.py`

```python
TTS_LANGUAGE = "fr"               # Language for voice readout
RECORD_SECONDS = 8                # Mic recording duration
AWS_S3_BUCKET = "your-bucket"     # S3 bucket for audio uploads
TRANSCRIBE_LANGUAGE_OPTIONS = []  # Empty = auto-detect all languages
DEFAULT_ISO_COUNTRY_CODE = None   # Set for currency disambiguation (e.g. "HK")
```

---

## Running

```bash
python main.py
```

Select mode 1 (image path) or 2 (speak into mic). The pipeline handles the rest.

---

## FX / Fee model

- **Rate source:** [Frankfurter API](https://www.frankfurter.app/) — free, no key, ECB mid-market rates. Cached 5 minutes per currency. Falls back to hardcoded approximate rates when offline.
- **bunq ZeroFX fees:** 0.50% markup always applied. Additional 0.30% on weekends (midpoint of bunq's published 0.15–0.50% weekend range).
- **EUR inputs:** passed through with zero fees and no API call.

---

## Visual model detail

`extractor.py` uses a two-model strategy:

1. **Claude Haiku** makes the initial extraction (fast, cheap).
2. If confidence is low on amount or currency, **Claude Sonnet** retries the same input.

Output is a typed union: `SuccessResult | AmountAmbiguousResult | CurrencyAmbiguousResult | FailureResult`. `model_interface.py` resolves ambiguous cases interactively.

An optional `GeoContext` (country + currency) can be passed to resolve ambiguous symbols like `$`. Set `DEFAULT_ISO_COUNTRY_CODE` and `DEFAULT_CURRENCY_ISO` in `hyperparams.py`.

---

## Audio model detail

`auto_voice_to_eur_aws.py` is a fully self-contained AWS pipeline:

1. **sounddevice** records raw PCM from the microphone.
2. The WAV is uploaded to an S3 bucket.
3. **Amazon Transcribe** transcribes the audio with automatic language detection (supports 40+ languages). Language hints in `TRANSCRIBE_LANGUAGE_OPTIONS` improve accuracy when the likely languages are known.
4. **Amazon Nova Lite** (via Bedrock) extracts the monetary amount and ISO currency code from the transcript.
5. The result is handed to `model_interface.py` and continues through the shared pipeline.

AWS credentials are read from `.env` / environment via `api_keys.py`. The local WAV file is deleted after upload.

---

## Project structure

```
hackathon_main/
├── main.py                     # Entry point — terminal control centre
├── model_interface.py          # Model output → [(amount, currency)]
├── extractor.py                # Visual model (Claude)
├── auto_voice_to_eur_aws.py    # Audio model (AWS)
├── fx_converter.py             # FX conversion + bunq fee model
├── call_fx_converter.py        # Programmatic FX wrapper
├── number_to_output.py         # Tkinter display + TTS
├── api_keys.py                 # Centralised secret loading
├── hyperparams.py              # All tunable parameters
├── gui.py                      # Standalone Gradio UI (template, unused in pipeline)
├── .env                        # API keys (not committed)
├── bunq_context.json           # Cached bunq session (auto-generated)
├── pyproject.toml
├── flake.nix
└── hackathon_toolkit/          # bunq API tutorial scripts (reference only)
    ├── bunq_client.py
    ├── 01_authentication.py
    └── ...
```

---

## Future work

- **GUI control centre** — `main.py` is intentionally a thin terminal layer. It is designed to be replaced by a proper GUI (the mode choice, image path input, and S3 bucket prompt map directly to buttons and file pickers).
- **bunq payment integration** — `bunq_client.py` in `hackathon_toolkit/` already handles auth and payments. The natural next step is triggering a bunq payment directly from the pipeline after conversion.
- **Batch / multi-price extraction** — the pipeline and FX layer already accept lists of tuples, so multi-item receipts are a one-change extension in `extractor.py`.
- **Geolocation auto-detection** — populate `DEFAULT_ISO_COUNTRY_CODE` from a GPS or IP-geolocation call at startup to resolve currency symbols automatically without user prompts.
- **Streaming audio** — replace the fixed-duration WAV recording with a voice-activity-detection loop so the recording stops automatically when the user stops speaking.
