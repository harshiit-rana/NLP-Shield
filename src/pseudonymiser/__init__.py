"""Local, offline pseudonymisation pipeline for scanned PDFs.

Rasterises/OCRs image-only PDFs, detects PII with Presidio (plus custom
recognisers), replaces every PII value with a unique, consistent pseudonym,
and stores the encrypted real<->pseudonym mapping in a local SQLite database
so LLM responses can be de-pseudonymised later.

Nothing in this package performs network I/O at runtime; the only network
activity in the project is the explicit one-time model download step.
"""

__version__ = "1.0.0"
