"""Jira REST API v3 클라이언트.

지원 작업:
  jira_search(query, max_results)              — JQL 또는 자유 텍스트로 이슈 검색
  jira_search_multi(queries, ...)              — 여러 키워드 병렬 검색 + dedupe + 랭킹
  jira_get_issue(issue_key)                    — 이슈 상세 (댓글·issuelinks·fix_versions·attachments 등)
"""

import json
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from .config import JiraConfig, jira_config

_NOT_CONFIGURED = (
    "Jira API not configured. "
    "Set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN in .env"
)


class JiraClient:
    def __init__(self, config: JiraConfig | None = None):
        self.config = config or jira_config()

    def _auth(self) -> HTTPBasicAuth:
        return HTTPBasicAuth(self.config.email, self.config.api_token)

    def _headers(self) -> dict:
        return {"Accept": "application/json", "Content-Type": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}{path}"

    # ── Public interface ───────────────────────────────────────────────────

    def search(self, query: str, max_results: int = 10) -> str:
        """JQL 또는 자유 텍스트로 이슈를 검색합니다."""
        if not self.config.configured:
            return _NOT_CONFIGURED

        max_results = min(max(1, max_results), 50)
        jql = self._build_jql(query)
        fields = "summary,status,priority,assignee,reporter,created,updated,description,labels,components"

        try:
            resp = requests.get(
                self._url("/rest/api/2/search"),
                params={"jql": jql, "maxResults": max_results, "fields": fields},
                auth=self._auth(),
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            return f"Jira API error {exc.response.status_code}: {exc.response.text[:400]}"
        except requests.RequestException as exc:
            return f"Jira connection error: {exc}"

        issues = resp.json().get("issues", [])
        if not issues:
            return f"No Jira issues found for query: {query}"

        return json.dumps(
            [self._summarize_issue(i) for i in issues],
            ensure_ascii=False,
            indent=2,
        )

    def search_multi(
        self,
        queries: list[str],
        max_per_query: int = 20,
        top_k: int = 30,
    ) -> str:
        """여러 키워드를 각각 jira_search 로 돌린 뒤 issue_key 로 dedupe + 매치 횟수 기록.

        랭킹: match_score desc → updated desc → priority asc.
        결과에 `matched_queries` (어느 키워드에서 잡혔는지) 포함 — 신뢰도 판단용.
        """
        if not self.config.configured:
            return _NOT_CONFIGURED
        if not queries:
            return "Error: queries must be non-empty list"

        max_per_query = min(max(1, max_per_query), 50)
        top_k = min(max(1, top_k), 100)
        fields = "summary,status,priority,assignee,reporter,created,updated,labels,components"

        errors: list[str] = []
        agg: dict[str, dict] = {}

        for q in queries:
            jql = self._build_jql(q)
            try:
                resp = requests.get(
                    self._url("/rest/api/2/search"),
                    params={"jql": jql, "maxResults": max_per_query, "fields": fields},
                    auth=self._auth(),
                    headers=self._headers(),
                    timeout=30,
                )
                resp.raise_for_status()
            except requests.HTTPError as exc:
                errors.append(f"'{q}' → {exc.response.status_code} {exc.response.text[:120]}")
                continue
            except requests.RequestException as exc:
                errors.append(f"'{q}' → {exc}")
                continue

            for issue in resp.json().get("issues", []):
                key = issue.get("key", "")
                if not key:
                    continue
                slot = agg.setdefault(key, {"issue": issue, "matched": []})
                if q not in slot["matched"]:
                    slot["matched"].append(q)

        # 안정 정렬 3단계: 약한 키부터.
        priority_rank = {"Highest": 0, "High": 1, "Medium": 2, "Low": 3, "Lowest": 4}
        items = list(agg.values())
        items.sort(key=lambda x: priority_rank.get(
            ((x["issue"].get("fields") or {}).get("priority") or {}).get("name", ""), 5
        ))
        items.sort(
            key=lambda x: ((x["issue"].get("fields") or {}).get("updated") or ""),
            reverse=True,
        )
        items.sort(key=lambda x: len(x["matched"]), reverse=True)

        results = []
        for item in items[:top_k]:
            f = (item["issue"].get("fields") or {})
            results.append({
                "key": item["issue"].get("key", ""),
                "summary": f.get("summary", ""),
                "status": (f.get("status") or {}).get("name", ""),
                "priority": (f.get("priority") or {}).get("name", ""),
                "updated": (f.get("updated") or "")[:10],
                "matched_queries": item["matched"],
                "match_score": f"{len(item['matched'])}/{len(queries)}",
            })

        payload = {
            "queries_executed": queries,
            "total_unique_issues": len(agg),
            "returned": len(results),
            "ranking": "match_score desc → updated desc → priority asc",
            "results": results,
        }
        if errors:
            payload["errors"] = errors
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def get_issue(self, issue_key: str) -> str:
        """이슈 상세 — description·댓글·issuelinks·fix_versions·attachments·subtasks 포함.

        cap 은 tool output 전역 200KB 절삭에 위임 (여기서 자르지 않음).
        """
        if not self.config.configured:
            return _NOT_CONFIGURED

        fields = (
            "summary,status,priority,assignee,reporter,created,updated,duedate,"
            "description,labels,components,comment,"
            "issuelinks,fixVersions,resolution,resolutiondate,attachment,subtasks"
        )
        try:
            resp = requests.get(
                self._url(f"/rest/api/2/issue/{issue_key}"),
                params={"fields": fields},
                auth=self._auth(),
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            return f"Jira API error {exc.response.status_code}: {exc.response.text[:400]}"
        except requests.RequestException as exc:
            return f"Jira connection error: {exc}"

        data = resp.json()
        f = data.get("fields", {})
        description = self._adf_to_text(f.get("description") or "")

        comments = []
        for c in (f.get("comment") or {}).get("comments", [])[-10:]:
            comments.append({
                "author": (c.get("author") or {}).get("displayName", ""),
                "created": (c.get("created") or "")[:10],
                "body": self._adf_to_text(c.get("body") or ""),
            })

        issuelinks = []
        for link in f.get("issuelinks", []) or []:
            link_type = (link.get("type") or {}).get("name", "")
            if link.get("inwardIssue"):
                direction = (link.get("type") or {}).get("inward", "")
                related = link["inwardIssue"]
            elif link.get("outwardIssue"):
                direction = (link.get("type") or {}).get("outward", "")
                related = link["outwardIssue"]
            else:
                continue
            issuelinks.append({
                "type": link_type,
                "direction": direction,
                "key": related.get("key", ""),
                "summary": (related.get("fields") or {}).get("summary", ""),
                "status": (((related.get("fields") or {}).get("status")) or {}).get("name", ""),
            })

        fix_versions = [
            {
                "name": v.get("name", ""),
                "released": v.get("released", False),
                "releaseDate": v.get("releaseDate", ""),
            }
            for v in (f.get("fixVersions") or [])
        ]

        attachments = [
            {
                "filename": a.get("filename", ""),
                "size": a.get("size", 0),
                "mimeType": a.get("mimeType", ""),
                "created": (a.get("created") or "")[:10],
            }
            for a in (f.get("attachment") or [])
        ]

        subtasks = [
            {
                "key": s.get("key", ""),
                "summary": (s.get("fields") or {}).get("summary", ""),
                "status": (((s.get("fields") or {}).get("status")) or {}).get("name", ""),
            }
            for s in (f.get("subtasks") or [])
        ]

        result = {
            "key": data["key"],
            "summary": f.get("summary", ""),
            "status": (f.get("status") or {}).get("name", ""),
            "priority": (f.get("priority") or {}).get("name", ""),
            "resolution": (f.get("resolution") or {}).get("name", ""),
            "resolutiondate": (f.get("resolutiondate") or "")[:10],
            "assignee": (f.get("assignee") or {}).get("displayName", ""),
            "reporter": (f.get("reporter") or {}).get("displayName", ""),
            "created": (f.get("created") or "")[:10],
            "updated": (f.get("updated") or "")[:10],
            "duedate": f.get("duedate") or "",
            "labels": f.get("labels", []),
            "components": [c.get("name", "") for c in (f.get("components") or [])],
            "fix_versions": fix_versions,
            "description": description,
            "comments": comments,
            "issuelinks": issuelinks,
            "subtasks": subtasks,
            "attachments": attachments,
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _build_jql(self, query: str) -> str:
        is_jql = any(op in query for op in ["=", "~", " AND ", " OR ", "ORDER BY"])
        jql = query if is_jql else f'text ~ "{query}" ORDER BY updated DESC'
        if self.config.project_key and "project" not in jql.lower():
            jql = f'project = {self.config.project_key} AND ({jql})'
        return jql

    def _summarize_issue(self, issue: dict) -> dict:
        f = issue.get("fields", {})
        return {
            "key": issue.get("key", ""),
            "summary": f.get("summary", ""),
            "status": (f.get("status") or {}).get("name", ""),
            "priority": (f.get("priority") or {}).get("name", ""),
            "created": (f.get("created") or "")[:10],
            "updated": (f.get("updated") or "")[:10],
            "description_preview": self._adf_to_text(f.get("description") or "")[:300],
        }

    @staticmethod
    def _adf_to_text(node: Any, _depth: int = 0) -> str:
        """Atlassian Document Format → plain text."""
        if _depth > 10:
            return ""
        if isinstance(node, str):
            return node
        if isinstance(node, dict):
            if node.get("type") == "text":
                return node.get("text", "")
            parts = [JiraClient._adf_to_text(c, _depth + 1) for c in node.get("content", [])]
            return " ".join(p for p in parts if p)
        return ""
