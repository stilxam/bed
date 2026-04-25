"""
server.py — bunq Travel Buddy · FastAPI backend
================================================

Serves the retro-futurism SPA from /static and exposes the existing
Python pipeline (extractor, fx_converter, trips, bunq_balance) as a
clean REST API consumed by static/index.html.

Run:
    uvicorn server:app --reload --port 8000
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import sqlite3
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import hyperparams
from api_keys import get_anthropic_api_key
from trips import create_trip, delete_trip, get_trip, load_trips, update_trip

# ── Bootstrap API keys ────────────────────────────────────────────────────────
os.environ.setdefault("ANTHROPIC_API_KEY", get_anthropic_api_key())

app = FastAPI(title="bunq Travel Buddy", docs_url="/api/docs")

# ── Static files ──────────────────────────────────────────────────────────────
_STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(_STATIC / "index.html")


# ── Geo ───────────────────────────────────────────────────────────────────────

@app.get("/api/geo")
async def geo():
    try:
        from geo import get_location
        loc = get_location()
        if loc:
            return {"country_name": loc.country_name, "currency_iso": loc.currency_iso}
    except Exception:
        pass
    return {"country_name": None, "currency_iso": None}


# ── Balance ───────────────────────────────────────────────────────────────────

@app.get("/api/balance")
async def balance():
    try:
        from bunq_balance import get_balance, get_all_balances
        return {"balance": get_balance(), "accounts": get_all_balances()}
    except Exception as exc:
        raise HTTPException(502, detail=str(exc))


# ── Scan (single price) ───────────────────────────────────────────────────────

@app.post("/api/scan")
async def scan(
    file: UploadFile = File(...),
    payment_type: str = Form("card"),
):
    """Extract a single price from an image and convert to EUR."""
    data = await file.read()
    suffix = Path(file.filename or "img.jpg").suffix or ".jpg"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)

    try:
        from anthropic import Anthropic
        from extractor import (
            AmountAmbiguousResult, CurrencyAmbiguousResult,
            FailureResult, GeoContext, SuccessResult, extract_from_image,
        )
        from fx_converter import convert_to_eur

        client = Anthropic()

        # Geo hint
        geo = _build_geo()

        result = extract_from_image(tmp_path, geo=geo, client=client)

        if isinstance(result, FailureResult):
            raise HTTPException(422, detail=f"No price found: {result.reason}")
        if isinstance(result, AmountAmbiguousResult):
            raise HTTPException(422, detail="Amount is ambiguous — please enter manually")
        if isinstance(result, CurrencyAmbiguousResult):
            raise HTTPException(422, detail=f"Currency unclear — suggest {result.suggested_currency_iso or 'unknown'}")

        # SuccessResult — compute both payment types for rate comparison
        fx_card     = convert_to_eur([(result.amount, result.currency_iso)], payment_type="card")[0]
        fx_transfer = convert_to_eur([(result.amount, result.currency_iso)], payment_type="transfer")[0]
        fx = fx_card if payment_type == "card" else fx_transfer
        return {
            "input_amount":   result.amount,
            "input_currency": result.currency_iso,
            "total_eur":      fx.total_eur,
            "base_eur":       fx.base_eur,
            "total_fees":     fx.total_fees,
            "service_fee":    fx.service_fee,
            "weekend_fee":    fx.weekend_fee,
            "source":         fx.source,
            "payment_type":   fx.payment_type,
            "is_weekend":     fx.is_weekend,
            "card": {
                "total_eur":  fx_card.total_eur,
                "total_fees": fx_card.total_fees,
                "source":     fx_card.source,
                "is_weekend": fx_card.is_weekend,
            },
            "transfer": {
                "total_eur":   fx_transfer.total_eur,
                "total_fees":  fx_transfer.total_fees,
                "weekend_fee": fx_transfer.weekend_fee,
                "source":      fx_transfer.source,
                "is_weekend":  fx_transfer.is_weekend,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)


# ── Menu scan ─────────────────────────────────────────────────────────────────

@app.post("/api/menu")
async def menu(
    file: UploadFile = File(...),
    payment_type: str = Form("card"),
):
    """Extract all priced items from a menu image, translate, and convert to EUR."""
    data = await file.read()
    suffix = Path(file.filename or "menu.jpg").suffix or ".jpg"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)

    try:
        from anthropic import Anthropic
        from extractor import extract_menu_from_image
        from fx_converter import convert_to_eur

        client = Anthropic()
        geo = _build_geo()
        menu_result = extract_menu_from_image(tmp_path, geo=geo, client=client)

        # Batch FX conversion for all items that have an amount
        convertible = [
            item for item in menu_result.items
            if item.amount is not None and menu_result.currency_iso
        ]
        fx_map: dict[int, object] = {}
        if convertible:
            pairs = [(item.amount, menu_result.currency_iso) for item in convertible]
            fx_list = convert_to_eur(pairs, payment_type=payment_type)
            for i, fx in enumerate(fx_list):
                fx_map[id(convertible[i])] = fx

        items_out = []
        for item in menu_result.items:
            fx = fx_map.get(id(item))
            items_out.append({
                "original_name":  item.original_name,
                "translated_name": item.translated_name,
                "amount":         item.amount,
                "original_price": (
                    f"{item.amount:,.0f} {menu_result.currency_iso}"
                    if item.amount is not None and menu_result.currency_iso else "—"
                ),
                "eur": fx.total_eur if fx and fx.base_rate > 0 else None,
            })

        return {"currency_iso": menu_result.currency_iso, "items": items_out}

    except Exception as exc:
        raise HTTPException(500, detail=str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)


# ── Voice ─────────────────────────────────────────────────────────────────────

@app.post("/api/voice")
async def voice(
    file: UploadFile = File(...),
    payment_type: str = Form("card"),
):
    """Transcribe speech → extract amount/currency → convert to EUR.

    Returns status="success" with FX data, "needs_currency" if currency was
    not detected (frontend should call /api/convert with user-supplied currency),
    or "no_amount" if nothing was found.
    """
    data = await file.read()
    suffix = Path(file.filename or "audio.webm").suffix or ".webm"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)

    try:
        from api_keys import get_aws_region
        from auto_voice_to_eur_aws import (
            download_transcript_text, extract_money_with_bedrock,
            get_language_metadata, start_transcribe_job,
            upload_to_s3, wait_for_transcribe_job,
        )
        from fx_converter import convert_to_eur

        bucket = hyperparams.AWS_S3_BUCKET
        if not bucket:
            raise HTTPException(503, detail="AWS_S3_BUCKET not configured in hyperparams.py")

        region = get_aws_region()
        uid = uuid.uuid4().hex
        ext = suffix.lstrip(".")
        s3_key = f"{hyperparams.AWS_S3_PREFIX.rstrip('/')}/voice_{uid}.{ext}"
        job_name = f"voice-{uid}"

        media_uri = upload_to_s3(tmp_path, bucket, s3_key, region)
        start_transcribe_job(
            media_uri=media_uri, region=region, job_name=job_name,
            language_options=hyperparams.TRANSCRIBE_LANGUAGE_OPTIONS or None,
            identify_multiple_languages=hyperparams.TRANSCRIBE_MULTI_LANGUAGE,
        )
        job = wait_for_transcribe_job(job_name, region)
        transcript = download_transcript_text(job["Transcript"]["TranscriptFileUri"])
        money = extract_money_with_bedrock(
            transcript=transcript, region=region,
            model_id=hyperparams.AWS_BEDROCK_MODEL_ID,
        )

        amount = money.get("amount")
        currency = money.get("currency")

        base = {
            "transcript": transcript,
            "amount": amount,
            "currency": currency,
            "confidence": money.get("confidence"),
            **get_language_metadata(job),
        }

        if amount is None:
            return {**base, "status": "no_amount"}
        if not currency:
            return {**base, "status": "needs_currency"}

        fx_card     = convert_to_eur([(float(amount), str(currency).upper())], payment_type="card")[0]
        fx_transfer = convert_to_eur([(float(amount), str(currency).upper())], payment_type="transfer")[0]
        fx = fx_card if payment_type == "card" else fx_transfer
        return {
            **base,
            "status":       "success",
            "total_eur":    fx.total_eur,
            "total_fees":   fx.total_fees,
            "source":       fx.source,
            "payment_type": fx.payment_type,
            "card": {
                "total_eur":  fx_card.total_eur,
                "total_fees": fx_card.total_fees,
                "source":     fx_card.source,
                "is_weekend": fx_card.is_weekend,
            },
            "transfer": {
                "total_eur":   fx_transfer.total_eur,
                "total_fees":  fx_transfer.total_fees,
                "weekend_fee": fx_transfer.weekend_fee,
                "source":      fx_transfer.source,
                "is_weekend":  fx_transfer.is_weekend,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)


# ── Direct FX conversion (used by voice currency-picker) ──────────────────────

class ConvertRequest(BaseModel):
    amount: float
    currency: str
    payment_type: str = "card"


@app.post("/api/convert")
async def convert(body: ConvertRequest):
    """Convert a known amount+currency to EUR. Used when voice detected an amount
    but not the currency — the frontend asks the user then calls this endpoint."""
    try:
        from fx_converter import convert_to_eur
        pair = (body.amount, body.currency.upper().strip())
        fx_card     = convert_to_eur([pair], payment_type="card")[0]
        fx_transfer = convert_to_eur([pair], payment_type="transfer")[0]
        fx = fx_card if body.payment_type == "card" else fx_transfer
        return {
            "total_eur":    fx.total_eur,
            "total_fees":   fx.total_fees,
            "source":       fx.source,
            "payment_type": fx.payment_type,
            "card": {
                "total_eur":  fx_card.total_eur,
                "total_fees": fx_card.total_fees,
                "source":     fx_card.source,
                "is_weekend": fx_card.is_weekend,
            },
            "transfer": {
                "total_eur":   fx_transfer.total_eur,
                "total_fees":  fx_transfer.total_fees,
                "weekend_fee": fx_transfer.weekend_fee,
                "source":      fx_transfer.source,
                "is_weekend":  fx_transfer.is_weekend,
            },
        }
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))


# ── Trips CRUD ────────────────────────────────────────────────────────────────

class TripCreate(BaseModel):
    name: str
    budget_eur: float
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    default_currency: Optional[str] = None


class TripUpdate(BaseModel):
    name: Optional[str] = None
    budget_eur: Optional[float] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    default_currency: Optional[str] = None
    default_own_currency: Optional[str] = None


@app.get("/api/trips")
async def list_trips():
    return [asdict(t) for t in load_trips()]


@app.post("/api/trips", status_code=201)
async def new_trip(body: TripCreate):
    trip = create_trip(
        name=body.name,
        budget_eur=body.budget_eur,
        start_date=body.start_date,
        end_date=body.end_date,
        default_currency=body.default_currency,
    )
    return asdict(trip)


@app.get("/api/trips/{trip_id}")
async def fetch_trip(trip_id: str):
    trip = get_trip(trip_id)
    if not trip:
        raise HTTPException(404, detail="Trip not found")
    return asdict(trip)


@app.patch("/api/trips/{trip_id}")
async def patch_trip(trip_id: str, body: TripUpdate):
    trip = update_trip(
        trip_id,
        name=body.name,
        budget_eur=body.budget_eur,
        start_date=body.start_date if body.start_date is not None else ...,
        end_date=body.end_date if body.end_date is not None else ...,
        default_currency=body.default_currency if body.default_currency is not None else ...,
        default_own_currency=body.default_own_currency if body.default_own_currency is not None else ...,
    )
    if not trip:
        raise HTTPException(404, detail="Trip not found")
    return asdict(trip)


@app.delete("/api/trips/{trip_id}", status_code=204)
async def remove_trip(trip_id: str):
    if not delete_trip(trip_id):
        raise HTTPException(404, detail="Trip not found")


# ── Trip dashboard ────────────────────────────────────────────────────────────

@app.get("/api/trips/{trip_id}/dashboard")
async def trip_dashboard(trip_id: str):
    """Return budget stats and payment log for a trip."""
    trip = get_trip(trip_id)
    if not trip:
        raise HTTPException(404, detail="Trip not found")

    own_ccy = trip.default_own_currency or "EUR"
    since = trip.start_date or (trip.created_at[:10] if trip.created_at else None)
    until = trip.end_date

    payments: list[dict] = []
    pay_error = ""
    try:
        from bunq_balance import get_payments
        payments = get_payments(since_date=since, until_date=until, count=200)
        # Fallback: if date filter yields nothing, return latest payments unfiltered
        # (common in sandbox where all transactions are created on the same day)
        if not payments:
            payments = get_payments(count=50)
    except Exception as exc:
        pay_error = str(exc)

    spent = sum(
        abs(float(p["amount"]))
        for p in payments
        if p.get("type") == "debit" and p.get("currency") == own_ccy
    )
    remaining = max(0.0, trip.budget_eur - spent)

    return {
        # Fields used by the home payment log
        "spent":        spent,
        "own_ccy":      own_ccy,
        "payments":     payments,
        "pay_error":    pay_error,
        # Fields used by the explore trip card
        "budget_eur":   trip.budget_eur,
        "spent_eur":    spent,
        "remaining_eur": remaining,
    }


# ── Events (Ticketmaster) ─────────────────────────────────────────────────────

@app.get("/api/events")
async def events_nearby(radius: int = 20, size: int = 8, trip_id: Optional[str] = None):
    """Return upcoming events near the user's current location, or at the trip destination."""
    try:
        import requests as _req
        from api_keys import get_ticketmaster_api_key

        api_key = get_ticketmaster_api_key()

        # Unambiguous currency → ISO 3166-1 country code (EUR excluded — maps to many countries)
        _CURRENCY_TO_COUNTRY = {
            "JPY": "JP", "USD": "US", "GBP": "GB", "CHF": "CH",
            "NOK": "NO", "SEK": "SE", "DKK": "DK", "PLN": "PL",
            "CZK": "CZ", "HUF": "HU", "AUD": "AU", "CAD": "CA",
            "NZD": "NZ", "KRW": "KR", "INR": "IN", "CNY": "CN",
            "BRL": "BR", "MXN": "MX", "ZAR": "ZA", "THB": "TH",
            "SGD": "SG", "HKD": "HK", "TRY": "TR", "AED": "AE",
        }

        country_code = None
        lat, lon = None, None

        if trip_id:
            trip = get_trip(trip_id)
            if trip and trip.default_currency:
                country_code = _CURRENCY_TO_COUNTRY.get(trip.default_currency.upper())

        if not country_code:
            try:
                from recommendations import detect_location, geo_from_location_result
                loc = detect_location()
                if loc:
                    geo = geo_from_location_result(loc)
                    lat, lon = geo.latitude, geo.longitude
            except Exception:
                pass

        if not country_code and (lat is None or lon is None):
            raise HTTPException(503, detail="Could not determine location for events")

        params: dict = {
            "apikey": api_key,
            "sort": "date,asc",
            "size": size,
            "locale": "*",
        }
        if country_code:
            params["countryCode"] = country_code
        else:
            params["latlong"] = f"{lat},{lon}"
            params["radius"] = radius
            params["unit"] = "km"
        resp = _req.get(
            "https://app.ticketmaster.com/discovery/v2/events.json",
            params=params,
            timeout=12,
        )
        resp.raise_for_status()
        raw = resp.json().get("_embedded", {}).get("events", [])

        events = []
        for ev in raw:
            dates = ev.get("dates", {})
            start = dates.get("start", {})
            venues = ev.get("_embedded", {}).get("venues", [{}])
            venue = venues[0] if venues else {}
            images = ev.get("images", [])
            img = next(
                (i["url"] for i in images if i.get("ratio") == "16_9" and i.get("width", 0) >= 640),
                images[0]["url"] if images else None,
            )
            classifications = ev.get("classifications", [{}])
            segment = classifications[0].get("segment", {}).get("name", "") if classifications else ""
            price_ranges = ev.get("priceRanges", [])
            events.append({
                "id":        ev.get("id", ""),
                "name":      ev.get("name", ""),
                "date":      start.get("localDate", ""),
                "time":      start.get("localTime", ""),
                "venue":     venue.get("name", ""),
                "city":      venue.get("city", {}).get("name", ""),
                "category":  segment,
                "url":       ev.get("url", ""),
                "image":     img,
                "price_min": price_ranges[0].get("min") if price_ranges else None,
                "price_max": price_ranges[0].get("max") if price_ranges else None,
            })

        return {"events": events, "total": len(events)}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))


