"""Audit: list ``except`` handlers whose body swallows the exception silently
(i.e. contains no logging / warning / print / raise / warnings call).

Read-only. Prints file:line, enclosing function, and the handler's body summary.
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

LOGGING_CALLS = {
    "log", "logger", "logging", "warnings", "print", "traceback",
    "capture_exception", "capture_exc",
}


def calls_log(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id in LOGGING_CALLS:
                return True
            if isinstance(f, ast.Attribute):
                if f.attr in {"warning", "error", "exception", "info", "debug", "critical", "warn"}:
                    return True
                if f.attr in {"format_exc", "print_exc", "capture_exception"}:
                    return True
    return False


def has_raise(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Raise) for n in ast.walk(node))


def func_name(parents: list[ast.AST]) -> str:
    for p in reversed(parents):
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return p.name
    return "<module>"


def main() -> None:
    count = 0
    for path in sorted(APP.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            print(f"SYNTAX ERROR {path}: {e}")
            continue
        parents_stack: list[ast.AST] = []

        class Visitor(ast.NodeVisitor):
            def visit(self, node):  # type: ignore[override]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    parents_stack.append(node)
                if isinstance(node, ast.Try):
                    for h in node.handlers:
                        if h.type is None or True:  # all handlers
                            if not calls_log(h) and not has_raise(h):
                                nonlocal count
                                count += 1
                                body0 = h.body[0] if h.body else None
                                first = ""
                                if isinstance(body0, ast.Pass):
                                    first = "pass"
                                elif isinstance(body0, (ast.Continue, ast.Break)):
                                    first = type(body0).__name__
                                elif isinstance(body0, ast.Return):
                                    first = "return"
                                else:
                                    first = f"{type(body0).__name__}..."
                                name = h.name or ""
                                print(f"{path.relative_to(APP)}:{h.lineno} func={func_name(parents_stack)} except={name or 'Exception'} -> {first}")
                self.generic_visit(node)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    parents_stack.pop()

        Visitor().visit(tree)
    print(f"\nTOTAL silent except handlers: {count}")


if __name__ == "__main__":
    main()
