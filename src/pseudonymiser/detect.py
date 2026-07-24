"""PII detection built on Microsoft Presidio.

Layers, all fully local:

1. Presidio's built-in recognisers (spaCy NER via a configurable model,
   default ``en_core_web_trf``) including the India-specific recognisers
   (IN_PAN, IN_AADHAAR, IN_PASSPORT, IN_VOTER, IN_VEHICLE_REGISTRATION).
2. Custom pattern recognisers with context words: IFSC codes, UPI IDs,
   Indian mobile numbers, PIN codes, bank account numbers, PH TIN,
   contract reference numbers, and monetary amounts.
3. An OCR-tolerance pass that repairs common character confusions
   (O<->0, I/l<->1, S<->5, B<->8) inside candidate spans and validates the
   result with Verhoeff (Aadhaar) / Luhn (cards) / PAN structure, so
   identifiers mangled by OCR are still caught while random strings are
   rejected by checksum.
4. Optionally, GLiNER (``urchade/gliner_multi_pii-v1``) as an extra
   recogniser whose results are unioned in (``detection.use_gliner``).
"""

from __future__ import annotations

import logging
import re

from presidio_analyzer import (
    AnalyzerEngine,
    EntityRecognizer,
    Pattern,
    PatternRecognizer,
    RecognizerResult,
)
from presidio_analyzer.nlp_engine import NlpArtifacts, NlpEngineProvider

from .config import DetectionConfig
from .validators import (
    luhn_valid,
    normalize_digits,
    normalize_pan,
    pan_structurally_valid,
    verhoeff_valid,
)

logger = logging.getLogger(__name__)


class DetectionError(Exception):
    """Raised when the analyzer cannot be constructed or run."""


# ---------------------------------------------------------------------------
# Custom pattern recognisers
# ---------------------------------------------------------------------------

