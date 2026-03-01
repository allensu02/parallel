"""vLLM / OpenAI-compatible provider — for self-hosted models.

Targets any OpenAI-compatible API endpoint (vLLM, TGI, etc.)
for use with open-source models like Kimi K2.5, Qwen2.5, etc.

NOTE: This provider is a scaffold for future deployment.
Configure via environment variables:
  - LLM_BASE_URL: Base URL of the vLLM/OpenAI-compatible endpoint
  - LLM_MODEL: Model name to use
  - LLM_API_KEY: Optional API key for the endpoint
"""

from __future__ import annotations

import os
from typing import Callable, Awaitable

from backend.agent.providers.base import LLMProvider


class VLLMProvider(LLMProvider):
    """LLM provider backed by a vLLM or OpenAI-compatible endpoint."""

    def __init__(
        self,
        base_url: str = "",
        model: str = "",
        api_key: str = "",
    ):
        self._base_url = base_url or os.getenv("LLM_BASE_URL", "http://localhost:8080/v1")
        self._model = model or os.getenv("LLM_MODEL", "kimi-k2.5-72b")
        self._api_key = api_key or os.getenv("LLM_API_KEY", "")
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    base_url=self._base_url,
                    api_key=self._api_key or "dummy",
                )
            except ImportError:
                raise RuntimeError(
                    "openai package required for VLLMProvider. "
                    "Install with: pip install openai"
                )
        return self._client

    @property
    def name(self) -> str:
        return "vllm"

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
    ) -> tuple[str, int]:
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content or ""
        tokens = (resp.usage.total_tokens if resp.usage else 0)
        return text.strip(), tokens

    async def generate_stream(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str, int]:
        client = self._get_client()
        parts: list[str] = []

        stream = client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )

        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                parts.append(delta.content)
                if on_token:
                    await on_token(delta.content)

        full_text = "".join(parts).strip()
        # Token count not always available in streaming; estimate
        estimated_tokens = len(full_text.split()) * 2  # Rough estimate
        return full_text, estimated_tokens

    async def classify(
        self,
        prompt: str,
        max_tokens: int = 10,
    ) -> tuple[str, int]:
        return await self.generate(prompt, max_tokens=max_tokens, temperature=0.0)
