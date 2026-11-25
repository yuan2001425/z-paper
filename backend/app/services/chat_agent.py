"""
chat_agent.py — 知识库对话 Agent 主逻辑

三层记忆架构（参照 Claude Code）：
  L1 热数据  — 系统 prompt：静态规则 + 论文目录 + 词表（每轮重建）
  L2 温数据  — 最近其他会话的 auto_summary，注入系统 prompt
  L3 冷数据  — 所有历史对话（通过 search_chat_history 工具随时可查）

五级上下文压缩（参照 Claude Code compact / autoCompact / microCompact，全部按估计 token 数触发）：
  L1 剪裁   — 超过 SNIP_TOKEN_THRESHOLD 后截断旧 tool 结果（microcompact 等价，无 LLM）
  L2 微压缩  — 超过 MICRO_TOKEN_THRESHOLD 后折叠旧 tool_call+result 对（无 LLM）
  L3 折叠   — 超过 FOLD_TOKEN_THRESHOLD 后截短旧消息（无 LLM）
  L4 自动压缩 — 超过 L4_TOKEN_THRESHOLD 后用 session memory 摘要旧轮（sessionMemoryCompact：
               优先用已维护的 session memory，无才回退 LLM 摘要）
  L5 应急压缩 — 超过 L5_TOKEN_THRESHOLD 强制截断，或 413 错误（reactiveCompact）
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
from app.services.chat_tools import TOOL_SPECS, execute_tool, get_todos
from app.services import memory as memory_svc
from app.services.session_memory import (
    update_session_memory as _update_session_memory_impl,
    should_update_memory,
    render_for_injection,
)

logger = logging.getLogger(__name__)

# ── 压缩阈值常量 ───────────────────────────────────────────────────────────────
# 全部按「估计 token 数」触发（对标 autoCompact / microCompact），而非按轮数——
# 轮数无法反映真实上下文压力：一次大章节读取(数千字)与一句寒暄都算一轮。
# DeepSeek deepseek-chat 上下文约 64k token，留 12k+ 给输出与最近消息。
PRESERVE_RECENT       = 3      # 所有级别保留最近 N 轮原样
MAX_COMPACT_FAILURES  = 3      # 熔断：L4 连续失败次数上限
SNIP_TOKEN_THRESHOLD  = 12_000 # L1：超过后截断旧 tool 结果（microcompact 等价）
MICRO_TOKEN_THRESHOLD = 18_000 # L2：超过后折叠旧工具调用对
FOLD_TOKEN_THRESHOLD  = 24_000 # L3：超过后截短旧消息
L4_TOKEN_THRESHOLD    = 30_000 # L4：超过后 session-memory 压缩（sessionMemoryCompact）
L5_TOKEN_THRESHOLD    = 45_000 # L5：超过后应急截断（reactiveCompact）
MAX_TOOL_ITERATIONS   = 16     # 单次回答最多工具调用轮数
MAX_QUERY_DB_FAILURES = 3      # query_database 连续失败次数上限（熔断）

_TIMEOUT_TOOL   = httpx.Timeout(connect=15, read=90,  write=30, pool=10)
_TIMEOUT_STREAM = httpx.Timeout(connect=15, read=180, write=30, pool=10)


# ── System Prompt ─────────────────────────────────────────────────────────────

_STATIC_SYSTEM = """\
你是一名个人学术研究助手，帮助用户阅读、理解、总结其论文库。你的任务是读论文并回答/总结，不做写文件或改代码。

# 核心规则
1. 回答前必须先调用至少一个工具去查证。即使问题看似离题，也要先搜索。
2. 引用具体段落时，用 markdown 链接格式 `[block N](paper_id)` 标注：paper_id 用工具结果里的**完整**论文 ID（不要截断），N 是 block_idx。例如 `[block 13](f752a8df-cd89-4e78-a188-ff7c70f05ba4)`。同一篇论文的多个 block 用多个链接分别标。这样用户能点击跳转到原文对应段落。
3. 除非用户用英文提问，否则用中文回答。
4. 独立的工具调用要一次性并行发起（见下），不要串行等待。

