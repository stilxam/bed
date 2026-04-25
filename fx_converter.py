"""
fx_converter.py — Foreign currency cost calculator for bunq hackathon
======================================================================

Two payment types with separate rate sources and fee models:

  card      Bunq Mastercard payment at a terminal or online.
            Rate:  Official Mastercard exchange rate.
            Fee:   0.5% ZeroFX network fee (always).
            Weekend: No extra markup — rate is already set by Mastercard.

  transfer  Sending money abroad via bunq's Wise integration.
            Rate:  Wise mid-market rate (the "real" Google rate).
            Fee:   Wise service fee (variable by corridor; see WISE_SERVICE_FEE_RATE
                   in hyperparams.py for the configurable approximation).
            Weekend: 0.30% extra markup on internal bunq FX conversions
                     (midpoint of the published 0.15–0.50% range).

Rate sources
------------
  Mastercard: https://sandbox.api.mastercard.com/enhanced/settlement/currencyrate
              OAuth 1.0a with RSA-SHA256. Credentials in api_keys.py.
  Wise:       https://api.wise.com/v1/rates  — Bearer auth. Key in api_keys.py.
  Fallback:   https://api.frankfurter.app   — Free ECB rates, no key needed.
              Used automatically when the primary source is unconfigured or down.

Usage
-----
    from fx_converter import convert_to_eur, FXResult

    results = convert_to_eur([(100.0, "USD"), (2500.0, "JPY")], payment_type="card")
    print_results(results)
"""

from __future__ import annotations

import base64
import json
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

import requests

# ── Types ─────────────────────────────────────────────────────────────────────

PaymentType = Literal["card", "transfer"]

# ── Fee constants ─────────────────────────────────────────────────────────────

CARD_NETWORK_FEE: float = 0.0050          # ZeroFX 0.5% on every card payment
TRANSFER_WEEKEND_MARKUP: float = 0.0030   # 0.30% midpoint of 0.15–0.50% weekend range

# ── API endpoints ─────────────────────────────────────────────────────────────

_MC_SANDBOX_URL = "https://sandbox.api.mastercard.com/enhanced/settlement/currencyrate/conversion-rate"
_MC_PROD_URL    = "https://api.mastercard.com/enhanced/settlement/currencyrate/conversion-rate"
_WISE_URL       = "https://api.wise.com/v1/rates"
_FRANKFURTER_URL = "https://api.frankfurter.app/latest"

# ── Fallback approximate rates (1 foreign unit → EUR) ─────────────────────────
# Used only when both the primary source and ECB live API are unreachable.

APPROXIMATE_RATES: dict[str, float] = {
    "USD": 0.920, "GBP": 1.175, "JPY": 0.00615, "CHF": 1.045,
    "SEK": 0.0875, "NOK": 0.0840, "DKK": 0.134,  "PLN": 0.232,
    "CZK": 0.0405, "HUF": 0.00255, "RON": 0.201, "TRY": 0.0272,
    "AUD": 0.580,  "CAD": 0.675,   "NZD": 0.530, "SGD": 0.685,
    "HKD": 0.118,  "CNY": 0.127,   "INR": 0.0109, "ZAR": 0.0500,
    "BRL": 0.163,  "MXN": 0.0455,  "IDR": 0.0000565, "THB": 0.0266,
    "MYR": 0.208,
}

# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class FXResult:
    input_amount: float
    input_currency: str
    payment_type: PaymentType
    base_rate: float      # 1 foreign unit = X EUR (pre-fee)
    base_eur: float
    service_fee: float    # ZeroFX 0.5% (card) OR Wise service fee (transfer)
    weekend_fee: float    # 0.30% extra on weekends for transfers; always 0 for card
    total_fees: float
    total_eur: float
    source: str           # "mastercard" | "wise_mid_market" | "ecb_live" | "ecb_cached" | "approximate"
    is_weekend: bool
    error: Optional[str] = None

    def __str__(self) -> str:
        if self.error and self.base_rate == 0.0:
            return (
                f"{self.input_amount:>10.2f} {self.input_currency} -> "
                f"[FAILED: {self.error}]"
            )
        mode = "Card" if self.payment_type == "card" else "Transfer"
        wknd = " (weekend markup)" if self.is_weekend and self.weekend_fee > 0 else ""
        approx = " [APPROXIMATE]" if self.source == "approximate" else ""
        return (
            f"{self.input_amount:>10.2f} {self.input_currency} -> "
            f"EUR {self.total_eur:>9.4f}  "
            f"[{mode}] (base EUR {self.base_eur:.4f} + fees EUR {self.total_fees:.4f})"
            f"{wknd}{approx}"
        )

    def to_dict(self) -> dict:
        return {
            "input_amount": self.input_amount,
            "input_currency": self.input_currency,
            "payment_type": self.payment_type,
            "base_rate": self.base_rate,
            "base_eur": round(self.base_eur, 6),
            "service_fee": round(self.service_fee, 6),
            "weekend_fee": round(self.weekend_fee, 6),
            "total_fees": round(self.total_fees, 6),
            "total_eur": round(self.total_eur, 4),
            "source": self.source,
            "is_weekend": self.is_weekend,
            "error": self.error,
        }


