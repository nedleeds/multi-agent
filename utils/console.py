"""Rich console helpers for consistent I/O display.

prompt_toolkit.patch_stdout 과 공존해야 하므로 rich Live 는 사용하지 않는다.
Thinking shimmer 와 subagent bullet pulse 는 REPL 레이아웃에 추가된 Window 가
`display_render_ft()` 를 매 프레임 호출하면서 prompt_toolkit 쪽에서 재렌더한다.
완료된 subagent 는 scrollback 에 ✓ 줄로 커밋되고 live 영역에서 사라진다.
"""

import math
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from prompt_toolkit.formatted_text import FormattedText, StyleAndTextTuples
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.theme import Theme

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

    @property
    def is_active(self) -> bool:
        return self._in_spinner or bool(self._active_tasks) or bool(self._todos)

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

        # 1) Todo 섹션 (가장 위)
        if self._todos:
            done  = sum(1 for it in self._todos if it.get("status") == "completed")
            total = len(self._todos)
            parts.append(("bold #CC785C", " ✻  "))
            parts.append(("bold", "Todo"))
            parts.append(("#6C6C6C", f"  ({done}/{total})"))
            for item in self._todos:
                status  = (item.get("status") or "pending").lower()
                content = item.get("content") or ""
                active  = (item.get("active_form") or "").strip()
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

            label = self._spinner_text
            cycle = 2.4
            pos = (now % cycle) / cycle * (len(label) + 8) - 4
            for i, ch in enumerate(label):
                c = _shimmer_color(abs(i - pos), self._spinner_base, self._spinner_peak)
                parts.append((c, ch))

            elapsed = now - _spin_start
            sub = [_fmt_time(elapsed)]
            total = _tokens_in + _tokens_out
            if total > 0:
                sub.append(f"↑{_fmt_tokens(_tokens_in)} ↓{_fmt_tokens(_tokens_out)}")
            parts.append(("#6C6C6C", f"  ({' · '.join(sub)})"))

        # 3) 활성 subagent — pulsing bullet + 현재 action
        for task in self._active_tasks:
            if parts:
                parts.append(("", "\n"))
            pulse = 0.5 + 0.5 * math.sin(now * 3.5)
            bullet_color = _lerp_color(pulse, _PULSE_BASE, _PULSE_PEAK)
            parts.append((f"bold {bullet_color}", "  ●  "))
            parts.append(("bold", task.description))
            elapsed = now - task.start_time
            parts.append(("#6C6C6C", f"  ({_fmt_time(elapsed)})"))
            # action 이 기본값이 아니면 다음 줄에 표시
            if task.action and task.action != "starting…":
                parts.append(("", "\n"))
                parts.append(("#6C6C6C", f"       ↳ {task.action}"))

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


def print_tool_call(name: str, output: str, max_preview: int = 200) -> None:
    # 1) scrollback — subagent 내부면 한 단계 더 들여쓰기
    preview = output[:max_preview] + ("…" if len(output) > max_preview else "")
    indent = "      " if _display._active_tasks else "  "
    console.print(
        f"{indent}[tool.name]⎿ {name}[/tool.name]  [tool.output]{preview}[/tool.output]"
    )
    # 2) 활성 subagent 가 있으면 그 bullet 의 action 을 live 업데이트
    _display.update_tool(name, output)


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
