import os
import shutil
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


class LocalStorage:
    """本地文件系统存储，替代 MinIO，适合单机开发环境"""

    def __init__(self):
        self.base_path = Path(settings.LOCAL_UPLOAD_PATH)
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"LocalStorage initialized at: {self.base_path.resolve()}")

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream"):
        """写入文件，key 作为相对路径（如 papers/uuid/file.pdf）"""
        target = self.base_path / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get_object(self, key: str) -> bytes:
        """读取文件"""
        target = self.base_path / key
        if not target.exists():
            raise FileNotFoundError(f"File not found: {key}")
        return target.read_bytes()

    def get_file_path(self, key: str) -> Path:
        """返回文件的绝对路径（供 FastAPI FileResponse 使用）"""
        return self.base_path / key

    def delete_object(self, key: str):
        """删除文件"""
        target = self.base_path / key
        if target.exists():
            target.unlink()

    def get_url(self, key: str) -> str:
        """返回可访问的 URL（通过 FastAPI 静态文件路由）"""
        return f"/uploads/{key}"


# 单例
local_storage = LocalStorage()
