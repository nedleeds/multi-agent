"""Error log writer — full traceback + context to `.agent_logs/<ts>.log`.

Agent 에서 발생한 예외의 원인을 나중에 재현·분석할 수 있도록, 콘솔에 찍는
한 줄 요약과 별개로 전체 스택·호출 지점 컨텍스트를 파일에 남긴다.

호출부에서 예외 객체에 `_agent_ctx = {...}` 속성을 붙여두면 그 dict 가
자동으로 로그에 포함된다. (예: loop.py 에서 model_id, base_url, turn, msgs 등)
"""

import traceback
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(".agent_logs")


def log_exception(exc: BaseException) -> Path:
    """Write traceback + optional `_agent_ctx` attached to exc. Returns log path."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path = LOG_DIR / f"{now.strftime('%Y%m%d-%H%M%S-%f')}.log"

    lines: list[str] = [
        f"[timestamp] {now.isoformat()}",
        f"[exception] {type(exc).__module__}.{type(exc).__name__}: {exc}",
    ]

    ctx = getattr(exc, "_agent_ctx", None)
    if isinstance(ctx, dict) and ctx:
        lines.append("")
        lines.append("[context]")
        for k, v in ctx.items():
            lines.append(f"  {k} = {v}")

    # 원인 체인 (`raise ... from ...`) 포함 전체 traceback
    lines.append("")
    lines.append("[traceback]")
    lines.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
