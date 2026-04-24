"""
model_interface.py — Normalises raw model output to [(amount, currency)] tuples.

Each model returns a different output format; this module converts any of them
into the canonical list[tuple[float, str]] that fx_converter.convert_to_eur()
expects.  Interactive disambiguation prompts are issued when results are
ambiguous.
"""

from __future__ import annotations


def from_extraction_result(result) -> list[tuple[float, str]] | None:
    """
    Convert an ExtractionResult (from extractor.py) to [(amount, currency)].

    Returns None on failure.  Prompts the user for missing information when
    the extraction result is ambiguous.
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
        currency = result.currency_iso
        if not currency:
            currency = input("  Enter currency code (e.g. USD, EUR): ").strip().upper()
        return [(amount, currency)]

    if isinstance(result, CurrencyAmbiguousResult):
        print(f"  Extracted text: '{result.raw_text}', amount: {result.amount}")
        if result.suggested_currency_iso:
            name = result.suggested_currency_name or result.suggested_currency_iso
            answer = input(
                f"  Is the currency {name} ({result.suggested_currency_iso})? [y/n]: "
            ).strip().lower()
            if answer == "y":
                return [(result.amount, result.suggested_currency_iso)]
        currency = input("  Enter currency code (e.g. USD, EUR): ").strip().upper()
        return [(result.amount, currency)]

    # FailureResult
    print("  Extraction failed — no price could be found.")
    return None


def from_voice_output(voice_data: dict) -> list[tuple[float, str]] | None:
    """
    Convert the dict output of the AWS voice pipeline to [(amount, currency)].

    Returns None if amount or currency could not be extracted.
    """
    amount = voice_data.get("amount_original")
    currency = voice_data.get("currency_original")
    if amount is None or not currency:
        print("  Could not extract amount or currency from audio.")
        return None
    return [(float(amount), str(currency).upper())]
