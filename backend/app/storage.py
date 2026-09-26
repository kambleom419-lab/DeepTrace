"""File storage.

Phase 1 uses a local directory. Phase 3 adds the Azure Blob implementation behind the
same three methods, so no calling code changes.
"""
from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

from app.config import get_settings


class LocalStorage:
    """Stores blobs as files under a root directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        target = (self.root / key).resolve()
        if not str(target).startswith(str(self.root)):
            raise ValueError(f"key escapes storage root: {key!r}")
        return target

    def put_file(self, key: str, src: Path) -> str:
        dst = self._resolve(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        return key

    def put_bytes(self, key: str, data: bytes) -> str:
        dst = self._resolve(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return key

    def get_bytes(self, key: str) -> bytes:
        return self._resolve(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()


@lru_cache
def get_storage() -> LocalStorage:
    settings = get_settings()
    if settings.storage_backend != "local":
        raise NotImplementedError(
            "storage_backend=%r is not implemented yet; Phase 3 adds Azure Blob. "
            "Set STORAGE_BACKEND=local for now." % settings.storage_backend
        )
    return LocalStorage(Path(settings.local_storage_root))
