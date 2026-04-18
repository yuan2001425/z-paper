import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON
from app.database import Base


class JobGlossarySnapshot(Base):
    """记录本次翻译使用的术语快照，用于复现和审计"""
    __tablename__ = "job_glossary_snapshots"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String(36), nullable=False, unique=True, index=True)
    # 格式：[{"en": "attention mechanism", "zh": "注意力机制", "status": "translate"}, ...]
    glossary_json = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
