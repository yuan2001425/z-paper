import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, UniqueConstraint
from app.database import Base


class GlossaryStatus:
    TRANSLATE = "translate"
    NEVER_TRANSLATE = "never_translate"
    TRANSLATE_WITH_ANNOTATION = "translate_with_annotation"


class UserGlossary(Base):
    __tablename__ = "glossaries"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    foreign_term = Column(String(300), nullable=False)
    zh_term = Column(String(300))
    source_language = Column(String(20), nullable=False, server_default='en')
    domain = Column(String(100), nullable=True)
    status = Column(String(30), default=GlossaryStatus.TRANSLATE)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("foreign_term", "source_language", name="uq_term_lang"),
    )
