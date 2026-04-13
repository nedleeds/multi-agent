"""Session planning via TodoManager.

Implements the s03 pattern: the model rewrites its current plan at any time,
keeps exactly one step in_progress, and gets nudged to refresh after several
rounds without an update.
"""

from .state import PlanItem, PlanningState

_MAX_ITEMS = 12
_REMINDER_INTERVAL = 3


class TodoManager:
    def __init__(self):
        self.state = PlanningState()

    def update(self, items: list[dict]) -> str:
        if len(items) > _MAX_ITEMS:
            raise ValueError(f"Plan too long (max {_MAX_ITEMS} items)")

        normalized: list[PlanItem] = []
        in_progress = 0
        for idx, raw in enumerate(items):
            content = str(raw.get("content", "")).strip()
            status = str(raw.get("status", "pending")).lower()
            active_form = str(raw.get("activeForm", "")).strip()

            if not content:
                raise ValueError(f"Item {idx}: content is required")
            if status not in {"pending", "in_progress", "completed"}:
                raise ValueError(f"Item {idx}: invalid status '{status}'")
            if status == "in_progress":
                in_progress += 1

            normalized.append(PlanItem(content=content, status=status, active_form=active_form))

        if in_progress > 1:
            raise ValueError("Only one item can be in_progress at a time")

        self.state.items = normalized
        self.state.rounds_since_update = 0
        return self.render()

    def note_round(self, used_todo: bool) -> None:
        self.state.rounds_since_update = 0 if used_todo else self.state.rounds_since_update + 1

    def reminder(self) -> str | None:
        if not self.state.items:
            return None
        if self.state.rounds_since_update < _REMINDER_INTERVAL:
            return None
        return "<reminder>Refresh your session plan before continuing.</reminder>"

    def render(self) -> str:
        if not self.state.items:
            return "No session plan."
        markers = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}
        lines = []
        for item in self.state.items:
            line = f"{markers[item.status]} {item.content}"
            if item.status == "in_progress" and item.active_form:
                line += f" ({item.active_form})"
            lines.append(line)
        done = sum(1 for i in self.state.items if i.status == "completed")
        lines.append(f"\n({done}/{len(self.state.items)} completed)")
        return "\n".join(lines)
