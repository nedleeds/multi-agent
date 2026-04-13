# multi-agent

`learn-claude-code` 레포(s01~s06)의 에이전트 시스템 core loop를 **ollama + vllm** 백엔드로 직접 구현한 프로젝트입니다.

## 개요

| 역할 | 모델 | 서버 |
|------|------|------|
| 메인 오케스트레이터 | gpt-120b (설정 가능) | ollama (`http://localhost:11434`) |
| 서브에이전트 | gemma 4 (설정 가능) | vllm (`http://localhost:8000`) |

두 서버 모두 OpenAI 호환 API(`/v1/chat/completions`)를 통해 연결합니다.

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
├── main.py              # REPL 진입점
├── model/
│   ├── base.py          # BaseLLM ABC
│   ├── config.py        # ModelConfig (환경변수 로딩)
│   ├── ollama.py        # OllamaModel — 메인 에이전트
│   └── vllm.py          # VLLMModel — 서브에이전트
├── agent/
│   ├── state.py         # LoopState, PlanningState, CompactState 데이터클래스
│   ├── loop.py          # 코어 루프 (s01+s02)
│   ├── planner.py       # TodoManager (s03)
│   ├── subagent.py      # 서브에이전트 실행기 (s04)
│   ├── skill.py         # SkillRegistry (s05)
│   ├── compact.py       # 컨텍스트 압축 (s06)
│   └── orchestrator.py  # 모든 컴포넌트 통합
├── tools/
│   ├── definitions.py   # OpenAI 포맷 tool 스키마
│   ├── handlers.py      # bash, read_file, write_file, edit_file
│   └── registry.py      # ToolRegistry — 이름→핸들러 디스패치
├── utils/
│   ├── console.py       # Rich 기반 입출력
│   └── messages.py      # normalize_messages — 히스토리 정합성 보장
└── skills/              # 스킬 파일 (SKILL.md)
    └── example/
        └── SKILL.md
```

## 시작하기

### 1. 환경 설정

```bash
cp .env.example .env
# .env에서 모델명, URL 등 설정
```

### 2. 의존성 설치

```bash
uv sync
```

### 3. 서버 실행

```bash
# ollama (메인)
ollama serve
ollama pull gpt-120b   # 또는 사용할 모델명

# vllm (서브에이전트)
vllm serve google/gemma-3-27b-it --port 8000
```

### 4. 실행

```bash
uv run main.py
# 또는 venv 활성화 후
source .venv/bin/activate
python main.py
```

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | ollama API 주소 |
| `OLLAMA_MODEL` | `gpt-120b` | 메인 모델명 |
| `OLLAMA_MAX_TOKENS` | `8000` | 최대 토큰 수 |
| `VLLM_BASE_URL` | `http://localhost:8000/v1` | vllm API 주소 |
| `VLLM_MODEL` | `google/gemma-3-27b-it` | 서브에이전트 모델명 |
| `VLLM_API_KEY` | `token-abc123` | vllm API 키 |
| `VLLM_MAX_TOKENS` | `4000` | 서브에이전트 최대 토큰 수 |

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

- 두 모델 모두 **OpenAI-compatible API**를 통해 연결하므로, `openai` 클라이언트 하나로 통일됩니다.
- 서브에이전트는 `CHILD_TOOLS`(bash/read/write/edit)만 받아 재귀적 서브에이전트 생성을 방지합니다.
- `normalize_messages`는 고아 `tool_call`에 placeholder를 삽입해 로컬 모델의 히스토리 검증 실패를 예방합니다.
- `compact_history`는 요약 전 `.transcripts/`에 전체 대화를 JSONL로 저장합니다.
