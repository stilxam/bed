#!/usr/bin/env python3
"""
auto_voice_to_eur_aws.py





Record microphone audio -> upload WAV to S3 -> Amazon Transcribe automatic language detection
-> AWS Bedrock money extraction -> convert detected currency to EUR.

This version does NOT require --language-code.

Output:
{
  "transcript": "twenty five dollars",
  "detected_language_code": "en-US",
  "identified_language_score": 0.98,
  "amount_original": 25.0,
  "currency_original": "USD",
  "amount_eur": 23.12,
  "currency_target": "EUR",
  "confidence": 0.95,
  "reason": "..."
}

Install:
    pip install boto3 sounddevice scipy requests

PowerShell credentials:
    $env:AWS_ACCESS_KEY_ID="..."
    $env:AWS_SECRET_ACCESS_KEY="..."
    $env:AWS_SESSION_TOKEN="..."       # if temporary credentials
    $env:AWS_DEFAULT_REGION="us-east-1"

Run:
    python auto_voice_to_eur_aws.py --seconds 8 --s3-bucket YOUR_BUCKET_NAME --pretty

Optional: restrict language candidates for better accuracy:
    python auto_voice_to_eur_aws.py --seconds 8 --s3-bucket YOUR_BUCKET_NAME --language-options en-US nl-NL ru-RU de-DE fr-FR es-ES --pretty

AWS requirements:
- S3 bucket in the same region or accessible from Transcribe.
- IAM permissions:
  s3:PutObject
  s3:GetObject
  transcribe:StartTranscriptionJob
  transcribe:GetTranscriptionJob
  bedrock:InvokeModel
- Amazon Bedrock model access enabled for the selected model.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
import wave
from pathlib import Path
from typing import Optional, Dict, Any, List

import boto3
import requests
import sounddevice as sd
from scipy.io.wavfile import write as wav_write


SYSTEM_PROMPT = """
You extract monetary amounts from short speech transcripts.

Return ONLY valid JSON with this schema:
{
  "amount": number | null,
  "currency": "ISO_4217_CODE" | null,
  "confidence": number,
  "reason": string
}

Rules:
- Understand multilingual transcripts.
- Convert spoken numbers into numeric decimal values.
- Detect currency from symbols, ISO codes, and currency names in any language.
- Examples:
  "twenty five dollars" -> {"amount":25,"currency":"USD"}
  "fifty euros" -> {"amount":50,"currency":"EUR"}
  "сто рублей" -> {"amount":100,"currency":"RUB"}
  "двадцать евро" -> {"amount":20,"currency":"EUR"}
  "veinticinco pesos mexicanos" -> {"amount":25,"currency":"MXN"}
  "cinquante francs suisses" -> {"amount":50,"currency":"CHF"}
  "fünf Pfund" -> {"amount":5,"currency":"GBP"}
  "vijftien euro" -> {"amount":15,"currency":"EUR"}
