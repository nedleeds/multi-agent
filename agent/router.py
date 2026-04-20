"""Intent router — 매 user turn 시작에서 한 번 호출해 어떤 tool tier 를 실을지 결정.

Orchestrator 는 항상 하나이지만, 38개 unified tool 스키마를 매번 싣는 건 낭비
(turn 당 ~3.4k token). 120b 메인 모델 한 번으로 intent 를 분류해서, 필요한
tier 만 골라 싣는다.

설계 핵심:
- **메인 모델 (gpt-oss-120b) 로 분류** — sub_model(7b) 로는 오분류 위험이 커서
  절감 효과를 까먹음. router 콜은 `tools=None` 이라 턴당 ~250 토큰으로 저렴.
- **system prompt 고정** — 바이트-동일하게 유지해서 vLLM/OpenAI 서버 prefix cache
  상주. 실제 부담은 `user msg + history tail` 만.
- **Fallback 보수적** — 파싱 실패/에러 시 `{CODING, ISSUE, TEAM}` 합집합으로
  복귀. "라우터가 놓쳐서 tool 이 없는" 상황이 제일 나쁨.
"""

import re
import time
from collections.abc import Iterable

from model.base import BaseLLM
from tools.definitions import VALID_INTENTS

# 바이트-동일 유지 → prefix cache hit.
_ROUTER_SYSTEM = """You are an intent classifier AND planner for a coding agent system.

## Part 1 — INTENT
Classify the user's latest message into one or more of these labels:

  CHAT    — greeting / small talk / meta Q about the agent itself
            ("안녕", "고마워", "뭐 할 수 있어?")
  CODING  — read / write / edit / search source code in this repo
            ("repl.py 봐줘", "bug 고쳐", "ls 툴 추가")
  ISSUE   — investigate incidents via Jira / Bitbucket / Confluence
            ("MEDIA-412 왜 터진거야", "최근 배포 관련 장애 찾아봐")
  TEAM    — teammates, worktrees, task graph, background jobs
            ("워크트리 만들어", "alice 띄워서 이거 맡겨")

## Part 2 — PLAN (optional roadmap)
If the request requires **3+ distinct actions**, emit a 3–5 step plan.
Each step = one concrete, checkable outcome (not implementation minutiae).

**Skip plan** (emit `(none)`) for:
  - CHAT, single grep/read, trivial one-liners
  - Ambiguous requests (let the agent probe first)

**For behavioral questions** ("어떻게 동작?", "X 하면 Y는 어떻게 처리?"):
plan should target the feature's **core implementation file** directly, NOT the
orchestrator/plumbing. e.g. "Bitbucket 키 잘못되면?" → first step is locating
`tools/api/bitbucket.py` and its config check path, not scanning orchestrator.py.

## Output format (strict — NO other text)

    INTENT: <labels, comma-separated>
    PLAN:
    - <step 1>
    - <step 2>
    - <step 3>

혹은 plan 없는 경우:

    INTENT: CHAT
    PLAN: (none)

## Examples

"안녕"
→
INTENT: CHAT
PLAN: (none)

"repl.py 의 REPLSession 역할 설명해"
→
INTENT: CODING
PLAN: (none)

"MEDIA-412 조사해서 유력 원인 파악해"
→
INTENT: ISSUE
PLAN:
- Jira MEDIA-412 및 유사 티켓 조사
- 관련 commit / PR diff 식별
- 원인 가설 + 최소 수정안 제시

"task_manager 분석하고 TODO 기능 붙이는 설계 해"
→
INTENT: CODING
PLAN:
- task_manager 현재 API 파악 (grep/read 로 public 메서드 목록화)
- TODO 통합 포인트 선정
- 설계안 (인터페이스 + 변경 파일) 제안
"""


class RouterResult:
    """Router 한 턴 결과. intent 분류 + 선택적 plan 을 담는다."""

    __slots__ = ("intents", "plan", "latency_ms", "raw", "fallback")

    def __init__(
        self,
        intents: set[str],
        plan: list[str],
        latency_ms: int,
        raw: str,
        fallback: bool,
    ) -> None:
        self.intents = intents
        self.plan = plan
        self.latency_ms = latency_ms
        self.raw = raw
        self.fallback = fallback

    def label(self) -> str:
        """`'CODING, ISSUE'` 형식 정렬 라벨 — 시각 로그용."""
        return ", ".join(sorted(self.intents)) if self.intents else "(empty)"


