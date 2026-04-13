---
name: code-review
description: Systematic code review checklist for Python files
---

When reviewing Python code, follow this checklist:

1. **Correctness** — Does the logic match the intent? Are edge cases handled?
2. **Readability** — Is the code self-explanatory? Are names descriptive?
3. **Safety** — Are there injection risks, uncaught exceptions, or path escapes?
4. **Performance** — Are there obvious O(n²) loops or redundant I/O?
5. **Tests** — Is the code testable? Are critical paths covered?

Output format:
- Start with a one-sentence overall verdict.
- List issues as `[SEVERITY] file:line — description` (SEVERITY: ERROR | WARN | NOTE).
- End with concrete suggestions for the top issue.
