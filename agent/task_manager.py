"""Persistent task graph with dependencies (s07 pattern).

Tasks live as JSON files in .tasks/ so they survive context compression.
Completing a task automatically unblocks dependents by clearing the
completed ID from every other task's blockedBy list.
"""

import json
import time
from pathlib import Path

_VALID_STATUSES = ("pending", "in_progress", "completed")


class TaskManager:
    def __init__(self, tasks_dir: Path):
        self.dir = tasks_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._next_id = self._max_id() + 1

    def _max_id(self) -> int:
        ids = []
        for f in self.dir.glob("task_*.json"):
            try:
                ids.append(int(f.stem.split("_")[1]))
            except (IndexError, ValueError):
                pass
        return max(ids) if ids else 0

    def _path(self, task_id: int) -> Path:
        return self.dir / f"task_{task_id}.json"

    def _load(self, task_id: int) -> dict:
        p = self._path(task_id)
        if not p.exists():
            raise ValueError(f"Task {task_id} not found")
        return json.loads(p.read_text())

    def _save(self, task: dict):
        self._path(task["id"]).write_text(json.dumps(task, indent=2, ensure_ascii=False))

    def _clear_dependency(self, completed_id: int):
        for f in self.dir.glob("task_*.json"):
            task = json.loads(f.read_text())
            if completed_id in task.get("blockedBy", []):
                task["blockedBy"].remove(completed_id)
                self._save(task)

    # ── Public API ────────────────────────────────────────────────────────

    def create(self, subject: str, description: str = "") -> str:
        task = {
            "id": self._next_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "owner": "",
            "worktree": "",
            "blockedBy": [],
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self._save(task)
        self._next_id += 1
        return json.dumps(task, indent=2, ensure_ascii=False)

    def get(self, task_id: int) -> str:
        return json.dumps(self._load(task_id), indent=2, ensure_ascii=False)

    def exists(self, task_id: int) -> bool:
        return self._path(task_id).exists()

    def update(
        self,
        task_id: int,
        status: str | None = None,
        owner: str | None = None,
        add_blocked_by: list[int] | None = None,
        remove_blocked_by: list[int] | None = None,
    ) -> str:
        task = self._load(task_id)
        if status is not None:
            if status not in _VALID_STATUSES:
                raise ValueError(f"Invalid status '{status}'. Valid: {_VALID_STATUSES}")
            task["status"] = status
            if status == "completed":
                self._clear_dependency(task_id)
        if owner is not None:
            task["owner"] = owner
        if add_blocked_by:
            task["blockedBy"] = list(set(task.get("blockedBy", []) + add_blocked_by))
        if remove_blocked_by:
            task["blockedBy"] = [x for x in task.get("blockedBy", []) if x not in remove_blocked_by]
        task["updated_at"] = time.time()
        self._save(task)
        return json.dumps(task, indent=2, ensure_ascii=False)

    def bind_worktree(self, task_id: int, worktree: str, owner: str = "") -> str:
        task = self._load(task_id)
        task["worktree"] = worktree
        if owner:
            task["owner"] = owner
        if task["status"] == "pending":
            task["status"] = "in_progress"
        task["updated_at"] = time.time()
        self._save(task)
        return json.dumps(task, indent=2, ensure_ascii=False)

    def unbind_worktree(self, task_id: int) -> str:
        task = self._load(task_id)
        task["worktree"] = ""
        task["updated_at"] = time.time()
        self._save(task)
        return json.dumps(task, indent=2, ensure_ascii=False)

    def list_all(self) -> str:
        tasks = []
        for f in sorted(self.dir.glob("task_*.json"), key=lambda f: int(f.stem.split("_")[1])):
            tasks.append(json.loads(f.read_text()))
        if not tasks:
            return "No tasks."
        lines = []
        markers = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}
        for t in tasks:
            marker = markers.get(t["status"], "[?]")
            blocked = f" (blocked: {t['blockedBy']})" if t.get("blockedBy") else ""
            owner = f" owner={t['owner']}" if t.get("owner") else ""
            wt = f" wt={t['worktree']}" if t.get("worktree") else ""
            lines.append(f"{marker} #{t['id']}: {t['subject']}{blocked}{owner}{wt}")
        return "\n".join(lines)

    def list_unclaimed(self) -> list[dict]:
        """Return pending tasks with no owner and no blockers (for auto-claim)."""
        result = []
        for f in sorted(self.dir.glob("task_*.json"), key=lambda f: int(f.stem.split("_")[1])):
            task = json.loads(f.read_text())
            if (task.get("status") == "pending"
                    and not task.get("owner")
                    and not task.get("blockedBy")):
                result.append(task)
        return result
