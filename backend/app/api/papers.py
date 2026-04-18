import asyncio
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.paper import Paper
from app.models.job import TranslationJob, JobStatus, JobType
from app.schemas.paper import PaperResponse, PaperSearchResponse
from app.storage.local_storage import local_storage

router = APIRouter()


@router.post("/extract-metadata")
async def extract_metadata(
    file: UploadFile = File(...),
    domain: Optional[str] = Form(None),
    paper_type: str = Form("journal"),
    db: Session = Depends(get_db),
):
    """上传 PDF，用 Qwen-VL 从第一页提取论文元数据。"""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="只支持 PDF 文件")

    content = await file.read()

    from app.services.metadata_extractor import metadata_extractor
    from app.services.title_translator import translate_title
    from app.models.user_glossary import UserGlossary

    result = metadata_extractor.extract(content, user_domain=domain, paper_type=paper_type)

    if result.get("title") and not result.get("title_zh"):
        glossary_terms = db.query(UserGlossary).all()
        result["title_zh"] = translate_title(
            title=result["title"],
            source_language=result.get("source_language", "en"),
            glossary_terms=glossary_terms,
            domain=domain,
        )

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
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    """搜索个人论文库"""
    query = db.query(Paper)
    if q:
        query = query.filter(
            Paper.title.contains(q) | Paper.title_zh.contains(q)
        )
    if year:
        query = query.filter(Paper.year == year)
    if paper_type:
        query = query.filter(Paper.paper_type == paper_type)

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
    query = db.query(Paper)
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
    # 删除存储文件
    try:
        local_storage.delete_object(paper.storage_key)
    except Exception:
        pass
    db.delete(paper)
    db.commit()
    return {"ok": True}
