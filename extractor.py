import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from anthropic import Anthropic

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

_MEDIA_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

_PROMPT = """\
You are a price extraction assistant.
Extract the single most prominent price from the input below.

{geo_context}

Return ONLY a JSON object in this exact format:
{{
  "amount": <number or null>,
  "currency_iso": <ISO 4217 string or null>,
  "raw_text": <the exact text you found, e.g. "HK$17">,
  "amount_confidence": <"high" | "low">,
  "currency_confidence": <"high" | "low">,
  "currency_inference_source": <"explicit" | "geolocation" | "unknown">
}}

Rules:
- If the number is ambiguous or unreadable, set amount to null and amount_confidence to "low".
- If the currency symbol is ambiguous (e.g. bare "$"), use the geolocation context to infer \
the currency and set currency_inference_source to "geolocation".
- If you cannot infer the currency even with geolocation, set currency_iso to null and \
currency_confidence to "low".
- Do not extract multiple prices. Pick the most prominent one.
- Do not add any text outside the JSON object.\
"""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class GeoContext:
    country_name: str
    iso_country_code: str    # ISO 3166-1 alpha-2, e.g. "HK"
    likely_currency_iso: str  # ISO 4217, e.g. "HKD"


@dataclass
class SuccessResult:
    status: Literal["success"]
    amount: float
    currency_iso: str
    raw_text: str
    currency_inferred: bool  # True if derived from geolocation rather than explicit symbol


@dataclass
class AmountAmbiguousResult:
    status: Literal["amount_ambiguous"]
    currency_iso: str | None
    raw_text: str
    # UX action: prompt user to enter the number manually


@dataclass
class CurrencyAmbiguousResult:
    status: Literal["currency_ambiguous"]
    amount: float
    raw_text: str
    suggested_currency_iso: str | None   # from geolocation
    suggested_currency_name: str | None  # e.g. "Hong Kong Dollar"
    # UX action: ask user to confirm the suggested currency


@dataclass
class FailureResult:
    status: Literal["failure"]
    reason: Literal["no_price_found", "api_error", "stt_error"]


ExtractionResult = (
    SuccessResult | AmountAmbiguousResult | CurrencyAmbiguousResult | FailureResult
)

# Minimal lookup for currency names shown in the ambiguous-currency user prompt.
# Expand as needed; missing entries fall back to None.
_CURRENCY_NAMES: dict[str, str] = {
    "USD": "US Dollar", "EUR": "Euro", "GBP": "British Pound",
    "JPY": "Japanese Yen", "CNY": "Chinese Yuan", "HKD": "Hong Kong Dollar",
    "AUD": "Australian Dollar", "CAD": "Canadian Dollar", "CHF": "Swiss Franc",
    "SGD": "Singapore Dollar", "KRW": "South Korean Won", "INR": "Indian Rupee",
    "MXN": "Mexican Peso", "BRL": "Brazilian Real", "THB": "Thai Baht",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _geo_context_str(geo: GeoContext | None) -> str:
    if geo is None:
        return "No geolocation context is available."
    return (
        f"Geolocation context: the user is currently in {geo.country_name} "
        f"({geo.iso_country_code}), where the typical currency is {geo.likely_currency_iso}."
    )


def _detect_media_type(data: bytes, fallback_suffix: str) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    media_type = _MEDIA_TYPES.get(fallback_suffix)
    if media_type is None:
        raise ValueError(f"Unsupported image format '{fallback_suffix}'. Accepted: {list(_MEDIA_TYPES)}")
    return media_type


def _image_message(image_path: Path, prompt: str) -> list[dict]:
    raw = image_path.read_bytes()
    media_type = _detect_media_type(raw, image_path.suffix.lower())
    data = base64.standard_b64encode(raw).decode("utf-8")
    return [
        {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                {"type": "text", "text": prompt},
            ],
        }
    ]


def _text_message(transcript: str, prompt: str) -> list[dict]:
    return [
        {
            "role": "user",
            "content": [{"type": "text", "text": f"{prompt}\n\nTranscript:\n{transcript}"}],
        }
    ]


def _parse_claude_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(text.strip())


def _needs_retry(parsed: dict) -> bool:
    return (
        parsed.get("amount_confidence") == "low"
        or parsed.get("currency_confidence") == "low"
    )


def _to_result(parsed: dict, geo: GeoContext | None) -> ExtractionResult:
    amount = parsed.get("amount")
    currency_iso = parsed.get("currency_iso")
    raw_text = parsed.get("raw_text", "")
    inferred = parsed.get("currency_inference_source") == "geolocation"

    if amount is None:
        return AmountAmbiguousResult(
            status="amount_ambiguous",
            currency_iso=currency_iso,
            raw_text=raw_text,
        )

    if currency_iso is None:
        suggested = geo.likely_currency_iso if geo else None
        return CurrencyAmbiguousResult(
            status="currency_ambiguous",
            amount=float(amount),
            raw_text=raw_text,
            suggested_currency_iso=suggested,
            suggested_currency_name=_CURRENCY_NAMES.get(suggested) if suggested else None,
        )

    return SuccessResult(
        status="success",
        amount=float(amount),
        currency_iso=currency_iso,
        raw_text=raw_text,
        currency_inferred=inferred,
    )


def _call(client: Anthropic, model: str, messages: list[dict]) -> dict:
    response = client.messages.create(
        model=model,
        max_tokens=200,
        temperature=0,
        messages=messages,
    )
    return _parse_claude_response(response.content[0].text)


def _extract(
    messages: list[dict],
    geo: GeoContext | None,
    client: Anthropic,
) -> ExtractionResult:
    parsed = _call(client, HAIKU, messages)
    if _needs_retry(parsed):
        parsed = _call(client, SONNET, messages)
    return _to_result(parsed, geo)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_from_image(
    image_path: str | Path,
    geo: GeoContext | None = None,
    client: Anthropic | None = None,
) -> ExtractionResult:
    """Extract a price and currency from an image file (JPEG, PNG, WEBP)."""
    client = client or Anthropic()
    prompt = _PROMPT.format(geo_context=_geo_context_str(geo))
    messages = _image_message(Path(image_path), prompt)
    return _extract(messages, geo, client)


def extract_from_transcript(
    transcript: str,
    geo: GeoContext | None = None,
    client: Anthropic | None = None,
) -> ExtractionResult:
    """Extract a price and currency from a Whisper STT transcript string."""
    client = client or Anthropic()
    prompt = _PROMPT.format(geo_context=_geo_context_str(geo))
    messages = _text_message(transcript, prompt)
    return _extract(messages, geo, client)
