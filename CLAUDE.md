# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

참조 레포: `/Users/dhl/Src/learn-claude-code` (s01~s12 패턴 원본)

## 명령어

```bash
uv sync                        # 의존성 설치
python main.py                 # 코딩 에이전트 모드
python main.py --issue         # 현장 이슈 분석 모드 (Jira/Bitbucket/Confluence)
python main.py --team          # 팀 에이전트 모드 (s07-s12: 태스크 그래프, 백그라운드, 팀, 워크트리)

# 에이전트 시스템 평가 (intent routing + tool 선택 + 결과 품질)
uv run python eval.py              # 전체 시나리오 실행 (실제 LLM 호출 — OPENAI_API_KEY 필요)
uv run python eval.py --list       # 시나리오 목록만 확인
uv run python eval.py --only grep  # 이름 substring 필터
uv run python eval.py --verbose    # 각 시나리오의 응답 + tool 호출 trace 출력
```

### eval.py — 에이전트 평가 러너

`eval.py` 의 `SCENARIOS` 리스트에 큐레이션된 시나리오가 있음. 각 시나리오는 fresh `OrchestratorAgent`
를 만들고 `agent.run(prompt)` 한 번 호출한 뒤 세 축으로 채점:

- `must_call`      — 반드시 호출돼야 할 tool (예: `grep`, `todo`, `write_file`)
- `must_not_call`  — 호출되면 intent routing 위반 (예: 코드 탐색인데 `bash`)
- `grader(reply)`  — 최종 응답 또는 파일 결과 검증

현재 시나리오: `read_readme`, `grep_for_class`, `write_file_scratch`, `todo_multistep`,
`no_bash_for_search`, `korean_query_expansion`. 종료 코드 = 실패 개수.

새 regression 을 추가할 때는 `SCENARIOS` 에 `Scenario(name, prompt, must_call, must_not_call, grader)`
한 줄 추가.

**결과 저장 위치**: 매 실행마다 `.eval_runs/<timestamp>/` 에 저장 (`.gitignore`).
  - `summary.json` — 전체 pass/fail 집계
  - `<scenario>.reply.txt` — 최종 응답 본문
  - `<scenario>.history.jsonl` — 전체 turn (tool call/result 포함, 디버깅용)
  - `<scenario>.error.txt` — 예외 발생 시 traceback

실패 분석할 때는 `.eval_runs/<ts>/<scenario>.history.jsonl` 을 열어 tool 호출 시퀀스 확인.
`.eval_scratch/` 는 시나리오 실행 중 쓰는 임시 공간 (매 실행 끝에 자동 삭제).

### 스킬 (`skills/`)

`load_skill(name)` 으로 on-demand 주입되는 playbook 들. 카탈로그(이름+description)는
system prompt 에 항상 실려있어 모델이 intent 로 트리거.

| 스킬 | 언제 로드 |
|------|----------|
| `debug-issue`         | 에러·스택트레이스·"안 됨" 보고 — 수정 전에 항상 로드 |
| `refactor-safely`     | rename / 시그니처 변경 / 파일 이동 |
| `issue-investigation` | "현장 이슈", "장애", "유사 사례", incident — Jira/Bitbucket/Confluence 병렬 |
| `parallel-work`       | "병렬", "여러 방안 비교", worktree / teammate / background |
| `codebase-onboard`    | 처음 보는 코드베이스 / "구조 설명" / "어디서부터 봐야" |
| `code-review`         | 파이썬 코드 리뷰 |

스킬은 "수정부터 뛰어들지 말고 playbook 순서대로" 를 강제하는 장치. 새 스킬은
`skills/<name>/SKILL.md` 에 frontmatter(`name`, `description`) + 본문으로 추가.

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

### 이슈 조사 파이프라인 (issue-investigation 스킬)

5단계 흐름 (자세한 내용은 `skills/issue-investigation/SKILL.md` 와 README 의 "이슈 조사 파이프라인" 섹션):

```
0. 게이트           — 구체어 하나도 없으면 되묻기 (최대 3개). 있으면 통과.
1. 키워드 분해      — 4–8개 각도 (원문/단어별/한↔영/에러 용어/코드 스타일)
2. 병렬 멀티서치    — 한 턴에 jira_task + bitbucket_task + confluence_task 동시 발사
                     · jira_search_multi(queries)        → match_score 랭킹
                     · bitbucket_search_multi(queries)   → commits + PRs 취합
3. 취합·랭킹 해석   — match_score (N/M) 기반 강한/약한 단서 구분
4. Deep-dive 분기   — 신뢰도 기반 자동/수동:
                     자동 조건 3개 (match_score ≥ 3, 2위와 1.5배 차, 시간축 일치)
                     충족 시 jira_get_issue → PR diff → causal hypothesis
5. 종합 보고        — 7섹션 고정 포맷 (요약/Checklist/Jira/코드 변경/문서/유력 원인/종합 판단)
```

