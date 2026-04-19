# multi-agent

Claude Code 스타일 REPL 을 로컬/사내 LLM 서버로 붙여 쓰는 통합 에이전트.

단일 진입점 `python main.py` 만 있고, 사용자 요청의 **intent** 에 따라 내부에서 자동으로:

- **기본 코딩 / 코드 탐색 / 수정** — bash, read/write/edit_file, grep/glob/ls/fuzzy_find
- **현장 이슈 조사** — Jira / Bitbucket / Confluence 서브에이전트 병렬 위임
- **병렬 · 격리 · 팀 작업** — task graph · git worktree · persistent teammates · background run

---

## 모델 배선

| 역할 | 클래스 | 모델 | 엔드포인트 |
|------|--------|------|-----------|
| **main** (오케스트레이터) | `OpenAIModel` | **gpt-oss-120b** | OpenAI API (Mac dev) · 사내 local gpt-oss 서버 (deploy) |
| **sub** (서브에이전트) | `VLLMModel` | gemma · qwen · 기타 | 로컬 vLLM / ollama (Mac) · 사내 vLLM 서버 (deploy) |

두 모델 모두 **OpenAI-compatible API** (`/v1/chat/completions`) 로 연결.
코드 수정 없이 `.env` 의 `*_BASE_URL` · `*_MODEL` · `*_API_KEY` 만 교체하면 환경 전환 끝.

---

## 빠른 시작 (Mac dev)

```bash
# 1. 의존성
uv sync

# 2. 탐색 툴 바이너리
brew install ripgrep fzf

# 3. .env 생성
cp .env.example .env
# OPENAI_API_KEY 채우기

# 4. (선택) subagent 용 로컬 서버 띄우기 — 둘 중 하나
#   A. vLLM 로 gemma 3:
vllm serve google/gemma-3-27b-it --port 8000
#   B. ollama 로 qwen2.5 (가볍게):
ollama serve
ollama pull qwen2.5:7b
# .env 에서 VLLM_BASE_URL=http://localhost:11434/v1 · VLLM_MODEL=qwen2.5:7b 로 맞추면 됨

# 5. 실행
python main.py
```

`.env` 최소 설정 (Mac, OpenAI 쓸 때):

```dotenv
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-oss-120b

VLLM_BASE_URL=http://localhost:8000/v1
VLLM_MODEL=google/gemma-3-27b-it
```

---

## 사내 환경으로 전환

**코드는 그대로**. `.env` 에서 두 블록만 교체.

### main (gpt-oss-120b → 사내 로컬 서버)

```dotenv
OPENAI_BASE_URL=http://gpt-oss.internal.company.com/v1
OPENAI_MODEL=gpt-oss-120b
OPENAI_API_KEY=dummy                # 사내 서버는 대개 임의 문자열 허용
OPENAI_MAX_TOKENS=16000
```

`OpenAIModel` 은 OpenAI SDK 에 `base_url` 을 넘겨 붙이므로 OpenAI-compatible 이면 어디든 동작.

### sub (gemma-4)

```dotenv
VLLM_BASE_URL=http://vllm.internal.company.com/v1
VLLM_MODEL=google/gemma-4-27b-it
VLLM_API_KEY=token-abc123
VLLM_MAX_TOKENS=12000
```

그 외 Jira / Bitbucket / Confluence 자격증명은 `.env.example` 참고 — 미설정이면 관련 툴 호출 시 `"not configured"` 안내만 리턴하고 크래시 안 함.

---

## 사용 예

```bash
python main.py
```

REPL 이 뜨면 자연어로 입력. 모델이 intent 로 툴을 선택해서 실행:

| 유저 입력 | 내부 동작 |
|----------|----------|
| "status bar 폴더 아이콘 색 바꿔줘" | `grep` → `read_file` → `edit_file` → `bash` 검증 |
| "이 버그 현상인데 유사 사례 있어?" | `jira_task` + `bitbucket_task` + `confluence_task` 병렬 |
| "리팩터링 두 방안 각각 격리된 worktree 에서 돌려봐" | `task_create` + `worktree_create` × 2 + `spawn_teammate` |
| "npm install 돌려놓고 계속 일해" | `background_run` + 이후 턴에서 자동 notification |

