"""Unit tests for the S3 adapter that need no server: policy shape and error mapping."""

import base64
import json

import pytest
from botocore.stub import Stubber

from apps.media_library.storage import S3Storage, StorageError

CONFIG = {
    "ENDPOINT_URL": "http://minio:9000",
    "PUBLIC_ENDPOINT_URL": "http://localhost:9000",
    "BUCKET": "bucket",
    "REGION": "ap-south-1",
    "ACCESS_KEY_ID": "key",
    "SECRET_ACCESS_KEY": "secret",
}


@pytest.fixture
def s3():
    return S3Storage(CONFIG)


def test_presigned_post_pins_key_type_and_size(s3):
    post = s3.presign_post("talent.gallery/abc", "image/png", 1024, ttl=600)
    assert post.url == "http://localhost:9000/bucket"  # the browser-facing endpoint
    assert post.fields["key"] == "talent.gallery/abc"
    assert post.fields["Content-Type"] == "image/png"
    conditions = json.loads(base64.b64decode(post.fields["policy"]))["conditions"]
    assert {"Content-Type": "image/png"} in conditions
    assert ["content-length-range", 1, 1024] in conditions
    assert {"key": "talent.gallery/abc"} in conditions
    assert {"bucket": "bucket"} in conditions


def test_signed_get_uses_the_public_endpoint(s3):
    url = s3.presign_get("talent.gallery/abc", ttl=900)
    assert url.startswith("http://localhost:9000/bucket/talent.gallery/abc?")
    assert "X-Amz-Expires=900" in url


def test_head_of_a_missing_object_is_none(s3):
    with Stubber(s3._client) as stub:
        stub.add_client_error("head_object", http_status_code=404, service_error_code="404")
        assert s3.head("missing") is None


def test_head_returns_size_and_unquoted_etag(s3):
    with Stubber(s3._client) as stub:
        stub.add_response(
            "head_object",
            {"ContentLength": 12, "ETag": '"abc"', "ContentType": "image/png"},
            {"Bucket": "bucket", "Key": "k"},
        )
        info = s3.head("k")
    assert (info.size, info.etag, info.content_type) == (12, "abc", "image/png")


def test_other_failures_raise_storage_error(s3):
    with Stubber(s3._client) as stub:
        stub.add_client_error("head_object", http_status_code=500, service_error_code="Internal")
        stub.add_client_error("delete_objects", http_status_code=503, service_error_code="Slow")
        with pytest.raises(StorageError):
            s3.head("k")
        with pytest.raises(StorageError):
            s3.delete(["k"])


def test_delete_reports_per_key_errors(s3):
    with Stubber(s3._client) as stub:
        stub.add_response("delete_objects", {"Errors": [{"Key": "k", "Code": "AccessDenied"}]})
        with pytest.raises(StorageError):
            s3.delete(["k"])
