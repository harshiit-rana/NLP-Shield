"""
PII redaction pipeline — reads OCR text output, detects and redacts
confidential entities, saves a redacted version safe to send to an AI.

Fully local: no network calls, no data leaves this machine.

Usage:
  python redact_pii.py ocr_output/1_page001.txt
  python redact_pii.py ocr_output/          # process whole folder
"""

import sys
import os
import re
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# ---------- CONFIG ----------

REPLACEMENT_TOKEN = "XXXX"   # what redacted spans get replaced with
CONFIDENCE_THRESHOLD = 0.4    # Presidio's own detection confidence

# Entity types to redact — trim/expand this list based on what you consider confidential
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
    "DATE_TIME",       # optional — dates can sometimes be sensitive in contracts (signing dates etc.)
]

# ---------- CUSTOM RECOGNIZERS (for contract/legal-specific patterns) ----------
# Generic NER models miss domain-specific identifiers like tax numbers,
# contract reference numbers, and monetary amounts written in words.
# Add patterns here for anything specific to your document types.

def build_custom_recognizers():
    recognizers = []

    # Philippine TIN (Tax Identification Number) — format: 000-000-000-000
    tin_pattern = Pattern(name="ph_tin_pattern", regex=r"\b\d{3}-\d{3}-\d{3}-\d{3}\b", score=0.85)
    tin_recognizer = PatternRecognizer(
        supported_entity="PH_TIN",
        patterns=[tin_pattern],
        context=["TIN", "tax identification"]
    )
    recognizers.append(tin_recognizer)

    # Generic contract/reference/case numbers — e.g. "RNO.6071/", "2017-02", "Public Bidding No. 2017-02"
    ref_pattern = Pattern(
        name="reference_number_pattern",
        regex=r"\b(?:No\.?|RNO\.?|Ref\.?)\s?[A-Z0-9]{2,}[-/][A-Z0-9]{1,}\b",
        score=0.6
    )
    ref_recognizer = PatternRecognizer(
        supported_entity="REFERENCE_NUMBER",
        patterns=[ref_pattern]
    )
    recognizers.append(ref_recognizer)

    # Monetary amounts (numeric with currency symbols/commas) — e.g. "P 1,495,000"
    money_pattern = Pattern(
        name="money_pattern",
        regex=r"[₱P$]\s?[\d,]+(?:\.\d{2})?",
        score=0.7
    )
    money_recognizer = PatternRecognizer(
        supported_entity="MONEY_AMOUNT",
        patterns=[money_pattern]
    )
    recognizers.append(money_recognizer)

    return recognizers


# ---------- ENGINE SETUP ----------

_analyzer = None
_anonymizer = None

def get_engines():
    global _analyzer, _anonymizer
    if _analyzer is None:
        _analyzer = AnalyzerEngine()
        for recognizer in build_custom_recognizers():
            _analyzer.registry.add_recognizer(recognizer)
    if _anonymizer is None:
        _anonymizer = AnonymizerEngine()
    return _analyzer, _anonymizer


def redact_text(text):
    analyzer, anonymizer = get_engines()

    all_entities = ENTITIES_TO_REDACT + ["PH_TIN", "REFERENCE_NUMBER", "MONEY_AMOUNT"]

    results = analyzer.analyze(
        text=text,
        entities=all_entities,
        language="en",
        score_threshold=CONFIDENCE_THRESHOLD
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


# ---------- MAIN ----------

def process_file(txt_path, out_dir):
    with open(txt_path, "r", encoding="utf-8") as f:
        text = f.read()

    redacted_text, detections = redact_text(text)

    base = os.path.splitext(os.path.basename(txt_path))[0]
    out_path = os.path.join(out_dir, f"{base}_redacted.txt")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(redacted_text)

    print(f"Processed: {txt_path}")
    print(f"  -> {len(detections)} entities redacted")
    for d in detections:
        original_span = text[d.start:d.end]
        print(f"    [{d.entity_type}] '{original_span}' (confidence: {d.score:.2f})")
    print(f"  -> Saved: {out_path}\n")


def main():
    if len(sys.argv) < 2:
        print("Usage: python redact_pii.py <txt_file_or_folder>")
        sys.exit(1)

    input_path = sys.argv[1]
    out_dir = "redacted_output"
    os.makedirs(out_dir, exist_ok=True)

    if os.path.isdir(input_path):
        txt_files = [os.path.join(input_path, f) for f in os.listdir(input_path)
                     if f.lower().endswith(".txt")]
        for txt_path in txt_files:
            process_file(txt_path, out_dir)
    else:
        process_file(input_path, out_dir)

    print(f"Done. Redacted files saved in ./{out_dir}/")


if __name__ == "__main__":
    main()