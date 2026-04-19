"""Model connection settings, loaded from environment variables."""

import os
from dataclasses import dataclass


@dataclass
class ModelConfig:
    base_url: str
    model_id: str
    api_key: str
    max_tokens: int = 8000
    temperature: float = 0.7


def ollama_config() -> ModelConfig:
    return ModelConfig(
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        model_id=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
        api_key="ollama",
        max_tokens=int(os.getenv("OLLAMA_MAX_TOKENS", "8000")),
    )


def vllm_config() -> ModelConfig:
    return ModelConfig(
        base_url=os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"),
        model_id=os.getenv("VLLM_MODEL", "google/gemma-4-27b-it"),
        api_key=os.getenv("VLLM_API_KEY", "token-abc123"),
        max_tokens=int(os.getenv("VLLM_MAX_TOKENS", "12000")),
    )


def openai_config() -> ModelConfig:
    # base_url 은 OpenAI API 기본값을 쓰되, 회사 로컬 gpt-oss 서버처럼
    # OpenAI-compatible 엔드포인트를 쓸 때는 env 로 override.
    return ModelConfig(
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model_id=os.getenv("OPENAI_MODEL", "gpt-oss-120b"),
        api_key=os.getenv("OPENAI_API_KEY", "dummy"),
        max_tokens=int(os.getenv("OPENAI_MAX_TOKENS", "16000")),
        temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.7")),
    )
