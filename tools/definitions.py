"""OpenAI-format tool schemas.

BASE_TOOLS        : bash, read_file, write_file, edit_file
CHILD_TOOLS       : same as BASE_TOOLS (subagent cannot spawn further subagents)
ORCHESTRATOR_TOOLS: BASE_TOOLS + todo, load_skill, task, compact
"""

BASH = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a shell command in the current workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
    },
}

READ_FILE = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read the contents of a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer", "description": "Max lines to return"},
            },
            "required": ["path"],
        },
    },
}

WRITE_FILE = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write content to a file, creating parent directories if needed.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
}

EDIT_FILE = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": "Replace an exact string in a file once.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
}

TODO = {
    "type": "function",
    "function": {
        "name": "todo",
        "description": "Rewrite the current session plan for multi-step work. Keep exactly one item in_progress.",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                            "activeForm": {
                                "type": "string",
                                "description": "Present-continuous label while in_progress",
                            },
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["items"],
        },
    },
}

LOAD_SKILL = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": "Load the full body of a named skill into the current context.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name from the catalog"},
            },
            "required": ["name"],
        },
    },
}

TASK = {
    "type": "function",
    "function": {
        "name": "task",
        "description": (
            "Delegate a subtask to a subagent with a fresh context. "
            "The subagent shares the filesystem but not the conversation history."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Full instructions for the subagent"},
                "description": {"type": "string", "description": "Short label for display"},
            },
            "required": ["prompt"],
        },
    },
}

COMPACT = {
    "type": "function",
    "function": {
        "name": "compact",
        "description": "Summarize earlier conversation to free up context space.",
        "parameters": {
            "type": "object",
            "properties": {
                "focus": {"type": "string", "description": "What to preserve in the summary"},
            },
        },
    },
}

BASE_TOOLS = [BASH, READ_FILE, WRITE_FILE, EDIT_FILE]
CHILD_TOOLS = BASE_TOOLS
ORCHESTRATOR_TOOLS = BASE_TOOLS + [TODO, LOAD_SKILL, TASK, COMPACT]

# ── Issue investigation tools ─────────────────────────────────────────────

JIRA_SEARCH = {
    "type": "function",
    "function": {
        "name": "jira_search",
        "description": (
            "Search Jira issues using JQL or free text. "
            "Use to find similar bugs, duplicate tickets, or past incidents. "
            "Example queries: 'NullPointerException login', 'status = Open AND priority = High'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "JQL query or free text keywords",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max results to return (default 10, max 50)",
                },
            },
            "required": ["query"],
        },
    },
}

JIRA_GET_ISSUE = {
    "type": "function",
    "function": {
        "name": "jira_get_issue",
        "description": "Get full details of a Jira issue including description and recent comments.",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_key": {
                    "type": "string",
                    "description": "Issue key, e.g. PROJ-123",
                },
            },
            "required": ["issue_key"],
        },
    },
}

BITBUCKET_LIST_COMMITS = {
    "type": "function",
    "function": {
        "name": "bitbucket_list_commits",
        "description": (
            "List recent commits and filter by keyword in the commit message. "
            "Useful for finding code changes that may have caused an issue."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Keyword to filter commit messages (empty = no filter)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max commits to fetch before filtering (default 20)",
                },
            },
        },
    },
}

BITBUCKET_GET_COMMIT = {
    "type": "function",
    "function": {
        "name": "bitbucket_get_commit",
        "description": "Get the details and diff of a specific commit by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "commit_id": {
                    "type": "string",
                    "description": "Full or short commit hash",
                },
            },
            "required": ["commit_id"],
        },
    },
}

BITBUCKET_LIST_PRS = {
    "type": "function",
    "function": {
        "name": "bitbucket_list_prs",
        "description": "List pull requests, optionally filtered by title keyword and state.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Title keyword filter (empty = no filter)",
                },
                "state": {
                    "type": "string",
                    "description": "PR state: OPEN | MERGED | DECLINED | ALL (default ALL)",
                },
            },
        },
    },
}

CONFLUENCE_SEARCH = {
    "type": "function",
    "function": {
        "name": "confluence_search",
        "description": (
            "Search Confluence pages using CQL or free text. "
            "Use to find runbooks, incident reports, architecture docs, or known issues."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "CQL query or free text keywords",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max results to return (default 10)",
                },
            },
            "required": ["query"],
        },
    },
}

CONFLUENCE_GET_PAGE = {
    "type": "function",
    "function": {
        "name": "confluence_get_page",
        "description": "Get the full content of a Confluence page by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Confluence page ID (numeric string)",
                },
            },
            "required": ["page_id"],
        },
    },
}

JIRA_TOOLS = [JIRA_SEARCH, JIRA_GET_ISSUE]
BITBUCKET_TOOLS = [BITBUCKET_LIST_COMMITS, BITBUCKET_GET_COMMIT, BITBUCKET_LIST_PRS]
CONFLUENCE_TOOLS = [CONFLUENCE_SEARCH, CONFLUENCE_GET_PAGE]
ALL_API_TOOLS = JIRA_TOOLS + BITBUCKET_TOOLS + CONFLUENCE_TOOLS

# Orchestrator tools for issue investigation
# (specialized task tools replace the generic task tool)
JIRA_TASK = {
    "type": "function",
    "function": {
        "name": "jira_task",
        "description": "Delegate a Jira investigation to a subagent. The subagent has access to jira_search and jira_get_issue.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What to investigate in Jira"},
            },
            "required": ["prompt"],
        },
    },
}

BITBUCKET_TASK = {
    "type": "function",
    "function": {
        "name": "bitbucket_task",
        "description": "Delegate a Bitbucket investigation to a subagent. The subagent can search commits, get diffs, and list PRs.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What to investigate in Bitbucket"},
            },
            "required": ["prompt"],
        },
    },
}

CONFLUENCE_TASK = {
    "type": "function",
    "function": {
        "name": "confluence_task",
        "description": "Delegate a Confluence search to a subagent. The subagent can search pages and read their content.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What to look up in Confluence"},
            },
            "required": ["prompt"],
        },
    },
}

ISSUE_INVESTIGATOR_TOOLS = [TODO, JIRA_TASK, BITBUCKET_TASK, CONFLUENCE_TASK, COMPACT]
