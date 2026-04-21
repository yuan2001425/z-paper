"""
chat_agent.py — 知识库对话 Agent 主逻辑

三层记忆架构（参照 Claude Code）：
  L1 热数据  — 系统 prompt：静态规则 + 论文目录 + 词表（每轮重建）
  L2 温数据  — 最近其他会话的 auto_summary，注入系统 prompt
  L3 冷数据  — 所有历史对话（通过 search_chat_history 工具随时可查）

五级上下文压缩（参照 Claude Code）：
  L1 剪裁   — 截断旧 tool 消息 content（>4 轮触发，无 LLM）
  L2 微压缩  — 折叠旧 tool_call+result 对为单行摘要（>8 轮，无 LLM）
  L3 折叠   — 截短旧 user/assistant 消息（>12 轮，无 LLM）
  L4 自动压缩 — LLM 全量摘要旧轮（>16 轮，生成 auto_summary）
  L5 应急压缩 — 强制截断（字符数超限或 413 错误）
  熔断器    — L4 连续失败 3 次后停止压缩尝试
"""

import json
import logging
from typing import Optional, Generator

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models.paper import Paper
from app.models.user_glossary import UserGlossary
from app.services.chat_tools import TOOL_SPECS, execute_tool

logger = logging.getLogger(__name__)

# ── 压缩阈值常量 ───────────────────────────────────────────────────────────────
SNIP_THRESHOLD       = 4      # L1：>N user turns 后截断旧 tool 结果
MICRO_THRESHOLD      = 8      # L2：>N turns 后折叠旧工具调用对
FOLD_THRESHOLD       = 12     # L3：>N turns 后截短旧消息
AUTO_THRESHOLD       = 16     # L4：>N turns 触发 LLM 全量压缩
PRESERVE_RECENT      = 3      # 所有级别保留最近 N 轮原样
MAX_COMPACT_FAILURES = 3      # 熔断：L4 连续失败次数上限
EMERGENCY_CHAR_LIMIT = 80_000 # L5：消息总字符数超限触发应急截断
MAX_TOOL_ITERATIONS       = 16    # 单次回答最多工具调用轮数
MAX_QUERY_DB_FAILURES     = 3     # query_database 连续失败次数上限（熔断）

_TIMEOUT_TOOL   = httpx.Timeout(connect=15, read=90,  write=30, pool=10)
_TIMEOUT_STREAM = httpx.Timeout(connect=15, read=180, write=30, pool=10)


# ── System Prompt ─────────────────────────────────────────────────────────────

_STATIC_SYSTEM = """\
You are a personal academic research assistant helping the user understand their paper library.

Core rules:
1. ALWAYS call at least one tool before giving any answer. No exceptions. Even if you think the question is off-topic, still search.
2. When citing content, always mention: paper title, section, and relevant text.
3. Answer in Chinese unless the user writes in English.
4. For complex questions, make multiple tool calls to gather comprehensive information.

Tool selection strategy:
- To find SPECIFIC content (a concept, result, method, claim): use search_in_paper(keyword) — it searches ALL sections including Introduction, which often contains key contributions and definitions. Never skip a section based on its title.
- To read a COMPLETE section: use get_paper_section — it returns the full concatenated text from that heading to the next same-level heading with no paragraph limit.
- get_paper_outline is only for understanding overall structure, NOT for deciding which sections to skip.
- For cross-paper questions ("which papers discuss X"): use search_across_papers instead of calling search_in_paper repeatedly. If the default limit=10 might miss papers, pass a larger limit (e.g., limit=30 or limit=50).
- When you find a relevant paragraph, use get_paragraph_context to read surrounding context.
- When you only have a paper_id and need quick metadata: use get_paper_metadata.
- If the user asks about past conversations: use search_chat_history.
- query_database is a LAST RESORT tool. Only use it when ALL other tools are insufficient. Prefer the specific tools above for all standard queries.

Critical rule about annotations (批注):
- Annotations are personal notes written by the USER. They can contain ANYTHING: names, events, relationships, personal reminders — not just academic content.
- WHENEVER a query mentions a name, a person, or any entity you do not recognise from the paper catalog, you MUST call search_annotations(query) BEFORE concluding it is not found.
- Never say "I cannot find this" without first calling search_annotations. That is the last resort check.

Bilingual search strategy (IMPORTANT):
- Papers store content in BOTH English (原文) and Chinese (译文).
- When the user asks in Chinese, you MUST search with BOTH Chinese AND English keywords.
- For terms IN the Glossary below, use the provided English equivalent.
- For terms NOT in the Glossary, use your own knowledge to translate them to English before searching.
- Make separate tool calls for Chinese query AND English query to maximise recall.

Annotation search strategy:
- When calling search_annotations, pass specific names/keywords, NOT the full sentence.
- Annotations may be in Chinese, English, OR pinyin. For Chinese names, also try pinyin.
- Run search_annotations for each important entity in the user's question.

Image tool rules:
- When a visual diagram, concept map, or process flow would help the user understand something, proactively call generate_image with a detailed prompt (include content, layout, style, colors, in both Chinese and English). After generating, embed the result as ![description](image_url) in your answer.
- Only call edit_image when the user explicitly asks to modify a previously generated image. Pass the exact image_url from the prior generate_image result.
- image_url from tool results is in /uploads/chat_generated/xxx.jpg format — use it directly in Markdown image syntax.
"""


