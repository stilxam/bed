"""
api_keys.py — Centralised API key management.

Priority order for every key:
  1. Environment variable (already exported in shell / CI)
  2. .env file in the project root (auto-loaded on import)

Never hardcode secrets here.  Add them to a .env file or export them in your
shell before running the pipeline.

Required variables
------------------
ANTHROPIC_API_KEY          — Claude / Anthropic models          (extractor.py)
BUNQ_API_KEY               — bunq banking API                   (bunq_client.py)
AWS_ACCESS_KEY_ID          — Amazon Web Services                (auto_voice_to_eur_aws.py)
AWS_SECRET_ACCESS_KEY
GOOGLE_MAPS_API_KEY
TICKETMASTER_API_KEY       — Ticketmaster Discovery API        (server.py /api/events)

Optional variables
------------------
AWS_SESSION_TOKEN          — only needed for temporary / STS credentials
AWS_DEFAULT_REGION         — defaults to us-east-1
MASTERCARD_CONSUMER_KEY    — Mastercard API app key             (fx_converter.py, card mode)
MASTERCARD_PRIVATE_KEY_PATH — path to RSA private key PEM file  (fx_converter.py, card mode)
WISE_API_KEY               — Wise platform API token            (fx_converter.py, transfer mode)

If Mastercard or Wise credentials are absent, fx_converter falls back to ECB live rates
(Frankfurter API) with the same fee model applied on top.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv not installed; rely on shell environment only


def _get(var: str, required: bool = True) -> str | None:
    val = os.getenv(var)
    if required and not val:
        raise EnvironmentError(
            f"Missing required environment variable: {var}\n"
            f"Add it to a .env file in the project root or export it in your shell.\n"
            f"Example .env line:  {var}=your_key_here"
        )
    return val


def get_anthropic_api_key() -> str:
    return _get("ANTHROPIC_API_KEY")  # type: ignore[return-value]


def get_bunq_api_key() -> str:
    return _get("BUNQ_API_KEY")  # type: ignore[return-value]


def get_aws_access_key_id() -> str:
    return _get("AWS_ACCESS_KEY_ID")  # type: ignore[return-value]


def get_aws_secret_access_key() -> str:
    return _get("AWS_SECRET_ACCESS_KEY")  # type: ignore[return-value]


def get_aws_session_token() -> str | None:
    return _get("AWS_SESSION_TOKEN", required=False)


def get_aws_region() -> str:
    return os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def get_mastercard_consumer_key() -> str:
    return _get("MASTERCARD_CONSUMER_KEY")  # type: ignore[return-value]


def get_mastercard_private_key_pem() -> str:
    path = _get("MASTERCARD_PRIVATE_KEY_PATH")  # type: ignore[arg-type]
    return Path(path).read_text()


def get_wise_api_key() -> str:
    return _get("WISE_API_KEY")  # type: ignore[return-value]


def get_google_maps_api_key() -> str:
    return _get("GOOGLE_MAPS_API_KEY")  # type: ignore[return-value]


def get_ticketmaster_api_key() -> str:
    return _get("TICKETMASTER_API_KEY")  # type: ignore[return-value]