# ── Internals ─────────────────────────────────────────────────────────────────

def _is_weekend() -> bool:
    return datetime.now(timezone.utc).weekday() >= 5


_rate_cache: dict[str, tuple[float, float]] = {}  # key → (rate, timestamp)
_CACHE_TTL = 300  # 5 minutes


# ── Mastercard OAuth 1.0a (RSA-SHA256) ───────────────────────────────────────

def _mastercard_auth_header(
    method: str,
    url: str,
    query_params: dict[str, str],
    consumer_key: str,
    private_key_pem: str,
) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex

    oauth_params: dict[str, str] = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce,
        "oauth_signature_method": "RSA-SHA256",
        "oauth_timestamp": timestamp,
        "oauth_version": "1.0",
    }

    def pct(s: str) -> str:
        return urllib.parse.quote(str(s), safe="")

    # Signature base string: all parameters (query + oauth) sorted and encoded
    all_params = {**query_params, **oauth_params}
    sorted_pairs = sorted((pct(k), pct(v)) for k, v in all_params.items())
    param_str = "&".join(f"{k}={v}" for k, v in sorted_pairs)

    base_str = "&".join([
        pct(method.upper()),
        pct(url.split("?")[0]),   # base URL without query string
        pct(param_str),
    ])

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode() if isinstance(private_key_pem, str) else private_key_pem,
        password=None,
    )
    sig_bytes = private_key.sign(
        base_str.encode("ascii"),
        asym_padding.PKCS1v15(),
        hashes.SHA256(),
    )
    oauth_params["oauth_signature"] = base64.b64encode(sig_bytes).decode()

    parts = ", ".join(f'{k}="{pct(v)}"' for k, v in sorted(oauth_params.items()))
    return f"OAuth {parts}"


def _get_rate_mastercard(currency: str) -> tuple[float, str]:
    from api_keys import get_mastercard_consumer_key, get_mastercard_private_key_pem
    import hyperparams

    ccy = currency.upper()
    cache_key = f"mc_{ccy}"
    cached = _rate_cache.get(cache_key)
    if cached and (time.time() - cached[1]) < _CACHE_TTL:
        return cached[0], "mastercard_cached"

    consumer_key = get_mastercard_consumer_key()
    private_key_pem = get_mastercard_private_key_pem()
    base_url = _MC_SANDBOX_URL if getattr(hyperparams, "MASTERCARD_USE_SANDBOX", True) else _MC_PROD_URL

    query_params = {
        "fxDate": "0",
        "transCurr": ccy,
        "crdhldBillCurr": "EUR",
        "bankFee": "0",
        "transAmt": "1",
    }
    auth = _mastercard_auth_header("GET", base_url, query_params, consumer_key, private_key_pem)

    resp = requests.get(base_url, params=query_params, headers={"Authorization": auth}, timeout=10)
    resp.raise_for_status()
    rate = float(resp.json()["data"]["conversionRate"])

    _rate_cache[cache_key] = (rate, time.time())
    return rate, "mastercard"


def _get_rate_wise(currency: str) -> tuple[float, str]:
    from api_keys import get_wise_api_key

    ccy = currency.upper()
    cache_key = f"wise_{ccy}"
    cached = _rate_cache.get(cache_key)
    if cached and (time.time() - cached[1]) < _CACHE_TTL:
        return cached[0], "wise_cached"

    api_key = get_wise_api_key()
    resp = requests.get(
        _WISE_URL,
        params={"source": ccy, "target": "EUR"},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"No rate returned by Wise for {ccy}→EUR")
    rate = float(data[0]["rate"])

    _rate_cache[cache_key] = (rate, time.time())
    return rate, "wise_mid_market"


def _get_rate_ecb(currency: str) -> tuple[float, str]:
    """ECB live rate via Frankfurter, with approximate fallback."""
    ccy = currency.upper()
    cached = _rate_cache.get(ccy)
    if cached and (time.time() - cached[1]) < _CACHE_TTL:
        return cached[0], "ecb_cached"

    try:
        resp = requests.get(
            _FRANKFURTER_URL,
            params={"from": ccy, "to": "EUR", "amount": "1"},
            timeout=10,
        )
        resp.raise_for_status()
        rate = float(resp.json()["rates"]["EUR"])
        _rate_cache[ccy] = (rate, time.time())
        return rate, "ecb_live"
    except Exception:
        if ccy in APPROXIMATE_RATES:
            rate = APPROXIMATE_RATES[ccy]
            _rate_cache[ccy] = (rate, time.time())
            return rate, "approximate"
        raise ValueError(f"No rate available for {ccy} (all sources failed)")


