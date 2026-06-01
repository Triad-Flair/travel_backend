import logging
from typing import BinaryIO

import boto3
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)


def _get_s3_client():
    kwargs = {
        "aws_access_key_id": settings.s3_access_key,
        "aws_secret_access_key": settings.s3_secret_key,
        "region_name": settings.s3_region,
    }
    if settings.s3_endpoint:
        kwargs["endpoint_url"] = settings.s3_endpoint
    return boto3.client("s3", **kwargs)


def generate_presigned_upload_url(key: str, content_type: str, expires: int = 3600) -> str:
    client = _get_s3_client()
    url = client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.s3_bucket,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=expires,
    )
    return url


def generate_presigned_download_url(key: str, expires: int = 3600) -> str:
    client = _get_s3_client()
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=expires,
    )
    return url


def upload_file(file: BinaryIO, key: str, content_type: str) -> str:
    client = _get_s3_client()
    client.upload_fileobj(
        file,
        settings.s3_bucket,
        key,
        ExtraArgs={"ContentType": content_type, "ServerSideEncryption": "AES256"},
    )
    return key


def delete_file(key: str) -> bool:
    try:
        client = _get_s3_client()
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
        return True
    except ClientError as exc:
        logger.error("S3 delete failed for key %s: %s", key, exc)
        return False


def get_public_url(key: str) -> str:
    if settings.s3_endpoint:
        return f"{settings.s3_endpoint}/{settings.s3_bucket}/{key}"
    return f"https://{settings.s3_bucket}.s3.{settings.s3_region}.amazonaws.com/{key}"
