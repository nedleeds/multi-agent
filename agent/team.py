"""Agent team: persistent teammates, JSONL inboxes, protocols, autonomy.

Combines s09 (teams), s10 (shutdown/plan protocols), s11 (autonomous agents).

Architecture:
  TeammateManager  — spawn, lifecycle, config.json roster
  MessageBus       — append-only JSONL inboxes, drain-on-read
  Protocols        — shutdown + plan approval with request_id correlation
  Autonomy (s11)   — idle polling: inbox → task board → auto-claim → work
"""

import json
import threading
import time
import uuid
from pathlib import Path

from model.base import BaseLLM
from utils.console import print_info
from utils.messages import normalize_messages

from .task_manager import TaskManager

POLL_INTERVAL = 5
IDLE_TIMEOUT = 60

VALID_MSG_TYPES = {
    "message",
    "broadcast",
    "shutdown_request",
    "shutdown_response",
    "plan_approval_response",
}

_claim_lock = threading.Lock()


# ── MessageBus ───────────────────────────────────────────────────────────────


class MessageBus:
    def __init__(self, inbox_dir: Path):
        self.dir = inbox_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def send(
        self,
        sender: str,
        to: str,
        content: str,
        msg_type: str = "message",
        extra: dict | None = None,
    ) -> str:
        if msg_type not in VALID_MSG_TYPES:
            return f"Error: Invalid type '{msg_type}'. Valid: {VALID_MSG_TYPES}"
        msg = {"type": msg_type, "from": sender, "content": content, "timestamp": time.time()}
        if extra:
            msg.update(extra)
        with open(self.dir / f"{to}.jsonl", "a") as f:
            f.write(json.dumps(msg) + "\n")
        return f"Sent {msg_type} to {to}"

    def read_inbox(self, name: str) -> list[dict]:
        p = self.dir / f"{name}.jsonl"
        if not p.exists():
            return []
        msgs = [json.loads(line) for line in p.read_text().strip().splitlines() if line]
        p.write_text("")  # drain
        return msgs

    def broadcast(self, sender: str, content: str, members: list[str]) -> str:
        count = sum(1 for name in members if name != sender and not self.send(sender, name, content, "broadcast").startswith("Error"))
        return f"Broadcast to {count} teammates"


# ── TeammateManager ───────────────────────────────────────────────────────────


