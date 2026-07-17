import asyncio
import difflib
import logging
import time
import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.paper import Paper
from app.models.job import TranslationJob, JobStatus, JobType
from app.schemas.job import JobResponse
from app.schemas.paper import PaperResponse, PaperSearchResponse
from app.storage.local_storage import local_storage

logger = logging.getLogger(__name__)
router = APIRouter()


class DuplicateCheckRequest(BaseModel):
    title: str = ""
    title_zh: str = ""


class LongChapterRequest(BaseModel):
    title: str
    start_page: int
    end_page: int


class LongChaptersRequest(BaseModel):
    chapters: List[LongChapterRequest]


def _title_similarity(a: str, b: str) -> float:
    """大小写不敏感的标题相似度，0~1。"""
    a, b = a.lower().strip(), b.lower().strip()
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


@router.post("/check-duplicate")
def check_duplicate(body: DuplicateCheckRequest, db: Session = Depends(get_db)):
    """检测库中是否存在相似标题的论文，返回相似度 ≥ 0.6 的结果（最多5条）。"""
    papers = db.query(Paper).all()
    results = []
    for p in papers:
        # 分别与外文标题和中文标题比较，取最高分
        sim = max(
            _title_similarity(body.title, p.title or ""),
            _title_similarity(body.title, p.title_zh or ""),
            _title_similarity(body.title_zh, p.title or ""),
            _title_similarity(body.title_zh, p.title_zh or ""),
        )
        if sim >= 0.6:
            results.append({
                "paper_id": p.id,
                "title": p.title or "",
                "title_zh": p.title_zh or "",
                "journal": p.journal or "",
                "year": p.year,
                "similarity": round(sim, 2),
                "pdf_url": local_storage.get_url(p.storage_key) if p.storage_key else None,
            })
    results.sort(key=lambda x: x["similarity"], reverse=True)
    return {"duplicates": results[:5]}


@router.post("/extract-metadata")
async def extract_metadata(
    file: UploadFile = File(...),
    domain: Optional[str] = Form(None),
    paper_type: str = Form("journal"),
    db: Session = Depends(get_db),
):
    """上传 PDF，用 Qwen-VL 从第一页提取论文元数据。"""
    t_req = time.time()
    logger.info("[/extract-metadata] 收到请求 filename=%s size=%.1fKB",
                file.filename, (file.size or 0) / 1024)

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="只支持 PDF 文件")

    content = await file.read()
    logger.info("[/extract-metadata] 文件读取完成 actual_size=%.1fKB 耗时=%.2fs",
                len(content) / 1024, time.time() - t_req)

    from app.services.metadata_extractor import metadata_extractor
    from app.services.title_translator import translate_title
    from app.models.user_glossary import UserGlossary

    t0 = time.time()
    result = metadata_extractor.extract(content, user_domain=domain, paper_type=paper_type)
    logger.info("[/extract-metadata] metadata_extractor 返回 耗时=%.2fs title=%r",
                time.time() - t0, result.get("title", "")[:60])

    if result.get("title") and not result.get("title_zh"):
        t0 = time.time()
        glossary_terms = db.query(UserGlossary).all()
        result["title_zh"] = translate_title(
            title=result["title"],
            source_language=result.get("source_language", "en"),
            glossary_terms=glossary_terms,
            domain=domain,
        )
        logger.info("[/extract-metadata] title_translator 返回 耗时=%.2fs title_zh=%r",
                    time.time() - t0, result.get("title_zh", "")[:60])

    logger.info("[/extract-metadata] 请求完成 总耗时=%.2fs", time.time() - t_req)
    return result


@router.post("/upload")
async def upload_paper(
    file: UploadFile = File(...),
    title: str = Form(...),
    title_zh: Optional[str] = Form(None),
    paper_type: str = Form("journal"),
    journal: str = Form(""),
    division: str = Form(""),
    year: Optional[int] = Form(None),
    doi: str = Form(""),
    source_language: str = Form("en"),
    domain: Optional[str] = Form(None),
    translate_images: bool = Form(True),
    db: Session = Depends(get_db),
):
    """上传 PDF + 填写元数据，创建翻译任务。"""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="只支持 PDF 文件")

    content = await file.read()
    file_size = len(content)

    paper_id = str(uuid.uuid4())
    storage_key = f"papers/{paper_id}/{file.filename}"
    local_storage.put_object(storage_key, content, content_type="application/pdf")

    paper = Paper(
        id=paper_id,
        title=title,
        title_zh=title_zh or None,
        paper_type=paper_type,
        journal=journal or None,
        division=division or None,
        year=year,
        doi=doi or None,
        source_language=source_language or "en",
        domain=domain or None,
        storage_key=storage_key,
        file_size=file_size,
    )
    db.add(paper)

    job = TranslationJob(
        id=str(uuid.uuid4()),
        paper_id=paper_id,
        status=JobStatus.PENDING,
        translate_images=translate_images,
    )
    db.add(job)
    db.commit()

    from app.tasks.translation_tasks import start_translation
    asyncio.create_task(start_translation(job.id, storage_key))

    return {"paper_id": paper_id, "job_id": job.id}


