"""Rich console helpers for consistent I/O display.

prompt_toolkit.patch_stdout 과 공존해야 하므로 rich Live 는 사용하지 않는다.
Thinking shimmer 와 subagent bullet pulse 는 REPL 레이아웃에 추가된 Window 가
`display_render_ft()` 를 매 프레임 호출하면서 prompt_toolkit 쪽에서 재렌더한다.
완료된 subagent 는 scrollback 에 ✓ 줄로 커밋되고 live 영역에서 사라진다.
"""

import math
import re
import shutil
import threading
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from prompt_toolkit.formatted_text import FormattedText, StyleAndTextTuples
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape as _rich_escape
from rich.panel import Panel
from rich.theme import Theme


# ── 라이브 리전 라인 wrap 방지 ────────────────────────────────────────────────
# 터미널 너비보다 긴 라인은 wrap 돼서 _spinner_height 계산이 어긋난다 → 입력 영역
# 과 겹치는 렌더 깨짐. render_ft 가 방출하는 모든 가변 라벨은 `_fit_width` 로
# 터미널 폭에 맞춰 잘라서 넘겨 wrap 자체를 일어나지 않게 한다.
def _char_cols(ch: str) -> int:
    """CJK/fullwidth = 2cols, 그 외 = 1col. 한글·일본어 정확히 잡음."""
    return 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1


def _visual_width(text: str) -> int:
    return sum(_char_cols(c) for c in text)


def _fit_width(text: str, max_cols: int) -> str:
    """visual width 기준으로 `text` 를 `max_cols` 이하로 잘라 `…` 덧붙임."""
    if max_cols <= 1:
        return "…"
    if _visual_width(text) <= max_cols:
        return text
    out: list[str] = []
    cols = 0
    for ch in text:
        w = _char_cols(ch)
        if cols + w > max_cols - 1:
            out.append("…")
            break
        out.append(ch)
        cols += w
    return "".join(out)


def _term_cols() -> int:
    """현재 터미널 width (column 수). 얻을 수 없으면 80 fallback."""
    try:
        return max(40, shutil.get_terminal_size((80, 24)).columns)
    except Exception:
        return 80

_theme = Theme({
    "tool.name":   "bold yellow",
    "tool.output": "dim white",
    "plan":        "bold magenta",
    "info":        "dim blue",
    "error":       "bold red",
})

console = Console(theme=_theme, highlight=False)


# ── 토큰 / 시간 추적 ──────────────────────────────────────────────────────────

_tokens_out: int = 0
_tokens_in:  int = 0
_spin_start: float = 0.0


def add_tokens(prompt: int = 0, completion: int = 0) -> None:
    global _tokens_out, _tokens_in
    _tokens_out += completion
    _tokens_in  += prompt


def reset_tokens() -> None:
    global _tokens_out, _tokens_in
    _tokens_out = 0
    _tokens_in  = 0


def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def token_snapshot() -> tuple[int, int]:
    """현재까지 누적된 (in, out) 토큰 수 — subagent 턴별 delta 계산용."""
    return _tokens_in, _tokens_out


# ── Assistant 응답 스트리밍 (라이브 리전) ─────────────────────────────────────
# 토큰을 scrollback 에 raw 로 뿌리면 markdown 포맷이 깨지므로, prompt_toolkit 의
# 라이브 영역 (_DisplayManager 의 render_ft) 에 ephemeral 하게 표시한다.
# 턴이 끝나면 라이브 영역을 비우고 REPL 이 `print_assistant(reply)` 로
# markdown 렌더된 최종본을 scrollback 에 커밋한다.

def stream_assistant_begin() -> None:
    """새 assistant 메시지 블록 시작 — 라이브 영역 스트림 버퍼 리셋."""
    _display.stream_begin()


def stream_assistant_delta(text: str) -> None:
    """모델이 뱉은 토큰 조각을 라이브 영역 버퍼에 누적. scrollback 에는 찍지 않음."""
    _display.stream_append(text)


def stream_assistant_end() -> None:
    """턴 끝 — 라이브 영역 비움. 최종 포맷된 출력은 print_assistant 가 담당."""
    _display.stream_end()


def fmt_time(seconds: float) -> str:
    return _fmt_time(seconds)


def fmt_tokens(n: int) -> str:
    return _fmt_tokens(n)


# ── 애니메이션 프레임 ─────────────────────────────────────────────────────────

