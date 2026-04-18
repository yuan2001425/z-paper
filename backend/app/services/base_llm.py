import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [2, 4, 8]


class BaseLLMCaller(ABC):
    """
    LLM 调用基类，封装 DeepSeek / Qwen API 调用、重试逻辑和日志。
    子类只需实现 build_prompt() 和 parse_response()。
    """

    # 子类可以覆盖这些属性
    api_base_url: str = settings.DEEPSEEK_BASE_URL
    api_key: str = settings.DEEPSEEK_API_KEY
    model: str = settings.DEEPSEEK_MODEL
    default_max_tokens: int = 4096

    @abstractmethod
    def build_prompt(self, input_data: dict) -> tuple[str, str]:
        """返回 (system_prompt, user_message)"""

    @abstractmethod
    def parse_response(self, raw_response: str) -> Any:
        """解析 LLM 返回的原始文本为结构化输出"""

    def run(self, input_data: dict, max_tokens: int = None) -> Any:
        """同步调用 LLM（供 Celery Worker 使用）"""
        if max_tokens is None:
            max_tokens = self.default_max_tokens

        system_prompt, user_message = self.build_prompt(input_data)
        caller_name = self.__class__.__name__

        for attempt in range(MAX_RETRIES):
            try:
                logger.info(f"[{caller_name}] attempt={attempt + 1}")
                raw = self._call_api(system_prompt, user_message, max_tokens)
                result = self.parse_response(raw)
                logger.info(f"[{caller_name}] success")
                return result
            except (httpx.HTTPStatusError, httpx.TimeoutException) as e:
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.warning(f"[{caller_name}] error={e}, retry in {delay}s")
                    time.sleep(delay)
                else:
                    logger.error(f"[{caller_name}] failed after {MAX_RETRIES} attempts: {e}")
                    raise
            except json.JSONDecodeError as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"[{caller_name}] JSON parse error, retry: {e}")
                    time.sleep(RETRY_DELAYS[attempt])
                else:
                    raise

    def _call_api(self, system_prompt: str, user_message: str, max_tokens: int) -> str:
        """调用 OpenAI 兼容格式的 API（DeepSeek / Qwen 均支持）"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                f"{self.api_base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    @staticmethod
    def extract_json(text: str) -> str:
        """从 LLM 输出中提取 JSON 块（兼容 ```json ... ``` 格式）"""
        text = text.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0].strip()
        return text
