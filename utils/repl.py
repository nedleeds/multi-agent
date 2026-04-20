"""Claude Code–style interactive REPL.

prompt_toolkit.Application을 항상 실행 상태로 유지하고,
agent 실행은 백그라운드 executor 에서 돌리면서 patch_stdout 으로 출력을 UI 위쪽에
흘려서, 상태줄 + 입력창이 agent 실행 중에도 하단에 계속 떠 있게 한다.
"""

import asyncio
import os
import re
import sys
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
from .error_log import log_exception

# amber 계열 — agent 아이콘과 대비
_USER_ICON  = "\uf007"          # Nerd Font person
_USER_COLOR = "#7DBBE8"         # 하늘색 — agent amber(#FF8000)와 대비

# 상태줄 nerd font 아이콘
_FOLDER_ICON    = "\uf07c"          #   (folder-open)
_AGENT_ICON     = "󰚩 "      # 󰚩  (md-robot — print_assistant와 동일)

_STYLE = Style.from_dict({
    "user-icon": f"{_USER_COLOR} bold",
    "hint":      "dim",
    "input":     "",
    "slash-cmd": "#5FD7AF bold",  # 명령어와 정확히 일치할 때 강조
})

_SLASH_COMMANDS: dict[str, str] = {
    "/help":    "슬래시 명령어 목록 표시",
    "/clear":   "화면 초기화 (대화 기록 유지)",
    "/models":  "현재 세션의 cwd + main/sub 모델 배너 재출력",
    "/cancel":  "실행 중인 턴 중단 (다음 턴 경계에서)",
    "/killall": "즉시 모든 agent 중단 + 프로세스 종료 (강제)",
    "/exit":    "종료",
}