_SPINNER_FRAMES: list[tuple[str, str]] = [
    ("·", "#3A3A3A"), ("✦", "#5C2E1A"), ("✶", "#8C4A2F"), ("✷", "#AA6244"),
    ("✸", "#CC785C"), ("✹", "#D4916F"), ("✺", "#E8B89A"),
    ("✻", "bold #FFCCB2"), ("✺", "#E8B89A"), ("✹", "#D4916F"),
    ("✸", "#CC785C"), ("✷", "#AA6244"), ("✶", "#8C4A2F"),
    ("✦", "#5C2E1A"), ("·", "#3A3A3A"),
]

_SHIMMER_BASE = (0xCC, 0x78, 0x5C)   # Claude Code amber
_SHIMMER_PEAK = (0xFF, 0xF0, 0xE8)   # 밝은 warm white

_COMPACT_BASE = (0x3D, 0x7D, 0xD8)   # Claude Code blue (context compress)
_COMPACT_PEAK = (0xC0, 0xDC, 0xFF)   # 밝은 blue-white

# subagent bullet pulse — amber 저조도 ↔ 고조도
_PULSE_BASE = (0x66, 0x33, 0x20)
_PULSE_PEAK = (0xFF, 0xCC, 0xAA)


def _shimmer_color(dist: float, base: tuple, peak: tuple) -> str:
    """base 색 → peak 색 보간. dist 가 0에 가까울수록 peak, 멀수록 base."""
    t = max(0.0, 1.0 - (dist / 2.5) ** 1.6)
    r = int(base[0] + (peak[0] - base[0]) * t)
    g = int(base[1] + (peak[1] - base[1]) * t)
    b = int(base[2] + (peak[2] - base[2]) * t)
    return f"#{r:02X}{g:02X}{b:02X}"


def _lerp_color(t: float, base: tuple, peak: tuple) -> str:
    """t(0..1) 로 base→peak 선형 보간."""
    t = max(0.0, min(1.0, t))
    r = int(base[0] + (peak[0] - base[0]) * t)
    g = int(base[1] + (peak[1] - base[1]) * t)
    b = int(base[2] + (peak[2] - base[2]) * t)
    return f"#{r:02X}{g:02X}{b:02X}"


# ── live 상태 매니저 (prompt_toolkit 렌더) ────────────────────────────────────
# 완료된 subagent/spinner 종료는 scrollback 에 한 줄 커밋.
# 진행 중인 것은 _active_tasks + _in_spinner 플래그만 들고, REPL 쪽 Window 가
# render_ft() 을 주기적으로 호출해서 shimmer/pulse 애니메이션을 그린다.

class _Task:
    def __init__(self, task_id: str, description: str):
        self.id          = task_id
        self.description = description
        self.start_time  = time.time()
        self.action      = "starting…"   # 현재 도구 호출 요약 (live 표시)
        self.bash_cmds   = 0
        self.bash_items  = 0


