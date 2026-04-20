"""Context compaction utilities.

Implements the s06 pattern — three levels of compaction:
  1. persist_large_output  : large tool outputs are saved to disk; only a preview stays in context.
  2. micro_compact         : old tool results beyond the recent N are replaced with a short placeholder.
  3. compact_history       : the entire conversation is summarized and replaced with that summary.
"""

import json
import time
from pathlib import Path

from model.base import BaseLLM
from utils.console import compacting_spinner, print_info

from .state import CompactState

CONTEXT_LIMIT = 50_000
_KEEP_RECENT = 3
_PERSIST_THRESHOLD = 30_000
_PREVIEW_CHARS = 2_000
_TRANSCRIPT_DIR = Path(".transcripts")
_TOOL_RESULTS_DIR = Path(".task_outputs/tool-results")


def estimate_size(messages: list[dict]) -> int:
    return len(json.dumps(messages, default=str))


def persist_large_output(tool_call_id: str, output: str) -> str:
    """If output exceeds threshold, save to disk and return a preview marker."""
    if len(output) <= _PERSIST_THRESHOLD:
        return output
    _TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _TOOL_RESULTS_DIR / f"{tool_call_id}.txt"
    if not path.exists():
        path.write_text(output, encoding="utf-8")
    preview = output[:_PREVIEW_CHARS]
    return (
        "<persisted-output>\n"
        f"Full output saved to: {path}\n"
        f"Preview:\n{preview}\n"
        "</persisted-output>"
    )


def micro_compact(messages: list[dict]) -> list[dict]:
    """Replace old tool-result bodies with a short placeholder, keeping the most recent N."""
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_indices) <= _KEEP_RECENT:
        return messages

    old = set(tool_indices[:-_KEEP_RECENT])
    result = []
    for i, msg in enumerate(messages):
        if i in old and len(str(msg.get("content", ""))) > 120:
            msg = {**msg, "content": "[Earlier result compacted. Re-run the tool if you need full detail.]"}
        result.append(msg)
    return result


def compact_history(
    messages: list[dict],
    state: CompactState,
    model: BaseLLM,
    focus: str | None = None,
) -> list[dict]:
    """Summarize the entire conversation and replace it with that summary."""
    # Persist transcript before discarding
    _TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    transcript = _TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with transcript.open("w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str, ensure_ascii=False) + "\n")
    print_info(f"[compact] transcript → {transcript}")

    # Summarize
    conversation = json.dumps(messages, default=str)[:80_000]
    prompt = (
        "Summarize this coding-agent conversation so work can continue.\n"
        "Preserve: current goal, key findings, files changed, remaining work, user constraints.\n"
        "Be compact but concrete.\n\n" + conversation
    )
    with compacting_spinner():
        response = model.chat([{"role": "user", "content": prompt}])
    summary = (response.choices[0].message.content or "").strip()

    if focus:
        summary += f"\n\nNext focus: {focus}"
    if state.recent_files:
        summary += "\n\nRecently accessed files:\n" + "\n".join(f"- {p}" for p in state.recent_files)

    state.has_compacted = True
    state.last_summary = summary
    return [{"role": "user", "content": "Conversation compacted.\n\n" + summary}]
