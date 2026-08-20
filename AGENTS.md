# Leek Quant — 智能体指南

## 当前状态

**已实现的可运行平台**（非预实现阶段）。仓库包含完整后端（`backend/`，FastAPI + Celery + SQLAlchemy 异步）、前端（`frontend/`，React + Vite）与 `docker-compose.yml` 编排。M0–M6b 已落地；**M5 多因子选股曾实现但已废弃并移除代码**；M7 仍为规划中。详见下方「开发里程碑」与 `README.md`。

## 项目定位

- **Leek Quant** = 纯 A 股量化平台，从 QuantDinger 精简而来（移除非 A 股市场、跨资产交易、云托管）
- **本地优先、隐私优先**：数据/策略保存在用户本地机器，Docker Compose 一键部署
- **参考项目**：`third_party/references/QuantDinger/` — 真实的 Dockerfile、CI、docker-compose、项目结构可供适配

## 技能调用规则（重要）

opencode 没有关键词自动触发技能机制，技能加载完全依赖模型判断。**以下触发词出现时，必须先用 `skill` 工具加载对应技能，再按其工作流执行，不得跳过：**

| 触发词 | 必须加载的技能 |
|---|---|
| 回测分析 / 回测记录分析 / 参数优化 / 参数敏感度 / 实盘就绪度 / 策略诊断 / backtest analysis / 回测深度诊断 / 深度诊断 / go-live / 绩效归因 / 退出理由分析 / 追踪误差 | `leek-quant-backtest-analysis` |
| 数据源回退 / 三层回退 / MyTT 指标 / 模拟交易 / 信号状态机 / 因子打分 / QuantDinger 参考 / 平台功能实现 | `leek-quant` |

注意：**「分析回测记录、优化参数、判断能否实盘」类请求属于回测分析技能，不是平台实现技能。** 不要把"回测分析"误路由到 `leek-quant`。

## 设计文档中的架构选型

| 层 | 选型 |
|---|---|
| 前端 | React 18 + Vite + TypeScript + Tailwind CSS + shadcn/ui |
| 图表 | TradingView Lightweight Charts |
| 编辑器 | Monaco Editor（Python + MyTT 自动补全） |
| 状态管理 | Zustand |
| 后端 | FastAPI（Python 3.11+） |
| ORM | SQLAlchemy 2.0（异步）+ Alembic |
| 数据库 | **PostgreSQL 15+**（统一存储，不使用 DuckDB/Parquet/SQLite 等独立存储） |
| 队列 | Celery + Redis |
| 实时通信 | Redis Pub/Sub → FastAPI WebSocket |
| 回测 | Python 原生 BacktestRunner（A 股规则在平台代码中实现） |
| 指标库 | MyTT（通达信/同花顺兼容，单文件零依赖） |
| 历史数据 | AData（Tier 1）→ Baostock（Tier 2）→ AkShare（Tier 3）三级回退 |
| 实时数据 | 东方财富 WebSocket（自建解析器） |
| 因子（⚠️ 已废弃） | 原规划类 Qlib 表达式范式，M5 多因子选股代码已移除 |

## 设计文档

所有 9 份设计文档都在 `docs/` 目录下。最全面的两份：
- `docs/finally-design.md`（1769 行）— 完整架构、数据库 schema、API 设计、所有子系统
- `docs/workbuddy-design.md`（1676 行）— 综合 6 份 AI 草稿，包含 Docker Compose 配置和里程碑

## 关键设计决策（来自文档）

- **统一 PostgreSQL**：市场数据、模拟交易、系统状态全部存储在 PostgreSQL 中，不另用 DuckDB/Parquet/SQLite（原规划的因子数据已随 M5 废弃）
- **三级数据回退**：AData → Baostock → AkShare，支持增量拉取（注：`data_update_state` 表已在迁移 `202607290001_drop_data_update_state.py` 中删除，当前增量同步进度不再依赖该表）
- **五档信号**：买入 / 加仓 / 减仓 / 卖出 / 等待，通过状态机映射到实际操作
- **模拟交易**：完整的 6 表闭环（`sim_accounts`、`sim_positions`、`sim_orders`、`sim_trades`、`sim_cash_flow`、`sim_daily_nav`），支持 T+1、涨跌停、手续费
- **多因子打分（⚠️ 已废弃并移除代码）**：M5 多因子选股曾实现（因子四表 + 8 内置因子 + 计算/排行榜/ICIR MVP + 前端因子页），但后续已整体移除 `app/factor` 包、`factors.py` API、`factor_tasks.py` 任务与 `FactorPage` 前端页。早期迁移仍可能在库中创建 `factor_definitions` / `factor_values` / `scoring_rank` / `factor_analysis` / `factor_score_runs` 表，但已无任何功能使用，请勿依赖。
- **日 K 线按年分区**存储于 PostgreSQL
- **交易日历存入数据库**：所有日期逻辑查询数据库，不硬编码假期列表

