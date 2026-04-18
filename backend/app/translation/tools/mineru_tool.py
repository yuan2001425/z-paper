"""MinerU PDF 解析工具

正确流程（Precision Extract API）：
1. POST /api/v4/file-urls/batch  → 获取 batch_id + 预签名上传 URL
2. PUT pdf_bytes → 预签名 URL    → 上传文件，MinerU 自动提交任务
3. GET /api/v4/extract-results/batch/{batch_id}  → 轮询直到 state=done
4. 下载 full_zip_url 中的 zip，解压：
   - 读取 *.md → 论文 Markdown 全文
   - 读取 images/* → 存入 local_storage，更新 Markdown 内的图片路径

返回：{"markdown": "修正过图片路径的 Markdown 全文"}
"""

import io
import re
import time
import zipfile
import logging

import httpx
from app.config import settings
from app.storage.local_storage import local_storage

logger = logging.getLogger(__name__)

_API_BASE = "https://mineru.net/api/v4"


class MineruParseTool:
    def __init__(self, pdf_bytes: bytes, filename: str = "document.pdf", paper_id: str = ""):
        self.pdf_bytes = pdf_bytes
        self.filename = filename
        self.paper_id = paper_id

    def _call_mineru(self) -> dict:
        headers = {
            "Authorization": f"Bearer {settings.MINERU_API_KEY}",
            "Content-Type": "application/json",
        }

        # ── Step 1：请求预签名上传地址 ─────────────────────────────────────
        logger.warning("[MinerU] 请求预签名上传地址，文件：%s (%d bytes)",
                       self.filename, len(self.pdf_bytes))
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{_API_BASE}/file-urls/batch",
                headers=headers,
                json={
                    "files": [{"name": self.filename}],
                    "model_version": "pipeline",
                    "is_ocr": True,
                    "enable_formula": True,
                    "enable_table": False,
                },
            )
            resp.raise_for_status()
            result = resp.json()

        batch_id = result["data"]["batch_id"]
        upload_url = result["data"]["file_urls"][0]
        logger.warning("[MinerU] batch_id=%s，开始上传文件", batch_id)

        # ── Step 2：PUT 文件到预签名地址 ───────────────────────────────────
        with httpx.Client(timeout=300.0) as client:
            resp = client.put(upload_url, content=self.pdf_bytes)
            resp.raise_for_status()
        logger.warning("[MinerU] 文件上传完成，开始轮询结果")

        # ── Step 3：轮询直到完成 ────────────────────────────────────────────
        poll_headers = {"Authorization": f"Bearer {settings.MINERU_API_KEY}"}
        for attempt in range(120):  # 最多等 10 分钟
            time.sleep(5)
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(
                    f"{_API_BASE}/extract-results/batch/{batch_id}",
                    headers=poll_headers,
                )
                resp.raise_for_status()
                data = resp.json()

            results = data.get("data", {}).get("extract_result", [])
            if not results:
                continue

            item = results[0]
            state = item.get("state", "")
            progress = item.get("extract_progress", "")
            logger.warning("[MinerU] 轮询 #%d state=%s progress=%s", attempt + 1, state, progress)

            if state == "done":
                full_zip_url = item.get("full_zip_url", "")
                if not full_zip_url:
                    raise RuntimeError("MinerU 返回 done 但 full_zip_url 为空")
                return self._download_and_parse(full_zip_url)
            elif state == "failed":
                raise RuntimeError(f"MinerU 解析失败: {item.get('err_msg', '未知错误')}")

        raise TimeoutError("MinerU API 超时（10分钟）")

    def _download_and_parse(self, zip_url: str) -> dict:
        """下载 zip，提取 .md 文件与图片，返回 {"markdown": str}"""
        logger.warning("[MinerU] 下载结果 zip：%s", zip_url)
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            resp = client.get(zip_url)
            resp.raise_for_status()

        markdown_text = ""
        image_path_map: dict[str, str] = {}  # 原始相对路径 → /uploads/... URL

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            namelist = zf.namelist()
            logger.warning("[MinerU] zip 内容：%s", namelist)

            # ── 提取图片，存入 local_storage ─────────────────────────────
            for name in namelist:
                lower = name.lower()
                if any(lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp")):
                    img_bytes = zf.read(name)
                    # 取文件名部分，存到 papers/{paper_id}/images/
                    img_filename = name.split("/")[-1]
                    storage_key = f"papers/{self.paper_id}/images/{img_filename}" if self.paper_id else f"tmp_images/{img_filename}"
                    local_storage.put_object(storage_key, img_bytes)
                    url = local_storage.get_url(storage_key)
                    # 记录多种可能的原始路径格式
                    image_path_map[name] = url               # e.g. "xxx/images/fig1.png"
                    image_path_map[img_filename] = url       # e.g. "fig1.png"
                    image_path_map[f"images/{img_filename}"] = url  # e.g. "images/fig1.png"

            # ── 提取 Markdown 文件 ────────────────────────────────────────
            md_file = next((n for n in namelist if n.endswith(".md")), None)
            if md_file:
                markdown_text = zf.read(md_file).decode("utf-8", errors="replace")
                logger.warning("[MinerU] 读取 Markdown 文件：%s（%d chars）", md_file, len(markdown_text))
            else:
                # 兜底：从 content_list.json 重建最简 Markdown
                logger.warning("[MinerU] 未找到 .md 文件，从 content_list.json 重建")
                markdown_text = self._rebuild_markdown_from_content_list(zf, namelist)

        # ── 替换 Markdown 内的图片路径 ────────────────────────────────────
        if image_path_map:
            markdown_text = self._replace_image_paths(markdown_text, image_path_map)

        return {"markdown": markdown_text}

    def _replace_image_paths(self, markdown: str, path_map: dict) -> str:
        """将 Markdown 中的相对图片路径替换为 /uploads/... 绝对 URL"""
        def replacer(m):
            alt = m.group(1)
            path = m.group(2)
            # 尝试各种路径格式
            for key, url in path_map.items():
                if path == key or path.endswith("/" + key) or key.endswith("/" + path.split("/")[-1]):
                    return f"![{alt}]({url})"
            return m.group(0)  # 未匹配则保留原样

        return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replacer, markdown)

    def _rebuild_markdown_from_content_list(self, zf: zipfile.ZipFile, namelist: list) -> str:
        """兜底：从 content_list.json 重建基础 Markdown"""
        import json
        content_list_file = next((n for n in namelist if n.endswith("_content_list.json")), None)
        if not content_list_file:
            return ""
        raw = json.loads(zf.read(content_list_file).decode("utf-8"))
        if isinstance(raw, list):
            items = raw
        else:
            items = raw.get("content_list", [])

        lines = []
        for block in items:
            btype = block.get("type", "text")
            text = block.get("text", "")
            if btype == "title":
                level = block.get("level", 2)
                lines.append("#" * level + " " + text)
            elif btype == "text":
                lines.append(text)
            elif btype == "equation":
                lines.append(f"$${text}$$")
            elif btype == "image":
                caption = block.get("img_caption", "")
                path = block.get("img_path", "")
                lines.append(f"![{caption}]({path})")
            elif btype == "table":
                caption = block.get("table_caption", "")
                path = block.get("img_path", "")
                lines.append(f"![{caption}]({path})")
        return "\n\n".join(lines)
