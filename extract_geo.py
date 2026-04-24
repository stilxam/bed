from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, Literal, Optional

import requests
from anthropic import Anthropic


# ---------------------------------------------------------------------------
# Claude models
# ---------------------------------------------------------------------------

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Claude price extraction prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a price extraction assistant for spoken audio. You receive transcripts of spoken prices in any language and extract structured price data.

Extract the single most prominent price from the transcript. Return ONLY a JSON object:
{"amount": <number or null>, "currency_iso": <ISO 4217 string or null>, "raw_text": <exact text found>, "amount_confidence": <"high"|"low">, "currency_confidence": <"high"|"low">, "currency_inference_source": <"explicit"|"geolocation"|"unknown">}

Rules:
- Handle spoken numbers in any language (e.g. "dix-sept euros", "十七港元", "siebzehn Euro").
- If the amount is ambiguous or misheared, set amount to null and amount_confidence to "low".
- If the currency symbol/word is ambiguous, use geolocation context to infer and set currency_inference_source to "geolocation".
- If currency cannot be inferred, set currency_iso to null and currency_confidence to "low".
- Pick the most prominent single price only.
- No text outside the JSON object.
"""


# ---------------------------------------------------------------------------
# Currency lookup
# ---------------------------------------------------------------------------

COUNTRY_TO_CURRENCY = {
    "NL": "EUR", "DE": "EUR", "FR": "EUR", "ES": "EUR", "IT": "EUR",
    "BE": "EUR", "AT": "EUR", "IE": "EUR", "PT": "EUR", "FI": "EUR",
    "GR": "EUR", "EE": "EUR", "LV": "EUR", "LT": "EUR", "LU": "EUR",
    "MT": "EUR", "CY": "EUR", "SK": "EUR", "SI": "EUR", "HR": "EUR",
    "US": "USD", "GB": "GBP", "CH": "CHF", "NO": "NOK", "SE": "SEK",
    "DK": "DKK", "PL": "PLN", "CZ": "CZK", "HU": "HUF", "RO": "RON",
    "BG": "BGN", "TR": "TRY", "RU": "RUB", "UA": "UAH", "CN": "CNY",
    "JP": "JPY", "KR": "KRW", "IN": "INR", "CA": "CAD", "AU": "AUD",
    "NZ": "NZD", "MX": "MXN", "BR": "BRL", "ZA": "ZAR",
}

CURRENCY_NAMES = {
    "USD": "US Dollar",
    "EUR": "Euro",
    "GBP": "British Pound",
    "JPY": "Japanese Yen",
    "CNY": "Chinese Yuan",
    "HKD": "Hong Kong Dollar",
    "AUD": "Australian Dollar",
    "CAD": "Canadian Dollar",
    "CHF": "Swiss Franc",
    "SGD": "Singapore Dollar",
    "KRW": "South Korean Won",
    "INR": "Indian Rupee",
    "MXN": "Mexican Peso",
    "BRL": "Brazilian Real",
    "THB": "Thai Baht",
    "NOK": "Norwegian Krone",
    "SEK": "Swedish Krona",
    "DKK": "Danish Krone",
    "PLN": "Polish Złoty",
    "CZK": "Czech Koruna",
    "HUF": "Hungarian Forint",
    "RON": "Romanian Leu",
    "BGN": "Bulgarian Lev",
    "TRY": "Turkish Lira",
    "RUB": "Russian Ruble",
    "UAH": "Ukrainian Hryvnia",
    "NZD": "New Zealand Dollar",
    "ZAR": "South African Rand",
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class GeoContext:
    country_name: str | None
    iso_country_code: str | None
    likely_currency_iso: str | None
    city: str | None = None
    region: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    timezone: str | None = None
    provider: str | None = None
    accuracy_note: str | None = None


@dataclass
class SuccessResult:
    status: Literal["success"]
    amount: float
    currency_iso: str
    raw_text: str
    currency_inferred: bool
    geo: GeoContext | None
    nearby_recommendations: list[dict[str, Any]]


@dataclass
class AmountAmbiguousResult:
    status: Literal["amount_ambiguous"]
    currency_iso: str | None
    raw_text: str
    geo: GeoContext | None
    nearby_recommendations: list[dict[str, Any]]


@dataclass
class CurrencyAmbiguousResult:
    status: Literal["currency_ambiguous"]
    amount: float
    raw_text: str
    suggested_currency_iso: str | None
    suggested_currency_name: str | None
    geo: GeoContext | None
    nearby_recommendations: list[dict[str, Any]]


@dataclass
class FailureResult:
    status: Literal["failure"]
    reason: Literal["no_price_found", "api_error", "stt_error", "geo_error"]
    details: str | None = None


ExtractionResult = (
    SuccessResult
    | AmountAmbiguousResult
    | CurrencyAmbiguousResult
    | FailureResult
)


# ---------------------------------------------------------------------------
# Location detection
# ---------------------------------------------------------------------------

def add_currency(result: Dict[str, Any]) -> Dict[str, Any]:
    country_code = result.get("country_code")
    result["default_currency"] = COUNTRY_TO_CURRENCY.get(country_code)
    result["accuracy_note"] = (
        "Approximate IP-based location. VPN, proxy, mobile network, or corporate network "
        "may give the wrong country."
    )
    return result


def provider_ipapi_co() -> Dict[str, Any]:
    r = requests.get("https://ipapi.co/json/", timeout=10)
    r.raise_for_status()
    d = r.json()

    return add_currency({
        "provider": "ipapi.co",
        "ip": d.get("ip"),
        "city": d.get("city"),
        "region": d.get("region"),
        "country": d.get("country_name"),
        "country_code": d.get("country_code"),
        "latitude": d.get("latitude"),
        "longitude": d.get("longitude"),
        "timezone": d.get("timezone"),
    })


def provider_ipwho_is() -> Dict[str, Any]:
    r = requests.get("https://ipwho.is/", timeout=10)
    r.raise_for_status()
    d = r.json()

    if d.get("success") is False:
        raise RuntimeError(d.get("message", "ipwho.is failed"))

    return add_currency({
        "provider": "ipwho.is",
        "ip": d.get("ip"),
        "city": d.get("city"),
        "region": d.get("region"),
        "country": d.get("country"),
        "country_code": d.get("country_code"),
        "latitude": d.get("latitude"),
        "longitude": d.get("longitude"),
        "timezone": (
            d.get("timezone") or {}
        ).get("id") if isinstance(d.get("timezone"), dict) else d.get("timezone"),
    })


def provider_ip_api_com() -> Dict[str, Any]:
    r = requests.get("http://ip-api.com/json/", timeout=10)
    r.raise_for_status()
    d = r.json()

    if d.get("status") != "success":
        raise RuntimeError(d.get("message", "ip-api.com failed"))

    return add_currency({
        "provider": "ip-api.com",
        "ip": d.get("query"),
        "city": d.get("city"),
        "region": d.get("regionName"),
        "country": d.get("country"),
        "country_code": d.get("countryCode"),
        "latitude": d.get("lat"),
        "longitude": d.get("lon"),
        "timezone": d.get("timezone"),
    })


def detect_location() -> Dict[str, Any]:
    providers: list[Callable[[], Dict[str, Any]]] = [
        provider_ipwho_is,
        provider_ip_api_com,
        provider_ipapi_co,
    ]

    errors = []

    for provider in providers:
        try:
            return provider()
        except Exception as exc:
            errors.append({
                "provider": provider.__name__.replace("provider_", ""),
                "error": str(exc),
            })

    raise RuntimeError(json.dumps({
        "message": "All IP geolocation providers failed.",
        "provider_errors": errors,
    }, ensure_ascii=False))


def geo_from_location_result(location: Dict[str, Any]) -> GeoContext:
    return GeoContext(
        country_name=location.get("country"),
        iso_country_code=location.get("country_code"),
        likely_currency_iso=location.get("default_currency"),
        city=location.get("city"),
        region=location.get("region"),
        latitude=location.get("latitude"),
        longitude=location.get("longitude"),
        timezone=location.get("timezone"),
        provider=location.get("provider"),
        accuracy_note=location.get("accuracy_note"),
    )


# ---------------------------------------------------------------------------
# Nearby place recommendations
# ---------------------------------------------------------------------------

def recommend_nearby_places(
    geo: GeoContext | None,
    query: str = "restaurants",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """
    Uses OpenStreetMap Nominatim search as a lightweight no-key place search.

    For production use, replace this with Google Places, Foursquare, Yelp, or another
    paid/local-search API because Nominatim is not designed for heavy commercial use.
    """

    if geo is None or geo.latitude is None or geo.longitude is None:
        return []

    url = "https://nominatim.openstreetmap.org/search"

    params = {
        "q": query,
        "format": "jsonv2",
        "limit": str(limit),
        "addressdetails": "1",
        "extratags": "1",
        "namedetails": "1",
        "bounded": "1",
        # Rough bounding box around current coordinates.
        # left, top, right, bottom
        "viewbox": (
            f"{geo.longitude - 0.03},"
            f"{geo.latitude + 0.03},"
            f"{geo.longitude + 0.03},"
            f"{geo.latitude - 0.03}"
        ),
    }

    headers = {
        "User-Agent": "audio-price-extractor/1.0"
    }

    r = requests.get(url, params=params, headers=headers, timeout=10)
    r.raise_for_status()

    places = []
    for item in r.json():
        places.append({
            "name": item.get("name") or item.get("display_name", "").split(",")[0],
            "display_name": item.get("display_name"),
            "category": item.get("category"),
            "type": item.get("type"),
            "latitude": item.get("lat"),
            "longitude": item.get("lon"),
        })

    return places


# ---------------------------------------------------------------------------
# Claude helpers
# ---------------------------------------------------------------------------

def geo_context_for_claude(geo: GeoContext | None) -> str:
    if geo is None:
        return "Geolocation context: not available."

    return (
        "Geolocation context:\n"
        f"- country_name: {geo.country_name}\n"
        f"- iso_country_code: {geo.iso_country_code}\n"
        f"- likely_currency_iso: {geo.likely_currency_iso}\n"
        f"- city: {geo.city}\n"
        f"- region: {geo.region}\n"
        f"- timezone: {geo.timezone}\n"
        f"- accuracy_note: {geo.accuracy_note}"
    )


def build_messages(
    transcript: str,
    geo: GeoContext | None,
    nearby_recommendations: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """
    We include nearby recommendations as context for the app output,
    but the system prompt still forces Claude to return only the price JSON.
    """

    user_text = f"""\
{geo_context_for_claude(geo)}

