"""
chat_tools.py — 知识库对话 Agent 的工具集

工具列表：
 1. search_papers          — 搜索论文元数据（标题/摘要/关键词）
 2. get_paper_outline      — 获取论文章节大纲
 3. search_in_paper        — 在单篇论文全文中搜索（附带批注）
 4. get_paper_section      — 获取指定章节完整文本（无数量限制，中英拼接）
 5. get_references         — 获取参考文献列表
 6. get_annotations        — 获取某篇论文的所有批注
 7. search_annotations     — 跨全库搜索用户批注
 8. search_across_papers   — 跨全库全文搜索关键词
 9. get_paragraph_context  — 获取某段落的前后文
10. get_paper_metadata     — 快速获取论文元数据（标题/摘要/作者/关键词）
11. search_chat_history    — 搜索历史对话记录（L3 冷数据）
12. generate_image         — 文生图（qwen-image-2.0）
13. edit_image             — 图片编辑（qwen-image-2.0-pro）
"""

import base64
import json
import logging
import uuid
from typing import Optional

from app.database import SessionLocal, engine
from app.models.paper import Paper
from app.models.result import TranslationResult
from app.models.annotation import Annotation
from sqlalchemy import text as _sql_text
from app.services import memory as memory_svc

logger = logging.getLogger(__name__)


# ── 会话任务清单（对标 TodoWrite） ─────────────────────────────────────────────
# key = session_id，value = [{content, status: pending|in_progress|completed}]
_TODOS: dict[str, list[dict]] = {}


def todo_write(session_id: Optional[str], todos: list) -> str:
    """更新会话任务清单（对标 TodoWrite：content 祈使式 + active_form 进行式，同一时刻仅一项 in_progress）。"""
    cleaned = []
    for t in todos or []:
        if not isinstance(t, dict) or not t.get("content"):
            continue
        status = t.get("status", "pending")
        if status not in ("pending", "in_progress", "completed"):
            status = "pending"
        cleaned.append({
            "content": t["content"],
            "active_form": (t.get("active_form") or t["content"]),
            "status": status,
        })
    # 全部完成时清空列表（对标 TodoWrite：allDone → newTodos=[]）
    if cleaned and all(c["status"] == "completed" for c in cleaned):
        _TODOS[session_id or "_default"] = []
        return json.dumps({
            "todos": [],
            "count": 0,
            "cleared": True,
            "message": "任务清单已全部完成并清空。请继续推进，若仍有未决事项重新拆解。",
        }, ensure_ascii=False)
    _TODOS[session_id or "_default"] = cleaned
    return json.dumps({
        "todos": cleaned,
        "count": len(cleaned),
        "message": "任务清单已更新。请继续用它跟踪进度：同一时刻只保持一项 in_progress，完成一项立即标记 completed。",
    }, ensure_ascii=False)


def get_todos(session_id: Optional[str]) -> list[dict]:
    return _TODOS.get(session_id or "_default", [])


# ── 内部工具函数 ───────────────────────────────────────────────────────────────

def _get_result(paper_id: str) -> Optional[TranslationResult]:
    with SessionLocal() as db:
        return db.query(TranslationResult).filter(
            TranslationResult.paper_id == paper_id
        ).order_by(TranslationResult.created_at.desc()).first()


def _get_paper_annotations_raw(result_id: str) -> list[dict]:
    with SessionLocal() as db:
        anns = db.query(Annotation).filter(Annotation.result_id == result_id).all()
    result = []
    for ann in anns:
        item = {"scope": ann.scope, "content": ann.content}
        if ann.selected_text:
            item["selected_text"] = ann.selected_text
        if ann.block_id:
            item["block_id"] = ann.block_id
        result.append(item)
    return result


def _heading_context(blocks: list[dict], block_idx: int) -> str:
    for i in range(block_idx - 1, -1, -1):
        lvl = blocks[i].get("标题等级", 0)
        if lvl and lvl >= 1:
            return blocks[i].get("中文文本") or blocks[i].get("文本", "")
    return ""


def _annotations_note(annotations: list[dict]) -> str:
    if annotations:
        return "以上是与本次查询相关的个人批注，请在回答中适当引用。"
    return ""


def _filter_relevant_annotations(annotations: list[dict], keywords: list[str]) -> list[dict]:
    if not annotations or not keywords:
        return []
    kw_lower = [w.lower() for w in keywords if len(w) >= 2]
    if not kw_lower:
        return annotations
    result = []
    for ann in annotations:
        combined = ((ann.get("content") or "") + " " + (ann.get("selected_text") or "")).lower()
        if any(kw in combined for kw in kw_lower):
            result.append(ann)
    return result


def _cutoff_index(blocks: list[dict], preserve_recent_turns: int = 3) -> int:
    """计算"旧轮"的截止 block 下标（简单返回 0，供内部调用方自行决定范围）"""
    return 0


