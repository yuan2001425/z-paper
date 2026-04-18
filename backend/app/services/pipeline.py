"""
翻译流水线编排器

流程：
  A     PDF 解析（MinerU）→ Markdown
  A.5   标题层级修正
  B     文本清理（并行，每块一个）→ 合并成 cleaned_markdown
  C     结构分类（并行，每块一个）→ 所有段落/标题/参考文献/图片对象
  D     段落翻译（并行，每段一个）→ 中文译文
  E     图片翻译（可选）
  F     保存结果
"""

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from app.database import SessionLocal
from app.models.job import TranslationJob, JobStatus, JobType
from app.models.paper import Paper
from app.models.user_glossary import UserGlossary
from app.models.job_glossary import JobGlossarySnapshot
from app.models.result import TranslationResult
from app.models.domain_glossary import DomainGlossary

from app.translation.pipeline_core import (
    parse_pdf,
    fix_heading_levels,
    split_by_window,
    cleanup_chunk,
    extract_terms_chunk,
    classify_chunk,
    verify_references,
    translate_paragraph,
    normalize_display_math,
)

logger = logging.getLogger(__name__)


def _push(job_id: str, stage: str, progress: int, message: str = ""):
    with SessionLocal() as db:
        job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
        if job:
            job.status = stage
            job.current_stage = message or stage
            job.progress = progress
            db.commit()


def _load_glossary(job_id: str) -> tuple[list[dict], str, str]:
    """返回 (glossary_list, domain_label, paper_id)"""
    with SessionLocal() as db:
        job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
        paper = db.query(Paper).filter(Paper.id == job.paper_id).first()
        glossary = db.query(UserGlossary).all()
        domain_glossary = []
        paper_domain = paper.domain if paper else None
        if paper_domain:
            domain_glossary = db.query(DomainGlossary).filter(
                DomainGlossary.domain == paper_domain
            ).all()

        glossary_list = [
            {"en": g.foreign_term, "zh": g.zh_term, "status": g.status}
            for g in glossary
        ]
        user_term_keys = {g.foreign_term.lower() for g in glossary}
        for dg in domain_glossary:
            if dg.en_term.lower() not in user_term_keys:
                glossary_list.append({"en": dg.en_term, "zh": dg.zh_term, "status": "translate"})

        domain_label = paper_domain or "学术"
        paper_id = job.paper_id

    return glossary_list, domain_label, paper_id


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 A + B
# ─────────────────────────────────────────────────────────────────────────────

