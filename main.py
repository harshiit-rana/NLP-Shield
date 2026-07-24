import sys
import os
import json
import logging
import cv2
import numpy as np
import re
from pdf2image import convert_from_path

from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# Suppress PaddleOCR's noisy internal logging before import
logging.getLogger("ppocr").setLevel(logging.ERROR)
os.environ["FLAGS_log_level"] = "3"
from paddleocr import PaddleOCR

# ---------- CONFIG ----------
POPPLER_PATH = r"D:\ocr-try\Release-26.02.0-0\poppler-26.02.0\Library\bin"

# OCR config
OCR_CONFIDENCE_THRESHOLD = 0.80
MIN_QUAD_AREA_RATIO = 0.20

# Redaction config
REPLACEMENT_TOKEN = "XXXX"
REDACTION_CONFIDENCE_THRESHOLD = 0.4
ENTITIES_TO_REDACT = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "LOCATION",
    "ORGANIZATION",
    "US_SSN",
    "CREDIT_CARD",
    "IBAN_CODE",
    "US_BANK_NUMBER",
    "DATE_TIME",
]


# ---------- 1. PDF TO IMAGES ----------
def convert_pdf(pdf_path, out_dir="temp_pdf_pages", dpi=300):
    os.makedirs(out_dir, exist_ok=True)
    pages = convert_from_path(
        pdf_path,
        dpi=dpi,
        poppler_path=POPPLER_PATH
    )

    base = os.path.splitext(os.path.basename(pdf_path))[0]
    saved_paths = []

    for i, page in enumerate(pages, start=1):
        out_path = os.path.join(out_dir, f"{base}_page{i:03d}.png")
        page.save(out_path, "PNG")
        saved_paths.append(out_path)
        print(f"  -> Extracted page to: {out_path}")

    return saved_paths


# ---------- 2. PREPROCESSING & OCR ----------
def order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect

def try_perspective_correction(img):
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edged = cv2.Canny(blurred, 50, 150)
        edged = cv2.dilate(edged, None, iterations=2)

        contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return img, False

        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]
        img_area = img.shape[0] * img.shape[1]

        for c in contours:
            peri = cv2.arcLength(c, True)
            for eps_factor in (0.02, 0.03, 0.04):
                approx = cv2.approxPolyDP(c, eps_factor * peri, True)
                if len(approx) == 4 and cv2.contourArea(approx) > MIN_QUAD_AREA_RATIO * img_area:
                    pts = approx.reshape(4, 2).astype("float32")
                    rect = order_points(pts)
                    (tl, tr, br, bl) = rect

                    widthA = np.linalg.norm(br - bl)
                    widthB = np.linalg.norm(tr - tl)
                    maxWidth = int(max(widthA, widthB))

                    heightA = np.linalg.norm(tr - br)
                    heightB = np.linalg.norm(tl - bl)
                    maxHeight = int(max(heightA, heightB))

                    if maxWidth < 10 or maxHeight < 10:
                        continue

                    dst = np.array([
                        [0, 0],
                        [maxWidth - 1, 0],
                        [maxWidth - 1, maxHeight - 1],
                        [0, maxHeight - 1]], dtype="float32")

                    M = cv2.getPerspectiveTransform(rect, dst)
                    warped = cv2.warpPerspective(img, M, (maxWidth, maxHeight))
                    return warped, True

        return img, False
    except Exception as e:
        print(f"  [warn] perspective correction skipped ({e})")
        return img, False

def preprocess_image(image_path, save_debug=False):
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")

    img, corrected = try_perspective_correction(img)

    h, w = img.shape[:2]
    if max(h, w) < 1800:
        scale = 1800 / max(h, w)
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(gray, h=7)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    contrast = clahe.apply(denoised)

    processed = cv2.cvtColor(contrast, cv2.COLOR_GRAY2BGR)

    if save_debug:
        debug_path = os.path.splitext(image_path)[0] + "_preprocessed.png"
        cv2.imwrite(debug_path, processed)

    return processed

_ocr_engine = None
def get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    return _ocr_engine

def is_in_margin_zone(box, img_width, margin_fraction=0.15):
    if not box:
        return False
    xs = [pt[0] for pt in box]
    avg_x = sum(xs) / len(xs)
    return avg_x < (img_width * margin_fraction)

def run_ocr(processed_img):
    ocr = get_ocr_engine()
    lines = []

    results = ocr.predict(processed_img)
    for res in results:
        texts = res.get("rec_texts", [])
        scores = res.get("rec_scores", [])
        boxes = res.get("rec_polys", res.get("dt_polys", [None] * len(texts)))

        for text, conf, box in zip(texts, scores, boxes):
            box_clean = box.tolist() if hasattr(box, "tolist") else box
            lines.append({"text": text, "confidence": float(conf), "box": box_clean})

    return lines


