"""End-to-end PII pseudonymisation pipeline — single script, fully local.

Flow (exactly as specified):
  1. PDF -> page images (PyMuPDF, 300 DPI, temp folder)
  2. Preprocess + PaddleOCR each image -> text; images are DELETED, text kept
  3. Presidio + spaCy transformer NER identify all PII in the text
  4. Every unique PII value gets a unique bogus value (Faker) stored in
     <name>.mapping.csv for remapping/restore
  5. All occurrences of every PII value are replaced with its bogus value
     -> <name>.sanitized.txt (with a residual-leak self check at the end)

Usage:
  python main.py document.pdf                 # or a folder of PDFs/images
  python main.py document.pdf -o output_dir
  python main.py --restore output/doc.sanitized.txt output/doc.mapping.csv

Everything runs offline; nothing leaves this machine.
"""

import argparse
import csv
import logging
import os
import re
import shutil
import sys
import tempfile

import cv2
import numpy as np

# Silence Paddle's noisy logging before it is imported.
logging.getLogger("ppocr").setLevel(logging.ERROR)
os.environ.setdefault("FLAGS_log_level", "3")

# ---------- CONFIG ----------

DPI = 300
OCR_CONFIDENCE_THRESHOLD = 0.80   # lines below this are printed for review
DETECTION_THRESHOLD = 0.35        # recall-first: over-redacting beats leaking
SPACY_MODEL = "en_core_web_trf"
FAKER_SEED = 20260724             # fixed seed => same doc gives same mapping

ENTITIES_TO_REDACT = [
    "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "LOCATION", "ORGANIZATION",
    "DATE_TIME", "NRP", "US_SSN", "CREDIT_CARD", "IBAN_CODE",
    "US_BANK_NUMBER", "PH_TIN", "REFERENCE_NUMBER", "MONEY_AMOUNT",
    "MONEY_WORDS", "ORG_CODE", "ORG_UNIT",
]

# Contract role words / generic terms that NER sometimes tags but that are
# not identifying on their own — never mapped, never swept.
GENERIC_VALUES = {
    "entity", "supplier", "procuring entity", "the entity", "the supplier",
    "contractor", "vendor", "buyer", "seller", "witness", "agreement",
    "contract", "annex", "schedule", "conditions of contract",
}
GENERIC_ORG_TOKENS = {
    "trading", "company", "inc", "corp", "corporation", "enterprises",
    "bureau", "internal", "revenue", "national", "office", "group",
    "limited", "ltd", "services", "international", "department", "division",
    "region", "republic", "the", "and", "of",
}
PERSON_SWEEP_STOPWORDS = {
    "hand", "seal", "witness", "signed", "dear", "shri", "smt", "miss",
    "mister", "doctor", "junior", "senior", "asst", "regional", "director",
}
# Words too common to sweep as fragments of a longer detected value.
FRAGMENT_STOPWORDS = GENERIC_ORG_TOKENS | PERSON_SWEEP_STOPWORDS | {
    "street", "road", "avenue", "city", "town", "republic", "district",
    "saint", "santo", "general", "public", "human", "resource", "resources",
    "management", "administrative", "revenue", "document", "processing",
}
ACRONYM_SKIP_WORDS = {"of", "the", "and", "for", "in"}


# ---------- 1. PDF -> IMAGES (PyMuPDF, no poppler needed) ----------

def pdf_to_images(pdf_path, img_dir, dpi=DPI):
    import fitz  # PyMuPDF

    os.makedirs(img_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    paths = []
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=dpi)
            out_path = os.path.join(img_dir, f"{base}_page{i:03d}.png")
            pix.save(out_path)
            paths.append(out_path)
            print(f"  -> page {i} rendered")
    return paths


# ---------- 2. PREPROCESS + PADDLEOCR ----------
# Same approach as the original working pipeline: perspective fix, upscale,
# denoise, CLAHE contrast — NO hard binarization (Paddle reads grayscale
# far better than thresholded images).

def _order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def try_perspective_correction(img, min_quad_area_ratio=0.5):
    """Warp a photographed page flat if a dominant quad is found; rendered
    PDF pages are already flat, so this is a no-op for them."""
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edged = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
        edged = cv2.dilate(edged, None, iterations=2)
        contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
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
                    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1],
                                    [0, h - 1]], dtype="float32")
                    M = cv2.getPerspectiveTransform(rect, dst)
                    return cv2.warpPerspective(img, M, (w, h))
        return img
    except cv2.error:
        return img