def _build_library_catalog() -> str:
    with SessionLocal() as db:
        papers = db.query(Paper).order_by(Paper.created_at.desc()).all()

    if not papers:
        return "\n== 您的论文库 ==\n（暂无论文，请先上传）\n"

    lines = [f"\n== 您的论文库（共 {len(papers)} 篇）=="]
    for p in papers[:50]:
        display = p.title_zh or p.title or "（无标题）"
        en_suffix = f" / {p.title}" if p.title and p.title != display else ""
        meta_parts = [x for x in [p.domain, str(p.year) if p.year else None, p.journal] if x]
        meta = f" ({', '.join(meta_parts)})" if meta_parts else ""
        lines.append(f"- [id:{p.id}] {display}{en_suffix}{meta}")

    if len(papers) > 50:
        lines.append(f"（另有 {len(papers) - 50} 篇，使用 search_papers 搜索）")
    lines.append("")
    return "\n".join(lines)


def _build_glossary_hint() -> str:
    with SessionLocal() as db:
        terms = (
            db.query(UserGlossary)
            .order_by(UserGlossary.foreign_term)
            .limit(120)
            .all()
        )
    if not terms:
        return ""
    lines = ["\n== 您的专有词表（中英双语检索参考）=="]
    for t in terms:
        zh = t.zh_term or ""
        en = t.foreign_term or ""
        if zh and en:
            lines.append(f"  {zh} → {en}")
        elif en:
            lines.append(f"  {en}")
    lines.append("")
    return "\n".join(lines)


def _build_warm_context(session_id: Optional[str] = None) -> str:
    """L2 温数据：注入最近 3 个其他会话的 auto_summary"""
    from app.models.chat import ChatSession
    try:
        with SessionLocal() as db:
            q = db.query(ChatSession).filter(
                ChatSession.auto_summary.isnot(None),
                ChatSession.auto_summary != "",
            ).order_by(ChatSession.updated_at.desc())
            if session_id:
                q = q.filter(ChatSession.id != session_id)
            sessions = q.limit(3).all()

        if not sessions:
            return ""

        lines = ["\n== 往期研究摘要（供参考）=="]
        for s in sessions:
            lines.append(f"- 【{s.title[:30]}】{s.auto_summary}")
        lines.append("")
        return "\n".join(lines)
    except Exception:
        return ""


def _system_prompt(session_id: Optional[str] = None) -> str:
    return _STATIC_SYSTEM + _build_library_catalog() + _build_glossary_hint()


# ── DeepSeek 调用 ─────────────────────────────────────────────────────────────

