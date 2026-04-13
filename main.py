#!/usr/bin/env python3
"""Multi-agent REPL entry point.

실행 모드:
  python main.py          → 일반 코딩 에이전트 (기본)
  python main.py --issue  → 현장 이슈 분석 에이전트 (Jira/Bitbucket/Confluence)
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

from agent import OrchestratorAgent
from agent.issue_investigator import IssueInvestigatorAgent
from model import OllamaModel, VLLMModel
from utils.console import console, print_assistant, print_error, print_info, print_user_prompt


def run_coding_agent(main_model: OllamaModel, sub_model: VLLMModel) -> None:
    """일반 코딩 에이전트 REPL."""
    agent = OrchestratorAgent(
        main_model=main_model,
        sub_model=sub_model,
        skills_dir=Path("skills"),
    )
    print_info("모드: 코딩 에이전트  (종료: exit 또는 Ctrl-C)\n")

    while True:
        try:
            user_input = print_user_prompt("agent >> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.strip().lower() in ("exit", "q", "quit", ""):
            break
        try:
            reply = agent.run(user_input)
            if reply:
                print_assistant(reply)
        except Exception as exc:
            print_error(str(exc))
        print()


def run_issue_agent(main_model: OllamaModel, sub_model: VLLMModel) -> None:
    """현장 이슈 분석 에이전트 REPL."""
    agent = IssueInvestigatorAgent(main_model=main_model, sub_model=sub_model)
    print_info("모드: 이슈 분석 에이전트  (종료: exit 또는 Ctrl-C)")
    print_info("현장에서 발생한 이슈 현상을 자유롭게 입력하세요.\n")

    while True:
        try:
            user_input = print_user_prompt("issue >> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.strip().lower() in ("exit", "q", "quit", ""):
            break
        try:
            reply = agent.run(user_input)
            if reply:
                print_assistant(reply)
        except Exception as exc:
            print_error(str(exc))
        print()


def main() -> None:
    main_model = OllamaModel()
    sub_model = VLLMModel()

    console.rule("[bold]multi-agent[/bold]")
    print_info(f"main={main_model.config.model_id}  sub={sub_model.config.model_id}")

    issue_mode = "--issue" in sys.argv
    if issue_mode:
        run_issue_agent(main_model, sub_model)
    else:
        run_coding_agent(main_model, sub_model)


if __name__ == "__main__":
    main()
