"""The storage interface, exercised against whichever backend is configured.

Runs against LocalStorage by default, and against Azurite when STORAGE_BACKEND=azure. The
same tests passing on both is the proof that the two implementations are interchangeable:

    pytest tests                              # SQLite  + local files
    STORAGE_BACKEND=azure ... pytest tests    # Postgres + Azurite
"""
from __future__ import annotations

import pytest

from app.storage import get_storage


@pytest.fixture
def storage():
    return get_storage()


def test_put_and_get_bytes(storage):
    storage.put_bytes("tests/hello.txt", b"deeptrace")
    assert storage.get_bytes("tests/hello.txt") == b"deeptrace"


def test_put_file_returns_the_key(storage, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00\x01\x02\x03" * 100)

    assert storage.put_file("tests/clip.mp4", src) == "tests/clip.mp4"
    assert storage.get_bytes("tests/clip.mp4") == src.read_bytes()


def test_overwriting_a_key_is_allowed(storage):
    # analysis re-runs on the same investigation id, so overwrite must not fail
    storage.put_bytes("tests/overwrite.txt", b"first")
    storage.put_bytes("tests/overwrite.txt", b"second")
    assert storage.get_bytes("tests/overwrite.txt") == b"second"


def test_a_missing_key_raises_filenotfound(storage):
    # the evidence endpoint relies on this exact exception to return a 404 rather than a 500
    with pytest.raises(FileNotFoundError):
        storage.get_bytes("tests/definitely-not-here-9f3a2b.bin")


def test_keys_may_be_nested(storage):
    key = "videos/INV-9999/nested/clip.mp4"
    storage.put_bytes(key, b"nested")
    assert storage.get_bytes(key) == b"nested"