def _call_deepseek(messages: list[dict], use_tools: bool = True, tools: list | None = None) -> dict:
    payload: dict = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    if use_tools:
        payload["tools"] = tools if tools is not None else TOOL_SPECS
        payload["tool_choice"] = "auto"

    with httpx.Client(timeout=_TIMEOUT_TOOL) as client:
        resp = client.post(
            f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()

    return resp.json()["choices"][0]["message"]


import re as _re

_DSML_BLOCK_RE  = _re.compile(r'<\uFF5CDSML\uFF5Cfunction_calls>.*?</\uFF5CDSML\uFF5Cfunction_calls>', _re.DOTALL)
_DSML_INVOKE_RE = _re.compile(r'<\uFF5CDSML\uFF5Cinvoke\s+name="([^"]+)">(.*?)</\uFF5CDSML\uFF5Cinvoke>', _re.DOTALL)
_DSML_PARAM_RE  = _re.compile(r'<\uFF5CDSML\uFF5Cparameter\s+name="([^"]+)"[^>]*>(.*?)</\uFF5CDSML\uFF5Cparameter>', _re.DOTALL)
_DSML_ANY_RE    = _re.compile(r'<\uFF5CDSML\uFF5C[^>]*>', _re.DOTALL)


def _extract_dsml_tool_calls(content: str) -> list[dict]:
    calls = []
    for invoke in _DSML_INVOKE_RE.finditer(content):
        name = invoke.group(1)
        body = invoke.group(2)
        args: dict = {}
        for param in _DSML_PARAM_RE.finditer(body):
            args[param.group(1)] = param.group(2).strip()
        calls.append({"name": name, "args": args})
    return calls


def _iter_raw_chunks(messages: list[dict]) -> Generator[str, None, None]:
    payload: dict = {
        "model":       settings.DEEPSEEK_MODEL,
        "messages":    messages,
        "temperature": 0.3,
        "max_tokens":  4096,
        "stream":      True,
        "tools":       TOOL_SPECS,
        "tool_choice": "none",
    }
    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type":  "application/json",
    }
    with httpx.Client(timeout=_TIMEOUT_STREAM) as client:
        with client.stream(
            "POST",
            f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:].strip()
                if raw == "[DONE]":
                    break
                try:
                    content = json.loads(raw)["choices"][0]["delta"].get("content") or ""
                    if content:
                        yield content
                except Exception:
                    pass


_DSML_START    = "<\uFF5CDSML\uFF5C"
_DSML_FC_CLOSE = "</\uFF5CDSML\uFF5Cfunction_calls>"


def _stream_guarded(messages: list[dict]) -> Generator:
    buf     = ""
    dsml    = ""
    in_dsml = False
    keep    = len(_DSML_START) - 1

    for chunk in _iter_raw_chunks(messages):
        if in_dsml:
            dsml += chunk
            if _DSML_FC_CLOSE in dsml:
                yield _extract_dsml_tool_calls(dsml)
                return
        else:
            buf += chunk
            idx = buf.find(_DSML_START)
            if idx >= 0:
                if idx > 0:
                    yield buf[:idx]
                dsml    = buf[idx:]
                buf     = ""
                in_dsml = True
            else:
                if len(buf) > keep:
                    yield buf[:-keep]
                    buf = buf[-keep:]

    if not in_dsml:
        if buf:
            yield buf
    else:
        calls = _extract_dsml_tool_calls(dsml)
        if calls:
            yield calls


# ── 五级压缩系统 ──────────────────────────────────────────────────────────────

def _count_user_turns(messages: list[dict]) -> int:
    return sum(1 for m in messages if m.get("role") == "user")


def _estimate_chars(messages: list[dict]) -> int:
    return sum(len(str(m.get("content") or "")) for m in messages)


def _get_cutoff(messages: list[dict]) -> int:
    """返回"保留最近 PRESERVE_RECENT 轮"的起始索引。"""
    user_count = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            user_count += 1
            if user_count >= PRESERVE_RECENT:
                return i
    return 0


