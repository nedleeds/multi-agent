"""OpenAI-format message normalization for the agent conversation history.

Three responsibilities:
  1. Detect orphaned tool_calls (assistant called a tool but no result followed)
     and insert a placeholder so the API doesn't reject the history.
  2. Merge consecutive same-role user messages into one.
  3. Pass everything else through unchanged.
"""


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

    # Merge consecutive plain user messages (not tool results)
    merged: list[dict] = []
    for msg in all_msgs:
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
