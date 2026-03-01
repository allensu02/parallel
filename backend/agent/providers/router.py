"""Model router — routes requests to appropriate provider based on task type.

Configuration via environment:
  - LLM_PROVIDER: "anthropic" (default), "vllm", or "openai_compat"
  - LLM_CLASSIFY_PROVIDER: Override provider for classification tasks
  - LLM_DRAFT_PROVIDER: Override provider for draft generation tasks
  - LLM_FALLBACK_PROVIDER: Fallback provider if primary fails

Usage:
    router = ModelRouter()
    text, tokens = await router.classify(prompt)
    text, tokens = await router.generate(prompt)
    text, tokens = await router.generate_stream(prompt, on_token=callback)
"""

from __future__ import annotations

import os
from typing import Callable, Awaitable

from backend.agent.providers.base import LLMProvider
from backend.agent.providers.anthropic_provider import AnthropicProvider


class ModelRouter:
    """Routes LLM requests to the appropriate provider based on task type."""

    def __init__(self):
        self._providers: dict[str, LLMProvider] = {}
        self._default_provider_name = os.getenv("LLM_PROVIDER", "anthropic")
        self._classify_provider_name = os.getenv("LLM_CLASSIFY_PROVIDER", "")
        self._draft_provider_name = os.getenv("LLM_DRAFT_PROVIDER", "")
        self._fallback_provider_name = os.getenv("LLM_FALLBACK_PROVIDER", "anthropic")

    def _get_provider(self, name: str) -> LLMProvider:
        """Get or create a provider by name."""
        if name not in self._providers:
            if name == "anthropic":
                self._providers[name] = AnthropicProvider()
            elif name in ("vllm", "openai_compat"):
                from backend.agent.providers.vllm_provider import VLLMProvider
                self._providers[name] = VLLMProvider()
            else:
                raise ValueError(f"Unknown LLM provider: {name}")
        return self._providers[name]

    @property
    def default_provider(self) -> LLMProvider:
        return self._get_provider(self._default_provider_name)

    @property
    def classify_provider(self) -> LLMProvider:
        name = self._classify_provider_name or self._default_provider_name
        return self._get_provider(name)

    @property
    def draft_provider(self) -> LLMProvider:
        name = self._draft_provider_name or self._default_provider_name
        return self._get_provider(name)

    @property
    def fallback_provider(self) -> LLMProvider:
        return self._get_provider(self._fallback_provider_name)

    async def classify(
        self,
        prompt: str,
        max_tokens: int = 10,
    ) -> tuple[str, int]:
        """Route classification request."""
        try:
            return await self.classify_provider.classify(prompt, max_tokens=max_tokens)
        except Exception as e:
            print(f"[Router] Classify failed with {self.classify_provider.name}, trying fallback: {e}")
            return await self.fallback_provider.classify(prompt, max_tokens=max_tokens)

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
    ) -> tuple[str, int]:
        """Route generation request."""
        try:
            return await self.draft_provider.generate(
                prompt, max_tokens=max_tokens, temperature=temperature
            )
        except Exception as e:
            print(f"[Router] Generate failed with {self.draft_provider.name}, trying fallback: {e}")
            return await self.fallback_provider.generate(
                prompt, max_tokens=max_tokens, temperature=temperature
            )

    async def generate_stream(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str, int]:
        """Route streaming generation request."""
        try:
            return await self.draft_provider.generate_stream(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                on_token=on_token,
            )
        except Exception as e:
            print(f"[Router] Stream failed with {self.draft_provider.name}, trying fallback: {e}")
            return await self.fallback_provider.generate_stream(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                on_token=on_token,
            )


# Singleton instance
_router: ModelRouter | None = None


def get_router() -> ModelRouter:
    """Get the global model router instance."""
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router
