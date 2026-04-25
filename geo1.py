from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Literal

import requests
from anthropic import Anthropic
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# Claude models
# ---------------------------------------------------------------------------

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Claude price extraction prompt
# ---------------------------------------------------------------------------

PRICE_EXTRACTION_SYSTEM_PROMPT = """\
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

QUERY_PLANNER_SYSTEM_PROMPT = """\
You are a travel search query planner for a budgeting app.

Given the traveler's time of day, budget level, and location, decide the best type of place to search for on Google Maps right now.

Return ONLY valid JSON with no other text:
{
  "query": "<Google Places text search query, 2-6 words>",
  "included_type": "<one of: restaurant, cafe, bar, shopping_mall, tourist_attraction — or null>",
  "reason": "<one sentence explaining why this fits the context>"
}
"""

RECOMMENDATION_SYSTEM_PROMPT = """\
You are an intelligent travel recommendation assistant integrated into a bunq travel budgeting app.

You receive live data from three sources:
1. The traveler's budget snapshot pulled from their bunq banking account
2. The current local time and time-of-day classification
3. Nearby places retrieved from the Google Maps Places API

Your job: curate the 3–5 best places for this specific traveler RIGHT NOW.

Reasoning framework:

BUDGET — match price level to remaining daily budget:
- Under €15/day remaining → cheap options only (€, free)
- €15–40/day remaining → moderate (€ to €€)
- Over €40/day remaining → any price level; favour quality and rating

TIME OF DAY — match venue type to the hour:
- morning (5–11h): cafes, bakeries, breakfast spots
- lunch (11–14h): restaurants, casual lunch places
- afternoon (14–17h): cafes, pastry shops, light snacks, nearby sights
- evening (17–22h): dinner restaurants, bars, cocktail venues
- night (22h+): late-night food, bars if open

QUALITY — favour places worth the visit:
- Prefer rating ≥ 4.2 with 50+ reviews
- A 4.4 with 800 reviews beats a 4.9 with 8 reviews
- Shorter distance wins when quality is comparable

For each selected place, write a "why" field (1–2 tight sentences) that references the traveler's actual numbers — their remaining daily budget, the time of day, the distance. Be specific, not generic.

Return ONLY a valid JSON array with no surrounding text or markdown fences:
[
  {
    "place_name": "string",
    "category": "string or null",
    "distance_m": number or null,
    "price_level_label": "string",
    "google_maps_uri": "string or null",
    "rating": number or null,
    "user_rating_count": number or null,
    "formatted_address": "string or null",
    "why": "string"
  }
]
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
class TimeContext:
    local_datetime: str | None
    local_time: str | None
    hour: int | None
    time_of_day: Literal[
        "morning",
        "lunch",
        "afternoon",
        "evening",
        "night",
        "unknown",
    ]


@dataclass
class RecommendationContext:
    daily_budget: float | None
    budget_tier: Literal["low", "medium", "high", "unknown"]
    time_of_day: str
    place_query: str
    recommendation_reason: str
    google_price_levels: list[str]


@dataclass
class SuccessResult:
    status: Literal["success"]
    amount: float
    currency_iso: str
    raw_text: str
    currency_inferred: bool
    geo: GeoContext | None
    time: TimeContext | None
    recommendation_context: RecommendationContext | None
    nearby_recommendations: list[dict[str, Any]]


@dataclass
class AmountAmbiguousResult:
    status: Literal["amount_ambiguous"]
    currency_iso: str | None
    raw_text: str
    geo: GeoContext | None
    time: TimeContext | None
    recommendation_context: RecommendationContext | None
    nearby_recommendations: list[dict[str, Any]]


@dataclass
class CurrencyAmbiguousResult:
    status: Literal["currency_ambiguous"]
    amount: float
    raw_text: str
    suggested_currency_iso: str | None
    suggested_currency_name: str | None
    geo: GeoContext | None
    time: TimeContext | None
    recommendation_context: RecommendationContext | None
    nearby_recommendations: list[dict[str, Any]]


@dataclass
class FailureResult:
    status: Literal["failure"]
    reason: Literal[
        "no_price_found",
        "api_error",
        "stt_error",
        "geo_error",
        "places_error",
    ]
    details: str | None = None


ExtractionResult = (
    SuccessResult
    | AmountAmbiguousResult
    | CurrencyAmbiguousResult
    | FailureResult
)


@dataclass
class BudgetInfo:
    """Structured budget snapshot passed into the recommendation agent."""
    total_budget_eur: float
    spent_eur: float
    remaining_eur: float
    home_currency: str
    days_total: int | None = None
    days_remaining: int | None = None
    budget_per_day_eur: float | None = None
    budget_left_per_day_eur: float | None = None


