"""Git worktree isolation bound to task IDs (s12 pattern).

WorktreeManager creates git worktrees under .worktrees/<name>/,
binds each to a task, and records the full lifecycle in events.jsonl.

State machines:
  Task:     pending -> in_progress -> completed
  Worktree: absent  -> active      -> removed | kept
"""

import json
import re
import subprocess
import time
from pathlib import Path

from .task_manager import TaskManager


# ── EventBus ────────────────────────────────────────────────────────────────


class EventBus:
    def __init__(self, log_path: Path):
        self.path = log_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("")

    def emit(
        self,
        event: str,
        task: dict | None = None,
        worktree: dict | None = None,
        error: str | None = None,
    ):
        payload = {
            "event": event,
            "ts": time.time(),
            "task": task or {},
            "worktree": worktree or {},
        }
        if error:
            payload["error"] = error
        with self.path.open("a") as f:
            f.write(json.dumps(payload) + "\n")

    def list_recent(self, limit: int = 20) -> str:
        n = max(1, min(int(limit or 20), 200))
        lines = self.path.read_text().splitlines()
        items = []
        for line in lines[-n:]:
            try:
                items.append(json.loads(line))
            except Exception:
                items.append({"event": "parse_error", "raw": line})
        return json.dumps(items, indent=2)


# ── WorktreeManager ───────────────────────────────────────────────────────────


def _detect_repo_root(cwd: Path) -> Path | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return Path(r.stdout.strip()) if r.returncode == 0 else None
    except Exception:
        return None


class WorktreeManager:
    def __init__(self, repo_root: Path, tasks: TaskManager, events: EventBus):
        self.repo_root = repo_root
        self.tasks = tasks
        self.events = events
        self.dir = repo_root / ".worktrees"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        if not self.index_path.exists():
            self.index_path.write_text(json.dumps({"worktrees": []}, indent=2))
        self.git_available = self._is_git_repo()

    def _is_git_repo(self) -> bool:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.repo_root, capture_output=True, text=True, timeout=10,
            )
            return r.returncode == 0
        except Exception:
            return False

    def _git(self, args: list[str]) -> str:
        if not self.git_available:
            raise RuntimeError("Not in a git repository. worktree tools require git.")
        r = subprocess.run(
            ["git", *args], cwd=self.repo_root,
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            raise RuntimeError((r.stdout + r.stderr).strip() or f"git {' '.join(args)} failed")
        return (r.stdout + r.stderr).strip() or "(no output)"

    def _load_index(self) -> dict:
        return json.loads(self.index_path.read_text())

    def _save_index(self, data: dict):
        self.index_path.write_text(json.dumps(data, indent=2))

    def _find(self, name: str) -> dict | None:
        return next((w for w in self._load_index().get("worktrees", []) if w.get("name") == name), None)

    @staticmethod
    def _validate_name(name: str):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", name or ""):
            raise ValueError("Invalid worktree name. Use 1-40 chars: letters, numbers, ., _, -")

    # ── Public API ────────────────────────────────────────────────────────

    def create(self, name: str, task_id: int | None = None, base_ref: str = "HEAD") -> str:
        self._validate_name(name)
        if self._find(name):
            raise ValueError(f"Worktree '{name}' already exists in index")
        if task_id is not None and not self.tasks.exists(task_id):
            raise ValueError(f"Task {task_id} not found")

        path = self.dir / name
        branch = f"wt/{name}"
        self.events.emit("worktree.create.before",
                         task={"id": task_id} if task_id is not None else {},
                         worktree={"name": name, "base_ref": base_ref})
        try:
            self._git(["worktree", "add", "-b", branch, str(path), base_ref])
            entry = {
                "name": name, "path": str(path), "branch": branch,
                "task_id": task_id, "status": "active", "created_at": time.time(),
            }
            idx = self._load_index()
            idx["worktrees"].append(entry)
            self._save_index(idx)
            if task_id is not None:
                self.tasks.bind_worktree(task_id, name)
            self.events.emit("worktree.create.after",
                             task={"id": task_id} if task_id is not None else {},
                             worktree={"name": name, "path": str(path), "branch": branch, "status": "active"})
            return json.dumps(entry, indent=2)
        except Exception as exc:
            self.events.emit("worktree.create.failed",
                             task={"id": task_id} if task_id is not None else {},
                             worktree={"name": name}, error=str(exc))
            raise

    def list_all(self) -> str:
        wts = self._load_index().get("worktrees", [])
        if not wts:
            return "No worktrees in index."
        return "\n".join(
            f"[{w.get('status', '?')}] {w['name']} -> {w['path']}"
            + (f" task={w['task_id']}" if w.get("task_id") is not None else "")
            for w in wts
        )

    def run(self, name: str, command: str) -> str:
        dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
        if any(d in command for d in dangerous):
            return "Error: Dangerous command blocked"
        wt = self._find(name)
        if not wt:
            return f"Error: Unknown worktree '{name}'"
        path = Path(wt["path"])
        if not path.exists():
            return f"Error: Worktree path missing: {path}"
        try:
            r = subprocess.run(command, shell=True, cwd=path,
                               capture_output=True, text=True, timeout=300)
            out = (r.stdout + r.stderr).strip()
            return out[:50_000] if out else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Timeout (300s)"

    def remove(self, name: str, force: bool = False, complete_task: bool = False) -> str:
        wt = self._find(name)
        if not wt:
            return f"Error: Unknown worktree '{name}'"
        self.events.emit("worktree.remove.before",
                         task={"id": wt.get("task_id")} if wt.get("task_id") is not None else {},
                         worktree={"name": name, "path": wt.get("path")})
        try:
            git_args = ["worktree", "remove"]
            if force:
                git_args.append("--force")
            git_args.append(wt["path"])
            self._git(git_args)

            if complete_task and wt.get("task_id") is not None:
                tid = wt["task_id"]
                before = json.loads(self.tasks.get(tid))
                self.tasks.update(tid, status="completed")
                self.tasks.unbind_worktree(tid)
                self.events.emit("task.completed",
                                 task={"id": tid, "subject": before.get("subject", ""), "status": "completed"},
                                 worktree={"name": name})

            idx = self._load_index()
            for item in idx.get("worktrees", []):
                if item.get("name") == name:
                    item["status"] = "removed"
                    item["removed_at"] = time.time()
            self._save_index(idx)
            self.events.emit("worktree.remove.after",
                             task={"id": wt.get("task_id")} if wt.get("task_id") is not None else {},
                             worktree={"name": name, "status": "removed"})
            return f"Removed worktree '{name}'"
        except Exception as exc:
            self.events.emit("worktree.remove.failed",
                             task={"id": wt.get("task_id")} if wt.get("task_id") is not None else {},
                             worktree={"name": name}, error=str(exc))
            raise

    def keep(self, name: str) -> str:
        wt = self._find(name)
        if not wt:
            return f"Error: Unknown worktree '{name}'"
        idx = self._load_index()
        kept = None
        for item in idx.get("worktrees", []):
            if item.get("name") == name:
                item["status"] = "kept"
                item["kept_at"] = time.time()
                kept = item
        self._save_index(idx)
        self.events.emit("worktree.keep",
                         task={"id": wt.get("task_id")} if wt.get("task_id") is not None else {},
                         worktree={"name": name, "status": "kept"})
        return json.dumps(kept, indent=2) if kept else f"Error: Unknown worktree '{name}'"

    def list_events(self, limit: int = 20) -> str:
        return self.events.list_recent(limit)
