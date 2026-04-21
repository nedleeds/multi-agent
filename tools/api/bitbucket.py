"""Bitbucket REST API 클라이언트.

Server (Data Center) / Cloud 모두 지원합니다.
  BITBUCKET_TYPE=server  → /rest/api/1.0/  (기본값)
  BITBUCKET_TYPE=cloud   → /2.0/

지원 작업:
  bitbucket_list_commits(keyword, limit)    — 키워드로 커밋 메시지 검색
  bitbucket_get_commit(commit_id)           — 특정 커밋 상세 + diff
  bitbucket_list_prs(query, state)          — PR 목록 (제목 키워드 필터)
  bitbucket_search_multi(queries, ...)      — 여러 키워드로 commit + PR 병렬 검색 + 취합
  bitbucket_get_pr_diff(pr_id)              — 특정 PR 의 unified diff 원문
  bitbucket_compare(from_ref, to_ref)       — 두 ref(브랜치/태그/커밋) 간 diff
"""

import json
import base64

import requests
from requests.auth import HTTPBasicAuth

from .config import BitbucketConfig, bitbucket_config

_NOT_CONFIGURED = (
    "Bitbucket API not configured. "
    "Set BITBUCKET_BASE_URL, BITBUCKET_USERNAME, BITBUCKET_APP_PASSWORD in .env"
)


