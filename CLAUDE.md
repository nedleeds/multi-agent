# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

참조 레포: `/Users/dhl/Src/learn-claude-code` (s01~s06 패턴 원본)

## 명령어

```bash
uv sync                        # 의존성 설치
python main.py                 # 코딩 에이전트 모드
python main.py --issue         # 현장 이슈 분석 모드 (Jira/Bitbucket/Confluence)
```

## 아키텍처

### 코딩 에이전트 모드 (`OrchestratorAgent`)

```
main.py
  └─► OrchestratorAgent (agent/orchestrator.py)
        ├─ main_model: OllamaModel  (loop.py에서 오케스트레이터로 호출)
        ├─ sub_model:  VLLMModel    (subagent.py에서 delegated task로 호출)
        ├─ ToolRegistry             tools/registry.py
        ├─ TodoManager              agent/planner.py
        ├─ SkillRegistry            agent/skill.py
        └─ CompactState             agent/compact.py
```

### 이슈 분석 모드 (`IssueInvestigatorAgent`)

```
main.py --issue
  └─► IssueInvestigatorAgent (agent/issue_investigator.py)
        ├─ main_model: OllamaModel  (조사 계획 + 최종 리포트 작성)
        ├─ sub_model:  VLLMModel    (각 서브에이전트 실행)
        │
        ├─► jira_task      → 서브에이전트 + JIRA_TOOLS
        │     jira_search, jira_get_issue
        ├─► bitbucket_task → 서브에이전트 + BITBUCKET_TOOLS
        │     bitbucket_list_commits, bitbucket_get_commit, bitbucket_list_prs
        └─► confluence_task → 서브에이전트 + CONFLUENCE_TOOLS
              confluence_search, confluence_get_page
```

### s01~s06 패턴 → 파일 매핑

| 패턴 | 파일 | 요점 |
|------|------|------|
| s01 loop | `agent/loop.py` | `run_one_turn` → `agent_loop` |
| s02 tools | `tools/` | definitions / handlers / registry |
| s03 todo | `agent/planner.py` | TodoManager |
| s04 subagent | `agent/subagent.py` | `run_subagent(tools=...)` — tools 파라미터로 API 전용 툴셋 주입 |
| s05 skill | `agent/skill.py` | SkillRegistry |
| s06 compact | `agent/compact.py` | `micro_compact` + `compact_history` |

### 메시지 포맷

OpenAI-compatible API 사용 (ollama/vllm 모두 동일):
- assistant: `{"role":"assistant","content":...,"tool_calls":[...]}`
- tool 결과: `{"role":"tool","tool_call_id":"...","content":"..."}`
- `utils/messages.py:normalize_messages` — 고아 tool_call 처리, 연속 user 메시지 병합

### 툴셋

| 상수 | 포함 툴 | 사용처 |
|------|---------|--------|
| `BASE_TOOLS` | bash, read_file, write_file, edit_file | - |
| `CHILD_TOOLS` | = BASE_TOOLS | 코딩 서브에이전트 |
| `ORCHESTRATOR_TOOLS` | BASE + todo, load_skill, task, compact | 코딩 오케스트레이터 |
| `JIRA_TOOLS` | jira_search, jira_get_issue | Jira 서브에이전트 |
| `BITBUCKET_TOOLS` | bitbucket_list_commits, bitbucket_get_commit, bitbucket_list_prs | Bitbucket 서브에이전트 |
| `CONFLUENCE_TOOLS` | confluence_search, confluence_get_page | Confluence 서브에이전트 |
| `ISSUE_INVESTIGATOR_TOOLS` | todo, jira_task, bitbucket_task, confluence_task, compact | 이슈 분석 오케스트레이터 |

### API 자격증명

모두 `.env`에서 설정 (`.env.example` 참조).
미설정 상태에서 API 툴을 호출하면 에러 대신 안내 메시지 반환.
`tools/api/config.py`의 각 Config 클래스에 `configured` 프로퍼티로 체크.
