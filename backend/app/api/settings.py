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

from app.config import REQUIRED_KEYS, apply_db_config, settings
from app.database import get_db
from app.models.app_config import AppConfig

router = APIRouter()


def _mask(value: str) -> str:
    """脱敏：只保留前 4 位，其余替换为 *"""
    if not value:
        return ""
    return value[:4] + "*" * max(0, len(value) - 4)


# ── 读取当前配置 ──────────────────────────────────────────────────────────────

@router.get("/")
def get_settings(db: Session = Depends(get_db)):
    rows = {r.key: r.value for r in db.query(AppConfig).all()}
    result = {}
    for key, label in REQUIRED_KEYS.items():
        # 优先显示 DB 里的值，其次 settings 里的值（来自 .env）
        raw = rows.get(key) or getattr(settings, key, "")
        result[key] = {
            "label":      label,
            "masked":     _mask(raw),
            "configured": bool(raw),
        }
    return result


# ── 保存配置 ──────────────────────────────────────────────────────────────────

class SettingsPayload(BaseModel):
    DEEPSEEK_API_KEY: str = ""
    QWEN_API_KEY:     str = ""
    MINERU_API_KEY:   str = ""


@router.put("/")
def update_settings(payload: SettingsPayload, db: Session = Depends(get_db)):
    updates: dict[str, str] = {
        "DEEPSEEK_API_KEY": payload.DEEPSEEK_API_KEY.strip(),
        "QWEN_API_KEY":     payload.QWEN_API_KEY.strip(),
        "MINERU_API_KEY":   payload.MINERU_API_KEY.strip(),
    }

    for key, value in updates.items():
        if not value:
            continue  # 留空 = 不修改
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
