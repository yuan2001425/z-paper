import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text, JSON
from app.database import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id                 = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title              = Column(String(200), nullable=False, default="新对话")
    history_json       = Column(JSON, default=list)   # 工作消息列表（L1 记忆）
    compaction_summary = Column(Text, nullable=True)   # 压缩摘要（L2 记忆）
    created_at         = Column(DateTime, default=datetime.utcnow)
    updated_at         = Column(DateTime, default=datetime.utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id             = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id     = Column(String(36), nullable=False, index=True)
    role           = Column(String(20), nullable=False)   # "user" | "assistant"
    content        = Column(Text, nullable=False)
    tool_calls_json = Column(JSON, default=list)   # [{name, args, result_snippet}]
    citations_json  = Column(JSON, default=list)   # [{paper_id, block_idx, text, type}]
    created_at     = Column(DateTime, default=datetime.utcnow)
