"""
R2 Object Storage — Cloudflare R2 via S3 API.
Multi-tenant file storage with pre-signed URLs.

Storage layout:
  {org_id}/{record_type}/{record_id}/{file_id}.ext

All file access goes through pre-signed URLs (time-limited, no public bucket).
"""
import os
import boto3
from botocore.config import Config

_endpoint = os.environ.get("R2_ENDPOINT_URL")
_access_key = os.environ.get("R2_ACCESS_KEY_ID")
_secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
_bucket = os.environ.get("R2_FILES_BUCKET")


def _get_client():
    return boto3.client(
        "s3",
        endpoint_url=_endpoint,
        aws_access_key_id=_access_key,
        aws_secret_access_key=_secret_key,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},  # R2 requires virtual-host-style for presigned URLs
        ),
    )


def build_key(org_id: str, record_type: str, record_id: str, filename: str) -> str:
    """Build the S3 object key: {org_id}/{record_type}/{record_id}/{filename}"""
    return f"{org_id}/{record_type}/{record_id}/{filename}"


async def upload_file(org_id: str, record_type: str, record_id: str,
                      filename: str, content: bytes, content_type: str = "image/jpeg") -> dict:
    """Upload a file to R2. Returns the key and metadata."""
    key = build_key(org_id, record_type, record_id, filename)
    client = _get_client()
    client.put_object(
        Bucket=_bucket,
        Key=key,
        Body=content,
        ContentType=content_type,
    )
    return {"key": key, "bucket": _bucket, "size": len(content)}


async def get_presigned_url(key: str, expires_in: int = 3600) -> str:
    """Generate a pre-signed GET URL (default: 1 hour)."""
    client = _get_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket, "Key": key},
        ExpiresIn=expires_in,
    )


async def get_presigned_upload_url(key: str, content_type: str = "image/jpeg",
                                    expires_in: int = 900) -> str:
    """Generate a pre-signed PUT URL for direct browser upload (default: 15 min)."""
    client = _get_client()
    return client.generate_presigned_url(
        "put_object",
        Params={"Bucket": _bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=expires_in,
    )


async def delete_file(key: str):
    """Delete a file from R2."""
    client = _get_client()
    client.delete_object(Bucket=_bucket, Key=key)


async def file_exists(key: str) -> bool:
    """Check if a file exists in R2."""
    client = _get_client()
    try:
        client.head_object(Bucket=_bucket, Key=key)
        return True
    except Exception:
        return False
