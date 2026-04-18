from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime


class PaperResponse(BaseModel):
    id: str
    title: Optional[str]
    title_zh: Optional[str]
    authors: Optional[List[str]]
    abstract: Optional[str]
    keywords: Optional[List[str]]
    doi: Optional[str]
    year: Optional[int]
    paper_type: Optional[str]
    journal: Optional[str]
    division: Optional[str]
    source_language: Optional[str]
    domain: Optional[str]
    page_count: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


class PaperSearchResponse(BaseModel):
    items: List[PaperResponse]
    total: int
    page: int
    page_size: int