@dataclass
class AgentRecommendation:
    """A single place recommendation produced by the Claude agent."""
    place_name: str
    why: str
    category: str | None = None
    distance_m: float | None = None
    price_level_label: str = ""
    google_maps_uri: str | None = None
    rating: float | None = None
    user_rating_count: int | None = None
    formatted_address: str | None = None


# ---------------------------------------------------------------------------
# Location detection
# ---------------------------------------------------------------------------

def add_currency(result: Dict[str, Any]) -> Dict[str, Any]:
    country_code = result.get("country_code")
    result["default_currency"] = COUNTRY_TO_CURRENCY.get(country_code)
    result["accuracy_note"] = (
        "Approximate IP-based location. VPN, proxy, mobile network, "
        "or corporate network may give the wrong country."
    )
    return result


def provider_ipapi_co() -> Dict[str, Any]:
    response = requests.get("https://ipapi.co/json/", timeout=10)
    response.raise_for_status()
    data = response.json()

    return add_currency({
        "provider": "ipapi.co",
        "ip": data.get("ip"),
        "city": data.get("city"),
        "region": data.get("region"),
        "country": data.get("country_name"),
        "country_code": data.get("country_code"),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": data.get("timezone"),
    })


def provider_ipwho_is() -> Dict[str, Any]:
    response = requests.get("https://ipwho.is/", timeout=10)
    response.raise_for_status()
    data = response.json()

    if data.get("success") is False:
        raise RuntimeError(data.get("message", "ipwho.is failed"))

    timezone_data = data.get("timezone")

    return add_currency({
        "provider": "ipwho.is",
        "ip": data.get("ip"),
        "city": data.get("city"),
        "region": data.get("region"),
        "country": data.get("country"),
        "country_code": data.get("country_code"),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": (
            timezone_data.get("id")
            if isinstance(timezone_data, dict)
            else timezone_data
        ),
    })


