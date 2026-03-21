"""LLM 客户端封装：支持 DeepSeek / Kimi 多 provider 切换 + token 计数 + 重试"""

# 切换方式：在 config.yaml 中修改 llm.provider = "deepseek" 或 "kimi"

import json
import time
from typing import Any

from openai import OpenAI

from app.config import get_config
from app.logger import get_logger

logger = get_logger(__name__)


class LLMClient:
    """LLM 客户端（OpenAI 兼容接口，支持多 provider）

    通过 config.yaml 中的 llm.provider 切换模型：
      - provider: "deepseek"  → deepseek-chat
      - provider: "kimi"       → kimi-k2.5
      - provider: "minimax"   → MiniMax-M2.7-highspeed
    """

    def __init__(self, cfg: dict | None = None):
        if cfg is None:
            cfg = get_config()["llm"]

        self.model = cfg.get("model", "deepseek-chat")
        self.temperature = cfg.get("temperature", 0.1)
        self.max_tokens = cfg.get("max_tokens", 4096)
        self.timeout = cfg.get("timeout", 120)
        self.max_retries = cfg.get("max_retries", 3)
        self.retry_delay = cfg.get("retry_delay", 2)

        self.client = OpenAI(
            api_key=cfg.get("api_key", ""),
            base_url=cfg.get("base_url", "https://api.deepseek.com"),
        )

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """
        发送对话请求并返回结果。

        返回:
            {
                "content": str,        # 模型回复文本
                "input_tokens": int,   # 输入 token 数
                "output_tokens": int,  # 输出 token 数
                "model": str,
            }
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature or self.temperature,
                    max_tokens=max_tokens or self.max_tokens,
                    timeout=self.timeout,
                )

                usage = response.usage
                result = {
                    "content": response.choices[0].message.content or "",
                    "input_tokens": usage.prompt_tokens if usage else 0,
                    "output_tokens": usage.completion_tokens if usage else 0,
                    "model": response.model or self.model,
                }

                logger.debug(
                    "LLM 调用成功: model=%s, in=%d, out=%d tokens",
                    result["model"],
                    result["input_tokens"],
                    result["output_tokens"],
                )
                return result

            except Exception as e:
                last_error = e
                logger.warning(
                    "LLM 调用失败 (第 %d/%d 次): %s",
                    attempt,
                    self.max_retries,
                    str(e),
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * attempt)

        raise RuntimeError(
            f"LLM 调用在 {self.max_retries} 次重试后仍然失败: {last_error}"
        )

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        **kwargs,
    ) -> dict[str, Any]:
        """
        发送请求并解析 JSON 响应。

        返回:
            {
                "data": dict | list,   # 解析后的 JSON
                "input_tokens": int,
                "output_tokens": int,
                "model": str,
            }
        """
        result = self.chat(system_prompt, user_prompt, **kwargs)
        content = result["content"]

        # 尝试提取 JSON（处理 Markdown 代码块包裹的情况）
        parsed = self._extract_json(content)
        if parsed is None:
            logger.error("无法解析 JSON 响应: %s", content[:500])
            raise ValueError(f"LLM 返回内容无法解析为 JSON: {content[:200]}")

        return {
            "data": parsed,
            "input_tokens": result["input_tokens"],
            "output_tokens": result["output_tokens"],
            "model": result["model"],
        }

    @staticmethod
    def _extract_json(text: str) -> dict | list | None:
        """从文本中提取 JSON，处理 Markdown 包裹"""
        text = text.strip()

        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试去除 Markdown 代码块
        import re

        json_block = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if json_block:
            try:
                return json.loads(json_block.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 尝试找到第一个 { 或 [
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass

        return None


# 模块级单例
_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """获取全局 LLM 客户端（懒加载）"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
