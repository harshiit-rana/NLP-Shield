"""End-to-end accuracy evaluation against the synthetic fixtures.

Runs the full pipeline (rasterise -> preprocess -> OCR -> detect ->
pseudonymize) on every fixture PDF in tests/fixtures/ and scores the
sanitized output against the fixture's ground truth:

* recall  — of the ground-truth PII values that OCR actually captured,
            how many were removed from the sanitized text
* precision — of the values the pipeline pseudonymized, how many
            correspond to a planted ground-truth value
* OCR misses are reported separately (a value OCR never captured cannot
  leak, but it also cannot be credited to detection)

Acceptance bar: ZERO ground-truth PII strings surviving in sanitized
output. Exit code 1 if the bar is not met.

Usage: python scripts/evaluate.py [--config config.yaml]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from pseudonymiser.config import load_config  # noqa: E402
from pseudonymiser.pipeline import Pipeline  # noqa: E402

FIXTURES_DIR = REPO / "tests" / "fixtures"


def squash(text: str) -> str:
    """Case-insensitive, whitespace/punct-tolerant comparison form."""
    return re.sub(r"[\s\-]+", "", text).lower()


def contains(haystack: str, needle: str) -> bool:
    return squash(needle) in squash(haystack)


def evaluate(config_path: str | None) -> int:
    truths = sorted(FIXTURES_DIR.glob("*.truth.json"))
    if not truths:
        print("No fixtures found — run: python scripts/make_fixtures.py")
        return 2

    config = load_config(config_path)
    per_type = defaultdict(lambda: {"caught": 0, "leaked": 0, "ocr_miss": 0})
    leaks: list[tuple[str, str, str]] = []  # (fixture, type, value)
    detected_values: list[tuple[str, str]] = []  # (entity_type, real_value)
    truth_values: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        config.store.db_path = str(Path(tmp) / "eval.db")
        config.store.key_file = str(Path(tmp) / "eval.key")
        out_dir = Path(tmp) / "out"
        pipeline = Pipeline(config)
        try:
            for truth_path in truths:
                truth = json.loads(truth_path.read_text())
                name = truth["fixture"]
                pdf = FIXTURES_DIR / f"{name}.pdf"
                report = pipeline.process_file(pdf, out_dir)
                extracted = Path(report.extracted_path).read_text()
                sanitized = Path(report.sanitized_path).read_text()

                for ent in truth["entities"]:
                    etype, value = ent["type"], ent["value"]
                    truth_values.append(squash(value))
                    if not contains(extracted, value):
                        per_type[etype]["ocr_miss"] += 1
                        continue
                    if contains(sanitized, value):
                        per_type[etype]["leaked"] += 1
                        leaks.append((name, etype, value))
                    else:
                        per_type[etype]["caught"] += 1

            detected_values = [(t, v) for t, _, v in
                               (row for row in pipeline.store.all_mappings())]
        finally:
            pipeline.close()

    # Precision: a pseudonymized value is a true positive if it overlaps a
    # planted value (type-agnostic — cross-type detection still protects).
    tp = fp = 0
    fp_by_type: dict[str, int] = defaultdict(int)
    for etype, value in detected_values:
        sv = squash(value)
        if any(sv in t or t in sv for t in truth_values if t):
            tp += 1
        else:
            fp += 1
            fp_by_type[etype] += 1

    print("\n=== Per-entity results (recall over OCR-captured values) ===")
    print(f"{'entity type':<26}{'caught':>7}{'leaked':>8}{'ocr-miss':>10}{'recall':>9}")
    for etype in sorted(per_type):
        s = per_type[etype]
        denom = s["caught"] + s["leaked"]
        recall = s["caught"] / denom if denom else float("nan")
        print(f"{etype:<26}{s['caught']:>7}{s['leaked']:>8}{s['ocr_miss']:>10}"
              f"{recall:>9.2f}")

    total_caught = sum(s["caught"] for s in per_type.values())
    total_leaked = sum(s["leaked"] for s in per_type.values())
    total_miss = sum(s["ocr_miss"] for s in per_type.values())
    overall_recall = (total_caught / (total_caught + total_leaked)
                      if (total_caught + total_leaked) else float("nan"))
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    f1 = (2 * precision * overall_recall / (precision + overall_recall)
          if precision + overall_recall > 0 else 0.0)

    print("\n=== Overall ===")
    print(f"recall:    {overall_recall:.3f}  ({total_caught}/{total_caught + total_leaked}"
          f" captured values removed; {total_miss} OCR misses excluded)")
    print(f"precision: {precision:.3f}  ({tp} matched / {fp} extra redactions)")
    print(f"f1:        {f1:.3f}")
    if fp_by_type:
        extras = ", ".join(f"{k}={v}" for k, v in sorted(fp_by_type.items()))
        print(f"extra (over-)redactions by type: {extras}")

    if leaks:
        print("\n*** ACCEPTANCE BAR FAILED — ground-truth PII in sanitized output:")
        for fixture, etype, value in leaks:
            print(f"  [{fixture}] {etype}: {value!r}")
        return 1
    print("\nACCEPTANCE BAR MET: zero ground-truth PII strings survived.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    sys.exit(evaluate(args.config))
