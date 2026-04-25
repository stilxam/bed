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
