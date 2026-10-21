# NLP Shield — Privacy-Preserving Document AI & Pseudonymisation

> End-to-end, fully local PII pseudonymisation and confidential document intelligence platform. Analyze confidential contracts, agreements, and sensitive records with external LLMs without leaking real-world identities.

---

## 🌟 Overview

When analyzing contracts, MSAs, legal agreements, financial statements, or medical records with modern Large Language Models (LLMs), organizations face significant privacy, GDPR, HIPAA, and DPDP compliance risks.

**NLP Shield** solves this with a **zero-trust de-identification pipeline**:
1. **Locally ingests** scanned PDFs and images via high-resolution rasterization and PaddleOCR.
2. **Detects Personally Identifiable Information (PII)** using Microsoft Presidio and a deep-learning transformer Named Entity Recognition (NER) model (`en_core_web_trf`).
3. **Replaces every sensitive entity** with a deterministic, context-preserving pseudorandom token (`PERSON_001`, `ORGANIZATION_001`, `MONEY_001`).
4. **Encodes and secures the identity mappings** in a local SQLite vault encrypted with **Fernet (AES-128)**.
5. **Enables confidential AI chat** (via Groq Cloud models like `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`, `llama-3.3-70b`). The LLM reasons strictly over privacy tokens without ever seeing real PII.
6. **One-Click Reversible Restoration**: Reconstructs real-world identities on the client side using the local key vault.

---

## 🏗️ Architecture

```
                                  LOCAL SECURE BOUNDARY
 ┌─────────────────┐       ┌────────────────────────┐       ┌─────────────────────────┐
 │ Scanned PDF/Img │ ───►  │  PyMuPDF + PaddleOCR   │ ───►  │ Presidio + Transformer  │
 └─────────────────┘       └────────────────────────┘       │   NER (en_core_web_trf) │
                                                            └───────────┬─────────────┘
                                                                        │
                                                                        ▼
 ┌─────────────────┐       ┌────────────────────────┐       ┌─────────────────────────┐
 │ Restored Report │ ◄───  │ One-Click Restoration  │ ◄───  │   Deterministic Token   │
 │   (Local Only)  │       │   (Local AES Vault)    │       │     Pseudonymisation    │
 └─────────────────┘       └────────────────────────┘       └───────────┬─────────────┘
                                                                        │
                                   EXTERNAL CLOUD                       ▼
                           ┌────────────────────────┐       ┌─────────────────────────┐
                           │   Groq LLM Reasoning   │ ◄───  │ Redacted Document & Chat│
                           │(model never sees PII!) │       │   (PERSON_001, etc.)    │
                           └────────────────────────┘       └─────────────────────────┘
```

---

## 📁 Project Structure

```
├── app.py                      # FastAPI web server & Groq LLM streaming proxy
├── pipeline_service.py         # Service layer integrating OCR, Presidio & MappingStore
├── main.py                     # Core offline pipeline & CLI tool
├── Dockerfile                  # Container definition (Python 3.11, PyTorch CPU, spaCy)
├── .dockerignore               # Docker build exclusions
├── .gitignore                  # Git exclusions (credentials, databases, caches)
├── requirements.txt            # Pinned runtime dependencies
├── pyproject.toml              # Project metadata and CLI entry points
├── config.yaml                 # Pipeline and OCR configuration settings
├── Makefile                    # Automation shortcuts (fixtures, tests, evaluate)
│
├── vaultdoc/                   # Frontend Web Application (NLP Shield UI)
│   ├── index.html              # Reactive single-page UI (Vue, Tailwind CSS)
│   ├── package.json            # Frontend packaging
│   └── src/                    # Source components and styles
│
├── src/pseudonymiser/          # Core Python Package
│   ├── detect.py               # Presidio analyzer & custom regex/checksum recognizers
│   ├── ocr.py                  # PyMuPDF rasterization & PaddleOCR/Tesseract engine
│   ├── pseudonymize.py         # Right-to-left replacer & residual fragment sweep
│   ├── store.py                # Encrypted SQLite mapping store (Fernet / AES)
│   ├── validators.py           # Verhoeff (Aadhaar), Luhn (Cards), PAN validators
│   ├── pipeline.py             # Orchestration pipeline
│   └── config.py               # Pydantic configuration schemas
│
├── scripts/                    # Development & Evaluation Utilities
│   ├── download_models.py      # Pre-caches spaCy & GLiNER transformer models
│   ├── evaluate.py             # Precision, recall, and zero-leak verification
│   └── make_fixtures.py        # Generates synthetic test fixtures
│
└── tests/                      # Automated Test Suite
    ├── fixtures/               # Synthetic scanned-PDF fixtures (no real PII)
    ├── test_ocr.py             # OCR extraction unit tests
    ├── test_pseudonymize.py    # Pseudonymisation & reversal round-trip tests
    ├── test_recognizers.py     # Custom PII recognizer tests
    ├── test_store.py           # Encrypted store tests
    └── test_validators.py      # Checksum & pattern validator tests
```

