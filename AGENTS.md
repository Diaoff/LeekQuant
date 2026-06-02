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
| Backtest | Python-native BacktestRunner (A-share rules implemented in platform code) |
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

| Phase | Content | Status |
|---|---|---|
| M0 | Skeleton: Docker Compose, PostgreSQL, Redis, FastAPI, React shell | ✅ Done |
| M1 | Infra: PostgreSQL, data sources, K-line incremental pull | ✅ Done |
| M2 | Watchlists, strategy CRUD, Monaco Editor | ✅ Done |
| M3 | Strategy & backtest: Python-native async backtest, trade records | ✅ Done |
| M4 | 5-level signals, full sim trading engine (6 tables) | ✅ Done |
| M5 | Multi-factor scoring, IC/IR analysis | ✅ Done |
| M6a | HTTP snapshot realtime: EastMoney HTTP, Redis Pub/Sub, WebSocket subscribe, frontend | ✅ Done |
| M6b | WebSocket streaming: EastMoney WS push, task/signal WS channels, reconnect | Pending |
| M7 | Polish: auth, stock pools, factor expression engine, weekly/monthly MV, monitoring, docs | Pending |

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

## Implementation guidance

**功能实现可以先从 QuantDinger 寻找灵感。** 在开始任何功能模块的实现前：

1. **先读 QuantDinger**：浏览 `third_party/references/QuantDinger/` 中对应模块的源码，搞清楚"他们是怎么做的"
2. **再适配 Leek Quant**：根据本项目的架构约束（PostgreSQL 统一存储、纯A股、无云端）做裁剪和适配
3. **避免重复造轮子**：数据模型、API 路由、Celery 任务、WebSocket 推送、模拟交易闭环等核心逻辑，QuantDinger 已有成熟实现可直接参考

| 功能领域 | QuantDinger 参考路径 | 适配要点 |
|---|---|---|
| Docker Compose 全家桶 | `docker-compose.yml` | 移除非A股市场服务 |
| FastAPI 路由与依赖注入 | `backend_api_python/` | 保持 PostgreSQL + Celery 架构 |
| 前端组件结构 | `frontend/` | 复用 Vite + React + shadcn/ui 模式 |
| MCP Server | `mcp_server/` | 按需裁剪工具集 |
| Alembic 迁移 | `backend_api_python/alembic/` | 迁移文件可直接复用表结构逻辑 |

> 核心原则：**参考不是复制**。目标是理解设计思路后，用 Leek Quant 的技术栈（PostgreSQL / FastAPI / Celery / React）重新实现，而不是直接 fork 或导入 QuantDinger 代码。

## Chinese-market data caveats

- All data sources are free Chinese financial data APIs
- EastMoney WebSocket requires a self-built parser (no off-the-shelf library)
- Trading calendar must account for Chinese holidays (queried from DB, not hardcoded)
- Backtest uses the Python-native `BacktestRunner`; A-share rules are implemented in platform code.
