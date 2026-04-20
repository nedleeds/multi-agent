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

from .permission import PermissionManager, needs_approval
from utils.console import (
    add_tokens,
    clear_activity,
    console,
    print_tool_call,
    set_activity,
    stream_assistant_begin,
    stream_assistant_delta,
    stream_assistant_end,
)
from utils.messages import normalize_messages

from .state import LoopState

_DEBUG = os.getenv("AGENT_DEBUG", "").strip() in ("1", "true", "yes")


def _debug(msg: str) -> None:
    if _DEBUG:
        console.print(f"[dim]  [debug] {msg}[/dim]")


def _count_lines(text: str) -> int:
    if not text:
        return 0
    # 마지막 줄 개행 여부 보정
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _activity_label(name: str, args: dict) -> str:
    """Spinner 아래 `↳ <...>` 로 표시할 '지금 실행 중' 한 줄. 결과가 오기 전
    호출되므로 output 에 의존하지 않고 tool name + primary arg 로만 구성."""
    if name == "read_file":
        path = args.get("path", "?")
        off = args.get("offset", 0) or 0
        return f"read_file {path}" + (f" @{off}" if off else "")
    if name == "ls":
        return f"ls {args.get('path', '.')}  depth={args.get('depth', 2)}"
    if name == "grep":
        return f"grep {args.get('pattern', '')!r}"
    if name == "glob":
        return f"glob {args.get('pattern', '')!r}"
    if name == "fuzzy_find":
        return f"fuzzy_find {args.get('query', '')!r}"
    if name == "bash":
        return f"bash {args.get('command', '')[:60]}"
    if name == "write_file":
        return f"write {args.get('path', '?')}"
    if name == "edit_file":
        return f"edit {args.get('path', '?')}"
    if name == "load_skill":
        return f"load_skill {args.get('name', '?')}"
    if name in ("task", "jira_task", "bitbucket_task", "confluence_task"):
        prompt = (args.get("prompt") or "")[:50].replace("\n", " ")
        return f"{name}: {prompt}…" if prompt else name
    # meta 툴 (todo, compact 등) 은 그냥 이름만
    return name


def _bullet_summary(name: str, args: dict, output: str) -> str:
    """Tool 결과를 bullet 한 줄로 요약. 전체 output 은 LLM 컨텍스트(state.messages)
    에 그대로 들어가고, 사용자 시각 bullet 은 **primary arg + 결과 메타**만 표시.

    Claude Code 의 `Read README.md (104 lines)` 패턴을 따른다. bash 는 명령 실행
    결과가 곧 사용자가 보고 싶은 것이므로 기존처럼 output 그대로.
    """
    # 에러면 그냥 error 메시지 보여주기
    if output.startswith("Error:"):
        return output

    if name == "read_file":
        path = args.get("path", "?")
        lines = _count_lines(output)
        offset = args.get("offset", 0) or 0
        where = f" @{offset}" if offset else ""
        return f"{path}{where}  ({lines} lines)"
    if name == "ls":
        path = args.get("path", ".")
        depth = args.get("depth", 2)
        entries = _count_lines(output)
        return f"{path}  depth={depth}  ({entries} entries)"
    if name == "grep":
        pat = args.get("pattern", "")
        if output.startswith("(no "):
            return f"{pat!r}  {output}"
        # content 모드는 line 수, files_with_matches 모드도 line 수 = 파일 수
        return f"{pat!r}  ({_count_lines(output)} matches)"
    if name == "glob":
        pat = args.get("pattern", "")
        if output.startswith("(no "):
            return f"{pat!r}  {output}"
        return f"{pat!r}  ({_count_lines(output)} files)"
    if name == "fuzzy_find":
        q = args.get("query", "")
        if output.startswith("(no "):
            return f"{q!r}  {output}"
        return f"{q!r}  ({_count_lines(output)} results)"
    if name == "write_file":
        return f"wrote {args.get('path', '?')}  ({len(args.get('content', ''))} bytes)"
    if name == "edit_file":
        return f"edited {args.get('path', '?')}"
    if name == "load_skill":
        return f"loaded: {args.get('name', '?')}"
    # bash / task / jira_task / bitbucket_task / confluence_task / 기타 —
    # 출력 자체가 사용자 관심사라 기존처럼 raw output.
    return output