# 回答风格（对标 Claude Code 的 Output efficiency）
- 直接给结论：先给答案，再给依据，不要铺垫、不要重复用户的问题。
- 简洁、信息密度高：不废话、不凑字、不堆砌无关过程。
- 用中文写通顺的成段文字，避免碎片化和过多符号；需要并列短事实（论文名、block_idx、数字）时才用列表/表格。
- 关键结论讲清楚即可，不要过度展开无关细节。

# 读论文的流程（先定位 → 看数量 → 再精读）
- 先定位：search_across_papers（跨库搜，不知道哪篇有内容时）或 search_in_paper（单篇内找具体概念/方法/结论/定义）。
- 再看数量：搜索结果带 total_found，先对全貌有数，再决定读哪些，不要只看前几条就下结论。search_across_papers 每页 30 条，用 offset 翻页看剩余命中。
- 再精读：get_paper_section 读整段章节全文，get_paragraph_context 读某 block 的前后文；或直接在 search_in_paper 里传 context=N 内联返回命中块前后 N 块（grep -C 等价），省一次调用。
- 论文按结构存储：正文块有 block_idx（相当于行号），标题块是章节（get_paper_outline 看大纲）。
- get_paper_outline 只用于理解整体结构，不要据此跳过章节——Introduction 常含核心贡献与定义，不要跳过。

# 工具使用
- 专用工具优先于 query_database。query_database 是最后手段，只有所有专用工具都不够时才用。
- 独立、无依赖的多个工具调用要**一次性并行发起**，提高效率；有依赖的才串行（比如先搜索拿到 paper_id，再读该 paper）。
- 双语检索（重要）：论文同时存英文原文和中文译文。中文提问时，中英文关键词都要搜；词表里有对应英文的用英文，没有的用自己的知识翻译后再搜。中文和英文各发一次独立调用，最大化召回。

# 批注（重要）
- 批注是用户写的个人笔记，可含任何内容（人名、事件、关系、提醒），不限于学术内容。
- 查询提到你不认识的人名/实体时，必须先 search_annotations 再下「找不到」的结论。
- 调用 search_annotations 时传具体名字/关键词（中文名也试拼音），不要传整句。

# 图片工具
- 当可视化（示意图/概念图/流程图）能帮助用户理解时，主动 generate_image，生成后用 ![说明](image_url) 嵌入回答；image_url 是 /uploads/chat_generated/xxx.jpg 格式，直接用。
- 仅当用户明确要求修改图片时用 edit_image，传入之前 generate_image 返回的 image_url。

# 任务规划与并行研究
- 复杂问题（如「对比多篇论文的方法」）先用 todo_write 拆解成步骤（搜 A → 读 A 方法段 → 搜 B → 读 B 方法段 → 综合对比），完成一项立即标记，不要攒着不标。
- 需要读**多篇论文**、且各篇相对独立时，用 spawn_research 把每个研究点写成一个**自包含**的 prompt（指定读哪篇论文、要回答什么），一次性并行分派多个只读子代理。子代理返回总结后你再综合——这样并行更快，且不会把大段原文塞进主上下文。子代理看不到主对话，prompt 必须写清楚。

# 上下文
- 对话有无限上下文：接近上限时系统会自动压缩旧内容。你应持续通过「会话研究笔记」保留关键结论，不因压缩而遗忘。
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


