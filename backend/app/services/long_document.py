import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path

import fitz
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.job import JobStatus, JobType, TranslationJob
from app.models.paper import Paper
from app.models.result import TranslationResult
from app.storage.local_storage import local_storage

logger = logging.getLogger(__name__)


def count_pdf_pages(pdf_bytes: bytes) -> int:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return doc.page_count


def render_page_thumbnail(pdf_bytes: bytes, page: int) -> bytes:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if page < 1 or page > doc.page_count:
            raise ValueError("page out of range")
        pix = doc.load_page(page - 1).get_pixmap(matrix=fitz.Matrix(0.35, 0.35), alpha=False)
        return pix.tobytes("png")


def split_pdf_range(pdf_bytes: bytes, start_page: int, end_page: int) -> bytes:
    out = fitz.open()
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as src:
            out.insert_pdf(src, from_page=start_page - 1, to_page=end_page - 1)
        return out.tobytes()
    finally:
        out.close()


def extract_level1_bookmarks(pdf_bytes: bytes) -> list[dict]:
    """Return level-1 PDF bookmarks as editable chapter suggestions."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page_count = doc.page_count
        toc = doc.get_toc(simple=True)

    level1 = []
    seen = set()
    for level, title, page in toc:
        title = (title or "").strip()
        if level != 1 or not title or page < 1 or page > page_count:
            continue
        key = (page, title)
        if key in seen:
            continue
        seen.add(key)
        level1.append({"title": title, "start_page": page})

    level1.sort(key=lambda item: item["start_page"])
    suggestions = []
    for index, item in enumerate(level1):
        next_start = level1[index + 1]["start_page"] if index + 1 < len(level1) else page_count + 1
        end_page = max(item["start_page"], next_start - 1)
        suggestions.append({
            "title": item["title"],
            "start_page": item["start_page"],
            "end_page": end_page,
        })
    return suggestions


def validate_chapters(chapters: list[dict], page_count: int) -> list[dict]:
    if not chapters:
        raise ValueError("请至少添加一个章节")

    normalized = []
    ranges: list[tuple[int, int, str]] = []
    for i, item in enumerate(chapters):
        title = (item.get("title") or f"第 {i + 1} 章").strip()
        start = int(item.get("start_page") or 0)
        end = int(item.get("end_page") or 0)
        if start < 1 or end < 1 or start > page_count or end > page_count:
            raise ValueError(f"{title} 的页码超出范围")
        if start > end:
            raise ValueError(f"{title} 的起始页不能大于结束页")
        for other_start, other_end, other_title in ranges:
            if not (end < other_start or start > other_end):
                raise ValueError(f"{title} 与 {other_title} 页码范围重叠")
        ranges.append((start, end, title))
        normalized.append({"title": title, "start_page": start, "end_page": end})

    normalized.sort(key=lambda x: (x["start_page"], x["end_page"]))
    return normalized


def _result_exists(db: Session, job_id: str) -> bool:
    return db.query(TranslationResult).filter(TranslationResult.job_id == job_id).first() is not None


def _merge_child_results(db: Session, parent_job: TranslationJob):
    parent_paper = db.query(Paper).filter(Paper.id == parent_job.paper_id).first()
    child_jobs = (
        db.query(TranslationJob)
        .filter(TranslationJob.parent_job_id == parent_job.id)
        .order_by(TranslationJob.chapter_index.asc())
        .all()
    )
    if not child_jobs:
        return

    body_blocks = []
    references = []
    seen_refs = set()
    memories = []

    for child_job in child_jobs:
        child_paper = db.query(Paper).filter(Paper.id == child_job.paper_id).first()
        child_result = db.query(TranslationResult).filter(TranslationResult.job_id == child_job.id).first()
        if not child_result:
            return

        structure = child_result.structure_json or {}
        chapter_title = child_paper.chapter_title if child_paper else f"第 {child_job.chapter_index or 1} 章"
        body_blocks.append({
            "标题等级": 1,
            "文本": chapter_title,
            "中文文本": chapter_title,
        })
        body_blocks.extend(structure.get("正文") or [])

        for ref in structure.get("参考文献") or []:
            if ref and ref not in seen_refs:
                seen_refs.add(ref)
                references.append(ref)

        if structure.get("文档记忆"):
            memories.append({
                "chapter_index": child_job.chapter_index,
                "chapter_title": chapter_title,
                "memory": structure["文档记忆"],
            })

    is_chinese = parent_paper.source_language == "zh" if parent_paper else False
    division_tags = []
    if parent_paper and parent_paper.division:
        division_tags = [tag.strip() for tag in parent_paper.division.split("、") if tag.strip()]

    result_structure = {
        "paper_type": "chinese" if is_chinese else "long_document",
        "authors": parent_paper.authors if parent_paper else [],
        "标题": parent_paper.title if parent_paper else "",
        "标题中文": parent_paper.title_zh if parent_paper else "",
        "所属期刊/会议": parent_paper.journal if parent_paper else "",
        "年份": str(parent_paper.year) if parent_paper and parent_paper.year else "",
        "期刊/会议分类标签": division_tags,
        "DOI": parent_paper.doi if parent_paper else "",
        "文档记忆": {"type": "long_document", "chapters": memories},
        "正文": body_blocks,
        "参考文献": references,
    }

    db.add(TranslationResult(
        id=str(uuid.uuid4()),
        job_id=parent_job.id,
        paper_id=parent_job.paper_id,
        structure_json=result_structure,
    ))


def refresh_parent_job(parent_job_id: str):
    with SessionLocal() as db:
        parent_job = db.query(TranslationJob).filter(TranslationJob.id == parent_job_id).first()
        if not parent_job:
            return

        children = (
            db.query(TranslationJob)
            .filter(TranslationJob.parent_job_id == parent_job_id)
            .order_by(TranslationJob.chapter_index.asc())
            .all()
        )
        if not children:
            parent_job.status = JobStatus.WAITING_CHAPTERS
            parent_job.progress = 0
            parent_job.current_stage = "等待分章"
            db.commit()
            return

        waiting = next((job for job in children if job.status == JobStatus.WAITING_TERM_REVIEW), None)
        failed = next((job for job in children if job.status == JobStatus.FAILED), None)
        completed = [job for job in children if job.status == JobStatus.COMPLETED]

        if failed:
            parent_job.status = JobStatus.FAILED
            parent_job.error_message = f"第 {failed.chapter_index} 章处理失败：{failed.error_message or ''}"
        elif waiting:
            parent_job.status = JobStatus.WAITING_TERM_REVIEW
            parent_job.current_stage = f"第 {waiting.chapter_index} 章等待术语审查"
            parent_job.progress = int(len(completed) / len(children) * 100)
        elif len(completed) == len(children):
            if not _result_exists(db, parent_job.id):
                _merge_child_results(db, parent_job)
            parent_job.status = JobStatus.COMPLETED
            parent_job.progress = 100
            parent_job.current_stage = "长文档处理完成"
            parent_job.completed_at = datetime.utcnow()
        else:
            active = next((job for job in children if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED)), None)
            parent_job.status = JobStatus.TRANSLATING
            parent_job.progress = int((len(completed) + ((active.progress or 0) / 100 if active else 0)) / len(children) * 100)
            active_index = active.chapter_index if active else len(completed) + 1
            parent_job.current_stage = f"正在处理第 {active_index} / {len(children)} 章"
        db.commit()


async def monitor_parent_job(parent_job_id: str):
    while True:
        await asyncio.to_thread(refresh_parent_job, parent_job_id)
        with SessionLocal() as db:
            parent = db.query(TranslationJob).filter(TranslationJob.id == parent_job_id).first()
            if not parent or parent.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                return
        await asyncio.sleep(5)


def create_chapter_tasks(parent_job_id: str, chapters: list[dict]) -> list[dict]:
    with SessionLocal() as db:
        parent_job = db.query(TranslationJob).filter(TranslationJob.id == parent_job_id).first()
        if not parent_job:
            raise ValueError("长文档任务不存在")

        parent_paper = db.query(Paper).filter(Paper.id == parent_job.paper_id).first()
        if not parent_paper:
            raise ValueError("长文档不存在")

        exists = db.query(TranslationJob).filter(TranslationJob.parent_job_id == parent_job_id).first()
        if exists:
            raise ValueError("该长文档已经提交分章")

        pdf_bytes = local_storage.get_object(parent_paper.storage_key)
        normalized = validate_chapters(chapters, parent_paper.page_count or count_pdf_pages(pdf_bytes))
        created = []

        for idx, chapter in enumerate(normalized, start=1):
            chapter_id = str(uuid.uuid4())
            storage_key = f"papers/{chapter_id}/chapter-{idx:03d}.pdf"
            chapter_bytes = split_pdf_range(pdf_bytes, chapter["start_page"], chapter["end_page"])
            local_storage.put_object(storage_key, chapter_bytes, content_type="application/pdf")

            chapter_paper = Paper(
                id=chapter_id,
                title=f"{parent_paper.title or parent_paper.title_zh} - {chapter['title']}",
                title_zh=f"{parent_paper.title_zh or parent_paper.title} - {chapter['title']}",
                authors=parent_paper.authors,
                paper_type=parent_paper.paper_type,
                journal=parent_paper.journal,
                division=parent_paper.division,
                year=parent_paper.year,
                doi=parent_paper.doi,
                source_language=parent_paper.source_language,
                domain=parent_paper.domain,
                document_role="long_chapter",
                parent_paper_id=parent_paper.id,
                chapter_index=idx,
                chapter_title=chapter["title"],
                start_page=chapter["start_page"],
                end_page=chapter["end_page"],
                storage_key=storage_key,
                file_size=len(chapter_bytes),
                page_count=chapter["end_page"] - chapter["start_page"] + 1,
            )
            db.add(chapter_paper)

            job_type = JobType.ARCHIVE if parent_paper.source_language == "zh" else JobType.TRANSLATION
            chapter_job = TranslationJob(
                id=str(uuid.uuid4()),
                paper_id=chapter_id,
                parent_job_id=parent_job_id,
                chapter_index=idx,
                total_chapters=len(normalized),
                job_type=job_type,
                status=JobStatus.PENDING,
                translate_images=parent_job.translate_images,
            )
            db.add(chapter_job)
            created.append({
                "paper_id": chapter_id,
                "job_id": chapter_job.id,
                "storage_key": storage_key,
                "job_type": job_type,
            })

        parent_job.status = JobStatus.PENDING
        parent_job.total_chapters = len(normalized)
        parent_job.current_stage = f"已创建 {len(normalized)} 个章节任务"
        db.commit()

    return created


def paper_upload_dir(storage_key: str | None) -> Path | None:
    if not storage_key:
        return None
    return local_storage.get_file_path(storage_key).parent