def _split_keywords(query: str) -> list[str]:
    """把查询拆成关键词。中文无空格 → 单 token（精确子串匹配）；英文按空格分词。"""
    q = (query or "").strip().lower()
    return [w for w in q.split() if w]


def _match_score(text: str, keywords: list[str]) -> tuple[bool, int]:
    """结构化块的关键词匹配（对标 grep 的精确匹配，但允许词序变化）。

    返回 (是否命中, 相关性分)：
    - 命中 = 所有关键词都在块内出现（AND 语义，避免英文多词退化成 OR 的误召回）；
    - 评分 = 连续短语命中最高（1000+），其次按各词出现次数累计（用于排序）。
    """
    if not keywords:
        return False, 0
    phrase = " ".join(keywords)
    if phrase in text:
        return True, 1000 + text.count(phrase)
    if all(k in text for k in keywords):
        return True, sum(text.count(k) for k in keywords)
    return False, 0


def _snippet(text: str, keywords: list[str], max_len: int = 150) -> str:
    """取关键词命中位置附近的片段（对标 grep 显示命中行，而非从块开头截断）。

    多关键词 AND 匹配时，优先让片段覆盖「所有关键词的跨度」（第一个到最后一个），
    这样片段能同时看到多个命中词；跨度过大时退化为居中第一个关键词。
    """
    t = (text or "").strip()
    if not t:
        return ""
    if len(t) <= max_len:
        return t
    low = t.lower()
    poses = [low.find(k) for k in keywords]
    poses = [p for p in poses if p >= 0]
    if not poses:
        return t[:max_len]
    lo, hi = min(poses), max(poses)
    if hi - lo <= max_len:
        # 关键词跨度能塞进窗口：取覆盖范围，两侧留一点
        start = max(0, lo - (max_len - (hi - lo)) // 2)
        end = min(len(t), start + max_len)
        start = max(0, end - max_len)
    else:
        # 跨度太大：居中第一个关键词
        start = max(0, lo - max_len // 3)
        end = min(len(t), start + max_len)
    return ("…" if start > 0 else "") + t[start:end] + ("…" if end < len(t) else "")


# ── 搜索范围（文件夹 → 论文 ID 集合） ─────────────────────────────────────────

def resolve_scope_paper_ids(folder_ids: list) -> Optional[set]:
    """把选中的文件夹（含其所有子文件夹）解析成论文 ID 集合。空列表返回 None（不限范围）。"""
    if not folder_ids:
        return None
    from app.models.folder import VirtualFolder, PaperFolderMapping
    with SessionLocal() as db:
        all_ids: set = set(folder_ids)
        def _collect(fid: int):
            for (cid,) in db.query(VirtualFolder.id).filter(VirtualFolder.parent_id == fid).all():
                if cid not in all_ids:
                    all_ids.add(cid)
                    _collect(cid)
        for fid in list(folder_ids):
            _collect(fid)
        paper_ids = {
            r[0] for r in db.query(PaperFolderMapping.paper_id)
            .filter(PaperFolderMapping.folder_id.in_(all_ids)).all()
        }
    return paper_ids or None


def _check_scope(paper_id: str, scope_paper_ids) -> Optional[str]:
    """若论文不在当前搜索范围内，返回错误 JSON；在范围内返回 None。"""
    if scope_paper_ids is not None and paper_id not in scope_paper_ids:
        return json.dumps({"error": "该论文不在当前对话的搜索范围内"}, ensure_ascii=False)
    return None


# 需要注入「搜索范围」的工具（读论文/搜索类）
_SCOPED_TOOLS = {
    "search_papers", "get_paper_outline", "search_in_paper", "get_paper_section",
    "get_references", "get_annotations", "search_annotations", "search_across_papers",
    "get_paragraph_context", "get_paper_metadata",
}


# ── 工具 1-7（原有） ────────────────────────────────────────────────────────────

def search_papers(query: str, domain: Optional[str] = None, scope_paper_ids=None) -> str:
    keywords = _split_keywords(query)
    with SessionLocal() as db:
        papers = db.query(Paper).all()

    results = []
    for p in papers:
        if scope_paper_ids is not None and p.id not in scope_paper_ids:
            continue
        if domain and p.domain and domain.lower() not in p.domain.lower():
            continue
        searchable = " ".join(filter(None, [
            p.title or "", p.title_zh or "",
            p.abstract or "", p.abstract_zh or "",
            " ".join(p.keywords or []) if p.keywords else "",
            p.domain or "", p.journal or "",
        ])).lower()
        matched, score = _match_score(searchable, keywords)
        if matched:
            results.append((score, p))

    results.sort(key=lambda x: x[0], reverse=True)
    # 不设数量上限：有多少匹配返回多少，避免漏掉相关论文
    output = []
    for _, p in results:
        output.append({
            "paper_id": p.id,
            "title": p.title or "",
            "title_zh": p.title_zh or "",
            "year": p.year,
            "domain": p.domain or "",
            "journal": p.journal or "",
            "abstract_snippet": (p.abstract_zh or p.abstract or "")[:200],
        })

    return json.dumps(
        {"total_found": len(output), "results": output}
        if output else
        {"total_found": 0, "results": [], "message": "未找到相关论文"},
        ensure_ascii=False,
    )


def get_paper_outline(paper_id: str, scope_paper_ids=None) -> str:
    err = _check_scope(paper_id, scope_paper_ids)
    if err:
        return err
    result = _get_result(paper_id)
    if not result:
        return json.dumps({"error": "未找到该论文的处理结果"}, ensure_ascii=False)

    blocks = result.structure_json.get("正文", [])
    headings = [
        {
            "level": b.get("标题等级"),
            "heading_en": b.get("文本", ""),
            "heading_zh": b.get("中文文本", ""),
        }
        for b in blocks if b.get("标题等级", 0) >= 1
    ]
    return json.dumps({
        "paper": {
            "title": result.structure_json.get("标题", ""),
            "title_zh": result.structure_json.get("标题中文", ""),
            "year": result.structure_json.get("年份", ""),
        },
        "outline": headings,
    }, ensure_ascii=False)


def search_in_paper(paper_id: str, query: str, section: Optional[str] = None, context: int = 0, scope_paper_ids=None) -> str:
    err = _check_scope(paper_id, scope_paper_ids)
    if err:
        return err
    result = _get_result(paper_id)
    if not result:
        return json.dumps({"error": "未找到该论文的处理结果"}, ensure_ascii=False)

    blocks = result.structure_json.get("正文", [])
    keywords = _split_keywords(query)

    s_start, s_end = 0, len(blocks)
    if section:
        sec_lower = section.lower()
        found = False
        found_level = 0
        for i, b in enumerate(blocks):
            lvl = b.get("标题等级", 0)
            if lvl >= 1:
                text = (b.get("文本", "") + " " + b.get("中文文本", "")).lower()
                if sec_lower in text and not found:
                    s_start, found_level, found = i, lvl, True
                elif found and lvl <= found_level:
                    s_end = i
                    break

    matches = []
    for idx in range(s_start, s_end):
        b = blocks[idx]
        if b.get("图片地址"):
            continue
        en, zh = b.get("文本", ""), b.get("中文文本", "")
        combined = (en + " " + zh).lower()
        matched, score = _match_score(combined, keywords)
        if matched:
            m = {
                "block_idx": idx,
                "heading_context": _heading_context(blocks, idx),
                "text_en": _snippet(en, keywords),
                "text_zh": _snippet(zh, keywords),
                "score": score,
            }
            # 对标 grep -C：context>0 时内联返回命中块前后各 context 个块的片段，
            # 让模型一次调用就能看到上下文，不必二次 get_paragraph_context。
            if context > 0:
                ctx = []
                for j in range(max(s_start, idx - context), min(s_end, idx + context + 1)):
                    if j == idx:
                        continue
                    bj = blocks[j]
                    if bj.get("图片地址"):
                        continue
                    ctx.append({
                        "block_idx": j,
                        "text_en": (bj.get("文本", "") or "")[:100],
                        "text_zh": (bj.get("中文文本", "") or "")[:100],
                    })
                if ctx:
                    m["context"] = ctx
            matches.append(m)

    matches.sort(key=lambda x: x["score"], reverse=True)
    all_annotations = _get_paper_annotations_raw(result.id)
    ann_keywords = keywords + (section.lower().split() if section else [])
    relevant_annotations = _filter_relevant_annotations(all_annotations, ann_keywords)

    out: dict = {"paper_id": paper_id, "total_found": len(matches), "matches": matches}
    if relevant_annotations:
        out["annotations"] = relevant_annotations
        out["annotation_note"] = _annotations_note(relevant_annotations)
    return json.dumps(out, ensure_ascii=False)


def get_paper_section(paper_id: str, heading: str, offset: int = 0, scope_paper_ids=None) -> str:
    """获取指定章节的完整文本内容（从该标题到下一同级/高级标题，排除图片块）。
    每段前带 [block_idx]（对标 Read 的 cat -n 行号），读完可直接用 block_idx 引用/精读。
    若章节过长可通过 offset 分页（每页 50 段）。
    """
    err = _check_scope(paper_id, scope_paper_ids)
    if err:
        return err
    result = _get_result(paper_id)
    if not result:
        return json.dumps({"error": "未找到该论文的处理结果"}, ensure_ascii=False)

    blocks = result.structure_json.get("正文", [])
    heading_lower = heading.lower()

    start_idx, start_level = -1, 0
    for i, b in enumerate(blocks):
        lvl = b.get("标题等级", 0)
        if lvl >= 1:
            text = (b.get("文本", "") + " " + b.get("中文文本", "")).lower()
            if heading_lower in text:
                start_idx, start_level = i, lvl
                break

    if start_idx == -1:
        available = [b.get("文本", "") for b in blocks if b.get("标题等级", 0) >= 1][:20]
        return json.dumps({"error": f"未找到标题 '{heading}'", "available_headings": available}, ensure_ascii=False)

    paragraphs = []
    for i in range(start_idx + 1, len(blocks)):
        b = blocks[i]
        lvl = b.get("标题等级", 0)
        if lvl >= 1 and lvl <= start_level:
            break
        if b.get("图片地址"):
            continue
        if b.get("文本") or b.get("中文文本"):
            paragraphs.append({
                "block_idx": i,
                "text_en": b.get("文本", ""),
                "text_zh": b.get("中文文本", ""),
            })

    PAGE_SIZE = 50
    total = len(paragraphs)
    page = paragraphs[offset: offset + PAGE_SIZE]

    # cat -n 风格：每段前带 [block_idx]，让 agent 读完能定位到具体块
    content_zh = "\n".join(f"[{p['block_idx']}] {p['text_zh']}" for p in page if p["text_zh"])
    content_en = "\n".join(f"[{p['block_idx']}] {p['text_en']}" for p in page if p["text_en"])

    all_annotations = _get_paper_annotations_raw(result.id)
    sec_keywords = (
        blocks[start_idx].get("文本", "") + " " + blocks[start_idx].get("中文文本", "")
    ).lower().split()
    relevant_annotations = _filter_relevant_annotations(all_annotations, sec_keywords)

    out: dict = {
        "paper_id": paper_id,
        "section_heading": blocks[start_idx].get("文本", ""),
        "section_heading_zh": blocks[start_idx].get("中文文本", ""),
        "content_zh": content_zh,
        "content_en": content_en,
        "paragraph_count": total,
        "offset": offset,
        "has_more": (offset + PAGE_SIZE) < total,
        "block_idx_range": [page[0]["block_idx"], page[-1]["block_idx"]] if page else None,
    }
    if relevant_annotations:
        out["annotations"] = relevant_annotations
        out["annotation_note"] = _annotations_note(relevant_annotations)
    return json.dumps(out, ensure_ascii=False)


def get_references(paper_id: str, scope_paper_ids=None) -> str:
    err = _check_scope(paper_id, scope_paper_ids)
    if err:
        return err
    result = _get_result(paper_id)
    if not result:
        return json.dumps({"error": "未找到该论文的处理结果"}, ensure_ascii=False)
    refs = result.structure_json.get("参考文献", [])
    return json.dumps({"paper_id": paper_id, "references": refs}, ensure_ascii=False)


def get_annotations(paper_id: str, scope_paper_ids=None) -> str:
    err = _check_scope(paper_id, scope_paper_ids)
    if err:
        return err
    result = _get_result(paper_id)
    if not result:
        return json.dumps({"error": "未找到该论文的处理结果"}, ensure_ascii=False)
    annotations = _get_paper_annotations_raw(result.id)
    return json.dumps({
        "paper_id": paper_id,
        "annotations": annotations,
        "count": len(annotations),
        "message": "暂无批注" if not annotations else f"共 {len(annotations)} 条批注",
    }, ensure_ascii=False)


def _annotation_tokens(query: str) -> list[str]:
    import re
    tokens: set[str] = set()
    q = query.strip().lower()
    for w in q.split():
        if w:
            tokens.add(w)
    for seq in re.findall(r'[\u4e00-\u9fff]+', q):
        tokens.add(seq)
        for l in (3, 2):
            if len(seq) >= l:
                for i in range(len(seq) - l + 1):
                    tokens.add(seq[i:i+l])
    return [t for t in tokens if len(t) >= 2]


def search_annotations(query: str, scope_paper_ids=None) -> str:
    tokens = _annotation_tokens(query)
    if not tokens:
        tokens = [query.lower().strip()]
    results = []
    with SessionLocal() as db:
        all_anns = db.query(Annotation).all()
        for ann in all_anns:
            combined = ((ann.content or "") + " " + (ann.selected_text or "")).lower()
            if any(t in combined for t in tokens):
                tr = db.query(TranslationResult).filter(
                    TranslationResult.id == ann.result_id
                ).first()
                if scope_paper_ids is not None and (not tr or tr.paper_id not in scope_paper_ids):
                    continue
                paper = db.query(Paper).filter(Paper.id == tr.paper_id).first() if tr else None
                results.append({
                    "paper_id": tr.paper_id if tr else "",
                    "paper_title": (paper.title_zh or paper.title) if paper else "未知论文",
                    "scope": ann.scope,
                    "content": ann.content,
                    "selected_text": ann.selected_text or "",
                    "block_id": ann.block_id or "",
                })
    return json.dumps({"query": query, "results": results, "count": len(results)}, ensure_ascii=False)


# ── 工具 8-11（新增） ──────────────────────────────────────────────────────────

def search_across_papers(keyword: str, offset: int = 0, scope_paper_ids=None) -> str:
    """在全库所有论文全文中搜索关键词，返回全部匹配段落（不设数量上限，可 offset 翻页）。"""
    keywords = _split_keywords(keyword)
    all_matches = []

    with SessionLocal() as db:
        results = db.query(TranslationResult).all()
        paper_map = {p.id: p for p in db.query(Paper).all()}

    for tr in results:
        if scope_paper_ids is not None and tr.paper_id not in scope_paper_ids:
            continue
        paper = paper_map.get(tr.paper_id)
        if not paper:
            continue
        blocks = tr.structure_json.get("正文", [])
        for idx, b in enumerate(blocks):
            if b.get("图片地址"):
                continue
            en = b.get("文本", "")
            zh = b.get("中文文本", "")
            combined = (en + " " + zh).lower()
            matched, score = _match_score(combined, keywords)
            if matched:
                all_matches.append({
                    "paper_id": tr.paper_id,
                    "paper_title": paper.title_zh or paper.title or "",
                    "block_idx": idx,
                    "heading_context": _heading_context(blocks, idx),
                    "text_zh": _snippet(zh, keywords),
                    "text_en": _snippet(en, keywords),
                    "score": score,
                })

    all_matches.sort(key=lambda x: x["score"], reverse=True)
    # 聚合视图：每篇命中论文 + 命中数（对标 Grep files_with_matches）
    agg: dict[str, int] = {}
    for m in all_matches:
        agg[m["paper_id"]] = agg.get(m["paper_id"], 0) + 1
    papers = [
        {
            "paper_id": pid,
            "paper_title": next(m["paper_title"] for m in all_matches if m["paper_id"] == pid),
            "match_count": cnt,
        }
        for pid, cnt in sorted(agg.items(), key=lambda x: -x[1])
    ]
    # 内容视图（对标 Grep content + head_limit/offset 分页）：只返回一页带正文的命中，
    # 避免几百条正文撑爆上下文。全貌由 total_found + papers 聚合给出（不设上限），
    # 用 offset 翻页看剩余命中。
    MAX_RESULTS = 30
    page = all_matches[offset: offset + MAX_RESULTS]
    out: dict = {
        "keyword": keyword,
        "total_found": len(all_matches),
        "papers": papers,
        "results": page,
        "offset": offset,
        "limit": MAX_RESULTS,
    }
    if offset + MAX_RESULTS < len(all_matches):
        out["has_more"] = True
        out["hint"] = (
            f"已显示第 {offset}–{offset + len(page) - 1} 条，共 {len(all_matches)} 条命中。"
            f"用 offset={offset + MAX_RESULTS} 继续翻页；或 search_in_paper(paper_id, keyword) 精读单篇。"
        )
    return json.dumps(out, ensure_ascii=False)


def get_paragraph_context(paper_id: str, block_idx: int, window: int = 2, scope_paper_ids=None) -> str:
    """以指定段落为中心，获取前后 window 个段落的完整内容（排除图片块）。"""
    err = _check_scope(paper_id, scope_paper_ids)
    if err:
        return err
    result = _get_result(paper_id)
    if not result:
        return json.dumps({"error": "未找到该论文的处理结果"}, ensure_ascii=False)

    blocks = result.structure_json.get("正文", [])
    if block_idx < 0 or block_idx >= len(blocks):
        return json.dumps({"error": f"block_idx {block_idx} 超出范围 (0-{len(blocks)-1})"}, ensure_ascii=False)

    context = []
    start = max(0, block_idx - window)
    end = min(len(blocks), block_idx + window + 1)

    for i in range(start, end):
        b = blocks[i]
        if b.get("图片地址"):
            continue
        context.append({
            "block_idx": i,
            "is_target": i == block_idx,
            "heading_context": _heading_context(blocks, i),
            "text_en": b.get("文本", "")[:600],
            "text_zh": b.get("中文文本", "")[:600],
        })

    return json.dumps({
        "paper_id": paper_id,
        "target_block_idx": block_idx,
        "context": context,
    }, ensure_ascii=False)


def get_paper_metadata(paper_id: str, scope_paper_ids=None) -> str:
    """快速获取论文元数据（标题、摘要、作者、年份、期刊、关键词）。"""
    err = _check_scope(paper_id, scope_paper_ids)
    if err:
        return err
    with SessionLocal() as db:
        paper = db.query(Paper).filter(Paper.id == paper_id).first()

    if not paper:
        return json.dumps({"error": "未找到该论文"}, ensure_ascii=False)

    return json.dumps({
        "paper_id": paper_id,
        "title": paper.title or "",
        "title_zh": paper.title_zh or "",
        "abstract": (paper.abstract_zh or paper.abstract or "")[:500],
        "authors": paper.authors or [],
        "year": paper.year,
        "journal": paper.journal or "",
        "domain": paper.domain or "",
        "keywords": paper.keywords or [],
    }, ensure_ascii=False)


def query_database(sql: str) -> str:
    """执行只读 SQL SELECT 查询，返回结果（最多 100 行）。"""
    sql_stripped = sql.strip()

    # 安全检查：只允许 SELECT，禁止多语句
    if not sql_stripped.upper().startswith("SELECT"):
        return json.dumps({"error": "只允许 SELECT 查询"}, ensure_ascii=False)
    if ";" in sql_stripped[:-1]:
        return json.dumps({"error": "不允许多语句"}, ensure_ascii=False)

    try:
        with engine.connect() as conn:
            result = conn.execute(_sql_text(sql_stripped))
            rows = [dict(row._mapping) for row in result.fetchmany(100)]
        return json.dumps({"rows": rows, "count": len(rows)}, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def search_chat_history(query: str, limit=5) -> str:
    """在所有历史对话的 AI 回复中搜索关键词（L3 冷数据）。"""
    from app.models.chat import ChatMessage, ChatSession
    query_lower = f"%{query.lower()}%"
    results = []

    with SessionLocal() as db:
        matches = (
            db.query(ChatMessage, ChatSession)
            .join(ChatSession, ChatMessage.session_id == ChatSession.id)
            .filter(
                ChatMessage.role == "assistant",
                ChatMessage.content.ilike(query_lower),
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(int(limit))
            .all()
        )
        for msg, sess in matches:
            snippet_start = msg.content.lower().find(query.lower())
            if snippet_start >= 0:
                snippet = msg.content[max(0, snippet_start - 50): snippet_start + 200]
            else:
                snippet = msg.content[:200]
            results.append({
                "session_id": sess.id,
                "session_title": sess.title,
                "created_at": str(msg.created_at),
                "snippet": snippet,
            })

    return json.dumps({
        "query": query,
        "results": results,
        "count": len(results),
        "message": "未找到相关历史对话" if not results else f"找到 {len(results)} 条历史记录",
    }, ensure_ascii=False)


# ── 图片生成 / 编辑 ────────────────────────────────────────────────────────────

def _call_image_api(payload: dict) -> str:
    """调用 DashScope 图片生成接口，返回生成图片的外部 URL。"""
    import httpx
    from app.config import settings
    from app.services.image_translation import _http_post_with_retry
    resp = _http_post_with_retry(
        url=f"{settings.QWEN_DASHSCOPE_BASE_URL}/services/aigc/multimodal-generation/generation",
        headers={"Authorization": f"Bearer {settings.QWEN_API_KEY}", "Content-Type": "application/json"},
        body=payload,
        timeout=120,
    )
    data = resp.json()
    choices = data.get("output", {}).get("choices", [])
    if not choices:
        raise ValueError(f"API 无输出: {str(data)[:200]}")
    content = choices[0].get("message", {}).get("content", [])
    img_url = next((c["image"] for c in content if "image" in c), None)
    if not img_url:
        raise ValueError(f"响应中无图片: {str(content)[:200]}")
    return img_url


def _save_remote_image(remote_url: str, filename: str) -> str:
    """下载远程图片，保存到 chat_generated/，返回本地 /uploads/... URL。"""
    import httpx
    from app.storage.local_storage import local_storage
    with httpx.Client(trust_env=False, timeout=60) as client:
        r = client.get(remote_url)
        r.raise_for_status()
    key = f"chat_generated/{filename}"
    local_storage.put_object(key, r.content, content_type="image/jpeg")
    return local_storage.get_url(key)


def generate_image(prompt: str) -> str:
    """根据提示词生成图片，返回图片 URL。"""
    from app.config import settings
    if not settings.QWEN_API_KEY:
        return json.dumps({"error": "QWEN_API_KEY 未配置"}, ensure_ascii=False)
    logger.info("[chat_tools] generate_image prompt=%r", prompt[:80])
    try:
        payload = {
            "model": settings.QWEN_IMAGE_GEN_MODEL,
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
            "parameters": {"n": 1, "watermark": False},
        }
        remote_url = _call_image_api(payload)
        local_url = _save_remote_image(remote_url, f"{uuid.uuid4().hex}.jpg")
        return json.dumps({"image_url": local_url, "prompt_used": prompt}, ensure_ascii=False)
    except Exception as e:
        logger.error("[chat_tools] generate_image 失败: %s", e)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def edit_image(image_url: str, instruction: str) -> str:
    """对已有图片按指令编辑，返回新图片 URL。"""
    from app.config import settings
    from app.storage.local_storage import local_storage
    if not settings.QWEN_API_KEY:
        return json.dumps({"error": "QWEN_API_KEY 未配置"}, ensure_ascii=False)
    logger.info("[chat_tools] edit_image url=%r instruction=%r", image_url, instruction[:80])
    try:
        # 读取本地图片
        key = image_url.lstrip("/")
        if key.startswith("uploads/"):
            key = key[len("uploads/"):]
        img_bytes = local_storage.get_object(key)
        b64 = base64.b64encode(img_bytes).decode()
        payload = {
            "model": settings.QWEN_IMAGE_MODEL,
            "input": {"messages": [{"role": "user", "content": [
                {"image": f"data:image/jpeg;base64,{b64}"},
                {"text": instruction},
            ]}]},
            "parameters": {"n": 1, "watermark": False, "prompt_extend": False},
        }
        remote_url = _call_image_api(payload)
        local_url = _save_remote_image(remote_url, f"{uuid.uuid4().hex}_edited.jpg")
        return json.dumps({"image_url": local_url}, ensure_ascii=False)
    except Exception as e:
        logger.error("[chat_tools] edit_image 失败: %s", e)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ── 工具注册表 + OpenAI-compatible specs ──────────────────────────────────────

TOOL_REGISTRY = {
    "search_papers":         search_papers,
    "get_paper_outline":     get_paper_outline,
    "search_in_paper":       search_in_paper,
    "get_paper_section":     get_paper_section,
    "get_references":        get_references,
    "get_annotations":       get_annotations,
    "search_annotations":    search_annotations,
    "search_across_papers":  search_across_papers,
    "get_paragraph_context": get_paragraph_context,
    "get_paper_metadata":    get_paper_metadata,
    "search_chat_history":   search_chat_history,
    "query_database":        query_database,
    "generate_image":        generate_image,
    "edit_image":            edit_image,
    "save_memory":           memory_svc.save_memory,
    "list_memories":         memory_svc.list_memories,
    "delete_memory":         memory_svc.delete_memory,
}

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "Search paper titles, abstracts, and keywords to find relevant papers. Returns ALL matching papers (no limit). Start with this when the user asks about a topic or paper.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keywords"},
                    "domain": {"type": "string", "description": "Optional academic domain filter"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_paper_outline",
            "description": "Get the section headings outline of a paper. Use to understand structure before deciding which sections to read.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string", "description": "Paper ID from search results"},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_paper",
            "description": "Search for specific content in a single paper's full text. Returns ALL matching blocks (no limit), each with block_idx + short snippet; also returns relevant user annotations. Pass context=N to inline-return N surrounding blocks around each match (like grep -C), so you can see context without a second get_paragraph_context call. Never skip Introduction — it often contains core contributions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "query": {"type": "string", "description": "Keywords to search (AND semantics: all words must appear)"},
                    "section": {"type": "string", "description": "Optional: limit search to a specific section heading"},
                    "context": {"type": "integer", "description": "Optional: number of blocks before/after each match to include inline (grep -C equivalent)", "default": 0},
                },
                "required": ["paper_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_paper_section",
            "description": "Get the COMPLETE text of a named section (from that heading to the next same-level heading), excluding images. Returns full concatenated Chinese and English text with no paragraph limit. Use after confirming the section is relevant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "heading": {"type": "string", "description": "Section heading (partial match OK, e.g. 'method', 'experiment')"},
                    "offset": {"type": "integer", "description": "Paragraph offset for very long sections (default 0, page size 50)", "default": 0},
                },
                "required": ["paper_id", "heading"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_references",
            "description": "Get the reference list of a paper.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_annotations",
            "description": "Get all user annotations for a paper. Annotations are the user's personal insights — highly valuable, prioritize them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_annotations",
            "description": "Search across ALL papers' annotations. Use when user asks 'what did I note about X' or mentions a name/entity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords to search in user annotations"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_across_papers",
            "description": "Search a keyword across ALL papers' full text at once. Returns total_found + a per-paper aggregation view (which papers matched + match count, no limit) + a page of top content results (30 per page). Use offset=N to page through more content results. Use for cross-paper questions like 'which papers discuss X' or when you don't know which paper contains specific content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Keyword(s) to search across all papers (AND semantics)"},
                    "offset": {"type": "integer", "description": "Skip first N content results for pagination (default 0)", "default": 0},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_paragraph_context",
            "description": "Get the surrounding paragraphs around a specific block. Use after search_in_paper finds a relevant block to understand the full context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "block_idx": {"type": "integer", "description": "The block index from search results"},
                    "window": {"type": "integer", "description": "Number of paragraphs before and after to include (default 2)", "default": 2},
                },
                "required": ["paper_id", "block_idx"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_paper_metadata",
            "description": "Get a paper's metadata (title, abstract, authors, year, journal, keywords) given its paper_id. Use when you already have a paper_id and need quick context without reading sections.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_chat_history",
            "description": "Search previous conversation history for mentions of a topic. Use when the user asks 'did we discuss X before' or you need context from past sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords to search in past conversation history"},
                    "limit": {"type": "integer", "description": "Max results (default 5)", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": """Execute a read-only SQL SELECT query against the database. Use this for complex or ad-hoc queries that other tools cannot express.

DATABASE SCHEMA:

papers (论文元数据):
  id TEXT, title TEXT, title_zh TEXT, abstract TEXT, abstract_zh TEXT,
  domain TEXT, year INTEGER, journal TEXT, authors JSON (array of strings),
  keywords JSON (array of strings), doi TEXT, page_count INTEGER, created_at TEXT

translation_results (翻译结果，含论文正文结构):
  id TEXT, paper_id TEXT → papers.id, created_at TEXT
  (structure_json 字段存论文全文，体积大，请勿 SELECT)

annotations (用户批注):
  id TEXT, result_id TEXT → translation_results.id,
  scope TEXT ('global'|'inline'), content TEXT, selected_text TEXT,
  block_id TEXT, created_at TEXT
  ⚠️ 批注通过 translation_results 关联 papers:
     annotations JOIN translation_results tr ON annotations.result_id = tr.id
     JOIN papers p ON tr.paper_id = p.id

chat_sessions (对话会话):
  id TEXT, title TEXT, auto_summary TEXT, created_at TEXT, updated_at TEXT

chat_messages (对话消息):
  id TEXT, session_id TEXT → chat_sessions.id,
  role TEXT ('user'|'assistant'), content TEXT, created_at TEXT

RULES:
- Only SELECT statements allowed
- Do NOT select structure_json column
- Results capped at 100 rows
""",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "A valid SQL SELECT statement"},
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "根据提示词生成一张图片。当需要用图片可视化论文核心概念、流程、对比关系时使用。生成后将返回的 image_url 以 Markdown ![说明](url) 格式嵌入回答，提示词应尽量丰富具体。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "详细的图片生成提示词，描述内容、布局、风格、颜色、标注等"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_image",
            "description": "对已有图片按指令进行修改。仅在用户明确要求编辑某张图片时调用，需提供图片 URL 和修改说明。",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_url": {"type": "string", "description": "要编辑的图片 URL（/uploads/chat_generated/... 格式）"},
                    "instruction": {"type": "string", "description": "编辑指令，描述要做什么修改"},
                },
                "required": ["image_url", "instruction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": "创建/更新会话任务清单，用于拆解复杂问题（如「对比多篇论文的方法」）。todos 是完整列表，每项含 content（祈使式，如「读 A 的方法段」）、active_form（进行式，如「正在读 A 的方法段」）和 status。同一时刻只有一项 in_progress；开始做就标 in_progress，完成立即标 completed（不要攒着）；全部完成则列表清空。",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "完整的任务列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "祈使式任务描述"},
                                "active_form": {"type": "string", "description": "进行式描述（执行中显示）"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                            },
                            "required": ["content", "status"],
                        },
                    },
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "保存/更新一条跨会话持久记忆。当了解到用户的角色/偏好/研究主题/外部资源指针，或用户明确要求记住时使用。name 用英文短横线 slug（同名字覆盖更新）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["user", "feedback", "project", "reference"], "description": "记忆类型"},
                    "name": {"type": "string", "description": "英文短横线 slug，如 user_research_domain"},
                    "description": {"type": "string", "description": "一行描述，用于日后判断相关性"},
                    "content": {"type": "string", "description": "记忆正文（feedback/project 用「规则 + Why + How to apply」结构）"},
                },
                "required": ["type", "name", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_memories",
            "description": "列出所有跨会话持久记忆（含正文），用于检查是否已有可更新的记忆、或用户要求回忆时查询。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_memory",
            "description": "删除一条跨会话持久记忆（用户要求忘记、或记忆已过时/错误时使用）。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "记忆的 name slug"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_research",
            "description": "并行分派多个只读研究子代理，各自搜索/阅读论文库并返回总结。用于需要读多篇论文的复杂问题（如「对比多篇论文的方法」）：把每个独立研究点写成一个自包含的 prompt，一次性并行分派，子代理返回总结后你再综合。子代理看不到主对话，所以每个 prompt 要写清楚读哪篇论文、要回答什么。",
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "要并行分派的研究子任务列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task_id": {"type": "string", "description": "简短标识，用于区分各子任务结果"},
                                "prompt": {"type": "string", "description": "自包含的研究问题：指定读哪篇论文/哪个方面、要回答什么（子代理看不到主对话）"},
                            },
                            "required": ["prompt"],
                        },
                    },
                },
                "required": ["tasks"],
            },
        },
    },
]


def execute_tool(name: str, args: dict, session_id: Optional[str] = None, call_deepseek=None, on_event=None, scope_paper_ids=None) -> str:
    # todo_write 需要会话上下文来存储任务清单，特殊处理
    if name == "todo_write":
        return todo_write(session_id, (args or {}).get("todos", []))

    # spawn_research 需要并发跑子代理（需 call_deepseek），特殊处理（惰性导入避免循环依赖）
    if name == "spawn_research":
        if call_deepseek is None:
            return json.dumps({"error": "spawn_research 需要 call_deepseek"}, ensure_ascii=False)
        from app.services import subagent
        return subagent.spawn_research((args or {}).get("tasks", []), call_deepseek, execute_tool, TOOL_SPECS, on_event=on_event, scope_paper_ids=scope_paper_ids)

    func = TOOL_REGISTRY.get(name)
    if not func:
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
    try:
        # 需要搜索范围注入的工具
        if name in _SCOPED_TOOLS:
            return func(scope_paper_ids=scope_paper_ids, **args)
        return func(**args)
    except Exception as e:
        logger.error(f"[chat_tools] 工具 {name} 失败: {e}", exc_info=True)
        return json.dumps({"error": str(e)}, ensure_ascii=False)
