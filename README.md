# 🌱 Leek Quant (韭菜量化)

<p align="center">
  <img src="docs/logo.png" alt="Leek Quant Logo" width="120" height="120">
</p>

<p align="center">
  <strong>纯A股本地优先量化研究与模拟交易平台</strong>
</p>

<p align="center">
  <a href="#技术栈"><img src="https://img.shields.io/badge/Python-3.11+-blue.svg" alt="Python"></a>
  <a href="#技术栈"><img src="https://img.shields.io/badge/FastAPI-0.111+-green.svg" alt="FastAPI"></a>
  <a href="#技术栈"><img src="https://img.shields.io/badge/React-18-61dafb.svg" alt="React"></a>
  <a href="#技术栈"><img src="https://img.shields.io/badge/PostgreSQL-15+-336791.svg" alt="PostgreSQL"></a>
  <a href="#技术栈"><img src="https://img.shields.io/badge/Celery-5.3+-38a169.svg" alt="Celery"></a>
  <a href="#技术栈"><img src="https://img.shields.io/badge/Docker-Compose-2496ED.svg" alt="Docker"></a>
  <a href="#许可证"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License"></a>
</p>

---

## ✨ 核心特性

### 📊 A股全市场数据（三层回退机制）
- **Tier1**: AData — 股票列表、日/周/月K线、基础行情
- **Tier2**: Baostock — K线备源、财务三表、估值指标
- **Tier3**: AkShare — 分钟线、交易日历、全市场补充数据
- 智能故障切换 + 数据校验 + 增量拉取

### 💻 Python策略编辑器
- Monaco Editor 集成，支持 Python 语法高亮与智能补全
- 内置 **MyTT 指标库**（28个函数）：MA、EMA、MACD、RSI、BOLL、KDJ 等
- 通达信/同花顺兼容语法，降低学习成本

### ⚡ 高性能回测引擎
- **Hikyuu C++ 内核**驱动，支持大规模历史回测
- 完整 **A股交易规则**：T+1、涨跌停、停牌处理
- **精确费用模型**：佣金万2.5+最低5元、印花税0.05%、过户费0.001%

### 🎯 五档信号系统
| 信号 | 语义 | 默认目标仓位 |
|------|------|-------------|
| 买入 | 强烈看多，建仓或加仓 | 100% |
| 增持 | 看多但非强信号 | 当前+25% |
| 减仓 | 降低风险暴露 | 当前-25% |
| 卖出 | 清仓离场 | 0% |
| 观望 | 维持现状 | 不变 |

### 🔄 模拟交易闭环
完整 6 表体系：**委托 → 成交 → 持仓 → 流水 → 净值快照**
- T+1 解锁机制（次日9:25自动解锁）
- 支持限价/市价/收盘价多种撮合模式
- 资金守恒校验，可审计可重放

### 📈 多因子选股
- 因子分类：估值、成长、质量、动量、波动、技术
- IC / IR 分析评估因子有效性
- 横截面打分排名 + 权重可配置
- M5 已完成：内置 8 个因子、`factor_definitions.enabled/default_weight` 配置、Top N 排行榜、IC/IR 写入、Celery/API 任务入口与前端 `/factor` 页面 MVP

### 🔴 实时行情推送
- 东方财富 WebSocket 自建解析器
- Redis Pub/Sub 广播 + 前端实时刷新
- 支持 AllTick 作为备用通道

### 🐳 Docker 一键部署
```bash
docker compose up -d   # 7个服务一键启动
```

---

## 🛠️ 技术栈

### 前端
| 技术 | 用途 |
|------|------|
| React 18 + Vite | 核心框架与构建工具 |
| Tailwind CSS + shadcn/ui | 原子化CSS与组件库 |
| TradingView Lightweight Charts | K线图、净值曲线 |
| Monaco Editor | Python策略编辑器 |

### 后端
| 技术 | 用途 |
|------|------|
| FastAPI + Pydantic v2 | 异步REST API与数据验证 |
| SQLAlchemy 2.0 (async) + Alembic | ORM与数据库迁移 |
| Celery + Redis | 异步任务队列与定时调度 |

