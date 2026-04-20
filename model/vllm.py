"""Subagent LLM: local vllm server via OpenAI-compatible API.

Qwen3 계열 모델은 기본적으로 thinking mode(<think> 토큰)가 켜질 수 있어
서브에이전트에서 불필요한 토큰 소모가 생긴다.
VLLM_DISABLE_THINKING=true (기본값)로 설정하면 extra_body로 억제한다.
"""

import os
from collections.abc import Callable

from openai import OpenAI
from openai.types.chat import ChatCompletion

from ._stream import accumulate_stream
from .base import BaseLLM
from .config import ModelConfig, vllm_config


class VLLMModel(BaseLLM):
    def __init__(self, config: ModelConfig | None = None):
        self.config = config or vllm_config()
        self._client = OpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
        )
        # Qwen3/3.5 thinking mode 억제 (기본 on)
        disable_thinking = os.getenv("VLLM_DISABLE_THINKING", "true").lower() != "false"
        self._extra_body = {"enable_thinking": False} if disable_thinking else {}

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
        if self._extra_body:
            params["extra_body"] = self._extra_body

        if on_content_delta is None:
            return self._client.chat.completions.create(**params)

        params["stream"] = True
        params["stream_options"] = {"include_usage": True}
        stream = self._client.chat.completions.create(**params)
        return accumulate_stream(stream, on_delta=on_content_delta)
