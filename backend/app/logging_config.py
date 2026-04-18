"""
统一日志配置

用法：
  在 main.py 顶部调用 setup_logging("api")
  在 celery_app.py 通过 Celery signals 调用 setup_logging("worker")

日志文件写入 backend/logs/，按大小滚动（10MB × 10份）。
"""

import logging
import logging.handlers
from pathlib import Path


def setup_logging(service: str = "api") -> None:
    """
    配置日志同时输出到控制台和文件。

    service: "api"（FastAPI 进程）或 "worker"（Celery worker 进程）
    """
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    class CompactFormatter(logging.Formatter):
        """WARNING 只保留前 80 字，ERROR 及以上保留完整。"""
        _BASE = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
        _FMT  = logging.Formatter(_BASE, datefmt="%Y-%m-%d %H:%M:%S")

        def format(self, record: logging.LogRecord) -> str:
            result = self._FMT.format(record)
            if record.levelno < logging.ERROR:
                result = result[:80]
            return result

    # ── 滚动文件 Handler（UTF-8，10MB × 10份）────────────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_dir / f"{service}.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(CompactFormatter())
    file_handler.setLevel(logging.WARNING)

    # ── 控制台 Handler ────────────────────────────────────────────────────────
    console_fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_fmt)
    console_handler.setLevel(logging.INFO)

    # ── Root logger ───────────────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # 清除已有 handler，防止重复
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # ── 降噪：第三方库只保留 WARNING 及以上 ───────────────────────────────────
    for noisy in ("httpx", "httpcore", "uvicorn.access", "sqlalchemy.engine",
                  "multipart", "PIL", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