class _DisplayManager:
    def __init__(self) -> None:
        self._active_tasks: list[_Task]  = []
        self._todos:        list[dict]   = []   # [{content, status, active_form}]
        self._lock          = threading.Lock()
        self._in_spinner:   bool         = False
        self._spinner_text: str          = ""
        self._spinner_base: tuple        = _SHIMMER_BASE
        self._spinner_peak: tuple        = _SHIMMER_PEAK
        # 라이브 영역 스트리밍 — 토큰 누적은 그대로 하되 live region 에는 그리지 않는다.
        # (과거 spinner 라벨 shimmer 와 충돌해 'Thinking…' 이 깨지는 이슈 발견 → 제거)
        # 모델 레이어 `on_content_delta` 는 유지해서 나중에 별도 영역/패턴으로 활용 가능.
        self._stream_text:  str          = ""
        # 현재 진행 중인 활동 — spinner 바로 아래 `↳ <활동>` 한 줄로 표시.
        # loop.py 가 tool 호출 직전 set, 결과 확보 후 clear.
        self._activity:     str          = ""
        # Pending permission 요청 — `{summary, preview, showing_full}` dict 또는 None.
        # agent/permission.py::PermissionManager 가 request 시 push, 결정 후 clear.
        self._pending_perm: dict | None  = None

    @property
    def is_active(self) -> bool:
        # stream_text 는 더 이상 live 표시 안 하므로 is_active 판정에서 제외.
        return (
            self._in_spinner
            or bool(self._active_tasks)
            or bool(self._todos)
            or bool(self._activity)
            or self._pending_perm is not None
        )

    # ── streaming (assistant content, accumulated but NOT rendered) ─────────
    # 콜백이 호출돼도 live region 에는 그리지 않음. 최종 답은 REPL 이
    # `print_assistant(reply)` 로 markdown 렌더해서 scrollback 에 커밋한다.

    def stream_begin(self) -> None:
        with self._lock:
            self._stream_text = ""

    def stream_append(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._stream_text += text

    def stream_end(self) -> None:
        with self._lock:
            self._stream_text = ""

    # ── current activity indicator ──────────────────────────────────────────

    def set_activity(self, text: str) -> None:
        """Spinner 바로 아래 `↳ <text>` 로 표시할 현재 진행 라인 설정."""
        with self._lock:
            self._activity = text or ""

    def clear_activity(self) -> None:
        with self._lock:
            self._activity = ""

    # ── pending permission request ──────────────────────────────────────────

    def set_pending_permission(self, info: dict) -> None:
        """PermissionManager 가 승인 대기 상태 진입 시 호출.
        info = {'summary': str, 'preview': str, 'showing_full': bool}
        """
        with self._lock:
            self._pending_perm = info

    def clear_pending_permission(self) -> None:
        with self._lock:
            self._pending_perm = None

    # ── spinner interface ────────────────────────────────────────────────────

    def spinner_start(self, text: str, base: tuple, peak: tuple) -> None:
        global _spin_start
        _spin_start = time.time()
        with self._lock:
            self._in_spinner   = True
            self._spinner_text = text
            self._spinner_base = base
            self._spinner_peak = peak

    def spinner_stop(self) -> None:
        with self._lock:
            was_running = self._in_spinner
            self._in_spinner = False
        if not was_running:
            return
        elapsed = time.time() - _spin_start
        parts = [_fmt_time(elapsed)]
        total = _tokens_in + _tokens_out
        if total > 0:
            parts.append(f"↑{_fmt_tokens(_tokens_in)} ↓{_fmt_tokens(_tokens_out)}")
        console.print(f"[dim] ✻  Done ({' · '.join(parts)})[/dim]")

    def spinner_tick(self) -> None:
        pass  # no-op (UI 쪽에서 invalidate 로 재렌더)

    # ── task board interface ─────────────────────────────────────────────────

    @property
    def has_tasks(self) -> bool:
        # print_tool_call 은 항상 inline scrollback 찍고, live action 은 update_tool 이 업데이트
        return False

    def start_task(self, description: str) -> None:
        with self._lock:
            task = _Task(str(len(self._active_tasks)), description)
            self._active_tasks.append(task)

    def update_tool(self, name: str, output: str) -> None:
        """진행 중인 subagent 의 action 필드를 갱신. 활성 task 없으면 no-op."""
        with self._lock:
            if not self._active_tasks:
                return
            task = self._active_tasks[-1]   # 가장 최근(nested) task
            if name == "bash":
                lines = [ln for ln in (output or "").strip().splitlines() if ln.strip()]
                task.bash_cmds  += 1
                task.bash_items += len(lines)
                task.action = f"bash · {task.bash_cmds} cmds · {task.bash_items} lines"
            else:
                brief = (output or "")[:60].replace("\n", " ")
                task.action = f"{name}  {brief}"

    def end_task(self, result: str) -> None:
        with self._lock:
            task = self._active_tasks.pop() if self._active_tasks else None
        if task is None:
            return
        elapsed = time.time() - task.start_time
        summary = result[:90].replace("\n", " ") if result else ""
        tail = f"  [dim]{summary}[/dim]" if summary else ""
        console.print(
            f"  [bold green]✓[/bold green] [dim]{task.description} "
            f"({_fmt_time(elapsed)})[/dim]{tail}"
        )

    def reset(self) -> None:
        with self._lock:
            self._active_tasks.clear()
            self._in_spinner = False
        # 주의: todos 는 reset 으로 안 지움 — 턴 경계를 넘어 유지

    # ── todo interface ───────────────────────────────────────────────────────

    def set_todos(self, items: list[dict]) -> None:
        """TodoManager 에서 호출. items: [{content, status, active_form}, ...]"""
        with self._lock:
            self._todos = list(items)

    def clear_todos(self) -> None:
        with self._lock:
            self._todos = []

    # ── prompt_toolkit 렌더 ──────────────────────────────────────────────────

    def render_ft(self) -> FormattedText:
        parts: StyleAndTextTuples = []
        now = time.time()

        # 0) Pending permission 이 있으면 최상단에 크게 — 사용자 결정이 block.
        #    spinner/activity 는 중복 시각 노이즈라 이 모드에선 생략.
        if self._pending_perm is not None:
            info = self._pending_perm
            parts.append(("bold #FFD75F", " ⚠  "))
            parts.append(("bold #E4E4E4", info.get("summary") or ""))
            parts.append(("", "\n"))
            parts.append(("#6C6C6C", "    ─────────────  preview" +
                          ("  (full)" if info.get("showing_full") else "  (앞 15줄)") +
                          "  ─────────────"))
            for ln in (info.get("preview") or "").splitlines() or [""]:
                parts.append(("", "\n"))
                parts.append(("#E4E4E4", f"    {ln}"))
            parts.append(("", "\n"))
            parts.append(("#6C6C6C", "    " + "─" * 57))
            parts.append(("", "\n"))
            parts.append(("bold #5FD7AF", "    [y]"))
            parts.append(("#E4E4E4", " 승인   "))
            parts.append(("bold #FF8787", "[n]"))
            parts.append(("#E4E4E4", " 거부   "))
            parts.append(("bold #87AFFF", "[d]"))
            parts.append(("#E4E4E4", " 전체 diff   "))
            parts.append(("bold #FFAF5F", "[a]"))
            parts.append(("#E4E4E4", " 이 세션 내내 자동승인   "))
            parts.append(("#6C6C6C", "(3분 타임아웃)"))
            return FormattedText(parts)

        # 1) Todo 섹션 (가장 위)
        if self._todos:
            done  = sum(1 for it in self._todos if it.get("status") == "completed")
            total = len(self._todos)
            # 각 todo 라인은 `    [icon]  [label]` — 6 col 프리픽스 뒤 라벨.
            # 터미널 너비 초과 방지: 라벨을 width - 7 로 cap (wrap 금지).
            label_max = max(10, _term_cols() - 7)
            parts.append(("bold #CC785C", " ✻  "))
            parts.append(("bold", "Todo"))
            parts.append(("#6C6C6C", f"  ({done}/{total})"))
            for item in self._todos:
                status  = (item.get("status") or "pending").lower()
                content = _fit_width(item.get("content") or "", label_max)
                active  = _fit_width((item.get("active_form") or "").strip(), label_max)
                parts.append(("", "\n"))
                if status == "completed":
                    parts.append(("#5FD787", "    ☑  "))
                    parts.append(("strike #808080", content))
                elif status == "in_progress":
                    label = active or content
                    idx = int(now * 12) % len(_SPINNER_FRAMES)
                    char, color = _SPINNER_FRAMES[idx]
                    parts.append((color, f"    {char}  "))
                    cycle = 2.4
                    pos = (now % cycle) / cycle * (len(label) + 8) - 4
                    for i, ch in enumerate(label):
                        c = _shimmer_color(abs(i - pos), _SHIMMER_BASE, _SHIMMER_PEAK)
                        parts.append((c, ch))
                else:  # pending
                    parts.append(("#6C6C6C", "    ☐  "))
                    parts.append(("#808080", content))

        # 2) Thinking 스피너
        if self._in_spinner:
            if parts:
                parts.append(("", "\n"))
            idx = int(now * 12) % len(_SPINNER_FRAMES)
            char, color = _SPINNER_FRAMES[idx]
            parts.append((color, f" {char}  "))

            # 스피너 라벨 + elapsed/토큰 서브 한 줄에 맞추기 — 터미널 너비 초과 시 wrap 방지.
            elapsed = now - _spin_start
            sub_bits = [_fmt_time(elapsed)]
            tk = _tokens_in + _tokens_out
            if tk > 0:
                sub_bits.append(f"↑{_fmt_tokens(_tokens_in)} ↓{_fmt_tokens(_tokens_out)}")
            sub_str = f"  ({' · '.join(sub_bits)})"
            # 프리픽스 ` X  ` (4col) + 라벨 + sub → 여유는 term - 4 - len(sub)
            label_budget = max(10, _term_cols() - 4 - _visual_width(sub_str))
            label = _fit_width(self._spinner_text, label_budget)
            cycle = 2.4
            pos = (now % cycle) / cycle * (len(label) + 8) - 4
            for i, ch in enumerate(label):
                c = _shimmer_color(abs(i - pos), self._spinner_base, self._spinner_peak)
                parts.append((c, ch))
            parts.append(("#6C6C6C", sub_str))

            # 2b) 현재 활동 서브라인 — `    ↳ <text>`; 4+2=6 col 프리픽스.
            if self._activity:
                parts.append(("", "\n"))
                act_budget = max(10, _term_cols() - 6)
                parts.append(("#6C6C6C", f"    ↳ {_fit_width(self._activity, act_budget)}"))

        # 3) 활성 subagent — pulsing bullet + 현재 action
        for task in self._active_tasks:
            if parts:
                parts.append(("", "\n"))
            pulse = 0.5 + 0.5 * math.sin(now * 3.5)
            bullet_color = _lerp_color(pulse, _PULSE_BASE, _PULSE_PEAK)
            parts.append((f"bold {bullet_color}", "  ●  "))
            # task description + `  (Ns)` — wrap 방지를 위해 라벨 cap.
            elapsed = now - task.start_time
            suffix = f"  ({_fmt_time(elapsed)})"
            desc_budget = max(10, _term_cols() - 5 - _visual_width(suffix))
            parts.append(("bold", _fit_width(task.description, desc_budget)))
            parts.append(("#6C6C6C", suffix))
            # action 이 기본값이 아니면 다음 줄에 표시
            if task.action and task.action != "starting…":
                parts.append(("", "\n"))
                action_budget = max(10, _term_cols() - 9)  # `       ↳ ` = 9col
                parts.append(("#6C6C6C", f"       ↳ {_fit_width(task.action, action_budget)}"))

        return FormattedText(parts)


_display = _DisplayManager()


# ── REPL 용 공개 API ──────────────────────────────────────────────────────────

def display_render_ft() -> FormattedText:
    return _display.render_ft()


def display_is_active() -> bool:
    return _display.is_active


def display_set_todos(items: list[dict]) -> None:
    """TodoManager 에서 호출 — live 영역에 체크리스트 표시."""
    _display.set_todos(items)


def display_clear_todos() -> None:
    _display.clear_todos()


def set_activity(text: str) -> None:
    """spinner 바로 아래 `↳ <text>` 서브라인 설정. 현재 실행 중 tool/단계 표시용."""
    _display.set_activity(text)


def clear_activity() -> None:
    _display.clear_activity()


def set_pending_permission(info: dict) -> None:
    """permission.py::PermissionManager 가 승인 요청 시 호출 — live region 에 표시."""
    _display.set_pending_permission(info)


def clear_pending_permission() -> None:
    _display.clear_pending_permission()


# ── TaskBoard proxy (외부 API 호환 유지) ──────────────────────────────────────

class _TaskBoardProxy:
    @property
    def active(self) -> bool:
        return False  # 항상 inline

    @property
    def has_live(self) -> bool:
        return False

    def start_task(self, description: str) -> None:
        _display.start_task(description)

    def update_tool(self, name: str, output: str) -> None:
        _display.update_tool(name, output)

    def end_task(self, result: str) -> None:
        _display.end_task(result)

    def reset(self) -> None:
        _display.reset()


task_board = _TaskBoardProxy()


# ── 공개 헬퍼 ─────────────────────────────────────────────────────────────────

def print_user_prompt(label: str = ">> ") -> str:
    return console.input(f"[bold cyan]{label}[/bold cyan] ")


def print_tool_call(
    name: str,
    output: str,
    max_preview: int = 200,
    max_lines: int = 10,
) -> None:
    """Commit one tool-call bullet to scrollback, **preserving** multi-line
    structure and leading indentation of the output.

    Layout:
        ``  ⎿ <name>  <line 1>``
        ``            <line 2>``     ← continuation lines align under line 1
        ``            … (+N more lines)``

    - `max_preview` : per-line character cap (mid-line … if exceeded)
    - `max_lines`   : total lines shown; more → tail summarized as `+N more`

    전체 output 은 `state.messages` 에 그대로 보존되므로 프리뷰는 시각적 요약용.
    Subagent 내부에서 호출되면 해당 task 의 pulsing-bullet action 필드만 갱신.
    """
    if _display._active_tasks:
        _display.update_tool(name, output)
        return

    text = (output or "").rstrip()
    if not text:
        console.print(
            f"  [tool.name]⎿ {name}[/tool.name]  [tool.output](no output)[/tool.output]"
        )
        return

    raw_lines = text.splitlines() or [text]
    # per-line length cap
    lines = [
        ln if len(ln) <= max_preview else ln[:max_preview] + "…"
        for ln in raw_lines
    ]
    total = len(lines)
    truncated = total > max_lines
    if truncated:
        lines = lines[:max_lines]

    # continuation column = 2 (leading) + 2 ("⎿ ") + len(name) + 2 ("  ")
    cont = " " * (6 + len(name))

    parts = [
        f"  [tool.name]⎿ {name}[/tool.name]  "
        f"[tool.output]{_rich_escape(lines[0])}[/tool.output]"
    ]
    for ln in lines[1:]:
        parts.append(f"{cont}[tool.output]{_rich_escape(ln)}[/tool.output]")
    if truncated:
        parts.append(f"{cont}[dim]… (+{total - max_lines} more lines)[/dim]")

    console.print("\n".join(parts))


def print_subagent_start(description: str) -> None:
    task_board.start_task(description)


def print_subagent_end(result: str) -> None:
    task_board.end_task(result)


def _auto_fence_raw_trees(text: str) -> str:
    """펜스 없이 나온 ASCII 트리(├└│─ 로 시작하는 연속 줄)를 ``` 코드블록으로 감싼다.

    Markdown 표준이 연속 줄을 한 문단으로 합쳐버리기 때문에, 모델이 펜스를 빼먹으면
    rich.Markdown 단에서 트리가 한 줄로 뭉개진다. 2줄 이상 연속된 트리 라인을 묶어 펜싱.
    """
    out: list[str] = []
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        if not buf:
            return
        if len(buf) >= 2:
            out.extend(["```", *buf, "```"])
        else:
            out.extend(buf)
        buf.clear()

    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            flush()
            out.append(line)
            in_fence = not in_fence
            continue
        if in_fence:
            out.append(line)
            continue
        if stripped and stripped[0] in "├└│─":
            buf.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out)


