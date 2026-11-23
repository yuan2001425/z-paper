"""
session_memory.py — 会话研究笔记（Session Memory，忠实对标 Claude Code 的 sessionMemory）

Claude Code 的 sessionMemory 核心设计（src/services/SessionMemory/）：
  1. 一份**固定结构的 markdown 笔记**，按「关注点」分区（当前状态 / 研究任务 / 关键结论 /
     错误与修正 …），而不是按时间顺序记流水账。
  2. 每个分区由「标题行 + 一行斜体说明（模板指令）」组成；更新时只改说明下方的正文，
     标题和斜体说明必须原样保留。
  3. 最重要的是「关键结论」分区：用户要求的具体输出（答案 / 表格 / 对比 / 数字）要
     **完整、逐字地重复**写进去 —— 这是「多问几句前面的结论就忘」的正解。
  4. 每个分区有字数上限、整份笔记有总量预算，超限时要求模型精简掉次要细节、保留最关键信息。

与 Claude Code 的一个关键差异：Claude Code 让后台子代理用 Edit 工具做「定点编辑」来保住结构；
本模块用「整段重新生成 + 确定性结构修复」达成同一目的——重新生成后按规范模板重建 8 个分区，
模型漏掉的空分区补回、多出的非规范标题丢弃，从而保证结构永不漂移。

本模块把分区精简为 9 个（去掉 Workflow、Codebase 等只对写代码有意义的区，保留 Learnings →
「有效方法与心得」，并额外加「用户偏好」适配论文阅读场景）。分区顺序与 Claude Code 模板一致。
"""

import logging

logger = logging.getLogger(__name__)

# 每个分区的字数上限（提示模型自觉精简）。
# 注意：本笔记在 z-paper 里是「每轮都注入 system prompt」的（上下文窗口远小于 Claude Code），
# 因此总量要压得足够小，否则会反过来推高压缩频率。~10000 字 ≈ 2500 token，可接受。
MAX_SECTION_CHARS = 2000
MAX_TOTAL_CHARS = 10_000

# ── 固定模板：每个分区 = 标题行 + 一行斜体说明（模板指令） ──────────────────────
# (标题, 斜体说明) 列表是唯一的结构真相来源；模板文本、结构校验、注入前清洗都由它派生。
_SECTION_DESCRIPTIONS: list[tuple[str, str]] = [
    ("# 会话主题", "_用 5~10 个词概括本会话研究主题，信息密度高、无废话_"),
    ("# 当前状态", "_当前正在研究什么？还有哪些待办尚未完成？下一步立即要做什么？_"),
    ("# 研究任务", "_用户想解决什么问题、研究目标是什么？有哪些背景说明或约束？_"),
    ("# 相关论文与章节", "_有哪些重要论文？它们的关键章节 / block_idx / 术语是什么？为什么相关？_"),
    ("# 错误与修正", "_遇到过什么错误、怎么解决的？用户纠正过什么？哪些检索方式/假设试过且失败、不要再试？_"),
    ("# 有效方法与心得", "_哪些方法/检索方式有效、哪些无效、该避免什么？不要与其它分区重复_"),
    ("# 关键结论", "_如果用户要求了具体输出（答案、表格、对比、数字），把完整准确的结果逐字重复写在这里_"),
    ("# 用户偏好", "_用户在阅读/总结上有什么习惯与偏好？喜欢什么样的回答风格、术语译法？_"),
    ("# 工作日志", "_逐步极简记录：尝试了什么、完成了什么，每条一句话_"),
]

_SECTION_HEADERS = [h for h, _ in _SECTION_DESCRIPTIONS]
_DESCRIPTION_LINES = {d for _, d in _SECTION_DESCRIPTIONS}

SESSION_MEMORY_TEMPLATE = "\n".join(
    f"{h}\n{d}" for h, d in _SECTION_DESCRIPTIONS
) + "\n"


