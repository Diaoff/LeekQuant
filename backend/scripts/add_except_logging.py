"""Add logging to silent ``except`` handlers.

Policy:
- "Deliberate fallback" handlers (body is only ``pass``/``continue``/``break``, a
  single ``return``, or a single assign-to-default) -> ``logger.debug`` (low noise,
  still visible).
- Everything else (non-trivial swallowed work) -> ``logger.warning`` with ``exc_info``.

Read-only unless ``APPLY=1``. Uses ``ast`` for analysis and line-based rewriting
(insertions applied bottom-up so line numbers stay valid). Ensures a module-level
``logger = logging.getLogger(__name__)`` exists.
"""
from __future__ import annotations

import ast
import logging
import os
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
APPLY = os.environ.get("APPLY") == "1"


def is_trivial(handler: ast.ExceptHandler) -> bool:
    body = handler.body
    if not body:
        return True
    if all(isinstance(s, (ast.Pass, ast.Continue, ast.Break)) for s in body):
        return True
    if len(body) == 1 and isinstance(body[0], ast.Return):
        return True
    if len(body) == 1 and isinstance(body[0], ast.Assign):
        val = body[0].value
        if isinstance(val, (ast.Constant, ast.Name, ast.Attribute)):
            return True
    return False


def has_logger_module(tree: ast.Module) -> bool:
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "logger":
                    return True
    return False


def last_import_line(tree: ast.Module) -> int:
    last = 0
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            last = n.end_lineno or n.lineno
    return last


def needs_logging_call(handler: ast.ExceptHandler) -> bool:
    for n in ast.walk(handler):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id in {"log", "logger", "logging", "warnings", "print"}:
                return False
            if isinstance(f, ast.Attribute) and f.attr in {
                "warning", "error", "exception", "info", "debug", "critical", "warn",
                "format_exc", "print_exc", "capture_exception",
            }:
                return False
    for n in ast.walk(handler):
        if isinstance(n, ast.Raise):
            return False
    return True


def build_log_stmt(handler: ast.ExceptHandler, func: str, indent: int, trivial: bool) -> str:
    pad = " " * indent
    level = "debug" if trivial else "warning"
    if handler.name:
        msg = f'{pad}logger.{level}("silent except in {func} ({handler.name}): %s", {handler.name})'
        if not trivial:
            msg = f'{pad}logger.warning("silent except in {func} ({handler.name})", exc_info=True)'
    else:
        if trivial:
            msg = f'{pad}logger.debug("silent except in {func}")'
        else:
            msg = f'{pad}logger.warning("silent except in {func}", exc_info=True)'
    return msg


def enclosing_func(parents: list[ast.AST]) -> str:
    for p in reversed(parents):
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return p.name
    return "<module>"


def process_file(path: Path) -> tuple[int, str]:
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print(f"SKIP {path}: syntax error {e}")
        return 0, src
    lines = src.splitlines(keepends=True)
    insertions: list[tuple[int, str]] = []  # (line_index_to_insert_before, text)
    parents_stack: list[ast.AST] = []

    class Visitor(ast.NodeVisitor):
        def visit(self, node):  # type: ignore[override]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                parents_stack.append(node)
            if isinstance(node, ast.Try):
                for h in node.handlers:
                    if not needs_logging_call(h):
                        continue
                    if not h.body:
                        continue
                    first = h.body[0]
                    indent = first.col_offset
                    trivial = is_trivial(h)
                    func = enclosing_func(parents_stack)
                    stmt = build_log_stmt(h, func, indent, trivial)
                    # insert before the first body statement's line
                    insertions.append((first.lineno - 1, stmt + "\n"))
            self.generic_visit(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                parents_stack.pop()

    Visitor().visit(tree)
    if not insertions:
        return 0, src

    # module-level logger
    added_logger = False
    if not has_logger_module(tree):
        li = last_import_line(tree)
        insert_at = li if li > 0 else 0
        extra = 'import logging\n' if not any(
            isinstance(n, ast.Import) and any(a.name == "logging" for a in n.names)
            for n in tree.body
        ) else ""
        logger_line = f'{extra}logger = logging.getLogger(__name__)\n'
        insertions.append((insert_at, logger_line))
        added_logger = True

    # apply bottom-up
    insertions.sort(key=lambda x: x[0], reverse=True)
    for idx, text in insertions:
        lines.insert(idx, text)
    new_src = "".join(lines)
    return len(insertions) - (1 if added_logger else 0), new_src


def main() -> None:
    total = 0
    for path in sorted(APP.rglob("*.py")):
        n, new_src = process_file(path)
        if n == 0:
            continue
        total += n
        print(f"{path.relative_to(APP)}: +{n} log line(s)")
        if APPLY:
            path.write_text(new_src, encoding="utf-8")
    print(f"\nTOTAL handlers instrumented: {total}")
    if not APPLY:
        print("(dry-run) set APPLY=1 to write changes")


if __name__ == "__main__":
    main()
