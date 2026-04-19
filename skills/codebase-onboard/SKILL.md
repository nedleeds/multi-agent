---
name: codebase-onboard
description: 처음 보는 코드베이스 — 구조·엔트리포인트·핵심 모듈을 빠르게 파악 후 답변
---

트리거: "이 프로젝트 뭐해?", "구조 설명해줘", "어디서부터 봐야 돼?", "이 레포 구조 알려줘", "아키텍처".

## 1. 프로젝트 정체성 (60초)

- `read_file('README.md', limit=80)` — 상단만.
- `read_file('pyproject.toml')` 또는 `package.json` / `Cargo.toml` / `go.mod` — 의존성 + 엔트리.
- `ls(path='.', depth=2)` — 최상위 구조.

## 2. 엔트리포인트

- `pyproject.toml` 의 `[project.scripts]` 나 `__main__` 블록 찾기.
- `grep 'if __name__.*__main__'` → 파일 위치.
- 해당 파일을 **전체** `read_file`.

## 3. 아키텍처 레이어 (사용자 질문에 맞춰 선택)

- `ls` 로 유력 후보 2-3개 디렉토리 (src/, agent/, lib/, components/) 진입.
- 각 디렉토리의 `__init__.py` / `index.ts` / `mod.rs` 읽기 — public surface 가 보임.
- CLAUDE.md / docs/ 가 있으면 거기가 지름길.

## 4. 답변 전 확인 체크리스트

아래 3개를 **파일 근거로** 말할 수 있어야 함. 하나라도 모호하면 파일 더 읽기:

- ☐ 프로젝트가 뭘 하는지 1 문장 (`README.md:N` 근거)
- ☐ 엔트리포인트 `file:line`
- ☐ 가장 중요한 모듈 3개 — 각 1줄 역할

## 5. 답변 포맷

1. 구조 스케치 먼저 — **ASCII 트리는 반드시 ``` fenced code block 안에** 출력 (그래야 줄바꿈 보존)
2. 그 다음 구체적 `file:line` 참조
3. **메모리로 답하지 말고 방금 읽은 파일만 인용** — 추측 금지

## 자주 하는 실수

- `ls(depth=1)` 로 끝내기 — 너무 얕음. depth 2-3 써야 실제 구조가 보임.
- README 제목만 보고 답 추측 — 실제 코드 안 읽으면 틀림.
- 하위 디렉토리 이름만 보고 역할 추측 — `__init__.py` 나 대표 파일 최소 하나는 읽기.
