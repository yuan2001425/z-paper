import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models.domain_glossary import DomainGlossary

router = APIRouter()

DOMAINS = [
    "计算机科学", "数学", "物理学", "化学", "生物学",
    "医学", "地理学", "经济学", "材料科学", "环境科学", "其他",
]


class DomainGlossaryCreate(BaseModel):
    domain: str
    en_term: str
    zh_term: str


@router.get("/domains")
def list_domains():
    return DOMAINS


@router.get("")
def list_domain_glossary(domain: str = Query(...), db: Session = Depends(get_db)):
    return db.query(DomainGlossary).filter(DomainGlossary.domain == domain).all()


@router.post("")
def add_domain_term(body: DomainGlossaryCreate, db: Session = Depends(get_db)):
    existing = db.query(DomainGlossary).filter(
        DomainGlossary.domain == body.domain,
        DomainGlossary.en_term == body.en_term,
    ).first()
    if existing:
        existing.zh_term = body.zh_term
        db.commit()
        return existing

    entry = DomainGlossary(
        id=str(uuid.uuid4()),
        domain=body.domain,
        en_term=body.en_term,
        zh_term=body.zh_term,
    )
    db.add(entry)
    db.commit()
    return entry


@router.delete("/{entry_id}")
def delete_domain_term(entry_id: str, db: Session = Depends(get_db)):
    entry = db.query(DomainGlossary).filter(DomainGlossary.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="词条不存在")
    db.delete(entry)
    db.commit()
    return {"ok": True}