### 数据存储
| 组件 | 说明 |
|------|------|
| PostgreSQL 15+ | 统一持久化存储（26张表，按年分区） |
| Redis 7 | Celery Broker / 缓存 / PubSub |

### 计算引擎
| 引擎 | 用途 |
|------|------|
| Hikyuu (C++) | A股高性能回测内核 |
| MyTT | 通达信/同花顺兼容指标库（28个函数） |

### 数据源
| 层级 | 数据源 | 主要用途 |
|------|--------|---------|
| Tier1 | AData | 股票列表、日K线（主源） |
| Tier2 | Baostock | K线备源、财务三表、基本面 |
| Tier3 | AkShare | 分钟线、交易日历、兜底 |

---

## 🚀 快速开始

### 前置要求
- [Docker](https://docs.docker.com/get-docker/) & Docker Compose
- Git
- ≥8GB 可用磁盘空间（PostgreSQL数据）

### 1️⃣ 克隆仓库
```bash
git clone https://github.com/your-org/leek-quant.git
cd leek-quant
```

### 2️⃣ 配置环境变量
```bash
cp .env.example .env
# 编辑 .env 设置以下关键配置：
# - POSTGRES_PASSWORD: PostgreSQL 密码
# - DATABASE_URL: 本机后端/测试连接 PostgreSQL
# - CONTAINER_DATABASE_URL: Compose 内 backend/celery 连接 PostgreSQL
# - REDIS_URL: Celery/缓存连接 Redis
```

### 3️⃣ 启动服务
```bash
docker compose up -d
```
这将启动 6 个服务：`postgres`、`redis`、`backend`、`celery_worker`、`celery_beat`、`frontend`

### 4️⃣ 访问应用
| 服务 | 地址 |
|------|------|
| 🖥️ 前端界面 | http://localhost:8080 |
| 📚 API文档(Swagger) | http://localhost:8000/api/docs |
| 📖 API文档(ReDoc) | http://localhost:8000/api/redoc |
| 🐘 PostgreSQL | localhost:5432 |
| ⚡ Redis | localhost:6379 |

---

## 📁 项目结构

```
leek-quant/
├── backend/                      # FastAPI 后端
│   ├── app/
│   │   ├── api/                 # REST API 路由
│   │   │   ├── data.py          # 行情 API
│   │   │   ├── strategies.py    # 策略 CRUD
│   │   │   ├── backtests.py     # 回测任务
│   │   │   ├── watchlist.py     # 自选股管理
│   │   │   └── factors.py       # M5: 因子定义/值/排行榜/ICIR查询
│   │   ├── backtest/            # M3: 回测引擎
│   │   │   ├── adapter.py       # Hikyuu 适配层
│   │   │   ├── cost.py          # A股费用计算
│   │   │   └── signals.py       # 五档信号状态机
│   │   ├── data/                # M1/M2: 数据层
│   │   │   ├── providers.py     # 三层数据源
│   │   │   ├── normalizers.py   # 数据标准化
│   │   │   └── validators.py    # 数据校验
│   │   ├── factor/              # M5: 多因子计算、标准化、ICIR分析
│   │   ├── core/                # 配置与工具
│   │   ├── libs/                # 第三方库
│   │   │   └── MyTT.py          # 指标库(28函数)
│   │   └── tasks/               # Celery 异步任务
│   │       ├── celery_app.py    # Celery 应用配置
│   │       ├── data_tasks.py    # 数据拉取任务
│   │       └── factor_tasks.py  # M5: 因子计算/分析任务
│   ├── alembic/                 # 数据库迁移
│   │   └── versions/            # 迁移版本(M0-M5)
│   ├── tests/                   # 测试套件
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/                     # React 前端
│   └── src/
│       ├── pages/               # 页面组件
│       │   ├── MarketPage.tsx   # 股票池页面
│       │   ├── StrategyPage.tsx # 策略编辑器
│       │   ├── BacktestPage.tsx # 回测结果
│       │   └── WatchlistPage.tsx# 自选股
│       └── App.tsx              # 路由配置
├── docs/                         # 设计文档
│   └── finally-design.md         # 完整技术架构
├── docker-compose.yml            # 服务编排(7个服务)
├── .env.example                  # 环境变量模板
└── README.md                     # 本文件
```

---

## 🗺️ 开发里程碑

| 阶段 | 状态 | 核心交付物 |
|------|------|-----------|
| **M0 基础环境** | ✅ 完成 | Docker Compose + PostgreSQL + Redis + FastAPI 骨架 |
| **M1 数据基座** | ✅ 完成 | 股票列表 + 日K线(年分区) + 交易日历 + 三层回退 |
| **M2 股票池与自选股** | ✅ 完成 | 动态筛选(ST/退市/行业) + 分组管理 + 基础前端 |
| **M3 策略与回测** | ✅ **完成** | Monaco编辑器 + MyTT补全 + Hikyuu异步回测 |
| **M4 信号与模拟交易** | 🚧 开发中 | 五档信号状态机 + 6表闭环 + T+1解锁 |
| **M5 多因子选股** | ✅ 完成 | 因子四表 + 8个内置因子 + 计算/排行榜/ICIR/API/任务 + 前端因子页 MVP |
| **M6 实时行情** | 📋 规划中 | 东方财富WebSocket + Redis广播 |
| **M7 优化完善** | 📋 规划中 | 监控告警 + 参数敏感性 + 文档完善 |

---

## 🇨🇳 A股特色规则

本平台针对A股市场特性做了深度适配：

### 交易制度
| 规则 | 说明 | 平台实现 |
|------|------|---------|
| **T+1** | 当日买入次日方可卖出 | `available_shares` 次日9:25自动解锁 |
| **涨跌停限制** | 主板±10%、ST±5%、创业板/科创板±20% | 下单前自动校验，拦截违规委托 |
| **最小交易单位** | 100股(1手) | 自动向下取整至100的倍数 |
| **红涨绿跌** | 中国市场视觉规范 | 所有图表/标签统一红涨绿跌配色 |

### 精确费用模型
```python
# 默认费率配置
commission_rate = 0.00025    # 佣金万2.5
min_commission = 5.0        # 最低5元
stamp_tax_rate = 0.0005     # 印花税卖出0.05%
transfer_fee_rate = 0.00001 # 过户费万0.1

# 示例：买入10000元股票
佣金 = max(10000 * 0.00025, 5.0)  # = 5.0元（触发最低佣金）
印花税 = 0                          # 买入不收
过户费 = 10000 * 0.00001           # = 0.1元
总费用 = 5.1元
```

### 特殊场景处理
- **停牌日**：保留记录，价格沿用前收盘价，成交量=0
- **ST/退市**：独立标识，涨跌幅限制不同，可配置是否排除
- **复权**：支持前复权/后复权/不复权三种模式

---

## 🗄️ 数据库设计

### 架构概览
- **26张表**统一存储在 PostgreSQL 15+
- K线表按**年份分区**（`daily_kline_2020` ~ `daily_kline_2026`）
- 金融字段使用 `NUMERIC` 类型避免浮点误差
- 大对象使用 `JSONB` 存储（策略参数、回测曲线等）

### 核心表清单
| 表名 | 用途 | 特殊设计 |
|------|------|---------|
| `stock_basic` | 股票基础信息(5000+) | ST/退市标识、市场分类 |
| `daily_kline` | 日K线（按年分区） | 复权因子、涨停/跌停标记 |
| `trade_calendar` | 交易日历 | 前后交易日索引 |
| `strategies` | 策略定义 | 源码存储、版本管理 |
| `backtest_results` | 回测结果 | 性能指标JSONB、净值曲线 |
| `signal_log` | 五档信号日志 | 目标仓位、动作映射 |
| `sim_accounts` | 模拟账户 | 多账户隔离 |
| `sim_positions` | 当前持仓 | T+1可用股数字段 |
| `sim_orders` | 委托单 | 冻结资金/持仓 |
| `sim_trades` | 成交记录 | 明细费用分项 |
| `sim_cash_flow` | 资金流水 | 正负入出账 |
| `sim_daily_nav` | 每日净值快照 | 累计净值、最大回撤 |
| `factor_definitions` | 因子定义 | 表达式、方向、权重 |
| `factor_values` | 因子值 | 原始值、标准化值、百分位 |
| `scoring_rank` | 打分排名 | 总分、排名、因子分解 |
| `factor_analysis` | IC/IR分析 | IC均值/标准差/IR |

---

## 📡 API文档

启动服务后访问交互式API文档：

- **Swagger UI**: http://localhost:8000/api/docs
- **ReDoc**: http://localhost:8000/api/redoc

### 主要API端点
| 模块 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 行情 | GET | `/api/stocks` | 全市场股票列表（支持筛选） |
| 行情 | GET | `/api/stocks/{ts_code}/klines` | K线查询（日/周/月） |
| 自选股 | GET/POST | `/api/watchlist` | 自选股CRUD |
| 策略 | GET/POST | `/api/strategies` | 策略管理 |
| 回测 | POST | `/api/backtests` | 提交异步回测任务 |
| 回测 | GET | `/api/backtests/{id}` | 查询回测结果 |
| 信号 | GET | `/api/signals` | 五档信号日志 |
| 模拟交易 | GET/POST | `/api/sim/accounts` | 模拟账户管理 |
| 模拟交易 | GET | `/api/sim/accounts/{id}/positions` | 持仓查询 |
| 模拟交易 | GET | `/api/sim/accounts/{id}/nav` | 净值曲线 |
| 因子 | GET | `/api/factors` | 因子定义与默认权重 |
| 因子 | GET | `/api/factors/rank?page_size=N` | 多因子 Top N 排行榜 |
| 因子 | GET | `/api/factors/values` | 单因子横截面值 |
| 因子 | GET | `/api/factors/analysis` | 因子 IC/IR 分析结果 |
| 任务 | POST | `/api/tasks/factors/compute` | 触发单日因子计算 |
| 任务 | POST | `/api/tasks/factors/analyze` | 触发因子 IC/IR 分析 |
| 系统 | GET | `/api/system/tasks` | 任务运行状态 |
| WebSocket | WS | `/ws/realtime` | 实时行情订阅 |
| WebSocket | WS | `/ws/tasks` | 任务状态推送 |

---

## 🧪 测试

默认集成测试会真实连接 PostgreSQL 与 Redis，不做 skip。运行前确保 `.env` 中的 `DATABASE_URL` / `REDIS_URL` 可达，或先启动依赖：

```bash
docker compose up -d postgres redis
```

```bash
# 运行全部测试
pytest backend/tests/ -v

# 运行特定模块测试
pytest backend/tests/test_data_normalizers.py -v   # 数据标准化
pytest backend/tests/test_m2_stock_management.py -v # 股票池管理
pytest backend/tests/test_repository.py -v          # 数据仓库
pytest backend/tests/test_tasks.py -v               # Celery任务
pytest backend/tests/test_m5_factors.py backend/tests/test_factor_api.py backend/tests/test_factor_tasks.py backend/tests/test_m5_factor_migration.py -v  # M5因子专项
pytest backend/tests/test_m5_integration.py -v      # M5真实PostgreSQL/Alembic/任务闭环集成

# 运行覆盖率报告
pytest backend/tests/ --cov=app --cov-report=html
```

M5 风险消除验证组合：

```bash
./.venv/bin/python -m pytest backend/tests/test_m5_factors.py backend/tests/test_factor_api.py backend/tests/test_factor_tasks.py backend/tests/test_m5_factor_migration.py
./.venv/bin/python -m pytest backend/tests/test_m5_integration.py
cd frontend && npm run typecheck && npm run build && npm run test:smoke
```

### 关键测试覆盖
- ✅ 数据源三层回退与标准化
- ✅ A股费用精确计算（佣金/印花税/过户费）
- ✅ 五档信号状态机（空仓/半仓/满仓）
- ✅ T+1解锁机制
- ✅ 涨跌停校验逻辑
- ✅ 模拟交易资金守恒
- ✅ Hikyuu回测适配层
- ✅ M5 因子四表迁移、幂等 seed、单日计算、DB 权重排名、Top N API、IC/IR upsert、Celery任务入口、前端因子页 MVP

---

## 🤝 贡献指南

我们欢迎社区贡献！请遵循以下流程：

1. **Fork** 本仓库
2. 创建特性分支：`git checkout -b feature/amazing-feature`
3. 提交更改：`git commit -m 'feat: add amazing feature'`
4. 推送分支：`git push origin feature/amazing-feature`
5. 提交 **Pull Request**

### 代码规范
- **风格**: PEP 8 + Black + isort
- **提交信息**: [Conventional Commits](https://www.conventionalcommits.org/)
  - `feat:` 新功能
  - `fix:` Bug修复
  - `docs:` 文档更新
  - `test:` 测试相关
  - `refactor:` 重构
- **测试要求**: 新增功能必须包含配套单元测试

---

## ⚠️ 免责声明

> **重要提示**

- 本平台**仅用于**量化研究、历史回测和模拟交易训练
- **不提供任何投资建议**，所有策略由用户自行研发和验证
- **不支持实盘自动交易**，模拟交易结果不代表实盘表现
- 用户应自行确认各数据源的使用条款和服务协议
- A股交易规则可能变化，请以交易所最新规定为准
- 使用本平台产生的任何投资决策后果由用户自行承担

---

## 📄 许可证

本项目采用 **MIT License** 开源（待最终确定）

```
Copyright (c) 2026 Leek Quant Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
```

---

## 🙏 致谢

感谢以下开源项目和社区：

- **[Hikyuu Team](https://github.com/fasiondog/hikyuu)** — C++高性能回测引擎，A股规则完美支持
- **[MyTT 作者](https://github.com/chenggepc/MyTT)** — 通达信/同花顺兼容指标库，28个常用函数
- **[QuantDinger](https://github.com/quantdigger/QuantDinger)** — 架构参考母版，FastAPI/React/Celery骨架
- **[TradingView](https://www.tradingview.com/lightweight-charts/)** — Lightweight Charts 图表库
- **[shadcn/ui](https://ui.shadcn.com/)** — 高质量React组件库
- **AData / Baostock / AkShare** — 免费A股数据源
- **东方财富** — WebSocket实时行情数据

---

## 📊 系统架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    前端层 (React + Vite)                      │
│  Dashboard │ Market │ StrategyEditor │ KLineChart │ SimUI    │
└─────────────────────┬───────────────────────────────────────┘
                      │ HTTP / WebSocket
┌─────────────────────▼───────────────────────────────────────┐
│                   API层 (FastAPI)                            │
│  Auth │ DataAPI │ StrategyAPI │ SignalAPI │ WebSocket GW     │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│              异步任务层 (Celery + Redis)                      │
│  DataWorker │ BacktestWorker │ FactorWorker │ SimWorker      │
└──────┬──────────────┬──────────────┬──────────────┬─────────┘
       │              │              │              │
┌──────▼──────┐ ┌────▼─────┐ ┌──────▼────┐ ┌──────▼──────┐
│   AData     │ │  Hikyuu  │ │   MyTT    │ │ EastMoney   │
│  Baostock   │ │ (C++内核)│ │ (指标库)   │ │ (WebSocket) │
│  AkShare    │ │          │ │           │ │             │
└──────┬──────┘ └────┬─────┘ └──────┬────┘ └──────┬───────┘
       │              │              │              │
┌──────▼──────────────▼──────────────▼──────────────▼───────┐
│                    存储层                                  │
│  PostgreSQL (26张表, 年分区)  │  Redis (队列/缓存/PubSub)  │
└───────────────────────────────────────────────────────────┘
```

---

<p align="center">
  <strong>🌱 Leek Quant — 让每个韭菜都能做专业量化研究</strong>
</p>

<p align="center">
  <sub>Built with ❤️ for Chinese A-Share investors</sub>
</p>