# ---------- 3. REDACTION ----------
def build_custom_recognizers():
    recognizers = []
    tin_pattern = Pattern(name="ph_tin_pattern", regex=r"\b\d{3}-\d{3}-\d{3}-\d{3}\b", score=0.85)
    tin_recognizer = PatternRecognizer(supported_entity="PH_TIN", patterns=[tin_pattern], context=["TIN", "tax identification"])
    recognizers.append(tin_recognizer)

    ref_pattern = Pattern(name="reference_number_pattern", regex=r"\b(?:No\.?|RNO\.?|Ref\.?)\s?[A-Z0-9]{2,}[-/][A-Z0-9]{1,}\b", score=0.6)
    ref_recognizer = PatternRecognizer(supported_entity="REFERENCE_NUMBER", patterns=[ref_pattern])
    recognizers.append(ref_recognizer)

    money_pattern = Pattern(name="money_pattern", regex=r"[₱P$]\s?[\d,]+(?:\.\d{2})?", score=0.7)
    money_recognizer = PatternRecognizer(supported_entity="MONEY_AMOUNT", patterns=[money_pattern])
    recognizers.append(money_recognizer)

    return recognizers

_analyzer = None
_anonymizer = None

def get_redaction_engines():
    global _analyzer, _anonymizer
    if _analyzer is None:
        _analyzer = AnalyzerEngine()
        for recognizer in build_custom_recognizers():
            _analyzer.registry.add_recognizer(recognizer)
    if _anonymizer is None:
        _anonymizer = AnonymizerEngine()
    return _analyzer, _anonymizer

def redact_text(text):
    analyzer, anonymizer = get_redaction_engines()
    all_entities = ENTITIES_TO_REDACT + ["PH_TIN", "REFERENCE_NUMBER", "MONEY_AMOUNT"]

    results = analyzer.analyze(
        text=text,
        entities=all_entities,
        language="en",
        score_threshold=REDACTION_CONFIDENCE_THRESHOLD
    )

    operators = {
        entity: OperatorConfig("replace", {"new_value": REPLACEMENT_TOKEN})
        for entity in all_entities
    }

    anonymized = anonymizer.anonymize(
        text=text,
        analyzer_results=results,
        operators=operators
    )

    return anonymized.text, results


# ---------- 4. FULL PIPELINE ----------
def process_single_image(img_path, out_dir):
    print(f"\n--- Processing Image: {img_path} ---")
    
    # OCR Stage
    processed = preprocess_image(img_path, save_debug=False)
    lines = run_ocr(processed)

    img_width = processed.shape[1]
    flagged = []
    full_text = ""
    for l in lines:
        if l["confidence"] < OCR_CONFIDENCE_THRESHOLD:
            l["likely_signature_noise"] = is_in_margin_zone(l["box"], img_width)
            flagged.append(l)
        full_text += l["text"] + "\n"

    print(f"  [OCR] {len(lines)} lines extracted, {len(flagged)} flagged for review.")

    # Redaction Stage
    redacted_text, detections = redact_text(full_text)
    
    # Save outputs
    base = os.path.splitext(os.path.basename(img_path))[0]
    
    # Save original text (audit)
    with open(os.path.join(out_dir, f"{base}_original.txt"), "w", encoding="utf-8") as f:
        f.write(full_text)
        
    # Save redacted text
    out_redacted_path = os.path.join(out_dir, f"{base}_redacted.txt")
    with open(out_redacted_path, "w", encoding="utf-8") as f:
        f.write(redacted_text)
        
    # Save JSON audit log (OCR confidence + Redactions)
    audit_log = {
        "ocr_flagged_lines": flagged,
        "redactions": []
    }
    for d in detections:
        audit_log["redactions"].append({
            "entity_type": d.entity_type,
            "score": d.score,
            "start": d.start,
            "end": d.end,
            "original_text": full_text[d.start:d.end]
        })
        
    with open(os.path.join(out_dir, f"{base}_audit.json"), "w", encoding="utf-8") as f:
        json.dump(audit_log, f, indent=2)

    print(f"  [Redact] {len(detections)} entities redacted.")
    print(f"  -> Saved redacted text: {out_redacted_path}")
    print(f"  -> Saved audit log: {base}_audit.json")


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <file_or_folder>")
        print("Supported formats: .pdf, .jpg, .png, .tiff, .bmp")
        sys.exit(1)

    input_path = sys.argv[1]
    out_dir = "output_processed"
    os.makedirs(out_dir, exist_ok=True)
    
    # Temporary dir for PDF pages
    pdf_pages_dir = "temp_pdf_pages"
    
    files_to_process = []
    
    if os.path.isdir(input_path):
        for f in os.listdir(input_path):
            files_to_process.append(os.path.join(input_path, f))
    else:
        files_to_process.append(input_path)
        
    for f in files_to_process:
        ext = os.path.splitext(f)[1].lower()
        if ext == ".pdf":
            print(f"\n[PDF] Converting {f} to images...")
            img_paths = convert_pdf(f, out_dir=pdf_pages_dir)
            for img_path in img_paths:
                process_single_image(img_path, out_dir)
        elif ext in [".jpg", ".jpeg", ".png", ".bmp", ".tiff"]:
            process_single_image(f, out_dir)
        else:
            print(f"Skipping unsupported file: {f}")
            
    print(f"\nPipeline complete. All outputs saved in ./{out_dir}/")

if __name__ == "__main__":
    main()