def run_one_turn(
    state: LoopState,
    model: BaseLLM,
    tools: list[dict],
    registry: ToolRegistry,
    system: str,
    extra_handlers: dict[str, Callable] | None = None,
    stream_to_console: bool = False,
    permissions: PermissionManager | None = None,
) -> bool:
    """Run one model call. Returns True if the loop should continue.

    `stream_to_console=True` 면 content 토큰을 scrollback 에 실시간 출력한다.
    오케스트레이터(사용자 대면)는 True, subagent 는 False (bullet pulse 로 표현됨).
    """
    api_messages = normalize_messages(
        [{"role": "system", "content": system}] + state.messages
    )
    _debug(
        f"turn={state.turn_count} model={model.config.model_id} "
        f"msgs={len(api_messages)} tools={len(tools or [])}"
    )
    on_delta = None
    if stream_to_console:
        stream_assistant_begin()
        on_delta = stream_assistant_delta
    try:
        response = model.chat(api_messages, tools=tools or None, on_content_delta=on_delta)
        if stream_to_console:
            stream_assistant_end()
    except Exception as exc:
        if stream_to_console:
            stream_assistant_end()
        # 에러 로그에 뜨는 메시지만으론 base_url/model 맥락을 알 수 없어서
        # 예외에 컨텍스트 붙여서 re-raise. utils/error_log.py 가 _agent_ctx 를 읽음.
        exc._agent_ctx = {  # type: ignore[attr-defined]
            "layer": "model.chat",
            "turn": state.turn_count,
            "model_id": model.config.model_id,
            "base_url": model.config.base_url,
            "messages": len(api_messages),
            "tools": len(tools or []),
        }
        raise
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
        if state.cancel_event is not None and state.cancel_event.is_set():
            state.transition_reason = "cancelled"
            return False
        name = tc.function.name

        # args 파싱은 display 요약에도 필요해서 handler 분기 이전에 공통화.
        try:
            args = json.loads(tc.function.arguments) if tc.function.arguments else {}
        except (ValueError, TypeError):
            args = {}

        # 중복 호출 가드 — 직전 2회와 완전히 동일한 signature 면 3번째 실행 차단.
        # 스키마 불일치로 힌트 따라갔다가 루프 도는 케이스 (과거 read_file@offset 사건).
        sig = f"{name}({json.dumps(args, sort_keys=True, ensure_ascii=False)})"
        is_duplicate = (
            len(state.recent_tool_sigs) >= 2
            and state.recent_tool_sigs[-1] == sig
            and state.recent_tool_sigs[-2] == sig
        )
        # 모델이 grep/glob 로 "좁혀서 찾는" 자세로 전환했다는 신호 — 그 파일에 대한
        # read_file 카운터를 리셋해서 이미 발견한 line 으로의 read_file budget 회복.
        # 예: cycling guard 가 발동해 모델이 grep 으로 def 위치를 찾았을 때, 그 한 지점을
        # 실제로 읽으려는 다음 read_file 을 막지 않기 위함.
        if name in ("grep", "glob", "fuzzy_find"):
            state.file_read_counts.clear()

        # read_file 전용 cycling 방어 — offset 을 계속 바꾸며 같은 파일 맴돌기 방지.
        # 1) 동일 (path, limit, offset) 재호출 → 바로 차단 (2번째부터)
        # 2) 같은 path 누적 5회 이상 → synthesize/grep 전환 유도
        read_file_block: str | None = None
        if name == "read_file":
            path = str(args.get("path", ""))
            key = (args.get("limit") or 0, args.get("offset", 0) or 0)
            state.file_read_counts[path] = state.file_read_counts.get(path, 0) + 1
            seen = state.file_read_seen.setdefault(path, set())
            if key in seen:
                read_file_block = (
                    f"Error: duplicate read_file blocked — `{path}` at limit={key[0]} offset={key[1]} "
                    f"was already read earlier in this turn. Look at the previous tool result instead. "
                    f"If you need a different section, use a different offset or `grep` for a pattern."
                )
            elif state.file_read_counts[path] >= 6:
                read_file_block = (
                    f"Error: you've called read_file on `{path}` {state.file_read_counts[path]} times this turn — "
                    f"you're cycling. Stop and do ONE of:\n"
                    f"  (a) synthesize an answer from chunks you've already seen,\n"
                    f"  (b) advance to the next step of your plan (see Todo),\n"
                    f"  (c) use `grep(pattern=..., path='{path}')` to locate the section you actually need."
                )
            else:
                seen.add(key)

        if is_duplicate:
            output = (
                f"Error: duplicate tool call blocked — `{name}` called 3× with identical args.\n"
                "This usually means the previous result wasn't what you expected. "
                "Change your approach: different args, a different tool, or answer with what you already have. "
                "Do NOT retry this call."
            )
        elif read_file_block:
            output = read_file_block
        else:
            # 파괴적 tool 은 사용자 승인 게이트 통과 필수 (permissions 가 wiring 된 경우).
            # 통과 못하면 실제 실행 skip 하고 Error 로 tool_result 주입 → 모델이 읽고 재판단.
            gated = bool(permissions) and needs_approval(name, args)
            if gated:
                # 승인 대기 중엔 activity 라인이 거슬리므로 임시 클리어.
                clear_activity()
                approved, reason = permissions.request(name, args)  # type: ignore[union-attr]
                if not approved:
                    output = f"Error: {reason}"
                    # ring buffer + bullet 은 기존 경로에 맡김 (아래)
                    state.recent_tool_sigs.append(sig)
                    if len(state.recent_tool_sigs) > 3:
                        state.recent_tool_sigs = state.recent_tool_sigs[-3:]
                    print_tool_call(name, _bullet_summary(name, args, output))
                    state.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": output,
                    })
                    continue
            # Spinner 바로 아래 `↳ <tool> <arg요약>` 으로 현재 실행 중인 단계 표시.
            # try/finally 로 예외 시에도 활동 표시 클리어 보장.
            set_activity(_activity_label(name, args))
            try:
                if extra_handlers and name in extra_handlers:
                    try:
                        output = str(extra_handlers[name](**args))
                    except Exception as exc:
                        output = f"Error: {exc}"
                else:
                    output = registry.dispatch(name, tc.function.arguments)
            finally:
                clear_activity()

        # 링버퍼 유지 (크기 3)
        state.recent_tool_sigs.append(sig)
        if len(state.recent_tool_sigs) > 3:
            state.recent_tool_sigs = state.recent_tool_sigs[-3:]

        # orchestrator 의 plan 감사에 사용 — 이 턴 안에서 todo 가 호출됐는지 추적.
        if name == "todo":
            state.todo_called = True

        # Tool 마다 맞춤 요약 (read_file/ls 등 결과 본문 대신 메타). LLM 은 full output 받음.
        print_tool_call(name, _bullet_summary(name, args, output))
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
    stream_to_console: bool = False,
    permissions: PermissionManager | None = None,
) -> None:
    """Run until the model stops calling tools or max_turns is reached.

    `stream_to_console` 를 전달하면 매 turn 의 content 가 scrollback 에 실시간 출력된다.
    최상위 사용자 대면 오케스트레이터만 True 로 설정. subagent 는 False (기본).

    `permissions` 가 주어지면 파괴적 tool 은 사용자 승인 경유. subagent 는 None
    (부모가 delegation 시점에 이미 승인받았으므로 내부 호출은 gating 안 함).
    """
    while state.turn_count <= max_turns:
        if state.cancel_event is not None and state.cancel_event.is_set():
            state.transition_reason = "cancelled"
            console.print("[info]⏹  cancelled at turn boundary[/info]")
            break
        if not run_one_turn(
            state, model, tools, registry, system, extra_handlers,
            stream_to_console=stream_to_console,
            permissions=permissions,
        ):
            if state.transition_reason == "cancelled":
                console.print("[info]⏹  cancelled mid-turn[/info]")
            break
