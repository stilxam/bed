"""
fx_converter.py — Foreign currency cost calculator for bunq hackathon
======================================================================

INPUT:  list of (amount: float, currency: str) tuples
OUTPUT: list of FXResult dataclasses, one per input tuple

Rate source
-----------
Primary: GET https://api.frankfurter.app/latest?from={CURRENCY}&to=EUR
    Real ECB mid-market rates, free, no key needed.
    Results are cached for 5 minutes per currency.

Fallback: hardcoded approximate rates (APPROXIMATE_RATES dict below).
    Used only when frankfurter.app is unreachable.
    Clearly flagged in output — refresh periodically.

Fee model
---------
Applies bunq's ZeroFX fee structure on top of the base rate:
    - 0.50% markup always applied
    - 0.30% extra on weekends (midpoint of bunq's 0.15–0.50% weekend range)

Usage
-----
    from fx_converter import convert_to_eur, FXResult

    results = convert_to_eur([
        (100.00, "USD"),
        (2500.00, "JPY"),
        (49.99, "GBP"),
    ])
    for r in results:
        print(r)

    # or run directly:
    #   python fx_converter.py
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Fee constants (from bunq public documentation)
# ---------------------------------------------------------------------------

ZEROFX_MARKUP: float = 0.0050         # 0.50% always applied
WEEKEND_EXTRA_MARKUP: float = 0.0030  # 0.30% extra on weekends (midpoint of 0.15-0.50%)

# ---------------------------------------------------------------------------
# Rate sources
# ---------------------------------------------------------------------------

FRANKFURTER_URL = "https://api.frankfurter.app/latest"

# Hardcoded approximate fallback rates (1 unit -> EUR).
# Used only when frankfurter.app is unreachable.
# Refresh these periodically -- they are intentionally rough.
APPROXIMATE_RATES: dict[str, float] = {
    "USD": 0.920, "GBP": 1.175, "JPY": 0.00615, "CHF": 1.045,
    "SEK": 0.0875, "NOK": 0.0840, "DKK": 0.134,  "PLN": 0.232,
    "CZK": 0.0405, "HUF": 0.00255, "RON": 0.201, "TRY": 0.0272,
    "AUD": 0.580,  "CAD": 0.675,   "NZD": 0.530, "SGD": 0.685,
    "HKD": 0.118,  "CNY": 0.127,   "INR": 0.0109, "ZAR": 0.0500,
    "BRL": 0.163,  "MXN": 0.0455,  "IDR": 0.0000565, "THB": 0.0266,
    "MYR": 0.208,
}

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class FXResult:
    input_amount: float
    input_currency: str
    base_rate: float        # 1 foreign unit = X EUR (pre-fee)
    base_eur: float         # raw converted amount before fees
    zerofx_fee: float       # bunq ZeroFX 0.50% markup
    weekend_fee: float      # bunq weekend extra markup (0 on weekdays)
    total_fees: float       # sum of all fees
    total_eur: float        # final EUR cost (base_eur + total_fees)
    source: str             # "ecb_live" | "ecb_cached" | "approximate"
    is_weekend: bool
    error: Optional[str] = None

    def __str__(self) -> str:
        if self.error and self.base_rate == 0.0:
            return (
                f"{self.input_amount:>10.2f} {self.input_currency} -> "
                f"[FAILED: {self.error}]"
            )
        wknd = " (weekend markup applied)" if self.is_weekend and self.weekend_fee > 0 else ""
        approx = " [APPROXIMATE]" if self.source == "approximate" else ""
        return (
            f"{self.input_amount:>10.2f} {self.input_currency} -> "
            f"EUR {self.total_eur:>9.4f}  "
            f"(base EUR {self.base_eur:.4f} + fees EUR {self.total_fees:.4f})"
            f"{wknd}{approx}"
        )

    def to_dict(self) -> dict:
        return {
            "input_amount": self.input_amount,
            "input_currency": self.input_currency,
            "base_rate": self.base_rate,
            "base_eur": round(self.base_eur, 6),
            "zerofx_fee": round(self.zerofx_fee, 6),
            "weekend_fee": round(self.weekend_fee, 6),
            "total_fees": round(self.total_fees, 6),
            "total_eur": round(self.total_eur, 4),
            "source": self.source,
            "is_weekend": self.is_weekend,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _is_weekend() -> bool:
    """True if today is Saturday (5) or Sunday (6) UTC."""
    return datetime.now(timezone.utc).weekday() >= 5


_rate_cache: dict[str, tuple[float, float]] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes


def _get_rate(currency: str) -> tuple[float, str]:
    """
    Return (rate, source) where rate is 1 unit of currency in EUR.
    source is one of: "ecb_live", "ecb_cached", "approximate".
    Raises ValueError if the currency is unknown and the network is down.
    """
    ccy = currency.upper()
    if ccy == "EUR":
        return 1.0, "no_conversion_needed"

    cached = _rate_cache.get(ccy)
    if cached and (time.time() - cached[1]) < CACHE_TTL_SECONDS:
        return cached[0], "ecb_cached"

    try:
        resp = requests.get(
            FRANKFURTER_URL,
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
        raise ValueError(f"No rate available for {ccy} (network down, not in APPROXIMATE_RATES)")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert_to_eur(items: list[tuple[float, str]]) -> list[FXResult]:
    """
    Convert a list of (amount, currency) tuples to EUR.

    Returns a list of FXResult in the same order as the input.
    """
    weekend = _is_weekend()
    results: list[FXResult] = []

    for amount, currency in items:
        ccy = currency.upper().strip()

        if ccy == "EUR":
            results.append(FXResult(
                input_amount=amount,
                input_currency="EUR",
                base_rate=1.0,
                base_eur=amount,
                zerofx_fee=0.0,
                weekend_fee=0.0,
                total_fees=0.0,
                total_eur=amount,
                source="no_conversion_needed",
                is_weekend=weekend,
            ))
            continue

        try:
            base_rate, source = _get_rate(ccy)
        except Exception as exc:
            results.append(FXResult(
                input_amount=amount,
                input_currency=ccy,
                base_rate=0.0,
                base_eur=0.0,
                zerofx_fee=0.0,
                weekend_fee=0.0,
                total_fees=0.0,
                total_eur=0.0,
                source="failed",
                is_weekend=weekend,
                error=str(exc),
            ))
            continue

        base_eur = amount * base_rate
        zerofx_fee = base_eur * ZEROFX_MARKUP
        weekend_fee = base_eur * WEEKEND_EXTRA_MARKUP if weekend else 0.0
        total_fees = zerofx_fee + weekend_fee
        total_eur = base_eur + total_fees

        results.append(FXResult(
            input_amount=amount,
            input_currency=ccy,
            base_rate=base_rate,
            base_eur=base_eur,
            zerofx_fee=zerofx_fee,
            weekend_fee=weekend_fee,
            total_fees=total_fees,
            total_eur=total_eur,
            source=source,
            is_weekend=weekend,
            error="APPROXIMATE -- refresh APPROXIMATE_RATES" if source == "approximate" else None,
        ))

    return results


# ---------------------------------------------------------------------------
# Pretty-print helper
# ---------------------------------------------------------------------------

def print_results(results: list[FXResult]) -> None:
    """Print a formatted table of conversion results."""
    sep = "-" * 82
    print(f"\n{sep}")
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
        print("  * Weekend markup applied (bunq charges 0.15-0.50% extra; using 0.30% midpoint)")
    if any(r.source == "approximate" for r in results):
        print("  ~ Approximate rate -- frankfurter.app unreachable, refresh APPROXIMATE_RATES")
    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_inputs: list[tuple[float, str]] = [
        (100.00,   "USD"),
        (2500.00,  "JPY"),
        (49.99,    "GBP"),
        (999.00,   "CHF"),
        (150.00,   "SEK"),
        (75.50,    "NOK"),
        (10_000.0, "TRY"),
        (200.00,   "AUD"),
        (50.00,    "EUR"),
    ]

    print("bunq FX Cost Calculator")
    results = convert_to_eur(test_inputs)
    print_results(results)

    print("JSON output:")
    print(json.dumps([r.to_dict() for r in results], indent=2))