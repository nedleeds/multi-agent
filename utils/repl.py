"""Claude Code–style interactive REPL.

prompt_toolkit.Application을 항상 실행 상태로 유지하고,
agent 실행은 백그라운드 executor 에서 돌리면서 patch_stdout 으로 출력을 UI 위쪽에
흘려서, 상태줄 + 입력창이 agent 실행 중에도 하단에 계속 떠 있게 한다.
"""

import asyncio
import os
import re
import subprocess
import time
from typing import Callable

from prompt_toolkit import Application
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText, StyleAndTextTuples
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.layout.processors import AfterInput, ConditionalProcessor
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

from .console import (
    console,
    display_is_active,
    display_render_ft,
    print_assistant,
    print_error,
    print_info,
    task_board,
    thinking_spinner,
)

# amber 계열 — agent 아이콘과 대비
_USER_ICON  = "\uf007"          # Nerd Font person
_USER_COLOR = "#7DBBE8"         # 하늘색 — agent amber(#FF8000)와 대비

# 상태줄 nerd font 아이콘
_FOLDER_ICON    = "\uf07c"          #   (folder-open)
_BRANCH_ICON    = "\ue0a0"          #   (powerline branch)
_AGENT_ICON     = "󰚩 "      # 󰚩  (md-robot — print_assistant와 동일)
# git status 아이콘 (starship 스타일)
_STAGED_ICON    = "\U000F107A"      # 󱁺  staged
_MODIFIED_ICON  = "\uf448"          #   modified
_REMOVED_ICON   = "\uf48e"          #   deleted
_RENAMED_ICON   = "\U000F0541"      # 󰕁  renamed
_UNTRACKED_ICON = "\uf128"          #   untracked
_CONFLICT_ICON  = "\uf467"          #   conflict
_AHEAD_ICON     = "\U000F1DA3"      # 󰶣  ahead
_BEHIND_ICON    = "\U000F1DA1"      # 󰶡  behind

_STYLE = Style.from_dict({
    "user-icon":          f"{_USER_COLOR} bold",
    "hint":               "dim",
    "input":              "",
    "slash-cmd":          "#5FD7AF bold",  # 명령어와 정확히 일치할 때 강조
    # 상태줄 — 항목별 아이콘은 강조색, 본문은 가독성 높은 색
    "status.folder-icon": "#FFD700",        # amber
    "status.folder":      "#E4E4E4 bold",
    "status.branch-icon": "#5FAFFF",        # 파랑
    "status.branch":      "#E4E4E4 bold",   # 흰색
    "status.added":       "#5FD787 bold",   # +N — 녹색 강조
    "status.deleted":     "#FF5F5F bold",   # -N — 빨강 강조
    # git status (file-level) — 모두 짙은 회색
    "status.staged":      "#6C6C6C",
    "status.modified":    "#6C6C6C",
    "status.removed":     "#6C6C6C",
    "status.renamed":     "#6C6C6C",
    "status.untracked":   "#6C6C6C",
    "status.conflicts":   "#6C6C6C",
    "status.ahead":       "#6C6C6C",
    "status.behind":      "#6C6C6C",
    "status.agent-icon":  "#CC785C bold",   # Claude amber — print_assistant와 동일
    "status.main":        "#E4E4E4 bold",
    "status.sub":         "#E4E4E4",
    "status.tag":         "#8A8A8A",        # (main)/(sub) 라벨 — 약하게
})

_SLASH_COMMANDS: dict[str, str] = {
    "/help":  "슬래시 명령어 목록 표시",
    "/clear": "화면 초기화 (대화 기록 유지)",
    "/exit":  "종료",
}

# 슬래시 명령어 토큰 — 양옆이 공백/경계여야 매칭 (예: "/exit", " /exit", "/exit ")
_CMD_PATTERN = re.compile(
    r"(?<!\S)(?:" + "|".join(re.escape(c) for c in _SLASH_COMMANDS) + r")(?!\S)"
)