슬래시 커맨드:
- `/help` — 명령어 목록
- `/clear` — 대화 기록 + todo 초기화 (화면 clear)
- `/exit` — 종료

---

## 주요 환경변수

### LLM

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | main 엔드포인트 (사내에선 로컬 gpt-oss URL) |
| `OPENAI_MODEL` | `gpt-oss-120b` | main 모델 ID |
| `OPENAI_API_KEY` | `dummy` | 원격 OpenAI 면 실제 키, 사내면 임의 문자열 가능 |
| `OPENAI_MAX_TOKENS` | `16000` | main 응답 최대 토큰 |
| `VLLM_BASE_URL` | `http://localhost:8000/v1` | sub 엔드포인트 |
| `VLLM_MODEL` | `google/gemma-3-27b-it` | sub 모델 ID |
| `VLLM_API_KEY` | `token-abc123` | sub API 키 |
| `VLLM_MAX_TOKENS` | `12000` | sub 응답 최대 토큰 (탐색 여유용) |
| `VLLM_DISABLE_THINKING` | `true` | Qwen3 등의 `<think>` 토큰 억제 |
| `AGENT_DEBUG` | `(unset)` | `1` 설정 시 매 LLM 호출의 turn/model/msgs/tools/finish_reason 덤프 |

### Jira / Bitbucket / Confluence

`.env.example` 참고. 모두 선택이며, 자격증명 미설정 시 관련 툴이 안내 메시지만 반환.

---

## 아키텍처

```
                  ┌─────────────────────────────────────────────┐
                  │  OrchestratorAgent  (agent/orchestrator.py) │
                  │                                             │
 사용자 ──► REPL ──►  main_model (gpt-oss-120b)                 │
            (utils/repl.py)   │                                 │
                  │           ├─ BASE + SEARCH tools            │
                  │           ├─ todo / compact / load_skill    │
                  │           │                                 │
                  │           ├─ task  ────► 일반 subagent      │
                  │           │              (sub_model=vLLM)   │
                  │           │                                 │
                  │           ├─ jira_task  ────► jira subagent │
                  │           ├─ bitbucket_task ── bb subagent  │
                  │           ├─ confluence_task ── cf subagent │
                  │           │                                 │
                  │           ├─ task_graph (s07 .tasks/)       │
                  │           ├─ background_run (s08 thread)    │
                  │           ├─ spawn_teammate (s09 inbox)     │
                  │           └─ worktree_* (s12 git)           │
                  └─────────────────────────────────────────────┘
```

단일 클래스, 단일 실행 진입점. 툴셋과 핸들러가 한 곳에 모여 있어 추가·교체가 쉬움.

### 디렉토리

```
multi-agent/
├── main.py                    # 단일 진입점
├── .env / .env.example
├── model/
│   ├── base.py                # BaseLLM ABC
│   ├── config.py              # 환경변수 → ModelConfig
│   ├── openai_model.py        # main (OPENAI_BASE_URL 존중 — 사내/외 모두)
│   ├── vllm.py                # sub (VLLM_BASE_URL)
│   └── ollama.py              # (대안 백엔드, 현재 main.py 에서는 미사용)
├── agent/
│   ├── orchestrator.py        # 통합 오케스트레이터 (38 tools, intent 라우팅)
│   ├── loop.py                # s01+s02 core loop — finish_reason="length" 처리
│   ├── subagent.py            # s04 — start/end 배너 · 에러 safety · evidence trace
│   ├── planner.py             # s03 TodoManager (live 영역에 체크리스트 렌더)
│   ├── skill.py               # s05 SkillRegistry
│   ├── compact.py             # s06 micro_compact / compact_history
│   ├── task_manager.py        # s07 파일 기반 태스크 그래프
│   ├── background.py          # s08 백그라운드 스레드 + 알림 큐
│   ├── team.py                # s09-s11 팀메이트 + 메시지버스 + 프로토콜
│   ├── worktree.py            # s12 WorktreeManager + EventBus
│   └── state.py               # LoopState / PlanningState / CompactState
├── tools/
│   ├── handlers.py            # bash · read/write/edit_file · grep · glob · ls · fuzzy_find
│   ├── definitions.py         # 38 tool 스키마, UNIFIED_TOOLS 집합
│   ├── registry.py            # 이름→핸들러 dispatch
│   └── api/
│       ├── jira.py · bitbucket.py · confluence.py
│       └── config.py          # 자격증명 dataclass + "not configured" fallback
├── utils/
│   ├── repl.py                # prompt_toolkit Application — 입력창 하단 고정, live 영역 + 상태줄
│   ├── console.py             # rich console + _DisplayManager (shimmer / pulse / todo)
│   └── messages.py            # normalize_messages — 고아 tool_call 보호, 2차 truncation cap
└── skills/                    # SKILL.md 파일들
```

