"""OrchestratorAgent: 통합 코딩 + 이슈조사 + 팀/워크트리 에이전트.

  ┌──────────────────────────────────────────────────────────────────────┐
  │  OrchestratorAgent (unified)                                         │
  │                                                                      │
  │  main_model ──► loop.py  ◄── UNIFIED_TOOLS                           │
  │                    │                                                 │
  │  extra_handlers:   │                                                 │
  │   [meta]     todo / load_skill / task / compact                      │
  │   [issue]    jira_task / bitbucket_task / confluence_task            │
  │              ─────► run_subagent(tools=JIRA/BB/CF_TOOLS, registry)   │
  │                                      │ registry 에 API 핸들러 등록   │
  │                                      ▼                               │
  │                                 JiraClient / BitbucketClient /       │
  │                                 ConfluenceClient                     │
  │   [team]     task_create/list/get/update            (s07)            │
  │              background_run/status                  (s08)            │
  │              spawn_teammate / send_message / ...    (s09)            │
  │              request_shutdown / submit_plan / ...   (s10)            │
  │              worktree_create/list/run/remove/...    (s12)            │
  └──────────────────────────────────────────────────────────────────────┘

단일 진입점. 모델이 intent 로 tool 을 선택한다.
"""

import json
import re
import threading
from pathlib import Path

from model.base import BaseLLM
from tools import definitions
from tools.api import BitbucketClient, ConfluenceClient, JiraClient
from tools.registry import ToolRegistry
from utils.console import (
    clear_activity,
    display_set_todos,
    print_info,
    print_tool_call,
    set_activity,
)

from .background import BackgroundManager
from .compact import CONTEXT_LIMIT, compact_history, estimate_size, micro_compact
from .loop import agent_loop
from .permission import PermissionManager
from .planner import TodoManager
from .router import classify as classify_intent
from .skill import SkillRegistry
from .state import CompactState, LoopState
from .subagent import run_subagent
from .task_manager import TaskManager
from .team import MessageBus, TeammateManager
from .worktree import EventBus, WorktreeManager, _detect_repo_root

_WORKDIR = Path.cwd()
_REPO_ROOT = _detect_repo_root(_WORKDIR) or _WORKDIR


# 사용자 입력에서 "깊게 답해줘" 의도를 감지하는 키워드. gpt-4o 기본 성향이 요약적이라
# 별도 힌트 없으면 bullet list 로 납작하게 답변하는 경향 → 아래 키워드 중 하나가 매칭되면
# 시스템 프롬프트에 depth mode 절 추가.
_DEPTH_RE = re.compile(
    r"("
    # Korean — 명시적 depth 요청 + 구조/흐름/시퀀스 질문
    r"자세히|상세히|구체적으로|심층적?|깊게|분석해|설명해|"
    r"어떻게\s*(?:동작|돌아가|작동|대응|처리|움직|흘러|구성|구현)|"
    r"시퀀스|플로우|흐름|구조|체계|체계적으로|"
    # English
    r"\bin\s?depth\b|\bthoroughly\b|\bdetailed\b|"
    r"\bwalk\s?through\b|\bexplain\s+how\b|"
    r"\bsequence\b|\bflow\b|\barchitecture\b|"
    r"\bhow\s+does\s+(?:\w+\s+){1,4}(?:work|handle|respond|process|behave)"
    r")",
    re.IGNORECASE,
)

_DEPTH_APPENDIX = (
    "\n\n## Response style (this turn — depth mode)\n"
    "User explicitly asked for thorough explanation. Go deeper than usual:\n"
    "- Cite concrete `file:line` references for claims (e.g. `utils/repl.py:292`).\n"
    "- Include 2–4 short code snippets copied verbatim from inspected files (not paraphrased).\n"
    "- Walk through structure/flow in order — not a flat bullet list of unrelated facts.\n"
    "- If the question spans multiple modules, describe how they connect.\n"
    "- Don't close with generic summary; close with actionable insight or next-step pointer.\n"
    "\n"
    "### For sequence / flow / 'how does X respond' questions (STRICT)\n"
    "The answer MUST be a **numbered sequence** of concrete steps:\n"
    "  1. <step name> — `file:line` + 1-line quote of the critical code\n"
    "  2. ...\n"
    "Cover BOTH happy path AND error branches. For API/IO code, explicitly enumerate:\n"
    "  - pre-check (e.g. `configured` property) — which file, what it returns\n"
    "  - HTTP call — request method/endpoint\n"
    "  - exception classes caught — which ones, what each returns\n"
    "  - caller propagation — how the error string reaches the user\n"
    "\n"
    "**Bad answer shape** (do NOT do this):\n"
    "  \"각 클라이언트에서 HTTP 응답 코드와 텍스트를 포함한 에러 메시지를 반환하는 방식으로 처리됩니다.\"\n"
    "  (vague, no file:line, no code, no sequence — reject this shape)\n"
    "\n"
    "**Good answer shape**:\n"
    "  \"1. Config 체크 — `tools/api/bitbucket.py:N`: `if not self.config.configured: return _NOT_CONFIGURED`\n"
    "   2. HTTP 호출 — `requests.get(self._url(...), auth=..., timeout=30)`\n"
    "   3. HTTPError 캐치 — `except requests.HTTPError as exc: return f\\\"Bitbucket API error {status}: {text}\\\"`\n"
    "   ...\"\n"
)


