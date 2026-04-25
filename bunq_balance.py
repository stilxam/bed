"""
bunq_balance.py — Fetch account balance from bunq.

Public API
----------
  get_balance() -> float
      Balance of the primary active account in EUR (or its native currency).
      This is the single numeric value consumed by the travel-budget feature.

  get_all_balances() -> list[dict]
      All active accounts with id, description, currency, and balance.
      Useful for future multi-account or multi-currency support.

Authentication
--------------
  Uses BunqClient from hackathon_toolkit/bunq_client.py.
  Session is cached in bunq_context.json at the project root and
  auto-refreshed when expired.
  Sandbox vs production is inferred from the BUNQ_API_KEY prefix
  ("sandbox_..." → sandbox, anything else → production).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make hackathon_toolkit importable without package restructuring.
sys.path.insert(0, str(Path(__file__).parent / "hackathon_toolkit"))

from bunq_client import BunqClient  # noqa: E402  (after sys.path mutation)
from api_keys import get_bunq_api_key


def _client() -> BunqClient:
    api_key = get_bunq_api_key()
    sandbox = api_key.startswith("sandbox_")
    client = BunqClient(api_key=api_key, sandbox=sandbox)
    client.authenticate()
    return client


def get_balance() -> float:
    """
    Return the balance of the primary active bunq account as a float.

    The value is in the account's own currency (almost always EUR for bunq).
    This is the number your teammate's budget feature should read.
    """
    client = _client()
    account_id = client.get_primary_account_id()
    resp = client.get(f"user/{client.user_id}/monetary-account-bank/{account_id}")
    account = resp[0].get("MonetaryAccountBank", {})
    balance = account.get("balance", {})
    return float(balance.get("value", 0.0))


def get_all_balances() -> list[dict]:
    """
    Return every active bunq account with its balance.

    Each dict has keys: id, description, currency, balance (float).
    """
    client = _client()
    resp = client.get(f"user/{client.user_id}/monetary-account-bank")
    accounts = []
    for item in resp:
        acc = item.get("MonetaryAccountBank", {})
        if acc.get("status") != "ACTIVE":
            continue
        bal = acc.get("balance", {})
        accounts.append({
            "id": acc["id"],
            "description": acc.get("description", ""),
            "currency": bal.get("currency", "EUR"),
            "balance": float(bal.get("value", 0.0)),
        })
    return accounts


# ── Quick test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Primary account balance:")
    balance = get_balance()
    print(f"  EUR {balance:,.2f}")

    print("\nAll active accounts:")
    for acc in get_all_balances():
        print(f"  [{acc['id']}] {acc['description']:<30} {acc['currency']} {acc['balance']:,.2f}")