def _snip_tool_content(content: str) -> str:
    """将工具结果压缩为结构索引：保留"找到了什么/在哪里"，丢弃具体正文。"""
    try:
        data = json.loads(content)
    except Exception:
        return content[:150] + "…[已压缩]"

    summary: dict = {"[已压缩]": True}

    # search_in_paper / search_across_papers
    if "matches" in data:
        matches = data["matches"]
        sections = list(dict.fromkeys(
            m.get("heading_context", "") for m in matches if m.get("heading_context")
        ))
        summary["找到"] = f"{len(matches)}处匹配"
        if sections:
            summary["章节"] = sections[:4]
        if matches:
            summary["最高分block"] = matches[0].get("block_idx")
        return json.dumps(summary, ensure_ascii=False)

    # search_across_papers results
    if "results" in data and "keyword" in data:
        results = data["results"]
        papers = list(dict.fromkeys(r.get("paper_title", "") for r in results if r.get("paper_title")))
        summary["找到"] = f"{len(results)}处匹配"
        summary["涉及论文"] = papers[:4]
        return json.dumps(summary, ensure_ascii=False)

    # search_papers
    if "results" in data:
        results = data["results"]
        titles = [r.get("title_zh") or r.get("title", "") for r in results]
        summary["找到"] = f"{len(results)}篇论文"
        summary["论文列表"] = [{"id": r.get("paper_id"), "title": t} for r, t in zip(results, titles)]
        return json.dumps(summary, ensure_ascii=False)

    # get_paper_section
    if "content_zh" in data:
        summary["章节"] = data.get("section_heading_zh") or data.get("section_heading", "")
        summary["段落数"] = data.get("paragraph_count", "?")
        summary["has_more"] = data.get("has_more", False)
        return json.dumps(summary, ensure_ascii=False)

    # get_paragraph_context
    if "context" in data:
        ctx = data["context"]
        summary["上下文段落数"] = len(ctx)
        summary["目标block"] = data.get("target_block_idx")
        return json.dumps(summary, ensure_ascii=False)

    # get_annotations / search_annotations
    if "annotations" in data:
        anns = data["annotations"]
        summary["批注数"] = len(anns)
        summary["摘要"] = [a.get("content", "")[:40] for a in anns[:3]]
        return json.dumps(summary, ensure_ascii=False)

    if "results" in data and "count" in data:
        summary["结果数"] = data.get("count", 0)
        return json.dumps(summary, ensure_ascii=False)

    # fallback
    return content[:150] + "…[已压缩]"


def _apply_snip(messages: list[dict]) -> list[dict]:
    """L1：将旧 tool 消息压缩为结构索引（保留找到什么/在哪里，丢弃正文）。"""
    cutoff = _get_cutoff(messages)
    result = []
    for i, m in enumerate(messages):
        if i < cutoff and m.get("role") == "tool":
            content = str(m.get("content") or "")
            if len(content) > 200:
                m = {**m, "content": _snip_tool_content(content)}
        result.append(m)
    return result


def _apply_micro_compact(messages: list[dict]) -> list[dict]:
    """L2：将旧轮中 assistant(tool_calls)+tool(result) 对折叠为单行摘要。"""
    cutoff = _get_cutoff(messages)
    result = []
    skip_ids: set[str] = set()

    i = 0
    while i < len(messages):
        m = messages[i]
        if i >= cutoff:
            result.append(m)
            i += 1
            continue

        if m.get("role") == "assistant" and m.get("tool_calls") and not m.get("content"):
            tc_list = m["tool_calls"]
            # 找到这批工具调用对应的所有 tool 结果
            tool_ids = {tc["id"] for tc in tc_list}
            summaries = []
            j = i + 1
            tool_results: dict[str, str] = {}
            while j < len(messages) and messages[j].get("role") == "tool":
                tr = messages[j]
                if tr.get("tool_call_id") in tool_ids:
                    tool_results[tr["tool_call_id"]] = str(tr.get("content", ""))[:50]
                    j += 1
                else:
                    break

            for tc in tc_list:
                name = tc.get("function", {}).get("name", "unknown")
                try:
                    args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                    key_args = ", ".join(f"{k}={repr(v)[:20]}" for k, v in list(args.items())[:2])
                except Exception:
                    key_args = ""
                snippet = tool_results.get(tc["id"], "")[:50]
                summaries.append(f"[工具调用] {name}({key_args}) → {snippet}")

            result.append({
                "role":    "assistant",
                "content": "\n".join(summaries),
            })
            i = j  # 跳过已合并的 tool 消息
            continue

        result.append(m)
        i += 1

    return result


