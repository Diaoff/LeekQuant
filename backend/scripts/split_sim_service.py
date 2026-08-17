"""Split sim/service.py into focused submodules (leaf-first, thin facade).

Pure AST line-slice move: function bodies are copied verbatim into submodules,
each submodule carries the original import header + needed cross-imports, and
service.py becomes a thin re-export facade so all existing callers keep working.

Run from backend/. Git-backed: revert with `git checkout -- app/sim` if needed.
"""
from __future__ import annotations

import ast
from pathlib import Path

SIM = Path(__file__).resolve().parent.parent / "app" / "sim"
SRC = SIM / "service.py"
APPLY = __import__("os").environ.get("APPLY") == "1"

GROUPS = {
    "_helpers": [
        "SignalOrderRequest", "_fee_config", "_global_fee_config",
        "_get_trade_calendar", "_get_kline", "_get_latest_kline_before_or_on",
        "_get_position",
    ],
    "accounts": [
        "get_account_or_404", "list_accounts", "create_account", "update_account",
        "delete_account", "list_child_rows",
    ],
    "nav": [
        "refresh_account_assets", "refresh_position_market_values",
        "check_stop_conditions", "unlock_t1_positions",
    ],
    "orders": [
        "_resolve_match_price", "_resolve_order_price_fallback", "_limit_rate",
        "_computed_limit_flags", "_insert_signal", "_strategy_signal_response",
        "generate_order_from_signal", "match_order", "cancel_order",
    ],
    "valuation": [
        "_realtime_ticks", "_apply_realtime_position_values", "_position_quote_codes",
        "_position_today_baselines", "_apply_position_today_pnl", "_latest_nav_total_asset",
        "_position_rows", "_account_positions", "enrich_account_with_realtime_valuation",
        "list_accounts_with_realtime_valuation", "get_account_with_realtime_valuation",
        "list_positions_with_realtime_valuation",
    ],
}

CROSS_IMPORTS = {
    "_helpers": [],
    "accounts": [],
    "nav": ["from app.sim._helpers import _get_trade_calendar"],
    "orders": [
        "from app.sim._helpers import (\n"
        "    _fee_config, _global_fee_config, _get_kline,\n"
        "    _get_latest_kline_before_or_on, _get_position, _get_trade_calendar,\n)",
        "from app.sim.accounts import get_account_or_404",
        "from app.sim.nav import refresh_account_assets",
    ],
    "valuation": ["from app.sim.accounts import get_account_or_404, list_accounts"],
}

DOCS = {
    "_helpers": '"""Leaf helpers for simulation: fee config + kline/trade-calendar data access."""',
    "accounts": '"""Simulation account CRUD."""',
    "nav": '"""Daily NAV refresh, T+1 unlock, stop-condition checks, position market values."""',
    "orders": '"""Signal -> order generation, order matching, cancel."""',
    "valuation": '"""Realtime valuation enrichment for accounts and positions."""',
}


def main() -> None:
    src = SRC.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)

    # collect def line ranges
    ranges: dict[str, tuple[int, int]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ranges[node.name] = (node.lineno, node.end_lineno or node.lineno)

    # original import header (lines 2..33, 1-indexed -> index 1..32) keeps
    # `from __future__`, third-party/local imports, logger, and module constants.
    header_block = "".join(lines[1:33])

    new_files: dict[str, str] = {}
    all_moved: list[str] = []
    for mod, names in GROUPS.items():
        chunks = [DOCS[mod], "", header_block.rstrip("\n"), ""]
        if CROSS_IMPORTS[mod]:
            chunks.append("\n".join(CROSS_IMPORTS[mod]))
            chunks.append("")
        for n in names:
            s, e = ranges[n]
            chunks.append("".join(lines[s - 1 : e]).rstrip("\n"))
            chunks.append("")
        new_files[mod] = "\n".join(chunks).rstrip("\n") + "\n"
        all_moved.extend(names)

    # rebuild service.py as a thin facade: keep header, re-export everything
    facade = header_block.rstrip("\n") + "\n\n"
    for mod, names in GROUPS.items():
        facade += f"from app.sim.{mod} import (\n"
        for n in names:
            facade += f"    {n},\n"
        facade += ")\n\n"
    facade = facade.rstrip("\n") + "\n"

    if not APPLY:
        print("--- service.py would become a facade re-exporting these ---")
        print(facade[:600], "...")
        for mod, content in new_files.items():
            print(f"\n=== {mod}.py ({len(content.splitlines())} lines) ===")
            print("\n".join(content.splitlines()[:6]))
        print(f"\nTotal moved defs: {len(all_moved)}")
        return

    for mod, content in new_files.items():
        (SIM / f"{mod}.py").write_text(content, encoding="utf-8")
    SRC.write_text(facade, encoding="utf-8")
    print("WROTE:", ", ".join(f"{m}.py" for m in new_files), "+ service.py facade")


if __name__ == "__main__":
    main()
