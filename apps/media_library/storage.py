"""Object storage adapter. The only module that knows about boto3.

Bytes never pass through Django on upload: the browser POSTs straight to a private bucket with a
pre-signed form, and reads use short-lived signed GET URLs. Everything else in the codebase calls
the module-level functions below, which delegate to the backend named by
`settings.MEDIA_STORAGE_BACKEND` (S3-compatible in every environment; an in-memory fake in tests).
"""

from dataclasses import dataclass
from functools import cache
from typing import Protocol

from django.conf import settings
from django.utils.module_loading import import_string


class StorageError(Exception):
    """The storage service failed or was unreachable. Safe to retry."""


@dataclass(frozen=True)
class PresignedPost:
    url: str
    fields: dict[str, str]


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    etag: str
    content_type: str


class Storage(Protocol):
    def presign_post(
        self, key: str, content_type: str, max_bytes: int, ttl: int
    ) -> PresignedPost: ...
    def head(self, key: str) -> ObjectInfo | None: ...
    def get(self, key: str, byte_range: tuple[int, int] | None = None) -> bytes: ...
    def put(self, key: str, data: bytes, content_type: str) -> ObjectInfo: ...
    def delete(self, keys: list[str]) -> None: ...
    def presign_get(self, key: str, ttl: int) -> str: ...


class S3Storage:
    """Any S3-compatible service: AWS S3 in staging/production, MinIO locally.

    `endpoint_url` is set only for MinIO. `public_endpoint_url` is the address the browser uses
    when it differs from the one the API reaches (docker: `minio:9000` vs `localhost:9000`);
    signed URLs are generated against it so the signature matches the host the browser calls.
    """

    _DELETE_BATCH = 1000  # S3 DeleteObjects limit

    def __init__(self, config: dict | None = None):
        config = {**settings.MEDIA_STORAGE, **(config or {})}
        self.bucket = config["BUCKET"]
        self._client = self._make_client(config, config.get("ENDPOINT_URL") or None)
        public = config.get("PUBLIC_ENDPOINT_URL") or config.get("ENDPOINT_URL") or None
        self._signer = self._make_client(config, public)

    @staticmethod
    def _make_client(config: dict, endpoint_url: str | None):
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=config.get("REGION") or None,
            aws_access_key_id=config.get("ACCESS_KEY_ID") or None,
            aws_secret_access_key=config.get("SECRET_ACCESS_KEY") or None,
            config=Config(
                signature_version="s3v4",
                # MinIO serves buckets by path; AWS works either way.
                s3={"addressing_style": "path" if endpoint_url else "auto"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=30,
            ),
        )

    def _call(self, operation, *args, **kwargs):
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return operation(*args, **kwargs)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(str(exc)) from exc

    def presign_post(self, key, content_type, max_bytes, ttl):
        # boto adds the exact bucket and key conditions itself.
        post = self._call(
            self._signer.generate_presigned_post,
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, max_bytes]],
            ExpiresIn=ttl,
        )
        return PresignedPost(url=post["url"], fields=dict(post["fields"]))

    def head(self, key):
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise StorageError(str(exc)) from exc
        except BotoCoreError as exc:
            raise StorageError(str(exc)) from exc
        return ObjectInfo(
            size=response["ContentLength"],
            etag=response.get("ETag", "").strip('"'),
            content_type=response.get("ContentType", ""),
        )

    def get(self, key, byte_range=None):
        kwargs = {"Bucket": self.bucket, "Key": key}
        if byte_range is not None:
            kwargs["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
        response = self._call(self._client.get_object, **kwargs)
        return self._call(response["Body"].read)

    def put(self, key, data, content_type):
        response = self._call(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return ObjectInfo(
            size=len(data), etag=response.get("ETag", "").strip('"'), content_type=content_type
        )

    def delete(self, keys):
        keys = list(dict.fromkeys(k for k in keys if k))
        for start in range(0, len(keys), self._DELETE_BATCH):
            batch = keys[start : start + self._DELETE_BATCH]
            response = self._call(
                self._client.delete_objects,
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
            if response.get("Errors"):  # a missing key is not an error in S3
                raise StorageError(f"Could not delete {len(response['Errors'])} object(s).")

    def presign_get(self, key, ttl):
        return self._call(
            self._signer.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=ttl,
        )


@cache
def get_storage() -> Storage:
    return import_string(settings.MEDIA_STORAGE_BACKEND)()


def presign_post(key: str, content_type: str, max_bytes: int, ttl: int) -> PresignedPost:
    return get_storage().presign_post(key, content_type, max_bytes, ttl)


def head(key: str) -> ObjectInfo | None:
    return get_storage().head(key)


def get(key: str, byte_range: tuple[int, int] | None = None) -> bytes:
    return get_storage().get(key, byte_range)


def put(key: str, data: bytes, content_type: str) -> ObjectInfo:
    return get_storage().put(key, data, content_type)


def delete(keys: list[str]) -> None:
    get_storage().delete(keys)


def presign_get(key: str, ttl: int) -> str:
    return get_storage().presign_get(key, ttl)
