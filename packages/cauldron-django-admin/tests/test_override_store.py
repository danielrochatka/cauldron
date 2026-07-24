"""Tests for UIOverrideStore (scope-aware API)."""
import os
import tempfile
from pathlib import Path

import pytest

from cauldron_django_admin.override_store import (
    ABSENT,
    EncodingError,
    FileSizeError,
    HashConflictError,
    InvalidFileError,
    InvalidScopeError,
    MissingExpectedHashError,
    TraversalError,
    UIOverrideStore,
)


@pytest.fixture()
def store(tmp_path):
    return UIOverrideStore(tmp_path)


def test_list_files_empty(store, tmp_path):
    (tmp_path / "admin").mkdir()
    result = store.list_files("admin")
    assert result == []


def test_write_and_read(store):
    store.write_file_atomic("admin", "test.css", "body { color: red; }", expected_hash=ABSENT)
    content = store.read_file("admin", "test.css")
    assert "color: red" in content


def test_list_files_after_write(store):
    store.write_file_atomic("admin", "test.css", "/* hello */", expected_hash=ABSENT)
    files = store.list_files("admin")
    assert "test.css" in files


def test_traversal_rejected(store):
    with pytest.raises((TraversalError, InvalidFileError, FileNotFoundError)):
        store.read_file("admin", "../escape.css")


def test_traversal_in_write_rejected(store):
    with pytest.raises((TraversalError, InvalidFileError)):
        store.write_file_atomic("admin", "../escape.css", "body {}", expected_hash=ABSENT)


def test_symlink_escape_rejected(store, tmp_path):
    # Create a target outside the root
    outside = tmp_path.parent / "outside_dir"
    outside.mkdir(exist_ok=True)
    target_file = outside / "secret.css"
    target_file.write_text("secret", encoding="utf-8")

    admin_dir = tmp_path / "admin"
    admin_dir.mkdir(exist_ok=True)
    symlink = admin_dir / "symlink.css"
    symlink.symlink_to(target_file)

    with pytest.raises(TraversalError):
        store.read_file("admin", "symlink.css")


def test_non_css_rejected(store):
    with pytest.raises(InvalidFileError):
        store.read_file("admin", "file.txt")

    with pytest.raises(InvalidFileError):
        store.write_file_atomic("admin", "file.txt", "body {}", expected_hash=ABSENT)


def test_invalid_scope_rejected(store):
    with pytest.raises(InvalidScopeError):
        store.read_file("badscope", "test.css")

    with pytest.raises(InvalidScopeError):
        store.list_files("badscope")


def test_missing_expected_hash_raises(store):
    with pytest.raises(MissingExpectedHashError):
        store.write_file_atomic("admin", "test.css", "body {}", expected_hash=None)


def test_absent_sentinel_for_new_files(store):
    # ABSENT expected → new file must not exist
    store.write_file_atomic("admin", "test.css", "body { color: blue; }", expected_hash=ABSENT)
    content = store.read_file("admin", "test.css")
    assert "blue" in content


def test_absent_raises_if_file_exists(store):
    store.write_file_atomic("admin", "test.css", "body { color: blue; }", expected_hash=ABSENT)
    with pytest.raises(HashConflictError):
        store.write_file_atomic("admin", "test.css", "body { color: red; }", expected_hash=ABSENT)


def test_hash_conflict(store):
    store.write_file_atomic("admin", "test.css", "body { color: blue; }", expected_hash=ABSENT)
    wrong_hash = "a" * 64
    with pytest.raises(HashConflictError):
        store.write_file_atomic("admin", "test.css", "body { color: red; }", expected_hash=wrong_hash)


def test_correct_hash_allows_write(store):
    store.write_file_atomic("admin", "test.css", "body { color: blue; }", expected_hash=ABSENT)
    correct_hash = store.calculate_hash("admin", "test.css")
    new_hash = store.write_file_atomic("admin", "test.css", "body { color: green; }", expected_hash=correct_hash)
    content = store.read_file("admin", "test.css")
    assert "green" in content
    assert len(new_hash) == 64  # SHA-256 hex


def test_delete_file(store):
    store.write_file_atomic("admin", "test.css", "body {}", expected_hash=ABSENT)
    file_hash = store.calculate_hash("admin", "test.css")
    store.delete_file_atomic("admin", "test.css", file_hash)
    with pytest.raises(FileNotFoundError):
        store.read_file("admin", "test.css")