---

## 탐색 툴셋 (grep / glob / ls / fuzzy_find)

모두 `.gitignore` 존중 · 바이너리 자동 제외 · `.venv`/`__pycache__`/`node_modules`/`.git` 등 노이즈 디렉토리 prune. `bash grep` / `bash find` 쓰지 말고 이걸 쓰도록 system prompt 에 명시.

| 툴 | 백엔드 | 용도 |
|---|--------|------|
| `grep(pattern, glob?, type?, output_mode?, context?, head_limit?)` | ripgrep | 내용 검색 |
| `glob(pattern, path?)` | `rg --files` | 파일 경로 매칭 |
| `ls(path?, depth?, dirs_only?, hidden?)` | find + prune | 트리 출력 |
| `fuzzy_find(query, path?, limit?)` | `rg --files \| fzf -f` | 퍼지 파일명 검색 |

필요 바이너리: `ripgrep` · `fzf` (`brew install ripgrep fzf`).

---

## UX 디테일

- **입력창 하단 고정** — prompt_toolkit `Application` 이 상태줄 + 입력 박스를 화면 하단에 유지. 터미널 scrollback 으로 이전 결과가 위로 쌓임 (Claude Code 스타일).
- **Todo live 체크리스트** — 모델이 `todo` 툴을 호출하면 상태줄 위 live 영역에 체크리스트가 그려지고, 완료 항목은 취소선, 진행 중 항목은 shimmer 효과.
- **Subagent bullet pulse** — 서브에이전트 실행 중 amber 펄스 bullet + 현재 tool 호출 (`↳ jira_search ...`) 실시간 표시.
- **Thinking shimmer** — 빛이 훑고 지나가는 애니메이션 (`utils/console.py:_shimmer_color`).
- **토큰 누적 표시** — 상태줄 우측 `↑in ↓out` (누적). 비용 감 잡는 용도.
- **Nested indent** — subagent 내부 tool call 은 `      ⎿` 6 스페이스 들여쓰기, main orchestrator 는 `  ⎿` 2 스페이스 — 시각적 구분.
- **Truncation 마커** — 200KB 초과 출력은 `[OUTPUT TRUNCATED — N bytes omitted. Re-run with pagination…]` 로 모델이 인지하고 재시도 유도.

---

## 스킬

모델이 intent 에 맞게 `load_skill(name)` 를 호출하면 해당 스킬의 step-by-step playbook 이 context 에 주입됨. 카탈로그(이름 + description)는 system prompt 에 항상 실려있어 트리거 기반 discovery 가능.

### 포함된 스킬 (`skills/`)

