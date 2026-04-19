"""Subagent runner.

Implements the s04 pattern: spawn a child agent with fresh messages=[].
The child works in its own context (sharing the filesystem), then returns
a text summary PLUS a compact evidence trail of its tool calls to the parent.

    Parent (ollama/120b)               Subagent (vllm/gemma4)
    ┌──────────────────┐               ┌──────────────────┐
    │ history=[...]    │  task(prompt) │ messages=[]  ◄── fresh
    │                  │ ───────────► │                  │
    │                  │              │ while tool_calls: │
    │                  │              │   execute tools  │
    │                  │  summary     │                  │
    │                  │  + evidence  │                  │
    │ result = "..."   │ ◄─────────── │ return report    │
    └──────────────────┘               └──────────────────┘
"""

import json
import time

from model.base import BaseLLM
from tools import definitions
from tools.registry import ToolRegistry
from utils.console import (
    console,
    fmt_time,
    fmt_tokens,
    print_subagent_end,
    print_subagent_start,
    task_board,
    token_snapshot,
)

from .loop import agent_loop
from .state import LoopState

_MAX_TURNS = 30

# 증거 섹션 — 각 tool 호출 당 최대 발췌 바이트
_EVIDENCE_PER_CALL = 800
# 증거 섹션 전체 최대 — 부모 컨텍스트 폭증 방지
_EVIDENCE_TOTAL    = 8_000


def _collect_evidence(messages: list[dict]) -> str:
    """assistant tool_call 과 뒤이은 tool result 를 pair 로 엮어 증거 섹션 구성."""
    # tool_call_id → (name, args_str)
    calls: dict[str, tuple[str, str]] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            tc_id = tc.get("id", "")
            fn    = tc.get("function") or {}
            calls[tc_id] = (fn.get("name", "?"), fn.get("arguments", ""))

    lines: list[str] = []
    used = 0
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        tc_id = msg.get("tool_call_id", "")
        if tc_id not in calls:
            continue
        name, raw_args = calls[tc_id]
        content = msg.get("content") or ""
        excerpt = content[:_EVIDENCE_PER_CALL]
        if len(content) > _EVIDENCE_PER_CALL:
            excerpt += f"… [+{len(content) - _EVIDENCE_PER_CALL} more bytes]"

        # args 는 한 줄로 축약 (JSON 디코드 실패하면 raw 그대로)
        try:
            args_obj = json.loads(raw_args) if raw_args else {}
            args_str = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)[:80]}"
                                 for k, v in args_obj.items())
        except (ValueError, TypeError):
            args_str = raw_args[:100]

        block = f"- **{name}**({args_str})\n```\n{excerpt}\n```"
        if used + len(block) > _EVIDENCE_TOTAL:
            lines.append(f"- _(truncated — {len(messages)} more tool result(s) omitted)_")
            break
        lines.append(block)
        used += len(block)

    return "\n".join(lines) if lines else "_(no tool calls recorded)_"


def run_subagent(
    prompt: str,
    model: BaseLLM,
    registry: ToolRegistry,
    system: str,
    description: str = "subtask",
    tools: list[dict] | None = None,
) -> str:
    """Run a subagent with fresh context and return summary + evidence.

    tools: tool schemas to give the subagent (default: CHILD_TOOLS).
           Pass API-specific tool sets for specialized subagents.
    """
    tool_schemas = tools if tools is not None else definitions.CHILD_TOOLS
    tool_names   = [t["function"]["name"] for t in tool_schemas]
    preview      = prompt[:80].replace("\n", " ")

    # ── 시작 배너 ────────────────────────────────────────────────────────────
    print_subagent_start(description)
    tools_tag = f"[{', '.join(tool_names[:6])}{'…' if len(tool_names) > 6 else ''}]"
    console.print(
        f"  [dim]↳ [sub:{description}] [bold]{model.config.model_id}[/bold] · "
        f"tools={tools_tag} · prompt=\"{preview}"
        f"{'…' if len(prompt) > 80 else ''}\"[/dim]"
    )

    start_time       = time.time()
    tokens_before_in, tokens_before_out = token_snapshot()
    state            = LoopState(messages=[{"role": "user", "content": prompt}])
    error_message    = None

    try:
        agent_loop(
            state=state,
            model=model,
            tools=tool_schemas,
            registry=registry,
            system=system,
            max_turns=_MAX_TURNS,
        )
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"

    elapsed       = time.time() - start_time
    tokens_in, tokens_out = token_snapshot()
    delta_in      = tokens_in  - tokens_before_in
    delta_out     = tokens_out - tokens_before_out
    stats         = (
        f"{state.turn_count} turns · {fmt_time(elapsed)} · "
        f"↑{fmt_tokens(delta_in)} ↓{fmt_tokens(delta_out)}"
    )

    # ── 에러 경로: task_board 정리 + 에러 배너 ────────────────────────────────
    if error_message:
        task_board.end_task(f"ERROR: {error_message}")
        console.print(
            f"  [error]✗ [sub:{description}] failed · {stats}[/error]\n"
            f"  [error]  {error_message}[/error]"
        )
        return f"(subagent error: {error_message})\n\n## Evidence\n{_collect_evidence(state.messages)}"

    # ── 정상 경로: summary + evidence 리포트 ──────────────────────────────────
    summary = ""
    for msg in reversed(state.messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            summary = msg["content"]
            break

    evidence = _collect_evidence(state.messages)
    if not summary:
        report = f"(no summary)\n\n## Evidence\n{evidence}"
    elif "## Evidence" in summary:
        report = f"{summary}\n\n## Tool Trace\n{evidence}"
    else:
        report = f"{summary}\n\n## Evidence\n{evidence}"

    print_subagent_end(report)
    console.print(f"  [dim]↳ [sub:{description}] done · {stats}[/dim]")
    return report
