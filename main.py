"""
main.py — Terminal control centre for the price extraction pipeline.

Usage:
    python main.py

Modes
-----
  1. Visual — extract a price from an image file (JPEG / PNG / WEBP).
  2. Audio  — record microphone audio and extract a price via AWS Transcribe.

Both modes feed through the same pipeline:
    model  →  model_interface  →  fx_converter  →  number_to_output (display)

Tune pipeline behaviour in hyperparams.py.
Set API credentials in a .env file or export them in your shell (see api_keys.py).
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import hyperparams
from api_keys import get_anthropic_api_key, get_aws_region
from fx_converter import convert_to_eur, print_results
from model_interface import from_extraction_result, from_voice_output
from number_to_output import build_gui


# ── Mode selection ────────────────────────────────────────────────────────────

def _choose_mode() -> str:
    print("\n=== Price Extraction Pipeline ===")
    print("  1. Visual  — extract price from an image file")
    print("  2. Audio   — record voice and extract price via AWS")
    print("  q. Quit")
    while True:
        choice = input("\nChoose mode [1/2/q]: ").strip().lower()
        if choice in ("1", "2", "q"):
            return choice
        print("  Please enter 1, 2, or q.")


def _choose_payment_type() -> str:
    print("\n  How will you pay?")
    print("  1. Card payment  — bunq Mastercard (ZeroFX: Mastercard rate + 0.5%)")
    print("  2. Transfer      — send via Wise (mid-market rate + Wise service fee)")
    while True:
        choice = input("  Payment type [1/2]: ").strip()
        if choice == "1":
            return "card"
        if choice == "2":
            return "transfer"
        print("  Please enter 1 or 2.")


# ── Visual mode ───────────────────────────────────────────────────────────────

def run_visual() -> list[tuple[float, str]] | None:
    from extractor import extract_from_image, GeoContext
    from anthropic import Anthropic
    from geo import get_location

    path_str = input("  Image path: ").strip().strip('"').strip("'")
    img_path = Path(path_str)
    if not img_path.exists():
        print(f"  File not found: {img_path}")
        return None

    # Build GeoContext from live IP geolocation, fall back to hyperparams.
    geo: GeoContext | None = None
    loc = get_location()
    if loc:
        geo = GeoContext(
            country_name=loc.country_name,
            iso_country_code=loc.iso_country_code,
            likely_currency_iso=loc.currency_iso,
        )
        print(f"  Geolocation: {loc.country_name} ({loc.iso_country_code}) → {loc.currency_iso}")
    elif hyperparams.DEFAULT_ISO_COUNTRY_CODE:
        geo = GeoContext(
            country_name=hyperparams.DEFAULT_COUNTRY_NAME or "",
            iso_country_code=hyperparams.DEFAULT_ISO_COUNTRY_CODE,
            likely_currency_iso=hyperparams.DEFAULT_CURRENCY_ISO or "",
        )

    os.environ.setdefault("ANTHROPIC_API_KEY", get_anthropic_api_key())
    client = Anthropic()

    print("  Analysing image...")
    result = extract_from_image(img_path, geo=geo, client=client)
    print(f"  Extraction result: {result}")
    return from_extraction_result(result)


# ── Audio mode ────────────────────────────────────────────────────────────────

def run_audio() -> list[tuple[float, str]] | None:
    from auto_voice_to_eur_aws import (
        record_wav,
        upload_to_s3,
        start_transcribe_job,
        wait_for_transcribe_job,
        download_transcript_text,
        extract_money_with_bedrock,
        get_language_metadata,
    )

    bucket = hyperparams.AWS_S3_BUCKET or input("  S3 bucket name: ").strip()
    if not bucket:
        print("  No S3 bucket specified. Cannot run audio pipeline.")
        return None

    region = get_aws_region()
    uid = uuid.uuid4().hex
    local_wav = Path(f"voice_money_{uid}.wav")
    s3_key = f"{hyperparams.AWS_S3_PREFIX.rstrip('/')}/voice_money_{uid}.wav"
    job_name = f"voice-money-{uid}"

    try:
        record_wav(local_wav, hyperparams.RECORD_SECONDS)
        media_uri = upload_to_s3(local_wav, bucket, s3_key, region)

        start_transcribe_job(
            media_uri=media_uri,
            region=region,
            job_name=job_name,
            language_options=hyperparams.TRANSCRIBE_LANGUAGE_OPTIONS or None,
            identify_multiple_languages=hyperparams.TRANSCRIBE_MULTI_LANGUAGE,
        )

        print("  Transcribing with automatic language detection...")
        job = wait_for_transcribe_job(job_name, region)
        transcript = download_transcript_text(job["Transcript"]["TranscriptFileUri"])
        print(f"  Transcript: {transcript}")

        money = extract_money_with_bedrock(
            transcript=transcript,
            region=region,
            model_id=hyperparams.AWS_BEDROCK_MODEL_ID,
        )
        print(f"  Extracted: {money}")

        voice_data = {
            "amount_original": money.get("amount"),
            "currency_original": money.get("currency"),
            **get_language_metadata(job),
        }
        return from_voice_output(voice_data)

    finally:
        if local_wav.exists():
            try:
                local_wav.unlink()
            except OSError:
                pass


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _print_balance() -> None:
    """Fetch and display the bunq account balance before each session."""
    try:
        from bunq_balance import get_balance
        balance = get_balance()
        print(f"\n  bunq balance: EUR {balance:,.2f}")
    except Exception as exc:
        print(f"\n  bunq balance: unavailable ({exc})")


def main() -> None:
    _print_balance()
    while True:
        choice = _choose_mode()

        if choice == "q":
            print("Goodbye.")
            sys.exit(0)

        items: list[tuple[float, str]] | None = None
        if choice == "1":
            items = run_visual()
        elif choice == "2":
            items = run_audio()

        if not items:
            print("  No items extracted. Try again.\n")
            continue

        payment_type = _choose_payment_type()

        print(f"\n  Items to convert: {items}")
        results = convert_to_eur(items, payment_type=payment_type)
        print_results(results)

        if input("Open display window? [y/n]: ").strip().lower() == "y":
            build_gui(results)


if __name__ == "__main__":
    main()
