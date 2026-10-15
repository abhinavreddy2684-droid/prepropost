"""In-memory stand-in for the storage adapter, used by the test settings.

It enforces the same pre-signed POST conditions S3 does (exact key, exact Content-Type, size
range), so tests can simulate the browser upload with `simulate_upload`.
"""

import hashlib
from dataclasses import dataclass

from .storage import ObjectInfo, PresignedPost, StorageError


@dataclass
class _Upload:
    content_type: str
    max_bytes: int


class InMemoryStorage:
    url = "https://storage.test/media"

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.signed_posts: dict[str, _Upload] = {}
        self.failing = False  # set True to make every call raise StorageError
        self.calls: list[tuple[str, str]] = []

    def _record(self, operation: str, key: str) -> None:
        self.calls.append((operation, key))
        if self.failing:
            raise StorageError(f"storage is down ({operation})")

    def presign_post(self, key, content_type, max_bytes, ttl):
        self._record("presign_post", key)
        self.signed_posts[key] = _Upload(content_type, max_bytes)
        return PresignedPost(
            url=self.url,
            fields={"key": key, "Content-Type": content_type, "policy": "fake", "x-ttl": str(ttl)},
        )

    def simulate_upload(self, key: str, data: bytes, content_type: str | None = None) -> None:
        """What the browser does with the signed form; refused like S3 would refuse it."""
        signed = self.signed_posts.get(key)
        if signed is None:
            raise AssertionError(f"No signed POST was issued for {key}")
        content_type = content_type or signed.content_type
        if content_type != signed.content_type or not 1 <= len(data) <= signed.max_bytes:
            raise AssertionError("The signed POST policy would reject this upload")
        self.objects[key] = (data, content_type)

    def head(self, key):
        self._record("head", key)
        if key not in self.objects:
            return None
        data, content_type = self.objects[key]
        return ObjectInfo(len(data), hashlib.md5(data).hexdigest(), content_type)

    def get(self, key, byte_range=None):
        self._record("get", key)
        if key not in self.objects:
            raise StorageError(f"NoSuchKey: {key}")
        data = self.objects[key][0]
        return data if byte_range is None else data[byte_range[0] : byte_range[1] + 1]

    def put(self, key, data, content_type):
        self._record("put", key)
        self.objects[key] = (bytes(data), content_type)
        return ObjectInfo(len(data), hashlib.md5(data).hexdigest(), content_type)

    def delete(self, keys):
        for key in keys:
            self._record("delete", key)
        for key in keys:
            self.objects.pop(key, None)

    def presign_get(self, key, ttl):
        self._record("presign_get", key)
        return f"{self.url}/{key}?ttl={ttl}&signature=fake"