## 第三方依赖

- `third_party/libs/MyTT.py`（v3.3，287 行）— 通达信/同花顺兼容的技术指标库。单文件，仅依赖 numpy/pandas。将集成到策略编辑和信号生成中。

## 开发里程碑（来自文档）

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 骨架：Docker Compose、PostgreSQL、Redis、FastAPI、React 外壳 | ✅ 完成 |
| M1 | 基础设施：PostgreSQL、数据源、K 线增量拉取 | ✅ 完成 |
| M2 | 自选股、策略 CRUD、Monaco 编辑器 | ✅ 完成 |
| M3 | 策略与回测：Python 原生异步回测、交易记录 | ✅ 完成 |
| M4 | 五档信号、完整模拟交易引擎（6 表） | ✅ 完成 |
| M5 | 多因子打分、IC/IR 分析 | ⚠️ 已废弃（曾实现，代码已整体移除，见上方设计决策说明） |
| M6a | HTTP 快照实时行情：东方财富 HTTP、Redis Pub/Sub、WebSocket 订阅、前端展示 | ✅ 完成 |
| M6b | WebSocket 推流：东方财富 WS 推送、任务/信号 WS 通道、断线重连 | ✅ 完成（realtime_ws / realtime_risk_guard 服务已部署） |
| M7 | 打磨：认证、周/月线、监控、文档（注：原「股票池」「因子表达式引擎」属已废弃的 M5，不再纳入） | 待处理 |

## 构建与工具

平台已具备完整构建与运行工具链：
- `docker-compose.yml`：PostgreSQL / Redis / backend / celery_worker / celery_backtest_worker / celery_beat / realtime_risk_guard / realtime_ws / frontend 共 9 个服务
- `backend/Dockerfile`（python:3.12-slim）、`backend/requirements.txt`、`backend/alembic/` 迁移
- 前端：`frontend/`（Vite + React + shadcn/ui），`npm run dev/build/typecheck`
- 本地脚本：`restart.sh`（本机直接起 backend + celery + frontend，不含 Docker）、`stop.sh`
- 参考实现位于 `third_party/references/QuantDinger/`（Dockerfile、CI 工作流、FastAPI 结构、MCP Server）

## 参考：QuantDinger 结构

`third_party/references/QuantDinger/` 是一个完整的可运行项目（v3.0.6），包含：
- `docker-compose.yml` — 4 个服务（postgres、redis、backend、frontend）
- `backend_api_python/` — FastAPI 后端
- `frontend/` — React SPA（预构建 dist + nginx）
- `mcp_server/` — MCP 协议服务器（pyproject.toml 依赖：mcp>=1.2.0、httpx>=0.27.0）
- `.github/workflows/` — basic-ci、docker-publish、update-frontend

## 实现指导

**功能实现可以先从 QuantDinger 寻找灵感。** 在开始任何功能模块的实现前：

1. **先读 QuantDinger**：浏览 `third_party/references/QuantDinger/` 中对应模块的源码，搞清楚"他们是怎么做的"
2. **再适配 Leek Quant**：根据本项目的架构约束（PostgreSQL 统一存储、纯 A 股、无云端）做裁剪和适配
3. **避免重复造轮子**：数据模型、API 路由、Celery 任务、WebSocket 推送、模拟交易闭环等核心逻辑，QuantDinger 已有成熟实现可直接参考

| 功能领域 | QuantDinger 参考路径 | 适配要点 |
|---|---|---|
| Docker Compose 全家桶 | `docker-compose.yml` | 移除非 A 股市场服务 |
| FastAPI 路由与依赖注入 | `backend_api_python/` | 保持 PostgreSQL + Celery 架构 |
| 前端组件结构 | `frontend/` | 复用 Vite + React + shadcn/ui 模式 |
| MCP Server | `mcp_server/` | 按需裁剪工具集 |
| Alembic 迁移 | `backend_api_python/alembic/` | 迁移文件可直接复用表结构逻辑 |

> 核心原则：**参考不是复制**。目标是理解设计思路后，用 Leek Quant 的技术栈（PostgreSQL / FastAPI / Celery / React）重新实现，而不是直接 fork 或导入 QuantDinger 代码。

## A 股数据注意事项

- 所有数据源均为免费的中国金融数据 API
- 东方财富 WebSocket 需要自建解析器（无现成库可用）
- 交易日历必须考虑中国法定节假日（从数据库查询，不硬编码）
- 回测使用 Python 原生的 `BacktestRunner`；A 股规则在平台代码中实现