"""Main orchestrator LLM: OpenAI API."""

from collections.abc import Callable

from openai import OpenAI
from openai.types.chat import ChatCompletion

from ._stream import accumulate_stream
from .base import BaseLLM
from .config import ModelConfig, openai_config


class OpenAIModel(BaseLLM):
    def __init__(self, config: ModelConfig | None = None):
        self.config = config or openai_config()
        # base_url 을 명시 — 회사 로컬 gpt-oss 서버 등 OpenAI-compatible 엔드포인트 지원
        self._client = OpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
        )

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_content_delta: Callable[[str], None] | None = None,
        **kwargs,
    ) -> ChatCompletion:
        params: dict = {
            "model": self.config.model_id,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            **kwargs,
        }
        if tools:
            params["tools"] = tools

        if on_content_delta is None:
            return self._client.chat.completions.create(**params)

        # 스트리밍 경로 — include_usage 로 최종 청크에 usage 포함
        params["stream"] = True
        params["stream_options"] = {"include_usage": True}
        stream = self._client.chat.completions.create(**params)
        return accumulate_stream(stream, on_delta=on_content_delta)