# ── AI Recommendations ────────────────────────────────────────────────────────

@app.get("/api/recommendations")
async def recommendations(trip_id: str, limit: int = 5, destination: bool = False):
    """Claude-powered place recommendations based on location, time, and budget.

    When destination=true, uses the trip's destination city (derived from
    default_currency) instead of the user's current IP location.
    """
    try:
        import dataclasses
        from anthropic import Anthropic
        from recommendations import (
            BudgetInfo, GeoContext, get_geo_context, get_nearby_recommendations,
            get_time_context,
        )

        # currency → (lat, lon, city, country_name, iso_country_code)
        _CURRENCY_TO_GEO: dict[str, tuple] = {
            "JPY": (35.6762, 139.6503, "Tokyo",         "Japan",                "JP"),
            "USD": (40.7128, -74.0060, "New York",      "United States",        "US"),
            "GBP": (51.5074, -0.1278,  "London",        "United Kingdom",       "GB"),
            "CHF": (47.3769,  8.5417,  "Zurich",        "Switzerland",          "CH"),
            "NOK": (59.9139, 10.7522,  "Oslo",          "Norway",               "NO"),
            "SEK": (59.3293, 18.0686,  "Stockholm",     "Sweden",               "SE"),
            "DKK": (55.6761, 12.5683,  "Copenhagen",    "Denmark",              "DK"),
            "PLN": (52.2297, 21.0122,  "Warsaw",        "Poland",               "PL"),
            "CZK": (50.0755, 14.4378,  "Prague",        "Czech Republic",       "CZ"),
            "HUF": (47.4979, 19.0402,  "Budapest",      "Hungary",              "HU"),
            "AUD": (-33.8688, 151.2093,"Sydney",        "Australia",            "AU"),
            "CAD": (43.6532, -79.3832, "Toronto",       "Canada",               "CA"),
            "NZD": (-36.8509, 174.7645,"Auckland",      "New Zealand",          "NZ"),
            "KRW": (37.5665, 126.9780, "Seoul",         "South Korea",          "KR"),
            "INR": (28.6139,  77.2090, "New Delhi",     "India",                "IN"),
            "CNY": (39.9042, 116.4074, "Beijing",       "China",                "CN"),
            "BRL": (-23.5505,-46.6333, "São Paulo",     "Brazil",               "BR"),
            "MXN": (19.4326, -99.1332, "Mexico City",   "Mexico",               "MX"),
            "ZAR": (-33.9249, 18.4241, "Cape Town",     "South Africa",         "ZA"),
            "THB": (13.7563, 100.5018, "Bangkok",       "Thailand",             "TH"),
            "SGD": ( 1.3521, 103.8198, "Singapore",     "Singapore",            "SG"),
            "HKD": (22.3193, 114.1694, "Hong Kong",     "Hong Kong",            "HK"),
            "TRY": (41.0082,  28.9784, "Istanbul",      "Turkey",               "TR"),
            "AED": (25.2048,  55.2708, "Dubai",         "United Arab Emirates", "AE"),
            "IDR": (-6.2088, 106.8456, "Jakarta",       "Indonesia",            "ID"),
            "MYR": ( 3.1390, 101.6869, "Kuala Lumpur",  "Malaysia",             "MY"),
            "PHP": (14.5995, 120.9842, "Manila",        "Philippines",          "PH"),
            "VND": (21.0285, 105.8542, "Hanoi",         "Vietnam",              "VN"),
        }

        trip = get_trip(trip_id)
        if not trip:
            raise HTTPException(404, detail="Trip not found")

        geo: GeoContext | None = None

        if destination and trip.default_currency:
            dest = _CURRENCY_TO_GEO.get(trip.default_currency.upper())
            if dest:
                lat, lon, city, country_name, iso_cc = dest
                geo = GeoContext(
                    country_name=country_name,
                    iso_country_code=iso_cc,
                    likely_currency_iso=trip.default_currency.upper(),
                    city=city,
                    latitude=lat,
                    longitude=lon,
                )

        if geo is None:
            geo = get_geo_context()

        if geo is None or geo.latitude is None or geo.longitude is None:
            raise HTTPException(503, detail="Could not determine location for recommendations")

        time_ctx = get_time_context(geo)

        spent = 0.0
        try:
            from bunq_balance import get_payments
            own_ccy = trip.default_own_currency or "EUR"
            since = trip.start_date or (trip.created_at[:10] if trip.created_at else None)
            payments = get_payments(since_date=since, until_date=trip.end_date)
            spent = sum(
                abs(float(p["amount"]))
                for p in payments
                if p.get("type") == "debit" and p.get("currency") == own_ccy
            )
        except Exception:
            pass

        remaining = trip.budget_eur - spent
        days_total = days_remaining = None
        if trip.start_date and trip.end_date:
            from datetime import date as _date
            start_d = _date.fromisoformat(trip.start_date)
            end_d   = _date.fromisoformat(trip.end_date)
            days_total     = max(1, (end_d - start_d).days)
            days_remaining = max(1, (end_d - _date.today()).days)

        budget_info = BudgetInfo(
            total_budget_eur=trip.budget_eur,
            spent_eur=spent,
            remaining_eur=remaining,
            home_currency=trip.default_own_currency or "EUR",
            days_total=days_total,
            days_remaining=days_remaining,
            budget_per_day_eur=(trip.budget_eur / days_total) if days_total else None,
            budget_left_per_day_eur=(remaining / days_remaining) if days_remaining else None,
        )

        client = Anthropic()
        recs = get_nearby_recommendations(
            geo=geo,
            time_context=time_ctx,
            budget_info=budget_info,
            client=client,
            limit=limit,
        )

        return {
            "recommendations": [dataclasses.asdict(r) for r in recs],
            "geo_city":    geo.city,
            "geo_country": geo.country_name,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_geo():
    """Return a GeoContext based on IP geolocation, or None."""
    try:
        from extractor import GeoContext
        from geo import get_location
        loc = get_location()
        if loc:
            return GeoContext(
                country_name=loc.country_name,
                iso_country_code=loc.iso_country_code,
                likely_currency_iso=loc.currency_iso,
            )
    except Exception:
        pass
    if hyperparams.DEFAULT_ISO_COUNTRY_CODE:
        from extractor import GeoContext
        return GeoContext(
            country_name=hyperparams.DEFAULT_COUNTRY_NAME or "",
            iso_country_code=hyperparams.DEFAULT_ISO_COUNTRY_CODE,
            likely_currency_iso=hyperparams.DEFAULT_CURRENCY_ISO or "",
        )
    return None


# ── Card transactions (Mastercard Actions — actual FX rates) ──────────────────

@app.get("/api/trips/{trip_id}/card-transactions")
async def card_transactions(trip_id: str):
    """Return settled card transactions for the trip period with actual FX rates used."""
    trip = get_trip(trip_id)
    if not trip:
        raise HTTPException(404, detail="Trip not found")
    since = trip.start_date or (trip.created_at[:10] if trip.created_at else None)
    try:
        from bunq_balance import get_mastercard_actions, get_payments
        actions = get_mastercard_actions(since_date=since)
        if not actions:
            # Sandbox fallback: format debit payments as card-activity entries.
            # Real Mastercard Actions only appear for physical/virtual card swipes.
            payments = get_payments(since_date=since, until_date=trip.end_date, count=50)
            if not payments:
                payments = get_payments(count=20)
            actions = [
                {
                    "id": p["id"],
                    "date": p["date"],
                    "description": p["description"],
                    "amount_local": abs(float(p["amount"])),
                    "currency_local": p.get("currency", "EUR"),
                    "amount_eur": abs(float(p["amount"])),
                    "status": "settled",
                }
                for p in payments
                if p.get("type") == "debit"
            ]
        return actions
    except Exception as exc:
        raise HTTPException(502, detail=str(exc))


# ── Card limit (Budget Guard) ─────────────────────────────────────────────────

class CardLimitRequest(BaseModel):
    amount_eur: float


@app.post("/api/card-limit")
async def set_card_limit(body: CardLimitRequest):
    """Set the primary card's daily spending limit to protect the trip budget."""
    if body.amount_eur <= 0:
        raise HTTPException(422, detail="amount_eur must be positive")
    try:
        from bunq_balance import get_cards, set_card_daily_limit
        cards = get_cards()
        if not cards:
            raise HTTPException(404, detail="No cards found on this account")
        card = next((c for c in cards if c.get("status") == "ACTIVE"), cards[0])
        ok = set_card_daily_limit(card["id"], body.amount_eur)
        if not ok:
            raise HTTPException(502, detail="bunq rejected the card limit update")
        return {"card_id": card["id"], "daily_limit_eur": body.amount_eur}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))


