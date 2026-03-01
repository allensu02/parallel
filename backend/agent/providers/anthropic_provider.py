"""Anthropic Claude provider — wraps the current Claude implementation.

This extracts the Anthropic-specific logic into the provider interface
so it can be swapped with vLLM or other providers.
"""

from __future__ import annotations

from typing import Callable, Awaitable

import anthropic

from backend.agent.providers.base import LLMProvider
from backend.config import ANTHROPIC_API_KEY


class AnthropicProvider(LLMProvider):
    """LLM provider backed by Anthropic's Claude API."""

    def __init__(
        self,
        api_key: str = "",
        model: str = "claude-sonnet-4-20250514",
    ):
        self._api_key = api_key or ANTHROPIC_API_KEY
        self._model = model
        self._client: anthropic.Anthropic | None = None

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not self._api_key:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    @property
    def name(self) -> str:
        return "anthropic"

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
        resp = client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        tokens = (resp.usage.input_tokens or 0) + (resp.usage.output_tokens or 0)
        return text, tokens

    async def generate_stream(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str, int]:
        client = self._get_client()
        parts: list[str] = []

        with client.messages.stream(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                parts.append(text)
                if on_token:
                    await on_token(text)

            final = stream.get_final_message()
            tokens = (final.usage.input_tokens or 0) + (final.usage.output_tokens or 0)

        return "".join(parts).strip(), tokens

    async def classify(
        self,
        prompt: str,
        max_tokens: int = 10,
    ) -> tuple[str, int]:
        return await self.generate(prompt, max_tokens=max_tokens, temperature=0.0)