- If currency is ambiguous, return null for currency.
- If amount is ambiguous, return null for amount.
- Do not include markdown.
"""


def record_wav(path: Path, seconds: int, sample_rate: int = 16000) -> None:
    print(f"Recording for {seconds} seconds...")
    audio = sd.rec(
        int(seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
    )
    sd.wait()
    wav_write(str(path), sample_rate, audio)
    print(f"Saved recording: {path}")


def upload_to_s3(local_path: Path, bucket: str, key: str, region: str) -> str:
    s3 = boto3.client("s3", region_name=region)
    s3.upload_file(str(local_path), bucket, key)
    return f"s3://{bucket}/{key}"


def start_transcribe_job(
    media_uri: str,
    region: str,
    job_name: str,
    language_options: Optional[List[str]] = None,
    identify_multiple_languages: bool = False,
) -> Dict[str, Any]:
    transcribe = boto3.client("transcribe", region_name=region)

    params: Dict[str, Any] = {
        "TranscriptionJobName": job_name,
        "Media": {"MediaFileUri": media_uri},
        "MediaFormat": "wav",
    }

    if identify_multiple_languages:
        params["IdentifyMultipleLanguages"] = True
    else:
        params["IdentifyLanguage"] = True

    if language_options:
        params["LanguageOptions"] = language_options

    return transcribe.start_transcription_job(**params)


def wait_for_transcribe_job(job_name: str, region: str, poll_seconds: int = 3, timeout_seconds: int = 300) -> Dict[str, Any]:
    transcribe = boto3.client("transcribe", region_name=region)
    start = time.time()

    while True:
        response = transcribe.get_transcription_job(TranscriptionJobName=job_name)
        job = response["TranscriptionJob"]
        status = job["TranscriptionJobStatus"]

        if status == "COMPLETED":
            return job

        if status == "FAILED":
            reason = job.get("FailureReason", "Unknown failure")
            raise RuntimeError(f"Transcription failed: {reason}")

        if time.time() - start > timeout_seconds:
            raise TimeoutError(f"Transcription timed out after {timeout_seconds} seconds.")

        time.sleep(poll_seconds)


def download_transcript_text(transcript_uri: str) -> str:
    response = requests.get(transcript_uri, timeout=30)
    response.raise_for_status()
    data = response.json()

    # Standard AWS transcript JSON
    return data["results"]["transcripts"][0]["transcript"].strip()


def extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in model output: {text}")

    return json.loads(match.group(0))


def extract_money_with_bedrock(
    transcript: str,
    region: str,
    model_id: str = "amazon.nova-lite-v1:0",
) -> Dict[str, Any]:
    bedrock = boto3.client("bedrock-runtime", region_name=region)

    payload = {
        "schemaVersion": "messages-v1",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"text": f"{SYSTEM_PROMPT}\n\nTranscript:\n{transcript}"}
                ],
            }
        ],
        "inferenceConfig": {
            "maxTokens": 300,
            "temperature": 0,
            "topP": 0.1,
        },
    }

    response = bedrock.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(payload),
    )

    body = json.loads(response["body"].read())

    text = (
        body.get("output", {})
        .get("message", {})
        .get("content", [{}])[0]
        .get("text", "")
    )

    if not text:
        raise RuntimeError(f"Unexpected Bedrock response shape: {body}")

    data = extract_json(text)

    if data.get("amount") is not None:
        data["amount"] = float(data["amount"])

    if data.get("currency") is not None:
        data["currency"] = str(data["currency"]).upper().strip()

    if data.get("confidence") is not None:
        data["confidence"] = float(data["confidence"])

    return data


def convert_to_eur(amount: float, source_currency: str) -> float:
    source_currency = source_currency.upper()

    if source_currency == "EUR":
        return round(float(amount), 2)

    # ECB-based free API. For unsupported currencies, replace with your bank/FX provider API.
    response = requests.get(
        "https://api.frankfurter.app/latest",
        params={
            "amount": amount,
            "from": source_currency,
            "to": "EUR",
        },
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()

    return round(float(data["rates"]["EUR"]), 2)


def get_language_metadata(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handles both single-language and multi-language Transcribe metadata.
    """
    detected_language_code = job.get("LanguageCode")
    identified_language_score = job.get("IdentifiedLanguageScore")

    # Multi-language jobs may include LanguageCodes list.
    language_codes = job.get("LanguageCodes")
    if language_codes and isinstance(language_codes, list):
        # Pick highest score when present.
        sorted_codes = sorted(
            language_codes,
            key=lambda x: x.get("DurationInSeconds", 0),
            reverse=True,
        )
        if sorted_codes:
            detected_language_code = sorted_codes[0].get("LanguageCode", detected_language_code)

    return {
        "detected_language_code": detected_language_code,
        "identified_language_score": identified_language_score,
        "language_codes": language_codes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Automatically detect spoken language, extract amount/currency, convert to EUR."
    )
    parser.add_argument("--seconds", type=int, default=8, help="Recording duration.")
    parser.add_argument("--s3-bucket", required=True, help="S3 bucket for temporary audio upload.")
    parser.add_argument("--s3-prefix", default="voice-money-inputs", help="S3 key prefix.")
    parser.add_argument("--region", default=None, help="AWS region. Defaults to AWS_DEFAULT_REGION or us-east-1.")
    parser.add_argument("--model-id", default="amazon.nova-lite-v1:0", help="Bedrock model ID.")
    parser.add_argument(
        "--language-options",
        nargs="*",
        default=None,
        help="Optional language candidates, e.g. en-US nl-NL ru-RU de-DE fr-FR es-ES.",
    )
    parser.add_argument(
        "--identify-multiple-languages",
        action="store_true",
        help="Use Transcribe multi-language identification instead of single dominant language.",
    )
    parser.add_argument("--keep-local-audio", action="store_true", help="Keep local WAV file.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")

    args = parser.parse_args()

    region = args.region or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"

    uid = uuid.uuid4().hex
    local_wav = Path(f"voice_money_{uid}.wav")
    s3_key = f"{args.s3_prefix.rstrip('/')}/voice_money_{uid}.wav"
    job_name = f"voice-money-{uid}"

    try:
        record_wav(local_wav, args.seconds)

        media_uri = upload_to_s3(local_wav, args.s3_bucket, s3_key, region)

        start_transcribe_job(
            media_uri=media_uri,
            region=region,
            job_name=job_name,
            language_options=args.language_options,
            identify_multiple_languages=args.identify_multiple_languages,
        )

        print("Transcribing with automatic language detection...")
        job = wait_for_transcribe_job(job_name, region)

        transcript_uri = job["Transcript"]["TranscriptFileUri"]
        transcript = download_transcript_text(transcript_uri)

        money = extract_money_with_bedrock(
            transcript=transcript,
            region=region,
            model_id=args.model_id,
        )

        amount = money.get("amount")
        currency = money.get("currency")

        amount_eur = None
        if amount is not None and currency:
            amount_eur = convert_to_eur(float(amount), currency)

        language_meta = get_language_metadata(job)

        output = {
            "transcript": transcript,
            **language_meta,
            "amount_original": float(amount) if amount is not None else None,
            "currency_original": currency,
            "amount_eur": amount_eur,
            "currency_target": "EUR",
            "confidence": money.get("confidence"),
            "reason": money.get("reason"),
            "transcribe_job_name": job_name,
            "s3_media_uri": media_uri,
        }

        if args.pretty:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(output, ensure_ascii=False))

    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "hint": (
                        "Check AWS credentials, S3 bucket permissions, Transcribe permissions, "
                        "Bedrock model access, microphone access, and internet access."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    finally:
        if local_wav.exists() and not args.keep_local_audio:
            try:
                local_wav.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    main()