def preprocess_image(image_path):
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")

    img = try_perspective_correction(img)

    h, w = img.shape[:2]
    if max(h, w) < 1800:
        scale = 1800 / max(h, w)
        img = cv2.resize(img, None, fx=scale, fy=scale,
                         interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(gray, h=7)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    contrast = clahe.apply(denoised)
    return cv2.cvtColor(contrast, cv2.COLOR_GRAY2BGR)


_ocr_engine = None


def get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from paddleocr import PaddleOCR
        _ocr_engine = PaddleOCR(
            lang="en",
            # the *_server models segfault in paddlepaddle 3.0 CPU conv on
            # WSL; the mobile models are accurate on 300-DPI scans and stable
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="PP-OCRv5_mobile_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            # paddlepaddle 3.x CPU crashes in oneDNN PIR conversion on some
            # ops; plain CPU inference avoids it
            enable_mkldnn=False,
        )
    return _ocr_engine


def ocr_image(image_path):
    """OCR one page image; returns (text, low-confidence lines)."""
    processed = preprocess_image(image_path)
    ocr = get_ocr_engine()

    lines = []
    for res in ocr.predict(processed):
        texts = res.get("rec_texts", [])
        scores = res.get("rec_scores", [])
        for text, conf in zip(texts, scores):
            lines.append((text, float(conf)))

    flagged = [(t, c) for t, c in lines if c < OCR_CONFIDENCE_THRESHOLD]
    text = "\n".join(t for t, _ in lines)
    return text, flagged


def extract_pdf_text(pdf_path):
    """PDF -> images -> OCR text; the images are deleted afterwards."""
    img_dir = tempfile.mkdtemp(prefix="pii_pages_")
    try:
        image_paths = pdf_to_images(pdf_path, img_dir)
        page_texts, all_flagged = [], []
        for n, img_path in enumerate(image_paths, start=1):
            text, flagged = ocr_image(img_path)
            page_texts.append(text)
            all_flagged.extend(flagged)
            print(f"  -> page {n} OCR done "
                  f"({len(text.splitlines())} lines, {len(flagged)} low-confidence)")
    finally:
        shutil.rmtree(img_dir, ignore_errors=True)  # images deleted, text kept
    return "\n\n".join(page_texts), all_flagged


# ---------- 3. PII DETECTION (Presidio + spaCy transformer NER) ----------

def build_custom_recognizers():
    from presidio_analyzer import Pattern, PatternRecognizer

    # Presidio compiles patterns with IGNORECASE by default; patterns that
    # rely on capitalisation (org codes, name shapes) must opt out.
    CASE_SENSITIVE = re.DOTALL | re.MULTILINE

    recognizers = []

    recognizers.append(PatternRecognizer(
        supported_entity="PH_TIN",
        patterns=[Pattern("ph_tin", r"\b\d{3}-\d{3}-\d{3}-\d{3}\b", 0.85)],
        context=["tin", "tax", "identification"],
    ))

    recognizers.append(PatternRecognizer(
        supported_entity="REFERENCE_NUMBER",
        patterns=[Pattern(
            "reference",
            r"\b(?:No\.?|RNO\.?|Ref\.?)\s?[A-Z0-9]{2,}[-/][A-Z0-9]{1,}\b", 0.6)],
    ))

    recognizers.append(PatternRecognizer(
        supported_entity="MONEY_AMOUNT",
        patterns=[Pattern(
            "money_symbol",
            r"(?:[₱$€£]|(?:Rs|INR|P)\.?\s?)\s?\d[\d,]*(?:\.\d{1,2})?", 0.7)],
    ))

    # Amounts written out in words: "ONE MILLION FOUR HUNDRED ... PESOS"
    number_word = (r"(?:one|two|three|four|five|six|seven|eight|nine|ten|"
                   r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
                   r"seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|"
                   r"sixty|seventy|eighty|ninety|hundred|thousand|million|"
                   r"billion|lakh|crore|and)")
    recognizers.append(PatternRecognizer(
        supported_entity="MONEY_WORDS",
        patterns=[Pattern(
            "money_words",
            rf"\b(?:{number_word}[\s\-]+){{2,}}"
            r"(?:pesos?|dollars?|rupees?|euros?|pounds?)\b", 0.6)],
        name="MoneyWordsRecognizer",
    ))

    # Hyphenated all-caps organisation codes: "BIR-RR9A-CABAMIRO".
    # Must contain a digit so hyphenated words ("in-charge") never match.
    recognizers.append(PatternRecognizer(
        supported_entity="ORG_CODE",
        patterns=[Pattern(
            "org_code",
            r"\b(?=[A-Z0-9-]{5,}\d)[A-Z]{2,6}(?:-[A-Z0-9]{2,12}){1,3}\b",
            0.6)],
        global_regex_flags=CASE_SENSITIVE,
    ))

    # Notary/attorney registration numbers, OCR-tolerant: "Doc. No.70",
    # "ROLLVO.4595" (Roll No.), "PTRN0.60C0577...", "18PN0.05502" (IBP No.),
    # "MCLEC0MPL.N0.V-U0109H5", "APP0INTMENT N0.2017-020".
    recognizers.append(PatternRecognizer(
        supported_entity="REFERENCE_NUMBER",
        patterns=[Pattern(
            "notary_id",
            r"\b(?:doc|page|book|roll|ptr|ibp|tin|serial|series|mcle\w*|"
            r"app\w{0,3}ntment\w*|\d{1,2}p)"
            r"\s*\.?\s*[nv][o0]?\s*[.,]?\s*[\w/;-]*[0-9ivxlc][\w/;-]*", 0.6)],
        name="NotaryIdRecognizer",
    ))

    # Organisational units with designators: "Revenue Region 9A"
    recognizers.append(PatternRecognizer(
        supported_entity="ORG_UNIT",
        patterns=[Pattern(
            "org_unit",
            r"\b(?:Revenue\s+)?(?:Region|District|Division|Zone|Branch)\s+"
            r"(?:No\.?\s*)?\d{1,3}\s?[A-Z]?\b", 0.5)],
    ))

    # Contract-style dates: "5th day of JUNE ; 2017" (OCR underscores too)
    recognizers.append(PatternRecognizer(
        supported_entity="DATE_TIME",
        patterns=[Pattern(
            "day_of_date",
            r"\b\d{1,2}(?:st|nd|rd|th)?[_\s]+day[_\s]+of[_\s]+"
            r"[A-Za-z]+[_\s;,]*\d{4}\b", 0.6)],
        name="DayOfDateRecognizer",
    ))

    # Signature blocks list names as "BALDWIN P. SEE" — NER regularly
    # misses these in noisy all-caps context, so match the shape directly.
    # Second pattern handles OCR-merged initials ("ANTONIOC. SUANG",
    # "MA.GRACIAB.JAVIER"); both take an optional honorific ("MA.").
    honorific = r"(?:\b(?:MA|MR|MRS|MS|DR|ATTY)[.,]\s*)?"
    recognizers.append(PatternRecognizer(
        supported_entity="PERSON",
        patterns=[
            Pattern("name_with_initial",
                    honorific + r"\b[A-Z][a-zA-Z]{2,}\s+[A-Z][.,]\s*"
                    r"[A-Z][a-zA-Z]+\b", 0.55),
            Pattern("name_merged_initial",
                    honorific + r"[A-Z][A-Za-z]{2,}[A-Z][.,]\s*"
                    r"[A-Z][a-zA-Z]+\b", 0.55),
        ],
        name="NameWithInitialRecognizer",
        global_regex_flags=CASE_SENSITIVE,
    ))

    # Street addresses: NER often tags only the city, not the street line.
    recognizers.append(PatternRecognizer(
        supported_entity="LOCATION",
        patterns=[Pattern(
            "street_address",
            r"\b\d{1,5}(?:[/-]\d{1,4})?\s+(?:[A-Z][A-Za-z.']*\s+){0,4}"
            r"(?:Street|Road|Avenue|Lane|Marg|Nagar|Boulevard|Drive|"
            r"Highway|St|Rd|Ave|Ln|Blvd|Hwy)\b\.?", 0.5)],
        name="StreetAddressRecognizer",
    ))

    return recognizers


_analyzer = None


def get_analyzer():
    global _analyzer
    if _analyzer is None:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        nlp_engine = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": SPACY_MODEL}],
            # Presidio ignores ORG by default; organisation names are
            # confidential in contracts, so map the label set explicitly.
            "ner_model_configuration": {
                "model_to_presidio_entity_mapping": {
                    "PERSON": "PERSON", "PER": "PERSON",
                    "FAC": "LOCATION",
                    "ORG": "ORGANIZATION",
                    "DATE": "DATE_TIME", "TIME": "DATE_TIME",
                    "NORP": "NRP",
                },
                "low_confidence_score_multiplier": 0.4,
                "low_score_entity_names": [],
                "labels_to_ignore": ["CARDINAL", "ORDINAL", "QUANTITY",
                                     "PERCENT", "MONEY", "LANGUAGE", "LAW",
                                     "EVENT", "PRODUCT", "WORK_OF_ART",
                                     "GPE", "LOC"],
            },
        }).create_engine()

        _analyzer = AnalyzerEngine(nlp_engine=nlp_engine,
                                   supported_languages=["en"])
        for rec in build_custom_recognizers():
            _analyzer.registry.add_recognizer(rec)
    return _analyzer


