"""Tool implementations: bash, read_file, write_file, edit_file."""

import subprocess
from pathlib import Path

WORKDIR = Path.cwd()
_DANGEROUS = ["rm -rf /", "sudo rm", "shutdown", "reboot", "> /dev/"]

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
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        if not output:
            return "(no output)"
        return _truncate(output, source_hint="bash")
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except OSError as exc:
        return f"Error: {exc}"


def read_file(path: str, limit: int | None = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        total = len(lines)
        if limit and limit < total:
            lines = lines[:limit] + [f"... ({total - limit} more lines — call read_file again with offset or a larger limit)"]
        return _truncate("\n".join(lines), source_hint=f"read_file('{path}', limit=...)")
    except Exception as exc:
        return f"Error: {exc}"


def write_file(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as exc:
        return f"Error: {exc}"


def edit_file(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        text = fp.read_text()
        if old_text not in text:
            return f"Error: Text not found in {path}"
        fp.write_text(text.replace(old_text, new_text, 1))
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

    try:
        result = subprocess.run(
            cmd, cwd=WORKDIR, capture_output=True, text=True, timeout=60
        )
        if result.returncode == 2:
            return f"Error: {result.stderr.strip()[:200]}"
        output = result.stdout.strip()
        if not output:
            return "(no matches)"
        lines = output.splitlines()
        if len(lines) > head_limit:
            output = "\n".join(lines[:head_limit])
            output += (
                f"\n\n[TRUNCATED at {head_limit} lines — raise head_limit, "
                f"use output_mode='files_with_matches' first, or narrow pattern/path. "
                f"Do NOT treat as complete.]"
            )
        return _truncate(output, source_hint=f"grep(pattern={pattern!r})")
    except subprocess.TimeoutExpired:
        return "Error: grep timeout (60s) — narrow scope"
    except FileNotFoundError:
        return "Error: ripgrep (rg) not found — install with `brew install ripgrep`"


def glob_files(pattern: str, path: str = ".") -> str:
    """File path pattern matching via rg --files. Respects .gitignore."""
    cmd = ["rg", "--files", "--no-messages", "-g", pattern, path]
    try:
        result = subprocess.run(
            cmd, cwd=WORKDIR, capture_output=True, text=True, timeout=30
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
    """Tree-style directory listing, 노이즈 디렉토리 자동 제외."""
    cmd = ["find", path, "-mindepth", "1", "-maxdepth", str(depth)]
    if not hidden:
        # 숨김 파일/폴더(/. 로 시작) prune
        cmd.extend(["-not", "-path", "*/.*"])
    for noise in _NOISE_DIRS:
        cmd.extend(["-not", "-path", f"*/{noise}", "-not", "-path", f"*/{noise}/*"])
    if dirs_only:
        cmd.extend(["-type", "d"])
    try:
        result = subprocess.run(
            cmd, cwd=WORKDIR, capture_output=True, text=True, timeout=30
        )
        output = result.stdout.strip()
        if not output:
            return "(no entries)"
        lines = sorted(output.splitlines())
        return _truncate("\n".join(lines), source_hint=f"ls(path={path!r}, depth={depth})")
    except subprocess.TimeoutExpired:
        return "Error: ls timeout (30s)"
    except FileNotFoundError:
        return "Error: find command not available"


def fuzzy_find(query: str, path: str = ".", limit: int = 50) -> str:
    """rg --files 로 얻은 경로 목록을 fzf -f <query> 로 fuzzy 랭킹."""
    try:
        rg = subprocess.run(
            ["rg", "--files", "--no-messages", path],
            cwd=WORKDIR, capture_output=True, text=True, timeout=30
        )
        if rg.returncode not in (0, 1):
            return f"Error: rg — {rg.stderr.strip()[:200]}"
        if not rg.stdout.strip():
            return "(no files)"
        fzf = subprocess.run(
            ["fzf", "-f", query],
            input=rg.stdout,
            cwd=WORKDIR, capture_output=True, text=True, timeout=15
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
