import io
import logging
from minio import Minio
from minio.error import S3Error
from app.config import settings

logger = logging.getLogger(__name__)


class MinIOClient:
    def __init__(self):
        self.client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        self._ensure_bucket()

    def _ensure_bucket(self):
        try:
            if not self.client.bucket_exists(settings.MINIO_BUCKET):
                self.client.make_bucket(settings.MINIO_BUCKET)
                logger.info(f"Created MinIO bucket: {settings.MINIO_BUCKET}")
        except S3Error as e:
            logger.error(f"MinIO bucket init error: {e}")

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream"):
        self.client.put_object(
            settings.MINIO_BUCKET,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    def get_object(self, key: str) -> bytes:
        resp = self.client.get_object(settings.MINIO_BUCKET, key)
        return resp.read()

    def get_presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        from datetime import timedelta
        return self.client.presigned_get_object(
            settings.MINIO_BUCKET, key,
            expires=timedelta(seconds=expires_seconds),
        )

    def delete_object(self, key: str):
        self.client.remove_object(settings.MINIO_BUCKET, key)


# 单例
minio_client = MinIOClient()
