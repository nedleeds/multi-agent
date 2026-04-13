"""Core agent loop: feed tool results back until the model stops calling tools.

Implements the s01 loop structure and s02 tool-dispatch pattern.

  user message
    → model reply
    → if tool_calls: execute each tool
    → append tool results
    → repeat
"""

import json
from collections.abc import Callable

from model.base import BaseLLM
from tools.registry import ToolRegistry
from utils.console import print_tool_call
from utils.messages import normalize_messages

from .state import LoopState


def run_one_turn(
    state: LoopState,
    model: BaseLLM,
    tools: list[dict],
    registry: ToolRegistry,
    system: str,
    extra_handlers: dict[str, Callable] | None = None,
) -> bool:
    """Run one model call. Returns True if the loop should continue."""
    api_messages = normalize_messages(
        [{"role": "system", "content": system}] + state.messages
    )
    response = model.chat(api_messages, tools=tools or None)
    choice = response.choices[0]
    msg = choice.message

    # Serialize assistant message to a plain dict for history storage
    assistant_entry: dict = {"role": "assistant", "content": msg.content}
    if msg.tool_calls:
        assistant_entry["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
    state.messages.append(assistant_entry)

    # Continue only when the model explicitly requested tool calls
    has_tool_calls = bool(msg.tool_calls)
    wants_tools = choice.finish_reason in ("tool_calls", "stop") and has_tool_calls
    if not wants_tools:
        state.transition_reason = None
        return False

    for tc in msg.tool_calls:
        name = tc.function.name
        if extra_handlers and name in extra_handlers:
            try:
                args = json.loads(tc.function.arguments)
                output = str(extra_handlers[name](**args))
            except Exception as exc:
                output = f"Error: {exc}"
        else:
            output = registry.dispatch(name, tc.function.arguments)

        print_tool_call(name, output)
        state.messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": output,
        })

    state.turn_count += 1
    state.transition_reason = "tool_result"
    return True


def agent_loop(
    state: LoopState,
    model: BaseLLM,
    tools: list[dict],
    registry: ToolRegistry,
    system: str,
    extra_handlers: dict[str, Callable] | None = None,
    max_turns: int = 50,
) -> None:
    """Run until the model stops calling tools or max_turns is reached."""
    while state.turn_count <= max_turns:
        if not run_one_turn(state, model, tools, registry, system, extra_handlers):
            break