def provider_ip_api_com() -> Dict[str, Any]:
    response = requests.get("http://ip-api.com/json/", timeout=10)
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "success":
        raise RuntimeError(data.get("message", "ip-api.com failed"))

    return add_currency({
        "provider": "ip-api.com",
        "ip": data.get("query"),
        "city": data.get("city"),
        "region": data.get("regionName"),
        "country": data.get("country"),
        "country_code": data.get("countryCode"),
        "latitude": data.get("lat"),
        "longitude": data.get("lon"),
        "timezone": data.get("timezone"),
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
# Time and budget-aware recommendation logic
# ---------------------------------------------------------------------------

BUDGET_TO_GOOGLE_PRICE_LEVELS = {
    "low": [
        "PRICE_LEVEL_FREE",
        "PRICE_LEVEL_INEXPENSIVE",
    ],
    "medium": [
        "PRICE_LEVEL_INEXPENSIVE",
        "PRICE_LEVEL_MODERATE",
    ],
    "high": [
        "PRICE_LEVEL_MODERATE",
        "PRICE_LEVEL_EXPENSIVE",
        "PRICE_LEVEL_VERY_EXPENSIVE",
    ],
    "unknown": [
        "PRICE_LEVEL_FREE",
        "PRICE_LEVEL_INEXPENSIVE",
        "PRICE_LEVEL_MODERATE",
        "PRICE_LEVEL_EXPENSIVE",
        "PRICE_LEVEL_VERY_EXPENSIVE",
    ],
}


GOOGLE_PRICE_LEVEL_LABELS = {
    "PRICE_LEVEL_FREE": "Free",
    "PRICE_LEVEL_INEXPENSIVE": "€",
    "PRICE_LEVEL_MODERATE": "€€",
    "PRICE_LEVEL_EXPENSIVE": "€€€",
    "PRICE_LEVEL_VERY_EXPENSIVE": "€€€€",
}


def get_time_context(geo: GeoContext | None) -> TimeContext:
    if geo is None or not geo.timezone:
        return TimeContext(
            local_datetime=None,
            local_time=None,
            hour=None,
            time_of_day="unknown",
        )

    try:
        now = datetime.now(ZoneInfo(geo.timezone))
        hour = now.hour

        if 5 <= hour < 11:
            time_of_day = "morning"
        elif 11 <= hour < 14:
            time_of_day = "lunch"
        elif 14 <= hour < 17:
            time_of_day = "afternoon"
        elif 17 <= hour < 22:
            time_of_day = "evening"
        else:
            time_of_day = "night"

        return TimeContext(
            local_datetime=now.isoformat(),
            local_time=now.strftime("%H:%M"),
            hour=hour,
            time_of_day=time_of_day,
        )

    except Exception:
        return TimeContext(
            local_datetime=None,
            local_time=None,
            hour=None,
            time_of_day="unknown",
        )


def get_budget_tier(
    daily_budget: float | None,
) -> Literal["low", "medium", "high", "unknown"]:
    if daily_budget is None:
        return "unknown"

    if daily_budget <= 12:
        return "low"

    if daily_budget <= 30:
        return "medium"

    return "high"


def choose_place_query(
    geo: GeoContext | None,
    time_context: TimeContext,
    daily_budget: float | None,
    activity_type: str | None = None,
) -> RecommendationContext:
    budget_tier = get_budget_tier(daily_budget)
    time_of_day = time_context.time_of_day
    country_code = geo.iso_country_code if geo else None
    activity = (activity_type or "").strip().lower()

    google_price_levels = BUDGET_TO_GOOGLE_PRICE_LEVELS.get(
        budget_tier,
        BUDGET_TO_GOOGLE_PRICE_LEVELS["unknown"],
    )

    if activity:
        if activity in {"food", "restaurant", "restaurants", "dinner"}:
            base_query = "restaurant"
        elif activity in {"cafe", "cafes", "coffee"}:
            base_query = "cafe coffee"
        elif activity in {"bar", "bars", "drinks"}:
            base_query = "bar"
        elif activity in {"shopping", "shops", "store", "stores"}:
            base_query = "shopping"
        elif activity in {"entertainment", "activity", "activities", "things to do"}:
            base_query = "entertainment"
        else:
            base_query = activity

        if budget_tier == "low":
            query = f"cheap {base_query} nearby"
            reason = (
                f"User requested {base_query}; low budget prioritizes inexpensive nearby options."
            )
        elif budget_tier == "high":
            query = f"best rated {base_query} nearby"
            reason = (
                f"User requested {base_query}; high budget prioritizes highly rated options."
            )
        else:
            query = f"{base_query} nearby"
            reason = f"User requested {base_query}; query is adapted to nearby options."

        return RecommendationContext(
            daily_budget=daily_budget,
            budget_tier=budget_tier,
            time_of_day=time_of_day,
            place_query=query,
            recommendation_reason=reason,
            google_price_levels=google_price_levels,
        )

    if country_code == "NL":
        if time_of_day == "morning":
            if budget_tier == "low":
                query = "cheap breakfast coffee McCafe bakery"
                reason = "Low morning budget: quick inexpensive coffee or breakfast options."
            elif budget_tier == "medium":
                query = "Bagels and Beans breakfast cafe"
                reason = "Medium morning budget: casual breakfast such as bagels, coffee, and cafés."
            elif budget_tier == "high":
                query = "best rated brunch cafe breakfast"
                reason = "Higher morning budget: brunch cafés and nicer breakfast places."
            else:
                query = "breakfast cafe"
                reason = "Morning with unknown budget: general breakfast cafés."

        elif time_of_day == "lunch":
            if budget_tier == "low":
                query = "cheap lunch sandwich takeaway"
                reason = "Low lunch budget: takeaway sandwiches and simple lunch spots."
            elif budget_tier == "medium":
                query = "lunch cafe"
                reason = "Medium lunch budget: cafés and casual lunch places."
            elif budget_tier == "high":
                query = "best rated restaurant lunch"
                reason = "Higher lunch budget: restaurants for a more complete lunch."
            else:
                query = "lunch nearby"
                reason = "Lunch with unknown budget: general lunch places."

        elif time_of_day == "afternoon":
            if budget_tier == "low":
                query = "coffee bakery cheap snack"
                reason = "Low afternoon budget: coffee, bakery, or snack options."
            elif budget_tier == "medium":
                query = "cafe pastries shopping"
                reason = "Medium afternoon budget: cafés, pastries, and casual shopping."
            elif budget_tier == "high":
                query = "best rated cafe shopping entertainment"
                reason = "Higher afternoon budget: nicer cafés, shopping, or entertainment."
            else:
                query = "cafe shopping nearby"
                reason = "Afternoon with unknown budget: cafés and shopping."

        elif time_of_day == "evening":
            if budget_tier == "low":
                query = "cheap dinner fast food"
                reason = "Low evening budget: fast food or inexpensive dinner options."
            elif budget_tier == "medium":
                query = "casual restaurant dinner"
                reason = "Medium evening budget: casual restaurants."
            elif budget_tier == "high":
                query = "best rated restaurant dinner cocktail bar"
                reason = "Higher evening budget: full-service dinner restaurants or cocktail bars."
            else:
                query = "restaurant dinner"
                reason = "Evening with unknown budget: general dinner restaurants."

        else:
            if budget_tier == "low":
                query = "cheap late night food"
                reason = "Night or unknown time with low budget: simple food options."
            elif budget_tier == "medium":
                query = "late night food bar"
                reason = "Night or unknown time with medium budget: late-night food or bars."
            elif budget_tier == "high":
                query = "best rated bar cocktail late night"
                reason = "Night or unknown time with higher budget: highly rated bars or late-night options."
            else:
                query = "food nearby"
                reason = "Unknown time and budget: general nearby food options."
    else:
        if time_of_day == "morning":
            if budget_tier == "low":
                query = "cheap breakfast coffee"
                reason = "Morning low budget: cheap breakfast and coffee places."
            elif budget_tier == "high":
                query = "best rated brunch cafe breakfast"
                reason = "Morning high budget: brunch or nicer breakfast cafés."
            else:
                query = "breakfast cafe"
                reason = "Morning recommendation: breakfast cafés."

        elif time_of_day == "lunch":
            if budget_tier == "low":
                query = "cheap lunch takeaway"
                reason = "Lunch low budget: takeaway and simple lunch spots."
            elif budget_tier == "high":
                query = "best rated restaurant lunch"
                reason = "Lunch high budget: restaurants."
            else:
                query = "lunch cafe"
                reason = "Lunch recommendation: cafés and casual food."

        elif time_of_day == "afternoon":
            if budget_tier == "low":
                query = "coffee bakery"
                reason = "Afternoon low budget: coffee and bakery options."
            elif budget_tier == "high":
                query = "best rated shopping entertainment cafe"
                reason = "Afternoon high budget: highly rated cafés, shopping, or entertainment."
            else:
                query = "cafe shopping entertainment"
                reason = "Afternoon recommendation: cafés, shopping, and activities."

        elif time_of_day == "evening":
            if budget_tier == "low":
                query = "cheap dinner fast food"
                reason = "Evening low budget: fast food or cheap dinner."
            elif budget_tier == "high":
                query = "best rated restaurant dinner bar"
                reason = "Evening high budget: dinner restaurants and bars."
            else:
                query = "casual restaurant dinner"
                reason = "Evening recommendation: casual dinner places."

        else:
            query = "food nearby"
            reason = "Fallback recommendation because time of day is unknown."

    return RecommendationContext(
        daily_budget=daily_budget,
        budget_tier=budget_tier,
        time_of_day=time_of_day,
        place_query=query,
        recommendation_reason=reason,
        google_price_levels=google_price_levels,
    )


# ---------------------------------------------------------------------------
# Google Places recommendations
# ---------------------------------------------------------------------------

GOOGLE_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"


def _haversine_distance_m(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    radius_m = 6_371_000

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(delta_lambda / 2) ** 2
    )

    return radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _google_price_level_to_budget_fit(
    price_level: str | None,
) -> Literal["low", "medium", "high", "unknown"]:
    if price_level in {"PRICE_LEVEL_FREE", "PRICE_LEVEL_INEXPENSIVE"}:
        return "low"

    if price_level == "PRICE_LEVEL_MODERATE":
        return "medium"

    if price_level in {"PRICE_LEVEL_EXPENSIVE", "PRICE_LEVEL_VERY_EXPENSIVE"}:
        return "high"

    return "unknown"


def _format_google_price_range(price_range: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(price_range, dict):
        return None

    start_price = price_range.get("startPrice")
    end_price = price_range.get("endPrice")

    def money_to_string(money: dict[str, Any] | None) -> str | None:
        if not isinstance(money, dict):
            return None

        currency_code = money.get("currencyCode")
        units = money.get("units")
        nanos = money.get("nanos", 0)

        if units is None:
            return None

        try:
            amount = float(units) + float(nanos) / 1_000_000_000
        except Exception:
            return None

        amount_text = str(int(amount)) if amount.is_integer() else f"{amount:.2f}"

        return f"{amount_text} {currency_code}" if currency_code else amount_text

    start = money_to_string(start_price)
    end = money_to_string(end_price)

    return {
        "raw": price_range,
        "start": start,
        "end": end,
        "label": f"{start}–{end}" if start and end else None,
    }


def _parse_google_timestamp(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None

    try:
        # Google timestamps may end in Z. Python prefers +00:00.
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except Exception:
        return None


def _is_open_for_next_hour(
    current_opening_hours: dict[str, Any],
    min_open_minutes: int = 60,
) -> tuple[bool, str | None, str]:
    """
    Returns:
        open_for_required_window, next_close_time_iso, explanation

    Uses currentOpeningHours.openNow and currentOpeningHours.nextCloseTime.
    If Google says openNow=True but does not provide nextCloseTime, we keep the place
    but mark the one-hour certainty as unknown.
    """

    open_now = current_opening_hours.get("openNow")

    if open_now is not True:
        return False, None, "Google did not confirm this place is open now."

    next_close_raw = current_opening_hours.get("nextCloseTime")
    next_close_dt = _parse_google_timestamp(next_close_raw)

    if next_close_dt is None:
        return True, None, (
            "Google confirms this place is open now, but nextCloseTime was not provided; "
            "cannot verify the full next hour."
        )

    now_utc = datetime.now(timezone.utc)
    required_until = now_utc + timedelta(minutes=min_open_minutes)

    if next_close_dt >= required_until:
        minutes_left = int((next_close_dt - now_utc).total_seconds() // 60)
        return True, next_close_dt.isoformat(), (
            f"Google confirms it is open now and next closing time is in about {minutes_left} minutes."
        )

    minutes_left = int((next_close_dt - now_utc).total_seconds() // 60)
    return False, next_close_dt.isoformat(), (
        f"Place is open now but appears to close in about {max(minutes_left, 0)} minutes, "
        f"which is less than the required {min_open_minutes} minutes."
    )


def _google_included_type_for_query(
    query: str,
    time_of_day: str,
    activity_type: str | None,
) -> str | None:
    q = f"{query} {activity_type or ''}".lower()

    if "bar" in q or "cocktail" in q or "drinks" in q or time_of_day == "night":
        return "bar"

    if "cafe" in q or "coffee" in q or "breakfast" in q or "brunch" in q:
        return "cafe"

    if "restaurant" in q or "dinner" in q or "lunch" in q or "food" in q:
        return "restaurant"

    if "shopping" in q or "mall" in q or "shops" in q or "store" in q:
        return "shopping_mall"

    if "cinema" in q or "movie" in q:
        return "movie_theater"

    if "museum" in q:
        return "museum"

    if "entertainment" in q:
        return "tourist_attraction"

    return None


def recommend_nearby_places(
    geo: GeoContext | None,
    query: str,
    limit: int,
    recommendation_context: RecommendationContext | None,
    activity_type: str | None = None,
    radius_m: float = 1500.0,
    min_rating: float = 4.0,
    min_open_minutes: int = 60,
    require_open_for_next_hour: bool = True,
) -> list[dict[str, Any]]:
    if geo is None or geo.latitude is None or geo.longitude is None:
        return []

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GOOGLE_MAPS_API_KEY is not set. Run: "
            'export GOOGLE_MAPS_API_KEY="your_google_maps_key"'
        )

    lat = float(geo.latitude)
    lon = float(geo.longitude)

    budget_tier = (
        recommendation_context.budget_tier
        if recommendation_context is not None
        else "unknown"
    )

    time_of_day = (
        recommendation_context.time_of_day
        if recommendation_context is not None
        else "unknown"
    )

    price_levels = (
        recommendation_context.google_price_levels
        if recommendation_context is not None
        else BUDGET_TO_GOOGLE_PRICE_LEVELS["unknown"]
    )

    included_type = _google_included_type_for_query(
        query=query,
        time_of_day=time_of_day,
        activity_type=activity_type,
    )

    page_size = min(max(limit * 4, 10), 20)

    body: dict[str, Any] = {
        "textQuery": query,
        "pageSize": page_size,
        "openNow": True,
        "minRating": min_rating,
        "priceLevels": price_levels,
        "rankPreference": "RELEVANCE",
        "locationBias": {
            "circle": {
                "center": {
                    "latitude": lat,
                    "longitude": lon,
                },
                "radius": radius_m,
            }
        },
    }

    if included_type:
        body["includedType"] = included_type
        body["strictTypeFiltering"] = False

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.id,"
            "places.displayName,"
            "places.formattedAddress,"
            "places.location,"
            "places.primaryType,"
            "places.types,"
            "places.rating,"
            "places.userRatingCount,"
            "places.priceLevel,"
            "places.priceRange,"
            "places.currentOpeningHours,"
            "places.regularOpeningHours,"
            "places.googleMapsUri,"
            "places.businessStatus"
        ),
    }

    response = requests.post(
        GOOGLE_TEXT_SEARCH_URL,
        headers=headers,
        json=body,
        timeout=12,
    )

    response.raise_for_status()

    data = response.json()
    places = data.get("places", [])

    normalized_places: list[dict[str, Any]] = []

    for place in places:
        opening_hours = place.get("currentOpeningHours") or {}

        open_for_next_hour, next_close_time, open_check_note = _is_open_for_next_hour(
            opening_hours,
            min_open_minutes=min_open_minutes,
        )

        if require_open_for_next_hour and not open_for_next_hour:
            continue

        location = place.get("location") or {}
        place_lat = location.get("latitude")
        place_lon = location.get("longitude")

        distance_m = None
        if place_lat is not None and place_lon is not None:
            distance_m = _haversine_distance_m(
                lat,
                lon,
                float(place_lat),
                float(place_lon),
            )

        rating = place.get("rating")
        user_rating_count = place.get("userRatingCount", 0)
        price_level = place.get("priceLevel")
        price_range = place.get("priceRange")
        estimated_budget_fit = _google_price_level_to_budget_fit(price_level)
        formatted_price_range = _format_google_price_range(price_range)

        normalized_places.append({
            "place_id": place.get("id"),
            "name": (place.get("displayName") or {}).get("text"),
            "formatted_address": place.get("formattedAddress"),
            "category": place.get("primaryType"),
            "types": place.get("types", []),

            "rating": rating,
            "user_rating_count": user_rating_count,

            "price_level": price_level,
            "price_level_label": GOOGLE_PRICE_LEVEL_LABELS.get(price_level, "Unknown"),
            "price_range": formatted_price_range,
            "estimated_budget_fit": estimated_budget_fit,

            "open_now": opening_hours.get("openNow") is True,
            "open_for_next_hour": open_for_next_hour,
            "min_open_minutes_required": min_open_minutes,
            "next_close_time": next_close_time,
            "opening_hours_status": (
                "verified_open_for_next_hour"
                if open_for_next_hour and next_close_time
                else "verified_open_now_next_hour_unknown"
                if open_for_next_hour
                else "not_open_for_required_window"
            ),
            "open_check_note": open_check_note,
            "current_opening_hours": opening_hours,
            "regular_opening_hours": place.get("regularOpeningHours"),

            "business_status": place.get("businessStatus"),
            "latitude": place_lat,
            "longitude": place_lon,
            "distance_m": round(distance_m, 1) if distance_m is not None else None,
            "google_maps_uri": place.get("googleMapsUri"),
            "source": "Google Places API Text Search",
        })

    def score(place: dict[str, Any]) -> tuple:
        rating = place.get("rating") or 0
        user_count = place.get("user_rating_count") or 0
        distance = place.get("distance_m")

        distance_score = -distance if distance is not None else -999_999

        fit = place.get("estimated_budget_fit")
        budget_fit_bonus = 1 if fit == budget_tier else 0

        open_window_bonus = 1 if place.get("open_for_next_hour") is True else 0

        return (
            open_window_bonus,
            rating,
            min(user_count, 2000),
            budget_fit_bonus,
            distance_score,
        )

    normalized_places.sort(key=score, reverse=True)

    return normalized_places[:limit]


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


def time_context_for_claude(time_context: TimeContext | None) -> str:
    if time_context is None:
        return "Local time context: not available."

    return (
        "Local time context:\n"
        f"- local_datetime: {time_context.local_datetime}\n"
        f"- local_time: {time_context.local_time}\n"
        f"- hour: {time_context.hour}\n"
        f"- time_of_day: {time_context.time_of_day}"
    )


def build_messages(
    transcript: str,
    geo: GeoContext | None,
    time_context: TimeContext | None,
) -> list[dict[str, str]]:
    user_text = f"""\
{geo_context_for_claude(geo)}

{time_context_for_claude(time_context)}

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


def call_claude(
    client: Anthropic,
    model: str,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    response = client.messages.create(
        model=model,
        max_tokens=200,
        temperature=0,
        system=PRICE_EXTRACTION_SYSTEM_PROMPT,
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
    time_context: TimeContext | None,
    recommendation_context: RecommendationContext | None,
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
            time=time_context,
            recommendation_context=recommendation_context,
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
            time=time_context,
            recommendation_context=recommendation_context,
            nearby_recommendations=nearby_recommendations,
        )

    return SuccessResult(
        status="success",
        amount=float(amount),
        currency_iso=currency_iso,
        raw_text=raw_text,
        currency_inferred=currency_source == "geolocation",
        geo=geo,
        time=time_context,
        recommendation_context=recommendation_context,
        nearby_recommendations=nearby_recommendations,
    )


# ---------------------------------------------------------------------------
# Public extraction function
# ---------------------------------------------------------------------------

def extract_price_with_geo_time_budget_and_places(
    transcript: str,
    daily_budget: float | None = None,
    place_query: str | None = None,
    activity_type: str | None = None,
    limit: int = 5,
    radius_m: float = 1500.0,
    min_rating: float = 4.0,
    min_open_minutes: int = 60,
    require_open_for_next_hour: bool = True,
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

    time_context = get_time_context(geo)

    recommendation_context = choose_place_query(
        geo=geo,
        time_context=time_context,
        daily_budget=daily_budget,
        activity_type=activity_type,
    )

    final_place_query = place_query or recommendation_context.place_query

    try:
        nearby_recommendations = recommend_nearby_places(
            geo=geo,
            query=final_place_query,
            limit=limit,
            recommendation_context=recommendation_context,
            activity_type=activity_type,
            radius_m=radius_m,
            min_rating=min_rating,
            min_open_minutes=min_open_minutes,
            require_open_for_next_hour=require_open_for_next_hour,
        )
    except Exception as exc:
        nearby_recommendations = [{
            "error": "place_recommendation_error",
            "details": str(exc),
            "source": "Google Places API Text Search",
        }]

    try:
        messages = build_messages(
            transcript=transcript,
            geo=geo,
            time_context=time_context,
        )

        parsed = call_claude(client, HAIKU, messages)

        if needs_retry(parsed):
            parsed = call_claude(client, SONNET, messages)

        return to_result(
            parsed=parsed,
            geo=geo,
            time_context=time_context,
            recommendation_context=recommendation_context,
            nearby_recommendations=nearby_recommendations,
        )

    except Exception as exc:
        return FailureResult(
            status="failure",
            reason="api_error",
            details=str(exc),
        )


# ---------------------------------------------------------------------------
# Public geo helper
# ---------------------------------------------------------------------------

def get_geo_context() -> GeoContext | None:
    """Detect user location with coordinates. Returns None on failure."""
    try:
        return geo_from_location_result(detect_location())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Recommendation agent — internal helpers
# ---------------------------------------------------------------------------

def _plan_search_query(
    geo: GeoContext | None,
    time_context: TimeContext,
    budget_info: BudgetInfo,
    client: Anthropic,
) -> tuple[str, str | None]:
    """
    Claude Haiku decides what type of place to search for right now.
    Returns (query, included_type). Falls back to rule-based on error.
    """
    budget_tier = get_budget_tier(budget_info.budget_left_per_day_eur)
    parts = [f"Time of day: {time_context.time_of_day}"]
    if time_context.local_time:
        parts[0] += f" (local time: {time_context.local_time})"
    parts.append(f"Budget tier: {budget_tier}")
    if budget_info.budget_left_per_day_eur is not None:
        parts.append(f"Daily budget remaining: €{budget_info.budget_left_per_day_eur:.2f}")
    if geo:
        parts.append(f"Location: {geo.city or 'unknown'}, {geo.country_name or 'unknown'}")
    else:
        parts.append("Location: unknown")

    try:
        resp = client.messages.create(
            model=HAIKU,
            max_tokens=150,
            temperature=0,
            system=QUERY_PLANNER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "\n".join(parts)}],
        )
        data = json.loads(resp.content[0].text.strip())
        return data.get("query", "food nearby"), data.get("included_type")
    except Exception:
        ctx = choose_place_query(geo, time_context, budget_info.budget_left_per_day_eur)
        return ctx.place_query, None


def _curate_recommendations(
    places: list[dict[str, Any]],
    budget_info: BudgetInfo,
    time_context: TimeContext,
    geo: GeoContext | None,
    client: Anthropic,
) -> list[AgentRecommendation]:
    """
    Claude Sonnet curates and explains the best nearby places.
    Falls back to a simple top-N list if the agent call fails.
    """
    if not places:
        return []

    budget_lines = [
        f"Total trip budget: €{budget_info.total_budget_eur:.2f}",
        f"Spent so far: €{budget_info.spent_eur:.2f}",
        f"Remaining: €{budget_info.remaining_eur:.2f}",
    ]
    if budget_info.days_remaining is not None:
        budget_lines.append(f"Days remaining: {budget_info.days_remaining}")
    if budget_info.budget_per_day_eur is not None:
        budget_lines.append(f"Planned daily budget: €{budget_info.budget_per_day_eur:.2f}")
    if budget_info.budget_left_per_day_eur is not None:
        budget_lines.append(f"Daily budget remaining today: €{budget_info.budget_left_per_day_eur:.2f}")

    location_str = (
        f"{geo.city or ''}, {geo.country_name or ''}".strip(", ")
        if geo else "unknown"
    )

    places_json = json.dumps(
        [
            {
                "name": p.get("name"),
                "category": p.get("category"),
                "distance_m": p.get("distance_m"),
                "price_level": p.get("price_level_label"),
                "rating": p.get("rating"),
                "user_rating_count": p.get("user_rating_count"),
                "formatted_address": p.get("formatted_address"),
                "google_maps_uri": p.get("google_maps_uri"),
            }
            for p in places
        ],
        ensure_ascii=False,
        indent=2,
    )

    user_text = (
        "BUDGET SNAPSHOT:\n"
        + "\n".join(budget_lines)
        + f"\n\nTIME CONTEXT:\n"
        + f"- Local time: {time_context.local_time or 'unknown'}\n"
        + f"- Time of day: {time_context.time_of_day}\n"
        + f"\nLOCATION: {location_str}\n"
        + f"\nNEARBY PLACES FROM GOOGLE MAPS:\n{places_json}\n"
        + "\nCurate the best 3–5 recommendations for this traveler right now."
    )

    try:
        resp = client.messages.create(
            model=SONNET,
            max_tokens=1200,
            temperature=0.3,
            system=RECOMMENDATION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_text}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return [
            AgentRecommendation(
                place_name=r.get("place_name", ""),
                category=r.get("category"),
                distance_m=r.get("distance_m"),
                price_level_label=r.get("price_level_label", ""),
                google_maps_uri=r.get("google_maps_uri"),
                rating=r.get("rating"),
                user_rating_count=r.get("user_rating_count"),
                formatted_address=r.get("formatted_address"),
                why=r.get("why", ""),
            )
            for r in json.loads(raw)
            if isinstance(r, dict)
        ]
    except Exception:
        return [
            AgentRecommendation(
                place_name=p.get("name", ""),
                category=p.get("category"),
                distance_m=p.get("distance_m"),
                price_level_label=p.get("price_level_label", ""),
                google_maps_uri=p.get("google_maps_uri"),
                rating=p.get("rating"),
                user_rating_count=p.get("user_rating_count"),
                formatted_address=p.get("formatted_address"),
                why="Highly rated and nearby.",
            )
            for p in places[:5]
        ]


# ---------------------------------------------------------------------------
# Recommendation agent — main public entry point
# ---------------------------------------------------------------------------

def get_nearby_recommendations(
    geo: GeoContext,
    time_context: TimeContext,
    budget_info: BudgetInfo,
    *,
    client: Anthropic | None = None,
    limit: int = 5,
    radius_m: float = 1500.0,
    min_rating: float = 4.0,
) -> list[AgentRecommendation]:
    """
    AI-powered nearby recommendation engine.

    Pipeline:
      1. Claude Haiku decides what type of place to search for
      2. Google Places API fetches candidates within radius_m
      3. Claude Sonnet curates and explains the best 3–5 picks

    Requires ANTHROPIC_API_KEY and GOOGLE_MAPS_API_KEY (set via api_keys.py
    or environment variables).
    """
    import os
    from api_keys import get_anthropic_api_key, get_google_maps_api_key

    os.environ.setdefault("ANTHROPIC_API_KEY", get_anthropic_api_key())
    os.environ.setdefault("GOOGLE_MAPS_API_KEY", get_google_maps_api_key())

    _client = client or Anthropic()

    query, _ = _plan_search_query(geo, time_context, budget_info, _client)

    budget_tier = get_budget_tier(budget_info.budget_left_per_day_eur)
    rec_ctx = RecommendationContext(
        daily_budget=budget_info.budget_left_per_day_eur,
        budget_tier=budget_tier,
        time_of_day=time_context.time_of_day,
        place_query=query,
        recommendation_reason="",
        google_price_levels=BUDGET_TO_GOOGLE_PRICE_LEVELS.get(
            budget_tier, BUDGET_TO_GOOGLE_PRICE_LEVELS["unknown"]
        ),
    )

    places = recommend_nearby_places(
        geo=geo,
        query=query,
        limit=limit * 3,
        recommendation_context=rec_ctx,
        radius_m=radius_m,
        min_rating=min_rating,
        require_open_for_next_hour=False,
    )

    return _curate_recommendations(places, budget_info, time_context, geo, _client)


# ---------------------------------------------------------------------------
# JSON helpers
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract spoken price with geolocation-aware currency inference, "
            "local time, daily budget, and Google Maps place recommendations."
        )
    )

    parser.add_argument(
        "transcript",
        help="STT transcript containing a spoken price.",
    )

    parser.add_argument(
        "--daily-budget",
        type=float,
        default=None,
        help="Daily budget in the local currency, e.g. 10 or 50.",
    )

    parser.add_argument(
        "--place-query",
        default=None,
        help=(
            'Optional override for Google Places text search, e.g. '
            '"cafes", "restaurants", "shopping mall", "cocktail bar". '
            "If omitted, the script chooses one based on budget and time of day."
        ),
    )

    parser.add_argument(
        "--activity-type",
        default=None,
        help=(
            "Optional activity category, e.g. food, cafe, restaurant, bar, "
            "shopping, entertainment."
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of nearby recommendations.",
    )

    parser.add_argument(
        "--radius-m",
        type=float,
        default=1500.0,
        help="Google Places location bias radius in meters.",
    )

    parser.add_argument(
        "--min-rating",
        type=float,
        default=4.0,
        help="Minimum Google rating for returned places.",
    )

    parser.add_argument(
        "--min-open-minutes",
        type=int,
        default=60,
        help="Require places to remain open for at least this many minutes.",
    )

    parser.add_argument(
        "--allow-closing-soon",
        action="store_true",
        help=(
            "If set, include places that are open now even if they close before "
            "--min-open-minutes."
        ),
    )

    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON.",
    )

    args = parser.parse_args()

    result = extract_price_with_geo_time_budget_and_places(
        transcript=args.transcript,
        daily_budget=args.daily_budget,
        place_query=args.place_query,
        activity_type=args.activity_type,
        limit=args.limit,
        radius_m=args.radius_m,
        min_rating=args.min_rating,
        min_open_minutes=args.min_open_minutes,
        require_open_for_next_hour=not args.allow_closing_soon,
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