def _git_stats() -> dict:
    """현재 디렉토리 git 정보. 실패하면 빈 값."""
    empty = {
        "branch": "", "added": 0, "deleted": 0,
        "staged": 0, "modified": 0, "removed": 0,
        "untracked": 0, "renamed": 0, "conflicts": 0,
        "ahead": 0, "behind": 0,
    }
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=0.5,
        ).stdout.strip()
        if not branch:
            return empty

        # HEAD 대비 전체 +/- 라인 수 — 한 번의 `git diff HEAD --numstat` 로 계산.
        # 이전 방식(unstaged + staged 합)은 동일 라인이 양쪽 diff 에 모두 잡히면
        # 중복 카운트돼서 실제보다 과다 집계됨.
        added = deleted = 0
        out = subprocess.run(
            ["git", "diff", "HEAD", "--numstat"],
            capture_output=True, text=True, timeout=0.5,
        ).stdout
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                added += int(parts[0])
                deleted += int(parts[1])

        # 파일 단위 status 카운트 (porcelain v1)
        staged = modified = removed = untracked = renamed = conflicts = 0
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=0.5,
        ).stdout
        for line in out.splitlines():
            if len(line) < 2:
                continue
            xy = line[:2]
            x, y = xy[0], xy[1]
            if xy == "??":
                untracked += 1
                continue
            if "U" in xy or xy in ("AA", "DD"):
                conflicts += 1
                continue
            if x == "R":
                renamed += 1
            if x in "MADRC":
                staged += 1
            if y == "M":
                modified += 1
            if y == "D":
                removed += 1

        # ahead/behind
        ahead = behind = 0
        out = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"],
            capture_output=True, text=True, timeout=0.5,
        ).stdout.strip()
        if out:
            cols = out.split()
            if len(cols) == 2 and cols[0].isdigit() and cols[1].isdigit():
                behind, ahead = int(cols[0]), int(cols[1])

        return {
            "branch": branch, "added": added, "deleted": deleted,
            "staged": staged, "modified": modified, "removed": removed,
            "untracked": untracked, "renamed": renamed, "conflicts": conflicts,
            "ahead": ahead, "behind": behind,
        }
    except Exception:
        return empty


_GIT_CACHE: dict = {"ts": 0.0, "data": _git_stats.__defaults__ or {}}


def _cached_git_stats() -> dict:
    now = time.time()
    if now - _GIT_CACHE["ts"] > 2.0:
        _GIT_CACHE["data"] = _git_stats()
        _GIT_CACHE["ts"] = now
    return _GIT_CACHE["data"]


class _SlashCommandLexer(Lexer):
    """라인 안에서 슬래시 명령어 토큰만 강조 스타일 적용."""

    def lex_document(self, document):
        def get_line(lineno: int) -> StyleAndTextTuples:
            line = document.lines[lineno]
            tokens: StyleAndTextTuples = []
            pos = 0
            for m in _CMD_PATTERN.finditer(line):
                if m.start() > pos:
                    tokens.append(("", line[pos:m.start()]))
                tokens.append(("class:slash-cmd", m.group()))
                pos = m.end()
            if pos < len(line):
                tokens.append(("", line[pos:]))
            return tokens or [("", line)]

        return get_line