_ALLCAPS_WORD = re.compile(r"\b[A-Z]{2,}\b")


def _title_allcaps(text):
    """NER is weak on ALL-CAPS headers; analyse a case-normalised copy too
    (offsets stay identical because only letter case changes)."""
    return _ALLCAPS_WORD.sub(lambda m: m.group().capitalize(), text)


def detect_pii(text):
    """Return all detections, position-ordered.

    Overlapping spans are deliberately kept: replacement is value-based
    (every occurrence of every detected value is swept), so a span that
    overlaps a longer one still contributes its value to the mapping —
    dropping it would leave the non-overlapping part of that value behind.
    """
    analyzer = get_analyzer()
    raw = analyzer.analyze(text=text, entities=ENTITIES_TO_REDACT,
                           language="en", score_threshold=DETECTION_THRESHOLD)
    titled = _title_allcaps(text)
    if titled != text:
        raw += analyzer.analyze(text=titled, entities=ENTITIES_TO_REDACT,
                                language="en",
                                score_threshold=DETECTION_THRESHOLD)
    raw.sort(key=lambda r: (r.start, -(r.end - r.start)))
    return raw


# ---------- 4. UNIQUE PII -> UNIQUE BOGUS (mapping CSV) ----------

_UNITS = ["", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN",
          "EIGHT", "NINE", "TEN", "ELEVEN", "TWELVE", "THIRTEEN",
          "FOURTEEN", "FIFTEEN", "SIXTEEN", "SEVENTEEN", "EIGHTEEN",
          "NINETEEN"]
