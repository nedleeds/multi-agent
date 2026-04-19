"""Core agent loop: feed tool results back until the model stops calling tools.

Implements the s01 loop structure and s02 tool-dispatch pattern.

  user message
    → model reply
    → if tool_calls: execute each tool
    → append tool results
    → repeat
"""

import json
import os
from collections.abc import Callable

from model.base import BaseLLM
from tools.registry import ToolRegistry
from utils.console import add_tokens, console, print_tool_call
from utils.messages import normalize_messages

from .state import LoopState

_DEBUG = os.getenv("AGENT_DEBUG", "").strip() in ("1", "true", "yes")


def _debug(msg: str) -> None:
    if _DEBUG:
        console.print(f"[dim]  [debug] {msg}[/dim]")


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
    _debug(
        f"turn={state.turn_count} model={model.config.model_id} "
        f"msgs={len(api_messages)} tools={len(tools or [])}"
    )
    response = model.chat(api_messages, tools=tools or None)
    if response.usage:
        add_tokens(
            prompt=response.usage.prompt_tokens or 0,
            completion=response.usage.completion_tokens or 0,
        )
    choice = response.choices[0]
    msg = choice.message
    _debug(
        f"  → finish={choice.finish_reason} "
        f"tool_calls={len(msg.tool_calls or [])} "
        f"content={(msg.content or '')[:80]!r}"
    )

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

    has_tool_calls = bool(msg.tool_calls)

    # finish_reason == "length" — 모델 출력이 max_tokens 에서 잘림.
    # 조용히 break 하지 말고 continue 유도 메시지를 주입해서 이어서 말하게 함.
    # 최대 2회까지만 재시도 (무한 loop 방지).
    if choice.finish_reason == "length" and not has_tool_calls:
        if state.length_continues >= 2:
            state.transition_reason = "length_exhausted"
            return False
        state.length_continues += 1
        state.messages.append({
            "role": "user",
            "content": (
                "[SYSTEM: your previous response was cut off at the token limit. "
                "Continue from exactly where you left off — do not restart or "
                "summarize. If you were calling a tool, emit the tool_call now.]"
            ),
        })
        state.transition_reason = "length_continue"
        return True

    # 그 외엔 기존 로직 — tool_calls 가 있을 때만 루프 계속
    wants_tools = choice.finish_reason in ("tool_calls", "stop") and has_tool_calls
    if not wants_tools:
        state.transition_reason = None
        return False

    # 정상 tool_call — length_continues 카운터 리셋
    state.length_continues = 0

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
