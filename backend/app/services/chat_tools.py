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


# ── 工具 1-7（原有） ────────────────────────────────────────────────────────────

def search_papers(query: str, domain: Optional[str] = None, limit=10) -> str:
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
    for _, p in results[:int(limit)]:
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


def search_in_paper(paper_id: str, query: str, section: Optional[str] = None, limit=8) -> str:
    result = _get_result(paper_id)
    if not result:
        return json.dumps({"error": "未找到该论文的处理结果"}, ensure_ascii=False)

    blocks = result.structure_json.get("正文", [])
    query_lower = query.lower()

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

    out: dict = {"paper_id": paper_id, "matches": matches[:int(limit)]}
    if relevant_annotations:
        out["annotations"] = relevant_annotations
        out["annotation_note"] = _annotations_note(relevant_annotations)
    return json.dumps(out, ensure_ascii=False)


def get_paper_section(paper_id: str, heading: str, offset: int = 0) -> str:
    """获取指定章节的完整文本内容（从该标题到下一同级/高级标题，排除图片块）。
    返回中英文拼接后的纯文本字符串，无数量限制。
    若章节过长可通过 offset 分页（每页 50 段）。
    """
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

    content_zh = "\n".join(p["text_zh"] for p in page if p["text_zh"])
    content_en = "\n".join(p["text_en"] for p in page if p["text_en"])

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


# ── 工具 8-11（新增） ──────────────────────────────────────────────────────────

def search_across_papers(keyword: str, limit=10) -> str:
    """在全库所有论文全文中搜索关键词，返回最相关的段落片段。"""
    keyword_lower = keyword.lower()
    all_matches = []

    with SessionLocal() as db:
        results = db.query(TranslationResult).all()
        paper_map = {p.id: p for p in db.query(Paper).all()}

    for tr in results:
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
            score = sum(1 for w in keyword_lower.split() if w in combined)
            if score > 0:
                all_matches.append({
                    "paper_id": tr.paper_id,
                    "paper_title": paper.title_zh or paper.title or "",
                    "block_idx": idx,
                    "heading_context": _heading_context(blocks, idx),
                    "text_zh": zh[:400],
                    "text_en": en[:400],
                    "score": score,
                })

    all_matches.sort(key=lambda x: x["score"], reverse=True)
    return json.dumps({
        "keyword": keyword,
        "results": all_matches[:int(limit)],
        "total_found": len(all_matches),
    }, ensure_ascii=False)


def get_paragraph_context(paper_id: str, block_idx: int, window: int = 2) -> str:
    """以指定段落为中心，获取前后 window 个段落的完整内容（排除图片块）。"""
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


def get_paper_metadata(paper_id: str) -> str:
    """快速获取论文元数据（标题、摘要、作者、年份、期刊、关键词）。"""
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
    with httpx.Client(timeout=60) as client:
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
                    "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
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
            "description": "Search for specific content in a single paper's full text. Also returns relevant user annotations. Never skip Introduction — it often contains core contributions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "query": {"type": "string", "description": "Keywords to search"},
                    "section": {"type": "string", "description": "Optional: limit search to a specific section heading"},
                    "limit": {"type": "integer", "description": "Max results to return (default 8)", "default": 8},
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
            "description": "Search a keyword across ALL papers' full text at once. Use for cross-paper questions like 'which papers discuss X' or when you don't know which paper contains specific content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Keyword(s) to search across all papers"},
                    "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
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
