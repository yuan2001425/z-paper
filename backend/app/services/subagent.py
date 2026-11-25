"""
subagent.py — 并行只读研究子代理（对标 Claude Code Coordinator 的 Research worker）

Claude Code 的 Coordinator（src/coordinator/）核心思想：
  - 主 Agent 把复杂问题拆成多个**自包含**的研究子任务，并行分派给多个 worker；
  - worker 各自跑一个独立的工具循环（只读搜索/阅读），把结果**总结**成一段返回；
  - 主 Agent 收到各 worker 的总结后综合（Synthesis），而不是把原始工具输出都塞进主上下文
    —— 这既并行加速，又**保护主上下文窗口**不被大段正文污染。

本模块把「多 worker + 异步通知」简化成「一个 spawn_research 工具，线程池并发跑 N 个
只读子代理，一次返回全部总结」。z-paper 的工具循环是同步的，这样最贴合、改动最小。

关键规则（来自 coordinatorMode.ts）：
  - worker 看不到主对话 → 每个子任务 prompt 必须自包含；
  - 只读研究任务自由并行；
  - 子代理只做只读搜索/阅读，不做写、不生成图、不碰记忆/任务清单。
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# 子代理可用的只读工具（排除 query_database/图片/记忆/任务清单/历史对话等）
READ_ONLY_TOOL_NAMES = {
    "search_papers",
    "get_paper_outline",
    "search_in_paper",
    "get_paper_section",
    "get_references",
    "get_annotations",
    "search_annotations",
    "search_across_papers",
    "get_paragraph_context",
    "get_paper_metadata",
}

_MAX_ITERS = 6  # 每个子代理最多工具轮数，防止钻牛角尖（对标 extractMemories 的 maxTurns）

_SUBAGENT_SYSTEM = """\
你是一个论文研究子代理。用工具搜索/阅读论文库，回答交给你的研究问题，然后返回总结。

规则：
- 自包含：你看不到主对话的其它内容，只根据交给你的研究问题工作。
- 必须用工具查证，不要凭空编造。
- 先定位（search_across_papers / search_in_paper），看 total_found 对全貌有数，再精读（get_paper_section / get_paragraph_context）。
- 双语检索：中文问题中英文关键词都要搜。
- 只做只读搜索与阅读，不调用图片/记忆/任务清单工具。
- 最后返回一段信息密度高的总结：涉及的论文标题、关键结论、关键 block_idx 定位、必要的原文引用。
- 总结要简洁完整，让主 Agent 不读原文也能据此综合对比。
"""


def _read_only_specs(tool_specs: list[dict]) -> list[dict]:
    return [t for t in tool_specs if t["function"]["name"] in READ_ONLY_TOOL_NAMES]


def run_subagent(
    prompt: str,
    call_deepseek,
    execute_tool_fn,
    tool_specs: list[dict],
    max_iters: int = _MAX_ITERS,
    scope_paper_ids=None,
) -> str:
    """跑一个只读研究子代理，返回其总结文本。"""
    tools = _read_only_specs(tool_specs)
    messages: list[dict] = [
        {"role": "system", "content": _SUBAGENT_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    for _ in range(max_iters):
        msg = call_deepseek(messages, use_tools=True, tools=tools)
        messages.append(msg)
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return msg.get("content", "") or ""

        for tc in tool_calls:
            func = tc["function"]
            try:
                args = json.loads(func.get("arguments") or "{}")
            except Exception:
                args = {}
            try:
                result_str = execute_tool_fn(func["name"], args, scope_paper_ids=scope_paper_ids)
            except Exception as e:
                result_str = json.dumps({"error": str(e)}, ensure_ascii=False)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result_str,
            })

    # 达到轮数上限：让模型给出最终总结
    try:
        final = call_deepseek(messages, use_tools=False)
        return final.get("content", "") or ""
    except Exception as e:
        logger.warning(f"[subagent] 最终总结失败: {e}")
        return ""


def spawn_research(
    tasks: list,
    call_deepseek,
    execute_tool_fn,
    tool_specs: list[dict],
    on_event=None,
    scope_paper_ids=None,
) -> str:
    """并行跑多个研究子任务。tasks = [{task_id?, prompt}]。返回 JSON 结果列表（保持顺序）。

    on_event：可选回调，用于把子任务进度实时推给上层（subagent_start/subagent_done），
    解决同步阻塞导致用户长时间静默的问题。
    """
    if not tasks:
        return json.dumps({"subagent_results": [], "error": "tasks 为空"}, ensure_ascii=False)

    # 归一化 + 去重（相同 prompt 只跑一次，但保留原顺序）
    normalized = []
    for t in tasks:
        if not isinstance(t, dict) or not (t.get("prompt") or "").strip():
            continue
        normalized.append({
            "task_id": (t.get("task_id") or "").strip() or t["prompt"].strip()[:24],
            "prompt": t["prompt"].strip(),
        })

    if not normalized:
        return json.dumps({"subagent_results": [], "error": "tasks 无有效 prompt"}, ensure_ascii=False)

    if on_event:
        on_event({
            "type": "subagent_start",
            "count": len(normalized),
            "tasks": [t["task_id"] for t in normalized],
        })

    results: dict[str, str] = {}
    # 并发：每个子代理一个线程；子代理内部串行多轮工具调用，不同子代理之间并行
    with ThreadPoolExecutor(max_workers=len(normalized)) as pool:
        futures = {
            pool.submit(run_subagent, t["prompt"], call_deepseek, execute_tool_fn, tool_specs, _MAX_ITERS, scope_paper_ids): t
            for t in normalized
        }
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                result = fut.result()
            except Exception as e:
                logger.warning(f"[subagent] 子任务 {t['task_id']} 失败: {e}")
                result = f"（子任务失败：{e}）"
            results[t["task_id"]] = result
            if on_event:
                on_event({"type": "subagent_done", "task_id": t["task_id"], "snippet": result[:200]})

    ordered = [
        {"task_id": t["task_id"], "prompt": t["prompt"], "result": results.get(t["task_id"], "")}
        for t in normalized
    ]
    return json.dumps({"subagent_results": ordered}, ensure_ascii=False)
