# Leek Quant — AGENTS.md

## Current state

Pre-implementation. No source code, no manifests, no build config at root level. All decisions are in design documents under `docs/`. The skeleton will be built from scratch.

## Project identity

- **Leek Quant** = pure A-share quant platform, cut down from QuantDinger (removed non-A-share markets, cross-asset trading, cloud hosting)
- **Local-first, privacy-first**: data/strategies on user's machine, Docker Compose one-click deploy
- **Reference project**: `third_party/references/QuantDinger/` — real Dockerfiles, CI, docker-compose, project structure to adapt

## Architecture from design docs

| Layer | Choice |
|---|---|
| Frontend | React 18 + Vite + TypeScript + Tailwind CSS + shadcn/ui |
| Charts | TradingView Lightweight Charts |
| Editor | Monaco Editor (Python + MyTT autocomplete) |
| State | Zustand |
| Backend | FastAPI (Python 3.11+) |
| ORM | SQLAlchemy 2.0 (async) + Alembic |
| DB | **PostgreSQL 15+** (single unified store — not DuckDB+Parquet+SQLite) |
| Queue | Celery + Redis |
| Real-time | Redis Pub/Sub → FastAPI WebSocket |
| Backtest | Hikyuu (C++ kernel, Python bindings, A-share rules built-in) |
| Indicators | MyTT (Tongdaxin/Tonghuashun compatible, single-file zero-dep) |
| Historical data | AData (Tier 1) → Baostock (Tier 2) → AkShare (Tier 3) fallback |
| Real-time data | EastMoney WebSocket (self-built parser) |
| Factors | Qlib-like expression paradigm (lightweight, stored in PostgreSQL) |

## Design documents

All 9 design docs are in `docs/`. The two most comprehensive:
- `docs/finally-design.md` (1769 lines) — complete architecture, DB schema, API design, all subsystems
- `docs/workbuddy-design.md` (1676 lines) — synthesised from all 6 AI drafts, includes Docker Compose config and milestones

## Key design decisions (from docs)

- **Unified PostgreSQL** for everything (market data, factors, sim trading, system state) — no separate DuckDB/Parquet/SQLite stores
- **3-tier data fallback**: AData → Baostock → AkShare, with incremental pull and `data_update_state` table tracking
- **5-level signals**: Buy / Add / Reduce / Sell / Wait, with a state machine mapping to actual operations
- **Sim trading**: Full 6-table closed loop (`sim_accounts`, `sim_positions`, `sim_orders`, `sim_trades`, `sim_cash_flow`, `sim_daily_nav`) with T+1, price limits, fees
- **Multi-factor scoring**: Valuation / Growth / Quality / Momentum, with IC/IR analysis
- **Daily K-line partitioned by year** in PostgreSQL
- **Trade calendar** in DB (all date logic queries the DB, no hardcoded holiday lists)

## Vendored dependencies

- `third_party/libs/MyTT.py` (v3.3, 287 lines) — Tongdaxin/Tonghuashun compatible technical indicators. Single-file, depends only on numpy/pandas. Will be integrated into strategy editing and signal generation.

## Development milestones (from docs)

| Phase | Content | ~Duration |
|---|---|---|
| M1 | Infra: PostgreSQL, data sources, K-line incremental pull | 1 week |
| M2 | Stock pools, watchlists, strategy CRUD, Monaco Editor, Hikyuu backtest | 2 weeks |
| M3 | 5-level signals, full sim trading engine (6 tables) | 2 weeks |
| M4 | Multi-factor scoring, IC/IR analysis, real-time WebSocket push | 2 weeks |
| M5 | NAV curves, parameter sensitivity, multi-account, docs | 1 week |

## Builds and tooling

None configured yet. When adding tooling, follow QuantDinger conventions (see `third_party/references/QuantDinger/`) for:
- Docker Compose layout (postgres, redis, backend, frontend services)
- CI workflows (basic-ci, docker-publish, update-frontend)
- Python project structure (backend_api_python, mcp_server packages)
- Dockerfiles (python:3.12-slim, nginx:1.25-alpine)

## Reference: QuantDinger structure

`third_party/references/QuantDinger/` is a full working project (v3.0.6) with:
- `docker-compose.yml` — 4 services (postgres, redis, backend, frontend)
- `backend_api_python/` — FastAPI backend
- `frontend/` — React SPA (prebuilt dist + nginx)
- `mcp_server/` — MCP protocol server (pyproject.toml deps: mcp>=1.2.0, httpx>=0.27.0)
- `.github/workflows/` — basic-ci, docker-publish, update-frontend

## Chinese-market data caveats

- All data sources are free Chinese financial data APIs
- EastMoney WebSocket requires a self-built parser (no off-the-shelf library)
- Trading calendar must account for Chinese holidays (queried from DB, not hardcoded)
- Hikyuu has native A-share trading rules (T+1, price limits, stamp tax)
