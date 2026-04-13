# multi-agent

`learn-claude-code` 레포(s01~s06)의 에이전트 시스템 core loop를 **ollama + vllm** 백엔드로 직접 구현한 프로젝트입니다.

## 개요

| 역할 | 모델 | 서버 |
|------|------|------|
| 메인 오케스트레이터 | gpt-120b (설정 가능) | ollama (`http://localhost:11434`) |
| 서브에이전트 | gemma 4 (설정 가능) | vllm (`http://localhost:8000`) |

두 서버 모두 OpenAI 호환 API(`/v1/chat/completions`)를 통해 연결합니다.

## 실행 모드

### 코딩 에이전트 (기본)

```bash
python main.py
```

일반 코딩 작업을 수행하는 에이전트. bash, 파일 읽기/쓰기/수정, 스킬 로딩, 서브에이전트 위임을 지원합니다.

### 현장 이슈 분석 에이전트

```bash
python main.py --issue
```

현장에서 발생한 이슈 현상을 입력하면 에이전트가 **Jira / Bitbucket / Confluence** 를 병렬로 조사해 분석 리포트를 작성합니다.

```
입력: "결제 서비스 재시작 후 주문 처리 시 500 에러 발생"

오케스트레이터 (ollama/120b)
  ├── jira_task      → 서브에이전트 (vllm/gemma4) — 유사 이슈, 동일 이슈, 기존 해결책 검색
  ├── bitbucket_task → 서브에이전트 (vllm/gemma4) — 관련 커밋/PR, 영향 가능성 코드 변경 확인
  └── confluence_task → 서브에이전트 (vllm/gemma4) — 런북, 이전 인시던트 리포트, 관련 문서 검색

출력: 구조화된 분석 리포트
  ## 이슈 요약
  ## 유사 Jira 이슈
  ## 관련 코드 변경
  ## 관련 문서
  ## 종합 판단 및 권고
```

## 구현된 패턴 (s01~s06)

| 파일 | 패턴 | 내용 |
|------|------|------|
| `agent/loop.py` | s01 + s02 | 코어 에이전트 루프 — tool_call 결과를 피드백하며 반복 |
| `agent/planner.py` | s03 | TodoManager — 세션 플랜 작성 및 갱신 촉구 |
| `agent/subagent.py` | s04 | fresh context로 서브에이전트 생성, 요약만 반환 |
| `agent/skill.py` | s05 | SkillRegistry — 카탈로그는 system prompt에, 내용은 요청 시 로딩 |
| `agent/compact.py` | s06 | micro_compact + compact_history — 컨텍스트 압축 |

## 디렉토리 구조

```
multi-agent/
├── main.py                    # REPL 진입점 (--issue 플래그로 모드 전환)
├── model/
│   ├── base.py                # BaseLLM ABC
│   ├── config.py              # ModelConfig (환경변수 로딩)
│   ├── ollama.py              # OllamaModel — 메인 오케스트레이터
│   └── vllm.py                # VLLMModel — 서브에이전트
├── agent/
│   ├── state.py               # LoopState, PlanningState, CompactState
│   ├── loop.py                # 코어 루프 (s01+s02)
│   ├── planner.py             # TodoManager (s03)
│   ├── subagent.py            # 서브에이전트 실행기 (s04), tools 파라미터로 툴셋 주입
│   ├── skill.py               # SkillRegistry (s05)
│   ├── compact.py             # 컨텍스트 압축 (s06)
│   ├── orchestrator.py        # 코딩 에이전트 — 모든 컴포넌트 통합
│   └── issue_investigator.py  # 이슈 분석 에이전트 — Jira/Bitbucket/Confluence 조사
├── tools/
│   ├── definitions.py         # OpenAI 포맷 tool 스키마 전체
│   ├── handlers.py            # bash, read_file, write_file, edit_file 구현
│   ├── registry.py            # ToolRegistry — 이름→핸들러 디스패치
│   └── api/
│       ├── config.py          # JiraConfig, BitbucketConfig, ConfluenceConfig
│       ├── jira.py            # jira_search, jira_get_issue
│       ├── bitbucket.py       # bitbucket_list_commits, get_commit, list_prs
│       └── confluence.py      # confluence_search, confluence_get_page
├── utils/
│   ├── console.py             # Rich 기반 입출력
│   └── messages.py            # normalize_messages — 히스토리 정합성 보장
└── skills/                    # 스킬 파일 (SKILL.md)
    └── example/
        └── SKILL.md
```

