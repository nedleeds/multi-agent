"""Abstract base class for all LLM backends."""

from abc import ABC, abstractmethod
from collections.abc import Callable

from openai.types.chat import ChatCompletion

from .config import ModelConfig


class BaseLLM(ABC):
    config: ModelConfig   # 모든 백엔드가 연결 정보(config)를 노출

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_content_delta: Callable[[str], None] | None = None,
        **kwargs,
    ) -> ChatCompletion:
        """Send messages and return an OpenAI-compatible ChatCompletion.

        `on_content_delta` 가 주어지면 `stream=True` 모드로 전환되어,
        content 토큰이 도착할 때마다 콜백에 raw 텍스트를 전달한다.
        최종 반환 객체는 기존 non-stream 코드와 동일한 shape (duck-typed).
        """
        ...
