"""Integration tests against a real S3-compatible server (MinIO in CI).

Run with MEDIA_TEST_S3_ENDPOINT=http://localhost:9000 (and MEDIA_TEST_S3_ACCESS_KEY /
MEDIA_TEST_S3_SECRET_KEY if not the MinIO defaults). Skipped when the variable is unset.
"""

import os
import urllib.error
import urllib.request
import uuid

import pytest

from apps.media_library.storage import S3Storage

ENDPOINT = os.environ.get("MEDIA_TEST_S3_ENDPOINT", "")

pytestmark = [
    pytest.mark.minio,
    pytest.mark.skipif(not ENDPOINT, reason="MEDIA_TEST_S3_ENDPOINT is not set"),
]


@pytest.fixture(scope="module")
def s3():
    storage = S3Storage(
        {
            "ENDPOINT_URL": ENDPOINT,
            "PUBLIC_ENDPOINT_URL": ENDPOINT,
            "BUCKET": f"test-{uuid.uuid4().hex[:12]}",
            "REGION": "us-east-1",
            "ACCESS_KEY_ID": os.environ.get("MEDIA_TEST_S3_ACCESS_KEY", "minioadmin"),
            "SECRET_ACCESS_KEY": os.environ.get("MEDIA_TEST_S3_SECRET_KEY", "minioadmin"),
        }
    )
    storage._client.create_bucket(Bucket=storage.bucket)
    yield storage
    keys = [
        o["Key"] for o in storage._client.list_objects_v2(Bucket=storage.bucket).get("Contents", [])
    ]
    storage.delete(keys)
    storage._client.delete_bucket(Bucket=storage.bucket)


def _browser_post(post, data: bytes, content_type: str) -> int:
    """A multipart/form-data POST exactly as the browser sends it: fields first, file last."""
    boundary = uuid.uuid4().hex
    fields = {**post.fields, "Content-Type": content_type}
    body = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields.items()
    )
    body += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="x"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    body += data + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        post.url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_presigned_post_upload_head_and_delete(s3):
    key = f"talent.gallery/{uuid.uuid4()}"
    post = s3.presign_post(key, "image/png", max_bytes=1024, ttl=60)
    assert _browser_post(post, b"\x89PNG fake bytes", "image/png") == 204
    info = s3.head(key)
    assert info.size == len(b"\x89PNG fake bytes") and info.etag
    assert s3.get(key, byte_range=(0, 3)) == b"\x89PNG"
    s3.delete([key])
    assert s3.head(key) is None


def test_policy_refuses_other_content_type_and_oversize(s3):
    key = f"talent.gallery/{uuid.uuid4()}"
    post = s3.presign_post(key, "image/png", max_bytes=8, ttl=60)
    assert _browser_post(post, b"abc", "audio/mpeg") == 403
    assert _browser_post(post, b"0123456789", "image/png") in (400, 403)
    assert s3.head(key) is None


def test_put_and_signed_get(s3):
    key = f"thumbs/{uuid.uuid4()}/400.webp"
    s3.put(key, b"webp-bytes", "image/webp")
    with urllib.request.urlopen(s3.presign_get(key, ttl=60), timeout=10) as response:
        assert response.read() == b"webp-bytes"
