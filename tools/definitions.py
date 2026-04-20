"""OpenAI-format tool schemas.

BASE_TOOLS              : bash, read_file, write_file, edit_file
CHILD_TOOLS             : same as BASE_TOOLS
ORCHESTRATOR_TOOLS      : BASE_TOOLS + todo, load_skill, task, compact
TEAM_ORCHESTRATOR_TOOLS : BASE_TOOLS + task_*, background_run, spawn_teammate,
                          send_message, read_inbox, broadcast_message,
                          request_shutdown, respond_shutdown,
                          submit_plan, review_plan,
                          worktree_create/list/run/keep/remove/events,
                          compact
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
        "description": (
            "Read file contents, optionally paginated. For large files, use `limit` + "
            "`offset` to scroll through sections. Truncation hint will tell you the "
            "next offset value to use."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path":   {"type": "string"},
                "limit":  {"type": "integer", "description": "Max lines to return (0/omit = until EOF)"},
                "offset": {"type": "integer", "description": "Start line (0-indexed, default 0)"},
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

# ── 코드 탐색 툴 (rg/fzf 기반) ────────────────────────────────────────────────

GREP = {
    "type": "function",
    "function": {
        "name": "grep",
        "description": (
            "Content search via ripgrep. Respects .gitignore, auto-excludes binary files.\n"
            "**Prefer this over `bash grep`** for finding where something is defined/used, "
            "locating patterns, or searching the codebase.\n"
            "Examples:\n"
            "  grep(pattern='def run_subagent')\n"
            "  grep(pattern='status.*bar', type='py')\n"
            "  grep(pattern='TODO', output_mode='files_with_matches')\n"
            "  grep(pattern='error', glob='**/*.py', context=3)\n"
            "  grep(pattern='ClassName', case_insensitive=true)"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern":          {"type": "string", "description": "Regex pattern"},
                "path":             {"type": "string", "description": "File or dir to search (default '.')"},
                "glob":             {"type": "string", "description": "Glob filter e.g. '**/*.py', '!tests/**'"},
                "type":             {"type": "string", "description": "rg file type e.g. 'py', 'js', 'rust', 'md'"},
                "output_mode":      {"type": "string", "enum": ["content", "files_with_matches", "count"],
                                      "description": "content=matching lines, files_with_matches=paths only, count=counts"},
                "case_insensitive": {"type": "boolean"},
                "line_numbers":     {"type": "boolean", "description": "default true"},
                "context":          {"type": "integer", "description": "lines before/after each match"},
                "head_limit":       {"type": "integer", "description": "truncate at N lines (default 200)"},
            },
            "required": ["pattern"],
        },
    },
}

GLOB = {
    "type": "function",
    "function": {
        "name": "glob",
        "description": (
            "Find files by glob pattern via `rg --files`. Respects .gitignore.\n"
            "Use for 'find all X files', 'list tests', 'enumerate source files'.\n"
            "Examples:\n"
            "  glob(pattern='**/*.py')\n"
            "  glob(pattern='tests/**/*.py')\n"
            "  glob(pattern='agent/*.py')\n"
            "  glob(pattern='**/*.{md,rst}')"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, supports {a,b}, **"},
                "path":    {"type": "string", "description": "default '.'"},
            },
            "required": ["pattern"],
        },
    },
}

LS = {
    "type": "function",
    "function": {
        "name": "ls",
        "description": (
            "Tree-style directory listing. Auto-excludes hidden dirs (.venv/.git/...) and noise "
            "(__pycache__, node_modules, .ruff_cache, dist, build, etc.).\n"
            "**Use this for 'project structure', 'list folders', 'explore layout'** — NOT `bash ls`.\n"
            "Examples:\n"
            "  ls(path='.', depth=3)                     # full tree 3 levels\n"
            "  ls(path='.', depth=2, dirs_only=true)      # folder hierarchy\n"
            "  ls(path='agent', depth=1)                  # shallow\n"
            "  ls(path='.', depth=4, hidden=true)         # include .env, .git, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path":      {"type": "string", "description": "default '.'"},
                "depth":     {"type": "integer", "description": "max depth (default 2)"},
                "dirs_only": {"type": "boolean", "description": "dirs only (default false)"},
                "hidden":    {"type": "boolean", "description": "include hidden entries (default false)"},
            },
        },
    },
}

FUZZY_FIND = {
    "type": "function",
    "function": {
        "name": "fuzzy_find",
        "description": (
            "Fuzzy file-path search via rg+fzf. Use when you don't know the exact filename.\n"
            "IMPORTANT: query uses fzf AND semantics — each word fuzzy-matches the file path, "
            "and ALL words must match. Use ONE narrow word per call for best results; "
            "for content search use `grep` instead.\n"
            "Examples:\n"
            "  fuzzy_find(query='repl')        # finds utils/repl.py\n"
            "  fuzzy_find(query='subagent')    # finds agent/subagent.py\n"
            "  fuzzy_find(query='cfg model')   # files whose path matches BOTH 'cfg' and 'model'"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Fuzzy query — words matched loosely"},
                "path":  {"type": "string", "description": "default '.'"},
                "limit": {"type": "integer", "description": "max results (default 50)"},
            },
            "required": ["query"],
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

SEARCH_TOOLS = [GREP, GLOB, LS, FUZZY_FIND]
BASE_TOOLS = [BASH, READ_FILE, WRITE_FILE, EDIT_FILE] + SEARCH_TOOLS
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

BITBUCKET_GET_PR_DIFF = {
    "type": "function",
    "function": {
        "name": "bitbucket_get_pr_diff",
        "description": (
            "Return the full unified diff for a specific pull request. "
            "Use this after bitbucket_list_prs to inspect the actual code changes in a PR."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pr_id": {
                    "type": "string",
                    "description": "Pull request ID (numeric)",
                },
            },
            "required": ["pr_id"],
        },
    },
}

BITBUCKET_COMPARE = {
    "type": "function",
    "function": {
        "name": "bitbucket_compare",
        "description": (
            "Return the unified diff between two refs (branches, tags, or commit hashes). "
            "Useful for 'what changed between release X and Y' or 'diff between main and feature branch'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "from_ref": {
                    "type": "string",
                    "description": "Source ref (older / feature branch). e.g. 'v1.2.0' or 'feature/foo'",
                },
                "to_ref": {
                    "type": "string",
                    "description": "Target ref (newer / base branch). e.g. 'v1.3.0' or 'main'",
                },
            },
            "required": ["from_ref", "to_ref"],
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

JIRA_SEARCH_MULTI = {
    "type": "function",
    "function": {
        "name": "jira_search_multi",
        "description": (
            "Run multiple Jira searches in parallel and aggregate results by issue_key. "
            "Each issue is annotated with which queries matched it (matched_queries) and a "
            "match_score. Use this INSTEAD of multiple jira_search calls when investigating "
            "an issue — decompose the user's description into 4–8 keyword variants (full phrase, "
            "individual words, Korean↔English, error class names) and pass them all at once. "
            "Ranking: match_score desc → updated desc → priority asc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keyword variants to search. e.g. ['playback 시간 초과', 'playback', '시간 초과', 'timeout', 'playback timeout']",
                },
                "max_per_query": {"type": "integer", "description": "Max results per query (default 20)"},
                "top_k": {"type": "integer", "description": "Max aggregated results to return (default 30)"},
            },
            "required": ["queries"],
        },
    },
}

BITBUCKET_SEARCH_MULTI = {
    "type": "function",
    "function": {
        "name": "bitbucket_search_multi",
        "description": (
            "Run multi-keyword search over recent commits AND pull requests in one call. "
            "Fetches recent commits/PRs once then matches each keyword against message/title/description "
            "client-side, aggregating by id with match_score. Use for finding recent changes that "
            "might relate to an issue — pass the same keyword variants used for jira_search_multi."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keyword variants (same decomposition as jira_search_multi)",
                },
                "commit_limit": {"type": "integer", "description": "Recent commits to fetch (default 50, max 100)"},
                "pr_state": {"type": "string", "description": "OPEN | MERGED | DECLINED | ALL (default ALL)"},
                "top_k": {"type": "integer", "description": "Max per category to return (default 20)"},
            },
            "required": ["queries"],
        },
    },
}

JIRA_TOOLS = [JIRA_SEARCH, JIRA_SEARCH_MULTI, JIRA_GET_ISSUE]
BITBUCKET_TOOLS = [
    BITBUCKET_LIST_COMMITS,
    BITBUCKET_GET_COMMIT,
    BITBUCKET_LIST_PRS,
    BITBUCKET_SEARCH_MULTI,
    BITBUCKET_GET_PR_DIFF,
    BITBUCKET_COMPARE,
]
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

# ── s07: Task graph tools ────────────────────────────────────────────────────

TASK_CREATE = {
    "type": "function",
    "function": {
        "name": "task_create",
        "description": "Create a new task on the persistent task board (.tasks/).",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["subject"],
        },
    },
}

TASK_LIST = {
    "type": "function",
    "function": {
        "name": "task_list",
        "description": "List all tasks with status, owner, blocked-by, and worktree binding.",
        "parameters": {"type": "object", "properties": {}},
    },
}

TASK_GET = {
    "type": "function",
    "function": {
        "name": "task_get",
        "description": "Get full details of a task by ID.",
        "parameters": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"],
        },
    },
}

TASK_UPDATE = {
    "type": "function",
    "function": {
        "name": "task_update",
        "description": "Update task status, owner, or dependency edges.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                "owner": {"type": "string"},
                "add_blocked_by": {"type": "array", "items": {"type": "integer"}},
                "remove_blocked_by": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["task_id"],
        },
    },
}

# ── s08: Background task tools ───────────────────────────────────────────────

BACKGROUND_RUN = {
    "type": "function",
    "function": {
        "name": "background_run",
        "description": "Run a slow shell command in the background. The agent keeps working; results arrive as notifications before the next LLM call.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
}

BACKGROUND_STATUS = {
    "type": "function",
    "function": {
        "name": "background_status",
        "description": "Check the status of all background tasks.",
        "parameters": {"type": "object", "properties": {}},
    },
}

# ── s09: Team communication tools ────────────────────────────────────────────

SPAWN_TEAMMATE = {
    "type": "function",
    "function": {
        "name": "spawn_teammate",
        "description": "Spawn a persistent teammate agent that runs in its own thread. Unlike subagents, teammates have identity and persist across tasks.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Unique teammate name (e.g. 'alice')"},
                "role": {"type": "string", "description": "Teammate role (e.g. 'coder', 'tester')"},
                "prompt": {"type": "string", "description": "Initial task or instructions for this teammate"},
            },
            "required": ["name", "role", "prompt"],
        },
    },
}

SEND_MESSAGE = {
    "type": "function",
    "function": {
        "name": "send_message",
        "description": "Send a message to a teammate's inbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "content": {"type": "string"},
                "type": {"type": "string", "enum": ["message", "broadcast"]},
            },
            "required": ["to", "content"],
        },
    },
}

READ_INBOX = {
    "type": "function",
    "function": {
        "name": "read_inbox",
        "description": "Read and drain the lead's inbox (messages from teammates).",
        "parameters": {"type": "object", "properties": {}},
    },
}

BROADCAST_MESSAGE = {
    "type": "function",
    "function": {
        "name": "broadcast_message",
        "description": "Broadcast a message to all teammates.",
        "parameters": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
        },
    },
}

LIST_TEAM = {
    "type": "function",
    "function": {
        "name": "list_team",
        "description": "Show all teammates and their current status.",
        "parameters": {"type": "object", "properties": {}},
    },
}

# ── s10: Shutdown + plan approval protocol tools ─────────────────────────────

REQUEST_SHUTDOWN = {
    "type": "function",
    "function": {
        "name": "request_shutdown",
        "description": "Request a teammate to shut down gracefully (sends a shutdown_request message).",
        "parameters": {
            "type": "object",
            "properties": {"teammate": {"type": "string"}},
            "required": ["teammate"],
        },
    },
}

RESPOND_SHUTDOWN = {
    "type": "function",
    "function": {
        "name": "respond_shutdown",
        "description": "Approve or reject a teammate's shutdown request.",
        "parameters": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "approve": {"type": "boolean"},
                "reason": {"type": "string"},
            },
            "required": ["request_id", "approve"],
        },
    },
}

SUBMIT_PLAN = {
    "type": "function",
    "function": {
        "name": "submit_plan",
        "description": "Submit a plan for lead review before executing risky changes.",
        "parameters": {
            "type": "object",
            "properties": {
                "from_name": {"type": "string"},
                "plan": {"type": "string"},
            },
            "required": ["from_name", "plan"],
        },
    },
}

REVIEW_PLAN = {
    "type": "function",
    "function": {
        "name": "review_plan",
        "description": "Approve or reject a plan submitted by a teammate.",
        "parameters": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "approve": {"type": "boolean"},
                "feedback": {"type": "string"},
            },
            "required": ["request_id", "approve"],
        },
    },
}

LIST_SHUTDOWN_REQUESTS = {
    "type": "function",
    "function": {
        "name": "list_shutdown_requests",
        "description": "List all pending/resolved shutdown requests.",
        "parameters": {"type": "object", "properties": {}},
    },
}

LIST_PLAN_REQUESTS = {
    "type": "function",
    "function": {
        "name": "list_plan_requests",
        "description": "List all pending/resolved plan approval requests.",
        "parameters": {"type": "object", "properties": {}},
    },
}

# ── s12: Worktree tools ───────────────────────────────────────────────────────

WORKTREE_CREATE = {
    "type": "function",
    "function": {
        "name": "worktree_create",
        "description": "Create a git worktree for isolated task execution. Optionally bind to a task (auto-advances task to in_progress).",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "task_id": {"type": "integer"},
                "base_ref": {"type": "string", "description": "Git ref to branch from (default: HEAD)"},
            },
            "required": ["name"],
        },
    },
}

WORKTREE_LIST = {
    "type": "function",
    "function": {
        "name": "worktree_list",
        "description": "List all worktrees tracked in .worktrees/index.json.",
        "parameters": {"type": "object", "properties": {}},
    },
}

WORKTREE_RUN = {
    "type": "function",
    "function": {
        "name": "worktree_run",
        "description": "Run a shell command inside a named worktree directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "command": {"type": "string"},
            },
            "required": ["name", "command"],
        },
    },
}

WORKTREE_KEEP = {
    "type": "function",
    "function": {
        "name": "worktree_keep",
        "description": "Mark a worktree as kept (preserved for later) without removing it.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
}

WORKTREE_REMOVE = {
    "type": "function",
    "function": {
        "name": "worktree_remove",
        "description": "Remove a worktree. Set complete_task=true to also mark the bound task as completed.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "force": {"type": "boolean"},
                "complete_task": {"type": "boolean"},
            },
            "required": ["name"],
        },
    },
}

WORKTREE_EVENTS = {
    "type": "function",
    "function": {
        "name": "worktree_events",
        "description": "Show recent worktree/task lifecycle events from .worktrees/events.jsonl.",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
        },
    },
}

# ── Toolsets ──────────────────────────────────────────────────────────────────

_TASK_GRAPH_TOOLS = [TASK_CREATE, TASK_LIST, TASK_GET, TASK_UPDATE]
_BACKGROUND_TOOLS = [BACKGROUND_RUN, BACKGROUND_STATUS]
_TEAM_TOOLS = [SPAWN_TEAMMATE, SEND_MESSAGE, READ_INBOX, BROADCAST_MESSAGE, LIST_TEAM]
_PROTOCOL_TOOLS = [REQUEST_SHUTDOWN, RESPOND_SHUTDOWN, SUBMIT_PLAN, REVIEW_PLAN,
                   LIST_SHUTDOWN_REQUESTS, LIST_PLAN_REQUESTS]
_WORKTREE_TOOLS = [WORKTREE_CREATE, WORKTREE_LIST, WORKTREE_RUN,
                   WORKTREE_KEEP, WORKTREE_REMOVE, WORKTREE_EVENTS]

TEAM_ORCHESTRATOR_TOOLS = (
    BASE_TOOLS
    + _TASK_GRAPH_TOOLS
    + _BACKGROUND_TOOLS
    + _TEAM_TOOLS
    + _PROTOCOL_TOOLS
    + _WORKTREE_TOOLS
    + [COMPACT]
)

# ── 통합 툴셋 ────────────────────────────────────────────────────────────────
# 모든 기능을 하나의 orchestrator 에 노출. 모델이 intent 로 tool 을 선택.
UNIFIED_TOOLS = (
    BASE_TOOLS                                         # bash, read/write/edit, grep/glob/ls/fuzzy_find
    + [TODO, LOAD_SKILL, TASK, COMPACT]                # 메타
    + [JIRA_TASK, BITBUCKET_TASK, CONFLUENCE_TASK]     # 이슈조사 (subagent 위임)
    + _TASK_GRAPH_TOOLS                                # s07
    + _BACKGROUND_TOOLS                                # s08
    + _TEAM_TOOLS                                      # s09
    + _PROTOCOL_TOOLS                                  # s10
    + _WORKTREE_TOOLS                                  # s12
)


# ── Intent 기반 tool tier 선택 ────────────────────────────────────────────────
# Router (agent/router.py) 가 user turn 마다 intent 집합을 반환하면 이 헬퍼가
# 해당 tier 에 맞는 tools 만 고른다. CHAT 만 있으면 tools 없이 즉답.

VALID_INTENTS = {"CHAT", "CODING", "ISSUE", "TEAM"}


def tools_for_tier(tier: set[str]) -> list[dict]:
    """Intent 집합 → 필요한 OpenAI-format tool schema 리스트.

    - `{CHAT}` 또는 빈 집합       → `[]`  (잡담 · 메타 질문 즉답)
    - 기술적 intent 가 하나라도    → `BASE_TOOLS + [TODO, LOAD_SKILL, TASK, COMPACT]`
    - `ISSUE` 추가                → Jira/BB/Confluence task delegation
    - `TEAM` 추가                 → task graph · background · team · protocol · worktree
    """
    clean = tier & VALID_INTENTS
    if not clean or clean == {"CHAT"}:
        return []

    tools: list[dict] = list(BASE_TOOLS) + [TODO, LOAD_SKILL, TASK, COMPACT]
    if "ISSUE" in clean:
        tools += [JIRA_TASK, BITBUCKET_TASK, CONFLUENCE_TASK]
    if "TEAM" in clean:
        tools += (
            _TASK_GRAPH_TOOLS
            + _BACKGROUND_TOOLS
            + _TEAM_TOOLS
            + _PROTOCOL_TOOLS
            + _WORKTREE_TOOLS
        )
    return tools