def build_custom_recognizers() -> list[PatternRecognizer]:
    """Pattern recognisers for identifiers Presidio has no builtin for."""
    recognizers: list[PatternRecognizer] = []

    recognizers.append(PatternRecognizer(
        supported_entity="IN_IFSC",
        patterns=[Pattern("ifsc", r"\b[A-Z]{4}0[A-Z0-9]{6}\b", 0.55)],
        context=["ifsc", "bank", "branch", "rtgs", "neft", "imps"],
    ))

    # UPI VPAs look like emails but have no dot-TLD after the handle.
    recognizers.append(PatternRecognizer(
        supported_entity="IN_UPI",
        patterns=[Pattern("upi", r"\b[a-zA-Z0-9][a-zA-Z0-9.\-_]{1,}@[a-zA-Z]{2,64}\b(?!\.[a-zA-Z])", 0.35)],
        context=["upi", "vpa", "pay", "gpay", "phonepe", "paytm", "bhim"],
    ))

    recognizers.append(PatternRecognizer(
        supported_entity="PHONE_NUMBER",
        patterns=[
            Pattern("in_mobile_cc", r"(?<![\d\w])\+91[\-\s]?[6-9]\d{4}[\-\s]?\d{5}(?!\d)", 0.6),
            Pattern("in_mobile", r"(?<![\d\-+])[6-9]\d{4}[\s\-]?\d{5}(?![\d])", 0.4),
        ],
        context=["phone", "mobile", "contact", "call", "cell", "whatsapp", "tel"],
        name="InMobileRecognizer",
    ))

    # 6-digit PIN codes: bare 6-digit numbers are too common to redact
    # blindly, so the generic pattern has a low base score that context
    # words must boost. Indian addresses, however, put the PIN directly
    # after the place name ("Mumbai 400001", "Jaipur-302016"); those
    # positional forms score above the threshold on their own.
    recognizers.append(PatternRecognizer(
        supported_entity="IN_PIN_CODE",
        patterns=[
            Pattern("pin", r"\b[1-9]\d{5}\b", 0.05),
            Pattern("pin_after_place", r"(?<=[A-Za-z][\s\-])[1-9]\d{5}\b", 0.4),
        ],
        context=["pin", "pincode", "postal", "zip", "address", "district"],
    ))

    # Bank account numbers: 9-18 digits, context-driven for the same reason.
    recognizers.append(PatternRecognizer(
        supported_entity="BANK_ACCOUNT",
        patterns=[Pattern("bank_account", r"\b\d{9,18}\b", 0.05)],
        context=["account", "a/c", "acct", "savings", "current", "acc"],
    ))

    # presidio 2.2.x ships no IN_VEHICLE_REGISTRATION recognizer; standard
    # plate format: state (2 letters) + district (1-2 digits) + series
    # (1-3 letters) + number (4 digits).
    recognizers.append(PatternRecognizer(
        supported_entity="IN_VEHICLE_REGISTRATION",
        patterns=[Pattern(
            "in_vehicle",
            r"\b(?:AP|AR|AS|BR|CG|CH|DD|DL|GA|GJ|HP|HR|JH|JK|KA|KL|LA|LD|MH|"
            r"ML|MN|MP|MZ|NL|OD|PB|PY|RJ|SK|TN|TR|TS|UK|UP|WB)"
            r"[\s\-]?\d{1,2}[\s\-]?[A-Z]{1,3}[\s\-]?\d{4}\b", 0.5)],
        context=["vehicle", "registration", "car", "plate", "rc"],
    ))

    # Indian corporate identifiers common in contracts/MSAs/NDAs.
    # GSTIN: 2-digit state code + embedded PAN + entity code + 'Z' + check char.
    recognizers.append(PatternRecognizer(
        supported_entity="IN_GSTIN",
        patterns=[Pattern("gstin", r"\b\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]\b", 0.6)],
        context=["gstin", "gst", "tax"],
    ))

    # CIN: L/U + 5-digit industry code + state + year + ownership + number.
    recognizers.append(PatternRecognizer(
        supported_entity="IN_CIN",
        patterns=[Pattern("cin", r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b", 0.6)],
        context=["cin", "corporate", "identity", "incorporation", "registration"],
    ))

    recognizers.append(PatternRecognizer(
        supported_entity="PH_TIN",
        patterns=[Pattern("ph_tin", r"\b\d{3}-\d{3}-\d{3}-\d{3}\b", 0.85)],
        context=["tin", "tax", "identification"],
    ))

    recognizers.append(PatternRecognizer(
        supported_entity="REFERENCE_NUMBER",
        patterns=[Pattern("reference", r"\b(?:No\.?|RNO\.?|Ref\.?)\s?[A-Z0-9]{2,}[-/][A-Z0-9]{1,}\b", 0.6)],
    ))

    recognizers.append(PatternRecognizer(
        supported_entity="MONEY_AMOUNT",
        patterns=[
            # at least one digit required — "[\d,]+" alone also matched a
            # bare comma after a stray "P"
            Pattern("money_symbol",
                    r"(?:[₱$€£]|(?:Rs|INR|P)\.?\s?)\s?\d[\d,]*(?:\.\d{1,2})?", 0.7),
        ],
    ))

    # Street addresses: NER often tags only the city, leaving the street
    # line ("1031 A. Mabini Street") untouched.
    recognizers.append(PatternRecognizer(
        supported_entity="LOCATION",
        name="StreetAddressRecognizer",
        patterns=[Pattern(
            "street_address",
            r"\b\d{1,5}(?:[/-]\d{1,4})?\s+(?:[A-Z][A-Za-z.']*\s+){0,4}"
            r"(?:Street|Road|Avenue|Lane|Marg|Nagar|Chowk|Boulevard|"
            r"Drive|Highway|St|Rd|Ave|Ln|Blvd|Hwy)\b\.?", 0.5)],
        context=["address", "residing", "located", "office", "at"],
    ))

    return recognizers


# ---------------------------------------------------------------------------
# OCR-tolerant structured-identifier recogniser
# ---------------------------------------------------------------------------

# Character classes that include the usual OCR lookalikes.
_D = r"[0-9OoQDIliSsB|Zz]"        # a "digit, possibly misread as a letter"
_A = r"[A-Z0158Z]"                # an "uppercase letter, possibly misread as a digit"

_AADHAAR_CAND = re.compile(rf"(?<![\w]){_D}{{4}}[\s\-]?{_D}{{4}}[\s\-]?{_D}{{4}}(?![\w])")
_CARD_CAND = re.compile(rf"(?<![\w]){_D}{{4}}(?:[\s\-]?{_D}{{4}}){{2,3}}(?:[\s\-]?{_D}{{1,3}})?(?![\w])")
_PAN_CAND = re.compile(rf"(?<![\w]){_A}{{5}}{_D}{{4}}{_A}(?![\w])")


class OcrTolerantRecognizer(EntityRecognizer):
    """Find structured identifiers even when OCR has garbled characters.

    Candidates are located with confusion-tolerant regexes, repaired with
    the confusion tables, then accepted only if they pass the relevant
    checksum/structure validation — so this adds recall without adding
    false positives.
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["IN_AADHAAR", "CREDIT_CARD", "IN_PAN"],
            name="OcrTolerantRecognizer",
        )

    def load(self) -> None:  # nothing to load
        return

    def analyze(self, text: str, entities: list[str],
                nlp_artifacts: NlpArtifacts | None = None) -> list[RecognizerResult]:
        results: list[RecognizerResult] = []

        if "IN_AADHAAR" in entities:
            for m in _AADHAAR_CAND.finditer(text):
                digits = normalize_digits(m.group())
                if len(digits) == 12 and digits[0] in "23456789" and verhoeff_valid(digits):
                    results.append(RecognizerResult("IN_AADHAAR", m.start(), m.end(), 0.9))

        if "CREDIT_CARD" in entities:
            for m in _CARD_CAND.finditer(text):
                digits = normalize_digits(m.group())
                if 13 <= len(digits) <= 19 and luhn_valid(digits):
                    results.append(RecognizerResult("CREDIT_CARD", m.start(), m.end(), 0.9))

        if "IN_PAN" in entities:
            for m in _PAN_CAND.finditer(text):
                pan = normalize_pan(m.group())
                if pan and pan_structurally_valid(pan):
                    results.append(RecognizerResult("IN_PAN", m.start(), m.end(), 0.85))

        return results


# ---------------------------------------------------------------------------
# Optional GLiNER recogniser
# ---------------------------------------------------------------------------

_GLINER_LABEL_MAP = {
    "person": "PERSON",
    "organization": "ORGANIZATION",
    "address": "LOCATION",
    "location": "LOCATION",
    "email": "EMAIL_ADDRESS",
    "phone number": "PHONE_NUMBER",
    "date of birth": "DATE_TIME",
    "credit card number": "CREDIT_CARD",
    "bank account number": "BANK_ACCOUNT",
    "passport number": "IN_PASSPORT",
    "identity card number": "IN_AADHAAR",
    "tax identification number": "IN_PAN",
}


class GlinerRecognizer(EntityRecognizer):
    """GLiNER-based PII recogniser (fully local, optional dependency)."""

    def __init__(self, model_name: str, threshold: float = 0.4) -> None:
        self._model_name = model_name
        self._threshold = threshold
        self._model = None
        super().__init__(
            supported_entities=sorted(set(_GLINER_LABEL_MAP.values())),
            name="GlinerRecognizer",
        )

    def load(self) -> None:
        try:
            from gliner import GLiNER
        except ImportError as exc:
            raise DetectionError(
                "detection.use_gliner is enabled but the 'gliner' package is "
                "not installed. Run 'make download-models' with GLINER=1 or "
                "disable the flag."
            ) from exc
        # local_files_only-friendly: the model must already be in the HF cache
        self._model = GLiNER.from_pretrained(self._model_name)
        logger.info("GLiNER model %s loaded", self._model_name)

    def analyze(self, text: str, entities: list[str],
                nlp_artifacts: NlpArtifacts | None = None) -> list[RecognizerResult]:
        if self._model is None:
            self.load()
        results: list[RecognizerResult] = []
        labels = list(_GLINER_LABEL_MAP.keys())
        # GLiNER has a limited context window; analyse in overlapping chunks.
        chunk, overlap = 2000, 200
        for offset in range(0, max(len(text), 1), chunk - overlap):
            piece = text[offset:offset + chunk]
            if not piece.strip():
                continue
            for ent in self._model.predict_entities(piece, labels, threshold=self._threshold):
                mapped = _GLINER_LABEL_MAP.get(ent["label"])
                if mapped and mapped in entities:
                    results.append(RecognizerResult(
                        mapped, offset + ent["start"], offset + ent["end"],
                        float(ent["score"]),
                    ))
        return results


# ---------------------------------------------------------------------------
# Analyzer construction and analysis
# ---------------------------------------------------------------------------

def build_analyzer(cfg: DetectionConfig) -> AnalyzerEngine:
    """Construct the Presidio AnalyzerEngine per configuration."""
    try:
        nlp_engine = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": cfg.spacy_model}],
            # Presidio ignores ORG by default; for legal documents
            # (contracts, MSAs, NDAs) organisation names are confidential,
            # so map the full OntoNotes label set explicitly.
            "ner_model_configuration": {
                "model_to_presidio_entity_mapping": {
                    "PERSON": "PERSON", "PER": "PERSON",
                    "GPE": "LOCATION", "LOC": "LOCATION", "FAC": "LOCATION",
                    "ORG": "ORGANIZATION",
                    "DATE": "DATE_TIME", "TIME": "DATE_TIME",
                    "NORP": "NRP",
                },
                "low_confidence_score_multiplier": 0.4,
                "low_score_entity_names": [],
                "labels_to_ignore": ["CARDINAL", "ORDINAL", "QUANTITY",
                                     "PERCENT", "MONEY", "LANGUAGE", "LAW",
                                     "EVENT", "PRODUCT", "WORK_OF_ART"],
            },
        }).create_engine()
    except OSError as exc:
        raise DetectionError(
            f"spaCy model {cfg.spacy_model!r} is not installed. "
            f"Run 'make download-models' first."
        ) from exc

    analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])

    for recognizer in build_custom_recognizers():
        analyzer.registry.add_recognizer(recognizer)
    analyzer.registry.add_recognizer(OcrTolerantRecognizer())
    if cfg.use_gliner:
        analyzer.registry.add_recognizer(
            GlinerRecognizer(cfg.gliner_model, cfg.gliner_threshold)
        )
    return analyzer


def analyze_text(analyzer: AnalyzerEngine, text: str,
                 cfg: DetectionConfig) -> list[RecognizerResult]:
    """Run detection and return non-overlapping results, best-span-first.

    Overlapping detections are resolved by keeping the longer span (ties:
    higher score) so replacements never corrupt each other.
    """
    raw = analyzer.analyze(
        text=text,
        entities=cfg.entities,
        language="en",
        score_threshold=cfg.score_threshold,
    )
    raw.sort(key=lambda r: (-(r.end - r.start), -r.score))
    chosen: list[RecognizerResult] = []
    for res in raw:
        if not any(res.start < c.end and c.start < res.end for c in chosen):
            chosen.append(res)
    chosen.sort(key=lambda r: r.start)
    logger.info("Detected %d PII entities", len(chosen))
    return chosen