def _apply_fold(messages: list[dict]) -> list[dict]:
    """L3：截短旧 user/assistant 消息 content 为前 150 字符。"""
    cutoff = _get_cutoff(messages)
    result = []
    for i, m in enumerate(messages):
        if i < cutoff and m.get("role") in ("user", "assistant") and not m.get("tool_calls"):
            content = str(m.get("content") or "")
            if len(content) > 150:
                m = {**m, "content": content[:150] + "…"}
        result.append(m)
    return result


def _apply_auto_compact(messages: list[dict]) -> tuple[list[dict], str]:
    """L4：LLM 全量摘要旧轮次。返回 (压缩后消息, summary文本)。"""
    cutoff = _get_cutoff(messages)
    old_msgs = messages[:cutoff]
    recent   = messages[cutoff:]

    if not old_msgs:
        return messages, ""

    history_text = "\n".join(
        f"{m['role'].upper()}: {str(m.get('content',''))[:400]}"
        for m in old_msgs
        if m.get("role") in ("user", "assistant") and m.get("content")
    )

    summary_msg = _call_deepseek([
        {"role": "system", "content": "用3-5句中文概括以下研究对话的核心讨论内容和结论（勿包含工具调用细节）。"},
        {"role": "user",   "content": history_text},
    ], use_tools=False)
    summary = summary_msg.get("content", "（历史已压缩）")

    compacted = [{"role": "system", "content": f"[对话历史摘要]\n{summary}"}] + recent
    return compacted, summary


def _apply_emergency_compact(messages: list[dict]) -> list[dict]:
    """L5：强制截断，仅保留最近 PRESERVE_RECENT 轮。"""
    cutoff = _get_cutoff(messages)
    recent = messages[cutoff:]
    return [{"role": "system", "content": "[历史已截断，对话过长]"}] + recent


def _compress(
    messages: list[dict],
    consecutive_failures: int = 0,
) -> tuple[list[dict], Optional[str], int]:
    """
    五级压缩入口。返回 (压缩后消息, summary|None, 新的连续失败计数)。
    """
    # 熔断器
    if consecutive_failures >= MAX_COMPACT_FAILURES:
        logger.warning("[chat_agent] 熔断器触发：跳过压缩")
        return messages, None, consecutive_failures

    turns = _count_user_turns(messages)
    summary: Optional[str] = None

    # L5 应急（字符数超限）
    if _estimate_chars(messages) > EMERGENCY_CHAR_LIMIT:
        logger.warning("[chat_agent] L5 应急压缩：字符数超限")
        return _apply_emergency_compact(messages), None, consecutive_failures

    # L1 剪裁
    if turns > SNIP_THRESHOLD:
        messages = _apply_snip(messages)

    # L2 微压缩
    if turns > MICRO_THRESHOLD:
        messages = _apply_micro_compact(messages)

    # L3 折叠
    if turns > FOLD_THRESHOLD:
        messages = _apply_fold(messages)

    # L4 自动压缩
    if turns > AUTO_THRESHOLD:
        try:
            messages, summary = _apply_auto_compact(messages)
            consecutive_failures = 0
            logger.info("[chat_agent] L4 自动压缩成功")
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"[chat_agent] L4 压缩失败 (连续{consecutive_failures}次): {e}")

    return messages, summary, consecutive_failures


# ── 主循环 ────────────────────────────────────────────────────────────────────