def test_delete_with_wrong_hash(store):
    store.write_file_atomic("admin", "test.css", "body {}", expected_hash=ABSENT)
    with pytest.raises(HashConflictError):
        store.delete_file_atomic("admin", "test.css", "wrong" * 12 + "1234")


def test_utf8_enforced(store, tmp_path):
    # Write non-UTF8 bytes directly to a file
    admin_dir = tmp_path / "admin"
    admin_dir.mkdir(exist_ok=True)
    bad_file = admin_dir / "bad.css"
    bad_file.write_bytes(b"\xff\xfe body {}")

    with pytest.raises(EncodingError):
        store.read_file("admin", "bad.css")


def test_file_not_found(store):
    with pytest.raises(FileNotFoundError):
        store.read_file("admin", "nonexistent.css")


def test_calculate_hash_consistent(store):
    content = "body { color: red; }"
    store.write_file_atomic("admin", "test.css", content, expected_hash=ABSENT)
    hash1 = store.calculate_hash("admin", "test.css")
    hash2 = store.calculate_hash("admin", "test.css")
    assert hash1 == hash2
    assert len(hash1) == 64


def test_pages_scope_isolated_from_admin(store):
    store.write_file_atomic("admin", "test.css", "/* admin */", expected_hash=ABSENT)
    store.write_file_atomic("pages", "test.css", "/* pages */", expected_hash=ABSENT)
    admin_content = store.read_file("admin", "test.css")
    pages_content = store.read_file("pages", "test.css")
    assert "admin" in admin_content
    assert "pages" in pages_content


# ---------------------------------------------------------------------------
# validate_target / inspect_state — public APIs
# ---------------------------------------------------------------------------


def test_inspect_state_absent(store):
    """inspect_state on a missing file returns exists=False and no hash."""
    state = store.inspect_state("admin", "nope.css")
    assert state == {"exists": False, "hash": None, "size": None}


def test_inspect_state_present(store):
    """inspect_state returns exists=True with the correct hash and size."""
    body = "body { color: teal; }"
    store.write_file_atomic("admin", "hello.css", body, expected_hash=ABSENT)
    state = store.inspect_state("admin", "hello.css")
    assert state["exists"] is True
    assert state["size"] == len(body.encode("utf-8"))
    assert len(state["hash"]) == 64  # sha256 hex


def test_validate_target_rejects_scope_prefix(store):
    """validate_target rejects non-CSS or otherwise invalid targets."""
    with pytest.raises(InvalidFileError):
        store.validate_target("admin", "foo.txt")


def test_validate_target_ok_for_missing_file(store):
    """validate_target does not require the file to exist."""
    store.validate_target("admin", "does-not-exist-yet.css")


def test_parallel_writes_total_size(store, tmp_path):
    """When writes together exceed the total budget, later writes must fail."""
    from cauldron_django_admin.override_store import (
        MAX_TOTAL_BYTES, MAX_FILE_BYTES, FileSizeError,
    )

    # Fill up to just under the total-root budget with per-file-limit writes.
    per_file = MAX_FILE_BYTES - 100
    payload = "a" * per_file
    written = 0
    idx = 0
    while written + per_file < MAX_TOTAL_BYTES:
        store.write_file_atomic(
            "admin", f"file{idx}.css", payload, expected_hash=ABSENT,
        )
        written += per_file
        idx += 1

    # Now one final write should push us over the total limit.
    with pytest.raises(FileSizeError):
        store.write_file_atomic(
            "admin", f"file{idx}.css", payload, expected_hash=ABSENT,
        )


def test_write_file_absent_sentinel(store):
    """ABSENT succeeds only for new files; existing files must use their hash."""
    store.write_file_atomic("admin", "x.css", "body{}", expected_hash=ABSENT)
    # Second ABSENT — file now exists → conflict.
    with pytest.raises(HashConflictError):
        store.write_file_atomic("admin", "x.css", "body{color:red}", expected_hash=ABSENT)


def test_public_constants_exposed():
    from cauldron_django_admin.override_store import MAX_FILE_BYTES, MAX_TOTAL_BYTES
    assert MAX_FILE_BYTES == 256 * 1024
    assert MAX_TOTAL_BYTES == 2 * 1024 * 1024
