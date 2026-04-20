"""Model connection settings, loaded from environment variables.

플랫폼별 기본값:
  - macOS / Linux: 공식 OpenAI API (`api.openai.com`)
  - Windows: 사내 LLM 서버 (`api.hd-aic.com`)

`.env` 의 `OPENAI_BASE_URL` / `OPENAI_MODEL` / `OPENAI_API_KEY` 는 플랫폼 기본값을
항상 override 한다.
"""

import os
from dataclasses import dataclass

from utils.shell import IS_WINDOWS


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
    # Windows = 사내 서버, macOS/Linux = 공식 OpenAI API.
    # `.env` 값은 플랫폼 기본값을 항상 override.
    if IS_WINDOWS:
        default_base_url = "https://api.hd-aic.com/hd-llm-model/v1"
        default_model_id = "/models/gpt-oss-120b"
        default_api_key = os.getenv("AIC_API_KEY", "token-abc123")
    else:
        default_base_url = "https://api.openai.com/v1"
        default_model_id = "gpt-oss-120b"
        default_api_key = "dummy"

    return ModelConfig(
        base_url=os.getenv("OPENAI_BASE_URL", default_base_url),
        model_id=os.getenv("OPENAI_MODEL", default_model_id),
        api_key=os.getenv("OPENAI_API_KEY", default_api_key),
        max_tokens=int(os.getenv("OPENAI_MAX_TOKENS", "16000")),
        temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.7")),
    )
