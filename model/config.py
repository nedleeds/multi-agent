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
        model_id=os.getenv("OLLAMA_MODEL", "gpt-120b"),
        api_key="ollama",
        max_tokens=int(os.getenv("OLLAMA_MAX_TOKENS", "8000")),
    )


def vllm_config() -> ModelConfig:
    return ModelConfig(
        base_url=os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"),
        model_id=os.getenv("VLLM_MODEL", "google/gemma-3-27b-it"),
        api_key=os.getenv("VLLM_API_KEY", "token-abc123"),
        max_tokens=int(os.getenv("VLLM_MAX_TOKENS", "4000")),
    )
