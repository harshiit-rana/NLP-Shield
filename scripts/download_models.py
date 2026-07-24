"""One-time model download for air-gap-friendly runtime.

This is the ONLY part of the project allowed to touch the network. Run it
once on a connected machine; afterwards the pipeline runs fully offline.

Usage:
  python scripts/download_models.py            # spaCy model only
  python scripts/download_models.py --gliner   # also fetch GLiNER weights
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def download_spacy(model: str) -> None:
    import spacy.util

    if spacy.util.is_package(model):
        print(f"spaCy model {model} already installed")
        return
    print(f"Downloading spaCy model {model} ...")
    subprocess.check_call([sys.executable, "-m", "spacy", "download", model])


def download_gliner(model: str) -> None:
    try:
        from gliner import GLiNER
    except ImportError:
        print("gliner is not installed; run: pip install gliner", file=sys.stderr)
        raise SystemExit(1)
    print(f"Downloading GLiNER model {model} into the local HF cache ...")
    GLiNER.from_pretrained(model)
    print("GLiNER model cached")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spacy-model", default="en_core_web_trf")
    parser.add_argument("--gliner", action="store_true",
                        help="also download the GLiNER PII model")
    parser.add_argument("--gliner-model", default="urchade/gliner_multi_pii-v1")
    args = parser.parse_args()

    download_spacy(args.spacy_model)
    if args.gliner:
        download_gliner(args.gliner_model)
    print("Done. The runtime needs no further network access.")


if __name__ == "__main__":
    main()