def _system_prompt(
    session_id: Optional[str] = None,
    session_memory: Optional[str] = None,
    user_message: str = "",
) -> str:
    parts = [_STATIC_SYSTEM]

    # 持久记忆指引 + 索引（总是注入，对标 MEMORY.md）
    parts.append(memory_svc.MEMORY_GUIDANCE)
    index = memory_svc.build_memory_index()
    if index:
        parts.append(index)
        # 只有存在记忆时才做「按需筛选」注入 ≤5 条相关正文（对标 findRelevantMemories）
        if user_message.strip():
            try:
                relevant = memory_svc.find_relevant_memories(user_message, _call_deepseek)
                rendered = memory_svc.render_relevant_memories(relevant)
                if rendered:
                    parts.append(rendered)
            except Exception as e:
                logger.warning(f"[chat_agent] 相关记忆筛选失败: {e}")

    # 会话任务清单（对标 TodoWrite 注入）
    todos = get_todos(session_id)
    if todos:
        marks = {"pending": "☐", "in_progress": "▶", "completed": "☑"}
        lines = ["\n== 当前任务清单（todo）=="]
        for t in todos:
            lines.append(f"- {marks.get(t.get('status'), '☐')} {t.get('content', '')}")
        lines.append("")
        parts.append("\n".join(lines))

    # 会话研究笔记（持续累积的关键结论）
    if session_memory:
        parts.append(
            "\n== 本会话研究笔记（持续累积；「关键结论」是此前已确认的完整答案，回答前优先参考）==\n"
            + render_for_injection(session_memory)
            + "\n"
        )

    parts.append(_build_library_catalog())
    parts.append(_build_glossary_hint())
    return "\n".join(parts)


# ── DeepSeek 调用 ─────────────────────────────────────────────────────────────

def _call_deepseek(messages: list[dict], use_tools: bool = True, tools: list | None = None, response_format: dict | None = None) -> dict:
    payload: dict = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 8192,
    }
    if use_tools:
        payload["tools"] = tools if tools is not None else TOOL_SPECS
        payload["tool_choice"] = "auto"
    if response_format:
        payload["response_format"] = response_format

    with httpx.Client(trust_env=False, timeout=_TIMEOUT_TOOL) as client:
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


def update_session_memory(
    session_memory: Optional[str],
    user_message: str,
    answer: str,
    tool_calls: Optional[list] = None,
) -> str:
    """会话研究笔记维护入口（忠实对标 Claude Code sessionMemory）。

    仅在本轮有实质进展时（回答够长，或调用了工具）才更新，避免寒暄轮污染笔记；
    实际的结构化增量更新在 app.services.session_memory 中。
    """
    if not should_update_memory(answer, bool(tool_calls)):
        return session_memory or ""
    return _update_session_memory_impl(
        session_memory or "",
        user_message,
        answer,
        _call_deepseek,
    )


def extract_durable_memories(user_message: str, answer: str, tool_calls: Optional[list] = None) -> int:
    """轮末提取跨会话持久记忆（对标 Claude Code extractMemories），返回保存条数。

    与 session memory 分工：session memory 是「本会话内」的研究笔记（会话结束即失去意义），
    持久记忆是「跨会话」的用户画像/偏好/研究主题/外部资源指针，长期有效。

    互斥（对标 extractMemories 的 hasMemoryWritesSince）：若本轮 agent 已用 save_memory
    主动存过记忆，则跳过后台提取，避免重复存。
    """
    if tool_calls and any(tc.get("name") == "save_memory" for tc in tool_calls):
        return 0
    try:
        items = memory_svc.extract_memories(user_message, answer, _call_deepseek)
        saved = 0
        for it in items:
            memory_svc.save_memory(it["type"], it["name"], it["description"], it["content"])
            saved += 1
        if saved:
            logger.info(f"[chat_agent] 提取 {saved} 条持久记忆")
        return saved
    except Exception as e:
        logger.warning(f"[chat_agent] extract_durable_memories 失败: {e}")
        return 0


import re as _re

_BAR = '\uFF5C'  # \uFF5C \u5168\u89D2\u7AD6\u7EBF\uFF08DSML \u5206\u9694\u7B26\uFF09

