from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Literal

import requests
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class GeoContext:
    country_name: str | None
    iso_country_code: str | None
    city: str | None = None
    region: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    timezone: str | None = None
    provider: str | None = None


@dataclass
class EventResult:
    status: Literal["success", "failure"]
    geo: GeoContext | None
    local_datetime: str | None
    events: list[dict[str, Any]]
    details: str | None = None


# ---------------------------------------------------------------------------
# Location detection (mirrors geo1.py)
# ---------------------------------------------------------------------------

def _add_meta(result: Dict[str, Any]) -> Dict[str, Any]:
    result["accuracy_note"] = (
        "Approximate IP-based location. VPN, proxy, mobile network, "
        "or corporate network may give the wrong country."
    )
    return result


def _provider_ipwho_is() -> Dict[str, Any]:
    resp = requests.get("https://ipwho.is/", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success") is False:
        raise RuntimeError(data.get("message", "ipwho.is failed"))
    tz = data.get("timezone")
    return _add_meta({
        "provider": "ipwho.is",
        "city": data.get("city"),
        "region": data.get("region"),
        "country": data.get("country"),
        "country_code": data.get("country_code"),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": tz.get("id") if isinstance(tz, dict) else tz,
    })


def _provider_ip_api_com() -> Dict[str, Any]:
    resp = requests.get("http://ip-api.com/json/", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(data.get("message", "ip-api.com failed"))
    return _add_meta({
        "provider": "ip-api.com",
        "city": data.get("city"),
        "region": data.get("regionName"),
        "country": data.get("country"),
        "country_code": data.get("countryCode"),
        "latitude": data.get("lat"),
        "longitude": data.get("lon"),
        "timezone": data.get("timezone"),
    })


def _provider_ipapi_co() -> Dict[str, Any]:
    resp = requests.get("https://ipapi.co/json/", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return _add_meta({
        "provider": "ipapi.co",
        "city": data.get("city"),
        "region": data.get("region"),
        "country": data.get("country_name"),
        "country_code": data.get("country_code"),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": data.get("timezone"),
    })


def detect_location() -> Dict[str, Any]:
    providers: list[Callable[[], Dict[str, Any]]] = [
        _provider_ipwho_is,
        _provider_ip_api_com,
        _provider_ipapi_co,
    ]
    errors = []
    for provider in providers:
        try:
            return provider()
        except Exception as exc:
            errors.append({"provider": provider.__name__, "error": str(exc)})
    raise RuntimeError(json.dumps({
        "message": "All IP geolocation providers failed.",
        "provider_errors": errors,
    }, ensure_ascii=False))


def geo_from_location(loc: Dict[str, Any]) -> GeoContext:
    return GeoContext(
        country_name=loc.get("country"),
        iso_country_code=loc.get("country_code"),
        city=loc.get("city"),
        region=loc.get("region"),
        latitude=loc.get("latitude"),
        longitude=loc.get("longitude"),
        timezone=loc.get("timezone"),
        provider=loc.get("provider"),
    )


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def local_now(geo: GeoContext | None) -> datetime | None:
    if geo is None or not geo.timezone:
        return None
    try:
        return datetime.now(ZoneInfo(geo.timezone))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Ticketmaster event search
# ---------------------------------------------------------------------------

TICKETMASTER_URL = "https://app.ticketmaster.com/discovery/v2/events.json"


def _parse_price_range(price_ranges: list[dict] | None) -> dict[str, Any] | None:
    if not price_ranges:
        return None
    pr = price_ranges[0]
    currency = pr.get("currency")
    mn = pr.get("min")
    mx = pr.get("max")
    label_parts = []
    if mn is not None:
        label_parts.append(f"{mn}")
    if mx is not None and mx != mn:
        label_parts.append(f"{mx}")
    label = ("–".join(label_parts) + f" {currency}") if label_parts and currency else None
    return {"min": mn, "max": mx, "currency": currency, "label": label}


def _extract_venue(venue: dict[str, Any]) -> dict[str, Any]:
    city = (venue.get("city") or {}).get("name")
    country = (venue.get("country") or {}).get("name")
    state = (venue.get("state") or {}).get("name")
    loc = venue.get("location") or {}
    return {
        "name": venue.get("name"),
        "address": (venue.get("address") or {}).get("line1"),
        "city": city,
        "state": state,
        "country": country,
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
    }


def find_nearby_events(
    geo: GeoContext,
    keyword: str | None = None,
    classification: str | None = None,
    radius_km: float = 20.0,
    limit: int = 10,
    days_ahead: int = 30,
) -> list[dict[str, Any]]:
    api_key = os.environ.get("TICKETMASTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "TICKETMASTER_API_KEY is not set. Run: "
            'export TICKETMASTER_API_KEY="your_ticketmaster_key"'
        )

    if geo.latitude is None or geo.longitude is None:
        return []

    lat = float(geo.latitude)
    lon = float(geo.longitude)

    now_utc = datetime.now(timezone.utc)
    end_dt = now_utc + timedelta(days=days_ahead)

    params: dict[str, Any] = {
        "apikey": api_key,
        "latlong": f"{lat},{lon}",
        "radius": int(radius_km),
        "unit": "km",
        "size": min(limit * 3, 50),
        "sort": "date,asc",
        "startDateTime": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if keyword:
        params["keyword"] = keyword
    if classification:
        params["classificationName"] = classification

    resp = requests.get(TICKETMASTER_URL, params=params, timeout=12)
    resp.raise_for_status()
    data = resp.json()

    raw_events = (
        (data.get("_embedded") or {}).get("events") or []
    )

    results: list[dict[str, Any]] = []

    for ev in raw_events:
        # Dates
        dates = ev.get("dates") or {}
        start = dates.get("start") or {}
        date_str = start.get("localDate")
        time_str = start.get("localTime")
        date_tbd = start.get("dateTBD", False)
        time_tbd = start.get("timeTBD", False)

        # Venues
        embedded = ev.get("_embedded") or {}
        venues_raw = embedded.get("venues") or []
        venues = [_extract_venue(v) for v in venues_raw]

        # Distance from user
        distance_m = None
        if venues:
            v0 = venues[0]
            try:
                vlat = float(v0["latitude"])
                vlon = float(v0["longitude"])
                distance_m = round(_haversine_m(lat, lon, vlat, vlon), 1)
            except (TypeError, ValueError):
                pass

        # Classification / genre
        classifications = ev.get("classifications") or []
        segment = genre = sub_genre = None
        if classifications:
            cl = classifications[0]
            segment = (cl.get("segment") or {}).get("name")
            genre = (cl.get("genre") or {}).get("name")
            sub_genre = (cl.get("subGenre") or {}).get("name")

        # Images — pick highest resolution
        images = ev.get("images") or []
        image_url = None
        if images:
            best = max(images, key=lambda i: (i.get("width") or 0) * (i.get("height") or 0))
            image_url = best.get("url")

        # Ticket links
        ticket_url = ev.get("url")  # Ticketmaster event/purchase page
        sales = (ev.get("sales") or {}).get("public") or {}
        tickets_on_sale = sales.get("startTBD") is False and bool(sales.get("startDateTime"))

        results.append({
            "id": ev.get("id"),
            "name": ev.get("name"),
            "url": ev.get("url"),
            "ticket_url": ticket_url,
            "tickets_on_sale": tickets_on_sale,
            "date": date_str,
            "time": time_str,
            "date_tbd": date_tbd,
            "time_tbd": time_tbd,
            "segment": segment,
            "genre": genre,
            "sub_genre": sub_genre,
            "venues": venues,
            "distance_m": distance_m,
            "price_range": _parse_price_range(ev.get("priceRanges")),
            "image_url": image_url,
            "info": ev.get("info"),
            "please_note": ev.get("pleaseNote"),
            "source": "Ticketmaster Discovery API",
        })

    # Sort by distance then date
    results.sort(key=lambda e: (
        e["distance_m"] if e["distance_m"] is not None else 999_999,
        e["date"] or "",
    ))

    return results[:limit]


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def get_nearby_events(
    keyword: str | None = None,
    classification: str | None = None,
    radius_km: float = 20.0,
    limit: int = 10,
    days_ahead: int = 30,
) -> EventResult:
    try:
        location = detect_location()
        geo = geo_from_location(location)
    except Exception as exc:
        return EventResult(
            status="failure",
            geo=None,
            local_datetime=None,
            events=[],
            details=str(exc),
        )

    now = local_now(geo)
    local_datetime_str = now.isoformat() if now else None

    try:
        events = find_nearby_events(
            geo=geo,
            keyword=keyword,
            classification=classification,
            radius_km=radius_km,
            limit=limit,
            days_ahead=days_ahead,
        )
    except Exception as exc:
        return EventResult(
            status="failure",
            geo=geo,
            local_datetime=local_datetime_str,
            events=[],
            details=str(exc),
        )

    return EventResult(
        status="success",
        geo=geo,
        local_datetime=local_datetime_str,
        events=events,
    )


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def dataclass_to_jsonable(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: dataclass_to_jsonable(v) for k, v in obj.__dict__.items()}
    if isinstance(obj, list):
        return [dataclass_to_jsonable(i) for i in obj]
    if isinstance(obj, dict):
        return {k: dataclass_to_jsonable(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Find events near your current location using IP geolocation "
            "and the Ticketmaster Discovery API. Returns a JSON with event "
            "links and ticket purchase links."
        )
    )

    parser.add_argument(
        "--keyword",
        default=None,
        help='Keyword to filter events, e.g. "Taylor Swift", "jazz", "comedy".',
    )

    parser.add_argument(
        "--classification",
        default=None,
        help='Event classification, e.g. "Music", "Sports", "Arts & Theatre", "Family".',
    )

    parser.add_argument(
        "--radius-km",
        type=float,
        default=20.0,
        help="Search radius in kilometres (default: 20).",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of events to return (default: 10).",
    )

    parser.add_argument(
        "--days-ahead",
        type=int,
        default=30,
        help="How many days ahead to search (default: 30).",
    )

    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )

    args = parser.parse_args()

    result = get_nearby_events(
        keyword=args.keyword,
        classification=args.classification,
        radius_km=args.radius_km,
        limit=args.limit,
        days_ahead=args.days_ahead,
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
