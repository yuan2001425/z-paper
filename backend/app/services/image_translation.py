"""
图片翻译服务

流程：
  1. Qwen-VL-Max 提取图片中的所有文字（OCR）
  2. DeepSeek 结合词表翻译文字，返回每段文字的翻译策略
  3. qwen-image-2.0-pro 在原图上按翻译指令替换文字
"""
import base64
import json
import logging
import re
import time
import httpx

from app.config import settings
from app.storage.local_storage import local_storage

logger = logging.getLogger(__name__)


def _http_post_with_retry(url: str, headers: dict, body: dict, timeout: int = 120, max_retries: int = 4) -> httpx.Response:
    """带指数退避重试的 POST，专门处理 429 限流。"""
    delay = 5
    for attempt in range(max_retries):
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=body)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        wait = delay * (2 ** attempt)
        logger.warning("[ImageTranslation] 429 限流，%d 秒后重试（第 %d/%d 次）", wait, attempt + 1, max_retries)
        time.sleep(wait)
    # 最后一次不捕获，让调用方处理
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=body)
    resp.raise_for_status()
    return resp


# ── 入口函数 ─────────────────────────────────────────────────────────────────

def translate_image(
    image_url: str,
    glossary_list: list[dict],
    paper_id: str = "",
    domain: str = "学术",
) -> str:
    """
    翻译单张图片中的文字。

    参数：
      image_url:     图片 URL，格式如 /uploads/papers/{id}/images/fig1.png
      glossary_list: 词表，格式 [{"en": ..., "zh": ..., "status": ...}]
      paper_id:      论文 ID，用于构造翻译后图片的存储路径
      domain:        学科领域，用于改善翻译质量

    返回：翻译后图片的 URL（失败或无需翻译时返回原 URL）
    """
    if not settings.QWEN_API_KEY:
        logger.warning("[ImageTranslation] QWEN_API_KEY 未配置，跳过图片翻译")
        return image_url

    image_b64, media_type = _load_image_b64(image_url)
    if not image_b64:
        return image_url

    # Step 1：OCR
    texts = _extract_text(image_b64, media_type)
    if not texts:
        logger.info("[ImageTranslation] 图片无可识别文字，跳过: %s", image_url)
        return image_url
    logger.info("[ImageTranslation] 识别到 %d 段文字: %s", len(texts), texts)

    # Step 2：DeepSeek 翻译
    translation_map = _translate_texts(texts, glossary_list, domain)
    if not translation_map:
        logger.warning("[ImageTranslation] 翻译结果为空，原图通过")
        return image_url

    # 过滤掉无需修改的项：never_translate 或 中英文完全一致（翻译结果等于原文）
    actionable = {
        orig: info
        for orig, info in translation_map.items()
        if info["action"] != "never_translate" and info["translated"] != orig
    }
    if not actionable:
        logger.info("[ImageTranslation] 无需修改文字，原图通过: %s", image_url)
        return image_url

    # Step 3：qwen-image-2.0-pro 编辑图片
    return _edit_image(image_url, image_b64, media_type, actionable, paper_id, domain=domain)


# ── Step 1：Qwen-VL OCR ────────────────────────────────────────────────────────

def _load_image_b64(image_url: str) -> tuple[str, str]:
    """从本地存储加载图片，返回 (base64字符串, media_type)"""
    key = image_url.lstrip("/")
    if key.startswith("uploads/"):
        key = key[len("uploads/"):]

    try:
        data = local_storage.get_object(key)
    except Exception as e:
        logger.warning("[ImageTranslation] 图片加载失败 key=%s: %s", key, e)
        return "", "image/png"

    lower = key.lower()
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        media_type = "image/jpeg"
    elif lower.endswith(".webp"):
        media_type = "image/webp"
    else:
        media_type = "image/png"

    return base64.b64encode(data).decode(), media_type


def _extract_text(image_b64: str, media_type: str) -> list[str]:
    """调用 Qwen-VL-Max 提取图片中所有有意义的文字"""
    prompt = (
        "请仔细识别这张学术论文图表中所有可见的文字内容，包括：\n"
        "- 坐标轴标签（X轴、Y轴的名称，不包括刻度数字）\n"
        "- 图例文字（Legend）\n"
        "- 图表标题\n"
        "- 标注文字、说明文字\n"
        "- 表格中的文字\n\n"
        "注意：\n"
        "- 提取完整的词组，不要逐字拆分（如 \"Training Loss\" 作为一个整体提取）\n"
        "- 纯数字（如 0.1、100）、数学符号、坐标刻度值 不提取\n"
        "- 单独的字母变量（如 x、y、t）不提取\n\n"
        "以 JSON 格式返回：{\"texts\": [\"text1\", \"text2\", ...]}\n"
        "若图中没有文字，返回：{\"texts\": []}"
    )

    logger.warning(
        "[ImageTranslation/OCR] → %s\n%s\n%s\n%s",
        settings.QWEN_VL_MODEL, "─" * 60, prompt, "─" * 60,
    )

    try:
        resp = _http_post_with_retry(
            url=f"{settings.QWEN_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.QWEN_API_KEY}",
                "Content-Type": "application/json",
            },
            body={
                "model": settings.QWEN_VL_MODEL,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            },
            timeout=30,
        )

        content = resp.json()["choices"][0]["message"]["content"]
        logger.warning(
            "[ImageTranslation/OCR] ← 响应\n%s\n%s\n%s",
            "─" * 60, content, "─" * 60,
        )

        m = re.search(r"\{[\s\S]*\}", content)
        if not m:
            return []
        data = json.loads(m.group())
        return [t for t in data.get("texts", []) if isinstance(t, str) and t.strip()]

    except Exception as e:
        logger.warning("[ImageTranslation] Qwen-VL OCR 失败: %s", e)
        return []


