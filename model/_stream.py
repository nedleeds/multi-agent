"""OpenAI-compatible chat.completions streaming accumulator.

`stream=True` 로 받은 청크 stream 을 순회하며:
  · content delta 를 on_delta 콜백에 전달 (UI 실시간 출력)
  · tool_calls · finish_reason · usage 를 재구성
  · 최종적으로 기존 non-stream 코드가 쓰던 형태와 호환되는 객체를 반환

OpenAI SDK 의 ChatCompletion shape 을 완벽히 흉내내진 않고, `agent/loop.py`
가 접근하는 속성 (`.choices[0].message.{content,tool_calls}`,
`.choices[0].finish_reason`, `.usage.{prompt,completion}_tokens`) 만 제공.
"""

from collections.abc import Callable, Iterable
from types import SimpleNamespace


def accumulate_stream(
    stream: Iterable,
    on_delta: Callable[[str], None] | None = None,
) -> SimpleNamespace:
    """Consume a chat.completions stream. Returns an object duck-typed like
    ChatCompletion that the existing agent_loop code can use unchanged."""
    content_parts: list[str] = []
    # tool_calls index → {id, type, name, arguments}
    tc_by_idx: dict[int, dict] = {}
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    for chunk in stream:
        # 일부 청크는 usage 전용 (choices 없음) — include_usage=True 일 때
        if getattr(chunk, "usage", None):
            prompt_tokens = getattr(chunk.usage, "prompt_tokens", prompt_tokens)
            completion_tokens = getattr(chunk.usage, "completion_tokens", completion_tokens)

        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        choice = choices[0]
        delta = getattr(choice, "delta", None)

        if delta is not None:
            text = getattr(delta, "content", None)
            if text:
                content_parts.append(text)
                if on_delta is not None:
                    on_delta(text)

            tc_deltas = getattr(delta, "tool_calls", None) or []
            for tcd in tc_deltas:
                idx = getattr(tcd, "index", 0) or 0
                entry = tc_by_idx.setdefault(
                    idx, {"id": "", "type": "function", "name": "", "arguments": ""}
                )
                if getattr(tcd, "id", None):
                    entry["id"] += tcd.id
                fn = getattr(tcd, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        entry["name"] += fn.name
                    if getattr(fn, "arguments", None):
                        entry["arguments"] += fn.arguments

        fr = getattr(choice, "finish_reason", None)
        if fr:
            finish_reason = fr

    # dict → duck-typed objects (agent_loop 이 속성 접근)
    tool_calls = None
    if tc_by_idx:
        tool_calls = [
            SimpleNamespace(
                id=entry["id"],
                type=entry["type"],
                function=SimpleNamespace(
                    name=entry["name"],
                    arguments=entry["arguments"],
                ),
            )
            for _, entry in sorted(tc_by_idx.items())
        ]

    message = SimpleNamespace(
        content="".join(content_parts) or None,
        tool_calls=tool_calls,
    )
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    return SimpleNamespace(choices=[choice], usage=usage)
