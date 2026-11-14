#!/usr/bin/env python3
"""Source rules for briefing2, checked without importing the code (standard library only).

Usage:  python3 tools/check_source_rules.py [--root PATH_TO_V2]
Exit code 0 = no violations, 1 = violations (listed as path:line: message).

Two rules, both about keeping the agent honest and the core pure:

1. Purity of briefing2/core (the module map, not published here, section 1). The core is pure functions over frozen
   data: it must not import input/output, clock, randomness or shell modules, and must not call open(), print(),
   datetime.now() and friends. "Today" is an argument, never read.
2. No escape hatches anywhere in briefing2: no `# type: ignore`, `# noqa`, `# pragma: no cover`, `# fmt: off`,
   `# mypy: ignore-errors`. If the type checker or linter is wrong, the fix is a human decision, not a comment.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

# Module prefixes the core may not import. `datetime` and `urllib.parse` are fine (pure); `urllib.request` is not.
FORBIDDEN_IMPORTS = (
    "os",
    "sys",
    "io",
    "pathlib",
    "shutil",
    "tempfile",
    "glob",
    "subprocess",
    "socket",
    "ssl",
    "http",
    "urllib.request",
    "urllib.error",
    "ftplib",
    "smtplib",
    "sqlite3",
    "requests",
    "httpx",
    "aiohttp",
    "logging",
    "random",
    "secrets",
    "time",
    "uuid",
    "threading",
    "asyncio",
    "multiprocessing",
    "briefing2.shell",
)
FORBIDDEN_CALL_NAMES = {"open", "print", "input", "exec", "eval", "compile", "__import__", "breakpoint"}
FORBIDDEN_CALL_ATTRS = {
    "now",
    "utcnow",
    "today",
    "read_text",
    "write_text",
    "read_bytes",
    "write_bytes",
    "unlink",
    "mkdir",
    "rmdir",
}
ESCAPE_COMMENT = re.compile(
    r"#\s*(type:\s*ignore|noqa|ruff:\s*noqa|pragma:\s*no cover|fmt:\s*(off|skip)|mypy:\s*ignore-errors|pylint:)",
    re.I,
)


def _is_forbidden(module: str) -> str | None:
    for prefix in FORBIDDEN_IMPORTS:
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


def _core_violations(path: Path, tree: ast.AST) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if (hit := _is_forbidden(alias.name)) is not None:
                    out.append(f"{path}:{node.lineno}: core imports '{alias.name}' (forbidden: {hit})")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level > 0:
                if "shell" in module.split(".") or any(a.name == "shell" for a in node.names):
                    out.append(f"{path}:{node.lineno}: core imports the shell")
                continue
            if (hit := _is_forbidden(module)) is not None:
                out.append(f"{path}:{node.lineno}: core imports from '{module}' (forbidden: {hit})")
            for alias in node.names:
                if (hit := _is_forbidden(f"{module}.{alias.name}")) is not None and hit != _is_forbidden(module):
                    out.append(f"{path}:{node.lineno}: core imports '{module}.{alias.name}' (forbidden: {hit})")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALL_NAMES:
                out.append(f"{path}:{node.lineno}: core calls {func.id}()")
            elif isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_CALL_ATTRS:
                out.append(f"{path}:{node.lineno}: core calls .{func.attr}() (clock or file access)")
        elif isinstance(node, ast.Global):
            out.append(f"{path}:{node.lineno}: core uses 'global'")
    return out


def _escape_violations(path: Path, source: str) -> list[str]:
    out: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT and ESCAPE_COMMENT.search(tok.string):
            out.append(f"{path}:{tok.start[0]}: escape-hatch comment '{tok.string.strip()}'")
    return out


def scan(root: Path) -> list[str]:
    """Return every violation under root/briefing2 as 'path:line: message' strings."""
    package = root / "briefing2"
    violations: list[str] = []
    for path in sorted(package.rglob("*.py")):
        source = path.read_text()
        rel = path.relative_to(root)
        violations += _escape_violations(rel, source)
        if "core" in path.relative_to(package).parts[:-1]:
            violations += _core_violations(rel, ast.parse(source, filename=str(path)))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    violations = scan(args.root)
    for v in violations:
        print(v)
    print(f"source rules: {len(violations)} violation(s)")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
