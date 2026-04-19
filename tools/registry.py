"""ToolRegistry: maps tool names to handler functions and dispatches calls."""

import json
from collections.abc import Callable

from . import handlers


class ToolRegistry:
    def __init__(self):
        self._handlers: dict[str, Callable] = {
            "bash":       lambda **kw: handlers.bash(kw["command"]),
            "read_file":  lambda **kw: handlers.read_file(kw["path"], kw.get("limit")),
            "write_file": lambda **kw: handlers.write_file(kw["path"], kw["content"]),
            "edit_file":  lambda **kw: handlers.edit_file(kw["path"], kw["old_text"], kw["new_text"]),
            "grep":       lambda **kw: handlers.grep(**kw),
            "glob":       lambda **kw: handlers.glob_files(**kw),
            "ls":         lambda **kw: handlers.list_dir(**kw),
            "fuzzy_find": lambda **kw: handlers.fuzzy_find(**kw),
        }

    def register(self, name: str, handler: Callable) -> None:
        self._handlers[name] = handler

    def dispatch(self, name: str, arguments: str | dict) -> str:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                return f"Error: Invalid arguments JSON: {exc}"
        handler = self._handlers.get(name)
        if not handler:
            return f"Unknown tool: {name}"
        try:
            return str(handler(**arguments))
        except Exception as exc:
            return f"Error: {exc}"