class BitbucketClient:
    def __init__(self, config: BitbucketConfig | None = None):
        self.config = config or bitbucket_config()

    def _auth(self) -> HTTPBasicAuth:
        return HTTPBasicAuth(self.config.username, self.config.app_password)

    def _auth_headers(self) -> dict:
        """latin-1 범위 밖 문자를 포함한 비밀번호 대응 — base64 직접 인코딩."""
        credentials = f"{self.config.username}:{self.config.app_password}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        return {
            "Accept": "application/json",
            "Authorization": f"Basic {encoded}",
        }

    def _headers(self) -> dict:
        return {"Accept": "application/json"}

    def _url(self, path: str) -> str:
        base = self.config.base_url.rstrip("/")
        if self.config.server_type == "cloud":
            return f"{base}/2.0{path}"
        return f"{base}/rest/api/1.0{path}"

    def _repo_path(self) -> str:
        if self.config.server_type == "cloud":
            return f"/repositories/{self.config.project_key}/{self.config.repo_slug}"
        return f"/projects/{self.config.project_key}/repos/{self.config.repo_slug}"

    # ── Public interface ───────────────────────────────────────────────────

    def list_commits(self, keyword: str = "", limit: int = 20) -> str:
        """최근 커밋을 가져오고 keyword로 메시지를 필터링합니다."""
        if not self.config.configured:
            return _NOT_CONFIGURED

        limit = min(max(1, limit), 100)
        try:
            resp = requests.get(
                self._url(f"{self._repo_path()}/commits"),
                params={"limit": limit},
                headers=self._auth_headers(),
                timeout=30,
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            return f"Bitbucket API error {exc.response.status_code}: {exc.response.text[:400]}"
        except requests.RequestException as exc:
            return f"Bitbucket connection error: {exc}"

        data = resp.json()
        commits = data.get("values", data) if isinstance(data, dict) else data

        if keyword:
            kw_lower = keyword.lower()
            commits = [
                c for c in commits
                if kw_lower in (c.get("message") or c.get("summary", {}).get("raw", "")).lower()
            ]

        if not commits:
            msg = "No commits found" + (f" matching '{keyword}'" if keyword else "")
            return msg

        return json.dumps(
            [self._summarize_commit(c) for c in commits[:limit]],
            ensure_ascii=False,
            indent=2,
        )

    def get_commit(self, commit_id: str) -> str:
        """특정 커밋의 상세 정보와 변경 파일 목록을 반환합니다."""
        if not self.config.configured:
            return _NOT_CONFIGURED

        try:
            resp = requests.get(
                self._url(f"{self._repo_path()}/commits/{commit_id}"),
                headers=self._auth_headers(),
                timeout=30,
            )
            resp.raise_for_status()
            commit = resp.json()

            # 변경 파일 목록 — 원문 전체 (tool output 단에서 200KB 절삭됨)
            diff_resp = requests.get(
                self._url(f"{self._repo_path()}/commits/{commit_id}/diff"),
                auth=self._auth(),
                headers={"Accept": "text/plain"},
                timeout=30,
            )
            diff_text = diff_resp.text if diff_resp.ok else "(diff unavailable)"
        except requests.HTTPError as exc:
            return f"Bitbucket API error {exc.response.status_code}: {exc.response.text[:400]}"
        except requests.RequestException as exc:
            return f"Bitbucket connection error: {exc}"

        result = {
            **self._summarize_commit(commit),
            "diff_preview": diff_text,
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    def get_pr_diff(self, pr_id: str) -> str:
        """특정 PR 의 unified diff 원문을 반환한다 (파일 변경 내역 전체)."""
        if not self.config.configured:
            return _NOT_CONFIGURED

        if self.config.server_type == "cloud":
            path = f"{self._repo_path()}/pullrequests/{pr_id}/diff"
        else:
            path = f"{self._repo_path()}/pull-requests/{pr_id}/diff"
        try:
            resp = requests.get(
                self._url(path),
                auth=self._auth(),
                headers={"Accept": "text/plain"},
                timeout=60,
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            return f"Bitbucket API error {exc.response.status_code}: {exc.response.text[:400]}"
        except requests.RequestException as exc:
            return f"Bitbucket connection error: {exc}"
        return resp.text or "(empty diff)"

    def compare(self, from_ref: str, to_ref: str) -> str:
        """두 ref(브랜치/태그/커밋) 간의 diff 를 반환한다.

        Server: GET /compare/diff?from=…&to=…
        Cloud:  GET /diff/{to..from}   (Cloud 스펙은 destination..source 순)
        """
        if not self.config.configured:
            return _NOT_CONFIGURED

        try:
            if self.config.server_type == "cloud":
                spec = f"{to_ref}..{from_ref}"
                resp = requests.get(
                    self._url(f"{self._repo_path()}/diff/{spec}"),
                    auth=self._auth(),
                    headers={"Accept": "text/plain"},
                    timeout=60,
                )
            else:
                resp = requests.get(
                    self._url(f"{self._repo_path()}/compare/diff"),
                    params={"from": from_ref, "to": to_ref},
                    auth=self._auth(),
                    headers={"Accept": "text/plain"},
                    timeout=60,
                )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            return f"Bitbucket API error {exc.response.status_code}: {exc.response.text[:400]}"
        except requests.RequestException as exc:
            return f"Bitbucket connection error: {exc}"
        return resp.text or "(empty diff)"

    def search_multi(
        self,
        queries: list[str],
        commit_limit: int = 50,
        pr_state: str = "ALL",
        top_k: int = 20,
    ) -> str:
        """여러 키워드로 commit/PR 을 **한 번 fetch 해 클라이언트 필터** 후 취합.

        - commits 는 최근 N 개를 한 번만 받아와서 각 키워드에 대해 메시지 매칭
        - PRs 도 최근 목록 한 번만 받아서 제목 매칭
        - id 로 dedupe + matched_queries 기록 + match_score 랭킹
        """
        if not self.config.configured:
            return _NOT_CONFIGURED
        if not queries:
            return "Error: queries must be non-empty list"

        commit_limit = min(max(1, commit_limit), 100)
        top_k = min(max(1, top_k), 100)

        # fetch once
        try:
            c_resp = requests.get(
                self._url(f"{self._repo_path()}/commits"),
                params={"limit": commit_limit},
                headers=self._auth_headers(),
                timeout=30,
            )
            c_resp.raise_for_status()
            p_resp = requests.get(
                self._url(f"{self._repo_path()}/pull-requests"),
                params={"limit": 50, "state": pr_state.upper()},
                headers=self._auth_headers(),
                timeout=30,
            )
            p_resp.raise_for_status()
        except requests.HTTPError as exc:
            return f"Bitbucket API error {exc.response.status_code}: {exc.response.text[:400]}"
        except requests.RequestException as exc:
            return f"Bitbucket connection error: {exc}"

        c_data = c_resp.json()
        commits_raw = c_data.get("values", c_data) if isinstance(c_data, dict) else c_data
        prs_raw = p_resp.json().get("values", [])

        # client-side multi-match
        commit_agg: dict[str, dict] = {}
        pr_agg: dict[str, dict] = {}

        for q in queries:
            ql = q.lower()
            for c in commits_raw:
                msg = (c.get("message") or (c.get("summary") or {}).get("raw", "")).lower()
                if ql in msg:
                    cid = str(c.get("id") or c.get("hash", ""))
                    slot = commit_agg.setdefault(cid, {"commit": c, "matched": []})
                    if q not in slot["matched"]:
                        slot["matched"].append(q)
            for pr in prs_raw:
                title = (pr.get("title") or "").lower()
                desc = (pr.get("description") or "").lower()
                if ql in title or ql in desc:
                    pid = str(pr.get("id", ""))
                    slot = pr_agg.setdefault(pid, {"pr": pr, "matched": []})
                    if q not in slot["matched"]:
                        slot["matched"].append(q)

        commit_items = sorted(commit_agg.values(), key=lambda x: len(x["matched"]), reverse=True)[:top_k]
        pr_items = sorted(pr_agg.values(), key=lambda x: len(x["matched"]), reverse=True)[:top_k]

        commits_out = []
        for item in commit_items:
            s = self._summarize_commit(item["commit"])
            s["matched_queries"] = item["matched"]
            s["match_score"] = f"{len(item['matched'])}/{len(queries)}"
            commits_out.append(s)

        prs_out = []
        for item in pr_items:
            pr = item["pr"]
            prs_out.append({
                "id": pr.get("id"),
                "title": pr.get("title", ""),
                "state": pr.get("state", ""),
                "author": (pr.get("author") or {}).get("displayName") or
                          (pr.get("author") or {}).get("user", {}).get("displayName", ""),
                "created": (pr.get("createdDate") or pr.get("created_on") or "")[:10],
                "updated": (pr.get("updatedDate") or pr.get("updated_on") or "")[:10],
                "matched_queries": item["matched"],
                "match_score": f"{len(item['matched'])}/{len(queries)}",
            })

        return json.dumps({
            "queries_executed": queries,
            "commits_unique": len(commit_agg),
            "prs_unique": len(pr_agg),
            "ranking": "match_score desc",
            "commits": commits_out,
            "pull_requests": prs_out,
        }, ensure_ascii=False, indent=2)

    def list_prs(self, query: str = "", state: str = "ALL") -> str:
        """PR 목록을 반환합니다. query로 제목 필터링, state로 상태 필터링."""
        if not self.config.configured:
            return _NOT_CONFIGURED

        params: dict = {"limit": 25, "state": state.upper()}
        try:
            resp = requests.get(
                self._url(f"{self._repo_path()}/pull-requests"),
                params=params,
                headers=self._auth_headers(),
                timeout=30,
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            return f"Bitbucket API error {exc.response.status_code}: {exc.response.text[:400]}"
        except requests.RequestException as exc:
            return f"Bitbucket connection error: {exc}"

        prs = resp.json().get("values", [])
        if query:
            q_lower = query.lower()
            prs = [p for p in prs if q_lower in (p.get("title") or "").lower()]

        if not prs:
            return "No pull requests found" + (f" matching '{query}'" if query else "")

        results = []
        for pr in prs:
            results.append({
                "id": pr.get("id"),
                "title": pr.get("title", ""),
                "state": pr.get("state", ""),
                "author": (pr.get("author") or {}).get("displayName", pr.get("author", {}).get("user", {}).get("displayName", "")),
                "created": (pr.get("createdDate") or pr.get("created_on") or "")[:10],
                "updated": (pr.get("updatedDate") or pr.get("updated_on") or "")[:10],
                "description_preview": (pr.get("description") or "")[:200],
            })
        return json.dumps(results, ensure_ascii=False, indent=2)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _summarize_commit(self, c: dict) -> dict:
        # Server and Cloud have slightly different field names
        author = (
            (c.get("author") or {}).get("displayName")
            or (c.get("author") or {}).get("user", {}).get("displayName")
            or (c.get("author") or {}).get("name", "")
        )
        message = c.get("message") or (c.get("summary") or {}).get("raw", "")
        commit_id = c.get("id") or c.get("hash", "")
        ts = c.get("authorTimestamp") or c.get("date") or ""
        if isinstance(ts, int):
            from datetime import datetime, timezone
            ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            ts = str(ts)[:10]
        return {
            "id": str(commit_id)[:12],
            "message": message[:200],
            "author": author,
            "date": ts,
        }