_TENS = ["", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY",
         "EIGHTY", "NINETY"]


def _int_to_words(n):
    if n == 0:
        return "ZERO"
    parts = []
    for divisor, label in ((10**6, "MILLION"), (10**3, "THOUSAND"), (1, "")):
        chunk, n = divmod(n, divisor)
        if not chunk:
            continue
        words = []
        h, rest = divmod(chunk, 100)
        if h:
            words += [_UNITS[h], "HUNDRED"]
        if rest < 20:
            if rest:
                words.append(_UNITS[rest])
        else:
            t, u = divmod(rest, 10)
            words.append(_TENS[t] + (f" {_UNITS[u]}" if u else ""))
        if label:
            words.append(label)
        parts.append(" ".join(words))
    return " ".join(parts)


def _normalize(value):
    """Canonical form used to deduplicate PII values."""
    return re.sub(r"\s+", " ", value).strip().casefold()


def _clean_value(value):
    """Strip OCR junk from the edges of a detected span."""
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip("_-–—:;,.| ").strip()


def _org_acronym(name):
    """'Bureau of Internal Revenue' -> 'BIR' (needs >= 3 letters)."""
    words = [w for w in re.split(r"[^A-Za-z]+", name)
             if w and w.lower() not in ACRONYM_SKIP_WORDS]
    if len(words) < 3:
        return None
    return "".join(w[0].upper() for w in words)


class BogusFactory:
    """One unique bogus value per unique real value, per entity type."""

    def __init__(self):
        from faker import Faker
        Faker.seed(FAKER_SEED)
        self._fake = Faker("en_PH")
        self._used = set()
        self._counters = {}

    def _unique(self, gen):
        for _ in range(50):
            candidate = str(gen())
            if candidate.casefold() not in self._used:
                return candidate
        raise RuntimeError("could not generate a unique bogus value")

    def make(self, entity_type, original):
        n = self._counters.get(entity_type, 0) + 1
        self._counters[entity_type] = n
        bogus = f"{entity_type}_{n:03d}"
        
        if entity_type == "DATE_TIME":
            m = re.search(r"\b((?:19|20)\d{2})\b", original)
            if m:
                bogus = f"{bogus} {m.group(1)}"
                
        self._used.add(bogus.casefold())
        return bogus


