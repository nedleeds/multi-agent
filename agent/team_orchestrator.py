"""TeamOrchestratorAgent: s07-s12 patterns wired into a single run() interface.

  ┌──────────────────────────────────────────────────────────────────┐
  │  TeamOrchestratorAgent                                           │
  │                                                                  │
  │  main_model ──► loop.py  ◄── TEAM_ORCHESTRATOR_TOOLS             │
  │                    │                                             │
  │  extra_handlers:   │                                             │
  │    task_create/update/list/get  ──► TaskManager   (s07)          │
  │    background_run/status        ──► BackgroundManager (s08)      │
  │    spawn_teammate               ──► TeammateManager  (s09-s11)   │
  │    send_message / read_inbox /  ──► MessageBus                   │
  │      broadcast / list_team                                       │
  │    request_shutdown / respond / ──► protocols        (s10)       │
  │      submit_plan / review_plan                                   │
  │    worktree_create/list/run/    ──► WorktreeManager  (s12)       │
  │      keep/remove/events                                          │
  │    compact                      ──► compact.py       (s06)       │
  └──────────────────────────────────────────────────────────────────┘
"""

import json
import threading
from pathlib import Path

from model.base import BaseLLM
from tools import definitions
from tools.registry import ToolRegistry
from utils.console import print_info

from .background import BackgroundManager
from .compact import CONTEXT_LIMIT, compact_history, estimate_size, micro_compact
from .loop import agent_loop
from .state import CompactState, LoopState
from .task_manager import TaskManager
from .team import MessageBus, TeammateManager
from .worktree import EventBus, WorktreeManager, _detect_repo_root

_WORKDIR = Path.cwd()
_REPO_ROOT = _detect_repo_root(_WORKDIR) or _WORKDIR