def run_chat_turn(
    user_message: str,
    history: list[dict],
    compaction_summary: Optional[str] = None,
    session_id: Optional[str] = None,
    compact_failures: int = 0,
) -> dict:
    """
    执行一轮对话（非流式）。

    返回：
      answer            — 最终回答文本
      tool_calls        — [{name, args, result_snippet}]
      citations         — [{paper_id, block_idx, text, type, ...}]
      new_history       — 更新后的工作消息列表
      compaction_summary — 若触发 L4 压缩则返回新摘要，否则 None
      compact_failures  — 更新后的熔断计数
    """
    api_messages: list[dict] = [{"role": "system", "content": _system_prompt(session_id)}]
    if compaction_summary:
        api_messages.append({"role": "system", "content": f"[对话历史摘要]\n{compaction_summary}"})
    api_messages.extend(history)
    api_messages.append({"role": "user", "content": user_message})

    tool_call_log: list[dict] = []
    citations: list[dict]     = []
    answer = ""
    query_db_failures = 0
    active_tools = TOOL_SPECS

    for _iter in range(MAX_TOOL_ITERATIONS):
        response_msg = _call_deepseek(api_messages, tools=active_tools)
        api_messages.append(response_msg)

        tool_calls = response_msg.get("tool_calls")
        if not tool_calls:
            answer = response_msg.get("content", "")
            break

        for tc in tool_calls:
            func   = tc["function"]
            name   = func["name"]
            try:
                args = json.loads(func["arguments"])
            except Exception:
                args = {}

            logger.info(f"[chat_agent] 工具: {name}({list(args.keys())})")
            result_str = execute_tool(name, args)

            # query_database 熔断：连续失败超限后禁用该工具
            if name == "query_database":
                try:
                    result_data = json.loads(result_str)
                    if "error" in result_data:
                        query_db_failures += 1
                        if query_db_failures >= MAX_QUERY_DB_FAILURES:
                            result_str = json.dumps({
                                "error": result_data["error"],
                                "system": f"query_database 已连续失败 {query_db_failures} 次，已禁用。请停止调用此工具，改用其他工具或直接根据已有信息回答。",
                            }, ensure_ascii=False)
                            active_tools = [t for t in TOOL_SPECS if t["function"]["name"] != "query_database"]
                            logger.warning("[chat_agent] query_database 熔断，已从可用工具中移除")
                    else:
                        query_db_failures = 0
                except Exception:
                    pass

            tool_call_log.append({
                "name": name,
                "args": args,
                "result_snippet": result_str[:300],
            })
            _collect_citations(name, args, result_str, citations)

            api_messages.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "content":      result_str,
            })
    else:
        final = _call_deepseek(api_messages, use_tools=False)
        api_messages.append(final)
        answer = final.get("content", "")

    new_history = [m for m in api_messages if m.get("role") != "system"]

    new_history, new_compaction, compact_failures = _compress(new_history, compact_failures)

    return {
        "answer":             answer,
        "tool_calls":         tool_call_log,
        "citations":          _enrich_citations(citations),
        "new_history":        new_history,
        "compaction_summary": new_compaction,
        "compact_failures":   compact_failures,
    }


