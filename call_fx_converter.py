"""
call_fx_converter.py — Programmatic wrapper around fx_converter.

Use main.py to run the full pipeline (model → interface → FX → display).
Use this module directly when you already have a [(amount, currency)] list
and just want the conversion results without launching the full pipeline.
"""

from fx_converter import convert_to_eur, print_results, FXResult


def run(items: list[tuple[float, str]]) -> list[FXResult]:
    """Convert items to EUR, print a summary table, and return the results."""
    results = convert_to_eur(items)
    print_results(results)
    return results
