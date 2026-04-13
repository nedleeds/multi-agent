"""Jira REST API v3 클라이언트.

지원 작업:
  jira_search(query, max_results) — JQL 또는 자유 텍스트로 이슈 검색
  jira_get_issue(issue_key)       — 특정 이슈 상세 조회 (댓글 포함)
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
                self._url("/rest/api/3/search"),
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

    def get_issue(self, issue_key: str) -> str:
        """특정 이슈의 전체 내용과 최근 댓글을 반환합니다."""
        if not self.config.configured:
            return _NOT_CONFIGURED

        fields = "summary,status,priority,assignee,reporter,created,updated,description,labels,components,comment"
        try:
            resp = requests.get(
                self._url(f"/rest/api/3/issue/{issue_key}"),
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
        fields_data = data.get("fields", {})
        description = self._adf_to_text(fields_data.get("description") or "")

        recent_comments = []
        for c in (fields_data.get("comment") or {}).get("comments", [])[-5:]:
            recent_comments.append({
                "author": (c.get("author") or {}).get("displayName", ""),
                "created": (c.get("created") or "")[:10],
                "body": self._adf_to_text(c.get("body") or "")[:600],
            })

        result = {
            "key": data["key"],
            "summary": fields_data.get("summary", ""),
            "status": (fields_data.get("status") or {}).get("name", ""),
            "priority": (fields_data.get("priority") or {}).get("name", ""),
            "assignee": (fields_data.get("assignee") or {}).get("displayName", ""),
            "reporter": (fields_data.get("reporter") or {}).get("displayName", ""),
            "created": (fields_data.get("created") or "")[:10],
            "updated": (fields_data.get("updated") or "")[:10],
            "labels": fields_data.get("labels", []),
            "description": description[:2000],
            "recent_comments": recent_comments,
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
