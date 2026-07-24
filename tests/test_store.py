"""Unit tests for the encrypted SQLite mapping store."""

import sqlite3

import pytest

from pseudonymiser.config import StoreConfig
from pseudonymiser.store import MappingStore, StoreError


@pytest.fixture
def store(tmp_path):
    cfg = StoreConfig(db_path=str(tmp_path / "test.db"),
                      key_file=str(tmp_path / "test.key"))
    with MappingStore(cfg) as s:
        yield s


def test_add_and_lookup(store):
    store.add_mapping("doc1", "PERSON", "Rajesh Sharma", "PERSON_001", "doc1")
    assert store.get_pseudonym("Rajesh Sharma", "doc1") == "PERSON_001"
    assert store.get_pseudonym("Rajesh Sharma", "other") is None


def test_uniqueness_constraint(store):
    store.add_mapping("doc1", "PERSON", "A", "PERSON_001", "doc1")
    store.add_mapping("doc1", "PERSON", "A", "PERSON_999", "doc1")  # ignored
    assert store.get_pseudonym("A", "doc1") == "PERSON_001"


def test_real_values_encrypted_on_disk(tmp_path):
    cfg = StoreConfig(db_path=str(tmp_path / "e.db"),
                      key_file=str(tmp_path / "e.key"))
    with MappingStore(cfg) as store:
        store.add_mapping("doc1", "PERSON", "SecretName", "PERSON_001", "doc1")
    conn = sqlite3.connect(cfg.db_path)
    blob = conn.execute("SELECT real_value_encrypted FROM mapping").fetchone()[0]
    conn.close()
    assert b"SecretName" not in blob
    raw = open(cfg.db_path, "rb").read()
    assert b"SecretName" not in raw


def test_decrypt_roundtrip(store):
    store.add_mapping("doc1", "EMAIL_ADDRESS", "a@b.com", "EMAIL_ADDRESS_001", "doc1")
    rows = store.all_mappings("doc1")
    assert ("EMAIL_ADDRESS", "EMAIL_ADDRESS_001", "a@b.com") in rows


def test_wrong_key_raises(tmp_path):
    db = tmp_path / "k.db"
    cfg1 = StoreConfig(db_path=str(db), key_file=str(tmp_path / "k1.key"))
    with MappingStore(cfg1) as store:
        store.add_mapping("d", "PERSON", "X", "PERSON_001", "d")
    cfg2 = StoreConfig(db_path=str(db), key_file=str(tmp_path / "k2.key"))
    with MappingStore(cfg2) as store2:
        with pytest.raises(StoreError):
            store2.all_mappings("d")


def test_count_and_exists(store):
    store.add_mapping("d", "PERSON", "A", "PERSON_001", "d")
    store.add_mapping("d", "PERSON", "B", "PERSON_002", "d")
    assert store.count_for_type("PERSON", "d") == 2
    assert store.pseudonym_exists("PERSON_002", "d")
    assert not store.pseudonym_exists("PERSON_003", "d")


def test_key_file_created_with_restrictive_permissions(tmp_path):
    import os
    cfg = StoreConfig(db_path=str(tmp_path / "p.db"),
                      key_file=str(tmp_path / "p.key"))
    MappingStore(cfg).close()
    mode = os.stat(tmp_path / "p.key").st_mode & 0o777
    assert mode == 0o600
