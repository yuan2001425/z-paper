"""
chat_tools.py — 知识库对话 Agent 的工具集

工具列表：
1. search_papers       — 搜索论文元数据（标题/摘要/关键词）
2. get_paper_outline   — 获取论文章节大纲
3. search_in_paper     — 在论文全文中搜索（自动附带该论文批注）
4. get_paper_section   — 获取特定章节全部段落（自动附带该论文批注）
5. get_references      — 获取参考文献列表
6. get_annotations     — 获取某篇论文的所有批注
7. search_annotations  — 跨全库搜索用户批注
"""

import json
import logging
from typing import Optional

from app.database import SessionLocal
from app.models.paper import Paper
from app.models.result import TranslationResult
from app.models.annotation import Annotation

logger = logging.getLogger(__name__)


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
    """只保留内容与关键词有交集的批注，避免无关批注污染回答"""
    if not annotations or not keywords:
        return []
    kw_lower = [w.lower() for w in keywords if len(w) >= 2]
    if not kw_lower:
        return annotations  # 关键词太短时全部返回
    result = []
    for ann in annotations:
        combined = ((ann.get("content") or "") + " " + (ann.get("selected_text") or "")).lower()
        if any(kw in combined for kw in kw_lower):
            result.append(ann)
    return result


# ── 7 个工具实现 ───────────────────────────────────────────────────────────────

def search_papers(query: str, domain: Optional[str] = None, limit: int = 5) -> str:
    query_lower = query.lower()
    with SessionLocal() as db:
        papers = db.query(Paper).all()

    results = []
    for p in papers:
        if domain and p.domain and domain.lower() not in p.domain.lower():
            continue
        searchable = " ".join(filter(None, [
            p.title or "", p.title_zh or "",
            p.abstract or "", p.abstract_zh or "",
            " ".join(p.keywords or []) if p.keywords else "",
            p.domain or "", p.journal or "",
        ])).lower()
        score = sum(1 for word in query_lower.split() if word in searchable)
        if score > 0:
            results.append((score, p))

    results.sort(key=lambda x: x[0], reverse=True)
    output = []
    for _, p in results[:limit]:
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
        {"results": output} if output else {"results": [], "message": "未找到相关论文"},
        ensure_ascii=False,
    )


def get_paper_outline(paper_id: str) -> str:
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


def search_in_paper(paper_id: str, query: str, section: Optional[str] = None) -> str:
    result = _get_result(paper_id)
    if not result:
        return json.dumps({"error": "未找到该论文的处理结果"}, ensure_ascii=False)

    blocks = result.structure_json.get("正文", [])
    query_lower = query.lower()

    # 定位章节范围
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
        score = sum(1 for w in query_lower.split() if w in combined)
        if score > 0:
            matches.append({
                "block_idx": idx,
                "heading_context": _heading_context(blocks, idx),
                "text_en": en[:500],
                "text_zh": zh[:500],
                "score": score,
            })

    matches.sort(key=lambda x: x["score"], reverse=True)
    all_annotations = _get_paper_annotations_raw(result.id)
    keywords = query_lower.split() + (section.lower().split() if section else [])
    relevant_annotations = _filter_relevant_annotations(all_annotations, keywords)

    out: dict = {"paper_id": paper_id, "matches": matches[:8]}
    if relevant_annotations:
        out["annotations"] = relevant_annotations
        out["annotation_note"] = _annotations_note(relevant_annotations)
    return json.dumps(out, ensure_ascii=False)


def get_paper_section(paper_id: str, heading: str) -> str:
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
                "text_en": b.get("文本", "")[:600],
                "text_zh": b.get("中文文本", "")[:600],
            })

    all_annotations = _get_paper_annotations_raw(result.id)
    # 用章节标题作为关键词过滤相关批注
    sec_keywords = (
        blocks[start_idx].get("文本", "") + " " + blocks[start_idx].get("中文文本", "")
    ).lower().split()
    relevant_annotations = _filter_relevant_annotations(all_annotations, sec_keywords)

    out: dict = {
        "paper_id": paper_id,
        "section_heading": blocks[start_idx].get("文本", ""),
        "section_heading_zh": blocks[start_idx].get("中文文本", ""),
        "paragraphs": paragraphs[:20],
    }
    if relevant_annotations:
        out["annotations"] = relevant_annotations
        out["annotation_note"] = _annotations_note(relevant_annotations)
    return json.dumps(out, ensure_ascii=False)


def get_references(paper_id: str) -> str:
    result = _get_result(paper_id)
    if not result:
        return json.dumps({"error": "未找到该论文的处理结果"}, ensure_ascii=False)
    refs = result.structure_json.get("参考文献", [])
    return json.dumps({"paper_id": paper_id, "references": refs}, ensure_ascii=False)


def get_annotations(paper_id: str) -> str:
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
    """将搜索词拆成 token 列表，支持空格分隔词 + 连续汉字序列（无需空格）"""
    import re
    tokens: set[str] = set()
    q = query.strip().lower()
    # 1. 空格分隔的词（英文/拼音友好）
    for w in q.split():
        if w:
            tokens.add(w)
    # 2. 连续汉字序列整体作为 token（"胡熊熊是谁" → "胡熊熊是谁" 已在上面，
    #    同时提取纯汉字子串如 "胡熊熊"、"是谁"）
    for seq in re.findall(r'[\u4e00-\u9fff]+', q):
        tokens.add(seq)
        # 同时加入长度 ≥2 的子串，提高召回（如"胡熊"也能匹配）
        for l in (3, 2):
            if len(seq) >= l:
                for i in range(len(seq) - l + 1):
                    tokens.add(seq[i:i+l])
    return [t for t in tokens if len(t) >= 2]


def search_annotations(query: str) -> str:
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


# ── 工具注册表 + OpenAI-compatible specs ──────────────────────────────────────

TOOL_REGISTRY = {
    "search_papers": search_papers,
    "get_paper_outline": get_paper_outline,
    "search_in_paper": search_in_paper,
    "get_paper_section": get_paper_section,
    "get_references": get_references,
    "get_annotations": get_annotations,
    "search_annotations": search_annotations,
}

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "Search paper titles, abstracts, and keywords to find relevant papers. Start with this when the user asks about a topic or paper.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keywords"},
                    "domain": {"type": "string", "description": "Optional academic domain filter"},
                    "limit": {"type": "integer", "description": "Max results (default 5)", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_paper_outline",
            "description": "Get the section headings outline of a paper. Use before reading sections.",
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
            "description": "Search for specific content in a paper's full text. Also returns all user annotations for this paper.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "query": {"type": "string", "description": "Keywords to search"},
                    "section": {"type": "string", "description": "Optional: limit to a specific section heading"},
                },
                "required": ["paper_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_paper_section",
            "description": "Get all paragraphs of a named section. Also returns all user annotations for this paper.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "heading": {"type": "string", "description": "Section heading (partial match OK)"},
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
            "description": "Search across ALL papers' annotations. Use when user asks 'what did I note about X' or 'where did I write about Y'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords to search in user annotations"},
                },
                "required": ["query"],
            },
        },
    },
]


def execute_tool(name: str, args: dict) -> str:
    func = TOOL_REGISTRY.get(name)
    if not func:
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
    try:
        return func(**args)
    except Exception as e:
        logger.error(f"[chat_tools] 工具 {name} 失败: {e}", exc_info=True)
        return json.dumps({"error": str(e)}, ensure_ascii=False)
