"""Tool implementations: bash, read_file, write_file, edit_file."""

import subprocess
from pathlib import Path

from utils.shell import run_shell

WORKDIR = Path.cwd()
# Unix 용 위험 패턴 + Windows PowerShell/cmd 용 위험 패턴
_DANGEROUS = [
    # Unix
    "rm -rf /", "sudo rm", "shutdown", "reboot", "> /dev/",
    # Windows (PowerShell / cmd)
    "Format-Volume", "Remove-Item -Recurse -Force C:\\",
    "rmdir /s", "del /s /q C:\\",
]

# tool output 한계 — 200KB. 넘으면 절삭 + 에이전트가 볼 수 있는 마커 부착.
_MAX_OUTPUT = 200_000


def _truncate(output: str, source_hint: str) -> str:
    """길이 초과 시 줄 경계에서 자르고, 에이전트가 인식 가능한 마커를 붙임."""
    if len(output) <= _MAX_OUTPUT:
        return output
    cut = _MAX_OUTPUT
    # 근처 500 byte 내 newline 에서 자름 — 라인 중간 절단 방지
    nl = output.rfind("\n", max(0, cut - 500), cut)
    if nl != -1:
        cut = nl
    omitted = len(output) - cut
    marker = (
        f"\n\n[OUTPUT TRUNCATED — {omitted:,} bytes omitted. "
        f"Re-run {source_hint} with pagination (head/tail/grep/sed -n Y,Zp) "
        f"or narrower scope (-maxdepth, specific path). "
        f"Do NOT treat this result as complete.]"
    )
    return output[:cut] + marker


def safe_path(path_str: str) -> Path:
    resolved = (WORKDIR / path_str).resolve()
    if not resolved.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path_str}")
    return resolved


def bash(command: str) -> str:
    if any(d in command for d in _DANGEROUS):
        return "Error: Dangerous command blocked"
    try:
        # Windows 에선 run_shell 이 PowerShell 로 위임. Unix 에선 /bin/sh.
        result = run_shell(command, cwd=WORKDIR, timeout=120)
        output = (result.stdout + result.stderr).strip()
        if not output:
            return "(no output)"
        return _truncate(output, source_hint="bash")
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except OSError as exc:
        return f"Error: {exc}"


def read_file(path: str, limit: int | None = None, offset: int = 0) -> str:
    """Read file contents, optionally paginated.

    - `offset` (0-indexed): 시작 라인. 기본 0.
    - `limit` : 읽을 최대 라인 수. None/0 이면 offset 부터 끝까지.
    잘린 경우 "call read_file again with offset=X" 힌트 포함 — 이 때 X 는
    **실제로 호출 가능한 offset 값**.
    """
    try:
        all_lines = safe_path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(all_lines)
        offset = max(0, int(offset or 0))

        if offset >= total and total > 0:
            return f"(offset {offset} >= total {total} — file has only {total} lines)"

        window = all_lines[offset:]
        if limit and limit < len(window):
            next_offset = offset + limit
            remaining = total - next_offset
            window = window[:limit] + [
                f"... ({remaining} more lines — call read_file(path, limit={limit}, offset={next_offset}))"
            ]
        return _truncate(
            "\n".join(window),
            source_hint=f"read_file('{path}', limit=..., offset={offset})",
        )
    except Exception as exc:
        return f"Error: {exc}"


