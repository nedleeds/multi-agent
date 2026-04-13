"""OrchestratorAgent: wires all agent components into a single run() interface.

  ┌─────────────────────────────────────────────────────────┐
  │  OrchestratorAgent                                      │
  │                                                         │
  │  main_model (ollama/120b) ──► loop.py  ◄── tools/       │
  │                                  │                      │
  │  extra_handlers:                 │                      │
  │    todo     ──► planner.py       │                      │
  │    load_skill ► skill.py         │                      │
  │    task     ──► subagent.py      │   sub_model          │
  │                  └──────────────────► (vllm/gemma4)     │
  │    compact  ──► compact.py       │                      │
  └─────────────────────────────────────────────────────────┘
"""

from pathlib import Path

from model.base import BaseLLM
from tools import definitions
from tools.registry import ToolRegistry
from utils.console import print_info, print_plan

from .compact import CONTEXT_LIMIT, compact_history, estimate_size, micro_compact
from .loop import agent_loop
from .planner import TodoManager
from .skill import SkillRegistry
from .state import CompactState, LoopState
from .subagent import run_subagent

_WORKDIR = Path.cwd()


class OrchestratorAgent:
    def __init__(
        self,
        main_model: BaseLLM,
        sub_model: BaseLLM,
        skills_dir: Path = _WORKDIR / "skills",
    ):
        self.main_model = main_model
        self.sub_model = sub_model
        self.registry = ToolRegistry()
        self.planner = TodoManager()
        self.skills = SkillRegistry(skills_dir)
        self.compact_state = CompactState()
        self.history: list[dict] = []

        self._extra_handlers = {
            "todo":       self._handle_todo,
            "load_skill": lambda name: self.skills.load(name),
            "task":       self._handle_task,
            "compact":    self._handle_compact,
        }

    # ── System prompts ──────────────────────────────────────────────────────

    def _system_prompt(self) -> str:
        return (
            f"You are a coding agent at {_WORKDIR}.\n"
            "Use tools to solve tasks step by step. "
            "Use the task tool to delegate exploration or independent subtasks to a subagent. "
            "Use todo for multi-step work. Use compact when the conversation grows too long.\n\n"
            f"Available skills:\n{self.skills.catalog()}"
        )

    def _subagent_system_prompt(self) -> str:
        return (
            f"You are a coding subagent at {_WORKDIR}. "
            "Complete the given task using tools, then summarize your findings concisely."
        )

    # ── Extra-handler implementations ────────────────────────────────────────

    def _handle_todo(self, items: list) -> str:
        result = self.planner.update(items)
        print_plan(self.planner.render())
        return result

    def _handle_task(self, prompt: str, description: str = "subtask") -> str:
        return run_subagent(
            prompt=prompt,
            model=self.sub_model,
            registry=self.registry,
            system=self._subagent_system_prompt(),
            description=description,
        )

    def _handle_compact(self, focus: str | None = None) -> str:
        self.history[:] = compact_history(
            self.history, self.compact_state, self.main_model, focus=focus
        )
        return "Conversation compacted."

    # ── Main entry point ─────────────────────────────────────────────────────

    def run(self, user_input: str) -> str:
        """Process one user message and return the final assistant reply."""
        self.history.append({"role": "user", "content": user_input})

        # Apply compaction before each turn
        self.history[:] = micro_compact(self.history)
        if estimate_size(self.history) > CONTEXT_LIMIT:
            print_info("[auto-compact]")
            self.history[:] = compact_history(
                self.history, self.compact_state, self.main_model
            )

        state = LoopState(messages=self.history)
        agent_loop(
            state=state,
            model=self.main_model,
            tools=definitions.ORCHESTRATOR_TOOLS,
            registry=self.registry,
            system=self._system_prompt(),
            extra_handlers=self._extra_handlers,
        )

        # Track whether todo was updated this turn for the planner nudge
        used_todo = any(
            tc.get("function", {}).get("name") == "todo"
            for msg in self.history
            for tc in (msg.get("tool_calls") or [])
        )
        self.planner.note_round(used_todo=used_todo)

        # Return last non-empty assistant reply
        for msg in reversed(self.history):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return ""
