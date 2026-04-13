"""Shared state dataclasses for the agent loop."""

from dataclasses import dataclass, field


@dataclass
class LoopState:
    """Minimal loop state: conversation history + turn tracking (s01)."""
    messages: list[dict]
    turn_count: int = 1
    transition_reason: str | None = None


@dataclass
class PlanItem:
    content: str
    status: str = "pending"   # pending | in_progress | completed
    active_form: str = ""


@dataclass
class PlanningState:
    """Session plan managed by TodoManager (s03)."""
    items: list[PlanItem] = field(default_factory=list)
    rounds_since_update: int = 0


@dataclass
class CompactState:
    """Context compaction bookkeeping (s06)."""
    has_compacted: bool = False
    last_summary: str = ""
    recent_files: list[str] = field(default_factory=list)

    def track_file(self, path: str) -> None:
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.append(path)
        if len(self.recent_files) > 5:
            self.recent_files[:] = self.recent_files[-5:]
