import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models.user_glossary import UserGlossary
from app.schemas.job import GlossaryItem

router = APIRouter()


class AddTermRequest(BaseModel):
    foreign_term: str
    zh_term: Optional[str] = None
    source_language: str = "en"
    domain: str                   # 必填，每条术语必须归属某一学科
    status: str = "translate"


@router.post("", response_model=GlossaryItem, status_code=201)
def add_term(body: AddTermRequest, db: Session = Depends(get_db)):
    if not body.domain or not body.domain.strip():
        raise HTTPException(status_code=400, detail="所属学科不能为空")
    if body.status not in ("translate", "never_translate", "translate_with_annotation"):
        raise HTTPException(status_code=400, detail="status 值不合法")

    term = UserGlossary(
        id=str(uuid.uuid4()),
        foreign_term=body.foreign_term.strip(),
        zh_term=body.zh_term.strip() if body.zh_term else None,
        source_language=body.source_language,
        domain=body.domain or None,
        status=body.status,
    )
    db.add(term)
    try:
        db.commit()
        db.refresh(term)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="该术语已存在")
    return term


@router.get("")
def list_glossary(
    domain: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(UserGlossary)
    if domain:
        q = q.filter(UserGlossary.domain == domain)
    q = q.order_by(UserGlossary.foreign_term)
    total = q.count()
    items = q.offset(offset).limit(limit).all()
    return {"total": total, "items": [GlossaryItem.model_validate(i) for i in items]}


@router.patch("/{term_id}")
def update_term(term_id: str, body: dict, db: Session = Depends(get_db)):
    term = db.query(UserGlossary).filter(UserGlossary.id == term_id).first()
    if not term:
        raise HTTPException(status_code=404, detail="术语不存在")
    if "zh_term" in body:
        term.zh_term = body["zh_term"]
    if "status" in body:
        term.status = body["status"]
    if "domain" in body:
        term.domain = body["domain"] or None
    db.commit()
    return {"ok": True}


@router.delete("/{term_id}")
def delete_term(term_id: str, db: Session = Depends(get_db)):
    term = db.query(UserGlossary).filter(UserGlossary.id == term_id).first()
    if not term:
        raise HTTPException(status_code=404, detail="术语不存在")
    db.delete(term)
    db.commit()
    return {"ok": True}