def run_phase_a_b(job_id: str, pdf_bytes: bytes):
    glossary_list, domain_label, paper_id = _load_glossary(job_id)

    try:
        _push(job_id, JobStatus.PARSING, 10, "正在解析 PDF 结构...")
        with SessionLocal() as db:
            job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
            paper = db.query(Paper).filter(Paper.id == job.paper_id).first()
            paper_title = paper.title or ""
            filename = paper.storage_key.split("/")[-1] if paper and paper.storage_key else "document.pdf"

        parsed = parse_pdf(pdf_bytes, filename=filename, paper_id=paper_id)
        markdown = parsed.get("markdown", "")
        logger.warning(
            "[pipeline] 阶段A完成，Markdown 长度=%d\n"
            "══════════════ RAW MARKDOWN START ══════════════\n"
            "%s\n"
            "══════════════ RAW MARKDOWN END ════════════════",
            len(markdown), markdown,
        )

        _push(job_id, JobStatus.PARSING, 15, "正在校正章节标题层级...")
        markdown = fix_heading_levels(markdown, paper_title)
        logger.warning("[pipeline] 阶段A.5完成，修正后 Markdown 长度=%d", len(markdown))

        _push(job_id, JobStatus.POLISHING, 20, "正在清理原文文本...")
        chunks = split_by_window(markdown)
        logger.warning("[pipeline] 阶段B：切分为 %d 个块", len(chunks))

        cleaned_chunks: list[str] = [""] * len(chunks)

        def _cleanup_worker(idx: int, chunk: str):
            return idx, cleanup_chunk(chunk, chunk_idx=idx)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(_cleanup_worker, i, c): i for i, c in enumerate(chunks)}
            done = 0
            for future in as_completed(futures):
                orig_idx = futures[future]
                try:
                    idx, cleaned = future.result()
                except Exception as e:
                    logger.error("[pipeline] chunk %d 清理失败: %s", orig_idx, e, exc_info=True)
                    idx, cleaned = orig_idx, chunks[orig_idx]
                cleaned_chunks[idx] = cleaned
                done += 1
                progress = 20 + int(done / max(len(chunks), 1) * 30)
                _push(job_id, JobStatus.POLISHING, progress, f"文本清理进度 {done}/{len(chunks)} 块")

        cleaned_markdown = "\n\n".join(cleaned_chunks)
        logger.warning("[pipeline] 阶段B完成，清理后 Markdown 长度=%d", len(cleaned_markdown))

        with SessionLocal() as db:
            from sqlalchemy.orm.attributes import flag_modified
            job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
            paper = db.query(Paper).filter(Paper.id == job.paper_id).first()
            paper.structure_json = {"markdown": cleaned_markdown, "title": paper.title or ""}
            flag_modified(paper, "structure_json")
            db.commit()

        # ── B.5：术语提取 ────────────────────────────────────────────────────
        _push(job_id, JobStatus.POLISHING, 51, "正在提取专业术语...")
        existing_keys = {g["en"].lower() for g in glossary_list}
        new_terms_raw: list[dict] = []

        def _terms_worker(idx: int, chunk: str):
            return extract_terms_chunk(chunk, existing_keys, domain=domain_label, chunk_idx=idx)

        with ThreadPoolExecutor(max_workers=8) as executor:
            term_futures = [executor.submit(_terms_worker, i, c) for i, c in enumerate(cleaned_chunks)]
            for fut in as_completed(term_futures):
                try:
                    for t in fut.result():
                        if t["en"].lower() not in existing_keys:
                            existing_keys.add(t["en"].lower())
                            new_terms_raw.append(t)
                except Exception as e:
                    logger.error("[pipeline] B.5 术语提取 worker 失败: %s", e)

        if new_terms_raw:
            # 有新术语：暂停，等待用户审查后再继续
            import json as _json
            with SessionLocal() as db:
                job_obj = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
                job_obj.status = JobStatus.WAITING_TERM_REVIEW
                job_obj.progress = 52
                job_obj.current_stage = f"发现 {len(new_terms_raw)} 个新术语，请审查后继续"
                job_obj.pending_terms = _json.dumps(new_terms_raw, ensure_ascii=False)
                db.commit()
            logger.warning("[pipeline] 阶段B.5：发现 %d 个新术语，等待用户审查", len(new_terms_raw))
            return  # ← 暂停，由用户确认后触发 run_phase_d_to_g
        else:
            logger.warning("[pipeline] 阶段B.5完成：无新术语，直接继续翻译")

        _push(job_id, JobStatus.TRANSLATING, 53, "术语提取完成，开始翻译...")
        run_phase_d_to_g(job_id)

    except Exception as e:
        logger.error("[pipeline] phase A+B 失败: %s", e, exc_info=True)
        with SessionLocal() as db:
            job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
            if job:
                job.status = JobStatus.FAILED
                job.error_message = str(e)
                db.commit()
        raise


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 C + D + E + F
# ─────────────────────────────────────────────────────────────────────────────

