"""Configuration loading for the pseudonymisation pipeline.

Configuration lives in a YAML file (``config.yaml`` at the repo root by
default) and is materialised into typed dataclasses so the rest of the
code never touches raw dicts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    """Raised when the configuration file is missing or malformed."""


@dataclass
class OcrConfig:
    engine: str = "tesseract"  # "tesseract" | "paddleocr"
    dpi: int = 300
    tesseract_oem: int = 1
    tesseract_psm: int = 3
    low_confidence_threshold: float = 60.0  # mean word confidence (0-100)
    deskew: bool = True
    denoise: bool = True
    adaptive_threshold: bool = True
    border_removal: bool = True
    perspective_correction: bool = True


@dataclass
class DetectionConfig:
    score_threshold: float = 0.35
    spacy_model: str = "en_core_web_trf"
    use_gliner: bool = False
    gliner_model: str = "urchade/gliner_multi_pii-v1"
    gliner_threshold: float = 0.4
    entities: list[str] = field(default_factory=lambda: [
        "PERSON", "ORGANIZATION", "EMAIL_ADDRESS", "PHONE_NUMBER",
        "LOCATION", "DATE_TIME",
        "CREDIT_CARD", "IBAN_CODE", "IP_ADDRESS", "URL",
        "US_SSN", "US_BANK_NUMBER", "UK_NINO",
        "IN_PAN", "IN_AADHAAR", "IN_PASSPORT", "IN_VOTER",
        "IN_VEHICLE_REGISTRATION",
        # Custom recognisers registered by detect.py:
        "IN_IFSC", "IN_UPI", "IN_PIN_CODE", "IN_GSTIN", "IN_CIN",
        "BANK_ACCOUNT", "PH_TIN", "REFERENCE_NUMBER", "MONEY_AMOUNT",
    ])


@dataclass
class PseudonymConfig:
    style: str = "token"        # "token" (PERSON_001) | "faker" (realistic)
    scope: str = "document"     # "document" | "corpus"
    faker_locale: str = "en_IN"


@dataclass
class StoreConfig:
    db_path: str = "pseudonymiser.db"
    # Key material resolution order: env var PSEUDONYMISER_KEY (the key
    # itself), env var PSEUDONYMISER_KEY_FILE, then this path. The key file
    # is created on first use and must stay gitignored.
    key_file: str = ".pseudonymiser.key"


@dataclass
class Config:
    ocr: OcrConfig = field(default_factory=OcrConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    pseudonyms: PseudonymConfig = field(default_factory=PseudonymConfig)
    store: StoreConfig = field(default_factory=StoreConfig)


def _merge(dc, data: dict):
    """Overlay a dict onto a dataclass instance, ignoring unknown keys."""
    for key, value in (data or {}).items():
        if hasattr(dc, key):
            setattr(dc, key, value)
    return dc


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load configuration from YAML; missing file yields pure defaults."""
    cfg = Config()
    if path is None:
        candidate = Path("config.yaml")
        path = candidate if candidate.exists() else None
    if path is None:
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Config file is not valid YAML: {path}") from exc
    _merge(cfg.ocr, raw.get("ocr", {}))
    _merge(cfg.detection, raw.get("detection", {}))
    _merge(cfg.pseudonyms, raw.get("pseudonyms", {}))
    _merge(cfg.store, raw.get("store", {}))
    return cfg
