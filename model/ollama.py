"""Main orchestrator LLM: local ollama server via OpenAI-compatible API."""

from collections.abc import Callable

from openai import OpenAI
from openai.types.chat import ChatCompletion

from ._stream import accumulate_stream
from .base import BaseLLM
from .config import ModelConfig, ollama_config


class OllamaModel(BaseLLM):
    def __init__(self, config: ModelConfig | None = None):
        self.config = config or ollama_config()
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

        params["stream"] = True
        params["stream_options"] = {"include_usage": True}
        stream = self._client.chat.completions.create(**params)
        return accumulate_stream(stream, on_delta=on_content_delta)