class TeammateManager:
    def __init__(
        self,
        team_dir: Path,
        bus: MessageBus,
        tasks: TaskManager,
        model: BaseLLM,
        workdir: Path,
    ):
        self.dir = team_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.bus = bus
        self.tasks = tasks
        self.model = model
        self.workdir = workdir
        self.config_path = team_dir / "config.json"
        self.config = self._load_config()
        self.threads: dict[str, threading.Thread] = {}

        # Protocol trackers (shutdown + plan approval)
        self._shutdown_requests: dict[str, dict] = {}
        self._plan_requests: dict[str, dict] = {}
        self._tracker_lock = threading.Lock()

    # ── Config helpers ──────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        if self.config_path.exists():
            return json.loads(self.config_path.read_text())
        return {"team_name": "default", "members": []}

    def _save_config(self):
        self.config_path.write_text(json.dumps(self.config, indent=2))

    def _find(self, name: str) -> dict | None:
        return next((m for m in self.config["members"] if m["name"] == name), None)

    def _set_status(self, name: str, status: str):
        m = self._find(name)
        if m:
            m["status"] = status
            self._save_config()

    def list_team(self) -> str:
        members = self.config.get("members", [])
        if not members:
            return "No teammates."
        return "\n".join(f"  {m['name']} ({m['role']}): {m['status']}" for m in members)

    # ── Spawn ───────────────────────────────────────────────────────────────

    def spawn(self, name: str, role: str, prompt: str) -> str:
        member = self._find(name)
        if member:
            if member["status"] not in ("idle", "shutdown"):
                return f"Error: '{name}' is currently {member['status']}"
            member.update({"status": "working", "role": role})
        else:
            member = {"name": name, "role": role, "status": "working"}
            self.config["members"].append(member)
        self._save_config()

        t = threading.Thread(
            target=self._loop, args=(name, role, prompt), daemon=True
        )
        self.threads[name] = t
        t.start()
        return f"Spawned '{name}' (role: {role})"

    # ── Teammate loop (s09 + s10 + s11) ────────────────────────────────────

    def _loop(self, name: str, role: str, prompt: str):
        team_name = self.config.get("team_name", "default")
        identity_block = (
            f"You are '{name}', role: {role}, team: {team_name}.\n"
            f"Working directory: {self.workdir}\n"
            "Use send_message to communicate. Use idle when you have nothing more to do."
        )
        tools = self._teammate_tools()

        while True:
            # WORK PHASE
            messages: list[dict] = [{"role": "user", "content": prompt}]
            idle_requested = False

            for _ in range(50):
                # Re-inject identity after compression
                if len(messages) <= 3:
                    messages = [
                        {"role": "user", "content": f"<identity>{identity_block}</identity>"},
                        {"role": "assistant", "content": f"I am {name}. Continuing."},
                    ] + messages

                # Drain inbox
                inbox = self.bus.read_inbox(name)
                if inbox:
                    messages.append({"role": "user", "content": f"<inbox>{json.dumps(inbox)}</inbox>"})

                api_messages = normalize_messages(
                    [{"role": "system", "content": identity_block}] + messages
                )
                try:
                    response = self.model.chat(api_messages, tools=tools or None)
                except Exception as exc:
                    print_info(f"[{name}] model error: {exc}")
                    break

                choice = response.choices[0]
                msg = choice.message

                assistant_entry: dict = {"role": "assistant", "content": msg.content}
                if msg.tool_calls:
                    assistant_entry["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": getattr(getattr(tc, "function", None), "name", ""),
                                "arguments": getattr(getattr(tc, "function", None), "arguments", "{}"),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                messages.append(assistant_entry)

                has_tools = bool(msg.tool_calls)
                wants_tools = choice.finish_reason in ("tool_calls", "stop") and has_tools
                if not wants_tools:
                    break

                for tc in (msg.tool_calls or []):
                    tc_func = getattr(tc, "function", None)
                    if tc_func is None:
                        continue
                    tc_name = tc_func.name
                    try:
                        args = json.loads(tc_func.arguments)
                    except Exception:
                        args = {}

                    if tc_name == "idle":
                        idle_requested = True
                        output = f"'{name}' entering idle mode."
                    else:
                        output = self._exec(name, tc_name, args)

                    print_info(f"  [{name}] {tc_name}: {str(output)[:120]}")
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(output)})

                if idle_requested:
                    break

            # IDLE PHASE (s11)
            self._set_status(name, "idle")
            resume = self._idle_poll(name, messages)
            if not resume:
                self._set_status(name, "shutdown")
                return
            self._set_status(name, "working")

    def _idle_poll(self, name: str, messages: list[dict]) -> bool:
        for _ in range(IDLE_TIMEOUT // POLL_INTERVAL):
            time.sleep(POLL_INTERVAL)
            inbox = self.bus.read_inbox(name)
            if inbox:
                messages.append({"role": "user", "content": f"<inbox>{json.dumps(inbox)}</inbox>"})
                return True
            unclaimed = self.tasks.list_unclaimed()
            if unclaimed:
                task = unclaimed[0]
                with _claim_lock:
                    # Re-check under lock to avoid double-claim
                    fresh = self.tasks.list_unclaimed()
                    if fresh:
                        self.tasks.update(task["id"], owner=name, status="in_progress")
                        messages.append({
                            "role": "user",
                            "content": f"<auto-claimed>Task #{task['id']}: {task['subject']}</auto-claimed>",
                        })
                        return True
        return False  # timeout → shutdown

    # ── Protocol: shutdown (s10) ────────────────────────────────────────────

    def request_shutdown(self, teammate: str) -> str:
        req_id = str(uuid.uuid4())[:8]
        with self._tracker_lock:
            self._shutdown_requests[req_id] = {"target": teammate, "status": "pending"}
        self.bus.send("lead", teammate, "Please shut down gracefully.",
                      "shutdown_request", {"request_id": req_id})
        return f"Shutdown request {req_id} sent to '{teammate}' (status: pending)"

    def respond_shutdown(self, request_id: str, approve: bool, reason: str = "") -> str:
        with self._tracker_lock:
            req = self._shutdown_requests.get(request_id)
        if not req:
            return f"Error: Unknown shutdown request {request_id}"
        req["status"] = "approved" if approve else "rejected"
        self.bus.send(req["target"], "lead", reason or ("Approved." if approve else "Rejected."),
                      "shutdown_response", {"request_id": request_id, "approve": approve})
        if approve:
            self._set_status(req["target"], "shutdown")
        return f"Shutdown {'approved' if approve else 'rejected'} for {req['target']} (req: {request_id})"

    def list_shutdown_requests(self) -> str:
        with self._tracker_lock:
            if not self._shutdown_requests:
                return "No shutdown requests."
            return json.dumps(self._shutdown_requests, indent=2)

    # ── Protocol: plan approval (s10) ──────────────────────────────────────

    def submit_plan(self, from_name: str, plan: str) -> str:
        req_id = str(uuid.uuid4())[:8]
        with self._tracker_lock:
            self._plan_requests[req_id] = {"from": from_name, "plan": plan, "status": "pending"}
        self.bus.send(from_name, "lead", plan, "message",
                      {"request_id": req_id, "plan_review_requested": True})
        return f"Plan request {req_id} submitted by '{from_name}'"

    def review_plan(self, request_id: str, approve: bool, feedback: str = "") -> str:
        with self._tracker_lock:
            req = self._plan_requests.get(request_id)
        if not req:
            return f"Error: Unknown plan request {request_id}"
        req["status"] = "approved" if approve else "rejected"
        self.bus.send("lead", req["from"], feedback or ("Plan approved." if approve else "Plan rejected."),
                      "plan_approval_response", {"request_id": request_id, "approve": approve})
        return f"Plan {'approved' if approve else 'rejected'} for '{req['from']}' (req: {request_id})"

    def list_plan_requests(self) -> str:
        with self._tracker_lock:
            if not self._plan_requests:
                return "No plan requests."
            return json.dumps(self._plan_requests, indent=2)

    # ── Tool dispatch for teammates ─────────────────────────────────────────

    def _exec(self, name: str, tool_name: str, args: dict) -> str:
        import subprocess
        workdir = self.workdir

        def safe(p: str) -> Path:
            resolved = (workdir / p).resolve()
            if not resolved.is_relative_to(workdir):
                raise ValueError(f"Path escapes workspace: {p}")
            return resolved

        handlers = {
            "bash": lambda: (
                subprocess.run(args["command"], shell=True, cwd=workdir,
                               capture_output=True, text=True, timeout=120)
            ),
            "read_file": lambda: open(safe(args["path"])).read()[:50_000],
            "write_file": lambda: (
                safe(args["path"]).parent.mkdir(parents=True, exist_ok=True) or
                safe(args["path"]).write_text(args["content"]) or
                f"Wrote {len(args['content'])} bytes"
            ),
            "send_message": lambda: self.bus.send(
                name, args["to"], args["content"], args.get("type", "message")
            ),
            "read_inbox": lambda: json.dumps(self.bus.read_inbox(args.get("name", name))),
            "task_list": lambda: self.tasks.list_all(),
            "task_update": lambda: self.tasks.update(
                args["task_id"], args.get("status"), args.get("owner")
            ),
            "shutdown_response": lambda: self.respond_shutdown(
                args["request_id"], args["approve"], args.get("reason", "")
            ),
        }

        handler = handlers.get(tool_name)
        if handler is None:
            return f"Unknown tool: {tool_name}"
        try:
            result = handler()
            if hasattr(result, "stdout"):  # subprocess result
                out = (result.stdout + result.stderr).strip()
                return out[:50_000] if out else "(no output)"
            return str(result)
        except Exception as exc:
            return f"Error: {exc}"

    def _teammate_tools(self) -> list[dict]:
        """OpenAI-format tool schemas for teammate agent loops."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "Run a shell command.",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read file contents.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write content to a file.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "send_message",
                    "description": "Send a message to another teammate or lead.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to": {"type": "string"},
                            "content": {"type": "string"},
                            "type": {"type": "string", "enum": list(VALID_MSG_TYPES)},
                        },
                        "required": ["to", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_inbox",
                    "description": "Read and drain your inbox messages.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "task_list",
                    "description": "List all tasks on the shared task board.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "task_update",
                    "description": "Update a task's status or owner.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task_id": {"type": "integer"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                            "owner": {"type": "string"},
                        },
                        "required": ["task_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "shutdown_response",
                    "description": "Respond to a shutdown request from the lead.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "request_id": {"type": "string"},
                            "approve": {"type": "boolean"},
                            "reason": {"type": "string"},
                        },
                        "required": ["request_id", "approve"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "idle",
                    "description": "Signal that you have no more work to do right now. You will wait for new tasks or messages.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]
