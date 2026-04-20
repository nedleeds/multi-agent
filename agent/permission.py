"""Tool execution permission gate.

파괴적 tool (`write_file`, `edit_file`, `worktree_remove`, 위험 bash) 은 모델 판단
만으로 실행되지 않도록 intercept 해서 사용자 승인을 요구한다.

Cross-thread 협조:
    agent (executor thread)          REPL (main thread)
    ────────────────────────────────────────────────────
    permissions.request(tool, args)
         │
         _display 에 pending 상태 푸시 → render_ft 가 live region 에 표시
         │
         Future 에서 blocking wait (timeout=180s)
                                     사용자 y/n/d/a 입력
                                           │
                                     REPLSession._handle_submit 이 분기해서
                                     permissions.approve() / deny() /
                                     enable_auto_session() / toggle_full_diff()
                                           │
                                     Future.set_result(...)
         │
         (approved → 실제 tool 실행) | (denied → tool_result = Error)
"""

import difflib
import re
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path

# ── 위험 패턴 (Unix + Windows PowerShell + cmd) ──────────────────────────────
# 한 regex 안에서 verbose + case-insensitive 로 한 번에 스캔.
_DANGEROUS_BASH_RE = re.compile(
    r"""(
        # ──── Unix ────
          \brm\s+-[a-zA-Z]*[rRf][a-zA-Z]*\s+[/~]   # rm -rf / or ~
        | \brm\s+--recursive                       # rm --recursive
        | \bsudo\b                                 # sudo ...
        | >\s*/dev/                                # > /dev/xxx
        | \bmv\s+\S+\s+/\s*$                       # mv X /
        | \bgit\s+reset\s+--hard                   # git reset --hard
        | \bgit\s+push\s+.*(?:--force|-f\b)        # git push --force / -f
        | \bgit\s+clean\s+-[a-zA-Z]*[fd]           # git clean -f / -fd
        | \bdd\s+if=                               # dd if=...
        | \bchmod\s+-R\s+777                       # chmod -R 777
        | \bshutdown\b                             # shutdown
        | \breboot\b                               # reboot
        | \bkill\s+-9\s+-1                         # kill -9 -1
        | \bmkfs\.                                 # mkfs.ext4 등
        # ──── Windows PowerShell ────
        | \bRemove-Item\b[^|;]*\s-Recurse\b
        | \bRemove-Item\b[^|;]*\s-Force\b
        | \bRemove-Item\b[^|;]*\s[cC]:
        | \bFormat-Volume\b
        | \bRestart-Computer\b
        | \bStop-Computer\b
        | \bStop-Process\b[^|;]*\s-Force\b
        | \bSet-ItemProperty\b[^|;]*\bHKLM:
        | \bNew-Item\b[^|;]*\s-Force\b[^|;]*[cC]:
        # ──── Windows cmd ────
        | \brmdir\s+/s
        | \bdel\s+/s
        | \bformat\s+[cC]:
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# 항상 승인을 요구하는 tool name
_ALWAYS_GATED = frozenset({"write_file", "edit_file", "worktree_remove"})

# 기본 타임아웃 — 사용자가 3분 내 미결정 시 자동 거부
DEFAULT_TIMEOUT_SEC = 180


def needs_approval(tool_name: str, args: dict) -> bool:
    """이 tool 호출이 사용자 승인을 요구하는지."""
    if tool_name in _ALWAYS_GATED:
        return True
    if tool_name == "bash":
        cmd = args.get("command", "") or ""
        return bool(_DANGEROUS_BASH_RE.search(cmd))
    return False


# ── 요약 / 미리보기 / 전체 diff 생성 ──────────────────────────────────────────

def _summary(tool_name: str, args: dict) -> str:
    """사용자에게 보여줄 한 줄 요약 (tool 과 primary arg + 규모)."""
    if tool_name == "write_file":
        path = args.get("path", "?")
        new_size = len(args.get("content", ""))
        existing = 0
        try:
            p = Path(path)
            if p.exists():
                existing = p.stat().st_size
        except OSError:
            pass
        if existing:
            delta = new_size - existing
            sign = "+" if delta >= 0 else ""
            return (
                f"write_file  {path}  ({new_size:,} bytes, 기존 {existing:,} → {sign}{delta:,})"
            )
        return f"write_file  {path}  ({new_size:,} bytes, 새 파일)"
    if tool_name == "edit_file":
        return f"edit_file  {args.get('path', '?')}"
    if tool_name == "worktree_remove":
        flags = []
        if args.get("force"):
            flags.append("--force")
        if args.get("complete_task"):
            flags.append("complete_task=true")
        tail = "  " + " ".join(flags) if flags else ""
        return f"worktree_remove  {args.get('name', '?')}{tail}"
    if tool_name == "bash":
        cmd = (args.get("command", "") or "").replace("\n", " ")
        return f"bash ⚠ 위험 패턴 감지  {cmd[:90]}"
    return f"{tool_name}  {args}"


def _preview(tool_name: str, args: dict, max_lines: int = 15) -> str:
    """라이브 영역에 노출할 축약 미리보기."""
    if tool_name == "write_file":
        content = args.get("content", "") or ""
        lines = content.splitlines() or [content]
        total = len(lines)
        shown = lines[:max_lines]
        preview = "\n".join(shown)
        if total > max_lines:
            preview += f"\n… (+{total - max_lines} more lines — press [d] for full)"
        return preview
    if tool_name == "edit_file":
        old = args.get("old_text", "") or ""
        new = args.get("new_text", "") or ""
        diff = list(difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile="before", tofile="after", lineterm="",
        ))
        if not diff:
            return "(no diff)"
        if len(diff) > max_lines:
            return "\n".join(diff[:max_lines]) + f"\n… (+{len(diff) - max_lines} more diff lines — [d] for full)"
        return "\n".join(diff)
    if tool_name == "bash":
        return args.get("command", "") or ""
    if tool_name == "worktree_remove":
        return (
            f"Remove git worktree '{args.get('name')}'"
            + (" with --force" if args.get("force") else "")
            + (" and mark bound task as completed" if args.get("complete_task") else "")
        )
    return str(args)[:400]


def _full_preview(tool_name: str, args: dict) -> str:
    """[d] 키로 확장한 전체 미리보기 (여전히 터미널 친화적으로 최대 80줄 cap)."""
    if tool_name == "write_file":
        content = args.get("content", "") or ""
        lines = content.splitlines() or [content]
        if len(lines) > 80:
            return "\n".join(lines[:80]) + f"\n… (+{len(lines) - 80} more lines — content too long for live preview)"
        return content
    if tool_name == "edit_file":
        old = args.get("old_text", "") or ""
        new = args.get("new_text", "") or ""
        diff = list(difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile="before", tofile="after", lineterm="",
        ))
        if len(diff) > 80:
            return "\n".join(diff[:80]) + f"\n… (+{len(diff) - 80} more diff lines)"
        return "\n".join(diff) or "(no diff)"
    return _preview(tool_name, args, max_lines=80)


# ── 요청 상태 ────────────────────────────────────────────────────────────────

@dataclass
class PermissionRequest:
    tool_name: str
    args: dict
    summary: str
    preview: str
    future: Future
    showing_full: bool = False


class PermissionManager:
    """Agent executor ↔ REPL UI 승인 브릿지.

    `request()` 는 agent 쪽에서 blocking 호출이고, REPL 쪽에서 `approve()` /
    `deny()` / `enable_auto_session()` / `toggle_full_diff()` 가 그 Future 를
    완료시킨다. `_display` 모듈 상태에 pending info 를 push/pop 해서 live region
    에 즉시 렌더.
    """

    def __init__(
        self,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        auto_approve_all: bool = False,
    ) -> None:
        self._lock = threading.Lock()
        self._pending: PermissionRequest | None = None
        self._auto_approved: set[str] = set()
        self._timeout_sec = timeout_sec
        # 비대화형 환경 (eval.py, CI) 용 — 모든 요청을 즉시 승인.
        self._auto_approve_all = auto_approve_all

    # ── agent 측 ─────────────────────────────────────────────────────────

    def request(self, tool_name: str, args: dict) -> tuple[bool, str]:
        """agent executor 에서 호출. (approved, reason) 반환.

        승인되면 approved=True + "approved" / "auto-approved (...)" 를 리턴,
        거부·타임아웃이면 approved=False + 이유 문자열.
        """
        if self._auto_approve_all:
            return True, "auto-approved (non-interactive mode)"
        if tool_name in self._auto_approved:
            return True, "auto-approved (session)"

        # 지연 임포트 — agent/permission.py ↔ utils/console.py 순환 피함
        from utils.console import clear_pending_permission, set_pending_permission

        fut: Future = Future()
        req = PermissionRequest(
            tool_name=tool_name,
            args=args,
            summary=_summary(tool_name, args),
            preview=_preview(tool_name, args),
            future=fut,
        )
        with self._lock:
            self._pending = req
        set_pending_permission({
            "summary":  req.summary,
            "preview":  req.preview,
            "showing_full": False,
        })

        try:
            result = fut.result(timeout=self._timeout_sec)
        except Exception:
            result = "timeout"
        finally:
            with self._lock:
                self._pending = None
            clear_pending_permission()

        if result == "approve":
            return True, "approved"
        if result == "auto_session":
            with self._lock:
                self._auto_approved.add(tool_name)
            return True, f"auto-approved (this session, all `{tool_name}` calls)"
        if result == "timeout":
            return False, f"user approval timeout ({int(self._timeout_sec)}s)"
        if isinstance(result, str) and result.startswith("deny"):
            reason = result[5:]
            return False, reason or "user declined"
        return False, f"unknown decision: {result!r}"

    # ── REPL 측 ──────────────────────────────────────────────────────────

    def has_pending(self) -> bool:
        with self._lock:
            return self._pending is not None

    def approve(self) -> bool:
        with self._lock:
            req = self._pending
        if req and not req.future.done():
            req.future.set_result("approve")
            return True
        return False

    def deny(self, reason: str = "") -> bool:
        with self._lock:
            req = self._pending
        if req and not req.future.done():
            req.future.set_result(f"deny:{reason}")
            return True
        return False

    def enable_auto_session(self) -> bool:
        with self._lock:
            req = self._pending
        if req and not req.future.done():
            req.future.set_result("auto_session")
            return True
        return False

    def toggle_full_diff(self) -> bool:
        """[d] 키 — 미리보기 확장/축소 토글. Future 는 건드리지 않음."""
        from utils.console import set_pending_permission

        with self._lock:
            req = self._pending
            if not req:
                return False
            req.showing_full = not req.showing_full
            preview = _full_preview(req.tool_name, req.args) if req.showing_full else _preview(req.tool_name, req.args)
        set_pending_permission({
            "summary":  req.summary,
            "preview":  preview,
            "showing_full": req.showing_full,
        })
        return True
