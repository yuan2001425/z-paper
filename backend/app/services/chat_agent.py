"""
chat_agent.py — 知识库对话 Agent 主逻辑

三层记忆：
  L1 工作记忆  — 当前对话 messages（放入 API 请求）
  L2 压缩记忆  — 超过阈值时 LLM 压缩旧轮次为摘要
  L3 持久记忆  — DB 会话 + 论文库（通过工具随时可查）
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

COMPACT_THRESHOLD_TURNS = 12   # 超过此轮数触发压缩
PRESERVE_RECENT_TURNS   = 3    # 压缩时保留最近 N 轮
MAX_TOOL_ITERATIONS     = 8    # 单次回答最多工具调用轮数

_TIMEOUT_TOOL = httpx.Timeout(connect=15, read=90, write=30, pool=10)
_TIMEOUT_STREAM = httpx.Timeout(connect=15, read=180, write=30, pool=10)


# ── System Prompt ─────────────────────────────────────────────────────────────

_STATIC_SYSTEM = """\
You are a personal academic research assistant helping the user understand their paper library.

Core rules:
1. ALWAYS call at least one tool before giving any answer. No exceptions. Even if you think the question is off-topic, still search.
2. When citing content, always mention: paper title, section, and relevant text.
3. Answer in Chinese unless the user writes in English.
4. For complex questions, make multiple tool calls to gather comprehensive information.

Critical rule about annotations (批注):
- Annotations are personal notes written by the USER. They can contain ANYTHING: names, events, relationships, personal reminders — not just academic content.
- WHENEVER a query mentions a name, a person, or any entity you do not recognise from the paper catalog, you MUST call search_annotations(query) BEFORE concluding it is not found.
- Never say "I cannot find this" without first calling search_annotations. That is the last resort check.

Bilingual search strategy (IMPORTANT):
- Papers store content in BOTH English (原文) and Chinese (译文).
- When the user asks in Chinese, you MUST search with BOTH Chinese AND English keywords.
- For terms IN the Glossary below, use the provided English equivalent.
- For terms NOT in the Glossary, use your own knowledge to translate them to English before searching. Never skip the English search just because a term is absent from the Glossary.
- Example: if the user asks about "注意力机制", ALSO search for "attention mechanism".
- Make separate tool calls for Chinese query AND English query to maximise recall.

Annotation search strategy (IMPORTANT):
- When calling search_annotations, pass the specific name or keyword as the query — NOT the full question sentence. For example, for "胡熊熊和肖柯羽是什么关系", call search_annotations("胡熊熊") and search_annotations("肖柯羽") separately.
- Annotations may be written in Chinese, English, OR pinyin romanization. For Chinese names or terms, ALSO try their pinyin romanization as a separate search call. For example, for "胡熊熊" also try search_annotations("huxiongxiong") or search_annotations("hu xiong xiong").
- Run search_annotations for each important name/entity in the user's question.
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
    """注入用户专有词表，供 agent 发散英文搜索关键词"""
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


def _system_prompt() -> str:
    return _STATIC_SYSTEM + _build_library_catalog() + _build_glossary_hint()


# ── DeepSeek 调用 ─────────────────────────────────────────────────────────────

def _call_deepseek(messages: list[dict], use_tools: bool = True) -> dict:
    payload: dict = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    if use_tools:
        payload["tools"] = TOOL_SPECS
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

# DeepSeek 有时会在 content 里输出 DSML 格式而非 tool_calls 字段
# ｜ = U+FF5C FULLWIDTH VERTICAL LINE
_DSML_BLOCK_RE  = _re.compile(r'<\uFF5CDSML\uFF5Cfunction_calls>.*?</\uFF5CDSML\uFF5Cfunction_calls>', _re.DOTALL)
_DSML_INVOKE_RE = _re.compile(r'<\uFF5CDSML\uFF5Cinvoke\s+name="([^"]+)">(.*?)</\uFF5CDSML\uFF5Cinvoke>', _re.DOTALL)
_DSML_PARAM_RE  = _re.compile(r'<\uFF5CDSML\uFF5Cparameter\s+name="([^"]+)"[^>]*>(.*?)</\uFF5CDSML\uFF5Cparameter>', _re.DOTALL)
_DSML_ANY_RE    = _re.compile(r'<\uFF5CDSML\uFF5C[^>]*>', _re.DOTALL)


def _extract_dsml_tool_calls(content: str) -> list[dict]:
    """从 content 里的 DSML 块中提取工具调用列表"""
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
    """底层流式调用，逐 token yield 原始文本（含可能的 DSML）"""
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


_DSML_START = "<\uFF5CDSML\uFF5C"                          # <｜DSML｜
_DSML_FC_CLOSE = "</\uFF5CDSML\uFF5Cfunction_calls>"        # </｜DSML｜function_calls>


