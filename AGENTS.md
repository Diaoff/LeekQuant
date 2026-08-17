# Leek Quant — 智能体指南

## 当前状态

预实现阶段。根目录下没有源代码、清单文件或构建配置。所有设计决策都在 `docs/` 下的设计文档中。骨架将从零开始搭建。

## 项目定位

- **Leek Quant** = 纯 A 股量化平台，从 QuantDinger 精简而来（移除非 A 股市场、跨资产交易、云托管）
- **本地优先、隐私优先**：数据/策略保存在用户本地机器，Docker Compose 一键部署
- **参考项目**：`third_party/references/QuantDinger/` — 真实的 Dockerfile、CI、docker-compose、项目结构可供适配

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
| 因子 | 类 Qlib 表达式范式（轻量级，存储在 PostgreSQL 中） |

## 设计文档

所有 9 份设计文档都在 `docs/` 目录下。最全面的两份：
- `docs/finally-design.md`（1769 行）— 完整架构、数据库 schema、API 设计、所有子系统
- `docs/workbuddy-design.md`（1676 行）— 综合 6 份 AI 草稿，包含 Docker Compose 配置和里程碑

## 关键设计决策（来自文档）

- **统一 PostgreSQL**：市场数据、因子、模拟交易、系统状态全部存储在 PostgreSQL 中，不另用 DuckDB/Parquet/SQLite
- **三级数据回退**：AData → Baostock → AkShare，支持增量拉取，通过 `data_update_state` 表追踪
- **五档信号**：买入 / 加仓 / 减仓 / 卖出 / 等待，通过状态机映射到实际操作
- **模拟交易**：完整的 6 表闭环（`sim_accounts`、`sim_positions`、`sim_orders`、`sim_trades`、`sim_cash_flow`、`sim_daily_nav`），支持 T+1、涨跌停、手续费
- **多因子打分**：估值 / 成长 / 质量 / 动量，含 IC/IR 分析（⚠️ 尚未实现：代码中无 `app/factor` 包、无 IC/IR 计算；仅 PostgreSQL 留有 `scoring_rank` / `factor_values` 表）
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
| M5 | 多因子打分、IC/IR 分析 | 待处理（实际未实现，见上方设计决策说明） |
| M6a | HTTP 快照实时行情：东方财富 HTTP、Redis Pub/Sub、WebSocket 订阅、前端展示 | ✅ 完成 |
| M6b | WebSocket 推流：东方财富 WS 推送、任务/信号 WS 通道、断线重连 | 待处理 |
| M7 | 打磨：认证、股票池、因子表达式引擎、周/月线、监控、文档 | 待处理 |

## 构建与工具

尚未配置构建工具。添加工具时，遵循 QuantDinger 的惯例（参见 `third_party/references/QuantDinger/`）：
- Docker Compose 布局（postgres、redis、backend、frontend 服务）
- CI 工作流（basic-ci、docker-publish、update-frontend）
- Python 项目结构（backend_api_python、mcp_server 包）
- Dockerfile（python:3.12-slim、nginx:1.25-alpine）

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