@router.post("/long-drafts")
async def create_long_draft(
    file: UploadFile = File(...),
    title: str = Form(...),
    title_zh: Optional[str] = Form(None),
    paper_type: str = Form("journal"),
    journal: str = Form(""),
    division: str = Form(""),
    authors: str = Form(""),
    year: Optional[int] = Form(None),
    doi: str = Form(""),
    source_language: str = Form("en"),
    domain: Optional[str] = Form(None),
    translate_images: bool = Form(True),
    db: Session = Depends(get_db),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="只支持 PDF 文件")

    content = await file.read()
    from app.services.long_document import count_pdf_pages
    try:
        page_count = count_pdf_pages(content)
    except Exception:
        raise HTTPException(status_code=400, detail="PDF 读取失败，无法创建长文档")

    paper_id = str(uuid.uuid4())
    storage_key = f"papers/{paper_id}/{file.filename}"
    local_storage.put_object(storage_key, content, content_type="application/pdf")

    is_chinese = (source_language or "en") == "zh"
    author_list = [a.strip() for a in authors.replace("，", ",").replace("、", ",").split(",") if a.strip()]
    paper = Paper(
        id=paper_id,
        title=title,
        title_zh=title_zh or title or None,
        authors=author_list,
        paper_type="long_document",
        journal=None,
        division=None,
        year=year,
        doi=None,
        source_language="zh" if is_chinese else (source_language or "en"),
        domain=domain or None,
        document_role="long_parent",
        storage_key=storage_key,
        file_size=len(content),
        page_count=page_count,
    )
    db.add(paper)

    job = TranslationJob(
        id=str(uuid.uuid4()),
        paper_id=paper_id,
        job_type=JobType.LONG_ARCHIVE if is_chinese else JobType.LONG_TRANSLATION,
        status=JobStatus.WAITING_CHAPTERS,
        current_stage="等待分章",
        progress=0,
        translate_images=False if is_chinese else translate_images,
    )
    db.add(job)
    db.commit()

    return {
        "draft_id": job.id,
        "paper_id": paper_id,
        "job_id": job.id,
        "page_count": page_count,
    }


@router.get("/long-drafts/{draft_id}/pages/{page}/thumbnail")
def get_long_draft_thumbnail(draft_id: str, page: int, db: Session = Depends(get_db)):
    job = db.query(TranslationJob).filter(TranslationJob.id == draft_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="长文档草稿不存在")
    paper = db.query(Paper).filter(Paper.id == job.paper_id).first()
    if not paper or paper.document_role != "long_parent":
        raise HTTPException(status_code=404, detail="长文档不存在")
    from app.services.long_document import render_page_thumbnail
    try:
        img = render_page_thumbnail(local_storage.get_object(paper.storage_key), page)
    except ValueError:
        raise HTTPException(status_code=400, detail="页码超出范围")
    return Response(content=img, media_type="image/png")


@router.get("/long-drafts/{draft_id}/outline")
def get_long_draft_outline(draft_id: str, db: Session = Depends(get_db)):
    job = db.query(TranslationJob).filter(TranslationJob.id == draft_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="闀挎枃妗ｈ崏绋夸笉瀛樺湪")
    paper = db.query(Paper).filter(Paper.id == job.paper_id).first()
    if not paper or paper.document_role != "long_parent":
        raise HTTPException(status_code=404, detail="闀挎枃妗ｄ笉瀛樺湪")
    from app.services.long_document import extract_level1_bookmarks
    chapters = extract_level1_bookmarks(local_storage.get_object(paper.storage_key))
    return {
        "has_outline": len(chapters) > 0,
        "chapters": chapters,
    }


@router.post("/long-drafts/{draft_id}/chapters")
async def submit_long_chapters(
    draft_id: str,
    body: LongChaptersRequest,
    db: Session = Depends(get_db),
):
    job = db.query(TranslationJob).filter(TranslationJob.id == draft_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="长文档草稿不存在")

    from app.services.long_document import create_chapter_tasks, monitor_parent_job
    try:
        created = create_chapter_tasks(draft_id, [c.model_dump() for c in body.chapters])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from app.tasks.translation_tasks import start_translation, start_archiving
    for item in created:
        if item["job_type"] == JobType.ARCHIVE:
            asyncio.create_task(start_archiving(item["job_id"], item["storage_key"]))
        else:
            asyncio.create_task(start_translation(item["job_id"], item["storage_key"]))
    asyncio.create_task(monitor_parent_job(draft_id))

    return {"ok": True, "chapters": created}


