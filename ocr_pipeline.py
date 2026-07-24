"""
OCR pipeline for scanned/photographed documents, CPU-only.

Steps:
  1. Preprocess image (deskew/perspective fix, denoise, contrast - NO hard binarization)
  2. Run PaddleOCR (detection + recognition), PaddleOCR 3.x compatible
  3. Flag any low-confidence lines for manual review
  4. Save results to a text file + a JSON file with confidence scores

Usage:
  python ocr_pipeline.py path/to/image.jpg
  python ocr_pipeline.py path/to/folder_of_images/
"""

import sys
import os
import json
import logging
import cv2
import numpy as np

# Suppress PaddleOCR's noisy internal logging before import
logging.getLogger("ppocr").setLevel(logging.ERROR)
os.environ["FLAGS_log_level"] = "3"

from paddleocr import PaddleOCR

CONFIDENCE_THRESHOLD = 0.80  # anything below this gets flagged for review
MIN_QUAD_AREA_RATIO = 0.20   # perspective correction only kicks in if a big-enough quad is found

# ---------- 1. PREPROCESSING ----------

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
    """Attempt to find document/receipt edges and warp to a flat rectangle.
    Falls back to the original image if no clear quadrilateral is found,
    or if anything goes wrong (never let this step crash the pipeline)."""
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


def preprocess_image(image_path, save_debug=False, debug_dir="debug_preprocessed"):
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
        os.makedirs(debug_dir, exist_ok=True)  # separate folder, not next to input
        debug_path = os.path.join(debug_dir, os.path.basename(image_path))
        cv2.imwrite(debug_path, processed)

    return processed


# ---------- 2. OCR (PaddleOCR 3.x) ----------

_ocr_engine = None

def get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,  # skip — not needed for flat scans, avoids oneDNN crash
            use_doc_unwarping=False,              # skip — for warped/curved photos only
            use_textline_orientation=False,       # skip — only needed for rotated/mixed-orientation lines
        )
    return _ocr_engine


def is_in_margin_zone(box, img_width, margin_fraction=0.15):
    """Rough check: does this detected box sit mostly in the left margin
    where signatures/stamps usually are? Helps triage flagged lines faster."""
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


# ---------- 3. MAIN ----------

def process_image(image_path, out_dir):
    print(f"Processing: {image_path}")
    processed = preprocess_image(image_path, save_debug=True)
    lines = run_ocr(processed)

    img_width = processed.shape[1]
    flagged = []
    for l in lines:
        if l["confidence"] < CONFIDENCE_THRESHOLD:
            l["likely_signature_noise"] = is_in_margin_zone(l["box"], img_width)
            flagged.append(l)

    base = os.path.splitext(os.path.basename(image_path))[0]

    # Save plain text
    txt_path = os.path.join(out_dir, f"{base}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        for l in lines:
            f.write(l["text"] + "\n")

    # Save full JSON with confidence scores
    json_path = os.path.join(out_dir, f"{base}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"lines": lines, "flagged_low_confidence": flagged}, f, indent=2)

    print(f"  -> {len(lines)} lines extracted, {len(flagged)} flagged for review")
    if flagged:
        print("  LOW CONFIDENCE LINES (review these manually):")
        for l in flagged:
            tag = "margin/signature?" if l["likely_signature_noise"] else "IN BODY TEXT - CHECK THIS"
            print(f"    [{l['confidence']:.2f}] ({tag}) {l['text']}")

    return lines, flagged


def main():
    if len(sys.argv) < 2:
        print("Usage: python ocr_pipeline.py <image_path_or_folder>")
        sys.exit(1)

    input_path = sys.argv[1]
    out_dir = "ocr_output"
    os.makedirs(out_dir, exist_ok=True)

    if os.path.isdir(input_path):
        exts = (".jpg", ".jpeg", ".png", ".bmp", ".tiff")
        images = [os.path.join(input_path, f) for f in os.listdir(input_path)
                  if f.lower().endswith(exts)]
        for img_path in images:
            process_image(img_path, out_dir)
    else:
        process_image(input_path, out_dir)

    print(f"\nDone. Results saved in ./{out_dir}/")


if __name__ == "__main__":
    main()