# ── Step 2：DeepSeek 翻译 ──────────────────────────────────────────────────────

def _translate_texts(
    texts: list[str],
    glossary_list: list[dict],
    domain: str,
) -> dict[str, dict]:
    """
    调用 DeepSeek 翻译文字列表，结合词表规则。

    返回：{原文: {"translated": 译文, "action": "translate"|"never_translate"|"translate_with_annotation"}}
    """
    # 只传与当前图片文字相关的词表条目
    texts_lower = " ".join(texts).lower()
    relevant = [t for t in glossary_list if t.get("en", "").lower() in texts_lower]

    def _rule_lines(status_filter, fmt):
        lines = [
            fmt(t) for t in relevant
            if t.get("status", "translate") == status_filter and t.get("en")
        ]
        return "\n".join(f"  {l}" for l in lines) if lines else "（无）"

    translate_rules = _rule_lines(
        "translate",
        lambda t: f'{t["en"]} → 固定译为「{t["zh"]}」' if t.get("zh") else "",
    )
    never_rules = _rule_lines("never_translate", lambda t: f'{t["en"]}（保留英文）')
    annotation_rules = _rule_lines(
        "translate_with_annotation",
        lambda t: f'{t["en"]} → 译为「{t["zh"]}」并括注原文' if t.get("zh") else "",
    )

    messages = [
        {
            "role": "system",
            "content": (
                f"你是精通{domain}领域的学术翻译专家。"
                "严格按要求输出 JSON 数组，不输出其他内容。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"以下是从{domain}领域学术论文图表中提取的文字列表：\n"
                f"{json.dumps(texts, ensure_ascii=False)}\n\n"
                "请对每段文字决定翻译策略，并给出翻译结果。\n\n"
                "【用户词表规则（优先级最高）】\n"
                f"固定翻译的词：\n{translate_rules}\n\n"
                f"保留英文的词：\n{never_rules}\n\n"
                f"翻译并括注原文的词：\n{annotation_rules}\n\n"
                "【通用翻译规则】\n"
                "- 有公认中文名的学术词：action=translate（如 Accuracy→准确率，Loss→损失，Epoch→轮次）\n"
                "- 人名、数学符号、数据集名、代码：action=never_translate\n"
                "- 无公认中文名的小众术语：action=never_translate（保留英文）\n"
                "- 缩写词（DNA、AI、LSTM 等）：action=never_translate\n\n"
                "action 含义：\n"
                "  translate               → 图中只显示中文\n"
                "  never_translate         → 图中保留英文不变\n"
                "  translate_with_annotation → 图中显示「中文（英文）」双语格式\n\n"
                "输出 JSON 数组，每项格式：\n"
                '{"original": "原文", "translated": "中文翻译", '
                '"action": "translate|never_translate|translate_with_annotation"}\n\n'
                "注意：never_translate 时 translated 填原文即可。\n\n"
                "示例：\n"
                '[{"original": "Accuracy", "translated": "准确率", "action": "translate"}, '
                '{"original": "BERT", "translated": "BERT", "action": "never_translate"}]'
            ),
        },
    ]

    prompt_text = "\n".join(f"[{m['role']}] {m['content']}" for m in messages)
    logger.warning(
        "[ImageTranslation/Translate] → %s\n%s\n%s\n%s",
        settings.DEEPSEEK_MODEL, "─" * 60, prompt_text, "─" * 60,
    )

    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.DEEPSEEK_MODEL,
                    "messages": messages,
                    "temperature": 0.1,
                    "max_tokens": 1024,
                },
            )
            resp.raise_for_status()

        content = resp.json()["choices"][0]["message"]["content"]
        logger.warning(
            "[ImageTranslation/Translate] ← 响应\n%s\n%s\n%s",
            "─" * 60, content, "─" * 60,
        )

        m = re.search(r"\[[\s\S]*\]", content)
        if not m:
            return {}
        items = json.loads(m.group())

        return {
            item["original"]: {
                "translated": item.get("translated", item["original"]),
                "action": item.get("action", "translate"),
            }
            for item in items
            if isinstance(item, dict) and item.get("original")
        }

    except Exception as e:
        logger.warning("[ImageTranslation] DeepSeek 翻译失败: %s", e)
        return {}


# ── Step 3：qwen-image-2.0-pro 图片编辑 ────────────────────────────────────────

