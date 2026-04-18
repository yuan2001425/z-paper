from typing import Optional
from pydantic import BaseModel
from datetime import datetime


class JobResponse(BaseModel):
    id: str
    paper_id: str
    job_type: Optional[str] = "translation"
    status: str
    current_stage: Optional[str]
    progress: int
    error_message: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class GlossaryItem(BaseModel):
    id: str
    foreign_term: str
    zh_term: Optional[str]
    source_language: str
    domain: Optional[str]
    status: str
    updated_at: datetime

    class Config:
        from_attributes = True
