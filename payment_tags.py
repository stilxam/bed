"""
payment_tags.py — SQLite-backed payment-to-trip association.

Payments fetched from bunq are tagged explicitly to a trip by the user.
This replaces the fragile date-range heuristic: a payment belongs to a trip
when the user says it does, not just because the dates overlap.

Payment data is snapshotted at tag-time so tagged payments remain readable
without re-fetching from bunq.

Schema
------
payment_tags(payment_id PK, trip_id, tagged_at, note_pushed,
             pay_date, description, counterparty, amount, currency, pay_type)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = Path(__file__).parent / "payment_tags.db"


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS payment_tags (
            payment_id   TEXT PRIMARY KEY,
            trip_id      TEXT NOT NULL,
            tagged_at    TEXT NOT NULL,
            note_pushed  INTEGER NOT NULL DEFAULT 0,
            pay_date     TEXT,
            description  TEXT,
            counterparty TEXT,
            amount       REAL,
            currency     TEXT,
            pay_type     TEXT
        )
    """)
    con.commit()
    return con


def tag_payment(payment: dict, trip_id: str) -> None:
    """Associate a payment dict with trip_id. Overwrites any existing tag."""
    pid = str(payment["id"])
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            """
            INSERT INTO payment_tags
                (payment_id, trip_id, tagged_at, note_pushed,
                 pay_date, description, counterparty, amount, currency, pay_type)
            VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(payment_id) DO UPDATE SET
                trip_id      = excluded.trip_id,
                tagged_at    = excluded.tagged_at,
                note_pushed  = 0,
                pay_date     = excluded.pay_date,
                description  = excluded.description,
                counterparty = excluded.counterparty,
                amount       = excluded.amount,
                currency     = excluded.currency,
                pay_type     = excluded.pay_type
            """,
            (
                pid, trip_id, now,
                payment.get("date", ""),
                payment.get("description", ""),
                payment.get("counterparty", ""),
                float(payment.get("amount", 0)),
                payment.get("currency", "EUR"),
                payment.get("type", "debit"),
            ),
        )


def untag_payment(payment_id: str | int) -> None:
    """Remove any trip association for this payment."""
    with _conn() as con:
        con.execute(
            "DELETE FROM payment_tags WHERE payment_id = ?", (str(payment_id),)
        )


def get_tagged_payments(trip_id: str) -> list[dict]:
    """Return all payment dicts tagged to this trip, newest first."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT payment_id, pay_date, description, counterparty,
                   amount, currency, pay_type
            FROM payment_tags
            WHERE trip_id = ?
            ORDER BY pay_date DESC, tagged_at DESC
            """,
            (trip_id,),
        ).fetchall()
    return [
        {
            "id": r["payment_id"],
            "date": r["pay_date"] or "",
            "description": r["description"] or "",
            "counterparty": r["counterparty"] or "",
            "amount": r["amount"] or 0.0,
            "currency": r["currency"] or "EUR",
            "type": r["pay_type"] or "debit",
        }
        for r in rows
    ]


def get_all_tagged_ids() -> set[str]:
    """Return every payment_id tagged to any trip."""
    with _conn() as con:
        rows = con.execute("SELECT payment_id FROM payment_tags").fetchall()
    return {r["payment_id"] for r in rows}


def mark_note_pushed(payment_id: str | int) -> None:
    """Record that a bunq note-text was successfully written for this payment."""
    with _conn() as con:
        con.execute(
            "UPDATE payment_tags SET note_pushed = 1 WHERE payment_id = ?",
            (str(payment_id),),
        )
