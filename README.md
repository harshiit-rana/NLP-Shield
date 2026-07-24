# Pseudonymisation_Project

Fully local pseudonymisation of PII in scanned documents, built for a
TPRM/GRC workflow: analysts process contracts, MSAs, SLAs, NDAs, ISMS
policies, audit reports, logs, e-mails, and attendance sheets, then paste
the sanitized text into an external LLM without leaking anything.

**Pipeline:** scanned PDF → rasterise (300 DPI) → OpenCV preprocessing →
OCR (Tesseract, PaddleOCR optional) → PII detection (Presidio +
transformer NER + custom recognisers with checksum validation) → every
PII value replaced by a **unique, consistent pseudonym** → encrypted
real↔pseudonym mapping stored in local SQLite → LLM output can be
**reversed** back to real values.

PDFs that already have a text layer skip OCR and are extracted directly
with PyMuPDF.

## Install

System dependencies: `tesseract-ocr` and `poppler-utils`.

```bash
# Debian/Ubuntu
sudo apt install tesseract-ocr poppler-utils
# or without root, via conda-forge (also provides Python 3.11):
conda create -n pseudonymiser -c conda-forge python=3.11 tesseract poppler

pip install -r requirements.txt
pip install -e .
make download-models        # one-time; the runtime is offline after this
```

`make download-models` fetches the spaCy transformer model
(`en_core_web_trf`). Add `GLINER=1` to also cache the optional GLiNER
model. This is the **only** step that touches the network; copy the
environment to an air-gapped machine afterwards if required.

## Quickstart

```bash
pseudonymise run input.pdf -o out/     # writes out/input.extracted.txt (raw, local audit copy)
                                       #        out/input.sanitized.txt (LLM-safe)
pseudonymise batch ./contracts -o out/ # whole folder; one bad file won't abort the batch
pseudonymise reverse llm_response.txt  # restore real values in LLM output
pseudonymise export-csv mappings.csv   # decrypted mapping export (sensitive!)
```

`run` prints a per-entity summary (counts only — raw PII is never logged)
and flags pages whose mean OCR confidence is low so an analyst can review
them manually.

## Configuration (`config.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `ocr.engine` | `tesseract` | `paddleocr` available as optional extra |
| `ocr.dpi` | `300` | rasterisation DPI (minimum enforced 300) |
| `ocr.low_confidence_threshold` | `60` | flag pages below this mean word confidence |
| `ocr.deskew` / `denoise` / `adaptive_threshold` / `border_removal` / `perspective_correction` | `true` | OpenCV preprocessing steps |
| `detection.score_threshold` | `0.35` | recall-first: over-redacting beats leaking |
| `detection.spacy_model` | `en_core_web_trf` | transformer NER; `en_core_web_lg` as lighter fallback |
| `detection.use_gliner` | `false` | union GLiNER (`urchade/gliner_multi_pii-v1`) results |
| `pseudonyms.style` | `token` | `token` → `PERSON_001`; `faker` → realistic stable fakes |
| `pseudonyms.scope` | `document` | `corpus` = same value maps identically across all documents |
| `store.db_path` | `pseudonymiser.db` | local SQLite mapping DB (gitignored) |
| `store.key_file` | `.pseudonymiser.key` | Fernet key file; or set `PSEUDONYMISER_KEY`/`PSEUDONYMISER_KEY_FILE` |

## Detected entities

Presidio built-ins (PERSON, ORGANIZATION, EMAIL_ADDRESS, PHONE_NUMBER,
LOCATION, DATE_TIME, CREDIT_CARD, IBAN_CODE, IP_ADDRESS, URL, US_SSN,
US_BANK_NUMBER, UK_NINO, IN_PAN, IN_AADHAAR, IN_PASSPORT, IN_VOTER) plus
custom recognisers: IN_IFSC, IN_UPI, Indian mobile numbers, IN_PIN_CODE,
IN_GSTIN, IN_CIN, IN_VEHICLE_REGISTRATION, BANK_ACCOUNT, PH_TIN,
REFERENCE_NUMBER, MONEY_AMOUNT.

**OCR-error tolerance:** structured identifiers are matched with
confusion-tolerant patterns (O↔0, I/l↔1, S↔5, B↔8), repaired, and then
validated — Aadhaar with the Verhoeff checksum, cards with Luhn, PAN by
structure — so garbled scans are still caught while random strings are
rejected.

## Accuracy (synthetic scanned-PDF fixtures)

`make fixtures && make evaluate` renders five image-only PDFs (contract,
KYC form, letter, NDA, skewed invoice) with 48 planted PII values and
scores the full pipeline end to end:

```
recall:    1.000  (48/48 OCR-captured values removed from sanitized output)
precision: 0.907  (5 extra redactions, all deliberate over-redaction)
f1:        0.951
ACCEPTANCE BAR MET: zero ground-truth PII strings survived.
```

Per-entity recall is 1.00 for all 20 entity types in the fixture set. A
real 3-page scanned contract processed cleanly with zero residual
email/phone/Aadhaar/PAN-shaped strings in the sanitized output.

Known limitations:

* Recall is measured on synthetic renders; very noisy real scans can
  still produce OCR text too garbled for any recogniser. Low-confidence
  pages are flagged for exactly this reason — review them.
* Handwriting, signatures, and stamps are not reliably OCR'd.
* Bare 6-digit numbers / 9-18-digit numbers are only treated as PIN
  codes / account numbers when context supports it; a stray account
  number with no context can slip through.
* NER can over-redact (e.g. state names, "next week") — by design.

## SECURITY

```
 scanned PDF ──► rasterise ──► OCR ──► detect ──► pseudonymize ──► sanitized.txt ──► (you paste to LLM)
     │                                                 │
     └── extracted.txt (raw, local only)               └── SQLite mapping DB (values Fernet-encrypted)
                                                            key: .pseudonymiser.key / env var
 LLM response ──► pseudonymise reverse ──► restored text (local)
```

* **Everything runs locally.** No cloud APIs, no telemetry, no external
  NER services. Project code contains no network imports; the only
  network activity is `make download-models`, run once.
* **The mapping DB** (`pseudonymiser.db`) stores real values encrypted
  with Fernet and hashed with a keyed HMAC — no plaintext PII at rest.
* **The key** lives in `.pseudonymiser.key` (created `0600`) or the
  `PSEUDONYMISER_KEY`/`PSEUDONYMISER_KEY_FILE` environment variables.
  Whoever holds the key + DB can re-identify: restrict access to the
  analysts authorised to run `reverse`, back the key up separately from
  the DB, and never commit either (both are gitignored).
* **`extracted.txt` and `export-csv` output contain raw PII** — they
  exist for audit/reversal and must stay inside your infrastructure.
* Logs contain entity types and counts only, never raw values.

## Development

```bash
make fixtures   # regenerate synthetic test PDFs (all PII is fake, valid-checksum)
make test       # 55 unit tests: recognisers, validators, OCR, store, round-trip
make evaluate   # end-to-end precision/recall/F1 + zero-leak acceptance bar
```
