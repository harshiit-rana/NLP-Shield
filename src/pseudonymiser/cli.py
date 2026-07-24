"""Command-line interface.

Commands:
  pseudonymise run input.pdf -o out/     process one file
  pseudonymise batch ./folder -o out/    process a directory
  pseudonymise reverse llm_response.txt  de-pseudonymise LLM output
  pseudonymise export-csv mappings.csv   export the mapping table (decrypted)
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from .config import load_config
from .pipeline import DocumentReport, Pipeline
from .pseudonymize import depseudonymize
from .store import MappingStore


def _print_report(report: DocumentReport) -> None:
    print(f"\n{report.input_path}")
    print(f"  pages: {report.pages}"
          + (f"  (low-confidence pages flagged for review: "
             f"{report.flagged_pages})" if report.flagged_pages else ""))
    print(f"  extracted: {report.extracted_path}")
    print(f"  sanitized: {report.sanitized_path}")
    if report.entity_counts:
        print("  entities pseudonymized:")
        for entity_type, count in sorted(report.entity_counts.items()):
            print(f"    {entity_type:<28}{count}")
    else:
        print("  entities pseudonymized: none detected")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pseudonymise",
        description="Local PII pseudonymisation for scanned PDFs.",
    )
    parser.add_argument("--config", default=None,
                        help="path to config.yaml (default: ./config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="process a single PDF or image")
    p_run.add_argument("input")
    p_run.add_argument("-o", "--out-dir", default="out")

    p_batch = sub.add_parser("batch", help="process every file in a folder")
    p_batch.add_argument("folder")
    p_batch.add_argument("-o", "--out-dir", default="out")

    p_rev = sub.add_parser("reverse", help="restore real values in LLM output")
    p_rev.add_argument("input", help="text file containing pseudonyms")
    p_rev.add_argument("-o", "--output", default=None,
                       help="output path (default: <input>.restored.txt)")
    p_rev.add_argument("--scope", default=None,
                       help="restrict to one scope (doc id or 'corpus'); "
                            "default: all scopes")

    p_csv = sub.add_parser("export-csv",
                           help="export decrypted mappings to CSV (sensitive!)")
    p_csv.add_argument("output", help="CSV path to write")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)

    if args.command == "run":
        pipeline = Pipeline(config)
        try:
            _print_report(pipeline.process_file(args.input, args.out_dir))
        finally:
            pipeline.close()
        return 0

    if args.command == "batch":
        pipeline = Pipeline(config)
        try:
            reports = pipeline.process_directory(args.folder, args.out_dir)
            for report in reports:
                _print_report(report)
            print(f"\nProcessed {len(reports)} file(s).")
        finally:
            pipeline.close()
        return 0

    if args.command == "reverse":
        in_path = Path(args.input)
        text = in_path.read_text(encoding="utf-8")
        with MappingStore(config.store) as store:
            restored = depseudonymize(text, store, scope=args.scope)
        out_path = Path(args.output) if args.output else \
            in_path.with_suffix(in_path.suffix + ".restored.txt")
        out_path.write_text(restored, encoding="utf-8")
        print(f"Restored text written to {out_path}")
        return 0

    if args.command == "export-csv":
        with MappingStore(config.store) as store:
            rows = store.all_mappings()
        out_path = Path(args.output)
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["entity_type", "pseudonym", "real_value"])
            writer.writerows(rows)
        print(f"Exported {len(rows)} mappings to {out_path}")
        print("WARNING: this file contains decrypted PII — handle accordingly.")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