def print_assistant(text: str) -> None:
    normalized = re.sub(r'\n{3,}', '\n\n', text.strip())
    normalized = _auto_fence_raw_trees(normalized)
    # 소프트 랩된 단일 개행은 공백으로 붙이되, fenced code block 내부는 그대로 둔다.
    # allowlist 에 리스트·헤딩·인용 외에도 테이블(|)·ASCII 트리(├└│─) 를 포함해야
    # 디렉토리 구조 스케치나 표가 한 줄로 뭉개지지 않는다.
    # (?!\A) / (?!\Z) — part 경계의 \n 은 collapse 하지 않음.
    # split 으로 잘린 part 내부 lookahead 는 다음 part(코드 펜스)를 볼 수 없으므로,
    # trailing \n 이 공백으로 바뀌면 ``` 가 줄 시작을 잃고 inline code 로 오인됨.
    parts = re.split(r'(```[\s\S]*?```)', normalized)
    for i, part in enumerate(parts):
        if i % 2 == 0:  # 코드 펜스 바깥 구간만
            parts[i] = re.sub(
                r'(?<!\n)(?<!\A)\n(?!\n)(?!\Z)(?![ \t]*[-*`#>|├└│─])',
                ' ',
                part,
            )
    normalized = "".join(parts)
    console.print(" [bold #CC785C]󰚩 [/bold #CC785C] ", end="")
    console.print(Markdown(normalized))


