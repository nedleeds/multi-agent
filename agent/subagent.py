"""Subagent runner.

Implements the s04 pattern: spawn a child agent with fresh messages=[].
The child works in its own context (sharing the filesystem), then returns
only a text summary to the parent — child context is discarded.

    Parent (ollama/120b)               Subagent (vllm/gemma4)
    ┌──────────────────┐               ┌──────────────────┐
    │ history=[...]    │  task(prompt) │ messages=[]  ◄── fresh
    │                  │ ───────────► │                  │
    │                  │              │ while tool_calls: │
    │                  │              │   execute tools  │
    │                  │  summary     │                  │
    │ result = "..."   │ ◄─────────── │ return last text │
    └──────────────────┘               └──────────────────┘
"""

from model.base import BaseLLM
from tools import definitions
from tools.registry import ToolRegistry
from utils.console import print_info

from .loop import agent_loop
from .state import LoopState

_MAX_TURNS = 30


def run_subagent(
    prompt: str,
    model: BaseLLM,
    registry: ToolRegistry,
    system: str,
    description: str = "subtask",
) -> str:
    """Run a subagent with fresh context and return its final text summary."""
    print_info(f"[subagent:{description}] starting…")
    state = LoopState(messages=[{"role": "user", "content": prompt}])

    agent_loop(
        state=state,
        model=model,
        tools=definitions.CHILD_TOOLS,
        registry=registry,
        system=system,
        max_turns=_MAX_TURNS,
    )

    # Return the last non-empty assistant reply; child context is then discarded
    for msg in reversed(state.messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            return msg["content"]
    return "(no summary)"
