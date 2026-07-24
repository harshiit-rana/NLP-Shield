"""Encrypted SQLite store for the real<->pseudonym mapping.

Real values are encrypted at rest with Fernet; lookups use a keyed
HMAC-SHA256 hash of the real value so the database never contains
plaintext PII. The key is resolved from (in order) the
``PSEUDONYMISER_KEY`` environment variable, the file named by
``PSEUDONYMISER_KEY_FILE``, then the configured key-file path (created on
first use, permissions 0600, and gitignored). The key must never be
committed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import StoreConfig

logger = logging.getLogger(__name__)


class StoreError(Exception):
    """Raised for key-management or database failures."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    real_value_encrypted BLOB NOT NULL,
    real_value_hash TEXT NOT NULL,
    pseudonym TEXT NOT NULL,
    scope TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (real_value_hash, scope)
);
CREATE INDEX IF NOT EXISTS idx_mapping_scope ON mapping (scope);
CREATE INDEX IF NOT EXISTS idx_mapping_pseudonym ON mapping (pseudonym, scope);
"""


def _load_key(cfg: StoreConfig) -> bytes:
    env_key = os.environ.get("PSEUDONYMISER_KEY")
    if env_key:
        return env_key.encode()

    key_path = Path(os.environ.get("PSEUDONYMISER_KEY_FILE", cfg.key_file))
    if key_path.exists():
        key = key_path.read_bytes().strip()
        if not key:
            raise StoreError(f"Key file {key_path} is empty")
        return key

    key = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    os.chmod(key_path, 0o600)
    logger.info("Generated new encryption key at %s (keep this file safe "
                "and out of version control)", key_path)
    return key


class MappingStore:
    """SQLite-backed pseudonym mapping with encryption at rest."""

    def __init__(self, cfg: StoreConfig) -> None:
        self._key = _load_key(cfg)
        try:
            self._fernet = Fernet(self._key)
        except (ValueError, TypeError) as exc:
            raise StoreError("Encryption key is not a valid Fernet key") from exc
        # Separate HMAC key derived from the Fernet key so hash and
        # encryption never share key material directly.
        self._hmac_key = hashlib.sha256(b"pseudonymiser-hmac:" + self._key).digest()
        db_path = Path(cfg.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- key-derived helpers -------------------------------------------------

    def hash_value(self, value: str) -> str:
        return hmac.new(self._hmac_key, value.encode("utf-8"),
                        hashlib.sha256).hexdigest()

    def hmac_seed(self, value: str) -> int:
        """Stable integer seed for Faker-style pseudonyms."""
        digest = hmac.new(self._hmac_key, b"seed:" + value.encode("utf-8"),
                          hashlib.sha256).digest()
        return int.from_bytes(digest[:8], "big")

    # -- mapping operations --------------------------------------------------

    def get_pseudonym(self, value: str, scope: str) -> str | None:
        row = self._conn.execute(
            "SELECT pseudonym FROM mapping WHERE real_value_hash = ? AND scope = ?",
            (self.hash_value(value), scope),
        ).fetchone()
        return row[0] if row else None

    def add_mapping(self, doc_id: str, entity_type: str, value: str,
                    pseudonym: str, scope: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO mapping (doc_id, entity_type, "
            "real_value_encrypted, real_value_hash, pseudonym, scope, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (doc_id, entity_type,
             self._fernet.encrypt(value.encode("utf-8")),
             self.hash_value(value), pseudonym, scope,
             datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def count_for_type(self, entity_type: str, scope: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM mapping WHERE entity_type = ? AND scope = ?",
            (entity_type, scope),
        ).fetchone()
        return int(row[0])

    def pseudonym_exists(self, pseudonym: str, scope: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM mapping WHERE pseudonym = ? AND scope = ? LIMIT 1",
            (pseudonym, scope),
        ).fetchone()
        return row is not None

    def all_mappings(self, scope: str | None = None) -> list[tuple[str, str, str]]:
        """Return (entity_type, pseudonym, real_value) rows, decrypted.

        Only call this for de-pseudonymisation or an explicit export the
        user requested — never for logging.
        """
        if scope is None:
            rows = self._conn.execute(
                "SELECT entity_type, pseudonym, real_value_encrypted FROM mapping"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT entity_type, pseudonym, real_value_encrypted "
                "FROM mapping WHERE scope = ?", (scope,)
            ).fetchall()
        out = []
        for entity_type, pseudonym, blob in rows:
            try:
                real = self._fernet.decrypt(blob).decode("utf-8")
            except InvalidToken as exc:
                raise StoreError(
                    "Could not decrypt a mapping row — the database was "
                    "written with a different key"
                ) from exc
            out.append((entity_type, pseudonym, real))
        return out

    def scopes(self) -> list[str]:
        return [r[0] for r in
                self._conn.execute("SELECT DISTINCT scope FROM mapping")]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "MappingStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
