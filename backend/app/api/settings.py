"""
settings.py — 运行时配置 API

端点：
  GET  /api/v1/settings        — 获取当前配置（key 脱敏显示）
  PUT  /api/v1/settings        — 保存配置到 DB，立即生效
  GET  /api/v1/settings/status — 检查必填 key 是否已配置
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import ADVANCED_KEYS, REQUIRED_KEYS, apply_db_config, settings
from app.database import get_db
from app.models.app_config import AppConfig

router = APIRouter()


def _mask(value: str) -> str:
    """脱敏：只保留前 4 位，其余替换为 *"""
    if not value:
        return ""
    return value[:4] + "*" * max(0, len(value) - 4)


def _default_of(key: str) -> str:
    """从 Settings 模型字段读取默认值（不受 DB 覆盖影响）"""
    field = settings.__class__.model_fields.get(key)
    if field is None:
        return ""
    default = field.default
    if default is None:
        return ""
    return str(default)


# ── 读取当前配置 ──────────────────────────────────────────────────────────────

@router.get("/")
def get_settings(db: Session = Depends(get_db)):
    rows = {r.key: r.value for r in db.query(AppConfig).all()}

    # 密钥（脱敏显示）
    keys = {}
    for key, label in REQUIRED_KEYS.items():
        # 优先显示 DB 里的值，其次 settings 里的值（来自 .env）
        raw = rows.get(key) or getattr(settings, key, "")
        keys[key] = {
            "label":      label,
            "masked":     _mask(raw),
            "configured": bool(raw),
        }

    # 模型名 / API 地址（明文显示）
    advanced = {}
    for key, label in ADVANCED_KEYS.items():
        default = _default_of(key)
        current = rows.get(key) if key in rows else default
        advanced[key] = {
            "label":    label,
            "value":    current,
            "default":  default,
            "modified": key in rows and rows[key] != default,
        }

    return {"keys": keys, "advanced": advanced}


# ── 保存配置 ──────────────────────────────────────────────────────────────────

class SettingsPayload(BaseModel):
    # 密钥
    DEEPSEEK_API_KEY: str = ""
    QWEN_API_KEY:     str = ""
    MINERU_API_KEY:   str = ""
    # 模型名 / API 地址
    DEEPSEEK_BASE_URL:       str = ""
    DEEPSEEK_MODEL:          str = ""
    QWEN_BASE_URL:           str = ""
    QWEN_DASHSCOPE_BASE_URL: str = ""
    QWEN_VL_MODEL:           str = ""
    QWEN_IMAGE_MODEL:        str = ""
    QWEN_IMAGE_GEN_MODEL:    str = ""
    MINERU_BASE_URL:         str = ""


@router.put("/")
def update_settings(payload: SettingsPayload, db: Session = Depends(get_db)):
    updates: dict[str, str] = {
        k: v.strip()
        for k, v in payload.model_dump().items()
        if v and v.strip()  # 留空 = 不修改
    }

    for key, value in updates.items():
        row = db.query(AppConfig).filter(AppConfig.key == key).first()
        if row:
            row.value      = value
            row.updated_at = datetime.utcnow()
        else:
            db.add(AppConfig(key=key, value=value, updated_at=datetime.utcnow()))

    db.commit()

    # 立即更新内存 settings，无需重启
    apply_db_config(updates)

    return {"ok": True}


# ── 状态检查（供前端路由守卫使用）────────────────────────────────────────────

@router.get("/status")
def get_settings_status(db: Session = Depends(get_db)):
    rows = {r.key: r.value for r in db.query(AppConfig).all()}
    missing = []
    for key, label in REQUIRED_KEYS.items():
        raw = rows.get(key) or getattr(settings, key, "")
        if not raw:
            missing.append({"key": key, "label": label})
    return {
        "configured": len(missing) == 0,
        "missing":    missing,
    }
