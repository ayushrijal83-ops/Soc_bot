"""S3-compatible object storage (AWS S3, Cloudflare R2, MinIO, ...) via boto3.

The bucket stays private: objects are uploaded without ACLs and read only through presigned
GET URLs (SigV4) with a short expiry.
"""

from pathlib import Path
from urllib.parse import urlparse

from src.media_storage.base import MediaStorageError, ObjectStorage, StorageObject

# S3 limit for presigned URLs (SigV4): 7 days.
MAX_PRESIGN_SECONDS = 7 * 24 * 3600


def _safe_error(action: str, exc: Exception) -> MediaStorageError:
    """Error text without request URLs, signatures or credentials: error code + HTTP status only."""
    code, status = None, None
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code")
        status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    detail = code or type(exc).__name__
    # No HTTP status = network/endpoint error; 5xx / 429 / SlowDown = transient.
    retryable = status is None or status >= 500 or status == 429 or code in ("SlowDown", "RequestTimeout")
    return MediaStorageError(f"{action} failed: {detail}" + (f" (HTTP {status})" if status else ""), retryable=retryable)


class S3ObjectStorage(ObjectStorage):
    provider = "s3"

    def __init__(
        self,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str | None = None,
        endpoint: str | None = None,
        client=None,
    ):
        self.bucket = bucket
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region or None,
                endpoint_url=endpoint or None,
                config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
            )
        self.client = client

    def upload_file(self, file_path: Path, object_key: str, content_type: str) -> StorageObject:
        from boto3.s3.transfer import TransferConfig

        path = Path(file_path)
        try:
            size = path.stat().st_size
            # upload_file streams from disk; files above the threshold go as multipart chunks.
            self.client.upload_file(
                str(path), self.bucket, object_key,
                ExtraArgs={"ContentType": content_type},
                Config=TransferConfig(multipart_threshold=64 * 1024 * 1024, multipart_chunksize=16 * 1024 * 1024),
            )
        except OSError as e:
            raise MediaStorageError(f"Temporary media upload failed: local file unreadable ({type(e).__name__})") from e
        except Exception as e:
            raise _safe_error("Temporary media upload", e) from e
        return StorageObject(key=object_key, size=size, content_type=content_type)

    def create_presigned_url(self, object_key: str, expires_in: int) -> str:
        expires_in = max(1, min(int(expires_in), MAX_PRESIGN_SECONDS))
        try:
            url = self.client.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": object_key}, ExpiresIn=expires_in
            )
        except Exception as e:
            raise _safe_error("Presigned URL creation", e) from e
        if urlparse(url).scheme != "https":
            raise MediaStorageError("Storage endpoint must be HTTPS: Instagram only fetches media over HTTPS")
        return url

    def delete_object(self, object_key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=object_key)
        except Exception as e:
            raise _safe_error("Temporary media deletion", e) from e

    def health_check(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception as e:
            raise _safe_error("Storage bucket check", e) from e
