import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text, Boolean
from app.database import Base


class JobStatus:
    PENDING = "pending"
    PARSING = "parsing"
    POLISHING = "polishing"
    WAITING_TERM_REVIEW = "waiting_term_review"
    TRANSLATING = "translating"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType:
    TRANSLATION = "translation"   # 外文论文翻译
    ARCHIVE     = "archive"       # 中文论文存档


class TranslationJob(Base):
    __tablename__ = "translation_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    paper_id = Column(String(36), nullable=False, index=True)
    job_type = Column(String(20), default=JobType.TRANSLATION)
    status = Column(String(50), default=JobStatus.PENDING)
    current_stage = Column(String(100))
    progress = Column(Integer, default=0)
    error_message = Column(Text)
    celery_task_id = Column(String(255))
    translate_images = Column(Boolean, default=True)
    # B.5 术语审查：暂存待审核术语 JSON，格式 [{"en": str, "zh": str}, ...]
    pending_terms = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
