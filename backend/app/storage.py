"""File storage.

Two backends behind one interface:

| STORAGE_BACKEND | Implementation     | Where the bytes live                          |
|-----------------|--------------------|-----------------------------------------------|
| `local`         | `LocalStorage`     | a directory on disk (development)             |
| `azure`         | `AzureBlobStorage` | a Blob container (Azurite, or real Azure)     |
| `s3`            | `S3Storage`        | an S3 bucket (MinIO, or real AWS)             |

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

        from azure.core.exceptions import HttpResponseError, ResourceExistsError

        try:
            self._client.create_container()
        except ResourceExistsError:
            pass  # someone else created it, or a previous run did
        except HttpResponseError as exc:
            # Same reasoning as S3Storage._ensure_bucket: a credential scoped so it cannot
            # create a container is normal in the cloud, where the deployment makes it, and
            # it must not turn every upload into a 500.
            if exc.status_code not in (401, 403):
                raise
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


class S3Storage:
    """Stores blobs in an S3 bucket.

    AWS and MinIO differ only in endpoint URL and credentials, so - like the Azure pair -
    the same code path is exercised locally and in the cloud.

    In AWS no credentials are configured at all: boto3 picks up the ECS task role, so there
    is no long-lived key anywhere in the image or the environment.
    """

    def __init__(
        self,
        bucket: str,
        region: str,
        endpoint_url: str = "",
        access_key: str = "",
        secret_key: str = "",
    ) -> None:
        import boto3

        self.bucket = bucket
        self.region = region

        kwargs: dict = {"region_name": region}
        if endpoint_url:
            # MinIO serves bucket-in-path URLs; without this boto3 tries
            # <bucket>.localhost, which does not resolve.
            from botocore.config import Config

            kwargs["endpoint_url"] = endpoint_url
            kwargs["config"] = Config(s3={"addressing_style": "path"})
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key

        self._client = boto3.client("s3", **kwargs)
        self._ready = False

    def _ensure_bucket(self) -> None:
        """Create the bucket on first use, so start-up does not require S3 to be up.

        Best-effort, and that word is load-bearing. Locally the emulator needs the bucket
        made for it, but in AWS the deployment creates the bucket and the task role
        deliberately has no `s3:CreateBucket` - so treating a denial as a failure made every
        single upload return 500 for the sake of a convenience only local development needs.
        A denial therefore means "assume it is already there"; if it genuinely is not, the
        PutObject that follows says so plainly.
        """
        if self._ready:
            return

        from botocore.exceptions import ClientError

        try:
            if self.region == "us-east-1":
                # the only region that rejects an explicit LocationConstraint
                self._client.create_bucket(Bucket=self.bucket)
            else:
                self._client.create_bucket(
                    Bucket=self.bucket,
                    CreateBucketConfiguration={"LocationConstraint": self.region},
                )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists", "AccessDenied"):
                raise
        self._ready = True

    def put_file(self, key: str, src: Path) -> str:
        self._ensure_bucket()
        # upload_file handles multipart for large videos; put_object would buffer it all
        self._client.upload_file(str(src), self.bucket, key)
        return key

    def put_bytes(self, key: str, data: bytes) -> str:
        self._ensure_bucket()
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def get_bytes(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "NoSuchBucket", "404"):
                # Callers only depend on the exception *type*: the evidence endpoint turns
                # FileNotFoundError into a 404. S3 raises its own, so translate it here.
                raise FileNotFoundError(key) from exc
            raise


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

    if settings.storage_backend == "s3":
        return S3Storage(
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
        )

    raise ValueError(
        f"unknown STORAGE_BACKEND={settings.storage_backend!r} (local | azure | s3)"
    )
