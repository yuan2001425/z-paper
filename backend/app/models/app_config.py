from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime
from app.database import Base


class AppConfig(Base):
    """单行 key-value 配置表，存储用户在界面上填写的 API key 等运行时配置"""
    __tablename__ = "app_config"

    key        = Column(String(100), primary_key=True)
    value      = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
