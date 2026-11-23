"""
memory.py — 跨会话持久记忆系统（忠实对标 Claude Code 的 memdir + extractMemories）

Claude Code 的设计（src/memdir/ + src/services/extractMemories/）：
  1. 四类记忆 user / feedback / project / reference，各有 when_to_save / how_to_use；
  2. 「索引」MEMORY.md 总是注入 system prompt（一行一条），具体记忆正文**按需筛选最多 5 条**注入；
  3. 记忆会过时，回答前要对照当前状态验证（drift 警告）；
  4. 明确「什么不要存」（能从库/代码里查到的、临时状态、寒暄）；
  5. 后台在每轮结束时提取持久记忆（extractMemories）。

本模块把「记忆文件」映射到 SQLite 表 `memories`，其余语义保持一致。
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

from app.database import SessionLocal
from app.models.memory import Memory

logger = logging.getLogger(__name__)

MEMORY_TYPES = ["user", "feedback", "project", "reference"]

# ── 注入 system prompt 的记忆指引（对标 memoryTypes.ts 的 TYPES / WHAT_NOT_TO_SAVE / HOW_TO_SAVE）──
MEMORY_GUIDANCE = """\
## 持久记忆系统（跨会话）

你在数据库里维护一份跨会话的持久记忆，让未来的对话能了解用户、其偏好与进行中的研究。四类记忆：

- **user**：用户的角色、研究领域、知识背景、读论文习惯。何时存：了解到用户角色/偏好/知识时。何时用：回答要贴合用户画像（例如用户是某领域专家，就用更专业的术语）。
- **feedback**：用户对你工作方式的指导（纠正「别这样」+ 确认「对，就这样」）。正文用「规则 + **Why:** 原因 + **How to apply:** 何时适用」结构，便于日后判断边界。何时存：用户纠正或确认你的做法时。
- **project**：进行中的研究主题、待解决问题、目标（库里查不出来的上下文）。相对日期要转成绝对日期。何时存：了解到「谁在做什么、为什么、何时」时。
- **reference**：指向外部资源的指针（某篇论文的 DOI/链接、某个数据集、某个工具的位置）。何时存：了解到外部系统及其用途时。

**什么不要存**：论文库正文/结构（用搜索工具能查到）、当前对话的临时状态、代码/git 里能看出来的东西。

**怎么存**：用 save_memory 工具。name 用英文短横线 slug；description 一句话（用于日后判断相关性，要具体）；content 写正文。按主题组织不按时间；更新或删除过时/错误的记忆；不重复存，先查 list_memories 是否已有可更新的。

**何时读**：记忆相关、或用户提到之前对话的内容时；用户明确要求记住/回忆时必须查。