def write_file(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as exc:
        return f"Error: {exc}"


def edit_file(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        text = fp.read_text(encoding="utf-8", errors="replace")
        if old_text not in text:
            return f"Error: Text not found in {path}"
        fp.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {path}"
    except Exception as exc:
        return f"Error: {exc}"


# ── 코드 탐색 툴 ──────────────────────────────────────────────────────────────
# rg/fzf 기반. rg 는 .gitignore 를 존중하고 바이너리 자동 제외.
# find 기반 ls 는 .venv/__pycache__/node_modules 등 노이즈 디렉토리 prune.

_NOISE_DIRS = [
    "__pycache__", "node_modules", ".ruff_cache", ".pytest_cache",
    ".mypy_cache", "dist", "build", ".tox", ".cache",
]


def grep(
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    type: str | None = None,
    output_mode: str = "content",
    case_insensitive: bool = False,
    line_numbers: bool = True,
    context: int = 0,
    head_limit: int = 200,
) -> str:
    """Content search via ripgrep."""
    cmd = ["rg", "--no-messages"]
    if case_insensitive:
        cmd.append("-i")
    if output_mode == "files_with_matches":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("-c")
    else:  # content
        if line_numbers:
            cmd.append("-n")
        if context > 0:
            cmd.extend(["-C", str(context)])
    if glob:
        cmd.extend(["-g", glob])
    if type:
        cmd.extend(["-t", type])
    cmd.extend(["--", pattern, path])

    def _run(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, cwd=WORKDIR, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )

    try:
        result = _run(cmd)
        fallback_hint = ""
        # rg 가 regex 파싱 실패 시 리턴코드 2 + stderr 에 "regex parse error".
        # 모델이 literal 문자열 의도로 `input(`, `arr[0]` 같이 특수문자를 썼을 확률이 높으니
        # `-F` (fixed-string) 로 1회 자동 재시도. 성공하면 상단에 안내 주석 첨부.
        if result.returncode == 2 and "regex parse error" in result.stderr:
            fixed_cmd = cmd[:1] + ["-F"] + cmd[1:]
            retry = _run(fixed_cmd)
            if retry.returncode in (0, 1):
                fallback_hint = (
                    f"[auto-fallback] pattern was not valid regex — retried with -F "
                    f"(fixed-string). For regex escaping rules, see `rg --help`.\n\n"
                )
                result = retry
            else:
                return f"Error: {result.stderr.strip()[:200]}"
        elif result.returncode == 2:
            return f"Error: {result.stderr.strip()[:200]}"
        output = result.stdout.strip()
        if not output:
            return fallback_hint + "(no matches)" if fallback_hint else "(no matches)"
        lines = output.splitlines()
        if len(lines) > head_limit:
            output = "\n".join(lines[:head_limit])
            output += (
                f"\n\n[TRUNCATED at {head_limit} lines — raise head_limit, "
                f"use output_mode='files_with_matches' first, or narrow pattern/path. "
                f"Do NOT treat as complete.]"
            )
        return _truncate(fallback_hint + output, source_hint=f"grep(pattern={pattern!r})")
    except subprocess.TimeoutExpired:
        return "Error: grep timeout (60s) — narrow scope"
    except FileNotFoundError:
        return "Error: ripgrep (rg) not found — install with `brew install ripgrep`"


def glob_files(pattern: str, path: str = ".") -> str:
    """File path pattern matching via rg --files. Respects .gitignore."""
    cmd = ["rg", "--files", "--no-messages", "-g", pattern, path]
    try:
        result = subprocess.run(
            cmd, cwd=WORKDIR, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        if result.returncode == 2:
            return f"Error: {result.stderr.strip()[:200]}"
        output = result.stdout.strip()
        if not output:
            return f"(no files match pattern={pattern!r})"
        return _truncate(output, source_hint=f"glob(pattern={pattern!r})")
    except subprocess.TimeoutExpired:
        return "Error: glob timeout (30s)"
    except FileNotFoundError:
        return "Error: ripgrep (rg) not found"


def list_dir(
    path: str = ".",
    depth: int = 2,
    dirs_only: bool = False,
    hidden: bool = False,
) -> str:
    """Tree-style directory listing, 노이즈 디렉토리 자동 제외.

    순수 Python 구현 — Unix `find` 의존 제거. macOS/Linux/Windows 동일하게 동작.
    출력 라인 포맷은 find 와 동일 (`./path/foo`) 로 유지.
    """
    try:
        base = (WORKDIR / path).resolve()
        if not base.exists():
            return f"Error: Path not found: {path}"
        if not base.is_dir():
            return f"Error: Not a directory: {path}"

        entries: list[str] = []
        prefix = path.rstrip("/\\") or "."

        def recurse(current: Path, display: str, remaining: int) -> None:
            try:
                children = list(current.iterdir())
            except OSError:
                return
            for child in children:
                name = child.name
                if not hidden and name.startswith("."):
                    continue
                if name in _NOISE_DIRS:
                    continue
                try:
                    is_dir = child.is_dir()
                except OSError:
                    is_dir = False
                child_display = f"{display}/{name}"
                if not (dirs_only and not is_dir):
                    entries.append(child_display)
                if is_dir and remaining > 1:
                    recurse(child, child_display, remaining - 1)

        recurse(base, prefix, depth)
        if not entries:
            return "(no entries)"
        return _truncate("\n".join(sorted(entries)), source_hint=f"ls(path={path!r}, depth={depth})")
    except OSError as exc:
        return f"Error: {exc}"


def fuzzy_find(query: str, path: str = ".", limit: int = 50) -> str:
    """rg --files 로 얻은 경로 목록을 fzf -f <query> 로 fuzzy 랭킹."""
    try:
        rg = subprocess.run(
            ["rg", "--files", "--no-messages", path],
            cwd=WORKDIR, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        if rg.returncode not in (0, 1):
            return f"Error: rg — {rg.stderr.strip()[:200]}"
        if not rg.stdout.strip():
            return "(no files)"
        fzf = subprocess.run(
            ["fzf", "-f", query],
            input=rg.stdout,
            cwd=WORKDIR, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        output = fzf.stdout.strip()
        if not output:
            return f"(no fuzzy matches for query={query!r})"
        lines = output.splitlines()[:limit]
        total = len(output.splitlines())
        result = "\n".join(lines)
        if total > limit:
            result += f"\n\n[showing top {limit} of {total} matches — raise limit or refine query]"
        return result
    except subprocess.TimeoutExpired:
        return "Error: fuzzy_find timeout"
    except FileNotFoundError as exc:
        return f"Error: {exc} — rg/fzf must be installed"
