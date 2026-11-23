from datetime import datetime
from pathlib import Path
from pydantic_settings import BaseSettings

# 无论从哪个目录启动 uvicorn，都解析到 backend/ 目录的绝对路径
_BACKEND_DIR = Path(__file__).resolve().parent.parent  # app/ → backend/
_DATA_DIR = _BACKEND_DIR / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    # Database — SQLite 单文件，无需安装任何数据库服务
    DATABASE_URL: str = f"sqlite:///{_DATA_DIR / 'zpaper.db'}"

    # 本地文件存储
    LOCAL_UPLOAD_PATH: str = str(_BACKEND_DIR / "uploads")

    # AI APIs
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    QWEN_API_KEY: str = ""
    QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_VL_MODEL: str = "qwen3.8-flash"
    QWEN_IMAGE_MODEL: str = "qwen-image-2.0-pro"
    QWEN_IMAGE_GEN_MODEL: str = "qwen-image-2.0"
    QWEN_TEXT_MODEL: str = "qwen3.5-flash"
    QWEN_DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/api/v1"

    MINERU_API_KEY: str = ""
    MINERU_BASE_URL: str = "https://mineru.net/api/v4"

    # Translation
    POLISH_CHUNK_SIZE: int = 5000
    TRANSLATE_CHUNK_SIZE: int = 5000

    class Config:
        env_file = (".env", "../.env")   # 有则加载，无则忽略（pydantic_settings 默认行为）
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()


# 需要用户配置的 key 及其说明（用于前端展示和状态检查）
REQUIRED_KEYS: dict[str, str] = {
    "DEEPSEEK_API_KEY": "DeepSeek API Key（翻译 / 知识库对话）",
    "QWEN_API_KEY":     "通义千问 API Key（图片识别 / 文本处理）",
    "MINERU_API_KEY":   "MinerU API Key（PDF 解析）",
}

# 模型名 / API 地址（明文显示、可修改，前端修改前需二次确认）
ADVANCED_KEYS: dict[str, str] = {
    "DEEPSEEK_BASE_URL":       "DeepSeek API 地址",
    "DEEPSEEK_MODEL":          "DeepSeek 模型名",
    "QWEN_BASE_URL":           "通义千问 OpenAI 兼容地址",
    "QWEN_DASHSCOPE_BASE_URL": "通义千问 DashScope 地址",
    "QWEN_VL_MODEL":           "通义千问视觉模型（图片 / 公式识别）",
    "QWEN_IMAGE_MODEL":        "通义千问图片编辑模型",
    "QWEN_IMAGE_GEN_MODEL":    "通义千问图片生成模型",
    "MINERU_BASE_URL":         "MinerU API 地址",
}

# 配置结构版本号：升级安装后据此重置「模型名 / API 地址」等非密钥配置（保留 API Key）。
# 每当需要强制覆盖老配置时 +1。
CONFIG_VERSION = 1


def apply_db_config(overrides: dict[str, str]) -> None:
    """将数据库中保存的配置覆盖到内存 settings 对象（进程内立即生效）"""
    for key, value in overrides.items():
        if value and hasattr(settings, key):
            object.__setattr__(settings, key, value)


def _migrate_config(db, AppConfig) -> None:
    """升级迁移：配置结构版本落后时，删除模型名/地址记录使其回落到代码默认值（保留 API Key）。"""
    row = db.query(AppConfig).filter(AppConfig.key == "_CONFIG_VERSION").first()
    current = int(row.value) if row and row.value else 0
    if current >= CONFIG_VERSION:
        return

    # 重置所有非密钥配置，让新版本默认值（如 qwen3.8-flash）生效
    for key in ADVANCED_KEYS:
        db.query(AppConfig).filter(AppConfig.key == key).delete()

    if row:
        row.value = str(CONFIG_VERSION)
        row.updated_at = datetime.utcnow()
    else:
        db.add(AppConfig(key="_CONFIG_VERSION", value=str(CONFIG_VERSION), updated_at=datetime.utcnow()))
    db.commit()


def load_db_config() -> None:
    """启动时从数据库加载配置，覆盖 .env 中的空值；并执行升级迁移"""
    try:
        from app.database import SessionLocal
        from app.models.app_config import AppConfig
        with SessionLocal() as db:
            _migrate_config(db, AppConfig)
            rows = db.query(AppConfig).all()
        apply_db_config({r.key: r.value for r in rows if r.value})
    except Exception:
        pass  # 数据库尚未初始化时忽略