def _stream_guarded(messages: list[dict]) -> Generator:
    """
    流式答复，遇到 DSML 时自动截断并解析工具调用。

    yield str        → 安全的文本 chunk（直接推前端）
    yield list[dict] → 解析出的 DSML 工具调用列表（需要执行）
    """
    buf     = ""   # 正常文本缓冲（用于跨 chunk 检测 DSML 起始）
    dsml    = ""   # DSML 累积缓冲
    in_dsml = False
    keep    = len(_DSML_START) - 1   # 跨 chunk 边界需要保留的字符数

    for chunk in _iter_raw_chunks(messages):
        if in_dsml:
            dsml += chunk
            if _DSML_FC_CLOSE in dsml:
                # 完整 DSML 块接收完毕
                yield _extract_dsml_tool_calls(dsml)
                return
            # 继续累积
        else:
            buf += chunk
            idx = buf.find(_DSML_START)
            if idx >= 0:
                # 先把 DSML 之前的安全文本推出去
                if idx > 0:
                    yield buf[:idx]
                dsml    = buf[idx:]
                buf     = ""
                in_dsml = True
            else:
                # 还没遇到 DSML，把安全部分推出去，保留边界
                if len(buf) > keep:
                    yield buf[:-keep]
                    buf = buf[-keep:]

    # 流结束
    if not in_dsml:
        if buf:
            yield buf
    else:
        # 流结束时 DSML 不完整，尝试解析已有内容
        calls = _extract_dsml_tool_calls(dsml)
        if calls:
            yield calls


# ── 会话压缩 ──────────────────────────────────────────────────────────────────

def _count_user_turns(messages: list[dict]) -> int:
    return sum(1 for m in messages if m.get("role") == "user")


def _compact(messages: list[dict]) -> tuple[list[dict], str]:
    """压缩旧轮次，保留最近 PRESERVE_RECENT_TURNS 轮"""
    user_count = 0
    cutoff = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            user_count += 1
            if user_count >= PRESERVE_RECENT_TURNS:
                cutoff = i
                break

    old_msgs = messages[:cutoff]
    recent   = messages[cutoff:]

    if not old_msgs:
        return messages, ""

    history_text = "\n".join(
        f"{m['role'].upper()}: {str(m.get('content',''))[:300]}"
        for m in old_msgs
        if m.get("role") in ("user", "assistant") and m.get("content")
    )

    try:
        summary_msg = _call_deepseek([
            {"role": "system", "content": "Summarize the research conversation concisely in Chinese (max 400 chars)."},
            {"role": "user",   "content": history_text},
        ], use_tools=False)
        summary = summary_msg.get("content", "（历史已压缩）")
    except Exception as e:
        logger.error(f"[chat_agent] 压缩失败: {e}")
        summary = "（历史已压缩）"

    compacted = [{"role": "system", "content": f"[对话历史摘要]\n{summary}"}] + recent
    return compacted, summary


# ── 主循环 ────────────────────────────────────────────────────────────────────

def run_chat_turn(
    user_message: str,
    history: list[dict],
    compaction_summary: Optional[str] = None,
) -> dict:
    """
    执行一轮对话。

    返回：
      answer            — 最终回答文本
      tool_calls        — [{name, args, result_snippet}]
      citations         — [{paper_id, block_idx, text, type, ...}]
      new_history       — 更新后的工作消息列表
      compaction_summary — 若触发压缩则返回新摘要，否则 None
    """
    # 拼装 API 消息（system + 历史摘要 + 历史 + 当前问题）
    api_messages: list[dict] = [{"role": "system", "content": _system_prompt()}]
    if compaction_summary:
        api_messages.append({"role": "system", "content": f"[对话历史摘要]\n{compaction_summary}"})
    api_messages.extend(history)
    api_messages.append({"role": "user", "content": user_message})

    tool_call_log: list[dict] = []
    citations: list[dict]     = []
    answer = ""

    for _iter in range(MAX_TOOL_ITERATIONS):
        response_msg = _call_deepseek(api_messages)
        api_messages.append(response_msg)

        tool_calls = response_msg.get("tool_calls")
        if not tool_calls:
            answer = response_msg.get("content", "")
            break

        # 执行工具
        for tc in tool_calls:
            func   = tc["function"]
            name   = func["name"]
            try:
                args = json.loads(func["arguments"])
            except Exception:
                args = {}

            logger.info(f"[chat_agent] 工具: {name}({list(args.keys())})")
            result_str = execute_tool(name, args)

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
        # 超出最大迭代
        final = _call_deepseek(api_messages, use_tools=False)
        api_messages.append(final)
        answer = final.get("content", "")

    # 去掉 system 消息，保存工作历史
    new_history = [m for m in api_messages if m.get("role") != "system"]

    # 检查是否需要压缩
    new_compaction = None
    if _count_user_turns(new_history) > COMPACT_THRESHOLD_TURNS:
        new_history, new_compaction = _compact(new_history)

    return {
        "answer":             answer,
        "tool_calls":         tool_call_log,
        "citations":          _enrich_citations(citations),
        "new_history":        new_history,
        "compaction_summary": new_compaction,
    }


