from pathlib import Path

import cv2
import gradio as gr
from dotenv import load_dotenv

load_dotenv()

from extractor import (
    AmountAmbiguousResult,
    CurrencyAmbiguousResult,
    ExtractionResult,
    FailureResult,
    SuccessResult,
    extract_from_image,
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


def process(image_path: str | None, flip: bool = False) -> str:
    if image_path is None:
        return ""
    path = Path(image_path)
    if flip:
        img = cv2.imread(str(path))
        cv2.imwrite(str(path), cv2.flip(img, 1))
    return _format_result(extract_from_image(path))


demo = gr.Interface(
    fn=process,
    inputs=[
        gr.Image(
            sources=["webcam", "upload"],
            type="filepath",
            label="Point at a price tag, snap a photo, or upload an image",
        ),
        gr.Checkbox(label="Flip image horizontally (use if webcam text appears mirrored)", value=True),
    ],
    outputs=gr.Text(label="Extracted price"),
    title="Price Extractor",
    description="Take a photo or upload an image containing a price. The app will extract the amount and currency.",
    flagging_mode="never",
)

if __name__ == "__main__":
    demo.launch()