def _has_depth_signal(user_msg: str) -> bool:
    """사용자 메시지에서 depth-dive 의도 키워드 탐지."""
    return bool(_DEPTH_RE.search(user_msg or ""))


def _is_thin_depth_reply(reply: str) -> bool:
    """Depth 턴인데 답변이 구조 없는 generic prose 면 True.

    판정: 아래 evidence-of-effort 신호 중 하나도 없으면 thin.
      - 코드 블록 (``` fenced)
      - 번호 리스트 (`1.` / `2.` 줄 시작)
      - 3개 이상의 source file reference (`foo.py`, `utils/bar.py` 등)

    모델이 "각 클라이언트에서 HTTP 오류를 raise_for_status() 로 감지…" 처럼
    tool 결과 많이 읽고 한 덩어리 문단으로 정리한 케이스를 잡는다.
    """
    if not reply or len(reply) < 50:
        return True
    if "```" in reply:
        return False
    if re.search(r"^\s*\d+\.\s", reply, re.MULTILINE):
        return False
    src_refs = re.findall(r"\b[\w/._-]+\.(?:py|md|toml|json|yaml|yml|sh)\b", reply)
    if len(src_refs) >= 3:
        return False
    return True


class OrchestratorAgent:
    def __init__(
        self,
        main_model: BaseLLM,
        sub_model: BaseLLM,
        skills_dir: Path = _WORKDIR / "skills",
        auto_approve_all: bool = False,
    ):
        self.main_model = main_model
        self.sub_model = sub_model
        self.planner = TodoManager()
        self.skills = SkillRegistry(skills_dir)
        self.compact_state = CompactState()
        self.history: list[dict] = []

        # Tool registry — API 툴을 여기 등록 (subagent 가 dispatch)
        self.registry = ToolRegistry()
        self._jira = JiraClient()
        self._bitbucket = BitbucketClient()
        self._confluence = ConfluenceClient()
        self._register_api_tools()

        # 팀/워크트리/백그라운드 매니저 (s07-s12)
        self.tasks = TaskManager(_REPO_ROOT / ".tasks")
        self.bg = BackgroundManager()
        self.bus = MessageBus(_REPO_ROOT / ".team" / "inbox")
        self.team = TeammateManager(
            team_dir=_REPO_ROOT / ".team",
            bus=self.bus,
            tasks=self.tasks,
            model=sub_model,
            workdir=_WORKDIR,
        )
        events = EventBus(_REPO_ROOT / ".worktrees" / "events.jsonl")
        self.worktrees = WorktreeManager(_REPO_ROOT, self.tasks, events)

        self._extra_handlers = self._build_extra_handlers()

        # Cooperative cancellation — /cancel sets this. agent_loop + run_one_turn
        # check between turns / between tool dispatches. Shared with all
        # subagents so cancel propagates into nested loops.
        self.cancel_event = threading.Event()

        # 파괴적 tool (write_file / edit_file / 위험 bash / worktree_remove) 의
        # 실행 직전 사용자 승인을 요구. REPL 이 이 manager 참조를 받아 키 입력으로
        # approve/deny/auto_session 신호를 보냄.
        # eval.py·CI 같은 비대화형 환경은 `auto_approve_all=True` 로 승인 요청을
        # 즉시 통과시킴.
        self.permissions = PermissionManager(auto_approve_all=auto_approve_all)

        # System prompt is pure function of workdir + skill catalog — both static.
        # Build once, reuse byte-identical across turns so the server-side KV prefix
        # cache (vllm prefix caching / ollama) stays warm.
        self._cached_system_prompt = self._build_system_prompt()

    # ── Registry (API 툴 — subagent 용) ───────────────────────────────────────

    def _register_api_tools(self) -> None:
        """Jira/Bitbucket/Confluence 핸들러를 registry 에 등록.
        orchestrator 자신은 `jira_task/bitbucket_task/confluence_task` delegation 을 통해
        subagent 로 위임하므로, 이 API 들은 subagent 가 dispatch 할 때만 사용됨.
        """
        jira, bb, cf = self._jira, self._bitbucket, self._confluence
        self.registry.register(
            "jira_search",           lambda query, max_results=10: jira.search(query, max_results))
        self.registry.register(
            "jira_search_multi",      lambda queries, max_per_query=20, top_k=30:
                jira.search_multi(queries, max_per_query, top_k))
        self.registry.register(
            "jira_get_issue",         lambda issue_key: jira.get_issue(issue_key))
        self.registry.register(
            "bitbucket_list_commits", lambda keyword="", limit=20: bb.list_commits(keyword, limit))
        self.registry.register(
            "bitbucket_get_commit",   lambda commit_id: bb.get_commit(commit_id))
        self.registry.register(
            "bitbucket_list_prs",     lambda query="", state="ALL": bb.list_prs(query, state))
        self.registry.register(
            "bitbucket_search_multi", lambda queries, commit_limit=50, pr_state="ALL", top_k=20:
                bb.search_multi(queries, commit_limit, pr_state, top_k))
        self.registry.register(
            "bitbucket_get_pr_diff",  lambda pr_id: bb.get_pr_diff(pr_id))
        self.registry.register(
            "bitbucket_compare",      lambda from_ref, to_ref: bb.compare(from_ref, to_ref))
        self.registry.register(
            "confluence_search",      lambda query, max_results=10: cf.search(query, max_results))
        self.registry.register(
            "confluence_get_page",    lambda page_id: cf.get_page(page_id))

    # ── Handler map ───────────────────────────────────────────────────────────

    def _build_extra_handlers(self) -> dict:
        t = self.tasks
        bg = self.bg
        bus = self.bus
        team = self.team
        wt = self.worktrees
        return {
            # 메타
            "todo":       self._handle_todo,
            "load_skill": lambda name: self.skills.load(name),
            "task":       self._handle_task,
            "compact":    self._handle_compact,
            # 이슈조사 delegation
            "jira_task":       self._handle_jira_task,
            "bitbucket_task":  self._handle_bitbucket_task,
            "confluence_task": self._handle_confluence_task,
            # 태스크 그래프 (s07)
            "task_create":  lambda subject, description="": t.create(subject, description),
            "task_list":    lambda: t.list_all(),
            "task_get":     lambda task_id: t.get(task_id),
            "task_update":  lambda task_id, status=None, owner=None,
                                   add_blocked_by=None, remove_blocked_by=None: t.update(
                                task_id, status, owner, add_blocked_by, remove_blocked_by
                            ),
            # 백그라운드 (s08)
            "background_run":    lambda command: bg.run(command),
            "background_status": lambda: bg.status(),
            # 팀메이트 (s09-s11)
            "spawn_teammate":    lambda name, role, prompt: team.spawn(name, role, prompt),
            "send_message":      lambda to, content, type="message": bus.send("lead", to, content, type),
            "read_inbox":        lambda: json.dumps(bus.read_inbox("lead")),
            "broadcast_message": lambda content: bus.broadcast(
                "lead", content, [m["name"] for m in team.config.get("members", [])]
            ),
            "list_team":         lambda: team.list_team(),
            "request_shutdown":       lambda teammate: team.request_shutdown(teammate),
            "respond_shutdown":       lambda request_id, approve, reason="": team.respond_shutdown(request_id, approve, reason),
            "submit_plan":            lambda from_name, plan: team.submit_plan(from_name, plan),
            "review_plan":            lambda request_id, approve, feedback="": team.review_plan(request_id, approve, feedback),
            "list_shutdown_requests": lambda: team.list_shutdown_requests(),
            "list_plan_requests":     lambda: team.list_plan_requests(),
            # 워크트리 (s12)
            "worktree_create": lambda name, task_id=None, base_ref="HEAD": wt.create(name, task_id, base_ref),
            "worktree_list":   lambda: wt.list_all(),
            "worktree_run":    lambda name, command: wt.run(name, command),
            "worktree_keep":   lambda name: wt.keep(name),
            "worktree_remove": lambda name, force=False, complete_task=False: wt.remove(name, force, complete_task),
            "worktree_events": lambda limit=20: wt.list_events(limit),
        }

    # ── System prompts ───────────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """Lean system prompt (~1.5K chars). Everything else lives in skills,
        loaded on demand via `load_skill(name)`. Mirrors learn-claude-code's
        `s_full.py` pattern: core identity + tool hint + skill catalog.
        """
        return (
            f"You are a unified assistant at {_WORKDIR}. Coding, codebase exploration, "
            "Jira/Bitbucket/Confluence issue investigation, team/worktree work — one entry point.\n"
            "Respond in the user's language.\n\n"
            "## Tool selection\n"
            "- Code search  → grep / glob / ls / fuzzy_find   (gitignore-aware; NEVER bash grep/find/ls)\n"
            "- Code edit    → edit_file / write_file          (verify with bash after)\n"
            "- Delegate     → task (general) / jira_task / bitbucket_task / confluence_task\n"
            "- Multi-step   → todo   (cross-session board → task_create / task_list / task_update)\n"
            "- Long command → background_run\n"
            "- Parallel     → worktree_create + worktree_run\n"
            "- Persistent   → spawn_teammate\n"
            "- Escape hatch → bash   (env, git, run — NOT for code exploration)\n\n"
            "## Core rules\n"
            "- Never answer about this codebase from memory — inspect files first.\n"
            "- Scan prior turns before any new search: files/line-numbers already mentioned are pointers.\n"
            "- On search miss / truncated output / survey queries (\"list all\", \"관련 전부\") "
            "→ `load_skill(\"search-iteration\")` before giving up or asking the user to narrow scope.\n"
            "- After `edit_file`/`write_file` → verify with bash (e.g. `uv run python -c 'import X'`).\n"
            "- On command failure → try 2–3 alternatives (`uv run <cmd>`, `python -m <cmd>`, `uv sync`) before blocking.\n"
            "- Use `compact` only when the conversation is genuinely too long.\n"
            "\n"
            "## Evidence-grounded answers (CRITICAL)\n"
            "Behavioral questions (\"어떻게 동작?\", \"X 하면 어떻게 돼?\") MUST be answered by "
            "**tracing actual code**, not by describing what looks plausible.\n"
            "- Every factual claim about behavior should point to `file:line` you actually read.\n"
            "- **Forbidden speculative patterns** (red flags): \"~일 수 있습니다\", \"~일 것입니다\", "
            "\"아마도\", \"could\", \"may\", \"should work\", \"probably\". If you use these, you have NOT "
            "verified — either verify or say \"이 부분은 확인하지 않았습니다\".\n"
            "- **Trace pattern** for \"how does X behave when Y\" questions:\n"
            "  1. `grep` for the feature's **core class/function** (not the orchestrator/loop plumbing).\n"
            "     e.g. \"Bitbucket 키 잘못되면?\" → `grep 'class BitbucketClient'`, NOT `grep 'bitbucket'`.\n"
            "  2. `read_file` the core implementation at the relevant section.\n"
            "  3. Follow the specific code path for the condition (error branch, config check, etc.).\n"
            "  4. Quote the actual code in your answer. Don't paraphrase unless necessary.\n"
            "- Plumbing files (`orchestrator.py`, `loop.py`, `main.py`) describe **routing**, not behavior.\n"
            "  If the user asks about feature behavior, do NOT fixate on plumbing.\n"
            "- **One-line summaries after extensive research = unacceptable**. If you read 10 files/chunks "
            "to answer, your reply must reflect that work. Short answer like \"X 방식으로 처리됩니다\" "
            "without specifics wastes the user's research budget. Either give the details you found, "
            "or admit you couldn't locate them.\n"
            "\n"
            "## Plan & progress tracking (CRITICAL)\n"
            "- A plan may be pre-drafted for you (see Todo section in the live region). Follow it.\n"
            "- **Don't re-read the same file chunk you already saw** — check prior tool results first. "
            "Re-reading identical offsets = you've lost track; step back and synthesize.\n"
            "- For large files (>300 lines), use `grep(pattern=..., path='X')` to locate sections "
            "BEFORE sequential `read_file` pagination. Sequential reads are for short files or "
            "when you already know what you're looking for.\n"
            "- **MANDATORY when a plan exists**: before your final assistant reply, you MUST call "
            "`todo(items=[...])` AT LEAST ONCE to reflect reality — mark each finished step as "
            "`completed`, advance the next step to `in_progress`. If a step turned out unnecessary, "
            "mark it `completed` with a note in your reply. **Skipping this leaves the plan looking "
            "unfinished to the user — that is a user-facing bug.**\n"
            "- **Don't announce and stop.** A reply like \"이제 X 하겠습니다\" / \"I'll do X next\" "
            "followed by `stop` = you gave up mid-plan. Either execute X NOW via tool calls, or mark "
            "it completed with a one-line justification. Announcements without action are treated as a bug.\n"
            "- If the plan was wrong or incomplete, revise via `todo(items=[...])` — don't just ignore it.\n\n"
            f"## Python\n`uv run python` / `uv run <tool>` at {_WORKDIR}.\n\n"
            "## Skills — load early with `load_skill(name)` when the request matches\n"
            f"{self.skills.catalog()}"
        )

    def _system_prompt(self) -> str:
        """Back-compat accessor — returns the cached prompt built in __init__."""
        return self._cached_system_prompt

    def _parent_plan_context(self) -> str:
        """현재 parent todo 중 in_progress item 이 있으면 context block 반환.
        Subagent 가 부모의 어느 step 을 실행하는 중인지 알면 scope 이탈 방지."""
        items = self.planner.state.items
        if not items:
            return ""
        active = next((i for i in items if i.status == "in_progress"), None)
        if not active:
            return ""
        total = len(items)
        done = sum(1 for i in items if i.status == "completed")
        return (
            "\n## Parent plan context\n"
            f"부모 에이전트의 plan: {done}/{total} 완료, 현재 진행 중 단계는\n"
            f"  → **{active.content}**\n"
            "이 delegated task 는 그 단계의 일부다. 스코프 이탈 말고 필요한 증거만 모아서 리턴.\n"
        )

    def _subagent_system_prompt(self) -> str:
        return self._parent_plan_context() + (
            f"/no_think\nYou are a coding subagent at {_WORKDIR}.\n"
            "Complete the delegated task using tools, then return a structured report.\n\n"
            "## Tool selection (pick the right tool, not just bash)\n"
            "- content search  → **`grep`**       (rg-backed, gitignore-aware, no binaries)\n"
            "- file discovery  → **`glob`**       (e.g. '**/*.py')\n"
            "- directory tree  → **`ls`**         (excludes .venv / __pycache__ / node_modules)\n"
            "- fuzzy file name → **`fuzzy_find`** (when exact path unknown)\n"
            "- read file       → `read_file`\n"
            "- escape hatch    → `bash`           (git, env, run — NOT for exploration)\n\n"
            "`grep`/`glob`/`ls`/`fuzzy_find` already filter noise — never use `bash grep` etc.\n\n"
            "## Completeness check (CRITICAL)\n"
            "If a tool result contains `[OUTPUT TRUNCATED`, `[TOOL RESULT TRUNCATED`, `[TRUNCATED at`, "
            "ends with `…`, or looks cut off — it IS incomplete. Re-run with narrower scope / higher limit "
            "(head_limit, glob filter, specific path). Never treat a truncated result as final.\n\n"
            "## Query expansion on search miss (CRITICAL)\n"
            "If `grep`/`fuzzy_find` returns `(no matches)`, try 2+ more variations BEFORE giving up:\n"
            "  - Translate: Korean concept ↔ English code term ('상태표시줄' → status/statusline/_status)\n"
            "  - Code-style identifiers: 'icon' → `_ICON`, 'color' → `#[0-9A-F]` or theme/style, 'folder' → dir/directory\n"
            "  - Relax the pattern: remove word boundaries, try prefix/substring matches\n"
            "Then fall back to `ls` + `read_file` on candidate dirs (utils/, src/, agent/) if still empty.\n\n"
            "## Thoroughness\n"
            "Structure/listing tasks demand the FULL picture. `ls(depth=3)` beats `ls(depth=1)`. "
            "'where is X' → grep the repo, read the top hits. Iterate until the delegated task is concretely answered. "
            "NEVER conclude \"couldn't find it\" without trying 3+ query variants and reading at least 2 candidate files.\n\n"
            "## Return format (REQUIRED)\n"
            "End with a message in exactly this structure:\n"
            "```\n"
            "## Summary\n"
            "<2–6 sentences answering the delegated task>\n\n"
            "## Evidence\n"
            "<key findings as short bullets — concrete facts: file paths, line numbers, counts, quoted snippets>\n"
            "```\n"
            "Do NOT return prose without the headings. The parent agent will extract both sections."
        )

    def _issue_subagent_prompt(self, source: str) -> str:
        """Jira/Bitbucket/Confluence 전용 subagent 프롬프트.

        source: "jira" | "bitbucket" | "confluence"
        """
        common_tail = (
            "\n## Completeness check\n"
            "If a result contains `not configured` or `connection error`, report that and stop — "
            "don't retry indefinitely. Otherwise, if output is truncated or empty, retry with "
            "relaxed keywords / different fields / wider date range before concluding 'nothing found'.\n\n"
            "## Query expansion on miss (CRITICAL)\n"
            "Natural-language issue description ≠ literal keywords in tickets/commits. Try:\n"
            "- Korean ↔ English ('로그인 에러' ↔ 'login error', 'null 참조' ↔ 'NullPointerException')\n"
            "- Component names, error class names, stack trace signatures\n"
            "- Broader → narrower: start with 2-3 broad keywords, then refine based on what you find\n"
            "Try 3+ search variations before giving up.\n\n"
            "## Return format (REQUIRED)\n"
            "End with:\n```\n## Summary\n<2–6 sentences answering the delegated investigation>\n\n"
            "## Evidence\n<concrete findings: issue keys / commit ids / page ids + titles + dates "
            "+ 1-line relevance notes>\n```"
        )
        if source == "jira":
            body = (
                f"/no_think\nYou are a Jira investigation subagent at {_WORKDIR}.\n"
                "## Primary pattern — keyword decomposition + multi-search + aggregation\n"
                "1. Decompose the issue description into 4–8 query variants:\n"
                "   - full phrase (as given)\n"
                "   - individual content words (nouns, verbs, error terms)\n"
                "   - Korean↔English translations ('시간 초과' ↔ 'timeout', '오류' ↔ 'error')\n"
                "   - error class names, HTTP status codes, component identifiers\n"
                "2. `jira_search_multi(queries=[...])` — ONE call, returns issues ranked by match_score.\n"
                "3. `jira_get_issue(key)` on the TOP 2–3 (highest match_score) for full detail:\n"
                "   description, comments, issuelinks, fix_versions, resolution, attachments, subtasks.\n"
                "4. Extract evidence: issue keys, status, priority, reporter/assignee, resolution,\n"
                "   linked PR/commit refs (often in description/comments/issuelinks), fix version + release date.\n"
                "Prefer `jira_search_multi` over multiple `jira_search` calls — aggregated score is stronger signal.\n"
            )
        elif source == "bitbucket":
            body = (
                f"/no_think\nYou are a Bitbucket code-analysis subagent at {_WORKDIR}.\n"
                "## Primary pattern — multi-keyword search then diff drilldown\n"
                "1. `bitbucket_search_multi(queries=[...])` with the SAME keyword variants used for jira_search_multi.\n"
                "   Returns ranked commits + PRs with match_score (how many keywords converged on each).\n"
                "2. For top candidates (match_score ≥ 2/N), pull actual diffs:\n"
                "   - PR candidate → `bitbucket_get_pr_diff(pr_id)`\n"
                "   - Commit candidate → `bitbucket_get_commit(commit_id)` (returns metadata + full diff)\n"
                "3. `bitbucket_compare(from_ref, to_ref)` for release-range diffs when user mentions a deploy "
                "(e.g. 'v1.2.0..v1.3.0' or 'main..feature').\n"
                "4. Analyze diff hunks — quote file:line + before/after for changes likely related to the symptom "
                "(timeout/retry values, config constants, race conditions, boundary checks, new sync calls).\n"
                "Prefer fetching actual diffs over guessing from commit messages — the diff is the evidence.\n"
            )
        elif source == "confluence":
            body = (
                f"/no_think\nYou are a Confluence documentation subagent at {_WORKDIR}.\n"
                "Given an issue description:\n"
                "1. Use `confluence_search` with issue keywords + words like 'incident', 'runbook', 'postmortem', "
                "'architecture', 'known issue'.\n"
                "2. Use `confluence_get_page` on top hits to read their content.\n"
                "3. Extract: page titles, urls, space, last_modified, key excerpts relevant to the issue.\n"
            )
        else:
            body = f"/no_think\nYou are an investigation subagent ({source}) at {_WORKDIR}.\n"
        return self._parent_plan_context() + body + common_tail

    # ── Extra-handler implementations ────────────────────────────────────────

    def _handle_todo(self, items: list) -> str:
        result = self.planner.update(items)
        # live display 에 push — 상태줄 위 Todo 섹션이 실시간 갱신됨
        display_set_todos([
            {"content": i.content, "status": i.status, "active_form": i.active_form}
            for i in self.planner.state.items
        ])
        return result

    def _handle_task(self, prompt: str, description: str = "subtask") -> str:
        return run_subagent(
            prompt=prompt,
            model=self.sub_model,
            registry=self.registry,
            system=self._subagent_system_prompt(),
            description=description,
            cancel_event=self.cancel_event,
        )

    def _handle_jira_task(self, prompt: str) -> str:
        print_info("[jira_task] subagent 시작")
        return run_subagent(
            prompt=prompt,
            model=self.sub_model,
            registry=self.registry,
            system=self._issue_subagent_prompt("jira"),
            description="jira",
            tools=definitions.JIRA_TOOLS,
            cancel_event=self.cancel_event,
        )

    def _handle_bitbucket_task(self, prompt: str) -> str:
        print_info("[bitbucket_task] subagent 시작")
        return run_subagent(
            prompt=prompt,
            model=self.sub_model,
            registry=self.registry,
            system=self._issue_subagent_prompt("bitbucket"),
            description="bitbucket",
            tools=definitions.BITBUCKET_TOOLS,
            cancel_event=self.cancel_event,
        )

    def _handle_confluence_task(self, prompt: str) -> str:
        print_info("[confluence_task] subagent 시작")
        return run_subagent(
            prompt=prompt,
            model=self.sub_model,
            registry=self.registry,
            system=self._issue_subagent_prompt("confluence"),
            description="confluence",
            tools=definitions.CONFLUENCE_TOOLS,
            cancel_event=self.cancel_event,
        )

    def _handle_compact(self, focus: str | None = None) -> str:
        if estimate_size(self.history) < CONTEXT_LIMIT // 2:
            return "Not needed. Stop calling tools and reply to the user directly."
        self.history[:] = compact_history(
            self.history, self.compact_state, self.main_model, focus=focus
        )
        return "Conversation compacted."

    # ── Main entry point ─────────────────────────────────────────────────────

    def cancel(self) -> None:
        """Signal cooperative cancellation. The current turn's agent_loop and
        any nested subagents will exit at the next turn boundary / tool dispatch.
        In-flight LLM HTTP calls complete normally (their result is discarded)."""
        self.cancel_event.set()

    def run(self, user_input: str) -> str:
        """Process one user message and return the final assistant reply."""
        # Fresh turn — clear any leftover cancel from a prior run.
        self.cancel_event.clear()

        # 이전 턴의 plan 이 전부 완료 상태면 historical — 현재 턴과 무관하므로 라이브
        # 리전에서 치움. 미완료 item 이 남아있으면 사용자가 이어갈 수도 있으니 보존.
        # Router 가 이번 턴에 새 plan 을 draft 하면 어차피 `_handle_todo` 가 교체하므로
        # 여기서의 클리어는 "no-plan 후속 질문" 케이스에만 실제 효과.
        items = self.planner.state.items
        if items and all(i.status == "completed" for i in items):
            self._handle_todo([])  # planner.update + display_set_todos([]) 둘 다 처리

        self.history.append({"role": "user", "content": user_input})

        # Drain background notifications before the next model call (s08)
        notifs = self.bg.drain()
        if notifs:
            notif_text = "\n".join(f"[bg:{n['task_id']}] {n['result']}" for n in notifs)
            self.history.append({
                "role": "user",
                "content": f"<background-results>\n{notif_text}\n</background-results>",
            })

        # Apply compaction before each turn
        self.history[:] = micro_compact(self.history)
        if estimate_size(self.history) > CONTEXT_LIMIT:
            print_info("[auto-compact]")
            self.history[:] = compact_history(
                self.history, self.compact_state, self.main_model
            )

        # Intent router — main 모델로 분류해서 tier 별 tools 만 노출.
        # compact 이후 호출 → 라우터가 받는 history tail 도 이미 정돈된 상태.
        set_activity("routing intent…")
        try:
            route = classify_intent(
                user_msg=user_input,
                history_tail=self.history[-6:-1],  # 방금 append 한 user 제외한 직전 교환
                main_model=self.main_model,
            )
        finally:
            clear_activity()
        tools = definitions.tools_for_tier(route.intents)
        # bullet point 실시간 로그 — tool call 과 동일한 `⎿` 스타일
        fallback_tag = " [fallback]" if route.fallback else ""
        print_tool_call(
            "router",
            f"{route.label()} → {len(tools)} tools  ({route.latency_ms}ms){fallback_tag}",
        )

        # Router 가 plan 을 함께 뽑았으면 TodoManager 에 바로 주입 + 라이브 리전 노출.
        # 첫 step 은 자동으로 in_progress 로 승격 — 모델이 "어디서부터 시작?" 헤매지 않게.
        if route.plan:
            items = [{"content": s, "status": "pending", "active_form": s} for s in route.plan]
            items[0]["status"] = "in_progress"
            self._handle_todo(items)  # planner.update + display_set_todos 모두 처리
            print_tool_call("router", f"plan drafted: {len(route.plan)} steps")

        # Depth-dive 의도 키워드 ("자세히", "설명해", "in depth" 등) 감지 시에만
        # 시스템 프롬프트에 depth mode 절 첨부. 짧은 질문은 기존대로 간결 응답.
        # 기본 프롬프트는 byte-identical 하게 캐시되고 appendix 만 turn-local.
        system_prompt = self._system_prompt()
        if _has_depth_signal(user_input):
            system_prompt += _DEPTH_APPENDIX
            print_tool_call("router", "depth mode enabled")

        state = LoopState(messages=self.history, cancel_event=self.cancel_event)
        agent_loop(
            state=state,
            model=self.main_model,
            tools=tools,
            registry=self.registry,
            system=system_prompt,
            extra_handlers=self._extra_handlers,
            stream_to_console=True,  # 라이브 리전에 토큰 스트림, 최종은 print_assistant 가 커밋
            permissions=self.permissions,  # 파괴적 tool 은 사용자 승인 경유
        )

        # Plan 감사 — 이번 턴에 router 가 plan 을 draft 했는데 planner 에 non-completed
        # 항목이 남아있으면 "plan 미완료" 상태. 모델에 합성 user 메시지로 continuation
        # 요청 후 한 번 더 짧게 돌림.
        #
        # 조건에서 `not state.todo_called` 뺌 — 모델이 todo 한 번 부르고 "이제 X 하겠습니다"
        # 선언 후 stop 하는 패턴이 대표적 누락 케이스라, todo 호출 여부와 무관하게
        # plan 완료 여부 자체를 기준으로 삼음. `route.plan` 로 "이번 턴에 새로 draft 됐는지"
        # 체크해서 과거 턴의 잔여 plan 은 nudge 대상 제외 (사용자가 무관한 주제로 넘어간 경우).
        plan_drafted_this_turn = bool(route.plan)
        incomplete = [i for i in self.planner.state.items if i.status != "completed"]
        if plan_drafted_this_turn and incomplete:
            lines = "\n".join(f"  - [{i.status}] {i.content}" for i in self.planner.state.items)
            self.history.append({
                "role": "user",
                "content": (
                    "[SYSTEM audit — plan incomplete]\n"
                    "Current plan:\n"
                    f"{lines}\n\n"
                    "Do NOT end the turn with only 'I'll do X next' — that's an announcement, not execution.\n"
                    "Choose ONE:\n"
                    "  (A) Execute the remaining steps NOW via tool calls, then mark them completed.\n"
                    "  (B) If a step is genuinely not applicable, mark it completed in `todo()` "
                    "with a one-line justification in your final reply.\n"
                    "  (C) If the work genuinely needs user input to proceed, mark what's done and ask "
                    "ONE specific question.\n"
                    "Don't re-do completed work. Don't speculate — keep answers evidence-grounded."
                ),
            })
            nudge_state = LoopState(
                messages=self.history,
                cancel_event=self.cancel_event,
            )
            agent_loop(
                state=nudge_state,
                model=self.main_model,
                tools=tools,
                registry=self.registry,
                system=system_prompt,
                extra_handlers=self._extra_handlers,
                stream_to_console=True,
                permissions=self.permissions,
                max_turns=10,  # 실제 남은 step 실행하려면 여유 필요 (grep/read/analyze 조합)
            )

        # Depth shape audit — depth-mode 턴인데 모델이 증거 잔뜩 읽고 generic 한 문단으로
        # 마무리한 경우 (structured output 무시) → 재포맷만 요청. tool 재호출 유도 X.
        if _has_depth_signal(user_input):
            last_reply = ""
            for msg in reversed(self.history):
                if msg.get("role") == "assistant" and msg.get("content"):
                    last_reply = msg["content"]
                    break
            if _is_thin_depth_reply(last_reply):
                self.history.append({
                    "role": "user",
                    "content": (
                        "[SYSTEM audit — answer shape rejected]\n"
                        "You gathered substantial evidence (multiple read_file + grep calls) but your "
                        "final reply is a single paragraph without structure or citations. That wastes "
                        "the research budget.\n\n"
                        "**Re-format your previous answer** using ONLY the tool results already in this turn. "
                        "Do NOT call read_file / grep / any other data-gathering tool — you have enough.\n\n"
                        "Required format:\n"
                        "1. `file.py:line` — one-line code quote of the critical logic\n"
                        "2. `file.py:line` — ...\n"
                        "3. ...\n\n"
                        "Cover the full flow (pre-check → HTTP call → exception branches → caller propagation). "
                        "Each step MUST cite a specific file:line you inspected and quote ≤2 lines of code verbatim."
                    ),
                })
                reshape_state = LoopState(
                    messages=self.history,
                    cancel_event=self.cancel_event,
                )
                agent_loop(
                    state=reshape_state,
                    model=self.main_model,
                    tools=tools,
                    registry=self.registry,
                    system=system_prompt,
                    extra_handlers=self._extra_handlers,
                    stream_to_console=True,
                    permissions=self.permissions,
                    max_turns=3,  # reformat 뿐이라 2–3 턴이면 충분
                )

        # Track whether todo was updated this turn for the planner nudge
        used_todo = state.todo_called
        self.planner.note_round(used_todo=used_todo)

        # Return last non-empty assistant reply
        for msg in reversed(self.history):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return ""
