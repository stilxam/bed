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

import datetime
import os
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
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

        # SuccessResult
        fx_list = convert_to_eur([(result.amount, result.currency_iso)], payment_type=payment_type)
        fx = fx_list[0]
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

        fx_list = convert_to_eur([(float(amount), str(currency).upper())], payment_type=payment_type)
        fx = fx_list[0]
        return {
            **base,
            "status":       "success",
            "total_eur":    fx.total_eur,
            "total_fees":   fx.total_fees,
            "source":       fx.source,
            "payment_type": fx.payment_type,
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
        fx_list = convert_to_eur(
            [(body.amount, body.currency.upper().strip())],
            payment_type=body.payment_type,
        )
        fx = fx_list[0]
        return {
            "total_eur":    fx.total_eur,
            "total_fees":   fx.total_fees,
            "source":       fx.source,
            "payment_type": fx.payment_type,
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
        payments = get_payments(since_date=since, until_date=until)
    except Exception as exc:
        pay_error = str(exc)

    spent = sum(
        abs(float(p["amount"]))
        for p in payments
        if p.get("type") == "debit" and p.get("currency") == own_ccy
    )

    return {
        "spent":     spent,
        "own_ccy":   own_ccy,
        "payments":  payments,
        "pay_error": pay_error,
    }


# ── Events (Ticketmaster) ─────────────────────────────────────────────────────

@app.get("/api/events")
async def events_nearby(radius: int = 20, size: int = 8):
    """Return upcoming events near the user's IP-detected location."""
    try:
        import requests as _req
        from api_keys import get_ticketmaster_api_key

        api_key = get_ticketmaster_api_key()

        lat, lon = None, None
        try:
            from geo1 import detect_location, geo_from_location_result
            loc = detect_location()
            if loc:
                geo = geo_from_location_result(loc)
                lat, lon = geo.latitude, geo.longitude
        except Exception:
            pass

        if lat is None or lon is None:
            raise HTTPException(503, detail="Could not determine location for events")

        params = {
            "apikey": api_key,
            "latlong": f"{lat},{lon}",
            "radius": radius,
            "unit": "km",
            "sort": "date,asc",
            "size": size,
            "locale": "*",
        }
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


# ── Dev entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