# 슬래시 명령어 토큰 — 양옆이 공백/경계여야 매칭 (예: "/exit", " /exit", "/exit ")
_CMD_PATTERN = re.compile(
    r"(?<!\S)(?:" + "|".join(re.escape(c) for c in _SLASH_COMMANDS) + r")(?!\S)"
)


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
        self._on_cancel: Callable[[], None] | None  = None
        self._agent_running: bool                   = False
        # 파괴적 tool 승인 브릿지 — run() 시 전달받음. 없으면 승인 게이트 disabled.
        self._permissions = None

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

    # ── banner ────────────────────────────────────────────────────────────────

    def _print_banner(self) -> None:
        """cwd + main/sub 모델명을 rich markup 으로 한 줄 출력.
        시작 시, `/clear` 후, `/models` 호출 시 사용. 스타일은 _STYLE 의 status.* 와 맞춤.
        """
        cwd = os.getcwd()
        home = os.path.expanduser("~")
        if cwd == home:
            path = "~"
        elif cwd.startswith(home + os.sep):
            path = "~" + cwd[len(home):]
        else:
            path = cwd

        parts = [f"[#FFD700] {_FOLDER_ICON}  [/][bold #E4E4E4]{path}[/]"]
        if self._main_model:
            parts.append(
                f"[bold #CC785C]{_AGENT_ICON} [/][bold #E4E4E4]{self._main_model}[/][#8A8A8A](main)[/]"
            )
        if self._sub_model:
            parts.append(
                f"[bold #CC785C]{_AGENT_ICON} [/][#E4E4E4]{self._sub_model}[/][#8A8A8A](sub)[/]"
            )
        console.print("   ".join(parts))
        console.print()

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

        # Pending permission 이 있으면 agent 쓰레드가 Future 에서 blocking 중.
        # 다른 커맨드 처리하지 않고 y/n/d/a 로 해석 → PermissionManager 에 통지.
        perm = self._permissions
        if perm is not None and perm.has_pending():
            key = line.lower().strip()
            if key in ("y", "yes", "승인"):
                perm.approve()
                run_in_terminal(lambda: console.print("[dim]  ✓ approved[/dim]"))
            elif key in ("n", "no", "거부"):
                perm.deny("declined by user")
                run_in_terminal(lambda: console.print("[dim]  ✗ denied[/dim]"))
            elif key in ("d", "diff", "detail"):
                perm.toggle_full_diff()
            elif key in ("a", "auto"):
                perm.enable_auto_session()
                run_in_terminal(lambda: console.print(
                    "[info]  ✓ auto-approved (세션 내내 동일 tool 자동승인)[/info]"
                ))
            else:
                run_in_terminal(lambda: console.print(
                    "[dim]  [y] 승인 · [n] 거부 · [d] 전체 diff · [a] 자동승인[/dim]"
                ))
            return True

        # /killall — 즉시 종료. 협조적 취소를 기다리지 않고 프로세스를 끝낸다.
        # 실행 중이든 아니든 동작해야 하므로 _agent_running 체크 이전에 처리.
        # 서브에이전트/백그라운드/팀메이트 스레드는 전부 daemon 이라 os._exit 로 함께 정리됨.
        # run_in_terminal 은 async 라 os._exit 이전에 출력되지 않을 수 있어 stderr 직접 기록.
        if line == "/killall":
            sys.stderr.write("\n⏻  /killall — terminating immediately\n")
            sys.stderr.flush()
            os._exit(130)

        # /cancel 은 agent 가 돌고 있을 때만 의미 있음 — running 체크 이전에 처리
        if line == "/cancel":
            if self._agent_running and self._on_cancel:
                self._on_cancel()
                run_in_terminal(lambda: console.print(
                    "[info] ⏹  /cancel requested — exiting at next turn boundary[/info]"
                ))
            else:
                run_in_terminal(lambda: console.print("[dim](nothing running)[/dim]"))
            return True

        # UI 전용 메타 커맨드 — agent 실행 중이든 유휴든 즉시 처리.
        # 에이전트 상태를 변경하지 않고 scrollback 에만 출력하므로 안전.
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

        if line == "/models":
            run_in_terminal(self._print_banner)
            return True

        # agent 가 돌고 있는 동안엔 나머지 입력 무시 (새 턴/clear/exit 는 경계에서만)
        if self._agent_running:
            return True

        if line == "/exit":
            self._app.exit()
            return True

        if line == "/clear":
            def _clear():
                console.clear()
                self._print_banner()
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
        # 턴 경계 — scrollback 에서 각 사용자 입력이 시작점임을 한 눈에 보이게.
        # 풀 너비 dim rule 을 쓰지 않고 짧은 회색 선으로 시각 노이즈 최소화.
        console.rule(style="#3A3A3A", characters="─", align="left")
        # 사용자 입력 표시 — bg를 어둡게, 텍스트는 하늘색으로 agent 응답과 구분
        console.print(
            f"[bold {_USER_COLOR}] {_USER_ICON} [/bold {_USER_COLOR}]"
            f"[on #1A2A35][{_USER_COLOR}] {line} [/{_USER_COLOR}][/on #1A2A35]"
        )
        try:
            task_board.reset()
            with thinking_spinner():
                reply = agent_fn(line)
            # 스트리밍은 라이브 리전(ephemeral)에서만 — turn 끝나면 지워짐.
            # 최종 답변의 markdown-rendered 본문은 여기서 scrollback 에 커밋.
            if reply:
                print_assistant(reply)
        except KeyboardInterrupt:
            print_info("[취소됨]")
        except Exception as exc:
            # 한 줄 요약만 찍고 전체 traceback + context 는 파일로.
            # exception 에 _agent_ctx 가 붙어있으면 model_id/base_url/turn 등 포함.
            log_path = log_exception(exc)
            print_error(f"{type(exc).__name__}: {exc}")
            ctx = getattr(exc, "_agent_ctx", None)
            if isinstance(ctx, dict):
                detail = "  ".join(f"{k}={v}" for k, v in ctx.items())
                print_error(f"  ctx: {detail}")
            print_error(f"  full traceback → {log_path}")

    # ── public API ────────────────────────────────────────────────────────────

    def run(
        self,
        agent_fn: Callable[[str], str],
        on_clear: Callable[[], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        permissions=None,
    ) -> None:
        self._agent_fn    = agent_fn
        self._on_clear    = on_clear
        self._on_cancel   = on_cancel
        self._permissions = permissions
        # 시작 시 화면 초기화 + 배너 한 번 출력 — 이후는 /models 로 on-demand 재호출
        console.clear()
        self._print_banner()
        if on_clear:
            on_clear()
        self._app.run()