| 스킬 | 트리거 | 내용 |
|------|--------|------|
| `debug-issue`          | 에러·스택트레이스·"X 가 안 됨"               | 증상 → 가설 → 검증 → 최소 수정 → 재검증 7단계 |
| `refactor-safely`      | 이름 변경·시그니처 변경·코드 이동             | 영향 범위 grep → 계약 이해 → 일괄 수정 → 전수 재검증 |
| `issue-investigation`  | "현장 이슈", "장애", "유사 사례", incident    | 5단계 파이프라인 — 게이트 → 키워드 분해 → 병렬 멀티서치 + `match_score` 랭킹 → Deep-dive (자동/수동) → causal hypothesis. 위 "이슈 조사 파이프라인" 섹션 참고 |
| `parallel-work`        | "병렬로", "여러 방안 비교", worktree         | task graph / worktree / teammate / background 선택 기준 + 패턴 |
| `codebase-onboard`     | "이 프로젝트 뭐해?", "구조 설명", "어디서부터" | README → pyproject → 엔트리포인트 → 레이어별 대표 파일 |
| `code-review`          | 파이썬 코드 리뷰 요청                         | correctness/readability/safety/perf/tests 체크리스트 |

### 새 스킬 추가

```
skills/my-skill/SKILL.md
```

```markdown
---
name: my-skill
description: 카탈로그에 표시될 한 줄 설명 (트리거 키워드 포함 권장)
---

스킬 본문 — 모델이 `load_skill("my-skill")` 호출할 때만 context 에 주입됨.
```

`description` 은 모델이 "로드할지 말지" 를 판단하는 유일한 힌트 — 트리거 키워드를 구체적으로.

---

## 구현 패턴 맵

| 패턴 | 파일 | 요점 |
|------|------|------|
| s01 loop | `agent/loop.py` | `run_one_turn` → `agent_loop`, `finish_reason="length"` 시 자동 continue |
| s02 tools | `tools/` | definitions · handlers · registry |
| s03 todo | `agent/planner.py` | TodoManager (live 체크리스트 렌더) |
| s04 subagent | `agent/subagent.py` | fresh context · tools 파라미터 · start/end 배너 · evidence trace |
| s05 skill | `agent/skill.py` | 카탈로그 주입 + lazy 로드 |
| s06 compact | `agent/compact.py` | micro_compact + compact_history |
| s07 task graph | `agent/task_manager.py` | 파일 기반 `.tasks/`, blockedBy 의존성 |
| s08 background | `agent/background.py` | 데몬 스레드 + 알림 큐 |
| s09 teams | `agent/team.py` | TeammateManager + MessageBus (JSONL inbox) |
| s10 protocols | `agent/team.py` | shutdown + plan approval (request_id) |
| s11 autonomous | `agent/team.py` | 유휴 폴링 + 태스크 클레임 + identity 재주입 |
| s12 worktree | `agent/worktree.py` | WorktreeManager + EventBus |

---

## 이슈 조사 파이프라인 (issue-investigation 스킬)

"최근 playback 시간 초과 에러가 왜 나지?" 같은 현장 이슈 질문을 받으면 5단계 파이프라인으로 처리. `skills/issue-investigation/SKILL.md` 참고.

```
쿼리
 ↓ [0. 게이트 — 구체어 ≥1 확인. 아니면 되묻기 (최대 3개)]
 ↓ [1. 키워드 분해 — 4–8개 각도 생성]
 ↓     · 원문 구 / 단어별 / 한↔영 / 에러 용어 / 코드 스타일 식별자
[2. 병렬 멀티서치 — 한 턴에 3 delegation 동시]
  - jira_task        → jira_search_multi(queries)
  - bitbucket_task   → bitbucket_search_multi(queries)
  - confluence_task  → confluence_search(…)
 ↓ [3. 취합 + 랭킹 — match_score desc → updated desc → priority asc]
 ↓ [4. Deep-dive 분기 (신뢰도 기반)]
 ↓     자동 조건 3개 모두 충족 시 묻지 않고 진행:
 ↓       ☐ 상위 match_score ≥ 3
 ↓       ☐ 2위와 1.5배 차
 ↓       ☐ 시간축 일치
 ↓     아니면 상위 2-3건 제시하고 사용자 선택 대기
 ↓
[Deep-dive 단계]
  - jira_get_issue(top.key)              전문 (desc, 댓글, issuelinks, fix_versions, resolution)
  - 연관 PR/commit 식별 (desc·댓글 추출 또는 issuelinks 또는 시간축 교차)
  - bitbucket_get_pr_diff / get_commit   실제 unified diff
  - diff hunk 분석                        timeout·config·경합·경계 조건 등
  - causal hypothesis                     구체 + 메커니즘 + 반증 + 최소 수정안
 ↓
[5. 종합 보고 — 고정 7 섹션]
  ## 이슈 요약 / ## Checklist / ## Jira / ## 코드 변경 / ## 문서 / ## 유력 원인 / ## 종합 판단
```

