"""SkillRegistry: lazy skill loading from SKILL.md files.

Implements the s05 pattern:
  1. Put a cheap skill catalog in the system prompt (describe_available).
  2. Load the full skill body only when the model calls load_skill(name).

SKILL.md format:
  ---
  name: my-skill
  description: One-line summary shown in the catalog
  ---
  Full skill instructions here…
"""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class _SkillDoc:
    name: str
    description: str
    body: str


class SkillRegistry:
    def __init__(self, skills_dir: Path):
        self._skills: dict[str, _SkillDoc] = {}
        if skills_dir.exists():
            self._load_all(skills_dir)

    def _load_all(self, skills_dir: Path) -> None:
        for path in sorted(skills_dir.rglob("SKILL.md")):
            meta, body = self._parse_frontmatter(path.read_text(encoding="utf-8"))
            name = meta.get("name", path.parent.name)
            self._skills[name] = _SkillDoc(
                name=name,
                description=meta.get("description", "No description"),
                body=body.strip(),
            )

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple[dict, str]:
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text
        meta: dict = {}
        for line in match.group(1).strip().splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
        return meta, match.group(2)

    def catalog(self) -> str:
        if not self._skills:
            return "(no skills available)"
        return "\n".join(f"- {s.name}: {s.description}" for s in self._skills.values())

    def load(self, name: str) -> str:
        skill = self._skills.get(name)
        if not skill:
            known = ", ".join(sorted(self._skills)) or "(none)"
            return f"Error: Unknown skill '{name}'. Available: {known}"
        return f'<skill name="{skill.name}">\n{skill.body}\n</skill>'
