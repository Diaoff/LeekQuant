# Leek Quant Code Wiki

> 纯 A 股本地优先量化研究与模拟交易平台。本文档基于仓库代码（2026-08）自动分析生成，覆盖整体架构、模块职责、关键类与函数、依赖关系与运行方式。

---

## 目录

1. [项目概览](#1-项目概览)
2. [整体架构](#2-整体架构)
3. [目录结构](#3-目录结构)
4. [后端架构](#4-后端架构)
   - 4.1 [应用入口与生命周期](#41-应用入口与生命周期)
   - 4.2 [配置系统](#42-配置系统)
   - 4.3 [中间件与可观测性](#43-中间件与可观测性)
   - 4.4 [API 路由清单](#44-api-路由清单)
   - 4.5 [数据层（三级回退）](#45-数据层三级回退)
   - 4.6 [回测引擎](#46-回测引擎)
   - 4.7 [模拟交易引擎](#47-模拟交易引擎)
   - 4.8 [实时行情子系统](#48-实时行情子系统)
   - 4.9 [跷跷板（Seesaw）子系统](#49-跷跷板seesaw子系统)
   - 4.10 [Celery 任务系统](#410-celery-任务系统)
5. [前端架构](#5-前端架构)
6. [数据库 Schema](#6-数据库-schema)
7. [依赖关系](#7-依赖关系)
8. [运行方式](#8-运行方式)
9. [关键设计约定](#9-关键设计约定)

---

## 1. 项目概览

**Leek Quant（韭菜量化）** 是一个纯 A 股、本地优先、隐私优先的量化研究与模拟交易平台，从 QuantDinger（`third_party/references/QuantDinger/`）精简适配而来——移除非 A 股市场、跨资产交易与云托管。

| 层 | 技术选型 |
|---|---|
| 前端 | React 18 + Vite + TypeScript + Tailwind CSS + shadcn/ui |
| 图表 | TradingView Lightweight Charts |
| 编辑器 | Monaco Editor（Python + MyTT 补全） |
| 后端 | FastAPI（Python 3.11+，实际镜像 3.12） |
| 数据库 | PostgreSQL 15+（统一存储，无 ORM，raw SQL + dataclass） |
| ORM/迁移 | SQLAlchemy 2.0 async（仅作引擎/会话）+ Alembic（原生 DDL） |
| 队列 | Celery + Redis（4 队列：default / data / backtest / trading） |
| 实时通信 | 东方财富 WebSocket 自建解析器 → Redis Pub/Sub → FastAPI WebSocket |
| 回测 | Python 原生 `BacktestRunner`（A 股规则在平台代码中实现） |
| 指标库 | MyTT v3.3（`backend/app/libs/MyTT.py`，通达信/同花顺兼容） |
| 历史数据 | EastMoney HTTP / Tencent HTTP / Mootdx / AData → Baostock → AkShare 多级回退 |
| 部署 | Docker Compose（9 服务）或本地脚本 `restart.sh` |

### 里程碑状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 骨架：Compose、PostgreSQL、Redis、FastAPI、React 外壳 | ✅ |
| M1 | 基础设施：数据源、K 线增量拉取 | ✅ |
| M2 | 自选股、策略 CRUD、Monaco 编辑器 | ✅ |
| M3 | 策略与回测：Python 原生异步回测、交易记录 | ✅ |
| M4 | 五档信号、完整模拟交易引擎（6 表闭环） | ✅ |
| M5 | 多因子打分 | ⚠️ 已废弃（代码已移除，迁移残留表仍在库中） |
| M6a | HTTP 快照实时行情 | ✅ |
| M6b | 东方财富 WS 推流、任务/信号 WS 通道、断线重连 | ✅ |
| M7 | 认证、周/月线、监控、文档 | 待处理 |

---

## 2. 整体架构

### 2.1 服务拓扑（docker-compose.yml，9 个服务）

```
                        ┌──────────────────────────────────────────┐
                        │              frontend (nginx:80→8080)     │
                        │   React SPA 静态资源 + /health + SPA 回退  │
                        └───────────────┬──────────────────────────┘
                                        │ HTTP (VITE_API_BASE_URL)
                        ┌───────────────▼──────────────────────────┐
                        │        backend (uvicorn :8000)            │
                        │  FastAPI REST + /ws/realtime WebSocket    │
                        │  健康检查: /api/health (DB+Redis 并行探测) │
                        └───────┬───────────────────────┬──────────┘
                                │                       │
              ┌─────────────────▼───────┐      ┌────────▼─────────────────────┐
              │  postgres:15 (:5432)    │      │  redis:7 (:6379)              │
              │  统一存储全部业务数据     │      │  Celery broker/result          │
              │  daily_kline 按年分区    │      │  实时行情 Pub/Sub 总线          │
              └────────▲────────────────┘      │  任务事件通道 celery:task_events│
                       │                       └────────▲─────────────────────┘
       ┌───────────────┼────────────────────────────────┼──────────────────────┐
       │               │                                │                      │
┌──────┴─────┐  ┌──────┴──────────────┐  ┌──────────────┴───────┐  ┌───────────┴────────┐
│ celery_    │  │ celery_backtest_    │  │ celery_beat          │  │ realtime_ws        │
│ worker     │  │ worker              │  │ 定时调度 (Asia/Shanghai)│  │ 东财 WS 拉取→       │
│ data/trading│ │ backtest 队列        │  │ K线同步/信号/T+1解锁   │  │ Redis 总线发布      │
└────────────┘  └─────────────────────┘  └──────────────────────┘  └────────────────────┘
                                                                     ┌────────────────────┐
                                                                     │ realtime_risk_guard│
                                                                     │ 实时风控: 盘中tick  │
                                                                     │ 止损/止盈检查       │
                                                                     └────────────────────┘
```

### 2.2 核心数据流

**历史数据流（拉取）**
```
Celery Beat 定时 → data_tasks.kline_sync_dispatch
  → data/service.py: sync_kline（DB 队列 kline_sync_jobs/kline_sync_items 派发）
  → data/fetcher.py: fetch_with_fallback（按优先级依次尝试 provider）
  → data/providers.py（EastMoney/Tencent/Mootdx/AData/Baostock/AkShare）
  → data/validators.py 校验 → repository/kline.py upsert_daily_kline（ON CONFLICT 幂等）
  → PostgreSQL daily_kline（按年分区）
```

**实时行情流（推送）**
```
东方财富 WS ── realtime/eastmoney_ws.py 解析 ──▶ realtime/ws_producer.py
    ──▶ RealtimeTick ──▶ realtime/bus.py RedisRealtimeBus（Pub/Sub，可回放）
    ──▶ backend /ws/realtime ──▶ 前端 useRealtimeTicks（断线重连 + replay_from 补拉）
    └─▶ realtime/risk_guard.py（盘中风控：止损/止盈/预警）
```

**回测/信号/模拟盘流**
```
策略源码(strategies 表) ──▶ backtest/strategy_runtime.py（受限沙箱执行 + 超时/内存限制）
  ──▶ backtest/adapter.py BacktestRunner（K线缓存 + 逐日事件驱动）
  ──▶ backtest/signals.py（五档信号 + A股规则过滤：T+1/涨跌停/停牌）
  ──▶ backtest/cost.py 费用模型 ──▶ backtest_results / backtest_trades / backtest_closed_lots
信号(signal_log) ──▶ sim/orders.py generate_order_from_signal ──▶ match_order 撮合
  ──▶ sim_positions / sim_trades / sim_cash_flow ──▶ sim/nav.py 每日净值快照
```

---

## 3. 目录结构

```
leek-quant/
├── docker-compose.yml          # 9 服务编排
├── restart.sh / stop.sh        # 本地（非 Docker）启停脚本
├── .env.example                # 环境变量模板
├── strategies/                 # 示例策略（vatp_trend_pullback.py）
├── docs/                       # 设计文档与审计报告
│   ├── finally-design.md       # 最全面设计文档（架构/schema/API）
│   ├── architecture.md         # 架构文档
│   └── code-wiki.md            # 本文档
├── backend/
│   ├── Dockerfile              # 多阶段构建，启动时先跑 alembic upgrade head
│   ├── requirements.txt
│   ├── alembic/                # 迁移（原生 SQL DDL）
│   ├── app/
│   │   ├── main.py             # FastAPI 入口
│   │   ├── api/                # 14 个路由模块
│   │   ├── core/               # 配置/中间件/日志/异步运行时/Celery健康
│   │   ├── data/               # 数据域：providers/fetcher/service/repository
│   │   ├── backtest/           # 回测域：adapter/runtime/signals/cost/rebalance
│   │   ├── sim/                # 模拟交易域：accounts/orders/nav/valuation
│   │   ├── realtime/           # 实时行情域：bus/eastmoney_ws/risk_guard
│   │   ├── tasks/              # Celery 任务与 Beat 调度
│   │   ├── preferences/        # 用户偏好（费率/同步并发）
│   │   ├── libs/MyTT.py        # 技术指标库
│   │   └── db/session.py       # asyncpg 引擎与会话
│   └── tests/                  # 70+ pytest 测试
├── frontend/
│   ├── Dockerfile + nginx.conf # 构建产物 + SPA 托管
│   ├── src/
│   │   ├── App.tsx / main.tsx  # 路由（React.lazy 按需加载）
│   │   ├── components/         # Layout / BacktestRunModal / Skeleton
│   │   ├── pages/              # 10 个页面
│   │   ├── hooks/              # WebSocket 系列 hooks
│   │   └── lib/                # utils / mytt-completions / theme
│   └── tests/smoke/            # Playwright 冒烟测试
└── third_party/
    ├── libs/MyTT.py
    └── references/QuantDinger/ # 参考项目（只读）
```

---

## 4. 后端架构

### 4.1 应用入口与生命周期

**文件：[backend/app/main.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/main.py)**

- `lifespan()`（L37-52）：启动时从数据库 `data_source_config` 表加载数据源优先级配置（`apply_config_from_db`）；关闭时释放 asyncpg 引擎与实时总线 Redis 连接。
- 中间件注册（L64-74，顺序敏感）：`RequestIDMiddleware`（最外层，注入/回显 `X-Request-ID`）→ `MetricsMiddleware`（按路径模板统计请求数/延迟）→ `CORSMiddleware`。
- 全局异常兜底（L77-100）：未处理异常返回脱敏 500 + `request_id`，原始 traceback 只进结构化日志。
- 路由注册（L103-116）：批量 `include_router` 注册 14 个业务路由模块。
- 健康检查：`/health`（进程存活）、`/api/health/db`、`/api/health/redis`、`/api/health`（聚合，DB+Redis 并行 ping，任一失败返回 `degraded`，供 Docker healthcheck 使用）。

### 4.2 配置系统

**文件：[backend/app/core/config.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/core/config.py)**

`Settings(BaseSettings)` + `@lru_cache get_settings()` 单例，从 `.env` / 环境变量读取。关键配置组：

| 配置组 | 代表字段 | 说明 |
|---|---|---|
| 基础 | `DATABASE_URL`（强制 `postgresql+asyncpg://`）、`REDIS_URL`、`BACKEND_CORS_ORIGINS` | 校验器保证驱动正确 |
| 策略沙箱 | `STRATEGY_EXEC_TIMEOUT_SECONDS`(2s)、`STRATEGY_EXEC_MEMORY_MB`(256)、`STRATEGY_EXEC_TRACEBACK_CHARS`(4000) | 用户代码执行限制 |
| 回测 | `BACKTEST_ADJUST_MODE`(qfq/hfq/none)、`BACKTEST_FILL_PRICE_MODE`(next_open/current_close/current_intraday) | 复权与成交价口径 |
| K 线同步 | `KLINE_SYNC_WORKER_COUNT/CONCURRENCY/BUDGET_SECONDS/STUCK_SECONDS`、`KLINE_PERMANENT_FAILURE_THRESHOLD`(50) | DB 队列架构参数 |
| Celery | `CELERY_TASK_SOFT_TIME_LIMIT`(1500s)/`TIME_LIMIT`(1800s) | 任务超时上界 |
| 实时 | `REALTIME_BUS_PERSISTENCE`、`WS_QUEUE_MAXSIZE`、`WS_PING_*` | 总线持久化与 WS 背压 |

所有数值项都有 `field_validator` 范围校验。

### 4.3 中间件与可观测性

- **[core/middleware.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/core/middleware.py)**：`RequestIDMiddleware`（L26）注入请求 ID；`MetricsMiddleware`（L47）按路由模板聚合 HTTP 指标。
- **[api/metrics.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/metrics.py)**：`GET /metrics` 暴露 Prometheus 指标（`METRICS_ENABLED` 开关）。
- **[core/logging.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/core/logging.py)**：结构化日志（JSON/文本可切）。
- **[core/celery_health.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/core/celery_health.py)**：Celery worker 存活探测，供状态页展示。
- **[core/asyncio_runtime.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/core/asyncio_runtime.py)**：`get_loop()` / `run_async()`——Celery worker 进程内常驻事件循环，避免模块级 async 引擎跨 loop 报错。

### 4.4 API 路由清单

所有路由模块位于 [backend/app/api/](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api)。OpenAPI 文档：`/api/docs`（Swagger）、`/api/redoc`。

| 模块 | 前缀 | 主要端点 |
|---|---|---|
| [data.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/data.py) | `/api/data` | `GET /status`、`GET /kline-sync/jobs[/{job_id}[/items]]`、`POST /sync/stock-basic`、`POST /sync/trade-calendar`、`POST /sync/kline`、`GET /fund-flow/{ts_code}`、`GET /sync/kline/result/{task_id}` |
| [sources.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/sources.py) | `/api/data` | `GET /sources`、`POST /sources/check`、`POST /sources/{source_name}/check`、`PUT /sources`（数据源优先级配置） |
| [tasks.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/tasks.py) | `/api/tasks` | `POST /data/sample-kline`、`POST /data/sync-all-kline`、`POST /data/incremental-kline[/catchup]`、`GET /data/sync-progress`、`POST /data/fundamentals`、`POST /data/fund-flow`、`GET /recent`、`GET /{task_id}` |
| [stocks.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/stocks.py) | `/api/stocks` | `GET ""`（列表检索）、`GET /{ts_code}/klines`、`GET /{ts_code}/kline` |
| [watchlist.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/watchlist.py) | `/api/watchlist` | CRUD（`GET/POST ""`、`PATCH/DELETE /{item_id}`）、`POST /batch`、分组管理 `/groups`、`GET /summary` |
| [strategies.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/strategies.py) | `/api/strategies` | CRUD：`GET/POST ""`、`GET/PATCH/DELETE /{strategy_id}` |
| [backtests.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/backtests.py) | `/api/backtests` | `POST /{strategy_id}/run`、`POST /batch`、`GET /{backtest_id}`、`GET /{backtest_id}/klines`、`DELETE /{backtest_id}`、`GET /{backtest_id}/status`、`POST /{backtest_id}/cancel` |
| [signals.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/signals.py) | `/api/signals` | `POST /trigger`、`DELETE /clear`、`GET ""` |
| [sim.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/sim.py) | `/api/sim` | 账户 CRUD `/accounts`、`GET /accounts/{id}/positions|orders|trades|cash-flow|nav`、`POST /accounts/{id}/signals`（信号→委托）、`POST /orders/{id}/match|cancel` |
| [preferences.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/preferences.py) | `/api/preferences` | `GET/PUT /trading-fee`、`GET/PUT /kline-sync` |
| [realtime.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/realtime.py) | — | `GET /api/realtime/snapshot`、`GET /api/realtime/risk-guard/status`、`WebSocket /ws/realtime`（L269） |
| [seesaw.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/seesaw.py) | `/api/seesaw` | 避险池 CRUD `/pool`、`GET /market-state`、`GET /recommend`、`GET/PUT /rules`、`GET /triggers`、`PATCH /triggers/{id}/perf` |
| [system.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/system.py) | `/api/system` | `GET /alerts`、`POST /alerts/{alert_id}/resolve`（数据质量告警） |
| [metrics.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/metrics.py) | — | `GET /metrics`（Prometheus） |

### 4.5 数据层（三级回退）

**目录：[backend/app/data/](file:///Users/diaoff/code/vibe/leek-quant/backend/app/data)**

#### 领域模型（dataclass，非 ORM）
[data/models.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/data/models.py) 定义 `StockBasic`（L10）、`TradeCalendarDay`（L26）、`DailyKline`（L37，含 `adj_factor`/停牌/涨跌停标记）、`StockFundamental`（L57，估值+成长+三表）、`FundFlowDaily`（L85，五档资金流）。全平台金融数值统一 `Decimal`。

#### Provider 抽象
[providers.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/data/providers.py)：

- `DataProvider` Protocol（L53）+ `ProviderCapability` 能力枚举（L35）+ `METHOD_CAPABILITIES` 能力矩阵——不同数据源支持不同方法（如东财支持实时报价，Baostock 支持财务三表）。
- 实现（均按能力裁剪）：
  - `EastMoneyHttpProvider`（L214）：股票列表/K 线/实时报价/资金流
  - `TencentHttpProvider`（L396）：K 线备源/实时报价
  - `MootdxProvider`（L565）：通达信协议
  - `ADataProvider`（L630）：Tier 1 K 线
  - `BaostockProvider`（L679）：K 线 + 财务三表（`_fetch_quarterly` 季度快照）
  - `AkShareProvider`（L867）/ `AkShareFundFlowProvider`（L1005）：全市场补充 + 资金流

#### 回退核心
[fetcher.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/data/fetcher.py)：

- `fetch_with_fallback()`（L254）：按数据库 `data_source_config` 配置的优先级依次尝试 provider，支持重试（`DATA_MAX_RETRIES`）、代理（`DATA_PROXY_URL`）、熔断跳过。
- `fetch_union()`（L356）：全 provider 拉取合并去重，用于 `stock_basic` 全标的池同步。

#### 同步服务
- [service.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/data/service.py)：`sync_kline()` 主流程（L206 入口 → L332 单股处理），基于 DB 队列（`kline_sync_jobs`/`kline_sync_items`）派发，失败重投、连续 50 次失败标记永久失败。
- [stock_service.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/data/stock_service.py)：股票检索（`StockFilters` L61）+ `sync_fundamentals()`（L771）。
- [service_fund_flow.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/data/service_fund_flow.py)：`sync_fund_flow()`（L26）。
- [source_service.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/data/source_service.py)：数据源优先级配置的读写与启动加载。
- [quality.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/data/quality.py)：数据质量校验，异常写入 `alert_events`。
- [validators.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/data/validators.py)：`DataValidationError`（L8）入站校验。
- [repository/](file:///Users/diaoff/code/vibe/leek-quant/backend/app/data/repository)：仓储层（raw SQL）——`kline.py`（`upsert_daily_kline` L14，`ON CONFLICT` 幂等）、`kline_sync.py`（DB 队列状态机）、`calendar.py`（交易日历，所有日期逻辑唯一来源）、`fundamentals.py`、`fund_flow.py`、`seesaw.py`、`stock.py`、`task_runs.py`（任务运行记录）、`alerts.py`。
- [seesaw.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/data/seesaw.py)：跷跷板领域模型（`DefensivePoolItem` L33、`DefensiveRules` L46、`MarketSignalRecord` L61、`RecommendationItem` L76）。

### 4.6 回测引擎

**目录：[backend/app/backtest/](file:///Users/diaoff/code/vibe/leek-quant/backend/app/backtest)**

- **[adapter.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/backtest/adapter.py)**——引擎核心：
  - 数据结构：`KBar`（L28）、`Position`（L46）、`TradeRecord`（L53）、`_LotEntry`/`_ClosedLot`（L76/L85，分批持仓与平仓明细）、`BacktestConfig`（L117）。
  - 性能优化：`_StockArrays`（L208，每只股票预计算 numpy 数组）+ `_SeriesView`（L238，按交易日对齐的只读切片，防前视）。
  - 防前视：`_FundamentalsSnapshot`（L291）按决策日截取"最近一期已公告财报"。
  - `BacktestContext`（L333）+ `BacktestRunner`：逐日事件驱动回测。
- **[strategy_runtime.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/backtest/strategy_runtime.py)**——用户策略沙箱：`StrategyExecutionError`/`StrategyTimeoutError`（L47/L51）、`StrategyExecutionOptions`（L56，超时/内存/traceback 截断）、`StrategyExecutionResult`（L63）。用户代码以受限方式执行，异常被结构化捕获。
- **[signals.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/backtest/signals.py)**——`SignalInput`/`SignalOutput`（L16/L23）+ A 股规则过滤（L76-101）：停牌不可交易、涨停不可买入、跌停不可卖出、T+1 当日买入不可卖出。
- **[cost.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/backtest/cost.py)**——费用模型：佣金万 2.5（最低 5 元）+ 印花税 0.05%（仅卖出）+ 过户费 0.001%。
- **[rebalance.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/backtest/rebalance.py)**——调仓规划器：`WeeklyRebalancePlanner`（L89）+ 决策数据类（`RankInfo`/`HoldingInfo`/`CandidateInfo`/`TargetPosition`/`PlannedOrder`/`RebalanceDecision`，L18-68）；`plan()`（L167）生成计划、`execute()`（L326）按成交 bar 执行；支持周度调仓与每日最大买入数限制。
- **[kline_cache.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/backtest/kline_cache.py)**——回测 K 线缓存（复权口径适配）。
- **[strategy_service.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/backtest/strategy_service.py)**——策略 CRUD 与源码快照。
- **[tasks.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/backtest/tasks.py)**——`run_backtest_task`（L381），跑在独立 `backtest` 队列；结果落 `backtest_results`（含 `strategy_source_snapshot` 源码快照保证可复现）+ 明细三表（`backtest_trades`/`backtest_closed_lots`/`backtest_stock_rankings`）。

### 4.7 模拟交易引擎

**目录：[backend/app/sim/](file:///Users/diaoff/code/vibe/leek-quant/backend/app/sim)**——完整 6 表闭环：**委托 → 成交 → 持仓 → 流水 → 净值快照**。

- **[orders.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/sim/orders.py)**——核心撮合：
  - `generate_order_from_signal()`（L157）：五档信号 → 委托单（含仓位目标换算），写入 `signal_log` 关联。
  - `match_order()`（L478）：撮合执行——`_resolve_match_price`（L43，限价/市价/收盘价模式）、`_limit_rate`/`_computed_limit_flags`（L85/L96，涨跌停校验：主板 10%、ST 5%、科创/创业 20%）、T+1 可卖校验、费用计算、持仓/现金流更新，全程 Decimal 资金守恒。
  - `cancel_order()`（L797）。
- **[accounts.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/sim/accounts.py)**——账户 CRUD（`create_account` L86 等）与级联子表查询（`list_child_rows` L192）。
- **[nav.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/sim/nav.py)**——净值体系：
  - `unlock_t1_positions()`（L153）：T+1 解锁（`available_shares` → `shares`，每日 09:25 Beat 触发）。
  - `snapshot_daily_nav()`（L193）：每日净值快照（15:20）。
  - `check_stop_conditions()`（L79）：止盈止损检查。
  - `refresh_position_market_values()`（L54）/ `refresh_account_assets()`（L38）。
- **[valuation.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/sim/valuation.py)**——实时估值：`enrich_account_with_realtime_valuation()`（L204）等，用实时 tick + 当日基线计算浮动盈亏。
- **[seesaw_switch.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/sim/seesaw_switch.py)**——跷跷板账户切换：`execute_seesaw_switch()`（L122）按大盘状态执行进攻/防守仓切换，`apply_seesaw_transition()`（L215）平滑过渡。
- **[serialize.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/sim/serialize.py)**——行序列化（金额规整、JSONB 解包）。
- **[_helpers.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/sim/_helpers.py)**——费率配置解析（`FeeConfig`）、交易日历/K 线查询工具。

**五档信号语义**（状态机映射到目标仓位）：

| 信号 | 语义 | 默认目标仓位 |
|---|---|---|
| 买入 | 强烈看多 | 100% |
| 增持 | 看多非强信号 | 当前 +25% |
| 减仓 | 降风险 | 当前 −25% |
| 卖出 | 清仓 | 0% |
| 观望 | 维持 | 不变 |

### 4.8 实时行情子系统

**目录：[backend/app/realtime/](file:///Users/diaoff/code/vibe/leek-quant/backend/app/realtime)**

- **[models.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/realtime/models.py)**——`RealtimeTick`（L30，Pydantic，含代码规范化）。
- **[bus.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/realtime/bus.py)**——Redis Pub/Sub 总线：`RealtimeBus` Protocol（L34）、`RedisRealtimeBus`（L148，`publish` L165 / `open_subscription` L179）、`RedisRealtimeSubscription`（L40，`subscribe`/`unsubscribe`/`listen`，`_replay_history` L79 支持断线回放 `replay_from`）。`REALTIME_BUS_PERSISTENCE` 开启时 tick 短暂持久化以支持补拉。
- **[eastmoney_ws.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/realtime/eastmoney_ws.py)**——东方财富 WebSocket 自建解析器（二进制帧解析）。
- **[producer.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/realtime/producer.py)** / **[ws_producer.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/realtime/ws_producer.py)**——行情生产者：HTTP 快照轮询（M6a）与 WS 推流（M6b），独立进程 `realtime_ws` 运行，产出 tick 发到总线。
- **[risk_guard.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/realtime/risk_guard.py)**——盘中实时风控（独立服务 `realtime_risk_guard`）：`RealtimeRiskGuard`（L429）订阅 tick 流，`handle_tick`（L452）对持仓执行止损/止盈/预警；`run`（L494）/ `run_snapshot_polling`（L516）两种驱动模式；`GuardPosition`（L30）持仓快照。
- **[api/realtime.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/realtime.py)**——`WebSocket /ws/realtime`（L269）：前端订阅入口，背压队列（`WS_QUEUE_MAXSIZE`）+ ping/pong 心跳 + 订阅增量同步。

### 4.9 跷跷板（Seesaw）子系统

大盘进攻/防守风格轮动策略：以沪深 300（`000300.SH`）为基准指数，通过 MA 均线组（`defensive_rules`：ma_short=5 / ma_long=20 / ma_long2=60）与回撤阈值（`drop_threshold`）判定大盘状态 `up / neutral / down`，状态为防守时给出避险池（`defensive_pool`，手动维护）推荐，并可对开启跷跷板的模拟账户执行进攻↔防守仓切换（[sim/seesaw_switch.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/sim/seesaw_switch.py)）。

- 领域逻辑：[data/seesaw.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/data/seesaw.py)；API：[api/seesaw.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/api/seesaw.py)；定时检测：[tasks/seesaw_tasks.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/tasks/seesaw_tasks.py) `check_market_state`（每日 16:05）。
- 记录表：`market_signal_log`（状态变更）、`seesaw_trigger_log`（触发记录 + 推荐股票后续表现 `subsequent_perf`）。

### 4.10 Celery 任务系统

**文件：[backend/app/tasks/celery_app.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/tasks/celery_app.py)**

- **队列路由**（L45-50）：`data_tasks.* → data`、`run_backtest → backtest`、`trading_tasks.* / signal_tasks.* → trading`，其余进 `default`。
- **可靠性配置**：`task_acks_late=True` + `task_reject_on_worker_lost=True`（worker 崩溃任务重投）、`worker_max_tasks_per_child=50`（防内存泄漏）、时区 `Asia/Shanghai`。
- **事件机制**：
  - `worker_process_init`（L123）：每进程建立常驻事件循环。
  - `worker_ready`（L138）：启动时清理 24h 前滞留 `running` 的 task_runs + 立即触发一次 K 线队列卡死恢复。
  - 任务生命周期信号（L190-220）将 `started/success/failed` 发布到 Redis 频道 `celery:task_events`，供 WebSocket 前端推送；`_reconcile_task_run`（L223）作为 task_runs 状态漂移的兜底（任务体被 SoftTimeLimitExceeded 杀死时仍能落终态）。

**Celery Beat 调度表**（L59-119）：

| 任务 | 调度 | 职责 |
|---|---|---|
| `data_tasks.update_stock_basic` | 周六 03:00 | 全标的池刷新 |
| `data_tasks.update_trade_calendar` | 周日 02:00 | 交易日历刷新 |
| `data_tasks.kline_sync_dispatch(incremental)` | 每日 16:00 与 21:00 | K 线增量同步（DB 队列派发） |
| `data_tasks.kline_sync_dispatch(full)` | 周日 04:00 | K 线全量同步 |
| `signal_tasks.generate_all_signals` | 每日 12:00 | 全量五档信号生成 |
| `data_tasks.sync_fundamentals` | 每日 19:30 | 财务数据同步 |
| `data_tasks.sync_fund_flow_task` | 每日 20:00 | 资金流同步 |
| `trading_tasks.unlock_t1_daily` | 每日 09:25 | T+1 持仓解锁 |
| `trading_tasks.match_pending_orders` | 每日 17:05 | 悬挂委托撮合（收盘价） |
| `trading_tasks.snapshot_nav_daily` | 每日 15:20 | 模拟账户净值快照 |
| `data_tasks.cleanup_stale_task_runs` | 每小时 :15 | 滞留任务清理 |
| `seesaw_tasks.check_market_state` | 每日 16:05 | 跷跷板大盘状态检测 |
| `data_tasks.kline_sync_recover_stuck` | 每 `KLINE_SYNC_RECOVER_INTERVAL_SECONDS`(60s) | K 线队列卡死恢复 |

辅助模块：[beat_lock.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/tasks/beat_lock.py)（Redis 分布式锁防 Beat 重复调度）、[tracking.py](file:///Users/diaoff/code/vibe/leek-quant/backend/app/tasks/tracking.py)（`_run_tracked` 任务运行记录包装）。

---

## 5. 前端架构

**目录：[frontend/](file:///Users/diaoff/code/vibe/leek-quant/frontend)**

### 5.1 技术栈与构建

- [package.json](file:///Users/diaoff/code/vibe/leek-quant/frontend/package.json)：React 18 + react-router-dom + Vite；脚本 `dev / build / preview / typecheck / test (vitest) / test:smoke (Playwright)`。
- [vite.config.ts](file:///Users/diaoff/code/vibe/leek-quant/frontend/vite.config.ts)：手动 vendor 分包（`monaco-editor`、`lightweight-charts`、`react-router`），monaco 依赖预优化。
- 主题：[lib/theme.tsx](file:///Users/diaoff/code/vibe/leek-quant/frontend/src/lib/theme.tsx)（暗色主题，**红涨绿跌** A 股配色）。

### 5.2 路由与页面

入口 [main.tsx](file:///Users/diaoff/code/vibe/leek-quant/frontend/src/main.tsx)：`BrowserRouter` + `ThemeProvider` + `Layout`，页面全部 `React.lazy` 按需加载。导航定义在 [components/Layout.tsx](file:///Users/diaoff/code/vibe/leek-quant/frontend/src/components/Layout.tsx)（L21-34）。

| 路由 | 页面 | 功能 |
|---|---|---|
| `/dashboard` | DashboardPage | 总览仪表盘 |
| `/market` | MarketPage | 行情页（分页 + 实时刷新） |
| `/watchlist` | WatchlistPage | 自选股管理（分组 + 实时行情） |
| `/strategy` | StrategyPage | 策略管理 + Monaco 编辑器（MyTT 补全 [lib/mytt-completions.ts](file:///Users/diaoff/code/vibe/leek-quant/frontend/src/lib/mytt-completions.ts)） |
| `/backtests` | BacktestPage | 回测列表/详情/收益分析/重跑（[lib/backtest-run.ts](file:///Users/diaoff/code/vibe/leek-quant/frontend/src/lib/backtest-run.ts)、[components/BacktestRunModal.tsx](file:///Users/diaoff/code/vibe/leek-quant/frontend/src/components/BacktestRunModal.tsx)） |
| `/signals` | SignalsPage | 五档信号列表与手动触发 |
| `/simulation` | SimulationPage | 模拟交易（账户/持仓/委托/成交/净值） |
| `/seesaw` | SeesawPage | 跷跷板（避险池/大盘状态/触发记录） |
| `/status` | StatusPage | 系统状态（服务健康/任务/告警） |
| `/preferences` | PreferencesPage | 偏好设置（费率/同步并发） |

### 5.3 WebSocket 体系

核心 [hooks/useWebSocket.ts](file:///Users/diaoff/code/vibe/leek-quant/frontend/src/hooks/useWebSocket.ts)：

- `WebSocketConnection`（L39-84）：连接状态机、订阅消息去重、消息处理器注册。
- 生命周期（L128-206）：open/message/error/close 处理、服务端错误与心跳、**指数退避自动重连**。
- `useWebSocket` hook（L228-328）：通过 `connectionRegistry` **同路径连接复用**（多个组件共享一条 WS），自动管理订阅增减与卸载清理。

上层 hooks：

- [useRealtimeTicks.ts](file:///Users/diaoff/code/vibe/leek-quant/frontend/src/hooks/useRealtimeTicks.ts)：订阅 `/ws/realtime` 实时 tick；重连时带 `?replay_from=<stream_id>` 补拉断线期间的行情（L37-64）。
- [useTaskEvents.ts](file:///Users/diaoff/code/vibe/leek-quant/frontend/src/hooks/useTaskEvents.ts)：订阅 Celery 任务生命周期事件（对应后端 `celery:task_events` 频道）。
- [useSignalEvents.ts](file:///Users/diaoff/code/vibe/leek-quant/frontend/src/hooks/useSignalEvents.ts)：订阅信号事件推送。

---

## 6. 数据库 Schema

PostgreSQL 统一存储；**无 ORM 模型**——迁移全部为原生 SQL DDL（见 [backend/alembic/versions/](file:///Users/diaoff/code/vibe/leek-quant/backend/alembic/versions)），业务层用 dataclass + raw SQL 仓储。

### 6.1 表清单（按域）

| 域 | 表 | 迁移 | 说明 |
|---|---|---|---|
| 市场 | `stock_basic` | 202605150001 | 全 A 股标的（含 ST/退市标记） |
| | `trade_calendar` | 202605150001 | 交易日历（日期逻辑唯一来源，勿硬编码假期） |
| | `daily_kline` | 202605150001 | 日 K 线，**按年分区**（2028-2030+DEFAULT 分区由 202607200001 补齐） |
| | `stock_fundamentals` | 202605180001 | 财务三表 + 估值/成长指标 |
| | `fund_flow_daily` | 202608200001 | 五档资金流 |
| 用户 | `users` | 202605150001 | 用户 |
| | `user_preferences` | 202605250001 | 费率、K 线同步偏好 |
| | `watchlist` / `watchlist_groups` | 202605180001 / 202605200005 | 自选股与分组 |
| 策略/回测 | `strategies` | 202605180002 | 策略源码 |
| | `backtest_results` | 202605180002 | 回测结果（202608180001 增加 `strategy_source_snapshot` 源码快照） |
| | `backtest_trades` / `backtest_closed_lots` / `backtest_stock_rankings` | 202608010001 | 明细三表（拆分原 JSONB 大字段，规避 256MB 限制） |
| 信号 | `signal_log` | 202605180002 | 五档信号日志 |
| 模拟交易 | `sim_accounts` | 202605220002 | 模拟账户（含跷跷板配置） |
| | `sim_positions` | 202605220002 | 持仓（`shares` ≠ `available_shares` 实现 T+1） |
| | `sim_orders` / `sim_trades` / `sim_cash_flow` / `sim_daily_nav` | 202605220002 | 委托/成交/现金流/净值快照 |
| 跷跷板 | `defensive_pool` / `market_signal_log` / `seesaw_trigger_log` / `defensive_rules` | 202608200003 | 避险池/大盘状态/触发记录/规则 |
| 系统 | `task_runs` | 202605150001 | Celery 任务运行记录 |
| | `alert_events` | 202605150001 | 数据质量/风控告警 |
| | `data_source_config` | 202605220001 | 数据源优先级（运行时可改） |
| | `kline_sync_jobs` / `kline_sync_items` | 202607230001 | K 线同步 DB 队列 |
| | `kline_sync_failures` | 202607220001 | 同步失败记录 |
| ⚠️ 废弃 | `factor_definitions` / `factor_values` / `scoring_rank` / `factor_analysis` / `factor_score_runs` | 202605220003 / 202607310002 | M5 多因子残留，代码已移除，勿依赖 |
| ⚠️ 废弃 | `data_update_state` | 已于 202607290001 删除 | 增量进度改由 DB 队列承担 |
| ⚠️ 废弃 | `stock_pools` / `stock_pool_items` | 202605200004 已删除 | 股票池功能已移除 |

### 6.2 迁移演进脉络

```
M0 基础表(20260515) → M2 自选股(20260518) → M3 策略/回测(20260518)
→ 数据源配置(20260522) → M4 模拟交易6表(20260522) → M5 因子表(20260522,后废弃)
→ 用户偏好(20260525) → K线分区扩展(20260720) → K线同步DB队列化(20260722-23)
→ 删data_update_state(20260729) → 回测明细三表化(20260801)
→ 资金流(20260820) + 跷跷板4表(20260820) + 策略源码快照(20260818)
```

---

## 7. 依赖关系

### 7.1 后端 Python 依赖（[backend/requirements.txt](file:///Users/diaoff/code/vibe/leek-quant/backend/requirements.txt)）

- Web：`fastapi`、`uvicorn`、`pydantic-settings`
- DB：`sqlalchemy[asyncio]`、`asyncpg`、`alembic`
- 队列：`celery`、`redis`
- 数据源：`adata`、`baostock`、`akshare`、`mootdx`（按需可选）、`httpx`
- 其它：`numpy`/`pandas`（MyTT 依赖）、`prometheus-client`

### 7.2 前端依赖（[frontend/package.json](file:///Users/diaoff/code/vibe/leek-quant/frontend/package.json)）

`react` / `react-dom` / `react-router-dom`、`monaco-editor`、`lightweight-charts`、`tailwindcss` + shadcn/ui 组件、`vitest`（单测）、`playwright`（冒烟）。

### 7.3 模块间依赖方向（后端）

```
api/ ──▶ sim/ ──▶ data/repository/ ──▶ db/session.py
 │         │                              ▲
 │         ├──▶ realtime/bus.py（实时估值） │
 ├──▶ backtest/ ──▶ data/（K线缓存/日历）───┘
 ├──▶ data/（service/fetcher/providers）
 ├──▶ tasks/ ──▶ sim/ + data/ + backtest/ + seesaw
 └──▶ preferences/
core/config.py 被所有层引用；libs/MyTT.py 被策略沙箱与回测引用
```

关键约束（来自 AGENTS.md / 技能规则）：

- 数据源只能经 `data/fetcher.py` 调用，禁止直接 import adata/baostock/akshare；
- 日期逻辑必须查 `trade_calendar.is_open`；
- 金融计算必须 `Decimal`，禁止 float；
- ts_code 格式统一 `600000.SH` / `000001.SZ`；
- 新功能实现前先查 `third_party/references/QuantDinger/` 参考实现。

---

## 8. 运行方式

### 8.1 Docker Compose（推荐）

```bash
cp .env.example .env    # 配置 POSTGRES_PASSWORD / DATABASE_URL / CONTAINER_DATABASE_URL / REDIS_URL 等
docker compose up -d    # 一键启动 9 个服务
```

- backend 容器启动命令（[backend/Dockerfile](file:///Users/diaoff/code/vibe/leek-quant/backend/Dockerfile)）：`alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000`（迁移自动执行）。
- frontend：Node 构建 → nginx 托管（[nginx.conf](file:///Users/diaoff/code/vibe/leek-quant/frontend/nginx.conf)：SPA 回退 + `/health`），端口 `8080:80`。
- 健康检查：backend/celery_backtest_worker/realtime 服务走 `/api/health` 或 `/api/health/redis`；postgres/redis 各自 probe。

### 8.2 本地开发（非 Docker）

```bash
./restart.sh   # 读取 .env → alembic 迁移 → 启动 backend + frontend + celery worker/backtest worker/beat
./stop.sh      # 全部停止
```

前端单独开发：`cd frontend && npm run dev`（Vite :5173，API 代理见 vite.config.ts）。

### 8.3 环境变量（[.env.example](file:///Users/diaoff/code/vibe/leek-quant/.env.example)）

| 变量 | 说明 |
|---|---|
| `POSTGRES_PASSWORD` | 数据库密码 |
| `DATABASE_URL` | 本机视角连接串（必须 `postgresql+asyncpg://`） |
| `CONTAINER_DATABASE_URL` | 容器内视角连接串 |
| `REDIS_URL` | Redis 连接（broker + backend + 总线） |
| `BACKEND_CORS_ORIGINS` | CORS 白名单（默认 5173） |
| `VITE_API_BASE_URL` | 前端 API 地址 |
| 其余 | 见 §4.2 配置组（策略沙箱/回测口径/K线同步/WS 等） |

### 8.4 测试

```bash
# 后端（pytest，根目录 pytest.ini；70+ 测试覆盖迁移/数据/回测/模拟盘/实时）
cd backend && pytest

# 前端单测
cd frontend && npm run test

# 前端 Playwright 冒烟（回测/行情/自选股）
cd frontend && npm run test:smoke
```

---

## 9. 关键设计约定

1. **统一 PostgreSQL**：市场数据、模拟交易、系统状态全部入库，不使用 DuckDB/Parquet/SQLite。
2. **无 ORM**：SQLAlchemy 仅提供异步引擎/会话；表结构由 Alembic 原生 DDL 维护，查询为 raw SQL + dataclass 映射。
3. **三级数据回退**：provider 优先级存于 `data_source_config`，运行时可调；所有数据访问必须经 `fetcher.py`。
4. **Decimal 金融计算**：全链路禁 float；费用模型 = 佣金万 2.5（最低 5 元）+ 印花税 0.05%（卖）+ 过户费 0.001%。
5. **A 股规则内建**：T+1（`available_shares` 机制 + 09:25 解锁）、涨跌停（主板 10%/ST 5%/科创创业 20%）、停牌、100 股整手。
6. **交易日历驱动**：所有日期逻辑查 `trade_calendar.is_open`，不硬编码节假日。
7. **防前视回测**：`_SeriesView` 截断 + 财报快照按公告日截取 + `next_open` 默认成交价。
8. **可靠性**：Celery `acks_late` + DB 队列重投 + 永久失败阈值 + 卡死恢复巡检 + task_runs 状态兜底对账。
9. **实时总线可回放**：Redis Pub/Sub + 短暂持久化，断线重连用 `replay_from` 补拉。
10. **前端红涨绿跌**（A 股习惯，与美股相反）。
11. **M5 因子系统已废弃**：`app/factor` 包、`factors.py` API、`FactorPage` 前端页均已移除，残留表勿依赖。