**핵심 결정**:
- 스코프 요구를 제거 — `.env` 의 JIRA_PROJECT_KEY 가 이미 프로젝트 스코프. 대신 **키워드 분해 + 멀티서치 + match_score 취합** 으로 누락 최소화.
- Deep-dive 는 항상 실행하지 않고 **신뢰도 조건** 으로 자동/수동 분기 — 매번 "진행할까요?" 묻는 bureaucracy 방지.
- 산출물은 후보 리스트가 아니라 **causal hypothesis** — "이 변경이 이 메커니즘으로 증상 유발" + 반증 경로 + 최소 수정안. debug-issue 스킬의 가설 원칙 준용.
- 게이트의 되묻기는 시스템 프롬프트의 "Never ask to narrow scope" 정책을 이 스킬에서만 override — 이슈 조사는 사용자 맥락이 구조적으로 필요.

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

### 팀 에이전트 모드 (`TeamOrchestratorAgent`)

```
main.py --team
  └─► TeamOrchestratorAgent (agent/team_orchestrator.py)
        ├─ main_model: OllamaModel
        ├─ sub_model:  VLLMModel  (팀메이트 루프에서 사용)
        │
        ├─ TaskManager      agent/task_manager.py   (s07: 파일 기반 태스크 그래프)
        ├─ BackgroundManager agent/background.py    (s08: 백그라운드 스레드 + 알림 큐)
        ├─ MessageBus       agent/team.py           (s09: JSONL 인박스)
        ├─ TeammateManager  agent/team.py           (s09-s11: 영속 팀메이트 + 프로토콜 + 자율)
        └─ WorktreeManager  agent/worktree.py       (s12: git worktree 격리)
```

### s01~s12 패턴 → 파일 매핑

| 패턴 | 파일 | 요점 |
|------|------|------|
| s01 loop | `agent/loop.py` | `run_one_turn` → `agent_loop` |
| s02 tools | `tools/` | definitions / handlers / registry |
| s03 todo | `agent/planner.py` | TodoManager (인메모리 체크리스트) |
| s04 subagent | `agent/subagent.py` | `run_subagent(tools=...)` — tools 파라미터로 API 전용 툴셋 주입 |
| s05 skill | `agent/skill.py` | SkillRegistry |
| s06 compact | `agent/compact.py` | `micro_compact` + `compact_history` |
| s07 task graph | `agent/task_manager.py` | TaskManager — `.tasks/` 파일 기반, `blockedBy` 의존성 |
| s08 background | `agent/background.py` | BackgroundManager — 데몬 스레드 + 알림 큐 |
| s09 teams | `agent/team.py` | TeammateManager + MessageBus (JSONL 인박스) |
| s10 protocols | `agent/team.py` | shutdown + plan approval (request_id 상관관계) |
| s11 autonomous | `agent/team.py` | 유휴 폴링 + 태스크 자동 클레임 + identity 재주입 |
| s12 worktree | `agent/worktree.py` | WorktreeManager + EventBus — 태스크 ID로 바인딩 |

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
| `JIRA_TOOLS` | jira_search, jira_search_multi, jira_get_issue | Jira 서브에이전트 |
| `BITBUCKET_TOOLS` | bitbucket_list_commits, bitbucket_get_commit, bitbucket_list_prs, bitbucket_search_multi, bitbucket_get_pr_diff, bitbucket_compare | Bitbucket 서브에이전트 |
| `CONFLUENCE_TOOLS` | confluence_search, confluence_get_page | Confluence 서브에이전트 |
| `ISSUE_INVESTIGATOR_TOOLS` | todo, jira_task, bitbucket_task, confluence_task, compact | 이슈 분석 오케스트레이터 |
| `TEAM_ORCHESTRATOR_TOOLS` | BASE + task_*, background_*, spawn_teammate, send/read/broadcast, request/respond_shutdown, submit/review_plan, worktree_*, compact | 팀 오케스트레이터 (s07-s12) |

### API 자격증명

모두 `.env`에서 설정 (`.env.example` 참조).
미설정 상태에서 API 툴을 호출하면 에러 대신 안내 메시지 반환.
`tools/api/config.py`의 각 Config 클래스에 `configured` 프로퍼티로 체크.
