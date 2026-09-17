"""Durable skill library: one .py file per skill on the host filesystem.

Host-side rather than on the brick's SD card, so skills survive reflashes and
stay reviewable/version-controlled. The brick stays stateless.
"""

from __future__ import annotations

import ast
from pathlib import Path


class SkillError(RuntimeError):
    pass


def _validate_name(name: str) -> str:
    cleaned = (name or "").strip()
    # Identifier-only names double as the invoked function name and make path
    # traversal impossible.
    if not cleaned.isidentifier() or cleaned.startswith("_"):
        raise SkillError(
            f"Invalid skill name {name!r}: use a Python identifier not starting with '_'"
        )
    return cleaned


class SkillStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.directory / f"{_validate_name(name)}.py"

    def exists(self, name: str) -> bool:
        return self._path(name).is_file()

    def save(self, name: str, source: str, description: str = "", *, overwrite: bool = False) -> Path:
        name = _validate_name(name)
        path = self._path(name)
        if path.exists() and not overwrite:
            raise SkillError(
                f"Skill {name!r} already exists. Call get_skill first, then define_skill "
                "with overwrite=true, or choose another name."
            )

        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise SkillError(f"Skill {name!r} has a syntax error: {exc}") from None

        defines_entrypoint = any(
            isinstance(node, ast.FunctionDef) and node.name == name for node in tree.body
        )
        if not defines_entrypoint:
            raise SkillError(
                f"Skill {name!r} must define a function called {name!r} as its entry point."
            )

        # repr() gives a correctly escaped literal, which is a valid docstring.
        body = source if ast.get_docstring(tree) else f"{description.strip()!r}\n\n{source}"
        path.write_text(body, encoding="utf-8")
        return path

    def get(self, name: str) -> str:
        path = self._path(name)
        if not path.is_file():
            raise SkillError(f"No skill named {name!r}")
        return path.read_text(encoding="utf-8")

    def delete(self, name: str) -> None:
        path = self._path(name)
        if not path.is_file():
            raise SkillError(f"No skill named {name!r}")
        path.unlink()

    def describe(self, name: str) -> dict[str, str]:
        source = self.get(name)
        try:
            doc = ast.get_docstring(ast.parse(source)) or ""
        except SyntaxError:
            doc = ""
        return {"name": name, "description": doc.strip()}

    def names(self) -> list[str]:
        return sorted(p.stem for p in self.directory.glob("*.py") if not p.stem.startswith("_"))

    def list(self) -> list[dict[str, str]]:
        return [self.describe(name) for name in self.names()]

    def combined_source(self) -> str:
        """Every skill concatenated, so skills can call each other for free."""
        parts = []
        for name in self.names():
            parts.append(f"# --- skill: {name} ---\n{self.get(name)}")
        return "\n\n".join(parts)
