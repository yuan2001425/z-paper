"""
标题翻译器：用 DeepSeek 将外文论文标题翻译为中文。
在自动提取元数据后调用，参考用户个人术语表中出现在标题内的术语。
"""

import logging
import re
import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _find_relevant_terms(title: str, glossary_terms: list) -> tuple[list, list]:
    """
    从术语表中找出标题里出现的词条，按单词边界匹配（大小写不敏感）。
    返回 (translate_terms, never_translate_terms)。
    """
    translate_terms = []
    never_translate_terms = []

    for t in glossary_terms:
        pattern = r'(?<![A-Za-z0-9_])' + re.escape(t.foreign_term) + r'(?![A-Za-z0-9_])'
        if re.search(pattern, title, flags=re.IGNORECASE):
            if t.status == "translate" and t.zh_term:
                translate_terms.append(t)
            elif t.status == "never_translate":
                never_translate_terms.append(t)

    return translate_terms, never_translate_terms


def translate_title(
    title: str,
    source_language: str,
    glossary_terms: list,       # list of UserGlossary ORM objects
    domain: str | None = None,
) -> str:
    """
    翻译外文论文标题。
    - glossary_terms: 用户术语表（已过滤与本次 source_language 匹配的条目）
    - 返回中文标题字符串，失败时返回空字符串（由用户手动填写）
    """
    if not title.strip() or not settings.DEEPSEEK_API_KEY:
        return ""

    translate_terms, never_translate_terms = _find_relevant_terms(title, glossary_terms)

    constraint_blocks = []
    if translate_terms:
        lines = "\n".join(f"  {t.foreign_term} → {t.zh_term}" for t in translate_terms)
        constraint_blocks.append(f"【必须按以下对照翻译】\n{lines}")
    if never_translate_terms:
        lines = "\n".join(f"  {t.foreign_term}" for t in never_translate_terms)
        constraint_blocks.append(f"【以下内容必须保留原文，不得翻译（无论大小写）】\n{lines}")

    glossary_block = ("\n\n" + "\n\n".join(constraint_blocks)) if constraint_blocks else ""

    domain_hint = f"（{domain}领域）" if domain else ""

    system_msg = (
        f"你是一位专业学术翻译{domain_hint}，请将外文论文标题翻译为简洁、规范的中文标题。"
        "只输出中文翻译结果，不加引号、不加解释。"
    )
    user_msg = f"请翻译以下论文标题：\n{title}{glossary_block}"

    logger.warning(
        "[TitleTranslator] → %s\nsystem: %s\nuser: %s",
        settings.DEEPSEEK_MODEL,
        system_msg,
        user_msg,
    )

    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user",   "content": user_msg},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 200,
                },
            )
            resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"[TitleTranslator] 翻译失败，将由用户手动填写: {e}")
        return ""