# DeepSeek \u5728 tool_choice="none" \u65F6\uFF0C\u4F1A\u5728 content \u91CC\u7528\u6587\u672C\u8868\u8FBE\u5DE5\u5177\u8C03\u7528\u3002\u5B9E\u9645\u89C2\u5BDF\u5230\u4E24\u79CD\u683C\u5F0F\uFF1A
#   1) <tool_call>{"name": "...", "arguments": {...}}</tool_call>   \u2014\u2014 \u5F53\u524D\u683C\u5F0F
#   2) <\uFF5CDSML\uFF5Cinvoke name="...">...</\uFF5CDSML\uFF5Cinvoke>\uFF08\u5355/\u53CC\u7AD6\u7EBF\u90FD\u517C\u5BB9\uFF09  \u2014\u2014 \u65E7 DSML \u683C\u5F0F
_TOOL_CALL_RE = _re.compile(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', _re.DOTALL)
_DSML_INVOKE_RE = _re.compile(
    r'<' + _BAR + r'+DSML' + _BAR + r'+\s*invoke\b[^>]*name="([^"]+)"[^>]*>(.*?)'
    r'</' + _BAR + r'+DSML' + _BAR + r'+\s*invoke\s*>', _re.DOTALL)
_DSML_PARAM_RE = _re.compile(
    r'(<' + _BAR + r'+DSML' + _BAR + r'+\s*parameter\b[^>]*>)'
    r'(.*?)'
    r'(</' + _BAR + r'+DSML' + _BAR + r'+\s*parameter\s*>)', _re.DOTALL)
# \u515C\u5E95\uFF1A\u6E05\u9664\u4EFB\u4F55\u5DE5\u5177\u8C03\u7528\u6807\u8BB0\uFF08<tool_call> \u6216 DSML\uFF09\uFF0C\u9632\u6B62\u6CC4\u6F0F\u5230\u56DE\u7B54
_TOOL_MARK_STRIP_RE = _re.compile(
    r'<tool_call>.*?</tool_call>'
    r'|</?' + _BAR + r'+DSML' + _BAR + r'+\s*[a-zA-Z_]*\s*[^>]*>', _re.DOTALL)


def _coerce_scalar(v: str):
    """DSML \u53C2\u6570\u503C\u7C7B\u578B\u8F6C\u6362\uFF08string="false" \u65F6\u503C\u4E3A\u6570\u5B57/\u5E03\u5C14\uFF09\u3002"""
    if v in ("true", "false"):
        return v == "true"
    try:
        return int(v)
    except (ValueError, TypeError):
        pass
    try:
        return float(v)
    except (ValueError, TypeError):
        pass
    return v


def _extract_tool_calls_from_text(content: str) -> list[dict]:
    """\u4ECE\u6A21\u578B\u6587\u672C\u91CC\u63D0\u53D6\u5DE5\u5177\u8C03\u7528\uFF0C\u652F\u6301 <tool_call> JSON \u548C DSML invoke \u4E24\u79CD\u683C\u5F0F\u3002"""
    calls: list[dict] = []
    # \u683C\u5F0F 1\uFF1A<tool_call> JSON
    for m in _TOOL_CALL_RE.finditer(content or ""):
        try:
            obj = json.loads(m.group(1))
            name = obj.get("name")
            if name:
                calls.append({"name": name, "args": obj.get("arguments") or {}})
        except Exception:
            pass
    # \u683C\u5F0F 2\uFF1ADSML invoke\uFF08\u53C2\u6570\u6309 string="true|false" \u8F6C\u7C7B\u578B\uFF09
    for invoke in _DSML_INVOKE_RE.finditer(content or ""):
        name = invoke.group(1)
        body = invoke.group(2)
        args: dict = {}
        for param in _DSML_PARAM_RE.finditer(body):
            open_tag = param.group(1)
            val = param.group(2).strip()
            mname = _re.search(r'name="([^"]+)"', open_tag)
            if not mname:
                continue
            pname = mname.group(1)
            if 'string="true"' not in open_tag:
                val = _coerce_scalar(val)
            args[pname] = val
        if name:
            calls.append({"name": name, "args": args})
    return calls


def _iter_raw_chunks(messages: list[dict], state: dict | None = None) -> Generator[str, None, None]:
    payload: dict = {
        "model":       settings.DEEPSEEK_MODEL,
        "messages":    messages,
        "temperature": 0.3,
        "max_tokens":  8192,
        "stream":      True,
        "tools":       TOOL_SPECS,
        "tool_choice": "none",
    }
    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type":  "application/json",
    }
    with httpx.Client(trust_env=False, timeout=_TIMEOUT_STREAM) as client:
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
                    obj = json.loads(raw)
                    choice = obj["choices"][0]
                    content = choice.get("delta", {}).get("content") or ""
                    if content:
                        yield content
                    fr = choice.get("finish_reason")
                    if fr:
                        if state is not None:
                            state["finish_reason"] = fr
                        if fr == "length":
                            logger.warning("[chat_agent] 流式回答被 max_tokens 截断（finish_reason=length）")
                except Exception:
                    pass


