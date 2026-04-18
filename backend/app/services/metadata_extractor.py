"""
MetadataExtractor

将 PDF 第一页渲染为图像，发给 Qwen-VL-Max 提取论文元数据，
用于在上传表单中自动预填标题、期刊、年份、语言等信息。
"""

import base64
import json
import logging
import re

import fitz  # PyMuPDF
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 期刊分区规则（与前端 TranslateUpload.vue 保持一致）
_JOURNAL_GROUPS = {
    "中科院分区（最多选一）": ["中科院一区", "中科院二区", "中科院三区", "中科院四区"],
    "JCR分区（最多选一）":    ["Q1", "Q2", "Q3", "Q4"],
    "检索收录（可多选）":      ["SCI", "SSCI", "EI", "Scopus", "CSCD", "北大核心", "CSSCI"],
}

# 会议分区规则
_CONFERENCE_GROUPS = {
    "CCF分类（最多选一）":    ["CCF-A", "CCF-B", "CCF-C", "非CCF"],
    "CORE分类（最多选一）":   ["CORE A*", "CORE A", "CORE B", "CORE C"],
    "检索收录（可多选）":      ["EI", "SCI", "SSCI", "CPCI", "Scopus"],
}


def _build_prompt(user_domain: str | None, paper_type: str = "journal") -> str:
    domain_hint = f" The paper belongs to the domain of [{user_domain}]." if user_domain else ""

    if paper_type == "conference":
        groups = _CONFERENCE_GROUPS
        venue_field = "conference"
        type_hint = "conference paper"
    else:
        groups = _JOURNAL_GROUPS
        venue_field = "journal"
        type_hint = "journal article"

    groups_desc = "\n".join(
        f"  - {group_name}: {' / '.join(options)}"
        for group_name, options in groups.items()
    )

    return (
        f"This is the first page of a {type_hint}.{domain_hint} "
        "Carefully read the page and extract the following fields. "
        "Return ONLY a JSON object with no extra text:\n"
        "{\n"
        '  "title": "original-language title of the paper (do not translate)",\n'
        '  "title_zh": "Chinese title if one appears on this page, otherwise empty string",\n'
        f'  "{venue_field}": "name of the {venue_field} (original language; empty string if not found)",\n'
        '  "year": "4-digit publication year as a string (empty string if not found)",\n'
        '  "source_language": "ISO 639-1 code of the paper\'s language; '
        'must be one of: en/fr/de/ja/ko/es/ru/ar/pt/it; default to en if uncertain",\n'
        '  "division": "ranking/indexing tags separated by Chinese enumeration comma \'、\' '
        "(empty string if none). Rules:\n"
        f"{groups_desc}\n"
        "  - 无分区/未分类 (mutually exclusive with all other tags; use alone)\n"
        '  - Tags from different groups may be combined; within one group choose at most one",\n'
        '  "doi": "DOI (e.g. 10.1145/3386569.3392506; empty string if not found)",\n'
        '  "corresponding_author_email": "corresponding author email (empty string if not found)"\n'
        "}\n"
        'Example division for a journal: "中科院一区、Q1、SCI、EI"\n'
        'Example division for a conference: "CCF-A、EI、Scopus"'
    )


class MetadataExtractor:
    def extract(
        self,
        pdf_bytes: bytes,
        user_domain: str | None = None,
        paper_type: str = "journal",
    ) -> dict:
        """
        提取 PDF 第一页元数据。
        失败时静默返回空结构，不抛出异常（表单允许用户手动填写）。
        返回字段中 division_tags 为 list[str]，供前端直接赋值给 el-select multiple。
        """
        empty = {
            "title": "", "title_zh": "", "journal": "",
            "year": "", "source_language": "en",
            "division_tags": [],
            "doi": "", "corresponding_author_email": "",
            "paper_type": paper_type,
        }

        if not settings.QWEN_API_KEY:
            logger.warning("QWEN_API_KEY 未配置，跳过元数据提取")
            return empty

        try:
            image_b64 = self._render_first_page(pdf_bytes)
            return self._call_qwen_vl(image_b64, user_domain, paper_type)
        except Exception as e:
            logger.warning(f"MetadataExtractor 失败，将由用户手动填写: {e}")
            return empty

    def _render_first_page(self, pdf_bytes: bytes) -> str:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[0]
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        doc.close()
        return base64.b64encode(img_bytes).decode()

    def _call_qwen_vl(self, image_b64: str, user_domain: str | None, paper_type: str = "journal") -> dict:
        prompt = _build_prompt(user_domain, paper_type)
        logger.warning(
            "[MetadataExtractor] → %s\n%s\n%s",
            settings.QWEN_VL_MODEL,
            "-" * 60,
            prompt,
        )
        payload = {
            "model": settings.QWEN_VL_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }

        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{settings.QWEN_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.QWEN_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()

        content = resp.json()["choices"][0]["message"]["content"]

        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            raise ValueError(f"无法从模型响应中解析 JSON: {content[:200]}")

        data = json.loads(match.group())
        str_fields = ["title", "title_zh", "journal", "year", "source_language",
                      "doi", "corresponding_author_email"]
        result = {k: str(data.get(k, "") or "") for k in str_fields}
        result["paper_type"] = paper_type

        # 校验 source_language
        from app.constants import SUPPORTED_LANGUAGES
        if result["source_language"] not in SUPPORTED_LANGUAGES:
            result["source_language"] = "en"

        # 校验 division：返回为 list[str]，每个 tag 必须在合法选项中
        groups = _CONFERENCE_GROUPS if paper_type == "conference" else _JOURNAL_GROUPS
        valid = {"无分区/未分类"}
        for opts in groups.values():
            valid.update(opts)

        raw_division = str(data.get("division", "") or "")
        tags = [t.strip() for t in raw_division.replace(",", "、").split("、") if t.strip()]
        result["division_tags"] = [t for t in tags if t in valid]

        return result


metadata_extractor = MetadataExtractor()