def print_plan(plan_text: str) -> None:
    console.print(Panel(
        plan_text,
        title="[magenta]Plan[/magenta]",
        border_style="dim magenta",
        expand=False,
    ))


def print_header(mode: str, meta: str | None = None) -> None:
    cwd = Path.cwd()
    try:
        label = "~/" + str(cwd.relative_to(Path.home()))
    except ValueError:
        label = str(cwd)
    meta_part = f"  [dim]{meta}[/dim]" if meta else ""
    console.print(f" [bold cyan]✻[/bold cyan] [bold]{mode}[/bold]  [dim]{label}[/dim]{meta_part}")


def print_info(message: str) -> None:
    console.print(f"[info]{message}[/info]")


def print_error(message: str) -> None:
    console.print(f"[error]✗[/error] {message}")


# ── 스피너 ────────────────────────────────────────────────────────────────────

@contextmanager
def _shimmer_spinner(text: str, base: tuple, peak: tuple):
    _display.spinner_start(text, base, peak)
    try:
        yield
    finally:
        _display.spinner_stop()


@contextmanager
def thinking_spinner(text: str = "Thinking…"):
    with _shimmer_spinner(text, _SHIMMER_BASE, _SHIMMER_PEAK):
        yield


@contextmanager
def compacting_spinner(text: str = "Compacting context…"):
    with _shimmer_spinner(text, _COMPACT_BASE, _COMPACT_PEAK):
        yield
