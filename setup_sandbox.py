#!/usr/bin/env python3
"""
setup_sandbox.py -- Bootstrap a bunq sandbox account with realistic travel data.

Creates (or reuses) a sandbox user, funds the account, and populates it with
diverse payments covering typical Amsterdam travel scenarios. Run once before
demoing the app.

Usage:
    python setup_sandbox.py          # reuse sandbox key from .env if present
    python setup_sandbox.py --fresh  # force-create a new sandbox user
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "hackathon_toolkit"))

from bunq_client import BunqClient  # noqa: E402

SUGARDADDY = "sugardaddy@bunq.com"
ENV_FILE = Path(__file__).parent / ".env"
CTX_FILE = Path(__file__).parent / "bunq_context.json"

# Travel payments: (description, eur_amount, merchant_name)
TRAVEL_PAYMENTS = [
    # Accommodation
    ("Hotel V Nesplein - Check-in night 1",      94.00, "Hotel V Nesplein"),
    ("Hotel V Nesplein - Night 2",                94.00, "Hotel V Nesplein"),
    # Food & drink
    ("Dinner - Restaurant Rijks",                 54.80, "Restaurant Rijks"),
    ("Lunch - Foodhallen Amsterdam",              22.40, "Foodhallen"),
    ("Dinner - NEMO Brasserie",                   38.60, "NEMO Brasserie"),
    ("Breakfast - Broodje Bert",                   9.60, "Broodje Bert"),
    ("Coffee - Starbucks Leidsestraat",            7.20, "Starbucks"),
    ("Stroopwafels & snacks - HEMA",              11.30, "HEMA"),
    # Transport
    ("NS Train - Schiphol -> Amsterdam Centraal",   5.90, "NS Trains"),
    ("GVB Tram - 24-hr day pass",                  9.00, "GVB Amsterdam"),
    ("Uber - AMS airport pickup",                 38.50, "Uber"),
    ("Lime scooter - 40 min",                      6.40, "Lime"),
    # Attractions
    ("Rijksmuseum - admission",                   22.50, "Rijksmuseum"),
    ("Van Gogh Museum - admission",               22.00, "Van Gogh Museum"),
    ("Anne Frank House - timed entry",            16.00, "Anne Frank House"),
    ("Heineken Experience - ticket",              28.00, "Heineken Experience"),
    ("Canal cruise - Lovers Cruises",             17.50, "Lovers Canal Cruises"),
    # Shopping & misc
    ("Albert Heijn - groceries",                  34.20, "Albert Heijn"),
    ("Etos Pharmacy - toiletries",                 8.90, "Etos"),
    ("Souvenir shop - Albert Cuyp markt",         14.50, "Albert Cuyp Market"),
]

# Funding rounds: (amount_str, description)
FUNDING_ROUNDS = [
    ("500.00", "Amsterdam trip - initial budget"),
    ("500.00", "Amsterdam trip - top-up"),
    ("300.00", "Amsterdam trip - extra spending money"),
]


# -- Helpers -------------------------------------------------------------------

def _read_env_key() -> str | None:
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("BUNQ_API_KEY="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            return val or None
    return None


def _write_env_key(api_key: str) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    found = False
    new_lines = []
    for line in lines:
        if line.startswith("BUNQ_API_KEY="):
            new_lines.append(f"BUNQ_API_KEY={api_key}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"BUNQ_API_KEY={api_key}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"  .env updated  BUNQ_API_KEY={api_key[:28]}...")


def _step(n: int, total: int, label: str) -> None:
    print(f"\n[{n}/{total}] {label}")


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _info(msg: str) -> None:
    print(f"     {msg}")


# -- Core setup ----------------------------------------------------------------

def get_or_create_user(force_fresh: bool) -> tuple[str, bool]:
    """Return (api_key, is_new). Creates a new sandbox user when needed."""
    if not force_fresh:
        existing = _read_env_key()
        if existing and existing.startswith("sandbox_"):
            _ok(f"Reusing existing sandbox key  {existing[:28]}...")
            return existing, False
        if existing and not existing.startswith("sandbox_"):
            print("  WARN  .env has a PRODUCTION key -- creating a separate sandbox user")
            print("        (production .env key is preserved; new key printed below)")

    print("  Creating new bunq sandbox user via /v1/sandbox-user-person ...")
    api_key = BunqClient.create_sandbox_user()
    _ok(f"New key: {api_key}")
    return api_key, True


def fund_account(client: BunqClient, account_id: int) -> float:
    total = 0.0
    for amount_str, desc in FUNDING_ROUNDS:
        _info(f"Requesting EUR {amount_str} from sugardaddy  [{desc}]")
        client.post(
            f"user/{client.user_id}/monetary-account/{account_id}/request-inquiry",
            {
                "amount_inquired": {"value": amount_str, "currency": "EUR"},
                "counterparty_alias": {
                    "type": "EMAIL",
                    "value": SUGARDADDY,
                    "name": "Sugar Daddy",
                },
                "description": desc,
                "allow_bunqme": False,
            },
        )
        total += float(amount_str)
        time.sleep(0.8)
    # Give sugardaddy a moment to auto-accept all requests
    _info("Waiting for sugardaddy auto-accept...")
    time.sleep(3)
    _ok(f"Funded with EUR {total:.2f}")
    return total


def create_travel_payments(client: BunqClient, account_id: int) -> float:
    total = 0.0
    for desc, amount, merchant in TRAVEL_PAYMENTS:
        _info(f"EUR {amount:>7.2f}  {desc}")
        client.post(
            f"user/{client.user_id}/monetary-account/{account_id}/payment",
            {
                "amount": {"value": f"{amount:.2f}", "currency": "EUR"},
                "counterparty_alias": {
                    "type": "EMAIL",
                    "value": SUGARDADDY,
                    "name": merchant,
                },
                "description": desc,
            },
        )
        total += amount
        time.sleep(0.35)
    _ok(f"{len(TRAVEL_PAYMENTS)} payments created  (EUR {total:.2f} total outgoing)")
    return total


def check_card_activity(client: BunqClient, account_id: int) -> int:
    """Query mastercard-action -- sandbox usually returns 0 unless a card was swiped."""
    try:
        resp = client.get(
            f"user/{client.user_id}/monetary-account/{account_id}/mastercard-action",
            params={"count": 50},
        )
        actions = [r for r in resp if "MastercardAction" in r]
        return len(actions)
    except Exception:
        return 0


def get_account_iban(client: BunqClient, account_id: int) -> str:
    """Return the IBAN of the given account."""
    try:
        resp = client.get(
            f"user/{client.user_id}/monetary-account-bank/{account_id}"
        )
        acc = resp[0].get("MonetaryAccountBank", {})
        for alias in acc.get("alias", []):
            if alias.get("type") == "IBAN":
                return alias.get("value", "--")
    except Exception:
        pass
    return "--"


# -- Main ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Populate a bunq sandbox account with demo travel data")
    parser.add_argument("--fresh", action="store_true", help="Force-create a new sandbox user")
    args = parser.parse_args()

    STEPS = 5
    print()
    print("=" * 56)
    print("  bunq Travel Buddy - Sandbox Setup")
    print("=" * 56)

    # 1. Get / create sandbox user
    _step(1, STEPS, "Sandbox user")
    api_key, is_new = get_or_create_user(force_fresh=args.fresh)

    # Clear stale context when using a fresh key so auth rebuilds
    if is_new and CTX_FILE.exists():
        CTX_FILE.unlink()
        _info("Cleared stale bunq_context.json")

    # 2. Authenticate
    _step(2, STEPS, "Authenticate")
    client = BunqClient(api_key=api_key, sandbox=True)
    client.authenticate()
    account_id = client.get_primary_account_id()
    iban = get_account_iban(client, account_id)
    _ok(f"User ID:      {client.user_id}")
    _ok(f"Account ID:   {account_id}")
    _ok(f"IBAN:         {iban}")

    if is_new:
        _step(3, STEPS, "Persist new API key")
        _write_env_key(api_key)
    else:
        _step(3, STEPS, "API key already in .env -- skipping write")

    # 4. Fund account
    _step(4, STEPS, "Fund account via sugardaddy")
    funded = fund_account(client, account_id)

    # 5. Create travel payments
    _step(5, STEPS, "Create travel payments")
    spent = create_travel_payments(client, account_id)

    # Done -- summary
    card_count = check_card_activity(client, account_id)
    approx_balance = funded - spent

    print()
    print("=" * 56)
    print("  Setup complete!")
    print("=" * 56)
    print(f"  Funded:            EUR {funded:.2f}")
    print(f"  Payments created:  {len(TRAVEL_PAYMENTS)}")
    print(f"  Approx. balance:   EUR {approx_balance:.2f}")
    print(f"  Card transactions: {card_count} (sandbox: 0 until card swiped)")
    print()
    print("  What each tab will show:")
    print("  Payment Log    20 travel transactions  [OK]")
    print("  Card Activity  0  (no sandbox card sim)")
    print("  Budget Guard   set limit to remaining  [OK]")
    print("  BunqMe Split   generate share link     [OK]")
    print()
    print("  uvicorn server:app --reload --port 8000")
    print("=" * 56)
    print()


if __name__ == "__main__":
    main()
