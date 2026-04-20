#!/usr/bin/env python3
"""OrchestratorAgent 평가 스크립트 — 큐레이션된 시나리오로 intent routing · tool 선택 · 결과 품질 확인.

사용법:
    uv run python eval.py                    # 전체 시나리오
    uv run python eval.py --only search      # 이름 substring 필터
    uv run python eval.py --verbose          # 응답 본문 + tool 호출 전체 출력
    uv run python eval.py --list             # 시나리오 목록만 출력하고 종료

각 시나리오:
  1) fresh OrchestratorAgent 생성
  2) agent.run(prompt) 실행
  3) 검사 항목:
       - must_call      — 반드시 호출되어야 하는 tool 이름들
       - must_not_call  — 호출되면 안 되는 tool 이름들 (intent routing 위반 감지)
       - grader(reply)  — 최종 응답 또는 파일 결과에 대한 사용자 정의 검증

종료 코드 = 실패한 시나리오 수 (0 == 전체 통과).
주의: 실제 OPENAI_API_KEY · vLLM 서버가 필요하고 매 시나리오마다 LLM 호출 비용이 발생.
"""

import argparse
import json
import shutil
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from agent import OrchestratorAgent
from model import OpenAIModel, VLLMModel

load_dotenv(override=True)

SCRATCH = Path(".eval_scratch")
RUNS_DIR = Path(".eval_runs")


# ── types ──────────────────────────────────────────────────────────────────

@dataclass
class Scenario:
    name: str
    prompt: str
    must_call: list[str] = field(default_factory=list)
    must_not_call: list[str] = field(default_factory=list)
    grader: Callable[[str, list[dict]], tuple[bool, str]] | None = None


@dataclass
class Result:
    name: str
    ok: bool
    reason: str
    elapsed: float
    calls: list[str]


# ── helpers ────────────────────────────────────────────────────────────────

def _tool_calls(history: list[dict]) -> list[str]:
    return [
        (tc.get("function") or {}).get("name", "")
        for msg in history
        for tc in (msg.get("tool_calls") or [])
    ]


def _grade(scenario: Scenario, reply: str, history: list[dict]) -> tuple[bool, str]:
    calls = _tool_calls(history)
    missing = [t for t in scenario.must_call if t not in calls]
    if missing:
        return False, f"missing required tool(s): {missing}"
    forbidden_hit = [t for t in scenario.must_not_call if t in calls]
    if forbidden_hit:
        return False, f"forbidden tool(s) called: {forbidden_hit}"
    if scenario.grader:
        ok, msg = scenario.grader(reply, history)
        if not ok:
            return False, f"grader: {msg}"
    return True, "ok"


def reply_contains(*needles: str) -> Callable[[str, list[dict]], tuple[bool, str]]:
    def g(reply: str, history: list[dict]) -> tuple[bool, str]:
        del history
        missing = [n for n in needles if n.lower() not in reply.lower()]
        return (not missing, f"missing {missing}" if missing else "")
    return g


def reply_contains_any(*needles: str) -> Callable[[str, list[dict]], tuple[bool, str]]:
    """At least one needle must appear — tolerant to multiple valid answers."""
    def g(reply: str, history: list[dict]) -> tuple[bool, str]:
        del history
        lower = reply.lower()
        if any(n.lower() in lower for n in needles):
            return True, ""
        return False, f"none of {list(needles)} found"
    return g


def file_has(path: Path, needle: str) -> Callable[[str, list[dict]], tuple[bool, str]]:
    def g(reply: str, history: list[dict]) -> tuple[bool, str]:
        del reply, history
        if not path.exists():
            return False, f"{path} not created"
        if needle not in path.read_text(encoding="utf-8"):
            return False, f"{path} missing {needle!r}"
        return True, ""
    return g


# ── scenarios ──────────────────────────────────────────────────────────────

SCENARIOS: list[Scenario] = [
    Scenario(
        name="read_readme",
        prompt="README.md 의 첫 번째 H1 제목을 원문 그대로 한 줄로 알려줘.",
        must_call=["read_file"],
        must_not_call=["task", "jira_task", "worktree_create", "spawn_teammate"],
        grader=reply_contains("multi-agent"),
    ),
    Scenario(
        name="grep_for_class",
        prompt="repo 에서 'class OpenAIModel' 이 정의된 파일 경로를 grep 으로 찾아서 알려줘.",
        must_call=["grep"],
        must_not_call=["bash", "task"],
        grader=reply_contains("openai_model.py"),
    ),
    Scenario(
        name="write_file_scratch",
        prompt=f"'{SCRATCH}/hello.txt' 파일을 만들고 정확히 'hello from eval' 이라고만 써줘.",
        must_call=["write_file"],
        grader=file_has(SCRATCH / "hello.txt", "hello from eval"),
    ),
    Scenario(
        name="todo_multistep",
        prompt=(
            "agent/loop.py 와 agent/subagent.py 두 파일을 각각 한 문단으로 요약해줘. "
            "여러 단계이니 todo 로 각 파일을 항목으로 관리하면서 진행해."
        ),
        must_call=["todo", "read_file"],
        grader=reply_contains("loop.py", "subagent.py"),
    ),
    Scenario(
        name="no_bash_for_search",
        prompt="repo 에 'VLLMModel' 이라는 식별자가 몇 번 등장하는지 세줘.",
        must_call=["grep"],
        must_not_call=["bash"],
    ),
    Scenario(
        name="korean_query_expansion",
        prompt="'상태표시줄' 관련 코드가 있는 파일 하나만 찾아서 경로를 알려줘.",
        must_call=["grep"],
        # 합리적 답이 여러 개 — repl.py (실제 UI), console.py (display manager), orchestrator.py (prompt 내 언급)
        grader=reply_contains_any("repl.py", "console.py", "orchestrator.py"),
    ),
]


