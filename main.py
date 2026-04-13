#!/usr/bin/env python3
"""Multi-agent REPL entry point.

Starts an interactive session:
  - Main agent : OllamaModel (orchestrator, 120b)
  - Sub agent  : VLLMModel   (delegated subtasks, gemma 4)
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

from agent import OrchestratorAgent
from model import OllamaModel, VLLMModel
from utils.console import print_assistant, print_error, print_info, print_user_prompt


def main() -> None:
    main_model = OllamaModel()
    sub_model = VLLMModel()
    agent = OrchestratorAgent(
        main_model=main_model,
        sub_model=sub_model,
        skills_dir=Path("skills"),
    )

    print_info(
        f"multi-agent ready  "
        f"main={main_model.config.model_id}  "
        f"sub={sub_model.config.model_id}"
    )
    print_info("Type 'exit' or Ctrl-C to quit.\n")

    while True:
        try:
            user_input = print_user_prompt()
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


if __name__ == "__main__":
    main()
