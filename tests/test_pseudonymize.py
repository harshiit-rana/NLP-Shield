"""Unit tests for pseudonym generation, consistency, and reversal."""

import pytest
from presidio_analyzer import RecognizerResult

from pseudonymiser.config import PseudonymConfig, StoreConfig
from pseudonymiser.pseudonymize import (
    Pseudonymizer,
    depseudonymize,
    sweep_person_fragments,
)
from pseudonymiser.store import MappingStore


@pytest.fixture
def store(tmp_path):
    cfg = StoreConfig(db_path=str(tmp_path / "t.db"),
                      key_file=str(tmp_path / "t.key"))
    with MappingStore(cfg) as s:
        yield s


def results_for(text, *values):
    out = []
    for value, etype in values:
        start = text.index(value)
        out.append(RecognizerResult(etype, start, start + len(value), 0.9))
    return out


def test_token_style_and_consistency(store):
    p = Pseudonymizer(store, PseudonymConfig(style="token"))
    text = "Rajesh met Meera. Later Rajesh called."
    res = [
        RecognizerResult("PERSON", 0, 6, 0.9),
        RecognizerResult("PERSON", 11, 16, 0.9),
        RecognizerResult("PERSON", 24, 30, 0.9),
    ]
    out = p.apply(text, res, doc_id="doc1")
    assert "Rajesh" not in out.text and "Meera" not in out.text
    # same value -> same pseudonym
    assert out.text.count("PERSON_001") == 2
    assert out.counts["PERSON"] == 3


def test_document_scope_isolated(store):
    p = Pseudonymizer(store, PseudonymConfig(style="token", scope="document"))
    t1 = p.apply("Rajesh", [RecognizerResult("PERSON", 0, 6, 0.9)], "doc1").text
    t2 = p.apply("Meera", [RecognizerResult("PERSON", 0, 5, 0.9)], "doc2").text
    # each doc restarts its counter
    assert t1 == "PERSON_001" and t2 == "PERSON_001"


def test_corpus_scope_shared(store):
    p = Pseudonymizer(store, PseudonymConfig(style="token", scope="corpus"))
    t1 = p.apply("Rajesh", [RecognizerResult("PERSON", 0, 6, 0.9)], "doc1").text
    t2 = p.apply("Rajesh", [RecognizerResult("PERSON", 0, 6, 0.9)], "doc2").text
    assert t1 == t2 == "PERSON_001"


def test_faker_style_stable(tmp_path):
    cfg = StoreConfig(db_path=str(tmp_path / "f.db"),
                      key_file=str(tmp_path / "f.key"))
    with MappingStore(cfg) as store:
        p = Pseudonymizer(store, PseudonymConfig(style="faker"))
        text = "Contact Rajesh Sharma"
        res = [RecognizerResult("PERSON", 8, 21, 0.9)]
        out1 = p.apply(text, res, "doc1").text
        out2 = p.apply(text, res, "doc1").text  # second run reuses mapping
        assert out1 == out2
        assert "Rajesh" not in out1


def test_roundtrip_reversal(store):
    p = Pseudonymizer(store, PseudonymConfig(style="token"))
    text = "Rajesh Sharma paid Rs. 500 to Meera on 9876543210."
    res = results_for(text, ("Rajesh Sharma", "PERSON"), ("Meera", "PERSON"),
                      ("9876543210", "PHONE_NUMBER"))
    sanitized = p.apply(text, res, "doc1").text
    assert "Rajesh" not in sanitized
    restored = depseudonymize(sanitized, store, scope="doc1")
    assert restored == text


def test_roundtrip_with_whitespace_spanning_detection(store):
    # NER spans sometimes include a trailing newline; the replacement must
    # not swallow it, or reversal cannot reproduce the original text.
    p = Pseudonymizer(store, PseudonymConfig(style="token"))
    text = "between Hardkraft Boxes\nPrivate Limited"
    res = [RecognizerResult("ORGANIZATION", 8, 24, 0.9)]  # includes the \n
    sanitized = p.apply(text, res, "doc1").text
    assert "\n" in sanitized
    assert depseudonymize(sanitized, store, scope="doc1") == text


def test_fragment_sweep_catches_garbled_names(store):
    store.add_mapping("d", "PERSON", "GRACIA B. JAVIER", "PERSON_001", "d")
    text = "Signed by TA B. JAVIER, Asst. Director, and witnessed by Gracia."
    swept, n = sweep_person_fragments(text, store, "d")
    assert "JAVIER" not in swept and "Gracia" not in swept
    assert "PERSON_001" in swept
    assert n == 2


def test_fragment_sweep_skips_short_and_common_tokens(store):
    store.add_mapping("d", "PERSON", "MY HAND AND SEAL", "PERSON_001", "d")
    text = "hand over the seal to me"
    swept, n = sweep_person_fragments(text, store, "d")
    assert swept == text and n == 0


def test_full_value_sweep_catches_missed_occurrences(store):
    # NER tagged "Ermita" once but missed a second occurrence in a noisy
    # signature block; the sweep must catch the verbatim leftover.
    store.add_mapping("d", "LOCATION", "Ermita", "LOCATION_001", "d")
    swept, n = sweep_person_fragments("St. Ermita area", store, "d")
    assert swept == "St. LOCATION_001 area" and n == 1


def test_full_value_sweep_skips_short_numeric_values(store):
    store.add_mapping("d", "DATE_TIME", "2017", "DATE_TIME_001", "d")
    swept, n = sweep_person_fragments("ref 2017-020", store, "d")
    assert swept == "ref 2017-020" and n == 0


def test_reversal_does_not_clobber_similar_tokens(store):
    # PERSON_001 vs PERSON_0011-style prefix collisions
    for i, name in enumerate(["Alice"] + [f"Name{n}" for n in range(1, 11)]):
        store.add_mapping("d", "PERSON", name, f"PERSON_{i+1:03d}", "d")
    store.add_mapping("d", "PERSON", "Bob", "PERSON_0011", "d")
    restored = depseudonymize("PERSON_0011 and PERSON_001", store, scope="d")
    assert restored == "Bob and Alice"
