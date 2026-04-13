# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

참조 레포: `/Users/dhl/Src/learn-claude-code` (s01~s06 패턴 원본)

## 명령어

```bash
# 의존성 설치
uv sync

# 실행
uv run main.py
python main.py          # venv 활성화 상태

# 단일 모듈 문법 검사
python -c "import agent; import tools; import utils; import model"
```

## 아키텍처

```
main.py
  └─► OrchestratorAgent (agent/orchestrator.py)
        ├─ main_model: OllamaModel  → loop.py에서 호출 (orchestrator 역할)
        ├─ sub_model:  VLLMModel    → subagent.py에서 호출 (delegated subtask)
        ├─ ToolRegistry             → tools/registry.py
        ├─ TodoManager              → agent/planner.py
        ├─ SkillRegistry            → agent/skill.py
        └─ CompactState             → agent/compact.py
```

### s01~s06 패턴 → 파일 매핑

| 패턴 | 파일 | 요점 |
|------|------|------|
| s01 loop | `agent/loop.py` | `run_one_turn` → `agent_loop` |
| s02 tools | `tools/` | definitions / handlers / registry |
| s03 todo | `agent/planner.py` | TodoManager |
| s04 subagent | `agent/subagent.py` | `run_subagent(fresh messages=[])` |
| s05 skill | `agent/skill.py` | SkillRegistry |
| s06 compact | `agent/compact.py` | `micro_compact` + `compact_history` |

### 메시지 포맷

Anthropic API가 아닌 **OpenAI-compatible API** 사용.
- assistant 메시지: `{"role":"assistant","content":...,"tool_calls":[...]}`
- tool 결과: `{"role":"tool","tool_call_id":"...","content":"..."}`
- `utils/messages.py:normalize_messages` — 고아 tool_call 처리, 연속 user 메시지 병합

### 툴셋

- `BASE_TOOLS` / `CHILD_TOOLS`: bash, read_file, write_file, edit_file
- `ORCHESTRATOR_TOOLS`: BASE + todo, load_skill, task, compact

### 환경변수

`.env.example` 참조. `OLLAMA_MODEL`, `VLLM_MODEL`이 핵심.
