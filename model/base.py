"""Abstract base class for all LLM backends."""

from abc import ABC, abstractmethod

from openai.types.chat import ChatCompletion

from .config import ModelConfig


class BaseLLM(ABC):
    config: ModelConfig   # 모든 백엔드가 연결 정보(config)를 노출

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs,
    ) -> ChatCompletion:
        """Send messages and return an OpenAI-compatible ChatCompletion."""
        ...