def run_phase_d_to_g(job_id: str):
    glossary_list, domain_label, paper_id = _load_glossary(job_id)

    with SessionLocal() as db:
        db.add(JobGlossarySnapshot(
            id=str(uuid.uuid4()),
            job_id=job_id,
            glossary_json=glossary_list,
        ))
        db.commit()

    try:
        with SessionLocal() as db:
            job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
            paper = db.query(Paper).filter(Paper.id == job.paper_id).first()
            structure = paper.structure_json or {}
            cleaned_markdown = structure.get("markdown", "")
            paper_title = paper.title or ""
            paper_title_zh = paper.title_zh or ""
            journal = paper.journal or ""
            year = paper.year
            doi = paper.doi or ""
            division_raw = paper.division or ""
            division_tags = [t.strip() for t in division_raw.split("、") if t.strip()] if division_raw else []
            translate_images_flag = job.translate_images if job.translate_images is not None else True

        if not cleaned_markdown:
            raise RuntimeError("清理后的 markdown 为空")

        # 将多行展示公式 $\nformula\n$ 归一化为 $$formula$$，避免被当成三个独立块
        cleaned_markdown = normalize_display_math(cleaned_markdown)

        # ── C：结构分类 ──────────────────────────────────────────────────────
        _push(job_id, JobStatus.TRANSLATING, 55, "正在分析文档结构...")
        chunks = split_by_window(cleaned_markdown)
        logger.warning("[pipeline] 阶段C：切分为 %d 个块", len(chunks))

        classified_chunks: list[list[dict]] = [None] * len(chunks)

        def _classify_worker(idx: int, chunk: str):
            objects = classify_chunk(chunk, chunk_idx=idx)
            objects = verify_references(objects, chunk_idx=idx)
            return idx, objects

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(_classify_worker, i, c): i for i, c in enumerate(chunks)}
            done = 0
            for future in as_completed(futures):
                orig_idx = futures[future]
                try:
                    idx, result = future.result()
                    classified_chunks[idx] = result
                except Exception as e:
                    logger.error("[pipeline] chunk %d 分类失败: %s", orig_idx, e, exc_info=True)
                    from app.translation.pipeline_core import _fallback_classify
                    classified_chunks[orig_idx] = _fallback_classify(chunks[orig_idx])
                done += 1
                progress = 55 + int(done / max(len(chunks), 1) * 5)
                _push(job_id, JobStatus.TRANSLATING, progress, f"结构分析进度 {done}/{len(chunks)} 块")

        flat_objects: list[dict] = []
        for i, chunk_items in enumerate(classified_chunks):
            if chunk_items is None:
                from app.translation.pipeline_core import _fallback_classify
                chunk_items = _fallback_classify(chunks[i])
            flat_objects.extend(chunk_items)

        logger.warning(
            "[pipeline] 阶段C完成：%d 个对象（段落=%d 标题=%d 参考文献=%d 图片=%d）",
            len(flat_objects),
            sum(1 for o in flat_objects if o.get("type") == "paragraph"),
            sum(1 for o in flat_objects if o.get("type") == "heading"),
            sum(1 for o in flat_objects if o.get("type") == "reference"),
            sum(1 for o in flat_objects if o.get("type") == "image"),
        )

        # ── D：段落翻译 ──────────────────────────────────────────────────────
        _push(job_id, JobStatus.TRANSLATING, 60, "开始逐段翻译...")
        translatable_indices = [
            i for i, obj in enumerate(flat_objects)
            if obj.get("type") in ("heading", "paragraph")
        ]
        logger.warning("[pipeline] 阶段D：%d 段需要翻译", len(translatable_indices))

        translated_texts: dict[int, str] = {}

        def _translate_worker(idx: int):
            obj = flat_objects[idx]
            zh = translate_paragraph(obj["text"], glossary_list, domain=domain_label, para_idx=idx)
            return idx, zh

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(_translate_worker, i): i for i in translatable_indices}
            done = 0
            for future in as_completed(futures):
                orig_idx = futures[future]
                try:
                    idx, zh = future.result()
                    translated_texts[idx] = zh
                except Exception as e:
                    logger.error("[pipeline] 段落 %d 翻译失败: %s", orig_idx, e, exc_info=True)
                    translated_texts[orig_idx] = ""
                done += 1
                progress = 60 + int(done / max(len(translatable_indices), 1) * 30)
                _push(job_id, JobStatus.TRANSLATING, progress, f"翻译进度 {done}/{len(translatable_indices)} 段")

        zhengwen: list[dict] = []
        all_references: list[str] = []

        for i, obj in enumerate(flat_objects):
            obj_type = obj.get("type")
            if obj_type == "heading":
                zhengwen.append({
                    "标题等级": obj.get("level", 1),
                    "文本": obj.get("text", ""),
                    "中文文本": translated_texts.get(i, ""),
                })
            elif obj_type == "paragraph":
                zhengwen.append({
                    "标题等级": 0,
                    "文本": obj.get("text", ""),
                    "中文文本": translated_texts.get(i, ""),
                })
            elif obj_type == "image":
                url = obj.get("url", "")
                zhengwen.append({"图片地址": url, "中文图片地址": url})
            elif obj_type == "reference":
                all_references.append(obj.get("text", ""))

        seen: set[str] = set()
        deduped_references: list[str] = []
        for ref in all_references:
            if ref and ref not in seen:
                deduped_references.append(ref)
                seen.add(ref)
        logger.warning("[pipeline] 参考文献 %d 条", len(deduped_references))

        # ── E：图片翻译 ──────────────────────────────────────────────────────
        image_indices = [i for i, b in enumerate(zhengwen) if "图片地址" in b]
        if image_indices and translate_images_flag:
            _push(job_id, JobStatus.TRANSLATING, 91, f"正在翻译 {len(image_indices)} 张图表...")
            from app.services.image_translation import translate_image
            from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _asc

            def _img_worker(idx: int):
                block = zhengwen[idx]
                new_url = translate_image(block["图片地址"], glossary_list, paper_id=paper_id, domain=domain_label)
                return idx, new_url

            with _TPE(max_workers=3) as executor:
                img_futures = {executor.submit(_img_worker, i): i for i in image_indices}
                done_imgs = 0
                for future in _asc(img_futures):
                    orig_i = img_futures[future]
                    try:
                        idx, new_url = future.result()
                        zhengwen[idx]["中文图片地址"] = new_url
                    except Exception as e:
                        logger.error("[pipeline] 图片 %d 翻译失败: %s", orig_i, e)
                    done_imgs += 1
                    _push(job_id, JobStatus.TRANSLATING,
                          91 + int(done_imgs / len(image_indices) * 4),
                          f"图表翻译进度 {done_imgs}/{len(image_indices)}")

        # ── F：保存结果 ──────────────────────────────────────────────────────
        _push(job_id, JobStatus.COMPLETED, 95, "保存翻译结果...")

        result_structure = {
            "标题": paper_title,
            "标题中文": paper_title_zh,
            "所属期刊/会议": journal,
            "年份": str(year) if year else "",
            "期刊/会议分类标签": division_tags,
            "DOI": doi,
            "正文": zhengwen,
            "参考文献": deduped_references,
        }

        with SessionLocal() as db:
            job_obj = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
            db.add(TranslationResult(
                id=str(uuid.uuid4()),
                job_id=job_id,
                paper_id=job_obj.paper_id,
                structure_json=result_structure,
            ))
            job_obj.status = JobStatus.COMPLETED
            job_obj.progress = 100
            job_obj.completed_at = datetime.utcnow()
            db.commit()

        _push(job_id, JobStatus.COMPLETED, 100, "翻译完成！")

    except Exception as e:
        logger.error("[pipeline] phase C-F 失败: %s", e, exc_info=True)
        with SessionLocal() as db:
            job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
            if job:
                job.status = JobStatus.FAILED
                job.error_message = str(e)
                db.commit()
        raise


