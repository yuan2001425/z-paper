import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, UniqueConstraint
from app.database import Base


class DomainGlossary(Base):
    __tablename__ = "domain_glossaries"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    domain = Column(String(100), nullable=False, index=True)
    en_term = Column(String(300), nullable=False)
    zh_term = Column(String(300), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("domain", "en_term", name="uq_domain_term"),
    )
