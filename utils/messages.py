"""OpenAI-format message normalization for the agent conversation history.

Responsibilities:
  1. Detect orphaned tool_calls (assistant called a tool but no result followed)
     and insert a placeholder so the API doesn't reject the history.
  2. Merge consecutive same-role user messages into one.
  3. Safety-cap every tool result content length — tool handler is first line of
     defense (200KB + marker); this is the second line (250KB hard cap) in case
     a handler forgot to apply its own marker.
"""

# 2차 안전망 — tool 핸들러는 이미 _MAX_OUTPUT(200KB) 에서 자르지만,
# 혹시 마커 없이 통과한 대용량 content 가 있으면 여기서 한 번 더 자르고 마커 부착.
_MAX_TOOL_CONTENT = 250_000


def _cap_tool_content(content: str | None) -> str | None:
    if not content or len(content) <= _MAX_TOOL_CONTENT:
        return content
    omitted = len(content) - _MAX_TOOL_CONTENT
    return (
        content[:_MAX_TOOL_CONTENT]
        + f"\n\n[TOOL RESULT TRUNCATED — {omitted:,} bytes omitted at messages layer. "
        f"Re-run tool with pagination or narrower scope. Do NOT treat as complete.]"
    )


def normalize_messages(messages: list[dict]) -> list[dict]:
    if not messages:
        return messages

    # Collect tool_call_ids that already have a matching tool result
    responded: set[str] = {
        msg["tool_call_id"]
        for msg in messages
        if msg.get("role") == "tool" and "tool_call_id" in msg
    }

    # Insert placeholder results for orphaned tool_calls
    extras: list[dict] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            tc_id = tc.get("id", "")
            if tc_id and tc_id not in responded:
                extras.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": "(cancelled)",
                })
                responded.add(tc_id)

    all_msgs = messages + extras

    # Merge consecutive plain user messages (not tool results) + cap tool content
    merged: list[dict] = []
    for msg in all_msgs:
        if msg.get("role") == "tool":
            capped = _cap_tool_content(msg.get("content"))
            if capped is not msg.get("content"):
                msg = {**msg, "content": capped}

        is_plain_user = msg.get("role") == "user" and "tool_call_id" not in msg
        prev_is_plain_user = (
            merged
            and merged[-1].get("role") == "user"
            and "tool_call_id" not in merged[-1]
        )
        if is_plain_user and prev_is_plain_user:
            prev_content = merged[-1]["content"] or ""
            curr_content = msg.get("content") or ""
            merged[-1] = {"role": "user", "content": f"{prev_content}\n{curr_content}"}
        else:
            merged.append(msg)

    return merged
