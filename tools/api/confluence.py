"""Confluence REST API v1 클라이언트.

Confluence Server / Cloud 모두 REST API v1을 지원합니다.
Cloud의 경우 base_url에 /wiki를 포함해야 합니다.
  예: https://your-domain.atlassian.net/wiki

지원 작업:
  confluence_search(query, max_results) — CQL 또는 자유 텍스트 검색
  confluence_get_page(page_id)          — 페이지 본문 조회
"""

import json
import re

import requests
from requests.auth import HTTPBasicAuth

from .config import ConfluenceConfig, confluence_config

_NOT_CONFIGURED = (
    "Confluence API not configured. "
    "Set CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN in .env"
)


class ConfluenceClient:
    def __init__(self, config: ConfluenceConfig | None = None):
        self.config = config or confluence_config()

    def _auth(self) -> HTTPBasicAuth:
        return HTTPBasicAuth(self.config.username, self.config.api_token)

    def _headers(self) -> dict:
        return {"Accept": "application/json"}

    def _url(self, path: str) -> str:
        base = self.config.base_url.rstrip("/")
        return f"{base}{path}"

    # ── Public interface ───────────────────────────────────────────────────

    def search(self, query: str, max_results: int = 10) -> str:
        """CQL 또는 자유 텍스트로 Confluence 페이지를 검색합니다."""
        if not self.config.configured:
            return _NOT_CONFIGURED

        max_results = min(max(1, max_results), 50)
        cql = self._build_cql(query)

        try:
            resp = requests.get(
                self._url("/rest/api/content/search"),
                params={
                    "cql": cql,
                    "limit": max_results,
                    "expand": "metadata.labels,version,space",
                },
                auth=self._auth(),
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            return f"Confluence API error {exc.response.status_code}: {exc.response.text[:400]}"
        except requests.RequestException as exc:
            return f"Confluence connection error: {exc}"

        pages = resp.json().get("results", [])
        if not pages:
            return f"No Confluence pages found for: {query}"

        results = []
        for page in pages:
            results.append({
                "id": page.get("id", ""),
                "title": page.get("title", ""),
                "type": page.get("type", ""),
                "space": (page.get("space") or {}).get("key", ""),
                "url": self._page_url(page),
                "last_modified": (page.get("version") or {}).get("when", "")[:10],
                "labels": [
                    lbl.get("name", "")
                    for lbl in (page.get("metadata") or {}).get("labels", {}).get("results", [])
                ],
            })
        return json.dumps(results, ensure_ascii=False, indent=2)

    def get_page(self, page_id: str) -> str:
        """페이지 ID로 Confluence 페이지 본문을 반환합니다."""
        if not self.config.configured:
            return _NOT_CONFIGURED

        try:
            resp = requests.get(
                self._url(f"/rest/api/content/{page_id}"),
                params={"expand": "body.storage,version,space,ancestors"},
                auth=self._auth(),
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            return f"Confluence API error {exc.response.status_code}: {exc.response.text[:400]}"
        except requests.RequestException as exc:
            return f"Confluence connection error: {exc}"

        data = resp.json()
        html_body = (data.get("body") or {}).get("storage", {}).get("value", "")
        plain_text = self._html_to_text(html_body)

        result = {
            "id": data.get("id", ""),
            "title": data.get("title", ""),
            "space": (data.get("space") or {}).get("key", ""),
            "url": self._page_url(data),
            "last_modified": (data.get("version") or {}).get("when", "")[:10],
            "content": plain_text[:4000],
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _build_cql(self, query: str) -> str:
        is_cql = any(op in query for op in ["=", "~", " AND ", " OR ", "type ="])
        cql = query if is_cql else f'text ~ "{query}" ORDER BY lastmodified DESC'
        if self.config.space_key and "space" not in cql.lower():
            cql = f'space = "{self.config.space_key}" AND ({cql})'
        return cql

    def _page_url(self, page: dict) -> str:
        links = page.get("_links") or {}
        webui = links.get("webui", "")
        if webui:
            base = self.config.base_url.rstrip("/")
            return f"{base}{webui}"
        return ""

    @staticmethod
    def _html_to_text(html: str) -> str:
        """Confluence storage format HTML → 간단한 plain text."""
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&quot;", '"', text)
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()
