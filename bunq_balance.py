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
from datetime import datetime
from pathlib import Path

# Make hackathon_toolkit importable without package restructuring.
sys.path.append(str(Path(__file__).parent / "hackathon_toolkit" / "hackathon_toolkit"))

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


def get_payments(
    since_date: str | None = None,
    until_date: str | None = None,
    count: int = 200,
) -> list[dict]:
    """
    Return payments from the primary bunq account.

    since_date / until_date: ISO date strings "YYYY-MM-DD", both inclusive.
    Each returned dict has: id, date, description, counterparty, amount (float),
    currency (str), type ("debit" | "credit").
    """
    client = _client()
    account_id = client.get_primary_account_id()
    resp = client.get(
        f"user/{client.user_id}/monetary-account/{account_id}/payment",
        params={"count": count},
    )

    payments = []
    for item in resp:
        p = item.get("Payment", {})
        if not p:
            continue

        created_str = p.get("created", "")
        try:
            created_dt = datetime.strptime(created_str[:19], "%Y-%m-%d %H:%M:%S")
            pay_date = created_dt.date().isoformat()
        except (ValueError, TypeError):
            pay_date = ""

        if since_date and pay_date and pay_date < since_date:
            continue
        if until_date and pay_date and pay_date > until_date:
            continue

        amount_obj = p.get("amount", {})
        amount_val = float(amount_obj.get("value", 0.0))
        currency = amount_obj.get("currency", "EUR")

        alias = p.get("counterparty_alias", {})
        counterparty = alias.get("display_name", "") or alias.get("iban", "")

        payments.append({
            "id": p.get("id"),
            "date": pay_date,
            "description": p.get("description", ""),
            "counterparty": counterparty,
            "amount": amount_val,
            "currency": currency,
            "type": "credit" if amount_val >= 0 else "debit",
        })

    return payments


def push_note_text(payment_id: int | str, content: str) -> bool:
    """
    POST a text note to an existing bunq payment. Returns True on success.
    Best-effort — callers should not rely on this succeeding.
    """
    try:
        client = _client()
        account_id = client.get_primary_account_id()
        client.post(
            f"user/{client.user_id}/monetary-account/{account_id}"
            f"/payment/{payment_id}/note-text",
            {"content": content},
        )
        return True
    except Exception:
        return False


def get_mastercard_actions(
    since_date: str | None = None,
    count: int = 50,
) -> list[dict]:
    """
    Return card transactions (Mastercard Actions) from the primary account.

    Each dict has: id, date, description, amount_local (float), currency_local,
    amount_eur (float), status ("pending" | "settled").
    """
    client = _client()
    account_id = client.get_primary_account_id()
    resp = client.get(
        f"user/{client.user_id}/monetary-account/{account_id}/mastercard-action",
        params={"count": count},
    )

    actions = []
    for item in resp:
        a = item.get("MastercardAction", {})
        if not a:
            continue

        status = a.get("authorisation_status", "")
        if status in ("FAILED", "EXPIRED"):
            continue

        created_str = a.get("created", "")
        try:
            created_dt = datetime.strptime(created_str[:19], "%Y-%m-%d %H:%M:%S")
            pay_date = created_dt.date().isoformat()
        except (ValueError, TypeError):
            pay_date = ""

        if since_date and pay_date and pay_date < since_date:
            continue

        local = a.get("amount_local", {})
        billing = a.get("amount_billing", {})
        local_val = abs(float(local.get("value", 0.0)))
        local_ccy = local.get("currency", "EUR")
        eur_val = abs(float(billing.get("value", 0.0)))

        actions.append({
            "id": a.get("id"),
            "date": pay_date,
            "description": a.get("description", ""),
            "amount_local": local_val,
            "currency_local": local_ccy,
            "amount_eur": eur_val,
            "status": "settled" if status in ("CLEARING", "CLEARED") else "pending",
        })

    return actions


def get_cards() -> list[dict]:
    """Return all cards with id, name_on_card, type, and status."""
    client = _client()
    resp = client.get(f"user/{client.user_id}/card")
    cards = []
    for item in resp:
        for key in ("CardDebit", "CardCredit", "CardVirtual"):
            c = item.get(key)
            if c:
                cards.append({
                    "id": c.get("id"),
                    "name_on_card": c.get("name_on_card", ""),
                    "type": key,
                    "status": c.get("status", ""),
                })
                break
    return cards


def set_card_daily_limit(card_id: int, amount_eur: float) -> bool:
    """Set the daily card spending limit. Returns True on success."""
    try:
        client = _client()
        client.put(
            f"user/{client.user_id}/card/{card_id}",
            {
                "limit": [
                    {
                        "daily_limit": {"value": f"{amount_eur:.2f}", "currency": "EUR"},
                        "type": "CARD",
                    }
                ]
            },
        )
        return True
    except Exception:
        return False


def create_bunqme_link(amount_eur: float, description: str) -> str | None:
    """Create a BunqMe tab and return its shareable URL, or None on failure."""
    try:
        client = _client()
        account_id = client.get_primary_account_id()
        resp = client.post(
            f"user/{client.user_id}/monetary-account/{account_id}/bunqme-tab",
            {
                "bunqme_tab_entry": {
                    "amount_inquired": {"value": f"{amount_eur:.2f}", "currency": "EUR"},
                    "description": description[:140],
                }
            },
        )
        tab_id = next((item["Id"]["id"] for item in resp if "Id" in item), None)
        if not tab_id:
            return None
        tab_resp = client.get(
            f"user/{client.user_id}/monetary-account/{account_id}/bunqme-tab/{tab_id}"
        )
        for item in tab_resp:
            tab = item.get("BunqMeTab", {})
            if tab:
                entry = tab.get("bunqme_tab_entry", {})
                return entry.get("bunqme_tab_share_url") or tab.get("bunqme_tab_share_url")
        return None
    except Exception:
        return None


# ── Quick test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Primary account balance:")
    balance = get_balance()
    print(f"  EUR {balance:,.2f}")

    print("\nAll active accounts:")
    for acc in get_all_balances():
        print(f"  [{acc['id']}] {acc['description']:<30} {acc['currency']} {acc['balance']:,.2f}")
