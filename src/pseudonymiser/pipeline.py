"""End-to-end orchestration: extract -> detect -> pseudonymize -> save.

Log output never contains raw PII: only entity types, counts, and
confidence figures are logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .detect import analyze_text, build_analyzer
from .ocr import IMAGE_EXTENSIONS, extract_document
from .pseudonymize import Pseudonymizer, sweep_person_fragments
from .store import MappingStore

logger = logging.getLogger(__name__)


@dataclass
class DocumentReport:
    """Per-document processing summary (safe to print — no raw PII)."""

    input_path: str
    extracted_path: str
    sanitized_path: str
    pages: int
    flagged_pages: list[int]
    entity_counts: dict[str, int] = field(default_factory=dict)

    @property
    def total_entities(self) -> int:
        return sum(self.entity_counts.values())


class Pipeline:
    """Reusable pipeline; engines are built once and shared across files."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._analyzer = build_analyzer(config.detection)
        self._store = MappingStore(config.store)
        self._pseudonymizer = Pseudonymizer(self._store, config.pseudonyms)

    @property
    def store(self) -> MappingStore:
        return self._store

    def process_file(self, input_path: str | Path, out_dir: str | Path) -> DocumentReport:
        """Process one PDF/image into extracted + sanitized text files."""
        input_path = Path(input_path)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        doc = extract_document(input_path, self._config.ocr)
        base = input_path.stem
        extracted_path = out_dir / f"{base}.extracted.txt"
        sanitized_path = out_dir / f"{base}.sanitized.txt"

        # extracted.txt contains raw PII by definition — it stays local and
        # the output directory is gitignored; it exists for audit/review.
        extracted_path.write_text(doc.text, encoding="utf-8")

        results = analyze_text(self._analyzer, doc.text, self._config.detection)
        pseudo = self._pseudonymizer.apply(doc.text, results, doc_id=base)
        sanitized = pseudo.text
        if self._config.pseudonyms.fragment_sweep:
            scope = ("corpus" if self._config.pseudonyms.scope == "corpus"
                     else base)
            sanitized, swept = sweep_person_fragments(sanitized, self._store,
                                                      scope)
            if swept:
                pseudo.counts["PERSON (fragment sweep)"] = swept
        sanitized_path.write_text(sanitized, encoding="utf-8")

        report = DocumentReport(
            input_path=str(input_path),
            extracted_path=str(extracted_path),
            sanitized_path=str(sanitized_path),
            pages=len(doc.pages),
            flagged_pages=doc.flagged_pages,
            entity_counts=pseudo.counts,
        )
        logger.info("Processed %s: %d pages, %d entities pseudonymized",
                    input_path.name, report.pages, report.total_entities)
        return report

    def process_directory(self, folder: str | Path,
                          out_dir: str | Path) -> list[DocumentReport]:
        """Process every supported file in a directory; one bad file does
        not abort the batch."""
        folder = Path(folder)
        if not folder.exists():
            raise FileNotFoundError(f"Input folder does not exist: {folder}")
        if not folder.is_dir():
            raise NotADirectoryError(f"Not a directory: {folder} "
                                     f"(use 'pseudonymise run' for single files)")
        supported = {".pdf"} | IMAGE_EXTENSIONS
        reports: list[DocumentReport] = []
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in supported:
                logger.info("Skipping unsupported file: %s", path.name)
                continue
            try:
                reports.append(self.process_file(path, out_dir))
            except Exception:
                logger.exception("Failed to process %s; continuing with "
                                 "remaining files", path.name)
        return reports

    def close(self) -> None:
        self._store.close()