### 핵심 설계 포인트

**키워드 분해 + 멀티서치**. 사용자 원문 쿼리("playback 시간 초과") 하나로만 검색하면 정확한 tokenization 일치에만 hit → 누락 많음. 4–8 각도로 쪼개서 병렬로 쏘고 **`matched_queries` / `match_score`** 로 얼마나 여러 각도에서 잡혔는지 표시 → **강한 단서 vs 약한 단서** 자동 구분.

**신뢰도 기반 자동/수동 분기**. 확실할 때는 자동으로 deep-dive, 애매할 때만 사용자에게 선택권 — "진행할까요?" 를 매번 묻는 bureaucracy 방지.

**Causal hypothesis 를 마지막 산출물로 강제**. 후보 리스트에서 끝내지 않고, 실제 diff 분석 → "이 변경이 이 메커니즘으로 증상 유발" 까지 간다. debug-issue 스킬의 가설 검증 원칙을 준용.

**게이트 override**. 시스템 프롬프트의 "Never ask to narrow scope" 는 코드 탐색 정책. 이슈 조사는 사용자 맥락이 꼭 필요한 경우가 있어 이 스킬에서만 예외 허용 (최대 3개 질문, 옵션은 명시).

### 쓰인 툴

| 툴 | 역할 |
|----|------|
| `jira_search_multi(queries[])`                 | 여러 키워드 병렬 검색 → dedupe + match_score 랭킹 |
| `jira_get_issue(key)`                          | 전문 조회 — description, comments, issuelinks, fix_versions, resolution, attachments, subtasks |
| `bitbucket_search_multi(queries[])`            | 최근 commits + PRs 한번 fetch 후 클라이언트 필터 + 취합 |
| `bitbucket_get_commit(id)` / `get_pr_diff(id)` | 실제 unified diff 원문 |
| `bitbucket_compare(from, to)`                  | 릴리스 범위 diff |
| `confluence_search` / `confluence_get_page`    | runbook / postmortem / 아키텍처 문서 |

### 예시 입력 → 출력

**입력**: `"최근 플러그인 실행할 때 playback 시간 초과 에러 왜 나?"`

**게이트**: "playback", "시간 초과", "플러그인" 구체어 있음 → 통과 (되묻기 없음).

**키워드 분해** (모델): `["playback 시간 초과", "playback", "시간 초과", "timeout", "playback timeout", "플러그인 playback"]`

**병렬 발사**: `jira_search_multi` / `bitbucket_search_multi` / `confluence_search` 동시.

**취합 결과 (Jira)**: MEDIA-412 (3/6), MEDIA-418 (2/6), AUDIO-77 (1/6) — 상위 자동 선택 조건 충족 → deep-dive 자동.

**Deep-dive**: `jira_get_issue('MEDIA-412')` → issuelinks 에 MEDIA-201 (2025-11, 같은 증상, 60s 로 복원해 해결). description 에 PR#1023 언급. `bitbucket_get_pr_diff('1023')` → `src/player/playback.ts:142` 에서 `TIMEOUT_MS 60_000 → 30_000` 축소.

**유력 원인**: PR#1023 의 buffer timeout 축소. 메커니즘 = 느린 네트워크에서 30초 내 미충전 → playback 중단. 반증 = revert 후 재현 여부. 최소 수정 = `TIMEOUT_MS = 60_000` 한 줄 hotfix.

---

## 평가 (eval.py)

`eval.py` 는 `OrchestratorAgent` 가 **intent 에 맞는 툴을 고르는지** + **결과 품질** 을 큐레이션된 시나리오로 검증하는 얇은 러너.

