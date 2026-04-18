from typing import Optional, Any
from pydantic import BaseModel
from datetime import datetime


class ResultResponse(BaseModel):
    id: str
    job_id: str
    paper_id: str
    structure_json: Optional[Any]
    created_at: datetime
    pdf_url: Optional[str] = None

    class Config:
        from_attributes = True


class AnnotationRequest(BaseModel):
    scope: str
    content: str
    block_id: Optional[str] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    selected_text: Optional[str] = None


class AnnotationUpdateRequest(BaseModel):
    content: str


class AnnotationResponse(BaseModel):
    id: str
    result_id: str
    scope: str
    content: str
    block_id: Optional[str]
    start_offset: Optional[int]
    end_offset: Optional[int]
    selected_text: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True