**记忆会过时**：回答前若记忆与实际不符，以当前观察为准，并更新或删除过时记忆。
"""

_SELECT_PROMPT = (
    "你在为论文阅读助手挑选与当前问题相关的记忆。给你用户的问题和可用记忆列表（name + 描述）。\n"
    '只输出 JSON 对象，格式：{"selected_memories": ["name1", "name2"]}，最多 5 个。'
    "只选你确信对回答有帮助的；不确定的不要选；没有相关的就输出 {\"selected_memories\": []}。"
)

_EXTRACT_SYSTEM = (
    "你是记忆提取助手。分析本轮对话，判断是否产生了值得**跨会话**保存的持久记忆。\n\n"
    + MEMORY_GUIDANCE
    + "\n\n"
    "你的任务：只输出 JSON 数组（不要任何其它文字、不要 markdown 代码块），每项："
    '{{"type": "user|feedback|project|reference", "name": "英文短横线slug", "description": "一行描述", "content": "正文"}}。'
    "没有值得存的就输出 []。"
)


def build_memory_index() -> str:
    """构建「索引」：所有记忆的一行摘要（对标 MEMORY.md，总是注入）。"""
    with SessionLocal() as db:
        memories = db.query(Memory).order_by(Memory.updated_at.desc()).all()
    if not memories:
        return ""
    lines = ["\n== 持久记忆索引（跨会话，共 %d 条）==" % len(memories)]
    for m in memories[:120]:  # 索引上限 120 条，防爆
        lines.append(f"- [{m.type}] {m.name} — {m.description}")
    if len(memories) > 120:
        lines.append(f"（另有 {len(memories) - 120} 条，用 list_memories 查询）")
    lines.append("")
    return "\n".join(lines)


def find_relevant_memories(query: str, call_deepseek, limit: int = 5) -> list[dict]:
    """按需筛选：从索引里挑出与当前 query 最相关的 ≤5 条记忆，返回其完整内容。

    对标 findRelevantMemories：先扫 name+description，再用一个轻量模型选出最相关的，
    命中后注入完整正文。失败时返回 []（优雅降级，不阻塞主流程）。
    """
    with SessionLocal() as db:
        memories = db.query(Memory).all()
    if not memories:
        return []

    manifest = "\n".join(f"- {m.name} ({m.type}): {m.description}" for m in memories)
    prompt = (
        f"用户问题：{query}\n\n可用记忆列表：\n{manifest}"
    )
    try:
        # 对标 sideQuery：json_schema 结构化输出，max_tokens 小（选择器只需返回 name 列表）
        result = call_deepseek(
            [{"role": "system", "content": _SELECT_PROMPT}, {"role": "user", "content": prompt}],
            use_tools=False,
            response_format={"type": "json_object"},
        )
        text = result.get("content", "") or ""
    except Exception as e:
        logger.warning(f"[memory] find_relevant_memories 调用失败: {e}")
        return []

    # 解析 JSON；失败则回退为「检查哪些已有 name 出现在输出里」
    valid = {m.name for m in memories}
    try:
        selected_names = json.loads(text).get("selected_memories", [])
    except Exception:
        selected_names = [n for n in valid if n in text]
    selected = [n for n in valid if n in selected_names][:limit]
    return [
        {"type": m.type, "name": m.name, "description": m.description, "content": m.content, "updated_at": m.updated_at}
        for m in memories if m.name in selected
    ]


def _staleness_note(updated_at) -> str:
    """对标 memoryAge.memoryFreshnessNote：记忆超过 1 天未更新时附「可能过时」警告。

    记忆是「写入时的时间点快照」，不是实时状态；命名的论文/术语/结论可能已变。
    """
    if not updated_at:
        return ""
    days = (datetime.utcnow() - updated_at).days
    if days <= 1:
        return ""
    return (
        f"（该记忆已 {days} 天未更新，是写入时的时间点快照而非实时状态；"
        f"若与当前观察不符，以当前观察为准，并更新或删除该记忆。）"
    )


def render_relevant_memories(selected: list[dict]) -> str:
    """把选中的相关记忆用 <system-reminder> 包装注入（对标 wrapMessagesInSystemReminder），
    并附过时警告，让模型知道这是「背景上下文」而非用户指令。"""
    if not selected:
        return ""
    lines = ["\n== 相关持久记忆（根据当前问题从记忆库选中）=="]
    for m in selected:
        head = f"【{m['type']}】{m['name']} — {m['description']}"
        note = _staleness_note(m.get("updated_at"))
        if note:
            head += f"\n{note}"
        lines.append(f"<system-reminder>\n{head}\n\n{m['content']}\n</system-reminder>")
    lines.append("")
    return "\n".join(lines)


def save_memory(memory_type: str, name: str, description: str, content: str) -> str:
    """保存/更新一条记忆（按 name upsert）。"""
    name = (name or "").strip().lower().replace(" ", "-")
    if not name:
        return json.dumps({"error": "name 必填"}, ensure_ascii=False)
    if not (content or "").strip():
        return json.dumps({"error": "content 必填"}, ensure_ascii=False)
    if memory_type not in MEMORY_TYPES:
        memory_type = "reference"

    with SessionLocal() as db:
        m = db.query(Memory).filter(Memory.name == name).first()
        if m:
            m.type = memory_type
            m.description = (description or m.description).strip()
            m.content = content.strip()
            m.updated_at = datetime.utcnow()
        else:
            m = Memory(
                type=memory_type,
                name=name,
                description=(description or "").strip(),
                content=content.strip(),
            )
            db.add(m)
        db.commit()
    return json.dumps({"ok": True, "name": name, "type": memory_type}, ensure_ascii=False)


def list_memories() -> str:
    with SessionLocal() as db:
        memories = db.query(Memory).order_by(Memory.updated_at.desc()).all()
    return json.dumps({
        "memories": [
            {"name": m.name, "type": m.type, "description": m.description, "content": m.content}
            for m in memories
        ],
        "count": len(memories),
    }, ensure_ascii=False)


def delete_memory(name: str) -> str:
    with SessionLocal() as db:
        m = db.query(Memory).filter(Memory.name == (name or "").strip()).first()
        if not m:
            return json.dumps({"error": f"未找到记忆 {name}"}, ensure_ascii=False)
        db.delete(m)
        db.commit()
    return json.dumps({"ok": True, "name": name}, ensure_ascii=False)


def extract_memories(user_message: str, answer: str, call_deepseek) -> list[dict]:
    """轮末提取：判断本轮是否产生值得跨会话保存的持久记忆，返回要保存的记忆列表。

    对标 extractMemories（后台 fork 在每轮结束提取）。这里用一次轻量调用完成。
    """
    with SessionLocal() as db:
        memories = db.query(Memory).all()
    manifest = "\n".join(f"- {m.name} ({m.type}): {m.description}" for m in memories) or "（暂无）"

    # 对标 buildExtractAutoOnlyPrompt：完整分类学作为 system，manifest + 本轮内容作为 user
    user_prompt = (
        f"已有记忆（name + 描述，避免重复，可更新的给同名覆盖）：\n{manifest}\n\n"
        f"本轮用户提问：\n{user_message}\n\n"
        f"本轮回答（可截断）：\n{(answer or '')[:1500]}"
    )
    try:
        result = call_deepseek(
            [{"role": "system", "content": _EXTRACT_SYSTEM}, {"role": "user", "content": user_prompt}],
            use_tools=False,
        )
        text = result.get("content", "") or ""
    except Exception as e:
        logger.warning(f"[memory] extract_memories 调用失败: {e}")
        return []

    # 稳健解析 JSON 数组（容忍模型包了 markdown 代码块或前后废话）
    try:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        items = json.loads(m.group(0)) if m else []
    except Exception:
        return []

    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if not it.get("name") or not it.get("content"):
            continue
        out.append({
            "type": it.get("type", "reference") if it.get("type") in MEMORY_TYPES else "reference",
            "name": it.get("name"),
            "description": it.get("description", ""),
            "content": it.get("content"),
        })
    return out
