import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text
from app.database import Base


class Annotation(Base):
    __tablename__ = "annotations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    result_id = Column(String(36), nullable=False, index=True)
    scope = Column(String(10), default="global")   # "global" | "inline"
    content = Column(Text, nullable=False)
    block_id = Column(String(100))
    start_offset = Column(Integer)
    end_offset = Column(Integer)
    selected_text = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
