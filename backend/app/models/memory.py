import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime
from app.database import Base


class Memory(Base):
    """跨会话持久记忆（对标 Claude Code memdir 的一个「记忆文件」）。

    四类：user / feedback / project / reference。
    name 是唯一 slug（相当于文件名），description 用于相关性判断，content 是正文。
    """
    __tablename__ = "memories"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    type        = Column(String(20), nullable=False)                    # user | feedback | project | reference
    name        = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=False, default="")
    content     = Column(Text, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
