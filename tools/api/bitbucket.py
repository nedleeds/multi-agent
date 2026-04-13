"""Bitbucket REST API 클라이언트.

Server (Data Center) / Cloud 모두 지원합니다.
  BITBUCKET_TYPE=server  → /rest/api/1.0/  (기본값)
  BITBUCKET_TYPE=cloud   → /2.0/

지원 작업:
  bitbucket_list_commits(keyword, limit) — 키워드로 커밋 메시지 검색
  bitbucket_get_commit(commit_id)        — 특정 커밋 상세 + diff
  bitbucket_list_prs(query, state)       — PR 목록 (제목 키워드 필터)
"""

import json

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
                auth=self._auth(),
                headers=self._headers(),
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
            msg = f"No commits found" + (f" matching '{keyword}'" if keyword else "")
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
                auth=self._auth(),
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
            commit = resp.json()

            # 변경 파일 목록
            diff_resp = requests.get(
                self._url(f"{self._repo_path()}/commits/{commit_id}/diff"),
                auth=self._auth(),
                headers={"Accept": "text/plain"},
                timeout=30,
            )
            diff_text = diff_resp.text[:3000] if diff_resp.ok else "(diff unavailable)"
        except requests.HTTPError as exc:
            return f"Bitbucket API error {exc.response.status_code}: {exc.response.text[:400]}"
        except requests.RequestException as exc:
            return f"Bitbucket connection error: {exc}"

        result = {
            **self._summarize_commit(commit),
            "diff_preview": diff_text,
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    def list_prs(self, query: str = "", state: str = "ALL") -> str:
        """PR 목록을 반환합니다. query로 제목 필터링, state로 상태 필터링."""
        if not self.config.configured:
            return _NOT_CONFIGURED

        params: dict = {"limit": 25, "state": state.upper()}
        try:
            resp = requests.get(
                self._url(f"{self._repo_path()}/pull-requests"),
                params=params,
                auth=self._auth(),
                headers=self._headers(),
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
            return f"No pull requests found" + (f" matching '{query}'" if query else "")

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