class TeamOrchestratorAgent:
    def __init__(
        self,
        main_model: BaseLLM,
        sub_model: BaseLLM,
    ):
        self.main_model = main_model
        self.sub_model = sub_model
        self.registry = ToolRegistry()
        self.compact_state = CompactState()
        self.history: list[dict] = []

        # s07
        self.tasks = TaskManager(_REPO_ROOT / ".tasks")
        # s08
        self.bg = BackgroundManager()
        # s09-s11
        inbox_dir = _REPO_ROOT / ".team" / "inbox"
        self.bus = MessageBus(inbox_dir)
        self.team = TeammateManager(
            team_dir=_REPO_ROOT / ".team",
            bus=self.bus,
            tasks=self.tasks,
            model=sub_model,
            workdir=_WORKDIR,
        )
        # s12
        events = EventBus(_REPO_ROOT / ".worktrees" / "events.jsonl")
        self.worktrees = WorktreeManager(_REPO_ROOT, self.tasks, events)

        self.cancel_event = threading.Event()
        self._extra_handlers = self._build_handlers()

    def cancel(self) -> None:
        self.cancel_event.set()

    # ── System prompt ─────────────────────────────────────────────────────────

    def _system_prompt(self) -> str:
        return (
            f"You are a team lead coding agent at {_WORKDIR}.\n"
            "Always respond in the same language the user writes in.\n"
            "Use task tools (task_create/update/list/get) to plan persistent work.\n"
            "Use background_run for slow commands; results arrive as notifications.\n"
            "Use spawn_teammate to delegate to persistent worker agents.\n"
            "Use worktree tools to run parallel or risky work in isolated directories.\n"
            "Use compact ONLY when the conversation is genuinely too long to continue.\n\n"
            "## After making code changes\n"
            "MANDATORY: After every edit_file or write_file, immediately run bash to verify:\n"
            "  uv run python -c 'import <module>'  # checks for syntax/import errors\n"
            "Do NOT explain that 'Python reloads on restart' or similar — just run the command. "
            "Only report success after the bash output confirms no errors.\n\n"
            "## When a command fails\n"
            "Never ask the user how to proceed on command errors. Try alternatives yourself:\n"
            "- Command not found → try `uv run <cmd>` or `python -m <cmd>`\n"
            "- Import error → check `uv sync` or inspect installed packages\n"
            "Exhaust at least 2-3 alternatives before telling the user you're blocked.\n\n"
            f"## Python environment\n"
            f"This project uses `uv`. Always run Python commands as `uv run python` or `uv run <tool>` at {_WORKDIR}.\n"
        )

    # ── Handler map ───────────────────────────────────────────────────────────

    def _build_handlers(self) -> dict:
        t = self.tasks
        bg = self.bg
        bus = self.bus
        team = self.team
        wt = self.worktrees

        return {
            # s07
            "task_create":  lambda subject, description="": t.create(subject, description),
            "task_list":    lambda: t.list_all(),
            "task_get":     lambda task_id: t.get(task_id),
            "task_update":  lambda task_id, status=None, owner=None,
                                   add_blocked_by=None, remove_blocked_by=None: t.update(
                                task_id, status, owner, add_blocked_by, remove_blocked_by
                            ),
            # s08
            "background_run":    lambda command: bg.run(command),
            "background_status": lambda: bg.status(),
            # s09
            "spawn_teammate":    lambda name, role, prompt: team.spawn(name, role, prompt),
            "send_message":      lambda to, content, type="message": bus.send("lead", to, content, type),
            "read_inbox":        lambda: json.dumps(bus.read_inbox("lead")),
            "broadcast_message": lambda content: bus.broadcast(
                "lead", content, [m["name"] for m in team.config.get("members", [])]
            ),
            "list_team":         lambda: team.list_team(),
            # s10
            "request_shutdown":       lambda teammate: team.request_shutdown(teammate),
            "respond_shutdown":       lambda request_id, approve, reason="": team.respond_shutdown(request_id, approve, reason),
            "submit_plan":            lambda from_name, plan: team.submit_plan(from_name, plan),
            "review_plan":            lambda request_id, approve, feedback="": team.review_plan(request_id, approve, feedback),
            "list_shutdown_requests": lambda: team.list_shutdown_requests(),
            "list_plan_requests":     lambda: team.list_plan_requests(),
            # s12
            "worktree_create": lambda name, task_id=None, base_ref="HEAD": wt.create(name, task_id, base_ref),
            "worktree_list":   lambda: wt.list_all(),
            "worktree_run":    lambda name, command: wt.run(name, command),
            "worktree_keep":   lambda name: wt.keep(name),
            "worktree_remove": lambda name, force=False, complete_task=False: wt.remove(name, force, complete_task),
            "worktree_events": lambda limit=20: wt.list_events(limit),
            # s06
            "compact": self._handle_compact,
        }

    # ── Compact handler ───────────────────────────────────────────────────────

    def _handle_compact(self, focus: str | None = None) -> str:
        if estimate_size(self.history) < CONTEXT_LIMIT // 2:
            return "Not needed. Stop calling tools and reply to the user directly."
        self.history[:] = compact_history(
            self.history, self.compact_state, self.main_model, focus=focus
        )
        return "Conversation compacted."

    # ── Main entry point ──────────────────────────────────────────────────────

    def run(self, user_input: str) -> str:
        self.cancel_event.clear()
        self.history.append({"role": "user", "content": user_input})

        # Drain background notifications before calling the model
        notifs = self.bg.drain()
        if notifs:
            notif_text = "\n".join(f"[bg:{n['task_id']}] {n['result']}" for n in notifs)
            self.history.append({
                "role": "user",
                "content": f"<background-results>\n{notif_text}\n</background-results>",
            })

        # Apply micro-compact; full compact if still over limit
        self.history[:] = micro_compact(self.history)
        if estimate_size(self.history) > CONTEXT_LIMIT:
            print_info("[auto-compact]")
            self.history[:] = compact_history(
                self.history, self.compact_state, self.main_model
            )

        state = LoopState(messages=self.history, cancel_event=self.cancel_event)
        agent_loop(
            state=state,
            model=self.main_model,
            tools=definitions.TEAM_ORCHESTRATOR_TOOLS,
            registry=self.registry,
            system=self._system_prompt(),
            extra_handlers=self._extra_handlers,
        )

        for msg in reversed(self.history):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return ""
