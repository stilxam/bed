import functools
from pathlib import Path

import cv2
import gradio as gr
import whisper
from dotenv import load_dotenv

load_dotenv()

from extractor import (
    AmountAmbiguousResult,
    CurrencyAmbiguousResult,
    ExtractionResult,
    FailureResult,
    SuccessResult,
    extract_from_image,
    extract_from_transcript,
)


def _format_result(result: ExtractionResult) -> str:
    if isinstance(result, SuccessResult):
        text = f"{result.amount}  {result.currency_iso}"
        if result.currency_inferred:
            text += "  (currency inferred from location)"
        return text

    if isinstance(result, AmountAmbiguousResult):
        currency_hint = f" | Currency detected: {result.currency_iso}" if result.currency_iso else ""
        return f"Could not read the amount clearly — please enter it manually.{currency_hint}"

    if isinstance(result, CurrencyAmbiguousResult):
        if result.suggested_currency_iso:
            name = result.suggested_currency_name or result.suggested_currency_iso
            return f"{result.amount}  — is this {name} ({result.suggested_currency_iso})?"
        return f"{result.amount}  — currency unclear, please select manually."

    if isinstance(result, FailureResult):
        msg = f"Extraction failed: {result.reason}"
        if result.detail:
            msg += f"\n{result.detail}"
        return msg

    return "Unknown result"


@functools.lru_cache(maxsize=1)
def _whisper_model() -> whisper.Whisper:
    return whisper.load_model("base")


def process_audio(audio_path: str | None) -> str:
    if audio_path is None:
        return ""
    try:
        result = _whisper_model().transcribe(audio_path, fp16=False)
        transcript = result["text"].strip()
    except Exception as exc:
        return f"Transcription failed: {exc}"
    if not transcript:
        return "No speech detected"
    return _format_result(extract_from_transcript(transcript))


def process(image_path: str | None, flip: bool = False) -> str:
    if image_path is None:
        return ""
    path = Path(image_path)
    if flip:
        img = cv2.imread(str(path))
        cv2.imwrite(str(path), cv2.flip(img, 1))
    return _format_result(extract_from_image(path))


def process_combined(image_path: str | None, flip: bool, audio_path: str | None) -> str:
    if audio_path is not None:
        return process_audio(audio_path)
    return process(image_path, flip)


demo = gr.Interface(
    fn=process_combined,
    inputs=[
        gr.Image(
            sources=["webcam", "upload"],
            type="filepath",
            label="Photo",
        ),
        gr.Checkbox(label="Flip image horizontally (use if webcam text appears mirrored)", value=True),
        gr.Audio(
            sources=["microphone"],
            type="filepath",
            label="Or say the price (any language)",
        ),
    ],
    outputs=gr.Text(label="Extracted price"),
    title="Price Extractor",
    flagging_mode="never",
)

if __name__ == "__main__":
    demo.launch()
