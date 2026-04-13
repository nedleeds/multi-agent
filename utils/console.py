"""Rich console helpers for consistent I/O display."""

from rich.console import Console
from rich.panel import Panel
from rich.theme import Theme

_theme = Theme({
    "prompt":      "bold cyan",
    "tool.name":   "bold yellow",
    "tool.output": "dim white",
    "assistant":   "bright_green",
    "plan":        "bold magenta",
    "info":        "dim blue",
    "error":       "bold red",
})

console = Console(theme=_theme)


def print_user_prompt(label: str = ">> ") -> str:
    """Render a styled prompt and return the user's input."""
    return console.input(f"[prompt]{label}[/prompt] ")


def print_tool_call(name: str, output: str, max_preview: int = 200) -> None:
    preview = output[:max_preview] + ("…" if len(output) > max_preview else "")
    console.print(f"[tool.name]▶ {name}[/tool.name]  [tool.output]{preview}[/tool.output]")


def print_assistant(text: str) -> None:
    console.print(Panel(text, border_style="green", expand=False))


def print_plan(plan_text: str) -> None:
    console.print(Panel(plan_text, title="Plan", border_style="magenta", expand=False))


def print_info(message: str) -> None:
    console.print(f"[info]{message}[/info]")


def print_error(message: str) -> None:
    console.print(f"[error]Error:[/error] {message}")
