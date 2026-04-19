---
name: parallel-work
description: 여러 작업을 병렬 실행 — task graph + worktree + teammate + background 의 선택 기준과 패턴
---

트리거: "병렬로", "여러 방안 비교", "격리된 브랜치에서", "teammate", "worktree", "동시에".

## 필요한 것 → 사용할 primitive

| 상황 | Primitive | 이유 |
|------|-----------|------|
| 오래 걸리는 단일 명령 (install/build/test) | `background_run` | 블로킹 없이 이후 턴에서 notification |
| 2개 이상 **코드 변경** 이 서로 간섭하면 안 됨 | `worktree_create` 여러 개 | 격리된 git branch 각각 |
| 2개 이상 **read-only 탐색** 을 동시에 | `task(prompt=...)` 여러 개 | fresh context, 부모 context 보호 |
| 세션 넘어 상태 유지하는 일꾼 | `spawn_teammate` | persistent worker + inbox |
| 세션 간 태스크 보드 | `task_create` / `task_update` | `.tasks/` 파일 기반 |

## 패턴 A — N개 리팩터링 방안 비교

1. `task_create(subject="방안 A — <설명>")` × N
2. 각 task 마다 `worktree_create(name="opt-a", task_id=<id>)` — 격리된 브랜치
3. `worktree_run(name="opt-a", command="<수정 + 테스트>")` 로 실행
4. `worktree_run` 결과 비교
5. 승자는 `worktree_keep(name="opt-X")`, 나머지는 `worktree_remove`

## 패턴 B — 탐색 fan-out

"X 가 모든 서브시스템에 어떻게 쓰이는지":
1. 서브시스템 분할 (frontend / backend / infra / tests)
2. **한 턴에** `task(prompt="<서브시스템> 에서 <패턴> 검색 — file:line + 용도")` 3-4개 동시 발사
3. 각 subagent 의 Evidence 섹션을 서브시스템별 표로 병합

## 패턴 C — 장시간 명령과 병행 작업

1. `background_run(command="pnpm install && pnpm test")`
2. 즉시 다른 일 계속 — 탐색·편집 뭐든
3. 이후 턴에서 `<background-results>` 로 자동 주입됨. `background_status` 로 중간 확인 가능.

## Worktree 쓰지 말 것

- Read-only 탐색 → `task` 로 충분
- 단일 파일 편집 → `edit_file` 한 방
- 어차피 main 브랜치로 들어갈 일 → 브랜치 하나만
- 결과가 독립이 아니라 순차 의존 → 직렬이 정답

## 마무리 체크

- worktree 를 만들었으면 끝날 때 `worktree_keep` 또는 `worktree_remove` 로 반드시 결론 — 방치 금지.
- 생성한 task 는 `task_update(status="done")` 으로 종결.
