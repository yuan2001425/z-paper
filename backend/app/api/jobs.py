import asyncio
import json
import os
import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.job import TranslationJob, JobStatus
from app.models.user_glossary import UserGlossary
from app.models.paper import Paper
from app.models.result import TranslationResult
from app.schemas.job import JobResponse

router = APIRouter()


# ── 任务列表 / 详情 / 删除 ─────────────────────────────────────────────────────

@router.get("", response_model=list[JobResponse])
def list_jobs(db: Session = Depends(get_db)):
    return db.query(TranslationJob).order_by(TranslationJob.created_at.desc()).all()


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


def _delete_upload_file(storage_key: str):
    """删除 uploads 目录下的原始 PDF 文件，静默忽略不存在的情况"""
    if not storage_key:
        return
    path = os.path.join(settings.LOCAL_UPLOAD_PATH, storage_key)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


@router.delete("/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    paper_id = job.paper_id
    db.query(TranslationResult).filter(TranslationResult.job_id == job_id).delete(synchronize_session=False)
    db.delete(job)
    storage_key = None
    if paper_id:
        paper = db.query(Paper).filter(Paper.id == paper_id).first()
        if paper:
            storage_key = paper.storage_key
            db.delete(paper)
    db.commit()
    _delete_upload_file(storage_key)
    return {"ok": True}


@router.delete("")
def clear_jobs(status: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """按状态批量删除任务，status 可为 completed / failed，不传则删除两者"""
    allowed = {JobStatus.COMPLETED, JobStatus.FAILED}
    target = ({status} & allowed) if status else allowed
    if target:
        jobs = db.query(TranslationJob).filter(TranslationJob.status.in_(target)).all()
        paper_ids = [j.paper_id for j in jobs if j.paper_id]
        job_ids = [j.id for j in jobs]
        db.query(TranslationResult).filter(TranslationResult.job_id.in_(job_ids)).delete(synchronize_session=False)
        db.query(TranslationJob).filter(TranslationJob.id.in_(job_ids)).delete(synchronize_session=False)
        storage_keys = []
        if paper_ids:
            papers = db.query(Paper).filter(Paper.id.in_(paper_ids)).all()
            storage_keys = [p.storage_key for p in papers if p.storage_key]
            db.query(Paper).filter(Paper.id.in_(paper_ids)).delete(synchronize_session=False)
        db.commit()
        for key in storage_keys:
            _delete_upload_file(key)
    return {"ok": True}


# ── 术语审查 ──────────────────────────────────────────────────────────────────

@router.get("/{job_id}/pending-terms")
def get_pending_terms(job_id: str, db: Session = Depends(get_db)):
    """
    返回待审核术语及论文默认领域：
    {
      "paper_domain": str | null,
      "terms": [{"en": str, "zh": str}, ...]
    }
    """
    job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status != JobStatus.WAITING_TERM_REVIEW:
        raise HTTPException(status_code=400, detail="该任务当前不在术语审查状态")
    paper = db.query(Paper).filter(Paper.id == job.paper_id).first()
    paper_domain = paper.domain if paper else None
    terms = json.loads(job.pending_terms or "[]")
    return {"paper_domain": paper_domain, "terms": terms}


# 处理方式与 UserGlossary.status 完全对应
_VALID_STATUSES = {"translate", "never_translate", "translate_with_annotation"}


class TermDecision(BaseModel):
    en: str
    zh: Optional[str] = None
    status: str = "translate"   # translate | never_translate | translate_with_annotation | skip


@router.post("/{job_id}/confirm-terms")
async def confirm_terms(
    job_id: str,
    decisions: List[TermDecision],
    db: Session = Depends(get_db),
):
    """
    用户确认术语审查结果，写入词库后继续翻译。

    status 字段含义：
      translate                → 按中文译名翻译
      never_translate          → 保留原文，不翻译
      translate_with_annotation → 翻译并在括号内保留原文
      skip                     → 本次不加入词库（跳过）
    """
    job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status != JobStatus.WAITING_TERM_REVIEW:
        raise HTTPException(status_code=400, detail="该任务当前不在术语审查状态")

    paper = db.query(Paper).filter(Paper.id == job.paper_id).first()
    paper_domain = paper.domain if paper else None

    saved = 0
    for d in decisions:
        if d.status not in _VALID_STATUSES:
            continue  # skip
        en = (d.en or "").strip()
        if not en:
            continue
        zh = (d.zh or "").strip() or None
        # never_translate 不需要中文译名；其余两种需要
        if d.status != "never_translate" and not zh:
            continue

        existing = db.query(UserGlossary).filter(
            UserGlossary.foreign_term == en,
            UserGlossary.source_language == "en",
        ).first()
        if existing:
            existing.zh_term = zh
            existing.status = d.status
            existing.domain = paper_domain
        else:
            db.add(UserGlossary(
                id=str(uuid.uuid4()),
                foreign_term=en,
                zh_term=zh,
                source_language="en",
                status=d.status,
                domain=paper_domain,
            ))
            saved += 1

    # 清空 pending_terms，恢复运行状态
    job.pending_terms = None
    job.status = JobStatus.POLISHING
    job.progress = 53
    job.current_stage = f"术语确认完成（{saved} 条加入词库），准备翻译..."
    db.commit()

    # 异步继续翻译流水线
    from app.services.pipeline import run_phase_d_to_g
    asyncio.create_task(asyncio.to_thread(run_phase_d_to_g, job_id))

    return {"ok": True, "saved": saved}
