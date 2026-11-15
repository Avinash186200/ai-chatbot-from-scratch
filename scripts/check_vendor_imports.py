"""Fail if any Python module imports a vendor LLM SDK outside the adapter.

    python scripts/check_vendor_imports.py            # exit 1 and list offenders

The server talks to every LLM through ``llm_adapter.py`` over plain httpx.
Importing a vendor SDK anywhere else would tie that module to one provider
and add a dependency the tests cannot mock at the transport. The check
parses each file's AST, so strings and comments that mention a vendor do
not count, and both ``import x`` and ``from x import y`` (including
submodules such as ``x.types``) do.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

VENDOR_SDKS = frozenset({"anthropic", "openai"})
ALLOWED = {Path("server/src/analytics_assistant/llm_adapter.py")}
SCAN_ROOTS = ("server", "packages", "evals", "scripts")
SKIP_PARTS = {".venv", "node_modules", "__pycache__", ".git", "build", "dist"}


def vendor_imports(source: str) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            if name.split(".")[0] in VENDOR_SDKS:
                found.append((node.lineno, name))
    return found


def scan(root: Path) -> list[str]:
    offenders = []
    for base in SCAN_ROOTS:
        for path in sorted((root / base).rglob("*.py")):
            rel = path.relative_to(root)
            if SKIP_PARTS & set(rel.parts) or rel in ALLOWED:
                continue
            for line, name in vendor_imports(path.read_text(encoding="utf-8")):
                offenders.append(f"{rel.as_posix()}:{line}: imports {name}")
    return offenders


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    offenders = scan(root)
    for o in offenders:
        print(o)
    print(f"vendor-import check: {len(offenders)} offending import(s)"
          + ("" if offenders else " - OK"))
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main())
