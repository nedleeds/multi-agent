"""Abstract base class for all LLM backends."""

from abc import ABC, abstractmethod

from openai.types.chat import ChatCompletion


class BaseLLM(ABC):
    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs,
    ) -> ChatCompletion:
        """Send messages and return an OpenAI-compatible ChatCompletion."""
        ...
