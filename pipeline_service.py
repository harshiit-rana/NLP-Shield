import os
import re
import csv
import io
import tempfile
import logging

from main import extract_pdf_text, ocr_image, detect_pii
from src.pseudonymiser.store import MappingStore
from src.pseudonymiser.config import StoreConfig, PseudonymConfig
from src.pseudonymiser.pseudonymize import Pseudonymizer, sweep_person_fragments, depseudonymize

logger = logging.getLogger(__name__)

class CustomPseudonymizer(Pseudonymizer):
    def _new_pseudonym(self, entity_type: str, value: str, scope: str) -> str:
        if self._cfg.style == "faker":
            return super()._new_pseudonym(entity_type, value, scope)
            
        n = self._store.count_for_type(entity_type, scope) + 1
        base_candidate = f"{entity_type}_{n:03d}"
        
        # Keep the year for DATE_TIME entities
        m = None
        if entity_type == "DATE_TIME":
            m = re.search(r"\b((?:19|20)\d{2})\b", value)
            if m:
                base_candidate = f"{base_candidate} {m.group(1)}"
                
        candidate = base_candidate
        while self._store.pseudonym_exists(candidate, scope):
            n += 1
            base_candidate = f"{entity_type}_{n:03d}"
            if m:
                base_candidate = f"{base_candidate} {m.group(1)}"
            candidate = base_candidate
            
        return candidate

def run_pipeline(file_bytes: bytes, filename: str) -> dict:
    base = os.path.splitext(filename)[0]
    
    # 1. Save bytes to temp file
    ext = os.path.splitext(filename)[1].lower()
    fd, temp_path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(file_bytes)
            
        # 2. Extract text (PDF or image)
        if ext == ".pdf":
            text, flagged = extract_pdf_text(temp_path)
        else:
            text, flagged = ocr_image(temp_path)
            
        # 3. Detect PII
        raw_detections = detect_pii(text)
        
        # Filter overlapping spans (keep the longest/first)
        detections = []
        for d in sorted(raw_detections, key=lambda x: (x.start, -(x.end - x.start))):
            if not any(d.start < fd.end and d.end > fd.start for fd in detections):
                detections.append(d)
        
        # 4. Pseudonymise using the secure MappingStore
        store_cfg = StoreConfig(db_path="pseudonymiser.db")
        pseud_cfg = PseudonymConfig(style="token", scope="corpus")
        
        with MappingStore(store_cfg) as store:
            pseudonymizer = CustomPseudonymizer(store, pseud_cfg)
            res = pseudonymizer.apply(text, detections, doc_id=base)
            
            # 5. Residual sweep for fragments (using our new text output)
            sanitized, num_swept = sweep_person_fragments(res.text, store, scope="corpus")
            
            # 6. Automatically save the scrubbed text to a file on the server
            os.makedirs("outputs", exist_ok=True)
            output_file = os.path.join("outputs", f"{base}-pseudonamed.txt")
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(sanitized)
                
            csv_path = os.path.join("outputs", f"{base}-keys.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["entity_type", "pseudonym", "real_value"])
                writer.writerows(store.all_mappings(scope="corpus"))
                
            # Format keys for UI
            all_maps = store.all_mappings(scope="corpus")
            keys_list = [
                {"token": pseudonym, "category": entity_type, "value": real_val, "occurrences": 1}
                for entity_type, pseudonym, real_val in all_maps
            ]
            
            # Return result
            return {
                "filename": filename,
                "sanitized_text": sanitized,
                "pseudonymized_text": sanitized,
                "original_text": text,
                "keys": keys_list,
                "pii_counts": res.counts,
                "fragments_swept": num_swept,
                "low_confidence_lines": len(flagged),
                "stats": {
                    "entities": sum(res.counts.values()),
                    "chars": len(sanitized),
                    "reduction": f"{(1 - len(sanitized)/(len(text) or 1)):.1%}"
                }
            }
            
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

def run_depseudonymize(text: str, csv_content: str = None) -> dict:
    """Restores the original PII from pseudonymised text using database or uploaded CSV."""
    if csv_content:
        reader = csv.reader(io.StringIO(csv_content))
        mappings = []
        for row in reader:
            if not row or len(row) < 3 or row[0].lower() in ("entity_type", "type", "category"):
                continue
            # entity_type, pseudonym/token, real_value
            mappings.append((row[0].strip(), row[1].strip(), row[2].strip()))
        
        restored = text
        resolved = []
        for ent_type, token, real_val in sorted(mappings, key=lambda m: -len(m[1])):
            if token in restored:
                restored = restored.replace(token, real_val)
                resolved.append({"token": token, "category": ent_type, "value": real_val})
        return {"restored_text": restored, "resolved": resolved}
    else:
        store_cfg = StoreConfig(db_path="pseudonymiser.db")
        with MappingStore(store_cfg) as store:
            all_maps = store.all_mappings(scope="corpus")
            restored = depseudonymize(text, store, scope="corpus")
            resolved = [
                {"token": p, "category": t, "value": r}
                for t, p, r in all_maps if p in text
            ]
            return {"restored_text": restored, "resolved": resolved}