def run_chat_turn_stream(
    user_message: str,
    history: list[dict],
    compaction_summary: Optional[str] = None,
    session_id: Optional[str] = None,
    compact_failures: int = 0,
) -> Generator[dict, None, None]:
    """
    混合模式：非流式工具循环 + 流式最终答案。

    yield 的 event dict 类型：
      {"type": "tool_start",  "name": str, "args": dict}
      {"type": "tool_done",   "name": str, "snippet": str}
      {"type": "answer_chunk","content": str}
      {"type": "done", "answer": str, "tool_calls": list, "citations": list,
                        "new_history": list, "compaction_summary": str|None,
                        "compact_failures": int}
    """
    api_messages: list[dict] = [{"role": "system", "content": _system_prompt(session_id)}]
    if compaction_summary:
        api_messages.append({"role": "system", "content": f"[对话历史摘要]\n{compaction_summary}"})
    api_messages.extend(history)
    api_messages.append({"role": "user", "content": user_message})

    tool_call_log: list[dict] = []
    citations:     list[dict] = []
    query_db_failures = 0
    active_tools = TOOL_SPECS

    # ── 阶段一：非流式工具循环 ──────────────────────────────────────────────
    for _iter in range(MAX_TOOL_ITERATIONS):
        try:
            response_msg = _call_deepseek(api_messages, use_tools=True, tools=active_tools)
        except Exception as e:
            logger.error(f"[chat_agent] 工具检测调用失败 (iter {_iter}): {e}")
            break

        tool_calls = response_msg.get("tool_calls")

        if not tool_calls:
            raw_content = response_msg.get("content") or ""
            dsml_calls  = _extract_dsml_tool_calls(raw_content)
            if dsml_calls:
                logger.info(f"[chat_agent] 检测到 DSML 格式工具调用: {[c['name'] for c in dsml_calls]}")
                tool_calls = [
                    {
                        "id":       f"dsml_{_iter}_{i}",
                        "type":     "function",
                        "function": {
                            "name":      c["name"],
                            "arguments": json.dumps(c["args"], ensure_ascii=False),
                        },
                    }
                    for i, c in enumerate(dsml_calls)
                ]
                clean_content = _DSML_BLOCK_RE.sub("", raw_content)
                clean_content = _DSML_ANY_RE.sub("", clean_content).strip() or None
                response_msg  = {**response_msg, "content": clean_content, "tool_calls": tool_calls}

        if not tool_calls:
            break

        api_messages.append(response_msg)

        for tc in tool_calls:
            func = tc["function"]
            name = func["name"]
            try:
                args = json.loads(func["arguments"])
            except Exception:
                args = {}

            logger.info(f"[chat_agent] 工具: {name}({list(args.keys())})")
            yield {"type": "tool_start", "name": name, "args": args}

            result_str = execute_tool(name, args)

            # query_database 熔断
            if name == "query_database":
                try:
                    result_data = json.loads(result_str)
                    if "error" in result_data:
                        query_db_failures += 1
                        if query_db_failures >= MAX_QUERY_DB_FAILURES:
                            result_str = json.dumps({
                                "error": result_data["error"],
                                "system": f"query_database 已连续失败 {query_db_failures} 次，已禁用。请停止调用此工具，改用其他工具或直接根据已有信息回答。",
                            }, ensure_ascii=False)
                            active_tools = [t for t in TOOL_SPECS if t["function"]["name"] != "query_database"]
                            logger.warning("[chat_agent] query_database 熔断，已从可用工具中移除")
                    else:
                        query_db_failures = 0
                except Exception:
                    pass

            tool_call_log.append({"name": name, "args": args, "result_snippet": result_str[:300]})
            _collect_citations(name, args, result_str, citations)

            yield {"type": "tool_done", "name": name, "snippet": result_str[:300]}

            api_messages.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "content":      result_str,
            })

    # ── 阶段二：流式答复（遇到 DSML 继续执行工具，最多 4 次额外循环） ────
    answer_parts:  list[str] = []
    MAX_STREAM_EXTRA = 4

    for _s in range(MAX_STREAM_EXTRA + 1):
        iter_text:  list[str]  = []
        dsml_calls: list[dict] = []

        try:
            for item in _stream_guarded(api_messages):
                if isinstance(item, str):
                    yield {"type": "answer_chunk", "content": item}
                    iter_text.append(item)
                    answer_parts.append(item)
                elif isinstance(item, list):
                    dsml_calls = item
        except Exception as e:
            logger.error(f"[chat_agent] 流式答复失败 (stream_iter {_s}): {e}")
            # L5 应急检测：413 错误
            is_413 = "413" in str(e)
            try:
                if is_413:
                    api_messages = _apply_emergency_compact(api_messages)
                fallback = _call_deepseek(api_messages, use_tools=False)
                text = fallback.get("content", "（生成失败，请重试）")
                yield {"type": "answer_chunk", "content": text}
                answer_parts.append(text)
                iter_text.append(text)
            except Exception:
                pass
            break

        if dsml_calls:
            fake_tcs = [
                {
                    "id":       f"s{_s}_{i}",
                    "type":     "function",
                    "function": {
                        "name":      c["name"],
                        "arguments": json.dumps(c["args"], ensure_ascii=False),
                    },
                }
                for i, c in enumerate(dsml_calls)
            ]
            api_messages.append({
                "role":       "assistant",
                "content":    "".join(iter_text) or None,
                "tool_calls": fake_tcs,
            })
            for i, c in enumerate(dsml_calls):
                name = c["name"]
                args = c["args"]
                logger.info(f"[chat_agent] 流式 DSML 工具: {name}({list(args.keys())})")
                yield {"type": "tool_start", "name": name, "args": args}
                result_str = execute_tool(name, args)
                tool_call_log.append({"name": name, "args": args, "result_snippet": result_str[:300]})
                _collect_citations(name, args, result_str, citations)
                yield {"type": "tool_done", "name": name, "snippet": result_str[:300]}
                api_messages.append({
                    "role":         "tool",
                    "tool_call_id": f"s{_s}_{i}",
                    "content":      result_str,
                })
        else:
            if iter_text:
                api_messages.append({"role": "assistant", "content": "".join(iter_text)})
            break

    answer = "".join(answer_parts)

    new_history = [m for m in api_messages if m.get("role") != "system"]
    new_history, new_compaction, compact_failures = _compress(new_history, compact_failures)

    yield {
        "type":               "done",
        "answer":             answer,
        "tool_calls":         tool_call_log,
        "citations":          _enrich_citations(citations),
        "new_history":        new_history,
        "compaction_summary": new_compaction,
        "compact_failures":   compact_failures,
    }


