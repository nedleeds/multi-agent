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
from pathlib import Path

from model.base import BaseLLM
from tools import definitions
from tools.api import BitbucketClient, ConfluenceClient, JiraClient
from tools.registry import ToolRegistry
from utils.console import display_set_todos, print_info

from .background import BackgroundManager
from .compact import CONTEXT_LIMIT, compact_history, estimate_size, micro_compact
from .loop import agent_loop
from .planner import TodoManager
from .skill import SkillRegistry
from .state import CompactState, LoopState
from .subagent import run_subagent
from .task_manager import TaskManager
from .team import MessageBus, TeammateManager
from .worktree import EventBus, WorktreeManager, _detect_repo_root

_WORKDIR = Path.cwd()
_REPO_ROOT = _detect_repo_root(_WORKDIR) or _WORKDIR


class OrchestratorAgent:
    def __init__(
        self,
        main_model: BaseLLM,
        sub_model: BaseLLM,
        skills_dir: Path = _WORKDIR / "skills",
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

    def _system_prompt(self) -> str:
        return (
            f"You are a unified assistant at {_WORKDIR}. You handle coding, codebase exploration, "
            "issue investigation (Jira/Bitbucket/Confluence), and team-based work (parallel "
            "tasks/worktrees/teammates) from a single entry point.\n"
            "Always respond in the same language the user writes in.\n\n"
            "## Intent routing — pick the tool that matches the user's request\n"
            "| Intent / 요청 유형                                | Primary tools |\n"
            "| ------------------------------------------------- | ------------- |\n"
            "| Search code / locate definition / 코드 찾기       | **grep, glob, fuzzy_find, ls, read_file** |\n"
            "| Modify code / 수정                                | edit_file, write_file  (+ bash 검증) |\n"
            "| Multi-step work / 여러 단계                       | **todo** |\n"
            "| Delegate exploration / 독립 탐색 위임             | **task** |\n"
            "| Issue investigation (Jira) / 유사 이슈·티켓       | **jira_task** |\n"
            "| Commit/PR history (Bitbucket) / 코드 변경 이력    | **bitbucket_task** |\n"
            "| Runbook / docs (Confluence) / 문서·런북           | **confluence_task** |\n"
            "| Long command (build/test) / 장시간 명령           | **background_run** |\n"
            "| Parallel / isolated branch / 병렬·격리 작업       | **worktree_create + worktree_run** |\n"
            "| Persistent worker / 영속 팀메이트                 | **spawn_teammate** |\n"
            "| Cross-session task board / 세션간 태스크 관리     | **task_create / task_list / task_update** |\n"
            "| Anything else (env, git, run)                     | bash |\n\n"
            "**Issue mode triggers**: \"현장 이슈\", \"장애\", \"버그 분석\", \"유사 사례\", \"incident\". "
            "→ fire `jira_task` + `bitbucket_task` + `confluence_task` in parallel, then synthesize. "
            "Final report structure: `## 이슈 요약` `## Jira` `## 코드 변경` `## 문서` `## 종합 판단`.\n"
            "**Team mode triggers**: \"병렬로\", \"여러 일 동시에\", \"teammate\", \"격리\", \"worktree\". "
            "→ `task_create` 로 work items 분해 → `worktree_create` 로 격리 → `spawn_teammate` 로 실행자 할당.\n"
            "**Default (coding / Q&A)**: BASE + SEARCH 만 사용. 팀·이슈 툴 호출 금지.\n\n"
            "## Core principles\n"
            "- Never answer about this codebase from memory. Inspect actual files first.\n"
            "- Use `compact` ONLY when the conversation is genuinely too long — never on short exchanges.\n\n"
            "## Search tool cheat sheet\n"
            "| Intent                          | Tool                      | Example |\n"
            "| ------------------------------- | ------------------------- | ------- |\n"
            "| content search (regex/string)   | **`grep`**                | grep(pattern='def foo', type='py') |\n"
            "| find files by name pattern      | **`glob`**                | glob(pattern='**/*.py') |\n"
            "| directory tree / structure      | **`ls`**                  | ls(path='.', depth=3) |\n"
            "| find file when name is fuzzy    | **`fuzzy_find`**          | fuzzy_find(query='repl') |\n"
            "| read one file                   | **`read_file`**           | read_file(path='utils/repl.py') |\n"
            "| modify one file                 | `write_file` / `edit_file`| — |\n"
            "| anything else (env, run, git)   | `bash`                    | bash(command='git log -n5') |\n\n"
            "`grep` / `glob` / `ls` / `fuzzy_find` already respect .gitignore and skip "
            "`.venv`, `__pycache__`, `node_modules`, `.git`, binaries, etc. — DO NOT use `bash grep` / "
            "`bash find` / `bash ls` for code exploration: you'll drown in noise.\n\n"
            "## Reasoning — before you call a tool\n"
            "Briefly plan: what's the target? which tool? which pattern/filter narrows it fastest?\n"
            "For 'where is X' questions: first `grep` or `fuzzy_find`, then `read_file` on hits. "
            "For 'project structure': `ls` with depth 2–3, not a single-level directory listing. "
            "For 'what does X do': grep to find the definition, then read the file around those line numbers.\n\n"
            "## Use conversation context as pointers (ALWAYS do this first)\n"
            "Before starting any new search, scan the prior turns:\n"
            "- Did you or the user already mention a specific file? → `read_file` it FIRST.\n"
            "- Did a prior `grep` locate a line number? → read around it, don't re-grep.\n"
            "- Is the user's current question a follow-up to something already discussed? → assume same module.\n"
            "The history is your cheat sheet — don't re-discover what's already answered.\n\n"
            "## Query expansion — user intent ≠ code identifier (CRITICAL)\n"
            "Natural-language intent rarely appears verbatim in code. Translate before searching:\n"
            "  \"status bar\" / \"상태표시줄\"  → `status`, `statusline`, `status_line`, `status\\.`\n"
            "  \"icon\" / \"아이콘\"            → `icon`, `ICON`, `_ICON`\n"
            "  \"color\" / \"색상\"             → `color`, `#[0-9A-Fa-f]`, `theme`, `style`\n"
            "  \"folder\" / \"폴더\"            → `folder`, `dir`, `directory`\n"
            "  \"input\" / \"입력\"             → `input`, `buffer`, `prompt`\n"
            "If a search returns `(no matches)`, IMMEDIATELY retry with at least 2 more variations "
            "(translation, code-style identifier, abbreviated form) BEFORE concluding. "
            "Korean concept? Try English code terms too, and vice versa.\n\n"
            "## Tool output — completeness check (CRITICAL)\n"
            "Every tool result MUST be checked before you use it:\n"
            "- If output contains `[OUTPUT TRUNCATED`, `[TOOL RESULT TRUNCATED`, `[TRUNCATED at`, "
            "ends with `…`, or mentions more items — the result is INCOMPLETE. Do NOT answer from it.\n"
            "- Re-run with pagination / narrower scope: raise `head_limit`, use `output_mode='files_with_matches'` "
            "first then drill down, add `glob`/`type` filter, narrow `path`, or increase `read_file`'s `limit`.\n\n"
            "## Never ask the user to narrow scope before exhausting tools\n"
            "\"I couldn't find it, could you clarify?\" is a failure — NOT a first response. Before asking, do ALL of:\n"
            "  1. Check conversation context — read any files/identifiers already mentioned in prior turns\n"
            "  2. Try 3+ grep variations (synonyms, translations, code-style identifiers)\n"
            "  3. `ls` the top 2–3 candidate directories (utils/, src/, agent/, components/, …) and `read_file` the most promising hits\n"
            "Only after this do you ask the user — and when you do, REPORT what you tried and what you found, "
            "not a bare \"couldn't find\".\n\n"
            "## Survey / exploration queries — \"related / 관련 / 전부 / list all / 모두\" (CRITICAL)\n"
            "Questions like \"what's related to X?\", \"list all Y\", \"시스템 프롬프트 관련 파일들\", "
            "\"해당 기능 전부\", \"related parts\" require a COMPREHENSIVE answer. Process:\n"
            "  1. Multiple grep variations — at least 3 patterns (synonyms, code-style, translation).\n"
            "  2. Read EACH hit file — not just the first one. A survey has multiple answers by definition.\n"
            "  3. Categorize findings by purpose/role.\n"
            "  4. Report each finding as `file:line — 1-line purpose` with at least 3 entries "
            "(or fewer + explicit search trail: \"searched X/Y/Z, only N relevant\").\n"
            "For broad surveys, consider `task(prompt='Find all X across the repo — list file:line + purpose per hit')` "
            "to delegate exhaustive exploration to a subagent.\n\n"
            "## Before emitting a final answer — thoroughness bar\n"
            "Before replying, especially to survey/exploration/list queries, verify:\n"
            "  ☐ 3+ search patterns attempted (if searching was needed)\n"
            "  ☐ ≥2 candidate files actually read (not just grep matches)\n"
            "  ☐ Answer cites concrete `file:line` references (not a single generic statement)\n"
            "  ☐ For \"all X\" queries — answer lists multiple entries, not one\n"
            "If any box is unchecked, iterate MORE before answering.\n\n"
            "## After making code changes\n"
            "MANDATORY: after every edit_file or write_file, run bash to verify — e.g. "
            "`uv run python -c 'import <module>'`. Do NOT claim success without verification.\n\n"
            "## When a command fails\n"
            "Never ask the user how to proceed on command errors. Try alternatives yourself:\n"
            "- Command not found → `uv run <cmd>` or `python -m <cmd>`\n"
            "- Import error → `uv sync` or inspect installed packages\n"
            "- Permission error → try a different approach\n"
            "Exhaust at least 2–3 alternatives before telling the user you're blocked.\n\n"
            "## Python environment\n"
            f"This project uses `uv`. Run Python as `uv run python` or `uv run <tool>` at {_WORKDIR}.\n\n"
            "## Skills — playbooks for recurring task types\n"
            "Call `load_skill(name)` EARLY when the user's request matches a skill's description. "
            "Skills give you concrete step-by-step procedures — load once, then follow. "
            "Don't reinvent the process each time. Loading happens BEFORE the first exploration tool call, "
            "not after you've already started.\n\n"
            f"Available skills:\n{self.skills.catalog()}"
        )

    def _subagent_system_prompt(self) -> str:
        return (
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
        return body + common_tail

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
        )

    def _handle_compact(self, focus: str | None = None) -> str:
        if estimate_size(self.history) < CONTEXT_LIMIT // 2:
            return "Not needed. Stop calling tools and reply to the user directly."
        self.history[:] = compact_history(
            self.history, self.compact_state, self.main_model, focus=focus
        )
        return "Conversation compacted."

    # ── Main entry point ─────────────────────────────────────────────────────

    def run(self, user_input: str) -> str:
        """Process one user message and return the final assistant reply."""
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

        state = LoopState(messages=self.history)
        agent_loop(
            state=state,
            model=self.main_model,
            tools=definitions.UNIFIED_TOOLS,
            registry=self.registry,
            system=self._system_prompt(),
            extra_handlers=self._extra_handlers,
        )

        # Track whether todo was updated this turn for the planner nudge
        used_todo = any(
            tc.get("function", {}).get("name") == "todo"
            for msg in self.history
            for tc in (msg.get("tool_calls") or [])
        )
        self.planner.note_round(used_todo=used_todo)

        # Return last non-empty assistant reply
        for msg in reversed(self.history):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return ""
