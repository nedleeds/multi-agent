"""Cross-platform shell invocation helper.

Unix (macOS/Linux) 에서는 기본 쉘(`shell=True` → /bin/sh)을 쓰고,
Windows 에서는 PowerShell (pwsh 우선, 없으면 powershell.exe)을 경유한다.

이유: `subprocess.run(cmd, shell=True)` 는 Windows 에서 `cmd.exe` 로 위임돼서
`ls`, `cat`, `grep` 같은 Unix 관용구가 그대로 통하지 않는다. PowerShell 은
`ls` / `cat` / `pwd` 등을 alias 로 제공해서 에이전트가 생성한 명령이 더 잘 동작함.
"""

import os
import shutil
import subprocess
from pathlib import Path

IS_WINDOWS = os.name == "nt"


def _powershell_exe() -> str:
    """pwsh (PowerShell Core, 크로스플랫폼) 우선. 없으면 Windows 내장 powershell.exe."""
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell"


def run_shell(
    command: str,
    *,
    cwd: Path | str | None = None,
    timeout: float | None = 120,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> subprocess.CompletedProcess:
    """Run a shell command, picking the right interpreter per platform.

    - Unix: `subprocess.run(command, shell=True, ...)` — /bin/sh
    - Windows: `subprocess.run(["pwsh|powershell", "-NoProfile", "-NonInteractive",
                                "-Command", command], ...)`
    """
    if IS_WINDOWS:
        return subprocess.run(
            [_powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors=errors,
            timeout=timeout,
        )
    return subprocess.run(
        command,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding=encoding,
        errors=errors,
        timeout=timeout,
    )
