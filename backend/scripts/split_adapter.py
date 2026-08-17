"""Split backtest/adapter.py via AST node ranges (robust, verbatim).

dataclasses -> models.py ; BacktestRunner class -> engine.py ;
adapter.py becomes a thin re-export facade.
Git-backed; revert with `git checkout -- app/backtest` if needed.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

BT = Path(__file__).resolve().parent.parent / "app" / "backtest"
SRC = BT / "adapter.py"
APPLY = os.environ.get("APPLY") == "1"

src = SRC.read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)
tree = ast.parse(src)

MODELS_NAMES = [
    "KBar", "Position", "TradeRecord", "_LotEntry", "_ClosedLot",
    "SellDirection", "BacktestConfig", "_SignalCandidate",
    "BacktestContext", "ScriptContext",
]

# header = all module-level imports + the `logger = ...` assignment
header_nodes = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
logger_node = next((n for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "logger" for t in n.targets)), None)
h_start = min(n.lineno for n in header_nodes)
h_end = max((n.end_lineno or n.lineno) for n in header_nodes)
if logger_node:
    h_end = max(h_end, logger_node.end_lineno or logger_node.lineno)
HEADER = "".join(lines[h_start - 1 : h_end])


def node_source(node: ast.AST) -> str:
    return "".join(lines[node.lineno - 1 : node.end_lineno])


models_defs, engine_defs = [], []
for n in tree.body:
    if isinstance(n, ast.ClassDef):
        if n.name in MODELS_NAMES:
            models_defs.append(node_source(n))
        elif n.name == "BacktestRunner":
            engine_defs.append(node_source(n))

assert models_defs and engine_defs, "extraction failed"

models_py = (
    '"""Backtest engine data models (dataclasses)."""\n\n'
    + HEADER.rstrip("\n") + "\n\n"
    + "\n\n".join(d.rstrip("\n") for d in models_defs) + "\n"
)
engine_py = (
    '"""Backtest engine: BacktestRunner main loop."""\n\n'
    + HEADER.rstrip("\n") + "\n\n"
    + "from app.backtest.models import (\n"
    + "".join(f"    {n},\n" for n in MODELS_NAMES)
    + ")\n\n"
    + "\n\n".join(d.rstrip("\n") for d in engine_defs) + "\n"
)
facade = (
    HEADER.rstrip("\n") + "\n\n"
    "from app.backtest.models import (\n"
    + "".join(f"    {n},\n" for n in MODELS_NAMES)
    + ")\n"
    "from app.backtest.engine import BacktestRunner\n"
)

if not APPLY:
    print("models.py:", len(models_py.splitlines()), "lines; engine.py:", len(engine_py.splitlines()), "lines")
    print("facade:\n", facade)
    raise SystemExit

(BT / "models.py").write_text(models_py, encoding="utf-8")
(BT / "engine.py").write_text(engine_py, encoding="utf-8")
SRC.write_text(facade, encoding="utf-8")
print("WROTE models.py, engine.py, adapter.py facade")
