"""File storage.

Two backends behind one interface:

| STORAGE_BACKEND | Implementation   | Where the bytes live                       |
|-----------------|------------------|--------------------------------------------|
| `local`         | `LocalStorage`   | a directory on disk (development)          |
| `azure`         | `AzureBlobStorage` | a Blob container (Azurite, or real Azure) |

The interface is deliberately tiny so the callers - `jobs.py` and `api/investigations.py` -
never learn which one is in use. Swapping backends is one environment variable.
"""
from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import get_settings


class Storage(Protocol):
    """Keys look like `videos/INV-0001/clip.mp4` - forward slashes, no leading slash."""

    def put_file(self, key: str, src: Path) -> str: ...

    def put_bytes(self, key: str, data: bytes) -> str: ...

    def get_bytes(self, key: str) -> bytes: ...


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


class AzureBlobStorage:
    """Stores blobs in a Blob container.

    Azurite and real Azure differ only in the connection string, so the same code path is
    exercised locally and in the cloud. That is the whole point of Phase 3.
    """

    def __init__(self, connection_string: str, container: str) -> None:
        from azure.storage.blob import BlobServiceClient

        self.container_name = container
        self._client = BlobServiceClient.from_connection_string(
            connection_string
        ).get_container_client(container)
        self._ready = False

    def _ensure_container(self) -> None:
        """Create the container on first use.

        Lazy on purpose: building the client must not require the emulator to be running,
        so the app can start before Azurite finishes booting.
        """
        if self._ready:
            return

        from azure.core.exceptions import ResourceExistsError

        try:
            self._client.create_container()
        except ResourceExistsError:
            pass  # someone else created it, or a previous run did
        self._ready = True

    def put_file(self, key: str, src: Path) -> str:
        self._ensure_container()
        with src.open("rb") as fh:  # handed to the SDK as a stream, not read into memory
            self._client.upload_blob(name=key, data=fh, overwrite=True)
        return key

    def put_bytes(self, key: str, data: bytes) -> str:
        self._ensure_container()
        self._client.upload_blob(name=key, data=data, overwrite=True)
        return key

    def get_bytes(self, key: str) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._client.download_blob(key).readall()
        except ResourceNotFoundError as exc:
            # Callers only depend on the exception *type*: the evidence endpoint turns
            # FileNotFoundError into a 404. Azure raises its own, so translate it here.
            raise FileNotFoundError(key) from exc


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()

    if settings.storage_backend == "local":
        return LocalStorage(Path(settings.local_storage_root))

    if settings.storage_backend == "azure":
        if not settings.storage_connection:
            raise RuntimeError(
                "STORAGE_BACKEND=azure needs STORAGE_CONNECTION "
                "(an Azurite or Azure Storage connection string)"
            )
        return AzureBlobStorage(settings.storage_connection, settings.storage_container)

    raise ValueError(
        f"unknown STORAGE_BACKEND={settings.storage_backend!r} (local | azure)"
    )