def build_mapping(text, detections):
    """unique real value -> (entity_type, bogus). Also derives org
    acronyms and name-fragment entries so residual mentions are caught."""
    factory = BogusFactory()
    mapping = {}          # normalized real -> dict(type, original, bogus, kind)

    def add(entity_type, original, bogus=None, kind="detected", cs=False):
        original = _clean_value(original)
        key = _normalize(original)
        if not key or len(key) < 3 or key in GENERIC_VALUES or key in mapping:
            return None
        entry = {
            "type": entity_type, "original": original,
            "bogus": bogus if bogus is not None
            else factory.make(entity_type, original),
            "kind": kind, "cs": cs,
        }
        # A bogus value must never contain any real value.
        while any(k in entry["bogus"].casefold() for k in mapping):
            entry["bogus"] = factory.make(entity_type, original)
        mapping[key] = entry
        return entry

    for res in detections:
        add(res.entity_type, text[res.start:res.end])

    # Derived entries: acronyms, two-word prefixes, distinctive single
    # tokens — so "BIR", "Stanley Bradley" or a bare surname never leak.
    for entry in list(mapping.values()):
        etype, original, bogus = entry["type"], entry["original"], entry["bogus"]
        if etype == "ORGANIZATION":
            acronym = _org_acronym(original)
            if acronym and re.search(rf"\b{re.escape(acronym)}\b", text):
                bogus_acronym = _org_acronym(bogus) or acronym[::-1]
                add("ORGANIZATION", acronym, bogus_acronym, kind="acronym")
        if etype in ("ORGANIZATION", "PERSON", "LOCATION"):
            words = [w for w in re.split(r"[^A-Za-z]+", original) if w]
            if len(words) >= 3:
                prefix = " ".join(words[:2])
                if all(w.lower() not in FRAGMENT_STOPWORDS for w in words[:2]):
                    add(etype, prefix, bogus, kind="fragment")
            for w in words:
                if len(w) < 3 or w.lower() in FRAGMENT_STOPWORDS:
                    continue
                if len(w) >= 4:
                    add(etype, w, bogus, kind="fragment")
                elif etype == "PERSON" and w[0].isupper():
                    # 3-letter surnames ("SEE") are swept case-sensitively
                    # so ordinary prose words are never touched
                    add(etype, w, bogus, kind="fragment", cs=True)

    return list(mapping.values())


def save_mapping_csv(mapping, csv_path):
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["entity_type", "original", "bogus", "kind"])
        for entry in sorted(mapping, key=lambda e: (e["type"], e["original"])):
            writer.writerow([entry["type"], entry["original"],
                             entry["bogus"], entry["kind"]])


# ---------- 5. REPLACE PII WITH BOGUS ----------

def _value_pattern(entry_or_value):
    """Regex for one value, tolerant of line breaks/extra spaces inside it.

    Case-sensitive entries (3-letter surnames) match only their UPPER and
    Title forms so ordinary prose words are never replaced."""
    if isinstance(entry_or_value, dict):
        value, cs = entry_or_value["original"], entry_or_value.get("cs")
    else:
        value, cs = entry_or_value, False
    if cs:
        forms = {value.upper(), value.capitalize()}
        return r"(?<![\w])(?:" + "|".join(re.escape(f) for f in forms) + r")(?![\w])"
    parts = [re.escape(p) for p in value.split(" ")]
    return r"(?<![\w])" + r"[\s_]+".join(parts) + r"(?![\w])"


def _entry_flags(entry):
    return 0 if entry.get("cs") else re.IGNORECASE


def apply_mapping(text, mapping):
    lookup = {_normalize(e["original"]): e for e in mapping}
    ordered = sorted(mapping, key=lambda e: -len(e["original"]))
    counts = {}

    def substitute(entries, text, flags):
        if not entries:
            return text
        big = re.compile("|".join(f"(?:{_value_pattern(e)})"
                                  for e in entries), flags)

        def repl(m):
            entry = lookup.get(_normalize(m.group(0)))
            if entry is None:  # matched a line-break variant; find by pattern
                for e in entries:
                    if re.fullmatch(_value_pattern(e), m.group(0), flags):
                        entry = e
                        break
            if entry is None:
                return m.group(0)
            counts[entry["type"]] = counts.get(entry["type"], 0) + 1
            bogus = entry["bogus"]
            return bogus.upper() if m.group(0).isupper() else bogus

        return big.sub(repl, text)

    text = substitute([e for e in ordered if not e.get("cs")], text,
                      re.IGNORECASE)
    text = substitute([e for e in ordered if e.get("cs")], text, 0)
    return text, counts


