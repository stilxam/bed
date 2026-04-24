"""
number_to_output.py — Phone-style price display with text-to-speech.

Called by main.py as the final output stage of the pipeline.
TTS language and other settings are configured in hyperparams.py.
"""

import os
import tempfile
import threading
import tkinter as tk
from tkinter import font as tkfont

from fx_converter import FXResult
from hyperparams import TTS_LANGUAGE

# ── Colours ──────────────────────────────────────────────────────────────────
BG      = "#F2F2F7"
CARD_BG = "#FFFFFF"
HEADER  = "#FF7819"
TEXT    = "#1C1C1E"
MUTED   = "#8E8E93"
EUR_COL = "#1C7C3A"

# ── TTS ──────────────────────────────────────────────────────────────────────

def _build_speech(results: list[FXResult]) -> str:
    lines = []
    for r in results:
        if r.base_rate == 0.0:
            continue
        lines.append(f"{r.input_amount:.2f} {r.input_currency}. {round(r.total_eur, 2)} euro.")
    return "  ".join(lines)


def _play_mp3(path: str) -> None:
    import pygame
    pygame.mixer.init()
    pygame.mixer.music.load(path)
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        pygame.time.Clock().tick(10)
    pygame.mixer.quit()


def speak(results: list[FXResult], btn: tk.Button) -> None:
    def _run():
        btn.config(state="disabled", text="Speaking…")
        try:
            from gtts import gTTS
            tts = gTTS(text=_build_speech(results), lang=TTS_LANGUAGE, slow=False)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                tmp = f.name
            tts.save(tmp)
            _play_mp3(tmp)
            os.unlink(tmp)
        except Exception as e:
            print(f"TTS error: {e}")
        finally:
            btn.config(state="normal", text="🔊  Speak")
    threading.Thread(target=_run, daemon=True).start()


# ── GUI ──────────────────────────────────────────────────────────────────────

def build_gui(results: list[FXResult]) -> None:
    root = tk.Tk()
    root.title("FX Prices")
    root.geometry("390x700")
    root.resizable(False, False)
    root.configure(bg=BG)

    bold   = tkfont.Font(family="Helvetica", size=16, weight="bold")
    body   = tkfont.Font(family="Helvetica", size=14)
    small  = tkfont.Font(family="Helvetica", size=11)
    header = tkfont.Font(family="Helvetica", size=18, weight="bold")

    # Header
    hdr = tk.Frame(root, bg=HEADER, height=60)
    hdr.pack(fill="x")
    hdr.pack_propagate(False)
    tk.Label(hdr, text="Price Converter", bg=HEADER, fg="white", font=header).pack(expand=True)

    # Scrollable card list
    canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
    scroll = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    canvas.pack(fill="both", expand=True)

    frame = tk.Frame(canvas, bg=BG)
    canvas.create_window((0, 0), window=frame, anchor="nw", width=374)

    for r in results:
        card = tk.Frame(frame, bg=CARD_BG, pady=14, padx=16,
                        highlightbackground="#E0E0E0", highlightthickness=1)
        card.pack(fill="x", padx=8, pady=6)

        top = tk.Frame(card, bg=CARD_BG)
        top.pack(fill="x")
        tk.Label(top, text=f"{r.input_amount:,.2f}", font=bold, bg=CARD_BG, fg=TEXT).pack(side="left")
        tk.Label(top, text=f"  {r.input_currency}", font=body, bg=CARD_BG, fg=MUTED).pack(side="left")

        bot = tk.Frame(card, bg=CARD_BG)
        bot.pack(fill="x", pady=(4, 0))
        if r.base_rate == 0.0:
            tk.Label(bot, text="Rate unavailable", font=small, bg=CARD_BG, fg="red").pack(side="left")
        else:
            tk.Label(bot, text=f"€ {r.total_eur:,.4f}", font=bold, bg=CARD_BG, fg=EUR_COL).pack(side="left")
            src = "~" if r.source == "approximate" else ""
            tk.Label(bot, text=f"  {src}{r.base_rate:.5f} rate · +€{r.total_fees:.4f} fees",
                     font=small, bg=CARD_BG, fg=MUTED).pack(side="left")

    frame.update_idletasks()
    canvas.configure(scrollregion=canvas.bbox("all"))

    # Speak button
    speak_btn = tk.Button(
        root, text="🔊  Speak", font=body,
        bg=HEADER, fg="white", activebackground="#e06010",
        relief="flat", pady=12, cursor="hand2",
        command=lambda: speak(results, speak_btn),
    )
    speak_btn.pack(fill="x", padx=16, pady=12)

    root.mainloop()