Nearby place recommendations available to the application:
{json.dumps(nearby_recommendations, ensure_ascii=False, indent=2)}

Transcript:
{transcript}
"""

    return [
        {
            "role": "user",
            "content": user_text,
        }
    ]


def parse_claude_response(raw: str) -> dict[str, Any]:
    text = raw.strip()

    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]

    return json.loads(text.strip())


def call_claude(client: Anthropic, model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    response = client.messages.create(
        model=model,
        max_tokens=200,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=messages,
    )

    return parse_claude_response(response.content[0].text)


def needs_retry(parsed: dict[str, Any]) -> bool:
    return (
        parsed.get("amount_confidence") == "low"
        or parsed.get("currency_confidence") == "low"
    )


def to_result(
    parsed: dict[str, Any],
    geo: GeoContext | None,
    nearby_recommendations: list[dict[str, Any]],
) -> ExtractionResult:
    amount = parsed.get("amount")
    currency_iso = parsed.get("currency_iso")
    raw_text = parsed.get("raw_text", "")
    amount_confidence = parsed.get("amount_confidence")
    currency_confidence = parsed.get("currency_confidence")
    currency_source = parsed.get("currency_inference_source")

    if amount is None or amount_confidence == "low":
        return AmountAmbiguousResult(
            status="amount_ambiguous",
            currency_iso=currency_iso,
            raw_text=raw_text,
            geo=geo,
            nearby_recommendations=nearby_recommendations,
        )

    if currency_iso is None or currency_confidence == "low":
        suggested_currency_iso = geo.likely_currency_iso if geo else None

        return CurrencyAmbiguousResult(
            status="currency_ambiguous",
            amount=float(amount),
            raw_text=raw_text,
            suggested_currency_iso=suggested_currency_iso,
            suggested_currency_name=(
                CURRENCY_NAMES.get(suggested_currency_iso)
                if suggested_currency_iso
                else None
            ),
            geo=geo,
            nearby_recommendations=nearby_recommendations,
        )

    return SuccessResult(
        status="success",
        amount=float(amount),
        currency_iso=currency_iso,
        raw_text=raw_text,
        currency_inferred=currency_source == "geolocation",
        geo=geo,
        nearby_recommendations=nearby_recommendations,
    )


# ---------------------------------------------------------------------------
# Public extraction function
# ---------------------------------------------------------------------------

def extract_price_with_geo_and_places(
    transcript: str,
    place_query: str = "restaurants",
    client: Anthropic | None = None,
) -> ExtractionResult:
    client = client or Anthropic()

    try:
        location = detect_location()
        geo = geo_from_location_result(location)
    except Exception as exc:
        return FailureResult(
            status="failure",
            reason="geo_error",
            details=str(exc),
        )

    try:
        nearby_recommendations = recommend_nearby_places(
            geo=geo,
            query=place_query,
            limit=5,
        )
    except Exception:
        nearby_recommendations = []

    try:
        messages = build_messages(
            transcript=transcript,
            geo=geo,
            nearby_recommendations=nearby_recommendations,
        )

        parsed = call_claude(client, HAIKU, messages)

        if needs_retry(parsed):
            parsed = call_claude(client, SONNET, messages)

        return to_result(parsed, geo, nearby_recommendations)

    except Exception as exc:
        return FailureResult(
            status="failure",
            reason="api_error",
            details=str(exc),
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def dataclass_to_jsonable(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {
            key: dataclass_to_jsonable(value)
            for key, value in obj.__dict__.items()
        }

    if isinstance(obj, list):
        return [dataclass_to_jsonable(item) for item in obj]

    if isinstance(obj, dict):
        return {
            key: dataclass_to_jsonable(value)
            for key, value in obj.items()
        }

    return obj


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract spoken price with geolocation-aware currency inference and nearby place recommendations."
    )
    parser.add_argument(
        "transcript",
        help="STT transcript containing a spoken price.",
    )
    parser.add_argument(
        "--place-query",
        default="restaurants",
        help='Nearby place search query, e.g. "cafes", "restaurants", "supermarkets".',
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON.",
    )

    args = parser.parse_args()

    result = extract_price_with_geo_and_places(
        transcript=args.transcript,
        place_query=args.place_query,
    )

    print(
        json.dumps(
            dataclass_to_jsonable(result),
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )


if __name__ == "__main__":
    main()