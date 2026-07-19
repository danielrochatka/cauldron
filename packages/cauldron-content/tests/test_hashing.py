"""Tests for the canonical content hash algorithm."""
import hashlib
import json

from cauldron_content.hashing import compute_content_hash, normalize_body


def test_normalize_body_empty():
    assert normalize_body("") == ""
    assert normalize_body(None) == ""  # type: ignore[arg-type]


def test_normalize_body_lf_endings():
    assert normalize_body("hello\r\nworld") == "hello\nworld\n"


def test_normalize_body_cr_endings():
    assert normalize_body("hello\rworld") == "hello\nworld\n"


def test_normalize_body_trailing_newline_preserved():
    assert normalize_body("hello\n") == "hello\n"


def test_hash_is_deterministic():
    args = ("id", "coll", "slug", "published", "schema", {"a": 1}, "body")
    h1 = compute_content_hash(*args)
    h2 = compute_content_hash(*args)
    assert h1 == h2


def test_hash_data_order_independent():
    h1 = compute_content_hash("id", "c", "s", "published", "sc", {"a": 1, "b": 2}, "")
    h2 = compute_content_hash("id", "c", "s", "published", "sc", {"b": 2, "a": 1}, "")
    assert h1 == h2


def test_hash_nested_data_sorted():
    h1 = compute_content_hash("i", "c", "s", "p", "sc", {"z": {"y": 1, "x": 2}}, "")
    h2 = compute_content_hash("i", "c", "s", "p", "sc", {"z": {"x": 2, "y": 1}}, "")
    assert h1 == h2


def test_hash_body_normalization_affects_hash_consistently():
    h_lf = compute_content_hash("i", "c", "s", "p", "sc", {}, "a\nb")
    h_crlf = compute_content_hash("i", "c", "s", "p", "sc", {}, "a\r\nb")
    assert h_lf == h_crlf


def test_hash_differs_by_id():
    h1 = compute_content_hash("a", "c", "s", "p", "sc", {}, "")
    h2 = compute_content_hash("b", "c", "s", "p", "sc", {}, "")
    assert h1 != h2


def test_hash_matches_expected_canonical_form():
    """Explicitly re-implement the algorithm and check byte-parity."""
    data = {"b": 2, "a": 1}
    canonical = {
        "body": "hello\n",
        "collection": "c",
        "data": {"a": 1, "b": 2},
        "id": "id",
        "schema": "sc",
        "slug": "s",
        "status": "published",
    }
    serialized = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    expected = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    got = compute_content_hash("id", "c", "s", "published", "sc", data, "hello")
    assert got == expected


def test_hash_hex_lowercase():
    h = compute_content_hash("a", "b", "c", "d", "e", {}, "")
    assert h == h.lower()
    assert len(h) == 64