def _format_history_tail(history_tail: Iterable[dict], max_chars: int = 400) -> str:
    """최근 user/assistant 컨텐츠만 짧게 접어 라우터 context 로 넘김.
    tool_call payload 는 제외 — intent 분류엔 방해.
    """
    lines: list[str] = []
    for msg in history_tail:
        role = msg.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        # 긴 assistant 응답은 앞쪽 요지만
        snippet = content[:200].replace("\n", " ")
        lines.append(f"{role}: {snippet}")
    joined = "\n".join(lines[-6:])  # 최대 최근 6줄
    return joined[-max_chars:]


def _parse(raw: str) -> set[str]:
    """모델 응답에서 유효한 intent 만 추출 — `INTENT:` 라인 우선, 없으면 전체 스캔."""
    upper = raw.upper()
    # "INTENT:" 이후 ~ "PLAN:" 이전 구간에서 추출 (PLAN 내부 고유명사 오염 방지)
    m = re.search(r"INTENT\s*:\s*(.*?)(?:PLAN\s*:|$)", upper, re.DOTALL)
    scope = m.group(1) if m else upper
    return set(re.findall(r"\b(CHAT|CODING|ISSUE|TEAM)\b", scope)) & VALID_INTENTS


def _parse_plan(raw: str, max_items: int = 7) -> list[str]:
    """`PLAN:` 이후 `- ` 로 시작하는 라인을 step 으로 수집.
    `(none)` / 빈 섹션 / 누락 시 빈 리스트 반환."""
    m = re.search(r"PLAN\s*:\s*(.*)", raw, re.DOTALL | re.IGNORECASE)
    if not m:
        return []
    body = m.group(1).strip()
    if not body or re.match(r"\(?\s*none\s*\)?", body, re.IGNORECASE):
        return []
    items: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        # list marker 가 **있는 라인만** step 으로 채택.
        # 코드펜스/일반 텍스트/헤더가 섞여있어도 강건하게 동작.
        marker = re.match(r"^(?:[\-\*\•]\s+|\d+[\.\)]\s+)(.+)$", line)
        if not marker:
            continue
        step = marker.group(1).strip()
        if step:
            items.append(step[:200])  # 비정상적으로 긴 step cap
        if len(items) >= max_items:
            break
    return items


def classify(
    user_msg: str,
    history_tail: list[dict],
    main_model: BaseLLM,
) -> RouterResult:
    """단일 메인-모델 호출로 intent 분류. 실패 시 보수적 fallback."""
    tail_str = _format_history_tail(history_tail)
    user_block = (
        (f"Recent exchange (context):\n{tail_str}\n\n" if tail_str else "")
        + f"Classify this message:\n{user_msg}"
    )

    started = time.monotonic()
    try:
        response = main_model.chat(
            messages=[
                {"role": "system", "content": _ROUTER_SYSTEM},
                {"role": "user", "content": user_block},
            ],
            tools=None,
            temperature=0.0,
            max_tokens=400,  # INTENT 한 줄 + 최대 ~5 step plan 여유있게
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        raw = (response.choices[0].message.content or "").strip()
        intents = _parse(raw)
        plan = _parse_plan(raw)
        if not intents:
            # 파싱 실패 — 보수적 합집합, plan 은 무효화 (잘못된 파싱일 수 있음)
            return RouterResult(
                intents={"CODING", "ISSUE", "TEAM"},
                plan=[],
                latency_ms=latency_ms,
                raw=raw,
                fallback=True,
            )
        return RouterResult(
            intents=intents,
            plan=plan,
            latency_ms=latency_ms,
            raw=raw,
            fallback=False,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return RouterResult(
            intents={"CODING", "ISSUE", "TEAM"},
            plan=[],
            latency_ms=latency_ms,
            raw=f"<error: {type(exc).__name__}: {exc}>",
            fallback=True,
        )