def _build_update_prompt(current_notes: str, user_message: str, answer: str) -> str:
    """生成「按结构增量更新笔记」的 prompt，忠实对标 Claude Code 的 getDefaultUpdatePrompt。

    关键规则全部来自源码：
      - 唯一的任务就是更新笔记，然后停止
      - 绝不改/删/增 `# 标题` 行
      - 绝不改/删 `_斜体说明_` 行（紧跟标题、以下划线开头结尾的那行是模板指令）
      - 只更新斜体说明行**下方**的正文
      - 不新增分区、不在现有结构之外加东西
      - 不提及「记笔记」这个流程本身
      - 没实质新内容就跳过该分区（保留空着，别删分区、别填「暂无」）
      - 内容要信息密度高、具体（论文名、章节、block_idx、术语、数字）
      - 关键结论分区要放完整、准确的输出
      - 总是更新「当前状态」
    """
    return (
        "重要：本条消息和这些指令不属于真实用户对话的一部分。"
        "不要在笔记内容中提及「记笔记」「提取笔记」或这些更新指令本身。\n\n"
        "基于下面的用户对话（排除这条记笔记指令本身、系统提示、以及任何历史摘要），"
        "更新会话研究笔记。\n\n"
        "笔记当前内容如下：\n"
        "<current_notes>\n"
        f"{current_notes}\n"
        "</current_notes>\n\n"
        "你唯一的任务就是输出更新后的完整笔记，然后停止。可以更新任意多个分区。不要做任何其他事。\n\n"
        "关键编辑规则：\n"
        "- 必须保持笔记的精确结构：所有 `# 标题` 行及其下紧跟的 `_斜体说明_` 行**原样保留**。\n"
        "- 绝不要修改、删除或新增任何 `# 标题` 行（例如 `# 关键结论`）。\n"
        "- 绝不要修改或删除 `_斜体说明_` 行（紧跟标题、以下划线开头结尾的那一行，是模板指令）。\n"
        "- 只更新每个分区斜体说明行**下方**的实际正文。\n"
        "- 不要在现有结构之外新增任何分区、总结或信息。\n"
        "- 不要在笔记中提及「记笔记」这个流程。\n"
        "- 如果某分区没有实质性新内容，就让它保持空着（保留标题和斜体说明），不要填「暂无」之类的占位废话，更不要删除该分区。\n"
        "- 正文要详细、信息密度高，包含具体细节（论文名、章节、block_idx、术语、数字）。\n"
        "- 「# 关键结论」分区：把用户要求的完整、准确输出（完整表格、完整答案、完整对比）重复写进去。\n"
        f"- 每个分区控制在约 {MAX_SECTION_CHARS} 字以内；若接近上限，精简掉次要细节、保留最关键信息。\n"
        f"- 整份笔记控制在约 {MAX_TOTAL_CHARS} 字以内，超限时主动压缩较旧/次要的内容。\n"
        "- 聚焦可操作、具体的信息，能帮助理解或重建本会话讨论的研究内容。\n"
        "- 重要：总是更新「# 当前状态」，反映最新进展 —— 这对遗忘后的连续性至关重要。\n\n"
        "本轮用户提问：\n"
        f"{user_message}\n\n"
        "本轮回答（可能被截断）：\n"
        f"{answer}\n\n"
        "请直接输出更新后的完整笔记（markdown，含所有 `# 标题` 和 `_斜体说明_` 行），不要输出任何解释或前缀。"
    )


def _repair_structure(text: str) -> str | None:
    """按规范模板重建结构（确定性修复）。

    - 若输出里连一个 `# 标题` 都没有 → 返回 None（模型完全失败，调用方回退旧笔记）。
    - 否则：按标题切分正文，丢弃模型自行新增的非规范标题，把内容填回规范顺序的 8 个分区；
      模型漏掉的分区补回（空着），残留的斜体说明行去重后由模板统一输出。
    """
    if not any(line.startswith("# ") for line in text.split("\n")):
        return None

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.split("\n"):
        s = line.rstrip("\n")
        if s.startswith("# "):
            current = s.strip()
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(s)

    out: list[str] = []
    for header, desc in _SECTION_DESCRIPTIONS:
        body = [
            l for l in sections.get(header, [])
            if l.strip() not in _DESCRIPTION_LINES  # 去掉模型可能重复输出的斜体说明
        ]
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
        out.append(header)
        out.append(desc)
        out.extend(body)
        out.append("")
    return "\n".join(out).strip() + "\n"


def render_for_injection(memory: str) -> str:
    """注入 system prompt 前，去掉模板的 `_斜体说明_` 行。

    斜体说明是对「笔记维护者」的指令，不是内容；每轮注入主对话时它们是噪声，也占 token。
    """
    return "\n".join(
        line for line in memory.split("\n")
        if line.strip() not in _DESCRIPTION_LINES
    ).strip()


def should_update_memory(answer: str, tool_calls_called: bool) -> bool:
    """判断本轮是否值得更新笔记（对标 shouldExtractMemory 的「自然停顿 / 实质性进展」）。

    z-paper 对话规模远小于 Claude Code 的编程会话，故不做 1 万 token 的初始化门槛，
    只跳过「没有实质进展的寒暄轮」：
      - 回答过短（< 120 字）且没有调用任何工具 → 跳过。
    """
    return (len(answer or "") >= 120) or tool_calls_called


def update_session_memory(
    current_notes: str,
    user_message: str,
    answer: str,
    call_deepseek,
) -> str:
    """用一次轻量 DeepSeek 调用，按固定结构增量更新会话研究笔记。

    参数 call_deepseek：一个可调用对象 (messages, use_tools=False) -> dict，返回
    {"content": str}。由 chat_agent 注入，避免本模块直接依赖 HTTP 客户端。

    返回更新后的笔记文本；若调用失败或结构完全损坏，返回原笔记（宁可少更新也不破坏结构）。
    """
    existing = (current_notes or "").strip()
    # 首次写入：以模板作为「当前笔记」喂给模型，模型才能看到并保留标题 + 斜体说明结构
    # （对标 Claude Code 的 setupSessionMemoryFile：先把模板写入文件，再读出来做增量更新）。
    current = existing or SESSION_MEMORY_TEMPLATE
    prompt = _build_update_prompt(current, user_message, answer)

    try:
        result = call_deepseek(
            [{"role": "user", "content": prompt}],
            use_tools=False,
        )
        new_notes = (result.get("content") or "").strip()
    except Exception as e:
        logger.warning(f"[session_memory] 更新笔记失败: {e}")
        return existing

    if not new_notes:
        return existing

    # 结构修复：保证标题/斜体说明/顺序永远符合规范模板
    repaired = _repair_structure(new_notes)
    if repaired is None:
        logger.warning("[session_memory] 更新结果无结构，丢弃本次更新")
        return existing

    return repaired
