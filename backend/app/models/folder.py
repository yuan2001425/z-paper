from datetime import datetime
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, UniqueConstraint
from app.database import Base


class VirtualFolder(Base):
    __tablename__ = "virtual_folders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    parent_id = Column(Integer, ForeignKey("virtual_folders.id", ondelete="CASCADE"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PaperFolderMapping(Base):
    __tablename__ = "paper_folder_mapping"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(String(36), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False)
    folder_id = Column(Integer, ForeignKey("virtual_folders.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (UniqueConstraint("paper_id", "folder_id"),)
