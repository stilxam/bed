"""
app.py — bunq Travel Buddy  ·  Streamlit interface
===================================================

Run:
    streamlit run app.py

Integrates the full pipeline:
  geo.py                  IP geolocation + automatic currency fallback
  extractor.py            Visual extraction  (Claude Haiku → Sonnet)
  auto_voice_to_eur_aws   Audio extraction  (AWS Transcribe + Bedrock Nova Lite)
  fx_converter.py         Mastercard (card) or Wise (transfer) + bunq fee model
  bunq_balance.py         Live account balance from bunq API

Note: model_interface.py uses terminal input() for disambiguation and is used
by main.py (CLI). This file re-implements disambiguation with Streamlit widgets.
"""

from __future__ import annotations

import io
import os
import uuid
from pathlib import Path

import streamlit as st

# ── Page config — must be the FIRST Streamlit call ────────────────────────────
st.set_page_config(
    page_title="bunq Travel Buddy",
    page_icon="💶",
    layout="centered",
    initial_sidebar_state="expanded",
)

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .block-container { max-width: 430px; padding-top: 1rem; }

  .app-title {
    text-align: center; font-size: 26px; font-weight: 700; margin-bottom: 10px;
  }

  .camera-box {
    border: 2px solid #111; border-radius: 18px;
    padding: 16px; min-height: 220px; background: #fafafa;
  }

  .result-box {
    border: 2px solid #111; border-radius: 18px;
    padding: 20px; min-height: 230px; margin-top: 20px; background: #ffffff;
  }

  .result-amount-foreign { font-size: 1.1rem; color: #8E8E93; margin-bottom: 2px; }
  .result-amount-eur     { font-size: 2.4rem; font-weight: 700; color: #1C7C3A; }
  .result-meta           { color: #8E8E93; font-size: 0.82rem; margin-top: 6px; }

  .badge {
    display: inline-block; padding: 2px 9px; border-radius: 20px;
    font-size: 0.75rem; font-weight: 600; color: white;
    background: #FF7819; margin-right: 5px;
  }
  .badge-transfer { background: #1C7C3A; }
  .badge-approx   { background: #8E8E93; }

  div.stButton > button {
    width: 100%; height: 52px; border-radius: 50px;
    font-size: 16px; font-weight: 600;
  }

  [data-testid="stFileUploader"]  { border: 1.5px dashed #999; border-radius: 14px; padding: 8px; }
  [data-testid="stCameraInput"]   { border-radius: 14px; }
</style>
""", unsafe_allow_html=True)

# ── Imports ────────────────────────────────────────────────────────────────────
import hyperparams
from api_keys import get_anthropic_api_key, get_aws_region
from extractor import (
    SuccessResult, AmountAmbiguousResult,
    CurrencyAmbiguousResult, FailureResult,
)
from fx_converter import convert_to_eur, FXResult

UPLOAD_DIR = Path("streamlit_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


# ── Cached data loaders ────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def _load_balance() -> tuple[float | None, list[dict], str]:
    """(primary_balance, all_accounts, error). Refreshed every 60 s."""
    try:
        from bunq_balance import get_balance, get_all_balances
        return get_balance(), get_all_balances(), ""
    except Exception as exc:
        return None, [], str(exc)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_geo():
    """GeoLocation or None. Refreshed every hour."""
    from geo import get_location
    return get_location()


# ── Pipeline helpers ───────────────────────────────────────────────────────────

def _run_visual(img_path: Path):
    """Return an ExtractionResult from extractor.py."""
    from extractor import extract_from_image, GeoContext
    from anthropic import Anthropic

    os.environ.setdefault("ANTHROPIC_API_KEY", get_anthropic_api_key())
    client = Anthropic()

    geo_loc = _load_geo()
    geo = None
    if geo_loc:
        geo = GeoContext(
            country_name=geo_loc.country_name,
            iso_country_code=geo_loc.iso_country_code,
            likely_currency_iso=geo_loc.currency_iso,
        )
    elif hyperparams.DEFAULT_ISO_COUNTRY_CODE:
        geo = GeoContext(
            country_name=hyperparams.DEFAULT_COUNTRY_NAME or "",
            iso_country_code=hyperparams.DEFAULT_ISO_COUNTRY_CODE,
            likely_currency_iso=hyperparams.DEFAULT_CURRENCY_ISO or "",
        )

    return extract_from_image(img_path, geo=geo, client=client)


def _run_audio(audio_path: Path) -> dict:
    """Upload audio → AWS Transcribe → Bedrock → return voice_data dict."""
    from auto_voice_to_eur_aws import (
        upload_to_s3, start_transcribe_job, wait_for_transcribe_job,
        download_transcript_text, extract_money_with_bedrock, get_language_metadata,
    )

    bucket = hyperparams.AWS_S3_BUCKET
    if not bucket:
        raise ValueError("AWS_S3_BUCKET is empty in hyperparams.py")

    region = get_aws_region()
    uid = uuid.uuid4().hex
    ext = audio_path.suffix.lstrip(".") or "wav"
    s3_key = f"{hyperparams.AWS_S3_PREFIX.rstrip('/')}/voice_money_{uid}.{ext}"
    job_name = f"voice-money-{uid}"

    media_uri = upload_to_s3(audio_path, bucket, s3_key, region)
    start_transcribe_job(
        media_uri=media_uri, region=region, job_name=job_name,
        language_options=hyperparams.TRANSCRIBE_LANGUAGE_OPTIONS or None,
        identify_multiple_languages=hyperparams.TRANSCRIBE_MULTI_LANGUAGE,
    )
    job = wait_for_transcribe_job(job_name, region)
    transcript = download_transcript_text(job["Transcript"]["TranscriptFileUri"])
    money = extract_money_with_bedrock(
        transcript=transcript, region=region, model_id=hyperparams.AWS_BEDROCK_MODEL_ID,
    )
    return {
        "transcript": transcript,
        "amount_original": money.get("amount"),
        "currency_original": money.get("currency"),
        "confidence": money.get("confidence"),
        **get_language_metadata(job),
    }


def _do_fx(items: list[tuple[float, str]], payment_type: str) -> list[FXResult]:
    return convert_to_eur(items, payment_type=payment_type)


def _make_tts(results: list[FXResult]) -> bytes | None:
    try:
        from gtts import gTTS
        lines = [
            f"{r.input_amount:.2f} {r.input_currency}. {round(r.total_eur, 2)} euro."
            for r in results if r.base_rate > 0
        ]
        if not lines:
            return None
        buf = io.BytesIO()
        gTTS("  ".join(lines), lang=hyperparams.TTS_LANGUAGE, slow=False).write_to_fp(buf)
        return buf.getvalue()
    except Exception:
        return None


# ── Session state ──────────────────────────────────────────────────────────────

_DEFAULTS: dict = {
    "stage": "idle",           # idle | extracted | ambiguous_amount | ambiguous_currency
                               # | ambiguous_voice_currency | done
    "extraction_raw": None,    # ExtractionResult from visual model
    "voice_data": None,        # dict from audio pipeline
    "extracted_items": None,   # list[tuple[float, str]] — ready for FX
    "fx_results": None,        # list[FXResult]
    "fx_payment_type": None,   # payment type used for current fx_results
    "transcript": None,        # shown after audio extraction
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


def _reset():
    for k, v in _DEFAULTS.items():
        st.session_state[k] = v


# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### bunq ✈️ Travel Buddy")
    st.divider()

    # Account balance
    st.markdown("**Account Balance**")
    primary_bal, all_accounts, bal_err = _load_balance()
    if primary_bal is not None:
        st.metric("Primary account", f"€ {primary_bal:,.2f}")
        if len(all_accounts) > 1:
            with st.expander(f"All accounts ({len(all_accounts)})"):
                for acc in all_accounts:
                    label = acc.get("description") or "Account"
                    st.write(f"**{label}**  {acc['currency']} {acc['balance']:,.2f}")
    else:
        st.warning(f"Balance unavailable\n\n`{bal_err}`")
    if st.button("↺ Refresh", use_container_width=True):
        _load_balance.clear()
        st.rerun()

    st.divider()

    # Geolocation
    st.markdown("**Your Location**")
    geo = _load_geo()
    if geo:
        st.success(f"📍 {geo.country_name}\n\nLocal currency: **{geo.currency_iso}**")
    else:
        st.warning("Location unavailable\n\n(currency entered manually when needed)")

    st.divider()

    # Payment type
    st.markdown("**Payment Type**")
    payment_type: str = st.radio(
        "payment_type_radio",
        options=["card", "transfer"],
        format_func=lambda x: "💳 Card (ZeroFX)" if x == "card" else "🏦 Transfer (Wise)",
        label_visibility="collapsed",
    )
    if payment_type == "card":
        st.caption("Mastercard rate + 0.5% ZeroFX fee. No weekend markup.")
    else:
        pct = hyperparams.WISE_SERVICE_FEE_RATE * 100
        st.caption(
            f"Wise mid-market rate + ~{pct:.1f}% service fee.\n\n"
            "Weekend: +0.30% for bunq FX account conversions."
        )

    # Recompute FX if payment type switched while a result is showing
    if (
        st.session_state["fx_results"] is not None
        and st.session_state["fx_payment_type"] != payment_type
    ):
        items = st.session_state["extracted_items"]
        if items:
            try:
                st.session_state["fx_results"] = _do_fx(items, payment_type)
                st.session_state["fx_payment_type"] = payment_type
            except Exception:
                st.session_state["fx_results"] = None


# ── Main UI ────────────────────────────────────────────────────────────────────

st.markdown('<div class="app-title">💶 Currency Scanner</div>', unsafe_allow_html=True)

input_mode = st.segmented_control(
    "Input type",
    ["📷 Photo", "🖼️ Upload", "🎤 Voice"],
    default="📷 Photo",
)

# ── Input area ─────────────────────────────────────────────────────────────────
st.markdown('<div class="camera-box">', unsafe_allow_html=True)

file_path: Path | None = None
source_type: str | None = None

if input_mode == "📷 Photo":
    camera_file = st.camera_input("Take a photo of a price tag")
    if camera_file:
        file_path = UPLOAD_DIR / f"camera_{uuid.uuid4().hex}.png"
        file_path.write_bytes(camera_file.getvalue())
        source_type = "image"

elif input_mode == "🖼️ Upload":
    uploaded = st.file_uploader(
        "Select a price tag photo",
        type=["jpg", "jpeg", "png", "webp"],
    )
    if uploaded:
        suffix = Path(uploaded.name).suffix or ".jpg"
        file_path = UPLOAD_DIR / f"gallery_{uuid.uuid4().hex}{suffix}"
        file_path.write_bytes(uploaded.getvalue())
        st.image(uploaded, use_container_width=True)
        source_type = "image"

elif input_mode == "🎤 Voice":
    audio_value = None
    try:
        audio_value = st.audio_input("Say the price out loud (any language)")
    except AttributeError:
        st.info("Live recording requires Streamlit ≥ 1.35 — upload a file instead.")
        audio_upload = st.file_uploader(
            "Upload audio file",
            type=["wav", "mp3", "flac", "ogg"],
            key="audio_fallback",
        )
        if audio_upload:
            audio_value = audio_upload

    if audio_value is not None:
        raw_bytes = (
            audio_value.getvalue()
            if hasattr(audio_value, "getvalue") else audio_value.read()
        )
        ext = "wav"
        if hasattr(audio_value, "name"):
            ext = Path(audio_value.name).suffix.lstrip(".") or "wav"
        file_path = UPLOAD_DIR / f"audio_{uuid.uuid4().hex}.{ext}"
        file_path.write_bytes(raw_bytes)
        st.audio(audio_value)
        source_type = "audio"

st.markdown("</div>", unsafe_allow_html=True)

process_clicked = st.button("Detect currency and price", use_container_width=True)

# ── Trigger extraction ─────────────────────────────────────────────────────────
if process_clicked:
    if not file_path or not source_type:
        st.warning("Capture a photo, upload an image, or record audio first.")
    else:
        _reset()
        if source_type == "image":
            with st.spinner("Analysing with Claude…"):
                try:
                    raw = _run_visual(file_path)
                    st.session_state["extraction_raw"] = raw
                    # Resolve non-ambiguous cases immediately
                    if isinstance(raw, SuccessResult):
                        st.session_state["extracted_items"] = [(raw.amount, raw.currency_iso)]
                        st.session_state["stage"] = "extracted"
                    elif isinstance(raw, FailureResult):
                        st.session_state["stage"] = "idle"
                        st.error(f"No price found. Reason: `{raw.reason}`")
                    elif isinstance(raw, AmountAmbiguousResult):
                        st.session_state["stage"] = "ambiguous_amount"
                    elif isinstance(raw, CurrencyAmbiguousResult):
                        st.session_state["stage"] = "ambiguous_currency"
                except Exception as exc:
                    st.error(f"Extraction failed: {exc}")

        elif source_type == "audio":
            with st.spinner("Uploading → Transcribing → Extracting… (~30 s)"):
                try:
                    vd = _run_audio(file_path)
                    st.session_state["voice_data"] = vd
                    st.session_state["transcript"] = vd.get("transcript", "")
                    amount = vd.get("amount_original")
                    currency = vd.get("currency_original")
                    if amount is not None and currency:
                        st.session_state["extracted_items"] = [
                            (float(amount), str(currency).upper())
                        ]
                        st.session_state["stage"] = "extracted"
                    elif amount is not None:
                        st.session_state["stage"] = "ambiguous_voice_currency"
                    else:
                        st.error("Could not extract an amount from the audio.")
                except Exception as exc:
                    st.error(f"Audio pipeline failed: {exc}")

# ── Auto-run FX when items are ready ──────────────────────────────────────────
if st.session_state["stage"] == "extracted" and st.session_state["extracted_items"]:
    items = st.session_state["extracted_items"]
    if (
        st.session_state["fx_results"] is None
        or st.session_state["fx_payment_type"] != payment_type
    ):
        with st.spinner("Fetching exchange rate…"):
            try:
                st.session_state["fx_results"] = _do_fx(items, payment_type)
                st.session_state["fx_payment_type"] = payment_type
                st.session_state["stage"] = "done"
            except Exception as exc:
                st.error(f"FX conversion failed: {exc}")

# ── Result box ─────────────────────────────────────────────────────────────────
st.markdown('<div class="result-box">', unsafe_allow_html=True)

stage = st.session_state["stage"]
raw = st.session_state["extraction_raw"]
voice = st.session_state["voice_data"]
results: list[FXResult] | None = st.session_state["fx_results"]

# ── Disambiguation: amount unclear (visual) ────────────────────────────────────
if stage == "ambiguous_amount" and isinstance(raw, AmountAmbiguousResult):
    st.warning(f"Read **\"{raw.raw_text}\"** — the number is unclear.")
    geo_ccy = geo.currency_iso if geo else ""
    amt = st.number_input("Enter the amount:", min_value=0.0, step=0.01, key="da_amount")
    ccy = st.text_input(
        "Currency code (e.g. USD):",
        value=raw.currency_iso or geo_ccy,
        max_chars=3, key="da_ccy",
    ).upper().strip()
    if st.button("✅ Confirm", key="confirm_amount"):
        if amt > 0 and ccy:
            st.session_state["extracted_items"] = [(amt, ccy)]
            st.session_state["stage"] = "extracted"
            st.rerun()

# ── Disambiguation: currency unclear (visual) ──────────────────────────────────
elif stage == "ambiguous_currency" and isinstance(raw, CurrencyAmbiguousResult):
    geo_ccy = geo.currency_iso if geo else None
    suggested = geo_ccy or raw.suggested_currency_iso
    st.warning(f"Read **{raw.amount:,.2f}** from *\"{raw.raw_text}\"* — currency unclear.")
    if suggested:
        source_label = "geolocation" if geo_ccy else "model suggestion"
        st.info(f"📍 {source_label.capitalize()} suggests: **{suggested}**")
    ccy = st.text_input(
        "Confirm or enter currency code:",
        value=suggested or "",
        max_chars=3, key="dc_ccy",
    ).upper().strip()
    if st.button("✅ Confirm", key="confirm_currency"):
        if ccy:
            st.session_state["extracted_items"] = [(raw.amount, ccy)]
            st.session_state["stage"] = "extracted"
            st.rerun()

# ── Disambiguation: currency unclear (audio) ───────────────────────────────────
elif stage == "ambiguous_voice_currency" and voice is not None:
    amount = voice.get("amount_original")
    geo_ccy = geo.currency_iso if geo else None
    transcript = st.session_state.get("transcript", "")
    if transcript:
        st.caption(f"Transcript: *\"{transcript}\"*")
    st.warning(f"Heard **{amount}** but could not identify the currency.")
    if geo_ccy:
        st.info(f"📍 Geolocation suggests: **{geo_ccy}**")
    ccy = st.text_input(
        "Confirm or enter currency code:",
        value=geo_ccy or "",
        max_chars=3, key="dv_ccy",
    ).upper().strip()
    if st.button("✅ Confirm", key="confirm_voice"):
        if ccy and amount is not None:
            st.session_state["extracted_items"] = [(float(amount), ccy)]
            st.session_state["stage"] = "extracted"
            st.rerun()

# ── Result display ─────────────────────────────────────────────────────────────
elif results:
    # Show transcript above result if this came from audio
    if st.session_state.get("transcript"):
        st.caption(f"*\"{st.session_state['transcript']}\"*")

    for r in results:
        if r.error and r.base_rate == 0.0:
            st.error(f"Could not convert {r.input_currency}: {r.error}")
            continue

        mode_badge = (
            '<span class="badge">💳 Card</span>'
            if r.payment_type == "card"
            else '<span class="badge badge-transfer">🏦 Transfer</span>'
        )
        approx_badge = (
            '<span class="badge badge-approx">~ approx rate</span>'
            if r.source == "approximate" else ""
        )
        weekend_note = " ⚠️ weekend" if r.is_weekend and r.weekend_fee > 0 else ""
        fee_line = f"+€{r.total_fees:.4f} fees{weekend_note}  ·  {r.source}"

        st.markdown(f"""
        <div class="result-amount-foreign">{r.input_amount:,.2f} {r.input_currency}</div>
        <div class="result-amount-eur">€ {r.total_eur:,.4f}</div>
        <div class="result-meta">{mode_badge}{approx_badge}{fee_line}</div>
        """, unsafe_allow_html=True)

        with st.expander("Fee breakdown"):
            c1, c2, c3 = st.columns(3)
            c1.metric("Base (pre-fee)", f"€ {r.base_eur:.4f}")
            c2.metric(
                "ZeroFX" if r.payment_type == "card" else "Wise fee",
                f"€ {r.service_fee:.4f}",
            )
            c3.metric("Weekend fee", f"€ {r.weekend_fee:.4f}")

    st.divider()
    col_tts, col_reset = st.columns(2)
    with col_tts:
        if st.button("🔊 Read aloud"):
            audio_out = _make_tts(results)
            if audio_out:
                st.audio(audio_out, format="audio/mp3")
            else:
                st.warning("gTTS unavailable.")
    with col_reset:
        if st.button("🔁 New scan"):
            _reset()
            st.rerun()

# ── Idle state ─────────────────────────────────────────────────────────────────
else:
    st.markdown(
        "<p style='color:#8E8E93;text-align:center;margin-top:40px'>"
        "Final converted price will appear here."
        "</p>",
        unsafe_allow_html=True,
    )

st.markdown("</div>", unsafe_allow_html=True)
