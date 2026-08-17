"""Analyze a module: list top-level defs (with line ranges) and which other
top-level names each def calls. Helps plan a safe leaf-first split."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

path = Path(sys.argv[1])
src = path.read_text(encoding="utf-8")
tree = ast.parse(src)

# collect top-level defs with their [start, end) line ranges
defs = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        defs.append((node.name, node.lineno, node.end_lineno or node.lineno))

# resolve name -> def index
name_to_def = {n: i for i, (n, _, _) in enumerate(defs)}

# for each def, find called top-level names
called: dict[str, set[str]] = {}
for node in tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        continue
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id in name_to_def:
                names.add(f.id)
    called[node.name] = names

print(f"=== {path.name}: {len(defs)} top-level defs ===")
for name, start, end in defs:
    deps = sorted(called[name])
    print(f"{name:42s} L{start:4d}-{end:<4d}  calls: {', '.join(deps) if deps else '-'}")
