"""
trips.py — Trip data model and JSON persistence.

A trip has:
  id          — UUID hex string, auto-generated on creation
  name        — user-facing display name
  budget_eur  — travel budget in EUR (float)

Trips are stored in trips.json at the project root.
All public functions read/write that file; the format is a plain JSON array.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

_TRIPS_FILE = Path(__file__).parent / "trips.json"


@dataclass
class Trip:
    id: str
    name: str
    budget_eur: float


# ── File I/O ──────────────────────────────────────────────────────────────────

def load_trips() -> list[Trip]:
    """Return all stored trips, or [] if the file is missing or corrupt."""
    if not _TRIPS_FILE.exists():
        return []
    try:
        data = json.loads(_TRIPS_FILE.read_text(encoding="utf-8"))
        return [Trip(**t) for t in data]
    except (json.JSONDecodeError, TypeError, KeyError):
        return []


def _save(trips: list[Trip]) -> None:
    _TRIPS_FILE.write_text(
        json.dumps([asdict(t) for t in trips], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Public CRUD ───────────────────────────────────────────────────────────────

def create_trip(name: str, budget_eur: float) -> Trip:
    """Create a new trip, persist it, and return it."""
    trip = Trip(id=uuid.uuid4().hex, name=name.strip(), budget_eur=float(budget_eur))
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
) -> Trip | None:
    """Update name and/or budget of an existing trip. Returns the updated trip or None."""
    trips = load_trips()
    for t in trips:
        if t.id == trip_id:
            if name is not None:
                t.name = name.strip()
            if budget_eur is not None:
                t.budget_eur = float(budget_eur)
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
