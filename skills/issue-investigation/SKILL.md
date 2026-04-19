---
name: issue-investigation
description: 현장 이슈·장애·유사 사례 조사 — 키워드 분해 + 병렬 멀티서치 + 취합 랭킹 + 유력 후보 deep-dive + causal hypothesis
---

트리거: "현장 이슈", "장애", "버그 분석", "유사 사례", "incident", "postmortem", "왜 터졌는지", "에러가 발생", "재발".

이 스킬은 5단계 파이프라인. **순서 엄수**. 각 단계가 끝나야 다음 단계 진입.

---

## 0. 게이트 — 되묻기 (최소 조건 확인)

사용자 쿼리가 **구체어 하나도 없는 상태** ("버그 터졌어", "이슈 봐줘" 수준) 면 조사 불가 → 되묻고 STOP.

구체어 하나라도 있으면 (컴포넌트/에러 용어/증상 명사/동작) → **바로 통과**.

스코프(어느 서비스) / 시간 범위 / 환경은 **빠져도 조사 시작**. 키워드 분해와 도구로 메꾸거나, 종합 보고에서 `⚠ 미확인` 으로 표시. 되묻기는 최대 3개, 옵션 항목은 `(선택)` 명시.

**override 주의**: 시스템 프롬프트의 "Never ask the user to narrow scope" 는 코드 탐색 정책. 이슈 조사는 사용자 맥락이 필수적으로 필요한 경우가 있어 이 스킬에서만 예외.

---

## 1. 키워드 분해 (4–8개)

쿼리에서 검색 각도를 다각화. 반드시 다음 카테고리 포함:

- **원문 구 그대로** (`"playback 시간 초과"`)
- **단어별 쪼개기** (`"playback"`, `"시간 초과"`, `"플러그인"`)
- **한↔영 번역** (`"playback timeout"`, `"재생 시간초과"`, `"오류"` ↔ `"error"`)
- **에러 용어·시그니처** (`"ReadTimeoutException"`, `"HTTP 504"`, `"buffering"`)
- **코드 스타일 식별자** (`"playback_timeout"`, `"PLAYBACK_TIMEOUT"`)

이유: "playback 시간 초과" 전체 문구로는 티켓이 잘 안 잡혀도, 각 단어로는 hit 이 많음. 교차된 결과가 진짜 연관 높음.

---

## 2. 병렬 멀티서치 (한 턴에 3 delegation)

**같은 키워드 세트** 를 3 소스에 동시 발사 (직렬 금지 — 반드시 한 어시스턴트 턴의 tool_calls 3개):

```
jira_task(prompt="""
  키워드 [k1, k2, k3, k4, k5, k6] 로 jira_search_multi 실행.
  상위 2-3건 (match_score 높은 순) 에 대해 jira_get_issue 로 전문 — 
  description, comments, issuelinks, fix_versions, resolution 까지 추출.
""")

bitbucket_task(prompt="""
  키워드 [k1, k2, k3, k4, k5, k6] 로 bitbucket_search_multi.
  match_score ≥ 2 후보에 대해 get_pr_diff / get_commit 으로 실제 diff.
  시간 범위 힌트가 있으면 bitbucket_compare 로 릴리스 범위 diff 도.
""")

confluence_task(prompt="""
  키워드 중 broad 한 2-3개로 confluence_search.
  runbook / postmortem / 아키텍처 / known issue 중 관련 페이지 상위 3건.
""")
```

---

## 3. 취합 + 랭킹 해석

**Jira 결과 해석** (match_score 기준):
- `N/N` (전 키워드 매칭) — 거의 확실히 연관
- `≥ 3/N` — 유력 후보
- `2/N` — 약한 단서 (다른 증거와 조합 필요)
- `1/N, broad 키워드만` — 거의 무시

**Bitbucket 결과 해석**:
- PR/commit 의 match_score 와 **시간축** 동시 체크 — 사용자의 "최근" 범위 안에서 발생한 것에 가중치.

**Confluence**: 보조 컨텍스트 (아키텍처·런북). 원인 결정에는 직접 쓰지 않음.

---

## 4. Deep-dive — 자동/수동 분기

### 자동 진입 조건 (3개 모두 충족)

- ☐ 상위 1건의 match_score **≥ 3**
- ☐ 2위와 score 차 **≥ 1.5배** (예: 4/6 vs 2/6)
- ☐ 시간축 일치 — issue created / PR merged 가 사용자 언급한 "최근" 범위 안

**모두 충족** → 묻지 말고 자동 진행.

### 수동 모드 (조건 미충족)

상위 2-3건 요약 제시하고 사용자 선택 요청:

