"""Abstract LLM provider interface.

All LLM providers must implement this interface, enabling easy swapping
between Anthropic, vLLM, OpenAI-compatible endpoints, etc.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Awaitable


class LLMProvider(ABC):
    """Abstract interface for LLM providers."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
    ) -> tuple[str, int]:
        """Generate a completion.

        Returns (text, tokens_used).
        """
        ...

    @abstractmethod
    async def generate_stream(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str, int]:
        """Generate a completion with streaming.

        Calls on_token(chunk) for each text chunk.
        Returns (full_text, tokens_used).
        """
        ...

    @abstractmethod
    async def classify(
        self,
        prompt: str,
        max_tokens: int = 10,
    ) -> tuple[str, int]:
        """Generate a short classification response.

        Returns (classification_text, tokens_used).
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name for logging."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier."""
        ...