---

## ⚡ Quickstart

### 1. Installation

#### System Dependencies:
- **Tesseract OCR** & **Poppler Utils** (if processing with Tesseract):
  ```bash
  # Ubuntu/Debian
  sudo apt install -y tesseract-ocr poppler-utils
  ```

#### Python Environment (Python 3.11 recommended):
```bash
# Clone the repository
git clone https://github.com/harshiit-rana/NLP-Shield.git
cd NLP-Shield

# Install dependencies
pip install -r requirements.txt
pip install -e .

# Download spaCy transformer model (one-time setup)
python -m spacy download en_core_web_trf
```

---

### 2. Running the Web Application (Recommended)

Start the interactive FastAPI server:
```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Open your browser to:
```
http://localhost:8000
```

#### Features in the Web App:
- **Tab 01 (De-identify):** Drag-and-drop any PDF or image. View extracted text and highlighted pseudonym tokens with automatic key bundle generation.
- **Tab 02 (Restore):** Paste any pseudonymized text and choose your key bundle or upload a CSV to instantly restore original identities.
- **Tab 03 (Confidential AI Chat):** Connect your Groq API key and converse with high-speed models (`openai/gpt-oss-120b`, `llama-3.3-70b`). Responses render full Markdown tables and include a **"🪄 Send to restore"** button to instantly de-pseudonymize AI answers.

---

### 3. Running via Docker

Build and run the self-contained container:
```bash
docker build -t nlp-shield .
docker run -d -p 8000:8000 --name nlp-shield nlp-shield
```
Visit `http://localhost:8000`.

---

### 4. Running via CLI

```bash
# Pseudonymise a single document
pseudonymise run contract.pdf -o out/

# Batch process an entire folder of contracts
pseudonymise batch ./documents -o out/

# Reverse / restore an LLM response using the local database
pseudonymise reverse llm_response.txt

# Export decrypted mapping table to CSV (restricted access)
pseudonymise export-csv mappings.csv
```

---

## 🔒 Security & Privacy Guarantees

1. **Air-Gapped Operation:** All OCR, PII detection, pseudonymisation, and restoration run 100% locally on your machine.
2. **Encrypted Mapping Store:** Real identities are encrypted using **Fernet (AES-128)** and hashed using keyed **HMAC-SHA256**. The mapping database (`pseudonymiser.db`) contains zero plaintext PII at rest.
3. **Secret Key Isolation:** The encryption key (`.pseudonymiser.key`) remains strictly on the client server. The external LLM receives only privacy tokens (`PERSON_001`, `ORGANIZATION_001`).
4. **Deterministic Token Mapping:** The same entity receives the exact same pseudonym across the document, allowing the LLM to understand relationships without knowing identity.
5. **Right-to-Left Replacement:** Redaction operates backwards by string offset to prevent offset corruption and multi-entity overlap errors.
6. **Residual Fragment Sweeping:** Secondary sweep ensures partial name fragments (e.g., truncated signature blocks) are redacted even if OCR missed full context.

---

## 🧪 Testing & Evaluation

Run the automated test suite and accuracy benchmarks:
```bash
# Generate synthetic test fixtures
make fixtures

# Run all 62 unit tests
pytest

# End-to-end precision, recall, and zero-leak verification
python scripts/evaluate.py
```

### Benchmark Results (Synthetic Scanned Fixtures)
- **Recall:** `1.000` (100% of ground-truth PII removed from sanitized text)
- **Precision:** `0.875` (deliberate safety over-redaction)
- **Zero-Leak Acceptance Bar:** **MET** (0 planted PII strings survived in output)

---

## 📄 License
MIT License. Built for secure, privacy-preserving document workflows.
