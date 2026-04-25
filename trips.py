"""
trips.py — Trip data model and JSON persistence.

A trip has:
  id               — UUID hex string, auto-generated on creation
  name             — user-facing display name
  budget_eur       — travel budget in EUR (float)
  start_date       — optional ISO date string (YYYY-MM-DD)
  end_date         — optional ISO date string (YYYY-MM-DD)
  default_currency — optional ISO 4217 code for the destination currency (e.g. "JPY")

Trips are stored in trips.json at the project root.
All public functions read/write that file; the format is a plain JSON array.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

_TRIPS_FILE = Path(__file__).parent / "trips.json"


@dataclass
class Trip:
    id: str
    name: str
    budget_eur: float
    start_date: Optional[str] = None        # ISO date string YYYY-MM-DD, or None
    end_date: Optional[str] = None          # ISO date string YYYY-MM-DD, or None
    default_currency: Optional[str] = None  # ISO 4217 destination currency, or None


# ── File I/O ──────────────────────────────────────────────────────────────────

def load_trips() -> list[Trip]:
    """Return all stored trips, or [] if the file is missing or corrupt."""
    if not _TRIPS_FILE.exists():
        return []
    try:
        data = json.loads(_TRIPS_FILE.read_text(encoding="utf-8"))
        trips = []
        for t in data:
            trips.append(Trip(
                id=t["id"],
                name=t["name"],
                budget_eur=t["budget_eur"],
                start_date=t.get("start_date"),
                end_date=t.get("end_date"),
                default_currency=t.get("default_currency"),
            ))
        return trips
    except (json.JSONDecodeError, TypeError, KeyError):
        return []


def _save(trips: list[Trip]) -> None:
    _TRIPS_FILE.write_text(
        json.dumps([asdict(t) for t in trips], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Public CRUD ───────────────────────────────────────────────────────────────

def create_trip(
    name: str,
    budget_eur: float,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    default_currency: Optional[str] = None,
) -> Trip:
    """Create a new trip, persist it, and return it."""
    trip = Trip(
        id=uuid.uuid4().hex,
        name=name.strip(),
        budget_eur=float(budget_eur),
        start_date=start_date or None,
        end_date=end_date or None,
        default_currency=default_currency.upper().strip() if default_currency else None,
    )
    trips = load_trips()
    trips.append(trip)
    _save(trips)
    return trip


def get_trip(trip_id: str) -> Trip | None:
    """Return a single trip by id, or None if not found."""
    return next((t for t in load_trips() if t.id == trip_id), None)


def update_trip(
    trip_id: str,
    name: str | None = None,
    budget_eur: float | None = None,
    start_date: str | None = ...,        # type: ignore[assignment]
    end_date: str | None = ...,          # type: ignore[assignment]
    default_currency: str | None = ...,  # type: ignore[assignment]
) -> Trip | None:
    """Update fields of an existing trip. Returns the updated trip or None.

    Pass the sentinel `...` (default) to leave optional fields unchanged.
    Pass a value or None to set / clear them.
    """
    _UNSET = ...
    trips = load_trips()
    for t in trips:
        if t.id == trip_id:
            if name is not None:
                t.name = name.strip()
            if budget_eur is not None:
                t.budget_eur = float(budget_eur)
            if start_date is not _UNSET:
                t.start_date = start_date or None
            if end_date is not _UNSET:
                t.end_date = end_date or None
            if default_currency is not _UNSET:
                t.default_currency = (
                    default_currency.upper().strip() if default_currency else None
                )
            _save(trips)
            return t
    return None


def delete_trip(trip_id: str) -> bool:
    """Delete a trip by id. Returns True if it existed."""
    trips = load_trips()
    filtered = [t for t in trips if t.id != trip_id]
    if len(filtered) == len(trips):
        return False
    _save(filtered)
    return True
