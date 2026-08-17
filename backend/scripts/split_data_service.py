"""Split data/service.py: move sampling/quality/window defs into submodules
via line-range deletion (robust, verbatim), service.py keeps sync orchestration
and re-exports the moved names for back-compat.

Git-backed; revert with `git checkout -- app/data` if needed.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "app" / "data"
SRC = DATA / "service.py"
APPLY = os.environ.get("APPLY") == "1"

src = SRC.read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)
tree = ast.parse(src)

GROUP = {
    "sample": [
        "default_kline_window", "_row_value", "_sample_bucket",
        "_balanced_sample_stock_codes", "select_sample_stock_codes",
        "select_all_stock_codes", "get_data_status",
    ],
    "quality": ["_daily_kline_quality_issues", "_create_kline_quality_alert", "_bulk_load_is_st"],
    "window": [
        "infer_incremental_kline_window", "infer_incremental_kline_ranges",
        "split_kline_ranges_by_year", "infer_full_kline_ranges",
    ],
}
CROSS = {
    "sample": [],
    "quality": [],
    "window": ["from app.data.sample import default_kline_window"],
}
DOCS = {
    "sample": '"""Stock sampling helpers for kline sync."""',
    "quality": '"""Daily kline data-quality checks and alert creation."""',
    "window": '"""Incremental / full kline sync window inference."""',
}

# Map name -> (start, end) inclusive 1-indexed
ranges: dict[str, tuple[int, int]] = {}
for n in tree.body:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        ranges[n.name] = (n.lineno, n.end_lineno or n.lineno)

# import header + module constants (shared by submodules)
import_nodes = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
logger_node = next((n for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "logger" for t in n.targets)), None)
h_start = min(n.lineno for n in import_nodes)
h_end = max((n.end_lineno or n.lineno) for n in import_nodes)
if logger_node:
    h_end = max(h_end, logger_node.end_lineno or logger_node.lineno)
HEADER = "".join(lines[h_start - 1 : h_end])
first_def_line = min(
    (n.lineno for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))),
    default=h_end + 1,
)
const_nodes = [n for n in tree.body if isinstance(n, ast.Assign) and h_end < n.lineno < first_def_line]
CONSTANTS = "\n".join("".join(lines[n.lineno - 1 : n.end_lineno]).rstrip("\n") for n in const_nodes)

# submodules content (verbatim slices + shared header/constants)
sub_contents: dict[str, str] = {}
for mod, names in GROUP.items():
    chunks = [DOCS[mod], "", HEADER.rstrip("\n"), "", CONSTANTS.rstrip("\n"), ""]
    if CROSS[mod]:
        chunks.append("\n".join(CROSS[mod]))
        chunks.append("")
    for nm in names:
        s, e = ranges[nm]
        chunks.append("".join(lines[s - 1 : e]).rstrip("\n"))
        chunks.append("")
    sub_contents[f"{mod}.py"] = "\n".join(chunks).rstrip("\n") + "\n"

# deletion-based rebuild of service.py
work = list(lines)
delete_ranges = sorted(
    (ranges[nm] for names in GROUP.values() for nm in names),
    reverse=True,
)
for s, e in delete_ranges:
    del work[s - 1 : e]  # removes original lines s..e inclusive

# insert re-export imports right before the first remaining top-level def
reexport_lines = []
for mod, names in GROUP.items():
    reexport_lines.append(f"from app.data.{mod} import (")
    for nm in names:
        reexport_lines.append(f"    {nm},")
    reexport_lines.append(")")
    reexport_lines.append("")

first_def_idx = next(
    i for i, ln in enumerate(work)
    if ln.startswith("def ") or ln.startswith("async def ")
)
# ensure blank line before insertion
if work[first_def_idx - 1].strip():
    work.insert(first_def_idx, "\n")
    first_def_idx += 1
work[first_def_idx:first_def_idx] = [l + "\n" for l in reexport_lines] + ["\n"]
service_content = "".join(work)

contents = dict(sub_contents)
contents["service.py"] = service_content

if not APPLY:
    for m in GROUP:
        print(f"{m}.py: {len(sub_contents[f'{m}.py'].splitlines())} lines")
    print("service.py (sync facade):", len(service_content.splitlines()), "lines")
    print("first 6 lines of service.py:")
    print("\n".join(service_content.splitlines()[:6]))
    raise SystemExit

# Validate in /tmp; only write to app/ if everything compiles.
tmp = Path(tempfile.mkdtemp())
try:
    dstdir = tmp / "data"
    dstdir.mkdir()
    for name, text in contents.items():
        (dstdir / name).write_text(text, encoding="utf-8")
    for p in DATA.glob("*.py"):
        if p.name not in contents:
            shutil.copy(p, dstdir / p.name)
    res = subprocess.run(
        [".venv/bin/python", "-m", "py_compile", *sorted(str(x) for x in dstdir.glob("*.py"))],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        print("COMPILE FAILED in /tmp:\n", res.stderr)
        import re
        mm = re.search(r"service\.py\", line (\d+)", res.stderr)
        if mm:
            ln = int(mm.group(1))
            bad = (dstdir / "service.py").read_text(encoding="utf-8").splitlines()
            for i in range(max(0, ln - 6), min(len(bad), ln + 2)):
                print(f"{i+1:4d}: {bad[i]}")
        raise SystemExit(1)
    for name, text in contents.items():
        (DATA / name).write_text(text, encoding="utf-8")
    print("WROTE sample.py, quality.py, window.py + service.py (sync facade)")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
