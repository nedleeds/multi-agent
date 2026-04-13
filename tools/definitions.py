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