def run_chat_turn_stream(
    user_message: str,
    history: list[dict],
    compaction_summary: Optional[str] = None,
) -> Generator[dict, None, None]:
    """
    混合模式：非流式工具循环（可靠） + 流式最终答案（流畅）。

    工具检测阶段使用非流式 API，保证 tool_calls 格式正确、不出 DSML。
    最终答案阶段使用流式 API（tool_choice="none"），逐 token 推送到前端。

    yield 的 event dict 类型：
      {"type": "tool_start",  "name": str, "args": dict}
      {"type": "tool_done",   "name": str, "snippet": str}
      {"type": "answer_chunk","content": str}
      {"type": "done", "answer": str, "tool_calls": list, "citations": list,
                        "new_history": list, "compaction_summary": str|None}
    """
    api_messages: list[dict] = [{"role": "system", "content": _system_prompt()}]
    if compaction_summary:
        api_messages.append({"role": "system", "content": f"[对话历史摘要]\n{compaction_summary}"})
    api_messages.extend(history)
    api_messages.append({"role": "user", "content": user_message})

    tool_call_log: list[dict] = []
    citations:     list[dict] = []

    # ── 阶段一：非流式工具循环 ──────────────────────────────────────────────
    for _iter in range(MAX_TOOL_ITERATIONS):
        try:
            response_msg = _call_deepseek(api_messages, use_tools=True)
        except Exception as e:
            logger.error(f"[chat_agent] 工具检测调用失败 (iter {_iter}): {e}")
            break

        tool_calls = response_msg.get("tool_calls")

        # ── DSML 回退：DeepSeek 有时把工具调用写进 content 而非 tool_calls ──
        if not tool_calls:
            raw_content = response_msg.get("content") or ""
            dsml_calls  = _extract_dsml_tool_calls(raw_content)
            if dsml_calls:
                logger.info(f"[chat_agent] 检测到 DSML 格式工具调用，转换为标准格式: {[c['name'] for c in dsml_calls]}")
                # 把 DSML 转成标准 tool_calls 格式，追加到历史
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
                # 清理 content 里的 DSML，保留自然语言部分
                clean_content = _DSML_BLOCK_RE.sub("", raw_content)
                clean_content = _DSML_ANY_RE.sub("", clean_content).strip() or None
                response_msg  = {**response_msg, "content": clean_content, "tool_calls": tool_calls}

        if not tool_calls:
            # 确实没有工具调用，进入流式答复阶段
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
                    dsml_calls = item   # DSML 工具调用
        except Exception as e:
            logger.error(f"[chat_agent] 流式答复失败 (stream_iter {_s}): {e}")
            try:
                fallback = _call_deepseek(api_messages, use_tools=False)
                text = fallback.get("content", "（生成失败，请重试）")
                yield {"type": "answer_chunk", "content": text}
                answer_parts.append(text)
                iter_text.append(text)
            except Exception:
                pass
            break

        if dsml_calls:
            # 把已生成的文本 + DSML 工具调用写入历史
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
            # 执行这些工具
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
            # 继续下一轮流式
        else:
            # 正常结束，无 DSML
            if iter_text:
                api_messages.append({"role": "assistant", "content": "".join(iter_text)})
            break

    answer = "".join(answer_parts)

    new_history = [m for m in api_messages if m.get("role") != "system"]
    new_compaction = None
    if _count_user_turns(new_history) > COMPACT_THRESHOLD_TURNS:
        new_history, new_compaction = _compact(new_history)

    yield {
        "type":               "done",
        "answer":             answer,
        "tool_calls":         tool_call_log,
        "citations":          _enrich_citations(citations),
        "new_history":        new_history,
        "compaction_summary": new_compaction,
    }


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
                "block_id":      ann.get("block_id", ""),   # "p-N" 格式，供前端精准定位
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
            if not snippet:          # 没有摘要时不产生引用，避免空卡片
                continue
            citations.append({
                "paper_id":    item.get("paper_id", ""),
                "paper_title": item.get("title_zh") or item.get("title", ""),
                "block_idx":   -1,
                "text":        snippet,
                "heading":     "摘要",
                "type":        "paragraph",
            })


def _enrich_citations(citations: list) -> list:
    """为所有引用补全 paper_title / authors / year（单次 DB 查询）"""
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

    # 过滤掉 text 为空的引用（避免前端出现无内容的空卡片）
    return [c for c in citations if (c.get("text") or "").strip()]
