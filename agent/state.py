"""Shared state dataclasses for the agent loop."""

from dataclasses import dataclass, field
from threading import Event


@dataclass
class LoopState:
    """Minimal loop state: conversation history + turn tracking (s01)."""
    messages: list[dict]
    turn_count: int = 1
    transition_reason: str | None = None
    # 연속 finish_reason=="length" 발생 횟수 — 무한 continue 방지용 카운터
    length_continues: int = 0
    # Cooperative cancellation — agent_loop / run_one_turn check between
    # turns and between tool dispatches. None = no cancellation wired.
    cancel_event: Event | None = None
    # 최근 실행된 tool call 의 signature(`name(args_json)`) 링버퍼.
    # 3회 연속 동일 → 스키마 불일치·환각 반복 판정, 해당 호출 skip + 진단 반환.
    recent_tool_sigs: list[str] = field(default_factory=list)
    # read_file cycling 방지 — 턴 내에서 path 별 호출 횟수 + 이미 본 (limit, offset) 집합.
    # 같은 (limit, offset) 재호출 → "이미 읽음" 진단으로 즉시 차단.
    # 같은 path 총 5회+ → "cycling 경고" 로 synthesize/grep 전환 유도.
    file_read_counts: dict[str, int] = field(default_factory=dict)
    file_read_seen: dict[str, set] = field(default_factory=dict)
    # 이 턴 내에서 `todo` tool 이 호출됐는지. 턴 종료 시 orchestrator 가 plan 감사에 사용.
    todo_called: bool = False


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
