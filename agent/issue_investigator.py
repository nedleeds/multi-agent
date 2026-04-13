"""IssueInvestigatorAgent: 현장 이슈를 입력받아 Jira/Bitbucket/Confluence를 병렬 조사합니다.

흐름:
  사용자: "현장 이슈: XXX 에러 발생"
    └─► OrchestratorAgent (ollama/120b) — 조사 계획 수립
          ├─► jira_task → 서브에이전트 (vllm/gemma4)
          │     jira_search / jira_get_issue 사용
          │     → 유사 이슈, 동일 이슈, 기존 해결 방법
          ├─► bitbucket_task → 서브에이전트 (vllm/gemma4)
          │     bitbucket_list_commits / bitbucket_get_commit / bitbucket_list_prs 사용
          │     → 관련 코드 변경, 영향 가능성 있는 커밋/PR
          └─► confluence_task → 서브에이전트 (vllm/gemma4)
                confluence_search / confluence_get_page 사용
                → 관련 문서, 런북, 이전 인시던트 리포트
          → 모든 결과 종합 → 구조화된 분석 리포트
"""

from model.base import BaseLLM
from tools import definitions
from tools.api import BitbucketClient, ConfluenceClient, JiraClient
from tools.registry import ToolRegistry
from utils.console import print_info, print_plan

from .compact import CONTEXT_LIMIT, compact_history, estimate_size, micro_compact
from .loop import agent_loop
from .planner import TodoManager
from .state import CompactState, LoopState
from .subagent import run_subagent

_SYSTEM_PROMPT = """\
You are an expert incident analysis agent. Your job is to investigate field issues by searching \
Jira, Bitbucket, and Confluence, then synthesize a clear analysis report.

When a field issue is described:

1. Use the todo tool to plan the investigation (3 parallel tasks).
2. Call ALL THREE of these task tools — each runs an independent subagent:
   - jira_task      : Find similar/identical Jira issues and known solutions
   - bitbucket_task : Find code changes (commits/PRs) that may have caused the issue
   - confluence_task: Find relevant documentation, runbooks, or past incident reports
3. After all three return, synthesize findings into a structured Korean/English report:

## 이슈 요약
## 유사 Jira 이슈 (Similar Jira Issues)
## 관련 코드 변경 (Related Code Changes)
## 관련 문서 (Related Documentation)
## 종합 판단 및 권고 (Assessment & Recommendations)

Always call all three task tools before writing the final report.
"""

_JIRA_SUBAGENT_SYSTEM = """\
You are a Jira investigation subagent. Given an issue description:
1. Use jira_search to find similar or identical issues (try multiple relevant keywords).
2. For the most relevant results (top 3), use jira_get_issue to get full details.
3. Return a concise summary: issue keys, titles, statuses, and any known resolutions.
"""

_BITBUCKET_SUBAGENT_SYSTEM = """\
You are a Bitbucket code analysis subagent. Given an issue description:
1. Use bitbucket_list_commits to find commits related to the issue (try error keywords, component names).
2. Use bitbucket_get_commit on suspicious commits to review the diff.
3. Use bitbucket_list_prs to find related pull requests.
4. Return a concise summary: commit IDs, messages, authors, dates, and what changed.
"""

_CONFLUENCE_SUBAGENT_SYSTEM = """\
You are a Confluence documentation subagent. Given an issue description:
1. Use confluence_search to find relevant pages (try error keywords, component names, "incident", "runbook").
2. Use confluence_get_page on the most relevant results to read their content.
3. Return a concise summary: page titles, URLs, and key information found.
"""


class IssueInvestigatorAgent:
    def __init__(
        self,
        main_model: BaseLLM,
        sub_model: BaseLLM,
    ):
        self.main_model = main_model
        self.sub_model = sub_model
        self.registry = self._build_registry()
        self.planner = TodoManager()
        self.compact_state = CompactState()
        self.history: list[dict] = []

        self._extra_handlers = {
            "todo":            self._handle_todo,
            "jira_task":       self._handle_jira_task,
            "bitbucket_task":  self._handle_bitbucket_task,
            "confluence_task": self._handle_confluence_task,
            "compact":         self._handle_compact,
        }

    # ── Registry ───────────────────────────────────────────────────────────

    def _build_registry(self) -> ToolRegistry:
        """Register all API tool handlers."""
        registry = ToolRegistry()
        jira = JiraClient()
        bb = BitbucketClient()
        cf = ConfluenceClient()

        registry.register("jira_search",           lambda query, max_results=10: jira.search(query, max_results))
        registry.register("jira_get_issue",         lambda issue_key: jira.get_issue(issue_key))
        registry.register("bitbucket_list_commits", lambda keyword="", limit=20: bb.list_commits(keyword, limit))
        registry.register("bitbucket_get_commit",   lambda commit_id: bb.get_commit(commit_id))
        registry.register("bitbucket_list_prs",     lambda query="", state="ALL": bb.list_prs(query, state))
        registry.register("confluence_search",      lambda query, max_results=10: cf.search(query, max_results))
        registry.register("confluence_get_page",    lambda page_id: cf.get_page(page_id))

        return registry

    # ── Extra handlers ─────────────────────────────────────────────────────

    def _handle_todo(self, items: list) -> str:
        result = self.planner.update(items)
        print_plan(self.planner.render())
        return result

    def _handle_jira_task(self, prompt: str) -> str:
        print_info("[jira_task] subagent 시작")
        return run_subagent(
            prompt=prompt,
            model=self.sub_model,
            registry=self.registry,
            system=_JIRA_SUBAGENT_SYSTEM,
            description="jira",
            tools=definitions.JIRA_TOOLS,
        )

    def _handle_bitbucket_task(self, prompt: str) -> str:
        print_info("[bitbucket_task] subagent 시작")
        return run_subagent(
            prompt=prompt,
            model=self.sub_model,
            registry=self.registry,
            system=_BITBUCKET_SUBAGENT_SYSTEM,
            description="bitbucket",
            tools=definitions.BITBUCKET_TOOLS,
        )

    def _handle_confluence_task(self, prompt: str) -> str:
        print_info("[confluence_task] subagent 시작")
        return run_subagent(
            prompt=prompt,
            model=self.sub_model,
            registry=self.registry,
            system=_CONFLUENCE_SUBAGENT_SYSTEM,
            description="confluence",
            tools=definitions.CONFLUENCE_TOOLS,
        )

    def _handle_compact(self, focus: str | None = None) -> str:
        self.history[:] = compact_history(
            self.history, self.compact_state, self.main_model, focus=focus
        )
        return "Conversation compacted."

    # ── Main entry point ───────────────────────────────────────────────────

    def run(self, issue_description: str) -> str:
        """이슈 현상을 입력받아 조사 결과 리포트를 반환합니다."""
        self.history.append({"role": "user", "content": issue_description})

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
            tools=definitions.ISSUE_INVESTIGATOR_TOOLS,
            registry=self.registry,
            system=_SYSTEM_PROMPT,
            extra_handlers=self._extra_handlers,
        )

        used_todo = any(
            tc.get("function", {}).get("name") == "todo"
            for msg in self.history
            for tc in (msg.get("tool_calls") or [])
        )
        self.planner.note_round(used_todo=used_todo)

        for msg in reversed(self.history):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return ""
