"""API credentials loaded from environment variables.

모든 값은 .env에서 설정합니다. 설정되지 않은 경우 각 클라이언트가
'not configured' 메시지를 반환하므로 런타임 에러는 발생하지 않습니다.
"""

import os
from dataclasses import dataclass


@dataclass
class JiraConfig:
    base_url: str       # e.g. https://your-domain.atlassian.net
    email: str          # Atlassian account email
    api_token: str      # API token from id.atlassian.com
    project_key: str    # e.g. PROJ  (빈 문자열이면 전체 프로젝트 검색)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.email and self.api_token)


@dataclass
class BitbucketConfig:
    base_url: str       # Server: https://bitbucket.company.com  Cloud: https://api.bitbucket.org
    username: str       # Bitbucket username
    app_password: str   # App password (Server: personal token)
    project_key: str    # e.g. MYPROJ
    repo_slug: str      # e.g. my-repo (빈 문자열이면 프로젝트 전체)
    server_type: str    # "server" | "cloud"

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.username and self.app_password)


@dataclass
class ConfluenceConfig:
    base_url: str       # e.g. https://your-domain.atlassian.net/wiki
    username: str
    api_token: str
    space_key: str      # e.g. ENG (빈 문자열이면 전체 스페이스 검색)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.username and self.api_token)


def jira_config() -> JiraConfig:
    return JiraConfig(
        base_url=os.getenv("JIRA_URL", os.getenv("JIRA_BASE_URL", "")),
        email=os.getenv("JIRA_USERNAME", os.getenv("JIRA_EMAIL", "")),
        api_token=os.getenv("JIRA_PASSWORD", os.getenv("JIRA_API_TOKEN", "")),
        project_key=os.getenv("JIRA_DEFAULT_PROJECT", os.getenv("JIRA_PROJECT_KEY", "")),
    )

def bitbucket_config() -> BitbucketConfig:
    return BitbucketConfig(
        base_url=os.getenv("BITBUCKET_URL", os.getenv("BITBUCKET_BASE_URL", "")),
        username=os.getenv("BITBUCKET_USERNAME", ""),
        app_password=os.getenv("BITBUCKET_TOKEN", os.getenv("BITBUCKET_APP_PASSWORD", os.getenv("BITBUCKET_PASSWORD", ""))),
        project_key=os.getenv("BITBUCKET_DEFAULT_PROJECT", os.getenv("BITBUCKET_PROJECT_KEY", "")),
        repo_slug=os.getenv("BITBUCKET_REPO_SLUG", ""),
        server_type=os.getenv("BITBUCKET_TYPE", "server"),  # "server" | "cloud"
    )

def confluence_config() -> ConfluenceConfig:
    return ConfluenceConfig(
        base_url=os.getenv("CONFLUENCE_URL", os.getenv("CONFLUENCE_BASE_URL", "")),
        username=os.getenv("CONFLUENCE_USERNAME", os.getenv("CONFLUENCE_EMAIL", "")),
        api_token=os.getenv("CONFLUENCE_PASSWORD", os.getenv("CONFLUENCE_API_TOKEN", "")),
        space_key=os.getenv("CONFLUENCE_SPACE_KEY", ""),
    )