```bash
uv run python eval.py              # 전체 (실제 LLM 호출 — OPENAI_API_KEY 필요)
uv run python eval.py --list       # 시나리오 목록만 출력
uv run python eval.py --only grep  # 이름 substring 필터
uv run python eval.py --verbose    # 응답 본문 + tool 호출 trace
```

각 시나리오는 fresh `OrchestratorAgent` 를 만들고 `agent.run(prompt)` 를 한 번 돌린 뒤 세 축으로 채점:

| 축 | 의미 | 예 |
|----|------|---|
| `must_call`     | 반드시 호출돼야 하는 tool | `grep`, `read_file`, `todo`, `write_file` |
| `must_not_call` | 호출되면 intent routing 위반 | 코드 탐색인데 `bash`, 단순 질문인데 `task` / `worktree_create` |
| `grader(reply)` | 최종 응답 또는 파일 결과 검증 | reply 에 `openai_model.py` 포함, `.eval_scratch/hello.txt` 에 기대 문자열 있음 |

현재 시나리오 (`SCENARIOS` in `eval.py`):

| 이름 | 초점 |
|------|------|
| `read_readme`            | 기본 `read_file` — 서브에이전트/팀 툴로 튀지 않는지 |
| `grep_for_class`         | 심볼 검색은 `grep` — `bash grep` 금지 |
| `write_file_scratch`     | `write_file` 로 파일 생성 (parent dir 자동 생성) |
| `todo_multistep`         | 멀티스텝 작업에 `todo` 사용 + 각 파일 `read_file` |
| `no_bash_for_search`     | "몇 번 나와?" 질문도 `grep` (+`output_mode='count'`) |
| `korean_query_expansion` | 한국어 개념("상태표시줄") → 영문 식별자 확장 후 `grep` |

종료 코드 = 실패한 시나리오 수. CI 에 물려도 되고, 모델/프롬프트 바꿨을 때 regression 감지용으로도 사용.

시나리오 추가: `SCENARIOS` 리스트에 `Scenario(name, prompt, must_call, must_not_call, grader)` 추가만 하면 됨.

### 결과 저장 위치

매 실행마다 `.eval_runs/<YYYY-MM-DDTHH-MM-SS>/` 디렉토리가 생기고 아래 파일들이 저장됨:

| 파일 | 내용 |
|------|------|
| `summary.json`                 | 전체 pass/fail + 시나리오별 `{ok, reason, elapsed, calls}` |
| `<scenario>.reply.txt`         | 해당 시나리오의 최종 assistant 응답 (사람이 읽기 좋게 텍스트) |
| `<scenario>.history.jsonl`     | 전체 turn (user / assistant / tool_calls / tool results) — 디버깅 용 |
| `<scenario>.error.txt`         | 예외 발생 시 traceback (정상 실행 시 없음) |

실행이 끝나면 콘솔 마지막에 `→ results saved to .eval_runs/…/` 로 경로를 찍어줌. 실패 원인 파보려면 `cat .eval_runs/<ts>/<scenario>.history.jsonl | jq .` 로 tool 호출 시퀀스 확인.

주의
- 매 시나리오마다 fresh agent → 실제 LLM 호출 비용/시간 발생 (전체 ~1–3분, 소수의 달러).
- `write_file_scratch` 는 `.eval_scratch/` 디렉토리를 만들었다가 끝나면 삭제.
- `.eval_scratch/` 와 `.eval_runs/` 는 모두 `.gitignore` 에 등록됨.

---

## 참고

- 탐색/이슈조사/팀 모드가 모두 `UNIFIED_TOOLS` 에 묶여 있어 모델이 intent 만 맞으면 자동 선택.
- system prompt 에 **completeness check / query expansion / 조기포기 금지** 지시가 명시돼 있어, tool 출력 잘렸거나 `(no matches)` 나와도 모델이 재시도하도록 유도.
- `compact_history` 는 요약 전 `.transcripts/<timestamp>.jsonl` 로 전체 대화 저장.
- `normalize_messages` 는 고아 `tool_call` 에 placeholder 를 삽입해 로컬 모델의 history 검증 실패를 예방.
