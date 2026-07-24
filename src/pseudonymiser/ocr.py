"""Text extraction from PDFs and images.

If a PDF has a real text layer it is extracted directly with PyMuPDF.
Otherwise pages are rasterised at >=300 DPI with pdf2image/poppler,
preprocessed with OpenCV (perspective correction, deskew, denoise,
adaptive thresholding, border removal) and OCR'd with Tesseract
(``--oem 1``, word-level confidence via ``image_to_data``). PaddleOCR is
available as an alternative engine behind ``ocr.engine: paddleocr``.

Everything here is offline; models/binaries must already be present
(see ``make download-models``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .config import OcrConfig

logger = logging.getLogger(__name__)


class OcrError(Exception):
    """Raised when a page or document cannot be processed."""


@dataclass
class PageText:
    """Extracted text for one page plus quality metadata."""

    page_number: int
    text: str
    mean_confidence: float | None = None  # None => text layer, no OCR
    low_confidence: bool = False
    source: str = "ocr"  # "ocr" | "text-layer"


@dataclass
class DocumentText:
    """Extraction result for one input document."""

    path: str
    pages: list[PageText] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)

    @property
    def flagged_pages(self) -> list[int]:
        return [p.page_number for p in self.pages if p.low_confidence]


# ---------------------------------------------------------------------------
# Text-layer detection / direct extraction
# ---------------------------------------------------------------------------

def has_text_layer(pdf_path: str | Path, min_chars_per_page: int = 32) -> bool:
    """True if the PDF already carries a usable embedded text layer."""
    import fitz  # PyMuPDF

    with fitz.open(pdf_path) as doc:
        if doc.page_count == 0:
            return False
        pages_with_text = sum(
            1 for page in doc if len(page.get_text().strip()) >= min_chars_per_page
        )
        return pages_with_text >= max(1, doc.page_count // 2)


def extract_text_layer(pdf_path: str | Path) -> DocumentText:
    """Extract the embedded text layer directly (no OCR)."""
    import fitz

    result = DocumentText(path=str(pdf_path))
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            result.pages.append(
                PageText(page_number=i, text=page.get_text(), source="text-layer")
            )
    logger.info("Extracted text layer from %s (%d pages)", pdf_path, len(result.pages))
    return result


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------

def _order_points(pts: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def _perspective_correction(img: np.ndarray, min_quad_area_ratio: float = 0.5) -> np.ndarray:
    """Warp a photographed page to a flat rectangle when a dominant
    quadrilateral is found; otherwise return the image unchanged."""
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edged = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
        edged = cv2.dilate(edged, None, iterations=2)
        contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return img
        img_area = img.shape[0] * img.shape[1]
        for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
            peri = cv2.arcLength(c, True)
            for eps in (0.02, 0.03, 0.04):
                approx = cv2.approxPolyDP(c, eps * peri, True)
                if len(approx) == 4 and cv2.contourArea(approx) > min_quad_area_ratio * img_area:
                    rect = _order_points(approx.reshape(4, 2).astype("float32"))
                    (tl, tr, br, bl) = rect
                    w = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
                    h = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
                    if w < 100 or h < 100:
                        continue
                    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]],
                                   dtype="float32")
                    return cv2.warpPerspective(img, cv2.getPerspectiveTransform(rect, dst), (w, h))
        return img
    except cv2.error:
        logger.warning("Perspective correction failed on a page; using original image")
        return img


def _measure_skew(gray: np.ndarray) -> float:
    """Estimated text-line skew in degrees, normalised to (-45, 45]."""
    inverted = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(inverted > 0))
    if len(coords) < 100:
        return 0.0
    angle = cv2.minAreaRect(coords)[-1]
    if angle > 45:
        angle -= 90
    return float(angle)


def _rotate(gray: np.ndarray, angle: float) -> np.ndarray:
    (h, w) = gray.shape[:2]
    m = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def _deskew(gray: np.ndarray, max_angle: float = 15.0) -> np.ndarray:
    """Rotate the page so text lines are horizontal (small angles only).

    minAreaRect's angle-sign convention has changed between OpenCV
    releases, so rather than trusting the sign we rotate both ways and
    keep the result whose re-measured residual skew is smallest.
    """
    angle = _measure_skew(gray)
    if abs(angle) < 0.1 or abs(angle) > max_angle:
        return gray
    best, best_residual = gray, abs(angle)
    for candidate_angle in (angle, -angle):
        rotated = _rotate(gray, candidate_angle)
        residual = abs(_measure_skew(rotated))
        if residual < best_residual:
            best, best_residual = rotated, residual
    return best


def _remove_borders(binary: np.ndarray, scan_fraction: float = 0.05,
                    dark_row_threshold: float = 0.7) -> np.ndarray:
    """Crop solid dark scanner borders from the page edges."""
    h, w = binary.shape[:2]
    dark = binary < 128

    def first_clean(rng, is_row: bool) -> int:
        for idx in rng:
            ratio = dark[idx, :].mean() if is_row else dark[:, idx].mean()
            if ratio < dark_row_threshold:
                return idx
        return rng[0]

    top = first_clean(range(0, int(h * scan_fraction)), True)
    bottom = first_clean(range(h - 1, int(h * (1 - scan_fraction)) - 1, -1), True)
    left = first_clean(range(0, int(w * scan_fraction)), False)
    right = first_clean(range(w - 1, int(w * (1 - scan_fraction)) - 1, -1), False)
    if bottom - top < h // 2 or right - left < w // 2:
        return binary
    return binary[top:bottom + 1, left:right + 1]


def preprocess(img: np.ndarray, cfg: OcrConfig) -> np.ndarray:
    """Full preprocessing chain; returns a single-channel image for OCR."""
    if cfg.perspective_correction:
        img = _perspective_correction(img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if cfg.deskew:
        gray = _deskew(gray)
    if cfg.denoise:
        gray = cv2.fastNlMeansDenoising(gray, h=7)
    if cfg.adaptive_threshold:
        gray = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 31, 15)
    if cfg.border_removal:
        gray = _remove_borders(gray)
    # Tesseract segments noticeably better with a clean white margin
    # around the page content.
    gray = cv2.copyMakeBorder(gray, 24, 24, 24, 24,
                              cv2.BORDER_CONSTANT, value=255)
    return gray


# ---------------------------------------------------------------------------
# OCR engines
# ---------------------------------------------------------------------------

def _ocr_tesseract(gray: np.ndarray, cfg: OcrConfig) -> tuple[str, float]:
    """Run Tesseract and return (text, mean word confidence 0-100)."""
    import pytesseract
    from pytesseract import Output

    tess_config = f"--oem {cfg.tesseract_oem} --psm {cfg.tesseract_psm}"
    data = pytesseract.image_to_data(gray, config=tess_config, output_type=Output.DICT)

    lines: dict[tuple[int, int, int], list[str]] = {}
    confidences: list[float] = []
    for i, word in enumerate(data["text"]):
        conf = float(data["conf"][i])
        if conf < 0 or not word.strip():
            continue
        confidences.append(conf)
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)

    text = "\n".join(" ".join(words) for _, words in sorted(lines.items()))
    mean_conf = float(np.mean(confidences)) if confidences else 0.0
    return text, mean_conf


_paddle_engine = None


def _ocr_paddle(gray: np.ndarray, cfg: OcrConfig) -> tuple[str, float]:
    """Run PaddleOCR (optional dependency) and return (text, mean conf 0-100)."""
    global _paddle_engine
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise OcrError(
            "ocr.engine is 'paddleocr' but PaddleOCR is not installed. "
            "Install the optional dependency (pip install paddleocr paddlepaddle) "
            "or set ocr.engine: tesseract in config.yaml."
        ) from exc

    if _paddle_engine is None:
        _paddle_engine = PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            # paddlepaddle 3.x CPU crashes in oneDNN PIR conversion on
            # some ops; plain CPU inference avoids it
            enable_mkldnn=False,
        )
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    texts: list[str] = []
    scores: list[float] = []
    for res in _paddle_engine.predict(bgr):
        texts.extend(res.get("rec_texts", []))
        scores.extend(float(s) for s in res.get("rec_scores", []))
    mean_conf = float(np.mean(scores)) * 100 if scores else 0.0
    return "\n".join(texts), mean_conf


def ocr_image(img: np.ndarray, cfg: OcrConfig, page_number: int = 1) -> PageText:
    """Preprocess and OCR one page image."""
    gray = preprocess(img, cfg)
    if cfg.engine == "paddleocr":
        text, conf = _ocr_paddle(gray, cfg)
    elif cfg.engine == "tesseract":
        text, conf = _ocr_tesseract(gray, cfg)
    else:
        raise OcrError(f"Unknown OCR engine: {cfg.engine!r}")
    low = conf < cfg.low_confidence_threshold
    if low:
        logger.warning("Page %d has low mean OCR confidence (%.1f); "
                       "flagging for manual review", page_number, conf)
    return PageText(page_number=page_number, text=text,
                    mean_confidence=conf, low_confidence=low)


# ---------------------------------------------------------------------------
# Document entry point
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"}


def extract_document(path: str | Path, cfg: OcrConfig) -> DocumentText:
    """Extract text from a PDF or image file, OCR-ing only when needed."""
    path = Path(path)
    if not path.exists():
        raise OcrError(f"Input file does not exist: {path}")

    if path.suffix.lower() == ".pdf":
        if has_text_layer(path):
            return extract_text_layer(path)
        return _extract_scanned_pdf(path, cfg)

    if path.suffix.lower() in IMAGE_EXTENSIONS:
        img = cv2.imread(str(path))
        if img is None:
            raise OcrError(f"Could not read image: {path}")
        return DocumentText(path=str(path), pages=[ocr_image(img, cfg)])

    raise OcrError(f"Unsupported file type: {path.suffix!r} ({path})")


def _extract_scanned_pdf(path: Path, cfg: OcrConfig) -> DocumentText:
    from pdf2image import convert_from_path
    from pdf2image.exceptions import PDFPageCountError, PDFInfoNotInstalledError

    dpi = max(cfg.dpi, 300)
    try:
        pages = convert_from_path(str(path), dpi=dpi)
    except PDFInfoNotInstalledError as exc:
        raise OcrError("poppler is not installed or not on PATH "
                       "(needed by pdf2image)") from exc
    except PDFPageCountError as exc:
        raise OcrError(f"Could not read PDF: {path}") from exc

    result = DocumentText(path=str(path))
    for i, pil_page in enumerate(pages, start=1):
        img = cv2.cvtColor(np.array(pil_page), cv2.COLOR_RGB2BGR)
        result.pages.append(ocr_image(img, cfg, page_number=i))
        logger.info("OCR page %d/%d of %s: mean confidence %.1f",
                    i, len(pages), path.name, result.pages[-1].mean_confidence)
    return result
