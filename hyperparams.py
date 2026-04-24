"""
hyperparams.py — Central hyperparameter configuration.

All tunable knobs for the pipeline live here so they are easy to find and
change without hunting through individual modules.
"""

from __future__ import annotations

# ── Output / TTS ──────────────────────────────────────────────────────────────
TTS_LANGUAGE: str = "fr"
# Language code used for text-to-speech in the display window.
# Options: "en" English,  "nl" Dutch,   "de" German,  "fr" French,
#          "es" Spanish,  "it" Italian, "pt" Portuguese, "ja" Japanese,
#          "zh" Chinese,  "ar" Arabic,  "hi" Hindi,   "ko" Korean,
#          "pl" Polish,   "tr" Turkish, "ru" Russian,  "sv" Swedish

# ── Microphone recording ──────────────────────────────────────────────────────
RECORD_SECONDS: int = 8       # Default recording duration for audio mode
AUDIO_SAMPLE_RATE: int = 16000  # WAV sample rate in Hz

# ── AWS / Amazon Transcribe ───────────────────────────────────────────────────
# Set AWS_S3_BUCKET to your bucket name, or leave empty to be prompted at runtime.
AWS_S3_BUCKET: str = ""
AWS_S3_PREFIX: str = "voice-money-inputs"
AWS_BEDROCK_MODEL_ID: str = "amazon.nova-lite-v1:0"

# Restrict Transcribe language candidates for higher accuracy.
# Leave empty for fully automatic detection across all supported languages.
# Example: ["en-US", "nl-NL", "ru-RU", "de-DE", "fr-FR", "es-ES"]
TRANSCRIBE_LANGUAGE_OPTIONS: list[str] = []
TRANSCRIBE_MULTI_LANGUAGE: bool = False  # True for mixed-language audio

# ── Visual extractor geolocation hint ────────────────────────────────────────
# Helps resolve ambiguous currency symbols (e.g. "$" → USD vs HKD vs AUD).
# Leave as None to skip geolocation context entirely.
DEFAULT_COUNTRY_NAME: str | None = None      # e.g. "Hong Kong"
DEFAULT_ISO_COUNTRY_CODE: str | None = None  # e.g. "HK"
DEFAULT_CURRENCY_ISO: str | None = None      # e.g. "HKD"