def _build_instruction(translation_map: dict[str, dict], domain: str = "学术") -> str:
    """将已过滤的翻译映射表（仅含需修改项）转为 qwen-image-2.0-pro 可理解的自然语言指令"""
    parts = []
    for original, info in translation_map.items():
        translated = info["translated"]
        if info["action"] == "translate_with_annotation":
            display = f"{translated}（{original}）"
            parts.append(f'将"{original}"替换为"{display}"')
        else:
            parts.append(f'将"{original}"替换为"{translated}"')

    if not parts:
        return ""

    rules = "\n".join(f"  - {p}" for p in parts)
    return (
        f"这是一篇{domain}领域学术论文中的图表，请执行以下文字翻译操作：\n\n"
        "【翻译清单】\n"
        f"{rules}\n\n"
        "【严格约束，必须全部遵守】\n"
        "1. 图中所有非文字内容——包括坐标轴线条、数据点、折线、柱形、散点、图例色块、"
        "背景、边框、标注箭头等图形元素——的位置、大小、颜色、形状绝对不能改变；\n"
        "2. 每段翻译文字必须放置在原文字完全相同的位置，不得发生任何偏移；\n"
        "3. 特别注意：图中可能存在竖排文字（从上到下或从下到上）或斜排文字（沿坐标轴方向倾斜），"
        "替换时必须保持与原文字完全相同的排列方向和角度，不得将竖排/斜排改为横排；\n"
        "4. 若中文翻译比原文更长导致空间不足，可以适当缩小字号（缩小幅度不超过原字号的 30%），"
        "保持文字清晰可读、与图表整体和谐美观，禁止文字溢出或遮挡图形元素；\n"
        "5. 翻译文字的颜色、粗细、对齐方式与原文保持一致；\n"
        "6. 纯数字、数学公式、坐标刻度值一律保留不变；\n"
        "7. 图表整体构图与数据可视化内容与原图完全一致，仅上述翻译清单中的文字发生变化。"
    )


def _edit_image(
    image_url: str,
    image_b64: str,
    media_type: str,
    translation_map: dict[str, dict],
    paper_id: str,
    domain: str = "学术",
) -> str:
    """调用 qwen-image-2.0-pro 编辑图片，将翻译后图片保存到本地并返回 URL"""
    if not settings.QWEN_IMAGE_MODEL:
        logger.warning("[ImageTranslation] QWEN_IMAGE_MODEL 未配置，跳过图片编辑")
        return image_url

    instruction = _build_instruction(translation_map, domain=domain)
    if not instruction:
        return image_url

    logger.warning(
        "[ImageTranslation/Edit] → %s\n%s\n%s\n%s",
        settings.QWEN_IMAGE_MODEL, "─" * 60, instruction, "─" * 60,
    )

    parameters: dict = {"n": 1, "watermark": False, "prompt_extend": False}

    try:
        resp = _http_post_with_retry(
            url=f"{settings.QWEN_DASHSCOPE_BASE_URL}/services/aigc/multimodal-generation/generation",
            headers={
                "Authorization": f"Bearer {settings.QWEN_API_KEY}",
                "Content-Type": "application/json",
            },
            body={
                "model": settings.QWEN_IMAGE_MODEL,
                "input": {
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"image": f"data:{media_type};base64,{image_b64}"},
                            {"text": instruction},
                        ],
                    }]
                },
                "parameters": parameters,
            },
            timeout=120,
        )

        data = resp.json()
        logger.warning(
            "[ImageTranslation/Edit] ← 响应\n%s\n%s\n%s",
            "─" * 60, str(data)[:800], "─" * 60,
        )

        # 提取生成图片的 URL
        choices = data.get("output", {}).get("choices", [])
        if not choices:
            logger.warning("[ImageTranslation] 响应中无 choices")
            return image_url

        content_items = choices[0].get("message", {}).get("content", [])
        image_out_url = next(
            (item["image"] for item in content_items if "image" in item),
            None,
        )
        if not image_out_url:
            logger.warning("[ImageTranslation] 响应中无图片 URL")
            return image_url

        # 下载并保存到本地
        with httpx.Client(timeout=60) as client:
            img_resp = client.get(image_out_url)
            img_resp.raise_for_status()
            edited_bytes = img_resp.content

        # 构造新的存储 key
        orig_filename = image_url.rstrip("/").split("/")[-1]
        base_name, _, ext = orig_filename.rpartition(".")
        new_filename = f"{base_name}_translated.{ext}" if ext else f"{orig_filename}_translated"

        new_key = (
            f"papers/{paper_id}/images/{new_filename}"
            if paper_id
            else f"tmp_images/{new_filename}"
        )
        local_storage.put_object(new_key, edited_bytes, content_type=media_type)
        new_url = local_storage.get_url(new_key)
        logger.info("[ImageTranslation] 翻译完成 → %s", new_url)
        return new_url

    except Exception as e:
        logger.warning("[ImageTranslation] 图片编辑失败，原图通过: %s", e, exc_info=True)
        return image_url