## 시작하기

### 1. 환경 설정

```bash
cp .env.example .env
# .env에서 모델명, URL, API 자격증명 설정
```

### 2. 의존성 설치

```bash
uv sync
```

### 3. 서버 실행

```bash
# ollama (메인 오케스트레이터)
ollama serve
ollama pull gpt-120b       # 사용할 모델명으로 변경

# vllm (서브에이전트)
vllm serve google/gemma-3-27b-it --port 8000
```

### 4. 실행

```bash
python main.py             # 코딩 에이전트
python main.py --issue     # 이슈 분석 에이전트
```

## 환경변수

### LLM 서버

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | ollama API 주소 |
| `OLLAMA_MODEL` | `gpt-120b` | 메인 모델명 |
| `OLLAMA_MAX_TOKENS` | `8000` | 최대 토큰 수 |
| `VLLM_BASE_URL` | `http://localhost:8000/v1` | vllm API 주소 |
| `VLLM_MODEL` | `google/gemma-3-27b-it` | 서브에이전트 모델명 |
| `VLLM_API_KEY` | `token-abc123` | vllm API 키 |
| `VLLM_MAX_TOKENS` | `4000` | 서브에이전트 최대 토큰 수 |

### Jira

| 변수 | 설명 |
|------|------|
| `JIRA_BASE_URL` | Cloud: `https://domain.atlassian.net` / Server: `https://jira.company.com` |
| `JIRA_EMAIL` | Atlassian 계정 이메일 |
| `JIRA_API_TOKEN` | API 토큰 (id.atlassian.com에서 발급) |
| `JIRA_PROJECT_KEY` | 검색 범위 제한용 프로젝트 키 (선택, 예: `PROJ`) |

### Bitbucket

| 변수 | 설명 |
|------|------|
| `BITBUCKET_TYPE` | `server` (Data Center, 기본값) 또는 `cloud` |
| `BITBUCKET_BASE_URL` | Server: `https://bitbucket.company.com` / Cloud: `https://api.bitbucket.org` |
| `BITBUCKET_USERNAME` | Bitbucket 사용자명 |
| `BITBUCKET_APP_PASSWORD` | Server: personal access token / Cloud: app password |
| `BITBUCKET_PROJECT_KEY` | 프로젝트 키 (예: `MYPROJ`) |
| `BITBUCKET_REPO_SLUG` | 특정 레포 슬러그 (선택, 예: `my-service`) |

### Confluence

| 변수 | 설명 |
|------|------|
| `CONFLUENCE_BASE_URL` | Cloud: `https://domain.atlassian.net/wiki` / Server: `https://confluence.company.com` |
| `CONFLUENCE_EMAIL` | Atlassian 계정 이메일 |
| `CONFLUENCE_API_TOKEN` | API 토큰 |
| `CONFLUENCE_SPACE_KEY` | 검색 범위 제한용 스페이스 키 (선택, 예: `ENG`) |

## 스킬 추가

`skills/<name>/SKILL.md` 형식으로 파일을 만들면 자동으로 카탈로그에 등록됩니다.

```markdown
---
name: my-skill
description: 카탈로그에 표시될 한 줄 설명
---

스킬 본문 (모델이 load_skill("my-skill")을 호출할 때 context에 주입됨)
```

## 아키텍처 메모

- 두 모델 모두 **OpenAI-compatible API**를 사용하므로 `openai` 클라이언트 하나로 통일됩니다.
- 이슈 분석 모드의 서브에이전트는 각각 `JIRA_TOOLS` / `BITBUCKET_TOOLS` / `CONFLUENCE_TOOLS`만 받아 역할이 명확히 분리됩니다.
- API 자격증명이 미설정 상태에서 툴을 호출하면 에러 대신 "not configured" 안내 메시지가 반환됩니다.
- `normalize_messages`는 고아 `tool_call`에 placeholder를 삽입해 로컬 모델의 히스토리 검증 실패를 예방합니다.
- `compact_history`는 요약 전 `.transcripts/`에 전체 대화를 JSONL로 저장합니다.
