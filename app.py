from __future__ import annotations

import os
import uuid
from pathlib import Path

import streamlit as st

import hyperparams
from api_keys import get_anthropic_api_key, get_aws_region
from fx_converter import convert_to_eur
from model_interface import from_extraction_result, from_voice_output


UPLOAD_DIR = Path("streamlit_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


# ---------------- PIPELINE FUNCTIONS ----------------

def process_image(img_path: Path):
    from extractor import extract_from_image, GeoContext
    from anthropic import Anthropic
    from geo import get_location

    geo = None
    loc = get_location()

    if loc:
        geo = GeoContext(
            country_name=loc.country_name,
            iso_country_code=loc.iso_country_code,
            likely_currency_iso=loc.currency_iso,
        )
    elif hyperparams.DEFAULT_ISO_COUNTRY_CODE:
        geo = GeoContext(
            country_name=hyperparams.DEFAULT_COUNTRY_NAME or "",
            iso_country_code=hyperparams.DEFAULT_ISO_COUNTRY_CODE,
            likely_currency_iso=hyperparams.DEFAULT_CURRENCY_ISO or "",
        )

    os.environ.setdefault("ANTHROPIC_API_KEY", get_anthropic_api_key())
    client = Anthropic()

    extraction_result = extract_from_image(img_path, geo=geo, client=client)
    items = from_extraction_result(extraction_result)

    if not items:
        return None, extraction_result

    results = convert_to_eur(items)
    return results, extraction_result


def process_audio(audio_path: Path):
    from auto_voice_to_eur_aws import (
        upload_to_s3,
        start_transcribe_job,
        wait_for_transcribe_job,
        download_transcript_text,
        extract_money_with_bedrock,
        get_language_metadata,
    )

    bucket = hyperparams.AWS_S3_BUCKET
    if not bucket:
        raise RuntimeError("AWS_S3_BUCKET is missing.")

    region = get_aws_region()
    uid = uuid.uuid4().hex

    s3_key = f"{hyperparams.AWS_S3_PREFIX.rstrip('/')}/voice_money_{uid}.wav"
    job_name = f"voice-money-{uid}"

    media_uri = upload_to_s3(audio_path, bucket, s3_key, region)

    start_transcribe_job(
        media_uri=media_uri,
        region=region,
        job_name=job_name,
        language_options=hyperparams.TRANSCRIBE_LANGUAGE_OPTIONS or None,
        identify_multiple_languages=hyperparams.TRANSCRIBE_MULTI_LANGUAGE,
    )

    job = wait_for_transcribe_job(job_name, region)
    transcript = download_transcript_text(job["Transcript"]["TranscriptFileUri"])

    money = extract_money_with_bedrock(
        transcript=transcript,
        region=region,
        model_id=hyperparams.AWS_BEDROCK_MODEL_ID,
    )

    voice_data = {
        "amount_original": money.get("amount"),
        "currency_original": money.get("currency"),
        **get_language_metadata(job),
    }

    items = from_voice_output(voice_data)

    if not items:
        return None, transcript, money

    results = convert_to_eur(items)
    return results, transcript, money


# ---------------- UI ----------------

st.set_page_config(
    page_title="Currency Scanner",
    page_icon="💶",
    layout="centered",
)

st.markdown("""
<style>
.block-container {
    max-width: 430px;
    padding-top: 1rem;
}

.app-title {
    text-align: center;
    font-size: 28px;
    font-weight: 700;
    margin-bottom: 10px;
}

.camera-box {
    border: 2px solid #111;
    border-radius: 18px;
    padding: 16px;
    min-height: 360px;
    background: #fafafa;
}

.result-box {
    border: 2px solid #111;
    border-radius: 18px;
    padding: 20px;
    min-height: 230px;
    margin-top: 20px;
    background: #ffffff;
}

div.stButton > button {
    width: 100%;
    height: 55px;
    border-radius: 50px;
    font-size: 17px;
    font-weight: 600;
}

[data-testid="stFileUploader"] {
    border: 1.5px dashed #999;
    border-radius: 16px;
    padding: 10px;
}

[data-testid="stCameraInput"] {
    border-radius: 16px;
}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="app-title">Currency Scanner</div>', unsafe_allow_html=True)

input_mode = st.segmented_control(
    "Input type",
    ["Take photo", "Upload photo", "Record audio"],
    default="Take photo",
)

st.markdown('<div class="camera-box">', unsafe_allow_html=True)

file_path = None
source_type = None

if input_mode == "Take photo":
    st.write("Camera")
    camera_file = st.camera_input("Take a photo")

    if camera_file:
        file_path = UPLOAD_DIR / f"camera_{uuid.uuid4().hex}.png"
        file_path.write_bytes(camera_file.getvalue())
        source_type = "image"

elif input_mode == "Upload photo":
    st.write("Gallery")
    uploaded_file = st.file_uploader(
        "Select picture from gallery",
        type=["jpg", "jpeg", "png", "webp"],
    )

    if uploaded_file:
        suffix = Path(uploaded_file.name).suffix
        file_path = UPLOAD_DIR / f"gallery_{uuid.uuid4().hex}{suffix}"
        file_path.write_bytes(uploaded_file.getvalue())
        st.image(uploaded_file, use_container_width=True)
        source_type = "image"

elif input_mode == "Record audio":
    st.write("Microphone")
    audio_file = st.audio_input("Record price by voice")

    if audio_file:
        file_path = UPLOAD_DIR / f"audio_{uuid.uuid4().hex}.wav"
        file_path.write_bytes(audio_file.getvalue())
        st.audio(audio_file)
        source_type = "audio"

st.markdown("</div>", unsafe_allow_html=True)

process_clicked = st.button("Detect currency and price")

st.markdown('<div class="result-box">', unsafe_allow_html=True)
st.subheader("Output")

if process_clicked:
    if not file_path:
        st.warning("Take a photo, upload a picture, or record audio first.")

    elif source_type == "image":
        with st.spinner("Detecting price from image..."):
            try:
                results, raw = process_image(file_path)

                if not results:
                    st.error("No currency or price detected.")
                    st.write(raw)
                else:
                    st.success("Detected")
                    st.write(results)

            except Exception as e:
                st.error(str(e))

    elif source_type == "audio":
        with st.spinner("Detecting price from audio..."):
            try:
                results, transcript, money = process_audio(file_path)

                st.write("Transcript:")
                st.write(transcript)

                if not results:
                    st.error("No currency or price detected.")
                    st.write(money)
                else:
                    st.success("Detected")
                    st.write(results)

            except Exception as e:
                st.error(str(e))
else:
    st.write("Final converted price will appear here.")

st.markdown("</div>", unsafe_allow_html=True)