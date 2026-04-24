"""
model_interface.py — Normalises raw model output to [(amount, currency)] tuples.

Each model returns a different output format; this module converts any of them
into the canonical list[tuple[float, str]] that fx_converter.convert_to_eur()
expects.

Currency resolution priority when a model cannot identify the currency:
  1. IP geolocation  (geo.py — silent, automatic)
  2. Terminal prompt  (user types the code manually)
"""

from __future__ import annotations


def _resolve_currency() -> str | None:
    """
    Try IP geolocation first. If that fails, ask the user.
    Returns the resolved ISO currency code, or None if the user skips.
    """
    from geo import get_location

    loc = get_location()
    if loc:
        print(f"  Geolocation: {loc.country_name} → {loc.currency_iso}")
        return loc.currency_iso

    print("  Geolocation unavailable.")
    raw = input("  Enter currency code (e.g. USD, EUR), or press Enter to skip: ").strip().upper()
    return raw or None


def from_extraction_result(result) -> list[tuple[float, str]] | None:
    """
    Convert an ExtractionResult (from extractor.py) to [(amount, currency)].

    Returns None on failure.
    """
    from extractor import (
        SuccessResult,
        AmountAmbiguousResult,
        CurrencyAmbiguousResult,
        FailureResult,
    )

    if isinstance(result, SuccessResult):
        return [(result.amount, result.currency_iso)]

    if isinstance(result, AmountAmbiguousResult):
        print(f"  Extracted text: '{result.raw_text}'")
        raw = input("  Amount unclear — enter the numeric value: ").strip()
        try:
            amount = float(raw)
        except ValueError:
            print("  Invalid number. Skipping.")
            return None
        currency = result.currency_iso or _resolve_currency()
        if not currency:
            return None
        return [(amount, currency)]

    if isinstance(result, CurrencyAmbiguousResult):
        print(f"  Extracted text: '{result.raw_text}', amount: {result.amount}")
        currency = _resolve_currency()
        if not currency:
            return None
        return [(result.amount, currency)]

    # FailureResult
    print("  Extraction failed — no price could be found.")
    return None


def from_voice_output(voice_data: dict) -> list[tuple[float, str]] | None:
    """
    Convert the dict output of the AWS voice pipeline to [(amount, currency)].

    If the model extracted an amount but not a currency, geolocation fills
    in the currency automatically.
    """
    amount = voice_data.get("amount_original")
    currency = voice_data.get("currency_original")

    if amount is None:
        print("  Could not extract amount from audio.")
        return None

    if not currency:
        print("  Currency not detected in audio — resolving via geolocation.")
        currency = _resolve_currency()
        if not currency:
            return None

    return [(float(amount), str(currency).upper())]