@router.get("/long-documents/{paper_id}/chapters")
def get_long_document_chapters(paper_id: str, db: Session = Depends(get_db)):
    parent = db.query(Paper).filter(Paper.id == paper_id).first()
    if not parent or parent.document_role != "long_parent":
        raise HTTPException(status_code=404, detail="长文档不存在")
    parent_job = db.query(TranslationJob).filter(
        TranslationJob.paper_id == paper_id,
        TranslationJob.parent_job_id.is_(None),
    ).order_by(TranslationJob.created_at.desc()).first()
    chapters = db.query(Paper).filter(
        Paper.parent_paper_id == paper_id,
        Paper.document_role == "long_chapter",
    ).order_by(Paper.chapter_index.asc()).all()
    result = []
    from app.models.result import TranslationResult
    for chapter in chapters:
        job = db.query(TranslationJob).filter(TranslationJob.paper_id == chapter.id).first()
        tr = db.query(TranslationResult).filter(TranslationResult.paper_id == chapter.id).order_by(TranslationResult.created_at.desc()).first()
        result.append({
            "paper_id": chapter.id,
            "job_id": job.id if job else None,
            "result_id": tr.id if tr else None,
            "chapter_index": chapter.chapter_index,
            "chapter_title": chapter.chapter_title,
            "start_page": chapter.start_page,
            "end_page": chapter.end_page,
            "status": job.status if job else "pending",
            "progress": job.progress if job else 0,
            "current_stage": job.current_stage if job else "",
        })
    return {
        "paper": PaperResponse.model_validate(parent),
        "parent_job": JobResponse.model_validate(parent_job) if parent_job else None,
        "chapters": result,
    }


@router.post("/upload-chinese")
async def upload_chinese_paper(
    file: UploadFile = File(...),
    title: str = Form(...),
    title_zh: Optional[str] = Form(None),
    paper_type: str = Form("journal"),
    journal: str = Form(""),
    division: str = Form(""),
    year: Optional[int] = Form(None),
    doi: str = Form(""),
    domain: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """上传中文 PDF，创建存档任务（无翻译流程）。"""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="只支持 PDF 文件")

    content = await file.read()
    file_size = len(content)

    paper_id = str(uuid.uuid4())
    storage_key = f"papers/{paper_id}/{file.filename}"
    local_storage.put_object(storage_key, content, content_type="application/pdf")

    paper = Paper(
        id=paper_id,
        title=title,
        title_zh=title_zh or title or None,
        paper_type=paper_type,
        journal=journal or None,
        division=division or None,
        year=year,
        doi=doi or None,
        source_language="zh",
        domain=domain or None,
        storage_key=storage_key,
        file_size=file_size,
    )
    db.add(paper)

    job = TranslationJob(
        id=str(uuid.uuid4()),
        paper_id=paper_id,
        job_type=JobType.ARCHIVE,
        status=JobStatus.PENDING,
        translate_images=False,
    )
    db.add(job)
    db.commit()

    from app.tasks.translation_tasks import start_archiving
    asyncio.create_task(start_archiving(job.id, storage_key))

    return {"paper_id": paper_id, "job_id": job.id}


@router.get("/search", response_model=PaperSearchResponse)
def search_papers(
    q: str = Query(default=""),
    year: int = Query(default=None),
    paper_type: str = Query(default=None),
    folder_id: Optional[int] = Query(default=None),
    unclassified: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    """搜索个人论文库。folder_id=N 只返回该文件夹内论文；unclassified=true 只返回未分类论文。"""
    query = db.query(Paper).filter(Paper.document_role != "long_chapter")
    if q:
        query = query.filter(
            Paper.title.contains(q) | Paper.title_zh.contains(q)
        )
    if year:
        query = query.filter(Paper.year == year)
    if paper_type:
        query = query.filter(Paper.paper_type == paper_type)
    if folder_id is not None:
        from app.models.folder import PaperFolderMapping
        subq = db.query(PaperFolderMapping.paper_id).filter(
            PaperFolderMapping.folder_id == folder_id
        )
        query = query.filter(Paper.id.in_(subq))
    elif unclassified:
        from app.models.folder import PaperFolderMapping
        mapped_subq = db.query(PaperFolderMapping.paper_id).distinct()
        query = query.filter(~Paper.id.in_(mapped_subq))

    total = query.count()
    items = query.order_by(Paper.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return PaperSearchResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("", response_model=PaperSearchResponse)
def list_papers(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    """获取所有论文列表"""
    query = db.query(Paper).filter(Paper.document_role != "long_chapter")
    total = query.count()
    items = query.order_by(Paper.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return PaperSearchResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{paper_id}", response_model=PaperResponse)
def get_paper(paper_id: str, db: Session = Depends(get_db)):
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在")
    return paper


@router.delete("/{paper_id}")
def delete_paper(paper_id: str, db: Session = Depends(get_db)):
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在")

    papers_to_delete = [paper]
    if paper.document_role == "long_parent":
        papers_to_delete.extend(
            db.query(Paper).filter(Paper.parent_paper_id == paper.id).all()
        )
    paper_ids = [p.id for p in papers_to_delete]
    storage_keys = [p.storage_key for p in papers_to_delete if p.storage_key]

    from app.models.result import TranslationResult
    db.query(TranslationResult).filter(TranslationResult.paper_id.in_(paper_ids)).delete(synchronize_session=False)
    db.query(TranslationJob).filter(TranslationJob.paper_id.in_(paper_ids)).delete(synchronize_session=False)
    for p in papers_to_delete:
        db.delete(p)
    db.commit()

    for key in storage_keys:
        try:
            local_storage.delete_object(key)
        except Exception:
            pass
    return {"ok": True}