# ── 引用收集 ──────────────────────────────────────────────────────────────────

def _collect_citations(tool_name: str, args: dict, result_str: str, citations: list):
    try:
        result = json.loads(result_str)
    except Exception:
        return

    paper_id = args.get("paper_id", "")

    if tool_name in ("search_in_paper", "get_paper_section"):
        matches = result.get("matches") or result.get("paragraphs", [])
        for m in matches[:3]:
            citations.append({
                "paper_id":       paper_id,
                "block_idx":      m.get("block_idx", -1),
                "heading":        m.get("heading_context", ""),
                "text":           m.get("text_zh") or m.get("text_en", ""),
                "type":           "paragraph",
            })
        for ann in result.get("annotations", []):
            citations.append({
                "paper_id":      paper_id,
                "block_idx":     -1,
                "block_id":      ann.get("block_id", ""),
                "text":          ann.get("content", ""),
                "selected_text": ann.get("selected_text", ""),
                "type":          "annotation",
            })

    elif tool_name in ("get_annotations",):
        for ann in result.get("annotations", []):
            citations.append({
                "paper_id":      paper_id,
                "block_idx":     -1,
                "block_id":      ann.get("block_id", ""),
                "text":          ann.get("content", ""),
                "selected_text": ann.get("selected_text", ""),
                "type":          "annotation",
            })

    elif tool_name == "search_annotations":
        for item in result.get("results", [])[:4]:
            citations.append({
                "paper_id":      item.get("paper_id", ""),
                "paper_title":   item.get("paper_title", ""),
                "block_idx":     -1,
                "block_id":      item.get("block_id", ""),
                "text":          item.get("content", ""),
                "selected_text": item.get("selected_text", ""),
                "type":          "annotation",
            })

    elif tool_name == "search_papers":
        for item in result.get("results", [])[:3]:
            snippet = item.get("abstract_snippet", "").strip()
            if not snippet:
                continue
            citations.append({
                "paper_id":    item.get("paper_id", ""),
                "paper_title": item.get("title_zh") or item.get("title", ""),
                "block_idx":   -1,
                "text":        snippet,
                "heading":     "摘要",
                "type":        "paragraph",
            })

    elif tool_name == "search_across_papers":
        for item in result.get("results", [])[:3]:
            text = item.get("text_zh") or item.get("text_en", "")
            if not text.strip():
                continue
            citations.append({
                "paper_id":    item.get("paper_id", ""),
                "paper_title": item.get("paper_title", ""),
                "block_idx":   item.get("block_idx", -1),
                "heading":     item.get("heading_context", ""),
                "text":        text,
                "type":        "paragraph",
            })

    elif tool_name == "get_paragraph_context":
        for ctx in result.get("context", [])[:2]:
            if ctx.get("is_target"):
                citations.append({
                    "paper_id":  paper_id,
                    "block_idx": ctx.get("block_idx", -1),
                    "heading":   ctx.get("heading_context", ""),
                    "text":      ctx.get("text_zh") or ctx.get("text_en", ""),
                    "type":      "paragraph",
                })


def _enrich_citations(citations: list) -> list:
    if not citations:
        return citations
    paper_ids = {c["paper_id"] for c in citations if c.get("paper_id")}
    if not paper_ids:
        return citations
    with SessionLocal() as db:
        papers = db.query(Paper).filter(Paper.id.in_(paper_ids)).all()
    pm = {p.id: p for p in papers}
    for c in citations:
        p = pm.get(c.get("paper_id", ""))
        if not p:
            continue
        if not c.get("paper_title"):
            c["paper_title"] = p.title_zh or p.title or ""
        if not c.get("authors"):
            c["authors"] = p.authors or []
        if not c.get("year"):
            c["year"] = p.year

    return [c for c in citations if (c.get("text") or "").strip()]
