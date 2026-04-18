import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Boolean, DateTime, Text, JSON
from app.database import Base


class Paper(Base):
    __tablename__ = "papers"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # 元数据
    title = Column(String(500))
    title_zh = Column(String(500))
    authors = Column(JSON)                   # ["Author A", "Author B"]
    abstract = Column(Text)
    abstract_zh = Column(Text)
    keywords = Column(JSON)
    doi = Column(String(100), index=True)
    year = Column(Integer)
    paper_type = Column(String(20))          # "journal" | "conference"
    journal = Column(String(300))
    division = Column(String(500))           # 分区标签，多个用顿号分隔
    source_language = Column(String(20), nullable=False, server_default='en')
    domain = Column(String(100), nullable=True)

    # 文件存储
    storage_key = Column(String(500), nullable=False)
    file_size = Column(Integer)
    page_count = Column(Integer)

    # MinerU 解析结果缓存
    structure_json = Column(JSON)

    created_at = Column(DateTime, default=datetime.utcnow)