class REPLSession:
    def __init__(
        self,
        mode: str = "code",
        history_file: str = ".agent_history",
        main_model: str = "",
        sub_model: str = "",
    ) -> None:
        self._mode           = mode
        self._main_model     = main_model
        self._sub_model      = sub_model
        self._agent_fn: Callable[[str], str] | None = None
        self._on_clear: Callable[[], None] | None   = None
        self._agent_running: bool                   = False

        self._buf = Buffer(
            name="input",
            history=FileHistory(history_file),
            multiline=True,
            accept_handler=self._handle_submit,
        )

        self._app = Application(
            layout=self._build_layout(),
            key_bindings=self._build_keybindings(),
            style=_STYLE,
            full_screen=False,
            mouse_support=False,
        )

    # ── layout ────────────────────────────────────────────────────────────────

    def _build_layout(self) -> Layout:
        def _prompt_line() -> FormattedText:
            return FormattedText([
                ("class:user-icon", f" {_USER_ICON}  "),
            ])

        def _status_line() -> FormattedText:
            cwd = os.getcwd()
            home = os.path.expanduser("~")
            if cwd == home:
                path = "~"
            elif cwd.startswith(home + os.sep):
                path = "~" + cwd[len(home):]
            else:
                path = cwd

            stats = _cached_git_stats()
            branch = stats["branch"]

            parts: list[tuple[str, str]] = []
            parts.append(("class:status.folder-icon", f" {_FOLDER_ICON}  "))
            parts.append(("class:status.folder", path))
            if branch:
                parts.append(("", "   "))
                parts.append(("class:status.branch-icon", f"{_BRANCH_ICON} "))
                parts.append(("class:status.branch", branch))

                # 파일 단위 status (starship 순서: conflicts, removed, renamed, modified, staged, untracked, ahead, behind)
                file_segs = (
                    ("conflicts", _CONFLICT_ICON,  "class:status.conflicts"),
                    ("removed",   _REMOVED_ICON,   "class:status.removed"),
                    ("renamed",   _RENAMED_ICON,   "class:status.renamed"),
                    ("modified",  _MODIFIED_ICON,  "class:status.modified"),
                    ("staged",    _STAGED_ICON,    "class:status.staged"),
                    ("untracked", _UNTRACKED_ICON, "class:status.untracked"),
                    ("ahead",     _AHEAD_ICON,     "class:status.ahead"),
                    ("behind",    _BEHIND_ICON,    "class:status.behind"),
                )
                segments: list[list[tuple[str, str]]] = []
                for key, icon, cls in file_segs:
                    n = stats[key]
                    if n > 0:
                        segments.append([(cls, f"{icon} {n}")])
                if stats["added"]:
                    segments.append([("class:status.added", f"+{stats['added']}")])
                if stats["deleted"]:
                    segments.append([("class:status.deleted", f"-{stats['deleted']}")])

                if segments:
                    parts.append(("", "("))
                    for i, seg in enumerate(segments):
                        if i > 0:
                            parts.append(("", ", "))
                        parts.extend(seg)
                    parts.append(("", ")"))
            if self._main_model:
                parts.append(("", "   "))
                parts.append(("class:status.agent-icon", f"{_AGENT_ICON} "))
                parts.append(("class:status.main", self._main_model))
                parts.append(("class:status.tag", "(main)"))
            if self._sub_model:
                parts.append(("", "   "))
                parts.append(("class:status.agent-icon", f"{_AGENT_ICON} "))
                parts.append(("class:status.sub", self._sub_model))
                parts.append(("class:status.tag", "(sub)"))
            return FormattedText(parts)

        hint_processor = ConditionalProcessor(
            AfterInput("  ↵ send · Alt+↵ newline · /help", style="class:hint"),
            filter=Condition(lambda: not self._buf.text),
        )

        def _spinner_height() -> D:
            if not display_is_active():
                return D.exact(0)
            ft = display_render_ft()
            # FormattedText tuple 은 2 또는 3 요소. 항상 index 1 이 본문.
            text = "".join(item[1] for item in ft)
            return D.exact(max(1, text.count("\n") + 1))

        return Layout(
            HSplit([
                # live 영역: Thinking shimmer + active subagent bullet pulse
                ConditionalContainer(
                    content=Window(
                        content=FormattedTextControl(display_render_ft),
                        height=_spinner_height,
                        dont_extend_height=True,
                        wrap_lines=False,
                    ),
                    filter=Condition(display_is_active),
                ),
                Window(
                    content=FormattedTextControl(_status_line),
                    height=1,
                    dont_extend_height=True,
                ),
                VSplit([
                    Window(
                        content=FormattedTextControl(_prompt_line),
                        width=4,
                        dont_extend_width=True,
                    ),
                    Window(
                        content=BufferControl(
                            buffer=self._buf,
                            input_processors=[hint_processor],
                            lexer=_SlashCommandLexer(),
                        ),
                        height=D(min=1, max=8),
                        dont_extend_height=True,
                        wrap_lines=True,
                        left_margins=[],
                    ),
                ]),
            ]),
            focused_element=self._buf,
        )

    # ── key bindings ──────────────────────────────────────────────────────────

    def _build_keybindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("enter")
        def _(event):
            event.current_buffer.validate_and_handle()

        @kb.add("escape", "enter")
        def _(event):
            event.current_buffer.insert_text("\n")

        @kb.add("c-c")
        def _(event):
            if not event.current_buffer.text:
                event.app.exit()
            else:
                event.current_buffer.reset()

        @kb.add("c-d")
        def _(event):
            event.app.exit()

        return kb

    # ── submit handler ────────────────────────────────────────────────────────

    def _handle_submit(self, buf: Buffer) -> bool:
        line = buf.text.strip()
        buf.reset()

        if not line:
            return False

        # agent 가 돌고 있는 동안엔 새 입력 무시 (버퍼는 이미 비웠으므로 무해)
        if self._agent_running:
            return True

        if line == "/exit":
            self._app.exit()
            return True

        if line == "/help":
            def _show():
                from rich.table import Table
                t = Table(show_header=False, box=None, padding=(0, 2), show_edge=False)
                for cmd, desc in _SLASH_COMMANDS.items():
                    t.add_row(f"[bold cyan]{cmd}[/bold cyan]", f"[dim]{desc}[/dim]")
                console.print(t)
                console.print()
            run_in_terminal(_show)
            return True

        if line == "/clear":
            def _clear():
                console.clear()
                if self._on_clear:
                    self._on_clear()
            run_in_terminal(_clear)
            return True

        if self._agent_fn is None:
            return True

        # agent 는 background task 로 돌리고, 그 안에서만 patch_stdout 적용
        # → Application UI (상태줄 + 입력창) 는 실행 중에도 화면 하단에 유지됨
        self._app.create_background_task(self._run_agent_async(line))
        return True

    async def _run_agent_async(self, line: str) -> None:
        self._agent_running = True
        refresh = asyncio.create_task(self._refresh_loop())
        try:
            loop = asyncio.get_running_loop()
            # raw=True — rich ANSI 스타일/커서 이스케이프 보존
            with patch_stdout(raw=True):
                await loop.run_in_executor(None, self._run_agent_sync, line)
        finally:
            refresh.cancel()
            try:
                await refresh
            except asyncio.CancelledError:
                pass
            self._agent_running = False
            self._app.invalidate()

    async def _refresh_loop(self) -> None:
        """display 가 active 인 동안 주기적으로 Application 을 재렌더링."""
        try:
            while True:
                if display_is_active():
                    self._app.invalidate()
                await asyncio.sleep(0.08)
        except asyncio.CancelledError:
            pass

    def _run_agent_sync(self, line: str) -> None:
        agent_fn = self._agent_fn
        if agent_fn is None:
            return
        # 사용자 입력 표시 — bg를 어둡게, 텍스트는 하늘색으로 agent 응답과 구분
        console.print(
            f"[bold {_USER_COLOR}] {_USER_ICON} [/bold {_USER_COLOR}]"
            f"[on #1A2A35][{_USER_COLOR}] {line} [/{_USER_COLOR}][/on #1A2A35]"
        )
        try:
            task_board.reset()
            with thinking_spinner():
                reply = agent_fn(line)
            if reply:
                print_assistant(reply)
        except KeyboardInterrupt:
            print_info("[취소됨]")
        except Exception as exc:
            print_error(str(exc))

    # ── public API ────────────────────────────────────────────────────────────

    def run(
        self,
        agent_fn: Callable[[str], str],
        on_clear: Callable[[], None] | None = None,
    ) -> None:
        self._agent_fn = agent_fn
        self._on_clear = on_clear
        # 시작 시 화면 초기화 — 입력창이 항상 최상단에서 시작
        console.clear()
        if on_clear:
            on_clear()
        self._app.run()