```
아래 후보 중 어느 쪽을 깊게 볼까요? 상위(A)로 진행해도 됩니다.
  A) MEDIA-412 (3/6) — Playback timeout after v2.3 [High, Open]
  B) MEDIA-418 (2/6) — 간헐 timeout 재발 [Medium, Open]
  C) AUDIO-77  (1/6) — 약한 단서
(기본: A)
```

사용자 응답 후 진행. 동일 질문 **재반복 금지**.

### 자동 진입 시 수행 단계

1. `jira_get_issue(top.key)` — description / 댓글 / issuelinks / fix_versions 전문
2. **연관 코드 변경 식별**:
   - a) Jira description·댓글에서 `PR#\d+` · commit hash 정규식 추출
   - b) 없으면 `issuelinks` 의 "relates to" 항목 추적
   - c) 그래도 없으면 bitbucket_search_multi 결과 중 이슈 `updated` 시각과 근접한 merged PR
3. `bitbucket_get_pr_diff(pr_id)` 또는 `bitbucket_get_commit(commit_id)` — 실제 unified diff
4. **Diff hunk 분석** — 증상과 연관될 만한 라인 지목:
   - 설정값 변경 (timeout / retry / buffer size / pool size)
   - 경합·초기화 순서
   - 경계 조건 / 타입 변경
   - 신규 동기 호출·lock·await 추가
   - 의존성 버전 업그레이드
5. **Causal hypothesis** (debug-issue 스킬 원칙 준용):
   - **구체**: `file:line` + 변경 전/후 값
   - **메커니즘**: "X 변경이 Y 경로에서 Z 조건 유발 → 증상"
   - **반증**: "이 변경을 revert 하면 증상 사라져야" 또는 "특정 재현 조건에서 확인"

---

## 5. 종합 보고 — 고정 포맷

```
## 이슈 요약
<2-3 문장: 무엇이 / 언제부터 / 영향 범위>

## Checklist
| 차원 | 값 | 근거 |
|------|---|------|
| 스코프       | <값 또는 ⚠ 미확인>     | 사용자 or Jira components |
| 시간 범위    | <값 또는 ⚠ 미확인>     | 사용자 or issue created |
| 증상 signature | <에러 class / HTTP> | Jira description |
| 환경         | prod/stg/⚠          | 사용자 or 태그 |
| 영향 범위    | N 사용자/⚠           | 댓글 집계 |
| 심각도       | priority + resolution | Jira |
| 관련 이슈 최다 | <KEY> (match_score) | jira_search_multi |

## Jira (상위 3건, match_score 순)
- <KEY-1> (N/M) [status/priority] — 제목 — 1줄 관련성
- <KEY-2> (N/M) …

## 코드 변경 (상위 2-3건)
- PR#<id> (match_score · merged YYYY-MM-DD) — 제목
- commit <hash> (match_score) — 메시지 첫 줄

## 문서
- <페이지 제목> — URL — 관련 발췌 1-2줄

## 유력 원인 (causal hypothesis)
**증상 ← 변경**: <심볼/파일:라인 + 변경 전/후>
**메커니즘**: <1-2 문장, 어떻게 그 변경이 증상을 유발하는지>
**근거 체인**:
  - <사실 1 — 출처>
  - <사실 2 — 출처>
  - <사실 3 — 출처>
**반증 경로**: <어떻게 가설을 확인/기각할지>
**가장 작은 수정안**: <1줄 hotfix 또는 revert 범위>

## 종합 판단
<어느 소스가 결정적 단서였는지 / 다음 액션 1개>
```

`⚠ 미확인` 은 숨기지 말고 명시. 가설이 약하면 `유력 원인` 섹션을 `## 후보 가설` 로 바꾸고 둘 이상 나열.

---

## 엣지 케이스

| 상황 | 처리 |
|------|------|
| jira_search_multi 가 `not configured` | 해당 섹션에 `[not configured — skipped]` 표시, 나머지 소스로 종합 |
| 어느 소스도 hit 0            | 키워드 2-3개 더 변형해서 **한 번 더** 멀티서치. 그래도 0 이면 "키워드 X/Y/Z 시도 결과 매칭 없음" 명시 |
| 상위 후보 모두 match_score 1/N | deep-dive 진입 안 함. "강한 단서 없음" 을 보고하고 사용자 추가 정보 요청 |
| 사용자가 "그냥 찾아봐" / "몰라" | 빠진 차원 `[추정]` 태그로 진행 + 종합 보고 상단에 추정 명시 |
| 같은 되묻기를 2번째 하려고 할 때 | 금지 — 진행하고 `⚠ 미해결` 로 표시 |