_TOOL_MARK_START_RE = _re.compile(r'<tool_call>|<' + _BAR + r'+DSML' + _BAR + r'+')
_TOOL_MARK_END_RE   = _re.compile(r'</tool_call>|</' + _BAR + r'+DSML' + _BAR + r'+\s*[a-zA-Z_]*\s*>')


def _stream_guarded(messages: list[dict], state: dict | None = None) -> Generator:
    """\u6D41\u5F0F\u8BFB\u53D6\uFF0C\u628A\u300C\u6B63\u6587\u300D\u4E0E\u300C\u5DE5\u5177\u8C03\u7528\u6807\u8BB0\u300D\u5206\u5F00\uFF1A\u6B63\u6587\u5B9E\u65F6 yield\uFF0C\u6807\u8BB0\u7D2F\u79EF\u5230\u6D41\u7ED3\u675F\u540E\u7EDF\u4E00\u89E3\u6790\u3002

    \u517C\u5BB9 <tool_call> JSON \u4E0E\u65E7 DSML \u4E24\u79CD\u683C\u5F0F\uFF1B\u89E3\u6790\u4E0D\u51FA\u5DE5\u5177\u8C03\u7528\u65F6\u515C\u5E95\u5265\u6389\u6807\u8BB0\uFF0C\u7EDD\u4E0D\u628A\u539F\u59CB\u6807\u8BB0\u6CC4\u6F0F\u6210\u4E71\u7801\u3002
    """
    buf       = ""
    markup    = ""
    in_markup = False
    keep      = 24  # \u8DB3\u591F\u5BB9\u7EB3 "<tool_call>" \u6216 "<\uFF5CDSML\uFF5C"

    for chunk in _iter_raw_chunks(messages, state):
        if in_markup:
            markup += chunk
        else:
            buf += chunk
            m = _TOOL_MARK_START_RE.search(buf)
            if m:
                idx = m.start()
                if idx > 0:
                    yield buf[:idx]
                markup    = buf[idx:]
                buf       = ""
                in_markup = True
            else:
                if len(buf) > keep:
                    yield buf[:-keep]
                    buf = buf[-keep:]

    if in_markup:
        calls = _extract_tool_calls_from_text(markup)
        if calls:
            yield calls
        else:
            # \u89E3\u6790\u4E0D\u51FA\u5DE5\u5177\u8C03\u7528\uFF1A\u5265\u6389\u6807\u8BB0\uFF0C\u6B8B\u7559\u6B63\u6587\u5F53\u4F5C\u7B54\u6848\uFF0C\u7EDD\u4E0D\u8BA9\u539F\u59CB\u6807\u8BB0\u6CC4\u6F0F
            yield _TOOL_MARK_STRIP_RE.sub("", markup)
    else:
        if buf:
            yield buf


# ── 五级压缩系统 ──────────────────────────────────────────────────────────────

