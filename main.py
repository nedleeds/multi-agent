#!/usr/bin/env python3
"""Multi-agent REPL entry point — 단일 통합 오케스트레이터.

배선 (고정):
  main_model  = OpenAIModel   — gpt-oss-120b (OpenAI API 또는 사내 local OpenAI-compatible 서버)
  sub_model   = VLLMModel     — gemma 계열 (사내 vLLM 서버, Mac 에서는 OpenAI-compat 엔드포인트 아무거나)

환경 전환은 코드 수정 없이 `.env` 의 BASE_URL / MODEL / API_KEY 만 바꿔서 수행 — README.md 참고.
"""
from pathlib import Path

from dotenv import load_dotenv

from agent import OrchestratorAgent
from model import OpenAIModel, VLLMModel
from utils.console import display_clear_todos
from utils.repl import REPLSession

load_dotenv(override=True)


def main() -> None:
    # Main 은 항상 gpt-oss-120b — OpenAIModel 이 OPENAI_BASE_URL 존중
    main_model = OpenAIModel()
    # Sub 은 vLLM 계열 — 사내에선 gemma, Mac 에선 ollama 등 어떤 OpenAI-compatible 엔드포인트도 가능
    sub_model  = VLLMModel()

    agent = OrchestratorAgent(
        main_model=main_model,
        sub_model=sub_model,
        skills_dir=Path("skills"),
    )

    def on_clear() -> None:
        agent.history.clear()
        agent.planner.state.items = []
        display_clear_todos()

    REPLSession(
        mode="unified",
        main_model=main_model.config.model_id,
        sub_model=sub_model.config.model_id,
    ).run(
        agent_fn=agent.run,
        on_clear=on_clear,
        on_cancel=agent.cancel,
        permissions=agent.permissions,  # 파괴적 tool 승인 브릿지
    )


if __name__ == "__main__":
    main()
