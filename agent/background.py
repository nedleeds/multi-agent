"""Background task execution with notification queue (s08 pattern).

Slow commands run in daemon threads; results are enqueued and drained
before each LLM call so the model learns about completions naturally.
"""

import subprocess
import threading
import uuid
from pathlib import Path

WORKDIR = Path.cwd()
_DANGEROUS = ["rm -rf /", "sudo rm", "shutdown", "reboot", "> /dev/"]


class BackgroundManager:
    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._queue: list[dict] = []
        self._lock = threading.Lock()

    def run(self, command: str) -> str:
        if any(d in command for d in _DANGEROUS):
            return "Error: Dangerous command blocked"
        task_id = str(uuid.uuid4())[:8]
        with self._lock:
            self._tasks[task_id] = {"status": "running", "command": command}
        t = threading.Thread(target=self._execute, args=(task_id, command), daemon=True)
        t.start()
        return f"Background task {task_id} started: {command!r}"

    def status(self) -> str:
        with self._lock:
            if not self._tasks:
                return "No background tasks."
            lines = [f"{tid}: {info['status']} — {info['command']!r}"
                     for tid, info in self._tasks.items()]
        return "\n".join(lines)

    def drain(self) -> list[dict]:
        """Return and clear all pending notifications."""
        with self._lock:
            notifs = self._queue[:]
            self._queue.clear()
        return notifs

    def _execute(self, task_id: str, command: str):
        try:
            r = subprocess.run(
                command, shell=True, cwd=WORKDIR,
                capture_output=True, text=True, timeout=300,
            )
            output = (r.stdout + r.stderr).strip()[:50_000]
        except subprocess.TimeoutExpired:
            output = "Error: Timeout (300s)"
        except Exception as exc:
            output = f"Error: {exc}"
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = "done"
            self._queue.append({"task_id": task_id, "result": output[:500]})