def _estimate_chars(messages: list[dict]) -> int:
    return sum(len(str(m.get("content") or "")) for m in messages)


def _estimate_tokens(messages: list[dict]) -> int:
    """粗略 token 估计（对标 roughTokenCountEstimation：length/4）。"""
    return max(1, _estimate_chars(messages) // 4)


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


# 对标 compact/prompt.ts 的 9 段摘要结构（适配论文阅读场景）
_COMPACT_SUMMARY_SYSTEM = """\
你的任务是对这段研究对话做一个详细总结。总结将放在继续会话的开头，后续新消息会接在总结之后。请彻底总结，让只读总结 + 新消息的人能完全理解并继续。

总结包含以下部分：
1. 用户请求与意图：用户明确要什么、研究目标是什么
2. 关键技术概念：涉及的重要概念、术语、方法
3. 相关论文与章节：具体论文 + 关键 block_idx/章节定位，为什么相关
4. 错误与修正：踩过的坑、用户纠正、失败过的检索方式
5. 问题解决：已解决的问题、进行中的排查
6. 所有用户消息：非工具结果的用户消息逐条列出
7. 待办事项：尚未完成的任务
8. 当前进展：总结前正在做什么
9. 下一步：最可能接续的下一步

请用中文，信息密度高、具体（含论文名/术语/数字），不要包含工具调用细节。"""


def _apply_auto_compact(messages: list[dict], session_memory: Optional[str] = None) -> tuple[list[dict], str]:
    """L4：摘要旧轮次。返回 (保留的最近消息, summary文本)。

    对标 Claude Code sessionMemoryCompact：优先用「已增量维护的 session memory」作为摘要，
    而不是当场跑一次有损 LLM 摘要——session memory 里已有结构化关键结论（关键结论逐字保留），
    不会丢。无 session memory 时才回退到 LLM 摘要。
    摘要不再内嵌进消息列表（改由调用方用 compaction_summary 字段注入），避免双重注入。
    """
    cutoff = _get_cutoff(messages)
    old_msgs = messages[:cutoff]
    recent   = messages[cutoff:]

    if not old_msgs:
        return messages, ""

    if session_memory:
        summary = render_for_injection(session_memory)
    else:
        history_text = "\n".join(
            f"{m['role'].upper()}: {str(m.get('content',''))[:400]}"
            for m in old_msgs
            if m.get("role") in ("user", "assistant") and m.get("content")
        )
        summary_msg = _call_deepseek([
            {"role": "system", "content": _COMPACT_SUMMARY_SYSTEM},
            {"role": "user",   "content": history_text},
        ], use_tools=False)
        summary = summary_msg.get("content", "（历史已压缩）")

    return recent, summary


def _apply_emergency_compact(messages: list[dict]) -> list[dict]:
    """L5：强制截断，仅保留最近 PRESERVE_RECENT 轮。"""
    cutoff = _get_cutoff(messages)
    recent = messages[cutoff:]
    return [{"role": "system", "content": "[历史已截断，对话过长]"}] + recent


def _compress(
    messages: list[dict],
    consecutive_failures: int = 0,
    session_memory: Optional[str] = None,
) -> tuple[list[dict], Optional[str], int]:
    """
    五级压缩入口。返回 (压缩后消息, summary|None, 新的连续失败计数)。
    """
    # 熔断器
    if consecutive_failures >= MAX_COMPACT_FAILURES:
        logger.warning("[chat_agent] 熔断器触发：跳过压缩")
        return messages, None, consecutive_failures

    summary: Optional[str] = None

    # L5 应急（估计 token 数超限）
    if _estimate_tokens(messages) > L5_TOKEN_THRESHOLD:
        logger.warning("[chat_agent] L5 应急压缩：token 数超限")
        return _apply_emergency_compact(messages), None, consecutive_failures

    # L1 剪裁（每级压缩后重新估 token，避免过度压缩）
    if _estimate_tokens(messages) > SNIP_TOKEN_THRESHOLD:
        messages = _apply_snip(messages)

    # L2 微压缩
    if _estimate_tokens(messages) > MICRO_TOKEN_THRESHOLD:
        messages = _apply_micro_compact(messages)

    # L3 折叠
    if _estimate_tokens(messages) > FOLD_TOKEN_THRESHOLD:
        messages = _apply_fold(messages)

    # L4 自动压缩（session-memory）
    if _estimate_tokens(messages) > L4_TOKEN_THRESHOLD:
        try:
            messages, summary = _apply_auto_compact(messages, session_memory)
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
    session_memory: Optional[str] = None,
    scope_paper_ids=None,
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
    api_messages: list[dict] = [{"role": "system", "content": _system_prompt(session_id, session_memory, user_message)}]
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
            result_str = execute_tool(name, args, session_id=session_id, call_deepseek=_call_deepseek, scope_paper_ids=scope_paper_ids)

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

    new_history, new_compaction, compact_failures = _compress(new_history, compact_failures, session_memory)

    return {
        "answer":             answer,
        "tool_calls":         tool_call_log,
        "citations":          _enrich_citations(citations + _extract_answer_citations(answer)),
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
    session_memory: Optional[str] = None,
    scope_paper_ids=None,
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
    api_messages: list[dict] = [{"role": "system", "content": _system_prompt(session_id, session_memory, user_message)}]
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
            dsml_calls  = _extract_tool_calls_from_text(raw_content)
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
                clean_content = _TOOL_MARK_STRIP_RE.sub("", raw_content).strip() or None
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
            yield {"type": "tool_start", "name": name, "args": args,
                   "batch": _iter, "parallel": len(tool_calls) > 1}

            # spawn_research 会阻塞较久（并行子代理），用后台线程 + 队列把子任务进度实时流给前端，
            # 避免用户长时间静默（对标 Claude Code 的子代理进度通知，但结果仍同步回到本轮）
            if name == "spawn_research":
                import queue as _queue
                import threading as _threading
                _q: _queue.Queue = _queue.Queue()

                def _run_spawn():
                    try:
                        _r = execute_tool(name, args, session_id=session_id, call_deepseek=_call_deepseek,
                                          on_event=lambda e: _q.put(e), scope_paper_ids=scope_paper_ids)
                        _q.put(("__result__", _r))
                    except Exception as _e:
                        _q.put(("__result__", json.dumps({"error": str(_e)}, ensure_ascii=False)))

                _t = _threading.Thread(target=_run_spawn, daemon=True)
                _t.start()
                while True:
                    _item = _q.get()
                    if isinstance(_item, tuple) and len(_item) == 2 and _item[0] == "__result__":
                        result_str = _item[1]
                        break
                    yield _item  # subagent_start / subagent_done 事件
                _t.join()
            else:
                result_str = execute_tool(name, args, session_id=session_id, call_deepseek=_call_deepseek, scope_paper_ids=scope_paper_ids)

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

    # ── 阶段二：流式答复（DSML 工具循环 + 截断续写） ────
    answer_parts:  list[str] = []
    MAX_STREAM_EXTRA = 4     # DSML 工具额外循环上限
    MAX_CONTINUE = 10        # 截断续写次数上限

    dsml_rounds = 0
    continue_rounds = 0

    while True:
        iter_text:  list[str]  = []
        dsml_calls: list[dict] = []
        stream_state: dict = {}

        try:
            for item in _stream_guarded(api_messages, stream_state):
                if isinstance(item, str):
                    yield {"type": "answer_chunk", "content": item}
                    iter_text.append(item)
                    answer_parts.append(item)
                elif isinstance(item, list):
                    dsml_calls = item
        except Exception as e:
            logger.error(f"[chat_agent] 流式答复失败: {e}")
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
            if dsml_rounds >= MAX_STREAM_EXTRA:
                break
            fake_tcs = [
                {
                    "id":       f"s{dsml_rounds}_{i}",
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
                yield {"type": "tool_start", "name": name, "args": args,
                       "batch": 1000 + dsml_rounds, "parallel": len(dsml_calls) > 1}
                result_str = execute_tool(name, args, session_id=session_id, call_deepseek=_call_deepseek, scope_paper_ids=scope_paper_ids)
                tool_call_log.append({"name": name, "args": args, "result_snippet": result_str[:300]})
                _collect_citations(name, args, result_str, citations)
                yield {"type": "tool_done", "name": name, "snippet": result_str[:300]}
                api_messages.append({
                    "role":         "tool",
                    "tool_call_id": f"s{dsml_rounds}_{i}",
                    "content":      result_str,
                })
            dsml_rounds += 1
            continue

        # 无 DSML：正常结束或截断续写
        if iter_text:
            api_messages.append({"role": "assistant", "content": "".join(iter_text)})

        if stream_state.get("finish_reason") == "length":
            if continue_rounds >= MAX_CONTINUE:
                break
            api_messages.append({
                "role": "user",
                "content": "请继续，从刚才中断的地方接着输出，不要重复已输出的内容。",
            })
            continue_rounds += 1
            continue

        break

    answer = "".join(answer_parts)
    # 兜底：剥掉任何残留的工具调用标记（<tool_call>/DSML），绝不让其泄漏到最终回答
    answer = _TOOL_MARK_STRIP_RE.sub("", answer).strip()

    new_history = [m for m in api_messages if m.get("role") != "system"]
    new_history, new_compaction, compact_failures = _compress(new_history, compact_failures, session_memory)

    yield {
        "type":               "done",
        "answer":             answer,
        "tool_calls":         tool_call_log,
        "citations":          _enrich_citations(citations + _extract_answer_citations(answer)),
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


# ── 回答引用解析（[block N](paper_id) → 精确引用） ─────────────────────────────
_ANSWER_CITE_RE = _re.compile(r'\[block\s*(\d+)\]\(([0-9a-fA-F-]{36})\)')


def _fetch_block_text(paper_id: str, block_idx: int) -> str:
    """从论文结构里取某个 block 的文本（用于生成精确引用卡片）。"""
    from app.models.result import TranslationResult
    with SessionLocal() as db:
        result = db.query(TranslationResult).filter(
            TranslationResult.paper_id == paper_id
        ).order_by(TranslationResult.created_at.desc()).first()
    if not result:
        return ""
    blocks = result.structure_json.get("正文", [])
    if block_idx < 0 or block_idx >= len(blocks):
        return ""
    b = blocks[block_idx]
    return (b.get("中文文本") or b.get("文本", "")).strip()[:200]


def _extract_answer_citations(answer: str) -> list[dict]:
    """解析回答文本里的 `[block N](paper_id)` 引用，生成精确引用。

    这是「引用卡片齐全 + 可跳转」的关键：模型在回答里引用了哪些段落，就精确收集哪些，
    而不是靠工具结果的前 N 条近似（后者漏掉大量引用，导致「只显示 6 篇」）。
    """
    citations: list[dict] = []
    seen: set = set()
    for m in _ANSWER_CITE_RE.finditer(answer or ""):
        block_idx = int(m.group(1))
        paper_id = m.group(2).lower()
        key = (paper_id, block_idx)
        if key in seen:
            continue
        seen.add(key)
        citations.append({
            "paper_id":  paper_id,
            "block_idx": block_idx,
            "heading":   "",
            "text":      _fetch_block_text(paper_id, block_idx),
            "type":      "paragraph",
        })
    return citations


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

    # 去重（按 type + paper_id + block_idx），并过滤无正文的引用
    seen: set = set()
    out: list = []
    for c in citations:
        if not (c.get("text") or "").strip():
            continue
        key = (c.get("type"), c.get("paper_id"), c.get("block_idx", -1))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
