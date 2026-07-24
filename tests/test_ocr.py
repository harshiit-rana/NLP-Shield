"""OCR-layer tests: preprocessing and Tesseract extraction on rendered
images (requires the tesseract binary; skipped if unavailable)."""

import shutil

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from pseudonymiser.config import OcrConfig
from pseudonymiser.ocr import ocr_image, preprocess

FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract not installed")


def render(text: str, rotate: float = 0.0) -> np.ndarray:
    img = Image.new("RGB", (1600, 400), "white")
    draw = ImageDraw.Draw(img)
    font = None
    for path in FONTS:
        try:
            font = ImageFont.truetype(path, 48)
            break
        except OSError:
            continue
    assert font is not None, "no TTF font available for test rendering"
    draw.text((60, 150), text, fill="black", font=font)
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor="white")
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def test_preprocess_returns_single_channel():
    out = preprocess(render("Sample text 123"), OcrConfig())
    assert out.ndim == 2


@needs_tesseract
def test_ocr_clean_text():
    page = ocr_image(render("Invoice number 4521 for Rajesh"), OcrConfig())
    assert "Invoice" in page.text
    assert "4521" in page.text


@needs_tesseract
def test_ocr_skewed_text_recovered_by_deskew():
    page = ocr_image(render("Deskew check line", rotate=3.0), OcrConfig())
    assert "Deskew" in page.text


@needs_tesseract
def test_low_confidence_flagging():
    noise = (np.random.default_rng(42).random((400, 1600, 3)) * 255).astype("uint8")
    cfg = OcrConfig(low_confidence_threshold=99.9, denoise=False,
                    adaptive_threshold=False)
    page = ocr_image(noise, cfg)
    assert page.low_confidence or page.text.strip() == ""