# ── runner ─────────────────────────────────────────────────────────────────

def _build() -> OrchestratorAgent:
    return OrchestratorAgent(
        main_model=OpenAIModel(),
        sub_model=VLLMModel(),
        skills_dir=Path("skills"),
        # eval 은 비대화형 — 파괴적 tool 승인 프롬프트 없이 즉시 통과.
        auto_approve_all=True,
    )


def _run_one(s: Scenario, verbose: bool, run_dir: Path) -> Result:
    try:
        agent = _build()
    except Exception as e:
        return Result(s.name, False, f"agent init: {type(e).__name__}: {e}", 0.0, [])
    t0 = time.monotonic()
    reply = ""
    try:
        reply = agent.run(s.prompt)
    except Exception as e:
        if verbose:
            traceback.print_exc()
        _persist_scenario(run_dir, s, reply, agent.history, error=e)
        return Result(
            s.name, False, f"raised {type(e).__name__}: {e}",
            time.monotonic() - t0, _tool_calls(agent.history),
        )
    elapsed = time.monotonic() - t0
    calls = _tool_calls(agent.history)
    ok, msg = _grade(s, reply, agent.history)
    _persist_scenario(run_dir, s, reply, agent.history)
    if verbose:
        print(f"\n--- {s.name} reply ---\n{reply}\n--- calls: {calls}\n")
    return Result(s.name, ok, msg, elapsed, calls)


def _persist_scenario(
    run_dir: Path,
    s: Scenario,
    reply: str,
    history: list[dict],
    error: Exception | None = None,
) -> None:
    """Save per-scenario artefacts: reply text + full history JSONL (+ error if any)."""
    (run_dir / f"{s.name}.reply.txt").write_text(reply or "", encoding="utf-8")
    with (run_dir / f"{s.name}.history.jsonl").open("w", encoding="utf-8") as f:
        for msg in history:
            f.write(json.dumps(msg, default=str, ensure_ascii=False) + "\n")
    if error is not None:
        (run_dir / f"{s.name}.error.txt").write_text(
            f"{type(error).__name__}: {error}\n\n{traceback.format_exc()}",
            encoding="utf-8",
        )


def main() -> int:
    p = argparse.ArgumentParser(description="Evaluate OrchestratorAgent across curated scenarios.")
    p.add_argument("--only", help="substring filter on scenario names")
    p.add_argument("--verbose", action="store_true", help="print full reply + tool trace per scenario")
    p.add_argument("--list", action="store_true", help="list scenarios and exit")
    args = p.parse_args()

    scenarios = SCENARIOS if not args.only else [s for s in SCENARIOS if args.only in s.name]

    if args.list:
        for s in scenarios:
            must = ",".join(s.must_call) or "-"
            print(f"  {s.name:28s}  must_call=[{must}]  {s.prompt[:60]}…")
        return 0

    if not scenarios:
        print(f"No scenarios matched --only={args.only!r}")
        return 1

    SCRATCH.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = RUNS_DIR / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running {len(scenarios)} scenario(s) → {run_dir}/\n")
    results: list[Result] = []
    for s in scenarios:
        print(f"▶ {s.name:28s} … ", end="", flush=True)
        r = _run_one(s, args.verbose, run_dir)
        results.append(r)
        mark = "PASS" if r.ok else "FAIL"
        print(f"{mark}  ({r.elapsed:5.1f}s)  {r.reason}")

    shutil.rmtree(SCRATCH, ignore_errors=True)

    passed = sum(1 for r in results if r.ok)
    total = len(results)
    summary = {
        "timestamp": timestamp,
        "passed": passed,
        "total": total,
        "results": [asdict(r) for r in results],
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\n{passed}/{total} passed")
    if passed < total:
        print("\nFailures:")
        for r in results:
            if not r.ok:
                print(f"  ✗ {r.name}: {r.reason}")
                print(f"      calls: {r.calls}")
    print(f"\n→ results saved to {run_dir}/")
    print(f"  - summary.json            (전체 pass/fail + 실행 시간)")
    print(f"  - <scenario>.reply.txt    (최종 응답 본문)")
    print(f"  - <scenario>.history.jsonl (tool call/result 포함 전체 turn)")
    return total - passed


if __name__ == "__main__":
    sys.exit(main())
