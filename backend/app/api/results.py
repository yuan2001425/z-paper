import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.result import TranslationResult
from app.models.annotation import Annotation
from app.models.paper import Paper
from app.schemas.result import ResultResponse, AnnotationRequest, AnnotationUpdateRequest, AnnotationResponse
from app.storage.local_storage import local_storage

router = APIRouter()


def _attach_pdf_url(result: TranslationResult, db: Session) -> ResultResponse:
    paper = db.query(Paper).filter(Paper.id == result.paper_id).first()
    pdf_url = local_storage.get_url(paper.storage_key) if paper and paper.storage_key else None
    data = ResultResponse.model_validate(result)
    data.pdf_url = pdf_url
    return data


class TranslateImageRequest(BaseModel):
    image_url: str
    block_index: int


@router.get("/by-job/{job_id}", response_model=ResultResponse)
def get_result_by_job(job_id: str, db: Session = Depends(get_db)):
    result = db.query(TranslationResult).filter(TranslationResult.job_id == job_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="译文不存在")
    return _attach_pdf_url(result, db)


@router.get("/by-paper/{paper_id}", response_model=ResultResponse)
def get_result_by_paper(paper_id: str, db: Session = Depends(get_db)):
    result = db.query(TranslationResult).filter(TranslationResult.paper_id == paper_id).order_by(TranslationResult.created_at.desc()).first()
    if not result:
        raise HTTPException(status_code=404, detail="译文不存在")
    return _attach_pdf_url(result, db)


@router.get("/{result_id}", response_model=ResultResponse)
def get_result(result_id: str, db: Session = Depends(get_db)):
    result = db.query(TranslationResult).filter(TranslationResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="译文不存在")
    return _attach_pdf_url(result, db)


# ── 批注 ──────────────────────────────────────────────

@router.post("/{result_id}/annotations", response_model=AnnotationResponse)
def create_annotation(
    result_id: str,
    req: AnnotationRequest,
    db: Session = Depends(get_db),
):
    annotation = Annotation(
        id=str(uuid.uuid4()),
        result_id=result_id,
        scope=req.scope,
        content=req.content,
        block_id=req.block_id,
        start_offset=req.start_offset,
        end_offset=req.end_offset,
        selected_text=req.selected_text,
    )
    db.add(annotation)
    db.commit()
    db.refresh(annotation)
    return annotation


@router.get("/{result_id}/annotations")
def list_annotations(result_id: str, db: Session = Depends(get_db)):
    return db.query(Annotation).filter(Annotation.result_id == result_id).all()


@router.patch("/{result_id}/annotations/{annotation_id}", response_model=AnnotationResponse)
def update_annotation(
    result_id: str,
    annotation_id: str,
    req: AnnotationUpdateRequest,
    db: Session = Depends(get_db),
):
    ann = db.query(Annotation).filter(
        Annotation.id == annotation_id,
        Annotation.result_id == result_id,
    ).first()
    if not ann:
        raise HTTPException(status_code=404, detail="批注不存在")
    ann.content = req.content
    db.commit()
    db.refresh(ann)
    return ann


@router.delete("/{result_id}/annotations/{annotation_id}")
def delete_annotation(
    result_id: str,
    annotation_id: str,
    db: Session = Depends(get_db),
):
    ann = db.query(Annotation).filter(
        Annotation.id == annotation_id,
        Annotation.result_id == result_id,
    ).first()
    if not ann:
        raise HTTPException(status_code=404, detail="批注不存在")
    db.delete(ann)
    db.commit()
    return {"ok": True}


# ── 图片翻译 ────────────────────────────────────────────────────────────────

@router.post("/{result_id}/translate-image")
def translate_image_block(
    result_id: str,
    req: TranslateImageRequest,
    db: Session = Depends(get_db),
):
    result = db.query(TranslationResult).filter(TranslationResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="译文不存在")

    from app.models.user_glossary import UserGlossary
    from app.models.domain_glossary import DomainGlossary

    paper = db.query(Paper).filter(Paper.id == result.paper_id).first()
    user_glossary = db.query(UserGlossary).all()
    domain_glossary = []
    if paper and paper.domain:
        domain_glossary = db.query(DomainGlossary).filter(
            DomainGlossary.domain == paper.domain
        ).all()

    glossary_list = [{"en": g.foreign_term, "zh": g.zh_term, "status": g.status} for g in user_glossary]
    user_keys = {g.foreign_term.lower() for g in user_glossary}
    for dg in domain_glossary:
        if dg.en_term.lower() not in user_keys:
            glossary_list.append({"en": dg.en_term, "zh": dg.zh_term, "status": "translate"})

    domain = (paper.domain or "学术") if paper else "学术"
    paper_id = result.paper_id or ""

    from app.services.image_translation import translate_image
    translated_url = translate_image(
        image_url=req.image_url,
        glossary_list=glossary_list,
        paper_id=paper_id,
        domain=domain,
    )

    changed = translated_url != req.image_url
    if changed:
        from sqlalchemy.orm.attributes import flag_modified
        zhengwen = list(result.structure_json.get("正文", []))
        idx = req.block_index
        if 0 <= idx < len(zhengwen) and "图片地址" in zhengwen[idx]:
            zhengwen[idx] = dict(zhengwen[idx])
            zhengwen[idx]["中文图片地址"] = translated_url
            new_json = dict(result.structure_json)
            new_json["正文"] = zhengwen
            result.structure_json = new_json
            flag_modified(result, "structure_json")
        db.commit()

    return {"translated_url": translated_url, "changed": changed}