def verify_no_leaks(sanitized, mapping):
    """Belt and braces: assert no original value survived replacement."""
    leaks = []
    for entry in mapping:
        if len(entry["original"]) < 4 and not entry.get("cs"):
            continue
        if re.search(_value_pattern(entry), sanitized, _entry_flags(entry)):
            leaks.append(entry)
    for entry in leaks:
        sanitized = re.sub(_value_pattern(entry), entry["bogus"],
                           sanitized, flags=_entry_flags(entry))
    return sanitized, leaks


# ---------- RESTORE (remap bogus -> original) ----------

def restore(sanitized_path, csv_path):
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("kind") != "fragment"]
    with open(sanitized_path, encoding="utf-8") as fh:
        text = fh.read()
    for row in sorted(rows, key=lambda r: -len(r["bogus"])):
        text = re.sub(re.escape(row["bogus"]), row["original"].replace("\\", r"\\"),
                      text, flags=re.IGNORECASE)
    out_path = re.sub(r"\.sanitized(\.txt)$", r".restored\1", sanitized_path)
    if out_path == sanitized_path:
        out_path = sanitized_path + ".restored.txt"
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"Restored text saved to: {out_path}")


# ---------- MAIN ----------

def process_pdf(input_path, out_dir):
    base = os.path.splitext(os.path.basename(input_path))[0]
    os.makedirs(out_dir, exist_ok=True)

    print(f"\n=== {os.path.basename(input_path)} ===")
    print("[1/4] PDF -> images -> OCR text (images deleted afterwards)")
    if input_path.lower().endswith(".pdf"):
        text, flagged = extract_pdf_text(input_path)
    else:
        text, flagged = ocr_image(input_path)

    extracted_path = os.path.join(out_dir, f"{base}.extracted.txt")
    with open(extracted_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"  -> extracted text: {extracted_path}")
    if flagged:
        print(f"  LOW-CONFIDENCE OCR LINES ({len(flagged)}) — review manually:")
        for t, c in flagged:
            print(f"    [{c:.2f}] {t}")

    print("[2/4] Identifying PII")
    detections = detect_pii(text)
    print(f"  -> {len(detections)} PII spans detected")

    print("[3/4] Assigning unique bogus values, writing mapping CSV")
    mapping = build_mapping(text, detections)
    csv_path = os.path.join(out_dir, f"{base}.mapping.csv")
    save_mapping_csv(mapping, csv_path)
    print(f"  -> {len(mapping)} unique PII values mapped: {csv_path}")

    print("[4/4] Replacing PII with bogus values")
    sanitized, counts = apply_mapping(text, mapping)
    sanitized, leaks = verify_no_leaks(sanitized, mapping)
    if leaks:
        print(f"  [self-check] force-replaced {len(leaks)} residual value(s)")
    else:
        print("  [self-check] no mapped value survives in the sanitized text")

    sanitized_path = os.path.join(out_dir, f"{base}.sanitized.txt")
    with open(sanitized_path, "w", encoding="utf-8") as fh:
        fh.write(sanitized)

    print(f"  -> sanitized text: {sanitized_path}")
    print("  Replacements by type: "
          + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("input", help="PDF/image file or a folder of them; "
                        "with --restore: the sanitized .txt")
    parser.add_argument("mapping_csv", nargs="?",
                        help="mapping CSV (only with --restore)")
    parser.add_argument("-o", "--out", default="output",
                        help="output directory (default: ./output)")
    parser.add_argument("--restore", action="store_true",
                        help="put original values back using the mapping CSV")
    args = parser.parse_args()

    if args.restore:
        if not args.mapping_csv:
            parser.error("--restore needs: sanitized.txt mapping.csv")
        restore(args.input, args.mapping_csv)
        return

    if os.path.isdir(args.input):
        exts = (".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif")
        files = sorted(os.path.join(args.input, f)
                       for f in os.listdir(args.input)
                       if f.lower().endswith(exts))
        if not files:
            sys.exit(f"No PDFs/images found in {args.input}")
        for path in files:
            try:
                process_pdf(path, args.out)
            except Exception as exc:
                print(f"FAILED on {path}: {exc}")
    else:
        if not os.path.exists(args.input):
            sys.exit(f"Input not found: {args.input}")
        process_pdf(args.input, args.out)

    print("\nDone.")


if __name__ == "__main__":
    main()