# ── BunqMe split link ─────────────────────────────────────────────────────────

class SplitRequest(BaseModel):
    amount_eur: float
    description: str = "Travel expense"


@app.post("/api/split")
async def split(body: SplitRequest):
    """Create a bunq.me payment request link for splitting a travel expense."""
    if body.amount_eur <= 0:
        raise HTTPException(422, detail="amount_eur must be positive")
    try:
        from bunq_balance import create_bunqme_link
        url = create_bunqme_link(body.amount_eur, body.description)
        if not url:
            raise HTTPException(502, detail="bunq returned no share URL")
        return {"url": url}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))


# ── Webhook: auto-sync when bunq card is used abroad ─────────────────────────

_CARD_EVENTS_DB = Path(__file__).parent / "card_events.db"


def _init_card_events_db() -> None:
    conn = sqlite3.connect(str(_CARD_EVENTS_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS card_events (
            event_id    TEXT PRIMARY KEY,
            received_at TEXT NOT NULL,
            category    TEXT,
            amount_eur  REAL,
            amount_local REAL,
            currency_local TEXT,
            description TEXT,
            raw_json    TEXT
        )
    """)
    conn.commit()
    conn.close()


_init_card_events_db()


def _verify_bunq_signature(body_bytes: bytes, signature_b64: str) -> bool:
    """Verify X-Bunq-Server-Signature using the server public key from bunq_context.json."""
    if not signature_b64:
        return False
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        ctx_path = Path(__file__).parent / "bunq_context.json"
        if not ctx_path.exists():
            return False
        ctx = json.loads(ctx_path.read_text())
        server_pub_pem = ctx.get("server_public_key", "")
        if not server_pub_pem:
            return False

        pub_key = serialization.load_pem_public_key(server_pub_pem.encode())
        sig = base64.b64decode(signature_b64)
        pub_key.verify(sig, body_bytes, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


@app.post("/api/bunq/webhook")
async def bunq_webhook(request: Request):
    """
    Receive real-time card transaction events from bunq.

    bunq POSTs here for CARD_TRANSACTION_SUCCESSFUL and PAYMENT events.
    Events are stored locally so the dashboard can reflect live spend
    without the user doing anything.

    Register this URL with bunq via POST /api/bunq/setup-webhook.
    """
    body_bytes = await request.body()
    sig = request.headers.get("X-Bunq-Server-Signature", "")

    if not _verify_bunq_signature(body_bytes, sig):
        raise HTTPException(401, detail="Invalid signature")

    try:
        payload = json.loads(body_bytes)
        notif = payload.get("NotificationUrl", {})
        category = notif.get("category", "")
        obj = notif.get("object", {})

        action = obj.get("MastercardAction", {}) or obj.get("Payment", {})
        if action:
            billing = action.get("amount_billing", action.get("amount", {}))
            local = action.get("amount_local", billing)
            event_id = str(action.get("id", uuid.uuid4().hex))
            conn = sqlite3.connect(str(_CARD_EVENTS_DB))
            conn.execute(
                """INSERT OR IGNORE INTO card_events
                   (event_id, received_at, category, amount_eur, amount_local,
                    currency_local, description, raw_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    category,
                    abs(float(billing.get("value", 0))),
                    abs(float(local.get("value", 0))),
                    local.get("currency", ""),
                    action.get("description", ""),
                    json.dumps(payload),
                ),
            )
            conn.commit()
            conn.close()
    except Exception:
        pass  # Never fail — bunq retries on non-200 responses

    return {"status": "ok"}


class WebhookSetupRequest(BaseModel):
    url: str


@app.post("/api/bunq/setup-webhook")
async def setup_webhook(body: WebhookSetupRequest):
    """
    Register this server as a bunq webhook receiver.

    bunq will POST CARD_TRANSACTION_SUCCESSFUL and PAYMENT events to
    <url>/api/bunq/webhook whenever the user's card is used.

    Requires a public HTTPS URL. For local dev, use ngrok:
      ngrok http 8000
    then pass the ngrok URL here.
    """
    webhook_url = body.url.rstrip("/") + "/api/bunq/webhook"
    try:
        import sys
        sys.path.append(str(Path(__file__).parent / "hackathon_toolkit"))
        from bunq_client import BunqClient
        from api_keys import get_bunq_api_key

        api_key = get_bunq_api_key()
        client = BunqClient(api_key=api_key, sandbox=api_key.startswith("sandbox_"))
        client.authenticate()

        client.post(
            f"user/{client.user_id}/notification-filter-url",
            {
                "notification_filters": [
                    {"category": "CARD_TRANSACTION_SUCCESSFUL", "notification_target": webhook_url},
                    {"category": "PAYMENT", "notification_target": webhook_url},
                ]
            },
        )
        return {"status": "registered", "webhook_url": webhook_url}
    except Exception as exc:
        raise HTTPException(502, detail=str(exc))


# ── Dev entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