# ─────────────────────────────────────────────────────────────────────────────
# 中文论文存档流水线（A + A.5 + B + C + F，无翻译/术语）
# ─────────────────────────────────────────────────────────────────────────────

def run_chinese_pipeline(job_id: str, pdf_bytes: bytes):
    try:
        with SessionLocal() as db:
            job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
            paper = db.query(Paper).filter(Paper.id == job.paper_id).first()
            paper_title = paper.title or ""
            paper_title_zh = paper.title_zh or paper_title
            journal = paper.journal or ""
            year = paper.year
            doi = paper.doi or ""
            division_raw = paper.division or ""
            division_tags = [t.strip() for t in division_raw.split("、") if t.strip()] if division_raw else []
            filename = paper.storage_key.split("/")[-1] if paper.storage_key else "document.pdf"
            paper_id = paper.id

        # ── A：PDF 解析 ──────────────────────────────────────────────────────
        _push(job_id, JobStatus.PARSING, 10, "正在解析 PDF 结构...")
        parsed = parse_pdf(pdf_bytes, filename=filename, paper_id=paper_id)
        markdown = parsed.get("markdown", "")

        # ── A.5：标题层级修正 ─────────────────────────────────────────────────
        _push(job_id, JobStatus.PARSING, 15, "正在校正章节标题层级...")
        markdown = fix_heading_levels(markdown, paper_title)

        # ── B：文本清理 ───────────────────────────────────────────────────────
        _push(job_id, JobStatus.POLISHING, 20, "正在清理文本...")
        chunks = split_by_window(markdown)

        cleaned_chunks: list[str] = [""] * len(chunks)

        def _cleanup_worker(idx: int, chunk: str):
            return idx, cleanup_chunk(chunk, chunk_idx=idx)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(_cleanup_worker, i, c): i for i, c in enumerate(chunks)}
            done = 0
            for future in as_completed(futures):
                orig_idx = futures[future]
                try:
                    idx, cleaned = future.result()
                except Exception as e:
                    logger.error("[chinese_pipeline] chunk %d 清理失败: %s", orig_idx, e)
                    idx, cleaned = orig_idx, chunks[orig_idx]
                cleaned_chunks[idx] = cleaned
                done += 1
                progress = 20 + int(done / max(len(chunks), 1) * 30)
                _push(job_id, JobStatus.POLISHING, progress, f"文本清理进度 {done}/{len(chunks)} 块")

        cleaned_markdown = "\n\n".join(cleaned_chunks)
        cleaned_markdown = normalize_display_math(cleaned_markdown)

        # ── C：结构分类 ───────────────────────────────────────────────────────
        _push(job_id, JobStatus.TRANSLATING, 55, "正在分析文档结构...")
        chunks2 = split_by_window(cleaned_markdown)
        classified_chunks: list[list[dict]] = [None] * len(chunks2)

        def _classify_worker(idx: int, chunk: str):
            objects = classify_chunk(chunk, chunk_idx=idx)
            objects = verify_references(objects, chunk_idx=idx)
            return idx, objects

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(_classify_worker, i, c): i for i, c in enumerate(chunks2)}
            done = 0
            for future in as_completed(futures):
                orig_idx = futures[future]
                try:
                    idx, result = future.result()
                    classified_chunks[idx] = result
                except Exception as e:
                    logger.error("[chinese_pipeline] chunk %d 分类失败: %s", orig_idx, e)
                    from app.translation.pipeline_core import _fallback_classify
                    classified_chunks[orig_idx] = _fallback_classify(chunks2[orig_idx])
                done += 1
                progress = 55 + int(done / max(len(chunks2), 1) * 35)
                _push(job_id, JobStatus.TRANSLATING, progress, f"结构分析进度 {done}/{len(chunks2)} 块")

        flat_objects: list[dict] = []
        for i, chunk_items in enumerate(classified_chunks):
            if chunk_items is None:
                from app.translation.pipeline_core import _fallback_classify
                chunk_items = _fallback_classify(chunks2[i])
            flat_objects.extend(chunk_items)

        # ── F：保存结果 ───────────────────────────────────────────────────────
        _push(job_id, JobStatus.COMPLETED, 95, "正在保存...")
        zhengwen: list[dict] = []
        all_references: list[str] = []

        for obj in flat_objects:
            obj_type = obj.get("type")
            if obj_type == "heading":
                zhengwen.append({
                    "标题等级": obj.get("level", 1),
                    "文本": obj.get("text", ""),
                })
            elif obj_type == "paragraph":
                zhengwen.append({
                    "标题等级": 0,
                    "文本": obj.get("text", ""),
                })
            elif obj_type == "image":
                zhengwen.append({"图片地址": obj.get("url", "")})
            elif obj_type == "reference":
                all_references.append(obj.get("text", ""))

        seen: set[str] = set()
        deduped_references = [r for r in all_references if r and not (seen.add(r) or r in seen)]

        result_structure = {
            "paper_type": "chinese",
            "标题": paper_title,
            "标题中文": paper_title_zh,
            "所属期刊/会议": journal,
            "年份": str(year) if year else "",
            "期刊/会议分类标签": division_tags,
            "DOI": doi,
            "正文": zhengwen,
            "参考文献": deduped_references,
        }

        with SessionLocal() as db:
            job_obj = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
            db.add(TranslationResult(
                id=str(uuid.uuid4()),
                job_id=job_id,
                paper_id=job_obj.paper_id,
                structure_json=result_structure,
            ))
            job_obj.status = JobStatus.COMPLETED
            job_obj.progress = 100
            job_obj.completed_at = datetime.utcnow()
            db.commit()

        _push(job_id, JobStatus.COMPLETED, 100, "存档完成！")

    except Exception as e:
        logger.error("[chinese_pipeline] 失败: %s", e, exc_info=True)
        with SessionLocal() as db:
            job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
            if job:
                job.status = JobStatus.FAILED
                job.error_message = str(e)
                db.commit()
        raise
