import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON
from app.database import Base


class TranslationResult(Base):
    __tablename__ = "translation_results"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String(36), nullable=False, unique=True, index=True)
    paper_id = Column(String(36), nullable=False, index=True)
    structure_json = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