def _get_rate(currency: str, payment_type: PaymentType) -> tuple[float, str]:
    """
    Fetch the exchange rate for the given payment type.
    Falls back to ECB if the primary source is unavailable.
    """
    ccy = currency.upper()
    try:
        if payment_type == "card":
            return _get_rate_mastercard(ccy)
        else:
            return _get_rate_wise(ccy)
    except Exception:
        return _get_rate_ecb(ccy)


# ── Public API ────────────────────────────────────────────────────────────────

def convert_to_eur(
    items: list[tuple[float, str]],
    payment_type: PaymentType = "card",
) -> list[FXResult]:
    """
    Convert a list of (amount, currency) tuples to EUR.

    payment_type:
      "card"     — Mastercard rate + 0.5% ZeroFX fee
      "transfer" — Wise mid-market rate + Wise service fee + weekend markup
    """
    import hyperparams

    weekend = _is_weekend()
    results: list[FXResult] = []

    for amount, currency in items:
        ccy = currency.upper().strip()

        if ccy == "EUR":
            results.append(FXResult(
                input_amount=amount, input_currency="EUR",
                payment_type=payment_type,
                base_rate=1.0, base_eur=amount,
                service_fee=0.0, weekend_fee=0.0,
                total_fees=0.0, total_eur=amount,
                source="no_conversion_needed", is_weekend=weekend,
            ))
            continue

        try:
            base_rate, source = _get_rate(ccy, payment_type)
        except Exception as exc:
            results.append(FXResult(
                input_amount=amount, input_currency=ccy,
                payment_type=payment_type,
                base_rate=0.0, base_eur=0.0,
                service_fee=0.0, weekend_fee=0.0,
                total_fees=0.0, total_eur=0.0,
                source="failed", is_weekend=weekend, error=str(exc),
            ))
            continue

        base_eur = amount * base_rate

        if payment_type == "card":
            service_fee = base_eur * CARD_NETWORK_FEE
            weekend_fee = 0.0  # Mastercard rate already reflects closed markets
        else:
            service_fee = base_eur * hyperparams.WISE_SERVICE_FEE_RATE
            weekend_fee = base_eur * TRANSFER_WEEKEND_MARKUP if weekend else 0.0

        total_fees = service_fee + weekend_fee
        total_eur = base_eur + total_fees

        results.append(FXResult(
            input_amount=amount, input_currency=ccy,
            payment_type=payment_type,
            base_rate=base_rate, base_eur=base_eur,
            service_fee=service_fee, weekend_fee=weekend_fee,
            total_fees=total_fees, total_eur=total_eur,
            source=source, is_weekend=weekend,
            error="APPROXIMATE rate — refresh APPROXIMATE_RATES" if source == "approximate" else None,
        ))

    return results


# ── Pretty-print helper ───────────────────────────────────────────────────────

def print_results(results: list[FXResult]) -> None:
    if not results:
        return
    payment_type = results[0].payment_type
    mode_label = "CARD (ZeroFX — Mastercard rate + 0.5%)" if payment_type == "card" \
        else "TRANSFER (Wise mid-market rate + Wise fee)"
    sep = "-" * 90
    print(f"\n  Mode: {mode_label}")
    print(sep)
    print(f"  {'AMOUNT':>12}  {'CCY':<5}  {'RATE':>10}  {'BASE EUR':>10}  {'FEES':>8}  {'TOTAL EUR':>10}  SOURCE")
    print(sep)
    for r in results:
        if r.error and r.base_rate == 0.0:
            print(f"  {r.input_amount:>12.2f}  {r.input_currency:<5}  [FAILED: {r.error}]")
            continue
        wknd = "*" if r.is_weekend and r.weekend_fee > 0 else " "
        approx = " ~" if r.source == "approximate" else "  "
        print(
            f"  {r.input_amount:>12.4f}  {r.input_currency:<5}  "
            f"{r.base_rate:>10.6f}  "
            f"{r.base_eur:>10.4f}  "
            f"+{r.total_fees:>7.4f}{wknd} "
            f"{r.total_eur:>10.4f}  "
            f"{r.source}{approx}"
        )
    print(sep)
    if any(r.is_weekend and r.weekend_fee > 0 for r in results):
        print("  * Weekend markup applied (bunq 0.15–0.50%; using 0.30% midpoint)")
    if any(r.source == "approximate" for r in results):
        print("  ~ Approximate rate — all live sources unreachable, refresh APPROXIMATE_RATES")
    print()


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_inputs: list[tuple[float, str]] = [
        (100.00, "USD"),
        (2500.00, "JPY"),
        (49.99, "GBP"),
        (999.00, "CHF"),
        (50.00, "EUR"),
    ]
    print("=== Card payment (Mastercard rate + ZeroFX 0.5%) ===")
    print_results(convert_to_eur(test_inputs, payment_type="card"))

    print("=== Transfer (Wise mid-market + Wise service fee) ===")
    print_results(convert_to_eur(test_inputs, payment_type="transfer"))
