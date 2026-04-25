"""
connect_bunq.py — Tiny connection check for the bunq Travel Buddy app.

Purpose
-------
Verify that the BUNQ_API_KEY in your .env is valid and that the app can
read your account, balance, and payment history. If this script prints
your IBAN and a few recent payments, the dashboard's Payment Log will
also show them.

Sandbox vs production
---------------------
A key starting with "sandbox_" → sandbox endpoints (test data only).
Anything else → production endpoints (real bunq account).

Get a sandbox key
-----------------
Either run `python setup_sandbox.py` (recommended — also seeds 20 demo
payments) or just run this script with no BUNQ_API_KEY set; a fresh
sandbox user will be created and printed for you to paste into .env.

Production: register the bunq Travel Buddy / Bunqmate app in the bunq
developer portal, generate an API key, and put it in .env as
BUNQ_API_KEY=<your-prod-key>. Payments only show up in the app once
this registration is complete and the key is active.

Usage
-----
    python connect_bunq.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "hackathon_toolkit"))

from bunq_client import BunqClient  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass


def main() -> None:
    api_key = os.getenv("BUNQ_API_KEY", "").strip()

    if not api_key:
        print("No BUNQ_API_KEY found — creating a sandbox user...")
        api_key = BunqClient.create_sandbox_user()
        print(f"  New sandbox key: {api_key}")
        print("  Add this line to .env, then re-run:")
        print(f"    BUNQ_API_KEY={api_key}\n")
        return

    sandbox = api_key.startswith("sandbox_")
    mode = "SANDBOX" if sandbox else "PRODUCTION"
    print(f"Mode: {mode}   key: {api_key[:14]}...")

    client = BunqClient(api_key=api_key, sandbox=sandbox)
    client.authenticate()
    account_id = client.get_primary_account_id()
    print(f"Authenticated — user {client.user_id}, account {account_id}")

    # IBAN + balance
    resp = client.get(f"user/{client.user_id}/monetary-account-bank/{account_id}")
    acc = resp[0].get("MonetaryAccountBank", {})
    bal = acc.get("balance", {})
    iban = next(
        (a.get("value") for a in acc.get("alias", []) if a.get("type") == "IBAN"),
        "—",
    )
    print(f"IBAN:    {iban}")
    print(f"Balance: {bal.get('currency', 'EUR')} {bal.get('value', '0.00')}")

    # Recent payments — proves the dashboard's Payment Log will populate
    print("\nLast 5 payments:")
    payments = client.get(
        f"user/{client.user_id}/monetary-account/{account_id}/payment",
        params={"count": 5},
    )
    if not payments:
        print("  (no payments yet — run `python setup_sandbox.py` to seed demo data)")
        return
    for item in payments:
        p = item.get("Payment", {})
        amt = p.get("amount", {})
        print(
            f"  {p.get('created', '')[:10]}  "
            f"{amt.get('value', '?'):>8} {amt.get('currency', '')}  "
            f"{p.get('description', '')[:45]}"
        )

    print("\nConnection OK — payments will appear in the Payment Log on the dashboard.")


if __name__ == "__main__":
    main()
