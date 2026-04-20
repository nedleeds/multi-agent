---
name: search-iteration
description: 코드 검색을 어떻게 잘 반복할지 — grep/glob/ls/fuzzy_find 선택, 쿼리 확장, 잘림 감지, 서베이 질문 ("관련 전부/list all/모두"), 매칭 실패 후 재시도, 사용자에게 되묻기 전 해야 할 것들
---

트리거: 검색을 한 번 이상 돌려야 하는 모든 작업 — "어디 있어?", "관련 파일", "list all X", "전부 알려줘", grep/glob 결과가 `(no matches)` 거나 truncated, survey/exploration 질문.

## 검색 도구 치트시트

| Intent                          | Tool                      | Example |
| ------------------------------- | ------------------------- | ------- |
| content search (regex/string)   | **`grep`**                | grep(pattern='def foo', type='py') |
| find files by name pattern      | **`glob`**                | glob(pattern='**/*.py') |
| directory tree / structure      | **`ls`**                  | ls(path='.', depth=3) |
| find file when name is fuzzy    | **`fuzzy_find`**          | fuzzy_find(query='repl') |
| read one file                   | **`read_file`**           | read_file(path='utils/repl.py') |
| modify one file                 | `write_file` / `edit_file`| — |
| anything else (env, run, git)   | `bash`                    | bash(command='git log -n5') |

`grep` / `glob` / `ls` / `fuzzy_find` 는 이미 .gitignore 와 `.venv`, `__pycache__`, `node_modules`, `.git`, 바이너리를 걸러냄. 코드 탐색에 `bash grep` / `bash find` / `bash ls` 를 쓰면 노이즈에 파묻힌다 — **금지**.

## 쿼리 확장 — user intent ≠ code identifier (CRITICAL)

자연어 표현이 코드에 그대로 등장하는 경우는 드물다. 검색 전에 번역:

  "status bar" / "상태표시줄"  → `status`, `statusline`, `status_line`, `status\.`
  "icon" / "아이콘"            → `icon`, `ICON`, `_ICON`
  "color" / "색상"             → `color`, `#[0-9A-Fa-f]`, `theme`, `style`
  "folder" / "폴더"            → `folder`, `dir`, `directory`
  "input" / "입력"             → `input`, `buffer`, `prompt`

검색이 `(no matches)` 를 반환하면, 결론 내기 전에 **최소 2회 이상 바리에이션** 으로 재시도: 번역, code-style identifier, 축약형. 한국어 개념이면 영어 코드 용어도 시도 — 역도 마찬가지.

## 대화 맥락을 포인터로 사용 (항상 먼저)

새 검색 전에 이전 턴을 스캔:

- 특정 파일이 이미 언급됐나? → **먼저 `read_file`**.
- 이전 `grep` 이 라인 번호를 찾았나? → 그 주변을 읽자. 다시 grep 하지 마라.
- 사용자의 현재 질문이 이미 논의된 것의 후속인가? → 같은 모듈이라고 가정.

히스토리가 치트시트다 — 이미 답이 있는 걸 재탐색하지 마라.

## Tool 결과 완전성 체크 (CRITICAL)

모든 tool 결과는 사용 전 확인:

- 출력에 `[OUTPUT TRUNCATED`, `[TOOL RESULT TRUNCATED`, `[TRUNCATED at` 가 포함됐거나, `…` 로 끝나거나, "more items" 를 언급하면 — 결과는 **불완전**. 그 결과로 답하지 마라.
- pagination / narrower scope 로 재실행: `head_limit` 증가, `output_mode='files_with_matches'` 로 먼저 파일 리스트 뽑고 드릴다운, `glob`/`type` 필터 추가, `path` 좁히기, `read_file` 의 `limit` 증가.

## 서베이 / 탐색 질문 — "관련 / 전부 / list all / 모두" (CRITICAL)

"X 관련 뭐가 있어?", "Y 전부 나열", "시스템 프롬프트 관련 파일들", "해당 기능 전부" 같은 질문은 **포괄적** 답변을 요구한다:

  1. grep 바리에이션 다수 — 최소 3개 패턴 (동의어, code-style, 번역).
  2. hit 파일을 **각각** 읽는다 — 첫 파일만 읽지 않는다. 서베이는 정의상 복수 답이 있다.
  3. 용도/역할로 분류.
  4. 결과는 `file:line — 1-line purpose` 로, 최소 3 entry (또는 탐색 trail 명시: "searched X/Y/Z, only N relevant").

범위가 넓으면 `task(prompt='Find all X across the repo — list file:line + purpose per hit')` 로 subagent 에 위임.

## 답 내보내기 전 — thoroughness 체크

특히 서베이/탐색/list 질문에서, 답하기 전에 확인:

  ☐ 3+ 검색 패턴 시도 (검색이 필요했던 경우)
  ☐ ≥2 후보 파일을 실제로 읽음 (단지 grep 매치가 아님)
  ☐ 답에 구체적 `file:line` 인용 (한 줄 일반론 금지)
  ☐ "all X" 질문에 — 답에 복수 항목 (하나만 있으면 안 됨)

하나라도 체크 안 된 항목이 있으면 답하기 전에 **더** 반복.

## 사용자에게 "범위 좁혀달라" 라고 묻기 전에

"찾을 수 없음, 좀 더 구체적으로?" 는 **첫 응답이 아니라 실패**다. 묻기 전에 반드시:

  1. 대화 맥락 체크 — 이전 턴에 언급된 파일/식별자 `read_file`.
  2. grep 바리에이션 3+ 시도 (동의어, 번역, code-style identifier).
  3. 후보 디렉토리 (utils/, src/, agent/, components/, …) 2–3 개 `ls` + 가장 유망한 hit `read_file`.

그 다음에야 묻는다 — 묻더라도 **시도한 것과 찾은 것을 보고**하고, "couldn't find" 라는 맨눈 응답은 금지.
