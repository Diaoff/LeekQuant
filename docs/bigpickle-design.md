# Leek Quant 开发架构文档

> **版本**：v1.0（BigPickle 综合版）
> **设计原则**：本地优先 · 隐私至上 · 专注A股 · 深度复用成熟开源 · 极少自研
> **基础框架**：QuantDinger（裁剪非A股部分，保留前后端/部署骨架）
> **设计哲学**：最大化复用 Hikyuu、MyTT、AData、Baostock、AkShare、Qlib，最小化自研

---

## 目录

1. [项目概述](#1-项目概述)
2. [技术栈总览](#2-技术栈总览)
3. [系统架构图](#3-系统架构图)
4. [PostgreSQL 完整表设计](#4-postgresql-完整表设计)
5. [数据源多层回退与增量拉取](#5-数据源多层回退与增量拉取)
6. [回测引擎集成方案（Hikyuu）](#6-回测引擎集成方案hikyuu)
7. [五档信号生成逻辑与状态机](#7-五档信号生成逻辑与状态机)
8. [模拟交易引擎工作流](#8-模拟交易引擎工作流)
9. [多因子打分模块设计](#9-多因子打分模块设计)
10. [前端页面与组件规划](#10-前端页面与组件规划)
11. [Docker Compose 部署配置](#11-docker-compose-部署配置)
12. [开发里程碑](#12-开发里程碑)
13. [风险与应对措施](#13-风险与应对措施)
14. [开源项目集成总结](#14-开源项目集成总结)

---

## 1. 项目概述

### 1.1 定位

Leek Quant 是面向个人投资者和小型量化团队的**纯A股量化交易平台**，从 QuantDinger（多市场AI量化平台）做减法，仅保留并深化A股特有能力：

- **本地优先、隐私至上**：数据、策略、账户完全保留在用户本地
- **Docker Compose 一键部署**：开箱即用
- **最大化复用开源**：深度集成 Hikyuu、MyTT、AData、Baostock、AkShare、Qlib

### 1.2 核心差异化能力

| 能力 | 说明 |
|------|------|
| **A股规则内置** | T+1、涨跌停、印花税/佣金/过户费、停牌处理完整覆盖 |
| **五档操作信号** | 买入/增持/减仓/卖出/观望，配合状态机自动映射实际操作 |
| **多因子选股** | 估值/成长/质量/动量四类因子，IC/IR 分析，打分排名 |
| **完整模拟交易** | 委托→成交→持仓→流水→净值快照，全链路6表体系 |
| **三层数据兜底** | AData→Baostock→AkShare 自动回退，数据不中断 |
| **高性能回测** | Hikyuu C++内核，百万K线秒级回测 |

---

## 2. 技术栈总览

| 层级 | 技术选型 | 说明 |
|------|---------|------|
| **前端框架** | React 18 + Vite + TypeScript | 现代SPA，热模块替换 |
| **UI组件库** | Tailwind CSS + shadcn/ui | 设计一致，高度可定制 |
| **K线图表** | TradingView Lightweight Charts | 专业金融图表，Canvas渲染 |
| **代码编辑器** | Monaco Editor | VSCode同源，MyTT 自动补全 |
| **状态管理** | Zustand | 轻量简洁 |
| **后端框架** | FastAPI (Python 3.11+) | 高性能异步，自动 OpenAPI 文档 |
| **ORM** | SQLAlchemy 2.0 (async) + Alembic | 异步支持，迁移管理 |
| **数据库** | **PostgreSQL 15+** | 统一存储，分区表+JSONB+窗口函数 |
| **消息队列** | Celery + Redis | 回测/因子计算/数据拉取 异步执行 |
| **实时推送** | Redis Pub/Sub + FastAPI WebSocket | 行情广播，前端实时刷新 |
| **回测引擎** | **Hikyuu** | C++内核，Python绑定，原生A股规则 |
| **技术指标** | **MyTT** | 通达信/同花顺兼容，单文件零依赖，策略内直接 `import` |
| **历史数据** | AData(Tier1) + Baostock(Tier2) + AkShare(Tier3) | 三层免费回退 |
| **实时行情** | 东方财富 WebSocket（自建解析） | 低延迟推送，Redis 广播 |
| **因子框架** | 参考 Qlib 表达式范式 | 轻量复刻，存入 PostgreSQL |
| **部署** | Docker Compose | 一键启动全部服务 |

---

## 3. 系统架构图

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          前端层 (React + Vite)                            │
│  ┌──────────┐ ┌──────────┐ ┌───────────────────┐ ┌──────────────────┐  │
│  │自选股     │ │股票池管理 │ │策略编辑器          │ │回测报告/因子榜    │  │
│  │实时看板   │ │动态筛选   │ │Monaco + MyTT提示   │ │TradingView图表   │  │
│  └──────────┘ └──────────┘ └───────────────────┘ └──────────────────┘  │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │ HTTP REST / WebSocket
┌────────────────────────────────┴─────────────────────────────────────────┐
│                          API层 (FastAPI)                                  │
│  ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌──────────────────────────┐  │
│  │用户/认证  │ │数据查询API │ │策略CRUD   │ │回测/信号/模拟交易API      │  │
│  └──────────┘ └────────────┘ └──────────┘ └──────────────────────────┘  │
└──────────────┬───────────────────────────────┬────────────────────────────┘
               │ 提交异步任务                   │ 直接调用（同步查询）
               ▼                               ▼
┌──────────────────────────────┐   ┌──────────────────────────────────────┐
│     Celery Workers            │   │   计算内核                            │
│  ┌─────────────────────────┐  │   │  ┌───────────────────────────────┐  │
│  │ 数据拉取 Worker          │  │   │  │ Hikyuu（C++回测引擎）          │  │
│  │  - 历史K线增量拉取       │  │   │  │ - A股 T+1、涨跌停、费用模型   │  │
│  │  - 交易日历更新          │  │   │  │ - 策略回测 + 绩效报告        │  │
│  │  - 基本面数据同步        │  │   │  └───────────────────────────────┘  │
│  ├─────────────────────────┤  │   │  ┌───────────────────────────────┐  │
│  │ 回测 Worker (backtest)  │  │   │  │ MyTT（技术指标库）              │  │
│  │  - Hikyuu 适配层调用    │  │   │  │ - 通达信/同花顺兼容            │  │
│  │  - 多股票并行回测       │  │   │  │ - 策略代码内直接 import        │  │
│  ├─────────────────────────┤  │   │  └───────────────────────────────┘  │
│  │ 因子 Worker (factor)    │  │   │  ┌───────────────────────────────┐  │
│  │  - 多因子计算/标准化    │  │   │  │ Qlib 因子范式（轻量复刻）       │  │
│  │  - IC/IR 分析           │  │   │  │ - 因子表达式引擎               │  │
│  │  - 打分排名生成         │  │   │  │ - 因子值→IC/IR→排名          │  │
│  ├─────────────────────────┤  │   │  └───────────────────────────────┘  │
│  │ 模拟交易 Worker         │  │   │                                     │
│  │  - 信号→委托→撮合      │  │   │                                     │
│  │  - T+1/T+0 处理        │  │   │                                     │
│  │  - 每日净值快照生成     │  │   └──────────────────────────────────────┘
│  └─────────────────────────┘  │
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                              存储层                                       │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  PostgreSQL 15+                                                    │  │
│  │  - 市场数据: stock_basic / daily_kline(分区) / trade_calendar     │  │
│  │  - 基本面: stock_fundamentals (JSONB 三表)                        │  │
│  │  - 用户策略: users / watchlist / stock_pools / strategies        │  │
│  │  - 回测信号: backtest_results (JSONB) / signal_log               │  │
│  │  - 因子打分: factor_values / scoring_rank / factor_analysis      │  │
│  │  - 模拟交易6表: accounts / positions / orders / trades /         │  │
│  │                   cash_flow / daily_nav                           │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Redis 7                                                          │  │
│  │  - Celery Broker / Result Backend                                │  │
│  │  - 东方财富 WebSocket → Redis Pub/Sub → FastAPI WS → 前端       │  │
│  │  - 热点数据缓存（K线/基本面/因子值）                              │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
               ▲
               │ 数据采集（独立守护进程）
┌──────────────┴──────────────────────────────────────────────────────────┐
│                         数据源层                                         │
│                                                                          │
│  历史数据（三层回退）：                                                   │
│  Tier 1: AData ───────────→ 日/周/月K线、全市场股票列表                  │
│     ↓ 失败自动回退                                                       │
│  Tier 2: Baostock ────────→ 日K线备源 + 财务三表 + PE/PB/ROE            │
│     ↓ 失败自动回退                                                       │
│  Tier 3: AkShare ─────────→ 分钟K线、全市场补充数据（兜底）              │
│                                                                          │
│  实时行情（独立通道）：                                                   │
│  东方财富 WebSocket → 自建解析器 → Redis Pub/Sub → FastAPI WS → 前端   │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 4. PostgreSQL 完整表设计

### 4.1 设计原则

- **统一存储**：替代 QuantDinger 的 DuckDB+Parquet+SQLite 组合，简化运维
- **分区表**：`daily_kline` 按年 `PARTITION BY RANGE(trade_date)`
- **JSONB**：策略配置、回测曲线、财务报表等半结构化数据
- **ACID**：金融数据强一致性保障
- **user_id 隔离**：所有用户相关表带 user_id，支持多账户隔离
- **复合索引**：`(ts_code, trade_date)`、`(account_id, trade_date)` 等高频查询路径建索引

### 4.2 基础/市场数据（4张）

#### stock_basic — 股票基础信息

```sql
CREATE TABLE stock_basic (
    ts_code      VARCHAR(10) PRIMARY KEY,   -- '600000.SH'
    symbol       VARCHAR(6)  NOT NULL,       -- '600000'
    name         VARCHAR(30) NOT NULL,
    market       VARCHAR(10),               -- '主板' / '创业板' / '科创板'
    industry     VARCHAR(50),
    area         VARCHAR(20),
    list_date    DATE,
    delist_date  DATE,
    is_st        BOOLEAN     DEFAULT FALSE, -- ST/*ST 标记
    is_delisted  BOOLEAN     DEFAULT FALSE,
    updated_at   TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX idx_stock_basic_st ON stock_basic(is_st, is_delisted);
CREATE INDEX idx_stock_basic_industry ON stock_basic(industry);
```

#### daily_kline — 日线K线（按年分区）

```sql
CREATE TABLE daily_kline (
    ts_code      VARCHAR(10) NOT NULL,
    trade_date   DATE        NOT NULL,
    open         NUMERIC(12,3),
    high         NUMERIC(12,3),
    low          NUMERIC(12,3),
    close        NUMERIC(12,3),
    pre_close    NUMERIC(12,3),             -- 前收盘价（停牌处理用）
    volume       BIGINT,
    amount       NUMERIC(20,2),
    adj_factor   NUMERIC(12,6),             -- 复权因子
    is_suspended BOOLEAN     DEFAULT FALSE,
    data_source  VARCHAR(20) DEFAULT 'adata',
    PRIMARY KEY (ts_code, trade_date)
) PARTITION BY RANGE (trade_date);

-- 按年建分区（建议用 pg_partman 自动化）
CREATE TABLE daily_kline_2020 PARTITION OF daily_kline
    FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');
CREATE TABLE daily_kline_2021 PARTITION OF daily_kline
    FOR VALUES FROM ('2021-01-01') TO ('2022-01-01');
CREATE TABLE daily_kline_2022 PARTITION OF daily_kline
    FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');
CREATE TABLE daily_kline_2023 PARTITION OF daily_kline
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE daily_kline_2024 PARTITION OF daily_kline
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE daily_kline_2025 PARTITION OF daily_kline
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE daily_kline_2026 PARTITION OF daily_kline
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
```

#### trade_calendar — 交易日历

```sql
CREATE TABLE trade_calendar (
    cal_date       DATE    PRIMARY KEY,
    is_open        BOOLEAN NOT NULL,
    pretrade_date  DATE,                   -- 上一个交易日
    is_weekend     BOOLEAN DEFAULT FALSE,
    is_holiday     BOOLEAN DEFAULT FALSE
);
```

#### stock_fundamentals — 基本面数据

```sql
CREATE TABLE stock_fundamentals (
    ts_code            VARCHAR(10) NOT NULL,
    report_date        DATE        NOT NULL,
    pe_ttm             NUMERIC(10,2),
    pb                 NUMERIC(10,2),
    ps_ttm             NUMERIC(10,2),
    pcf_ttm            NUMERIC(10,2),
    roe                NUMERIC(10,4),
    roa                NUMERIC(10,4),
    market_cap         NUMERIC(16,2),
    float_market_cap   NUMERIC(16,2),
    dividend_yield     NUMERIC(10,4),
    revenue            NUMERIC(20,2),
    net_profit         NUMERIC(20,2),
    revenue_growth     NUMERIC(10,4),
    net_profit_growth  NUMERIC(10,4),
    gross_margin       NUMERIC(10,4),
    debt_to_equity     NUMERIC(10,4),
    current_ratio      NUMERIC(10,4),
    free_cash_flow     NUMERIC(16,2),
    income_statement   JSONB,
    balance_sheet      JSONB,
    cashflow_statement JSONB,
    PRIMARY KEY (ts_code, report_date)
);
```

### 4.3 用户与策略（5张）

```sql
CREATE TABLE users (
    id            SERIAL      PRIMARY KEY,
    username      VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(200) NOT NULL,
    created_at    TIMESTAMP   DEFAULT NOW()
);

CREATE TABLE watchlist (
    id         SERIAL      PRIMARY KEY,
    user_id    INTEGER     REFERENCES users(id) ON DELETE CASCADE,
    ts_code    VARCHAR(10) REFERENCES stock_basic(ts_code),
    group_name VARCHAR(50) DEFAULT '默认',
    added_at   TIMESTAMP   DEFAULT NOW(),
    UNIQUE(user_id, ts_code, group_name)
);

CREATE TABLE stock_pools (
    id          SERIAL      PRIMARY KEY,
    user_id     INTEGER     REFERENCES users(id) ON DELETE CASCADE,
    pool_name   VARCHAR(100) NOT NULL,
    description TEXT,
    filters     JSONB,
    created_at  TIMESTAMP   DEFAULT NOW(),
    updated_at  TIMESTAMP   DEFAULT NOW()
);

CREATE TABLE stock_pool_items (
    pool_id   INTEGER REFERENCES stock_pools(id) ON DELETE CASCADE,
    ts_code   VARCHAR(10) REFERENCES stock_basic(ts_code),
    added_at  TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (pool_id, ts_code)
);

CREATE TABLE strategies (
    id          SERIAL      PRIMARY KEY,
    user_id     INTEGER     REFERENCES users(id) ON DELETE CASCADE,
    name        VARCHAR(100) NOT NULL,
    description TEXT,
    code        TEXT        NOT NULL,   -- Python源码，可 import MyTT
    config      JSONB,                 -- 参数配置 JSON
    version     INTEGER     DEFAULT 1,
    status      VARCHAR(20) DEFAULT 'draft',
    pool_id     INTEGER     REFERENCES stock_pools(id),
    created_at  TIMESTAMP   DEFAULT NOW(),
    updated_at  TIMESTAMP   DEFAULT NOW()
);
```

### 4.4 回测与信号（2张）

```sql
CREATE TABLE backtest_results (
    id              SERIAL    PRIMARY KEY,
    strategy_id     INTEGER   REFERENCES strategies(id),
    user_id         INTEGER   REFERENCES users(id),
    pool_id         INTEGER   REFERENCES stock_pools(id),
    start_date      DATE,
    end_date        DATE,
    initial_capital NUMERIC(20,2),
    total_return    NUMERIC(12,4),
    annual_return   NUMERIC(12,4),
    sharpe_ratio    NUMERIC(8,4),
    max_drawdown    NUMERIC(8,4),
    annual_vol      NUMERIC(8,4),
    win_rate        NUMERIC(6,4),
    trade_count     INTEGER,
    equity_curve    JSONB,
    trade_records   JSONB,
    params_snapshot JSONB,
    task_status     VARCHAR(20) DEFAULT 'pending',
    error_message   TEXT,
    created_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX idx_backtest_strategy ON backtest_results(strategy_id);
CREATE INDEX idx_backtest_user ON backtest_results(user_id);

CREATE TABLE signal_log (
    id              BIGSERIAL   PRIMARY KEY,
    strategy_id     INTEGER     REFERENCES strategies(id),
    user_id         INTEGER     REFERENCES users(id),
    ts_code         VARCHAR(10) NOT NULL,
    trade_date      DATE        NOT NULL,
    signal          VARCHAR(10) CHECK (signal IN ('买入','增持','减仓','卖出','观望')),
    target_ratio    NUMERIC(5,4),
    current_ratio   NUMERIC(5,4),
    reason          TEXT,
    market_snapshot JSONB,
    created_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX idx_signal_log_date ON signal_log(trade_date DESC);
CREATE INDEX idx_signal_log_code ON signal_log(ts_code, trade_date DESC);
CREATE INDEX idx_signal_log_strategy ON signal_log(strategy_id, trade_date DESC);
```

### 4.5 因子与打分（3张）

```sql
CREATE TABLE factor_values (
    ts_code      VARCHAR(10),
    trade_date   DATE,
    factor_name  VARCHAR(50),
    value        NUMERIC(18,6),
    PRIMARY KEY (ts_code, trade_date, factor_name)
);

CREATE INDEX idx_factor_date ON factor_values(trade_date, factor_name);
CREATE INDEX idx_factor_name ON factor_values(factor_name, trade_date);

CREATE TABLE scoring_rank (
    trade_date       DATE,
    ts_code          VARCHAR(10),
    total_score      NUMERIC(12,4),
    rank             INTEGER,
    factor_breakdown JSONB,
    PRIMARY KEY (trade_date, ts_code)
);

CREATE INDEX idx_scoring_rank_date ON scoring_rank(trade_date, rank);

CREATE TABLE factor_analysis (
    id            SERIAL      PRIMARY KEY,
    factor_name   VARCHAR(50) NOT NULL,
    period_start  DATE,
    period_end    DATE,
    ic            NUMERIC(10,6),
    ic_mean       NUMERIC(10,6),
    ic_std        NUMERIC(10,6),
    ir            NUMERIC(10,6),
    ic_gt_0_pct   NUMERIC(6,4),
    details       JSONB,
    calc_at       TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX idx_factor_analysis_name ON factor_analysis(factor_name);
```

### 4.6 模拟交易（6张完整体系）

```sql
-- 1. 模拟账户
CREATE TABLE sim_accounts (
    id              SERIAL      PRIMARY KEY,
    user_id         INTEGER     REFERENCES users(id) ON DELETE CASCADE,
    strategy_id     INTEGER     REFERENCES strategies(id),
    name            VARCHAR(100) NOT NULL,
    initial_capital NUMERIC(20,2) NOT NULL,
    cash            NUMERIC(20,2) NOT NULL,
    frozen_cash     NUMERIC(20,2) DEFAULT 0,
    total_asset     NUMERIC(20,2),
    created_at      TIMESTAMP   DEFAULT NOW(),
    updated_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX idx_sim_accounts_user ON sim_accounts(user_id);

-- 2. 当前持仓
CREATE TABLE sim_positions (
    id               SERIAL      PRIMARY KEY,
    account_id       INTEGER     REFERENCES sim_accounts(id) ON DELETE CASCADE,
    ts_code          VARCHAR(10) NOT NULL,
    shares           INTEGER     NOT NULL,          -- 总持股数
    available_shares INTEGER     DEFAULT 0,          -- 可卖出股数（T+1限制）
    avg_cost         NUMERIC(12,3) NOT NULL,
    current_price    NUMERIC(12,3),
    market_value     NUMERIC(20,2),
    unrealized_pnl   NUMERIC(20,2),
    profit_rate      NUMERIC(10,4),
    updated_at       TIMESTAMP   DEFAULT NOW(),
    UNIQUE(account_id, ts_code)
);

CREATE INDEX idx_sim_positions_account ON sim_positions(account_id);

-- 3. 委托单
CREATE TABLE sim_orders (
    id            BIGSERIAL   PRIMARY KEY,
    account_id    INTEGER     REFERENCES sim_accounts(id),
    ts_code       VARCHAR(10) NOT NULL,
    direction     VARCHAR(4)  CHECK (direction IN ('买入','卖出')),
    order_type    VARCHAR(10) DEFAULT '限价',
    price         NUMERIC(12,3),
    volume        INTEGER     NOT NULL,
    filled_volume INTEGER     DEFAULT 0,
    status        VARCHAR(10) DEFAULT '未报',
    signal_id     BIGINT      REFERENCES signal_log(id),
    submit_time   TIMESTAMP   DEFAULT NOW(),
    cancel_time   TIMESTAMP
);

CREATE INDEX idx_sim_orders_account ON sim_orders(account_id, status);
CREATE INDEX idx_sim_orders_status ON sim_orders(status);

-- 4. 成交记录
CREATE TABLE sim_trades (
    id           BIGSERIAL   PRIMARY KEY,
    order_id     BIGINT      REFERENCES sim_orders(id),
    account_id   INTEGER     REFERENCES sim_accounts(id),
    ts_code      VARCHAR(10) NOT NULL,
    direction    VARCHAR(4)  CHECK (direction IN ('买入','卖出')),
    price        NUMERIC(12,3) NOT NULL,
    volume       INTEGER     NOT NULL,
    amount       NUMERIC(20,2),
    stamp_tax    NUMERIC(12,4) DEFAULT 0,
    commission   NUMERIC(12,4) DEFAULT 0,
    transfer_fee NUMERIC(12,4) DEFAULT 0,
    total_fee    NUMERIC(12,4),
    trade_time   TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX idx_sim_trades_account ON sim_trades(account_id);
CREATE INDEX idx_sim_trades_order ON sim_trades(order_id);

-- 5. 资金流水
CREATE TABLE sim_cash_flow (
    id               BIGSERIAL   PRIMARY KEY,
    account_id       INTEGER     REFERENCES sim_accounts(id),
    flow_type        VARCHAR(20),
    amount           NUMERIC(20,2),
    balance_after    NUMERIC(20,2),
    related_trade_id BIGINT      REFERENCES sim_trades(id),
    remark           TEXT,
    created_at       TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX idx_sim_cash_flow_account ON sim_cash_flow(account_id);

-- 6. 每日净值快照
CREATE TABLE sim_daily_nav (
    id             BIGSERIAL  PRIMARY KEY,
    account_id     INTEGER    REFERENCES sim_accounts(id),
    nav_date       DATE       NOT NULL,
    total_asset    NUMERIC(20,2),
    cash           NUMERIC(20,2),
    position_value NUMERIC(20,2),
    daily_return   NUMERIC(12,8),
    cumulative_nav NUMERIC(12,4),
    max_drawdown   NUMERIC(8,4),
    UNIQUE(account_id, nav_date)
);

CREATE INDEX idx_sim_daily_nav ON sim_daily_nav(account_id, nav_date DESC);
```

---

## 5. 数据源多层回退与增量拉取

### 5.1 三层数据源分工

| 层级 | 数据源 | 覆盖内容 | 特点 |
|------|--------|---------|------|
| **Tier 1** | **AData** | 日/周/月K线、全市场股票列表 | 专注A股，更新快，接口稳定 |
| **Tier 2** | **Baostock** | 日K线备源、财务三表、PE/PB/ROE | 基本面数据完整，免费 |
| **Tier 3** | **AkShare** | 分钟K线、全市场补充数据 | 覆盖面最广，社区维护，兜底 |
| **实时独立** | **东方财富 WebSocket** | 盘中实时行情 | 自建解析，不走历史数据通道 |

### 5.2 三层回退实现

```python
# backend/app/data/fetcher.py
import asyncio
import random
import logging
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)


class ChinaStockDataFetcher:
    """三层回退历史K线数据获取器"""

    def __init__(self):
        self._source_health = {"adata": True, "baostock": True, "akshare": True}

    async def fetch_kline(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
    ) -> Optional[pd.DataFrame]:
        """
        三层回退获取K线数据
        顺序：AData → Baostock → AkShare
        每源最多重试3次，指数退避
        """
        sources = [
            ("adata",    self._fetch_adata),
            ("baostock", self._fetch_baostock),
            ("akshare",  self._fetch_akshare),
        ]

        for source_name, fetch_func in sources:
            for attempt in range(3):
                try:
                    df = await fetch_func(ts_code, start_date, end_date, adjust)
                    if df is not None and len(df) > 0:
                        if source_name != "adata":
                            logger.warning(
                                f"[{ts_code}] 使用备用数据源: {source_name}"
                            )
                        return self._normalize(df, source_name)
                except Exception as e:
                    wait = (2 ** attempt) + random.uniform(0, 0.5)
                    logger.warning(
                        f"[{ts_code}] {source_name} 第{attempt+1}次失败: "
                        f"{e}，等待{wait:.1f}s"
                    )
                    await asyncio.sleep(wait)

            logger.error(f"[{ts_code}] {source_name} 连续失败，切换下一源")

        raise ValueError(
            f"[{ts_code}] 所有数据源均失败，请检查网络或数据源状态"
        )

    async def _fetch_adata(self, ts_code, start_date, end_date, adjust):
        import adata
        loop = asyncio.get_event_loop()
        symbol = ts_code.split('.')[0]
        df = await loop.run_in_executor(
            None,
            lambda: adata.stock.market.get_market(
                stock_code=symbol,
                start_date=start_date,
                end_date=end_date,
                k_type=1,
            ),
        )
        return df

    async def _fetch_baostock(self, ts_code, start_date, end_date, adjust):
        import baostock as bs
        loop = asyncio.get_event_loop()

        def _sync():
            bs.login()
            rs = bs.query_history_k_data_plus(
                ts_code.lower().replace('.', '.'),
                "date,open,high,low,close,volume,amount,adjustflag",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="2",
            )
            df = rs.get_data()
            bs.logout()
            return df

        return await loop.run_in_executor(None, _sync)

    async def _fetch_akshare(self, ts_code, start_date, end_date, adjust):
        import akshare as ak
        loop = asyncio.get_event_loop()
        symbol = ts_code.split('.')[0]
        df = await loop.run_in_executor(
            None,
            lambda: ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                adjust=adjust,
            ),
        )
        return df

    def _normalize(self, df: pd.DataFrame, source: str) -> pd.DataFrame:
        """统一字段名"""
        col_map = {
            "trade_date": "trade_date",
            "日期": "trade_date",
            "open": "open",
            "开盘": "open",
            "high": "high",
            "最高": "high",
            "low": "low",
            "最低": "low",
            "close": "close",
            "收盘": "close",
            "volume": "volume",
            "成交量": "volume",
            "amount": "amount",
            "成交额": "amount",
        }
        df = df.rename(columns=col_map)
        df["data_source"] = source
        return df[["trade_date", "open", "high", "low", "close",
                   "volume", "amount", "data_source"]]
```

### 5.3 增量拉取机制

```python
# backend/app/tasks/data_tasks.py
from celery import shared_task
from datetime import date, timedelta


@shared_task(bind=True, max_retries=3)
def incremental_kline_update(self):
    """每交易日 18:00 触发，增量更新全市场K线"""
    with get_session() as db:
        latest_dates = db.execute("""
            SELECT ts_code, MAX(trade_date) as last_date
            FROM daily_kline
            GROUP BY ts_code
        """).fetchall()

        today = get_latest_trade_date(db)

        fetcher = ChinaStockDataFetcher()
        for ts_code, last_date in latest_dates:
            if last_date >= today:
                continue
            try:
                df = fetcher.fetch_kline(
                    ts_code,
                    start_date=str(last_date + timedelta(days=1)),
                    end_date=str(today),
                )
                bulk_upsert_kline(db, ts_code, df)
            except Exception as e:
                logger.error(f"[{ts_code}] 增量更新失败: {e}")


def bulk_upsert_kline(db, ts_code: str, df: pd.DataFrame):
    """批量upsert，幂等写入"""
    from sqlalchemy.dialects.postgresql import insert
    from app.models import DailyKline

    stmt = insert(DailyKline).values(
        ts_code=ts_code,
        trade_date=df["trade_date"],
        open=df["open"],
        high=df["high"],
        low=df["low"],
        close=df["close"],
        volume=df["volume"],
        amount=df["amount"],
        data_source=df["data_source"],
    )
    stmt = stmt.on_conflict_do_nothing(
        constraint="daily_kline_pkey"
    )
    db.execute(stmt)
    db.commit()
```

**增量更新策略要点：**
- 记录每只股票 `daily_kline` 中的 `MAX(trade_date)`，仅拉取之后数据
- `INSERT ... ON CONFLICT DO NOTHING` 保证幂等，可安全重试
- 请求间隔至少 0.3s，避免触发反爬
- Celery Beat 在每个交易日 18:00 触发，非交易日自动跳过
- 停牌股票保留 `is_suspended=TRUE` 记录，不跳过

### 5.4 实时行情架构

```python
# backend/app/realtime/eastmoney_ws.py
import asyncio
import json
import time
import websockets
import redis.asyncio as aioredis


class EastMoneyWSParser:
    """东方财富 WebSocket 实时行情解析器"""

    WS_URL = "wss://push2.eastmoney.com/api/qt/stock/sse"

    def __init__(self, redis_url: str):
        self.redis = aioredis.from_url(redis_url)
        self.subscribed_codes: set[str] = set()

    async def start(self, stock_codes: list[str]):
        self.subscribed_codes = set(stock_codes)
        while True:
            try:
                await self._connect_and_stream()
            except Exception as e:
                logger.error(f"WebSocket 断线: {e}，3秒后重连...")
                await asyncio.sleep(3)

    async def _connect_and_stream(self):
        async with websockets.connect(self.WS_URL, ping_interval=30) as ws:
            await self._subscribe(ws)
            async for message in ws:
                tick = self._parse(message)
                if tick:
                    await self.redis.publish(
                        f"realtime:{tick['ts_code']}",
                        json.dumps(tick),
                    )

    def _subscribe(self, ws):
        """订阅代码（格式需按实际抓包调整）"""
        codes = ",".join(self.subscribed_codes)
        sub_msg = {"action": "sub", "codes": codes}
        return ws.send(json.dumps(sub_msg))

    def _parse(self, raw: str) -> dict | None:
        """解析东方财富推送（字段映射按实际抓包调整）"""
        try:
            data = json.loads(raw)
            return {
                "ts_code": data.get("f12"),
                "price": data.get("f2"),
                "change": data.get("f3"),
                "change_pct": data.get("f4"),
                "volume": data.get("f5"),
                "amount": data.get("f6"),
                "high": data.get("f15"),
                "low": data.get("f16"),
                "open": data.get("f17"),
                "pre_close": data.get("f18"),
                "ts": int(time.time() * 1000),
            }
        except Exception:
            return None
```

### 5.5 交易日历服务

```python
@shared_task
def update_trade_calendar():
    """每周更新交易日历，从 AkShare 获取"""
    import akshare as ak
    df = ak.tool_trade_date_hist_sina()
    with get_session() as db:
        for _, row in df.iterrows():
            db.execute("""
                INSERT INTO trade_calendar (cal_date, is_open)
                VALUES (:date, :open)
                ON CONFLICT (cal_date) DO UPDATE SET is_open = :open
            """, {"date": row["trade_date"], "open": row["is_open"]})
        db.commit()


def get_latest_trade_date(db) -> date:
    """获取最近的交易日"""
    row = db.execute("""
        SELECT cal_date FROM trade_calendar
        WHERE is_open = TRUE AND cal_date <= CURRENT_DATE
        ORDER BY cal_date DESC LIMIT 1
    """).fetchone()
    return row[0] if row else date.today()


def is_trade_day(db, d: date) -> bool:
    """判断是否交易日"""
    row = db.execute(
        "SELECT is_open FROM trade_calendar WHERE cal_date = :d",
        {"d": d},
    ).fetchone()
    return row[0] if row else False
```

---

## 6. 回测引擎集成方案（Hikyuu）

### 6.1 集成优势

| 特性 | 说明 |
|------|------|
| **C++ 内核** | pybind11 零拷贝调用，百万K线秒级回测 |
| **原生A股规则** | 内置 T+1、涨跌停、ST标记、分红除权处理 |
| **组件化设计** | Signal / MoneyManager / StopLoss / TradeCost 可拼装 |
| **Python 绑定** | 用户策略直接用 Python 编写 |
| **参数优化** | 内置网格搜索优化器 |

### 6.2 Hikyuu 适配层

```python
# backend/app/backtest/hikyuu_adapter.py
import hikyuu as hk
from hikyuu import crtTM, SYS_Simple, SM, Query
import asyncio
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=4)


class HikyuuBacktestAdapter:
    """PostgreSQL 数据 → Hikyuu 引擎 → 结果序列化"""

    def __init__(self, db_session):
        self.db = db_session

    async def run_async(self, config: dict) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(executor, self._run_sync, config)

    def _run_sync(self, config: dict) -> dict:
        strategy_code = config["strategy_code"]
        ts_codes = config["stock_pool"]
        start_date = config["start_date"]
        end_date = config["end_date"]
        initial_capital = config.get("initial_capital", 100000)

        # 1. 从 PostgreSQL 加载 K 线
        sm = self._build_stock_manager(ts_codes, start_date, end_date)

        # 2. 执行用户策略，提取 Signal 组件
        signal_obj = self._compile_user_signal(strategy_code)

        # 3. 构建 A 股交易环境
        tm = crtTM(
            init_cash=initial_capital,
            cost=hk.TC_FixedSpread(
                buy_cost=0.00025,
                sell_cost=0.00025,
                min_cost=5.0,
                stamp_tax=0.0005,
                transfer_fee=0.00001,
            ),
        )

        # 4. 逐股票运行回测
        results = {}
        for ts_code in ts_codes:
            try:
                stock = sm[ts_code]
                sys = SYS_Simple(
                    tm=crtTM(init_cash=initial_capital),
                    sg=signal_obj,
                )
                sys.run(stock, Query(start_date, end_date))
                results[ts_code] = self._extract_result(sys, tm)
            except Exception as e:
                results[ts_code] = {"error": str(e)}

        return self._aggregate_results(results, initial_capital)

    def _build_stock_manager(self, ts_codes, start_date, end_date):
        sm = SM
        for ts_code in ts_codes:
            klines = self.db.execute("""
                SELECT trade_date, open, high, low, close, volume, amount
                FROM daily_kline
                WHERE ts_code = :code
                  AND trade_date BETWEEN :start AND :end
                  AND is_suspended = FALSE
                ORDER BY trade_date
            """, {"code": ts_code, "start": start_date, "end": end_date}).fetchall()

            k_data = hk.KData()
            for row in klines:
                k_data.append(hk.KRecord(
                    row.trade_date, row.open, row.high,
                    row.low, row.close, row.volume, row.amount,
                ))
            sm.add_temp_stock(ts_code, k_data)
        return sm

    def _compile_user_signal(self, strategy_code: str):
        """
        安全执行用户策略代码，提取 Signal 对象。
        策略约定：定义 create_signal() 函数，返回 hikyuu.SignalBase 实例。
        示例：
            from MyTT import *
            from hikyuu import *
            def create_signal():
                ma5 = MA(CLOSE, 5)
                ma20 = MA(CLOSE, 20)
                return SG_Cross(ma5, ma20)
        """
        sandbox = {
            "hikyuu": hk,
            "__builtins__": {},
        }
        try:
            import MyTT
            sandbox["MyTT"] = MyTT
            sandbox.update({
                k: getattr(MyTT, k)
                for k in dir(MyTT) if not k.startswith('_')
            })
        except ImportError:
            pass

        exec(strategy_code, sandbox)
        if "create_signal" not in sandbox:
            raise ValueError("策略代码必须定义 create_signal() 函数")
        return sandbox["create_signal"]()

    def _extract_result(self, sys, tm) -> dict:
        perf = sys.tm.performance
        return {
            "total_return": float(perf.total_return),
            "annual_return": float(perf.annual_return),
            "sharpe_ratio": float(perf.sharpe_ratio),
            "max_drawdown": float(perf.max_drawdown),
            "win_rate": float(perf.win_rate),
            "trade_count": perf.trade_count,
            "equity_curve": [
                {
                    "date": str(r.datetime.date()),
                    "nav": float(r.total_assets / tm.init_cash),
                }
                for r in sys.tm.get_trade_list()
            ],
        }

    def _aggregate_results(self, results: dict, initial_capital: float) -> dict:
        return {
            "per_stock": results,
            "summary": {
                "initial_capital": initial_capital,
                "stock_count": len(results),
            },
        }
```

### 6.3 A股规则适配详解

| 规则 | Hikyuu 实现方式 | 补充说明 |
|------|----------------|---------|
| **T+1** | `System` 设置 `enable_t1=True` | 当日买入不可卖出 |
| **涨跌停** | 自动识别涨停/跌停价，过滤无效信号 | 一字板自动处理 |
| **印花税** | `TC_FixedSpread(stamp_tax=0.0005)` | 仅卖出时收取，2023年减半后 0.05% |
| **佣金** | `TC_FixedSpread(buy_cost=0.00025, sell_cost=0.00025, min_cost=5.0)` | 双向万2.5，最低5元 |
| **过户费** | `TC_FixedSpread(transfer_fee=0.00001)` | 双向0.001% |
| **停牌处理** | 过滤 `is_suspended=TRUE` 的交易日 | 数据加载时跳过 |
| **ST/退市** | `stock_basic.is_st` 标记，策略可过滤 | 回测时可排除ST |

### 6.4 异步回测任务流程

```
用户提交回测请求
    ↓
FastAPI 创建 backtest_results 记录 (status=pending)
    ↓
提交 Celery 任务到 backtest 队列（返回 task_id）
    ↓
Celery Worker 执行 HikyuuBacktestAdapter.run_async
    ├── 从 PostgreSQL 加载 K 线数据
    ├── 编译用户策略代码（提取 Signal 组件）
    ├── 配置 A 股交易环境（T+1/费用/涨跌停）
    ├── 逐股票运行回测
    └── 写入 backtest_results (status=done, equity_curve/trade_records)
    ↓
前端轮询 /api/backtest/{id}/status 获取结果
```

---

## 7. 五档信号生成逻辑与状态机

### 7.1 信号定义

```python
# backend/app/signal/types.py
from enum import Enum
from pydantic import BaseModel


class SignalType(str, Enum):
    BUY = "买入"
    ADD = "增持"
    REDUCE = "减仓"
    SELL = "卖出"
    WAIT = "观望"


class Signal(BaseModel):
    signal_type: SignalType
    ts_code: str
    trade_date: str
    target_ratio: float = 0.0   # 目标仓位 0.0~1.0
    reason: str = ""
    price: float = 0.0
```

### 7.2 状态机转移规则

**核心原则**：空仓时"增持"视为"买入"，持仓满时"买入"视为无操作。

| 当前仓位 | 买入 | 增持 | 观望 | 减仓 | 卖出 |
|---------|------|------|------|------|------|
| **0%（空仓）** | 买入→满仓 | **等同买入**→50% | 无操作 | 无操作 | 无操作 |
| **25%** | 加仓→100% | 加仓→50% | 无操作 | 无操作 | 清仓→0% |
| **50%** | 加仓→100% | 无操作 | 无操作 | 减仓→25% | 清仓→0% |
| **75%** | 加仓→100% | 无操作 | 无操作 | 减仓→50% | 清仓→0% |
| **100%（满仓）** | 无操作 | 无操作 | 无操作 | 减仓→50% | 清仓→0% |

### 7.3 状态机实现

```python
# backend/app/signal/state_machine.py

class SignalStateMachine:
    """五档信号状态机，根据当前持仓状态映射实际操作"""

    def __init__(self, current_ratio: float = 0.0):
        self.current_ratio = current_ratio

    def execute(self, signal: Signal) -> tuple[str, float]:
        """
        返回 (操作类型, 目标仓位)
        操作类型：BUY / SELL_PARTIAL / SELL_ALL / HOLD
        """
        s = signal.signal_type.value
        r = self.current_ratio
        t = signal.target_ratio

        if s == "观望":
            return "HOLD", r

        if r == 0.0:  # 空仓
            if s in ("买入", "增持"):
                target = t if t > 0 else (1.0 if s == "买入" else 0.5)
                return "BUY", target
            return "HOLD", 0.0

        # 有仓位
        if s == "买入":
            return ("BUY", min(t, 1.0)) if t > r else ("HOLD", r)
        elif s == "增持":
            new = min(r + 0.25, 1.0)
            return ("BUY", new) if new > r else ("HOLD", r)
        elif s == "减仓":
            new = max(t, r - 0.25, 0.0)
            return "SELL_PARTIAL", new
        elif s == "卖出":
            return "SELL_ALL", 0.0

        return "HOLD", r

    def apply_cn_rules(
        self, action: str, pos_date: str, today: str, is_limit_up: bool = False
    ) -> str:
        """A股规则过滤：T+1、涨跌停"""
        if action.startswith("SELL") and pos_date == today:
            return "HOLD"  # T+1：当日买入不可卖
        if is_limit_up and action == "BUY":
            return "HOLD"  # 涨停：买入可能无法成交
        return action
```

### 7.4 信号生成触发机制

```python
# backend/app/tasks/signal_tasks.py
import numpy as np


@shared_task
def generate_daily_signals(strategy_id: int, user_id: int):
    """每交易日 17:00 为策略生成信号"""
    with get_session() as db:
        strategy = db.query(Strategy).get(strategy_id)
        pool_codes = get_pool_stocks(db, strategy.pool_id)

        for ts_code in pool_codes:
            klines = get_klines(db, ts_code, days=250)
            np_close = np.array([k.close for k in klines])

            signal = run_strategy_code(
                strategy.code, ts_code, np_close, klines[-1]
            )
            if signal:
                db.add(SignalLog(
                    strategy_id=strategy_id,
                    user_id=user_id,
                    ts_code=ts_code,
                    trade_date=date.today(),
                    signal=signal.signal_type.value,
                    target_ratio=signal.target_ratio,
                    reason=signal.reason,
                    market_snapshot={
                        "close": float(klines[-1].close),
                        "volume": int(klines[-1].volume),
                        "ma5": float(np_close[-5:].mean()),
                        "ma20": float(np_close[-20:].mean()),
                    },
                ))
        db.commit()


def run_strategy_code(code: str, ts_code: str, np_close, last_k) -> Signal | None:
    """在隔离环境中执行用户策略代码"""
    sandbox = {
        "__builtins__": {},
        "ts_code": ts_code,
        "close": np_close,
        "np": np,
    }
    try:
        import MyTT
        sandbox["MyTT"] = MyTT
        sandbox.update({
            k: getattr(MyTT, k) for k in dir(MyTT) if not k.startswith('_')
        })
    except ImportError:
        pass

    sandbox_code = code + "\n\nsignal = generate_signal(ts_code, close)"
    exec(sandbox_code, sandbox)
    return sandbox.get("signal")
```

### 7.5 用户策略模板示例

```python
# 用户在前端 Monaco 编辑器中编写的策略示例
from MyTT import *
import numpy as np

def generate_signal(ts_code, close):
    """
    参数：
        ts_code: 股票代码
        close: numpy array, 最近250日收盘价
    返回：
        Signal 对象或 None（观望）
    """
    ma5 = MA(close, 5)[-1]
    ma20 = MA(close, 20)[-1]
    rsi = RSI(close, 6)[-1]
    current_price = close[-1]

    # 买入条件：5日均线上穿20日均线，RSI < 70
    if ma5 > ma20 and rsi < 70 and ma5 > close[-2] * 1.01:
        return Signal(
            signal_type=SignalType.BUY,
            ts_code=ts_code,
            trade_date=str(date.today()),
            target_ratio=1.0,
            reason=f"MA5({ma5:.2f})上穿MA20({ma20:.2f}), RSI({rsi:.1f})",
            price=current_price,
        )

    # 卖出条件：RSI > 80 超买
    if rsi > 80:
        return Signal(
            signal_type=SignalType.SELL,
            ts_code=ts_code,
            trade_date=str(date.today()),
            target_ratio=0.0,
            reason=f"RSI超买({rsi:.1f}>80)",
            price=current_price,
        )

    return None  # 观望
```

---

## 8. 模拟交易引擎工作流

### 8.1 整体流程

```
策略信号 (signal_log)
    ↓
模拟交易引擎 (Celery Task)
    ↓
┌──────────────────────────────────────────────────────────┐
│  规则校验层                                               │
│  ✓ 交易日检查（trade_calendar）                          │
│  ✓ T+1 校验（available_shares vs total_shares）         │
│  ✓ 涨跌停价格检查                                        │
│  ✓ 停牌检查（is_suspended）                              │
│  ✓ 资金/持仓充足性校验                                    │
└──────────────────────────────────────────────────────────┘
    ↓ 通过校验
创建委托单 (sim_orders, status='待成交')
    ↓
模拟撮合引擎
    ├── 限价单：price >= 卖一（买入）或 price <= 买一（卖出）时成交
    └── 市价单：以最新价/收盘价成交
    ↓ 成交
生成成交记录 (sim_trades)
    ├── 印花税 = amount × 0.0005（仅卖出）
    ├── 佣金 = max(amount × 0.00025, 5.0)（双向）
    └── 过户费 = amount × 0.00001（双向，沪市）
    ↓
更新持仓 (sim_positions)
    ├── 买入：增加 shares, avg_cost 加权平均, available_shares 次日才加
    └── 卖出：减少 available_shares 和 shares
    ↓
更新账户资金 (sim_accounts)
    ├── 买入：cash -= amount + fees
    └── 卖出：cash += amount - fees
    ↓
记录资金流水 (sim_cash_flow)
    ↓
（收盘后）生成净值快照 (sim_daily_nav)
    ├── total_asset = cash + Σ(shares × close_price)
    ├── daily_return = (total_asset / prev_total_asset) - 1
    └── cumulative_nav = cumulative_nav_prev × (1 + daily_return)
```

### 8.2 交易费用计算

```python
# backend/app/trading/cost_calculator.py

class AShareCostCalculator:
    """A股标准交易费用计算"""

    STAMP_TAX_RATE = 0.0005       # 印花税 0.05%（仅卖出）
    COMMISSION_RATE = 0.00025     # 佣金 万2.5（双向）
    MIN_COMMISSION = 5.0          # 最低佣金 5元
    TRANSFER_FEE_RATE = 0.00001   # 过户费 0.001%（沪市双向）

    def calculate(
        self, direction: str, price: float, volume: int, market: str = "SH"
    ) -> dict:
        amount = price * volume
        commission = max(amount * self.COMMISSION_RATE, self.MIN_COMMISSION)
        stamp_tax = amount * self.STAMP_TAX_RATE if direction == "卖出" else 0.0
        transfer_fee = amount * self.TRANSFER_FEE_RATE if market == "SH" else 0.0
        total_fee = commission + stamp_tax + transfer_fee

        return {
            "amount": amount,
            "commission": round(commission, 4),
            "stamp_tax": round(stamp_tax, 4),
            "transfer_fee": round(transfer_fee, 4),
            "total_fee": round(total_fee, 4),
            "net_amount": (
                amount + total_fee if direction == "买入" else amount - total_fee
            ),
        }
```

### 8.3 T+1 持仓管理

```python
def process_buy_trade(db, account_id, ts_code, volume, price, trade_date):
    """买入成交后更新持仓（T+1：当日买入不计入 available_shares）"""
    pos = db.query(SimPosition).filter_by(
        account_id=account_id, ts_code=ts_code
    ).first()

    if pos is None:
        pos = SimPosition(
            account_id=account_id,
            ts_code=ts_code,
            shares=0,
            available_shares=0,
            avg_cost=price,
        )
        db.add(pos)

    total_cost = pos.avg_cost * pos.shares + price * volume
    pos.shares += volume
    pos.avg_cost = total_cost / pos.shares
    pos.updated_at = datetime.now()
    db.commit()


def process_sell_trade(db, account_id, ts_code, volume, price):
    """卖出成交后更新持仓"""
    pos = db.query(SimPosition).filter_by(
        account_id=account_id, ts_code=ts_code
    ).first()

    if not pos or pos.available_shares < volume:
        raise ValueError("可卖持仓不足")

    pos.shares -= volume
    pos.available_shares -= volume
    if pos.shares == 0:
        pos.avg_cost = 0
    pos.updated_at = datetime.now()
    db.commit()


@shared_task
def unlock_t1_positions():
    """每交易日 09:25 解锁昨日买入的持仓"""
    yesterday = get_prev_trade_date(date.today())
    with get_session() as db:
        trades = db.query(SimTrade).filter(
            SimTrade.direction == "买入",
            func.date(SimTrade.trade_time) == yesterday,
        ).all()

        for trade in trades:
            pos = db.query(SimPosition).filter_by(
                account_id=trade.account_id, ts_code=trade.ts_code
            ).first()
            if pos:
                pos.available_shares += trade.volume
        db.commit()
```

### 8.4 核心撮合引擎

```python
# backend/app/trading/matching_engine.py

@shared_task(bind=True, max_retries=3)
def match_orders(self):
    """定时撮合：对所有 '待成交' 状态的委托进行撮合"""
    with get_session() as db:
        orders = db.query(SimOrder).filter(
            SimOrder.status.in_(["待成交", "部分成交"])
        ).all()

        calc = AShareCostCalculator()

        for order in orders:
            try:
                # 获取最新行情
                latest_k = get_latest_kline(db, order.ts_code)
                if not latest_k:
                    continue

                price = latest_k.close
                market = "SH" if order.ts_code.endswith(".SH") else "SZ"

                # 涨跌停检查
                if is_limit_up(latest_k) and order.direction == "买入":
                    continue  # 涨停，暂不成交
                if is_limit_down(latest_k) and order.direction == "卖出":
                    continue

                # 计算可成交量
                remaining = order.volume - order.filled_volume
                fee_info = calc.calculate(
                    order.direction, price, remaining, market
                )

                # 资金/持仓检查
                account = db.query(SimAccount).get(order.account_id)
                if order.direction == "买入":
                    if account.cash < fee_info["net_amount"]:
                        continue
                    account.cash -= fee_info["net_amount"]
                    account.frozen_cash -= fee_info["net_amount"]
                else:
                    pos = db.query(SimPosition).filter_by(
                        account_id=order.account_id, ts_code=order.ts_code
                    ).first()
                    if not pos or pos.available_shares < remaining:
                        continue
                    account.cash += fee_info["net_amount"]

                # 生成成交记录
                trade = SimTrade(
                    order_id=order.id,
                    account_id=order.account_id,
                    ts_code=order.ts_code,
                    direction=order.direction,
                    price=price,
                    volume=remaining,
                    amount=fee_info["amount"],
                    stamp_tax=fee_info["stamp_tax"],
                    commission=fee_info["commission"],
                    transfer_fee=fee_info["transfer_fee"],
                    total_fee=fee_info["total_fee"],
                )
                db.add(trade)

                # 更新持仓
                if order.direction == "买入":
                    process_buy_trade(
                        db, order.account_id, order.ts_code,
                        remaining, price, date.today(),
                    )
                else:
                    process_sell_trade(
                        db, order.account_id, order.ts_code,
                        remaining, price,
                    )

                # 记录资金流水
                flow_type = "买入" if order.direction == "买入" else "卖出"
                db.add(SimCashFlow(
                    account_id=order.account_id,
                    flow_type=flow_type,
                    amount=-fee_info["net_amount"]
                        if order.direction == "买入"
                        else fee_info["net_amount"],
                    balance_after=account.cash,
                    related_trade_id=trade.id,
                    remark=f"{order.ts_code} {order.direction} {remaining}股 @ {price}",
                ))

                # 更新委托单状态
                order.filled_volume += remaining
                order.status = "全部成交" if order.filled_volume >= order.volume else "部分成交"

            except Exception as e:
                logger.error(f"撮合失败 order_id={order.id}: {e}")

        db.commit()
```

---

## 9. 多因子打分模块设计

### 9.1 因子体系

| 因子类别 | 代表因子 | 数据来源 | 更新频率 |
|---------|---------|---------|---------|
| **估值** | PE_TTM、PB、PS_TTM、PCF、股息率 | Baostock 基本面 | 每季报 |
| **成长** | 营收增速、净利润增速、ROE 增速 | Baostock 财务三表 | 每季报 |
| **质量** | ROE、ROA、毛利率、资产负债率、现金流比 | Baostock 财务三表 | 每季报 |
| **动量** | 1M/3M/6M/12M 价格动量 | daily_kline + MyTT | 每交易日 |
| **技术** | RSI6、BIAS6、换手率 | daily_kline + MyTT | 每交易日 |
| **波动** | 20日波动率、最大回撤 | daily_kline | 每交易日 |

### 9.2 因子计算引擎（参考 Qlib 表达式范式）

```python
# backend/app/factor/engine.py
from MyTT import *
import numpy as np
import pandas as pd


FACTOR_REGISTRY = {
    # 技术/动量因子
    "RSI6":     lambda df: RSI(df["close"].values, N=6)[-1],
    "BIAS6":    lambda df: BIAS(df["close"].values, L=6)[-1],
    "MOM1M":    lambda df: df["close"].values[-1] / df["close"].values[-21] - 1,
    "MOM3M":    lambda df: df["close"].values[-1] / df["close"].values[-63] - 1,
    "MOM6M":    lambda df: df["close"].values[-1] / df["close"].values[-126] - 1,
    "VOL20":    lambda df: float(np.std(df["close"].pct_change().values[-20:])),
    "MA5_slope": lambda df: df["close"].values[-1] / df["close"].values[-5] - 1,
    # 估值因子（从基本面表取）
    "PE":       "fundamental_pe_ttm",
    "PB":       "fundamental_pb",
    "PS":       "fundamental_ps_ttm",
    "ROE":      "fundamental_roe",
    "DIV_YIELD": "fundamental_dividend_yield",
}


class FactorEngine:
    """多因子计算引擎"""

    def compute_technical_factors(self, ts_code: str, klines: pd.DataFrame) -> dict:
        """计算技术/动量因子"""
        results = {}
        for name, func in FACTOR_REGISTRY.items():
            if callable(func):
                try:
                    results[name] = float(func(klines))
                except Exception as e:
                    logger.warning(f"因子 {name} 计算失败 [{ts_code}]: {e}")
                    results[name] = None
        return results

    def normalize_and_score(
        self, factor_df: pd.DataFrame, weights: dict[str, float] | None = None
    ) -> pd.Series:
        """
        跨截面标准化 + 加权综合打分
        步骤：
        1. 去极值（Winsorize 1%/99%）
        2. Z-Score 标准化
        3. 方向调整（小PE好 → 取负）
        4. 加权求和 → 百分位排名
        """
        default_weights = {
            "PE_RANK": -1.0,
            "PB_RANK": -1.0,
            "ROE_RANK": 1.0,
            "RSI6": -0.5,
            "MOM3M": 1.0,
            "VOL20": -0.5,
        }
        w = weights or default_weights

        normalized = {}
        for col in factor_df.columns:
            s = factor_df[col].copy()
            lower, upper = s.quantile(0.01), s.quantile(0.99)
            s = s.clip(lower, upper)
            normalized[col] = (s - s.mean()) / (s.std() + 1e-8)

        score = sum(normalized[f] * w.get(f, 1.0) for f in normalized)
        return score.rank(pct=True)
```

### 9.3 IC/IR 分析

```python
# backend/app/factor/analysis.py

def compute_ic_ir(
    db, factor_name: str, start_date: str, end_date: str, forward_days: int = 5
) -> dict:
    """
    IC = corr(因子值, forward_days日后收益率)
    IR = IC_mean / IC_std
    """
    factor_df = pd.read_sql("""
        SELECT fv.ts_code, fv.trade_date, fv.value as factor_val,
               (dk_future.close / dk_now.close - 1) as forward_return
        FROM factor_values fv
        JOIN daily_kline dk_now
            ON fv.ts_code = dk_now.ts_code
            AND fv.trade_date = dk_now.trade_date
        JOIN daily_kline dk_future
            ON fv.ts_code = dk_future.ts_code
            AND dk_future.trade_date = (
                SELECT cal_date FROM trade_calendar
                WHERE cal_date > fv.trade_date AND is_open = TRUE
                ORDER BY cal_date LIMIT 1 OFFSET :fwd-1
            )
        WHERE fv.factor_name = :fname
          AND fv.trade_date BETWEEN :start AND :end
    """, db.bind, params={
        "fname": factor_name,
        "start": start_date,
        "end": end_date,
        "fwd": forward_days,
    })

    ic_series = factor_df.groupby("trade_date").apply(
        lambda g: g["factor_val"].corr(g["forward_return"])
    ).dropna()

    return {
        "factor_name": factor_name,
        "period_start": start_date,
        "period_end": end_date,
        "forward_days": forward_days,
        "ic_mean": float(ic_series.mean()),
        "ic_std": float(ic_series.std()),
        "ir": float(ic_series.mean() / (ic_series.std() + 1e-8)),
        "ic_gt_0_pct": float((ic_series > 0).mean()),
        "ic_count": len(ic_series),
    }


@shared_task
def compute_daily_factors():
    """每日 17:30 计算全市场因子值"""
    with get_session() as db:
        stock_codes = db.query(StockBasic.ts_code).filter(
            StockBasic.is_delisted == False
        ).all()

        engine = FactorEngine()
        today = date.today()

        for (ts_code,) in stock_codes:
            klines = get_klines_as_df(db, ts_code, days=250)
            if klines is None or len(klines) < 60:
                continue

            factors = engine.compute_technical_factors(ts_code, klines)
            for name, value in factors.items():
                if value is not None:
                    db.execute("""
                        INSERT INTO factor_values (ts_code, trade_date, factor_name, value)
                        VALUES (:code, :date, :name, :val)
                        ON CONFLICT (ts_code, trade_date, factor_name)
                        DO UPDATE SET value = :val
                    """, {"code": ts_code, "date": today, "name": name, "val": value})
        db.commit()
```

### 9.4 打分排名任务

```python
@shared_task
def generate_scoring_rank():
    """每周五收盘后生成全市场股票打分排名"""
    with get_session() as db:
        today = date.today()
        factor_names = ["RSI6", "MOM3M", "VOL20", "PE", "PB", "ROE"]

        # 加载所有因子值 → DataFrame
        rows = db.execute("""
            SELECT fv.ts_code, fv.factor_name, fv.value
            FROM factor_values fv
            WHERE fv.trade_date = (
                SELECT MAX(trade_date) FROM factor_values
                WHERE trade_date <= :today
            )
        """, {"today": today}).fetchall()

        df = pd.DataFrame(rows, columns=["ts_code", "factor_name", "value"])
        pivot = df.pivot(index="ts_code", columns="factor_name", values="value")

        # 计算得分
        engine = FactorEngine()
        pivot["score"] = engine.normalize_and_score(pivot)
        pivot["rank"] = pivot["score"].rank(ascending=False)

        # 写入 scoring_rank
        for ts_code, row in pivot.iterrows():
            db.execute("""
                INSERT INTO scoring_rank (trade_date, ts_code, total_score, rank, factor_breakdown)
                VALUES (:date, :code, :score, :rank, :breakdown)
                ON CONFLICT (trade_date, ts_code)
                DO UPDATE SET total_score = :score, rank = :rank, factor_breakdown = :breakdown
            """, {
                "date": today,
                "code": ts_code,
                "score": float(row.get("score", 0)),
                "rank": int(row.get("rank", 9999)),
                "breakdown": json.dumps({k: float(v) for k, v in row.items()
                                         if k not in ["score", "rank", "ts_code"]}),
            })
        db.commit()
```

---

## 10. 前端页面与组件规划

### 10.1 页面路由与功能

| 路由 | 页面名 | 核心功能 | 关键组件 |
|------|--------|---------|---------|
| `/` | **首页 Dashboard** | 大盘指数、自选股看板、今日信号摘要、模拟账户净值 | TradingView 迷你图、信号卡片 |
| `/market` | **股票池** | 全市场列表（ST/退市标识）、动态筛选器、股票详情 | 数据表格、筛选面板、K线图 |
| `/watchlist` | **自选股** | 分组管理（拖拽排序）、实时行情看板、批量操作 | 看板卡片、分组Tab、WebSocket |
| `/strategy` | **策略中心** | 策略列表、Monaco 编辑器、回测配置/结果 | Monaco Editor、参数面板、图表 |
| `/signals` | **信号中心** | 五档信号日志、筛选/统计、K线跳转 | 信号列表、状态标签、详情抽屉 |
| `/simulation` | **模拟交易** | 账户概览、持仓/委托/成交/流水、净值曲线 | 持仓表格、委托面板、净值图 |
| `/factor` | **因子选股** | 因子列表、IC/IR 图表、打分排行榜、权重配置 | 排行榜表格、雷达图、权重滑块 |
| `/settings` | **系统设置** | 数据更新状态、定时任务监控、多账户管理、告警 | 状态卡片、开关配置、日志 |

### 10.2 核心组件实现

#### Monaco 编辑器（策略编辑）

```typescript
// frontend/src/components/StrategyEditor.tsx
import Editor, { Monaco } from "@monaco-editor/react";

const MYTT_COMPLETIONS = [
  { label: "MA", detail: "MA(CLOSE, N) - 简单移动平均", insert: "MA(CLOSE, $1)" },
  { label: "EMA", detail: "EMA(CLOSE, N) - 指数移动平均", insert: "EMA(CLOSE, $1)" },
  { label: "MACD", detail: "MACD(CLOSE, SHORT, LONG, M) - MACD", insert: "MACD(CLOSE, $1, $2, $3)" },
  { label: "RSI", detail: "RSI(CLOSE, N) - 相对强弱指标", insert: "RSI(CLOSE, $1)" },
  { label: "BOLL", detail: "BOLL(CLOSE, N, P) - 布林带", insert: "BOLL(CLOSE, $1, $2)" },
  { label: "KDJ", detail: "KDJ(CLOSE, HIGH, LOW) - 随机指标", insert: "KDJ(CLOSE, HIGH, LOW)" },
  { label: "BIAS", detail: "BIAS(CLOSE, L) - 乖离率", insert: "BIAS(CLOSE, $1)" },
  { label: "ATR", detail: "ATR(CLOSE, HIGH, LOW, N) - 平均真实波幅", insert: "ATR(CLOSE, HIGH, LOW, $1)" },
  { label: "DMA", detail: "DMA(CLOSE, N1, N2) - 平行线差", insert: "DMA(CLOSE, $1, $2)" },
  { label: "TRIX", detail: "TRIX(CLOSE, N) - 三重指数平滑", insert: "TRIX(CLOSE, $1)" },
  { label: "VR", detail: "VR(CLOSE, VOLUME, N) - 成交量变异率", insert: "VR(CLOSE, VOLUME, $1)" },
  { label: "OBV", detail: "OBV(CLOSE, VOLUME) - 能量潮", insert: "OBV(CLOSE, VOLUME)" },
  { label: "SignalType", detail: "SignalType.BUY / .ADD / .REDUCE / .SELL / .WAIT" },
  { label: "Signal", detail: "Signal(signal_type, ts_code, trade_date, target_ratio, reason, price)" },
];

function setupMyTTCompletions(monaco: Monaco) {
  monaco.languages.registerCompletionItemProvider("python", {
    provideCompletionItems: (_model, position) => ({
      suggestions: MYTT_COMPLETIONS.map((item) => ({
        label: item.label,
        kind: monaco.languages.CompletionItemKind.Function,
        detail: item.detail,
        insertText: item.insert || item.label,
        insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
        range: {
          startLineNumber: position.lineNumber,
          startColumn: position.column,
          endLineNumber: position.lineNumber,
          endColumn: position.column,
        },
      })),
    }),
  });
}

export function StrategyEditor({ value, onChange }: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <Editor
      height="60vh"
      language="python"
      theme="vs-dark"
      value={value}
      onChange={(v) => onChange(v ?? "")}
      beforeMount={setupMyTTCompletions}
      options={{
        fontSize: 14,
        minimap: { enabled: false },
        suggestOnTriggerCharacters: true,
        automaticLayout: true,
      }}
    />
  );
}
```

#### TradingView K线图（信号叠加）

```typescript
// frontend/src/components/KLineChart.tsx
import { createChart, CrosshairMode, IChartApi } from "lightweight-charts";
import { useEffect, useRef } from "react";

interface KLineData {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

interface SignalPoint {
  trade_date: string;
  signal: string;
  price?: number;
}

export function KLineChart({ klineData, signals }: {
  klineData: KLineData[];
  signals: SignalPoint[];
}) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!chartRef.current) return;

    const chart = createChart(chartRef.current, {
      width: chartRef.current.clientWidth,
      height: 500,
      crosshair: { mode: CrosshairMode.Normal },
      layout: {
        background: { color: "#1a1a2e" },
        textColor: "#d1d4dc",
      },
      grid: {
        vertLines: { color: "#2d2d44" },
        horzLines: { color: "#2d2d44" },
      },
      timeScale: { timeVisible: true },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#f23645",
      downColor: "#089981",
      borderUpColor: "#f23645",
      borderDownColor: "#089981",
      wickUpColor: "#f23645",
      wickDownColor: "#089981",
    });

    candleSeries.setData(
      klineData.map((k) => ({
        time: k.time,
        open: k.open,
        high: k.high,
        low: k.low,
        close: k.close,
      }))
    );

    const markers = signals.map((sig) => ({
      time: sig.trade_date,
      position: (
        sig.signal === "买入" || sig.signal === "增持"
      ) ? "belowBar" as const : "aboveBar" as const,
      color: sig.signal === "买入"
        ? "#f23645"
        : sig.signal === "卖出"
        ? "#089981"
        : "#ffd700",
      shape: (
        sig.signal === "买入" || sig.signal === "增持"
      ) ? "arrowUp" as const : "arrowDown" as const,
      text: sig.signal,
    }));

    candleSeries.setMarkers(markers);
    chartInstance.current = chart;

    return () => chart.remove();
  }, [klineData, signals]);

  return <div ref={chartRef} className="w-full" />;
}
```

#### 实时行情 Hook（WebSocket）

```typescript
// frontend/src/hooks/useRealtimePrice.ts
import { useEffect, useState, useCallback } from "react";

interface TickData {
  ts_code: string;
  price: number;
  change_pct: number;
  volume: number;
  amount: number;
  high: number;
  low: number;
  ts: number;
}

export function useRealtimePrice(tsCodes: string[]) {
  const [prices, setPrices] = useState<Record<string, TickData>>({});
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const ws = new WebSocket(`ws://${window.location.hostname}:8000/ws/realtime`);

    ws.onopen = () => {
      setConnected(true);
      ws.send(JSON.stringify({ action: "subscribe", codes: tsCodes }));
    };

    ws.onmessage = (e) => {
      const data: TickData = JSON.parse(e.data);
      setPrices((prev) => ({ ...prev, [data.ts_code]: data }));
    };

    ws.onclose = () => {
      setConnected(false);
      setTimeout(() => {
        // 自动重连逻辑由外层或心跳处理
      }, 3000);
    };

    ws.onerror = () => ws.close();

    return () => ws.close();
  }, [tsCodes.join(",")]);

  return { prices, connected };
}
```

### 10.3 组件复用策略

所有前端组件基于 **shadcn/ui** 构建，保持视觉一致：
- 表格：`<Table>` + 自定义排序/筛选逻辑
- 对话框：`<Dialog>` 回测详情、信号详情
- 下拉选择：`<Select>` 股票池选择、时间范围
- 卡片：`<Card>` Dashboard 卡片
- 标签页：`<Tabs>` 自选股分组、模拟交易子页
- Toast：`<Toast>` 信号告警、任务状态通知

---

## 11. Docker Compose 部署配置

### 11.1 docker-compose.yml

```yaml
# docker-compose.yml
services:
  # ── 数据层 ──
  postgres:
    image: postgres:15-alpine
    container_name: leek-postgres
    environment:
      POSTGRES_DB:       leek_quant
      POSTGRES_USER:     leek
      POSTGRES_PASSWORD: ${DB_PASSWORD:-leek_quant_2025}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./backend/migrations/init.sql:/docker-entrypoint-initdb.d/01_init.sql
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U leek -d leek_quant"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: leek-redis
    command: redis-server --requirepass ${REDIS_PASSWORD:-leek_redis_2025}
    ports:
      - "6379:6379"
    volumes:
      - redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD:-leek_redis_2025}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  # ── 应用层 ──
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: leek-backend
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    environment:
      DATABASE_URL: postgresql+asyncpg://leek:${DB_PASSWORD:-leek_quant_2025}@postgres:5432/leek_quant
      REDIS_URL:    redis://:${REDIS_PASSWORD:-leek_redis_2025}@redis:6379/0
      SECRET_KEY:   ${SECRET_KEY:-changeme-in-production}
    volumes:
      - ./backend:/app
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  # ── Celery Workers ──
  celery_worker:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: leek-celery-worker
    command: celery -A app.celery_app worker --loglevel=info --concurrency=4 -Q default,backtest,data,factor
    environment:
      DATABASE_URL: postgresql+asyncpg://leek:${DB_PASSWORD:-leek_quant_2025}@postgres:5432/leek_quant
      REDIS_URL:    redis://:${REDIS_PASSWORD:-leek_redis_2025}@redis:6379/0
    volumes:
      - ./backend:/app
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  celery_beat:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: leek-celery-beat
    command: celery -A app.celery_app beat --loglevel=info
    environment:
      DATABASE_URL: postgresql+asyncpg://leek:${DB_PASSWORD:-leek_quant_2025}@postgres:5432/leek_quant
      REDIS_URL:    redis://:${REDIS_PASSWORD:-leek_redis_2025}@redis:6379/0
    volumes:
      - ./backend:/app
    depends_on:
      - celery_worker
    restart: unless-stopped

  # ── 实时行情守护进程 ──
  realtime_ws:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: leek-realtime-ws
    command: python -m app.realtime.eastmoney_ws
    environment:
      REDIS_URL: redis://:${REDIS_PASSWORD:-leek_redis_2025}@redis:6379/0
    volumes:
      - ./backend:/app
    depends_on:
      - redis
    restart: unless-stopped

  # ── 前端 ──
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    container_name: leek-frontend
    ports:
      - "80:80"
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  pgdata:
  redisdata:
```

### 11.2 后端 Dockerfile

```dockerfile
# backend/Dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 11.3 关键依赖

```txt
# backend/requirements.txt
# Web框架
fastapi[standard]>=0.111.0
uvicorn[standard]>=0.29.0
pydantic>=2.0.0
pydantic-settings>=2.0.0

# 数据库
sqlalchemy[asyncio]>=2.0.0
asyncpg>=0.29.0
alembic>=1.13.0

# 任务队列
celery[redis]>=5.3.0
redis>=5.0.0

# A股数据源
adata>=3.0.0
baostock>=0.8.8
akshare>=1.14.0

# 回测引擎
hikyuu>=2.1.0

# 技术指标（单文件，从 https://github.com/mpquant/MyTT 获取）
# MyTT.py → 直接放入项目目录

# 科学计算
numpy>=1.26.0
pandas>=2.1.0
scipy>=1.13.0

# 工具
websockets>=12.0
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
httpx>=0.27.0
```

### 11.4 Celery Beat 定时任务配置

```python
# backend/app/celery_config.py
from celery.schedules import crontab

CELERYBEAT_SCHEDULE = {
    # 交易日 18:00 → K线增量更新
    "incremental-kline-update": {
        "task": "app.tasks.data_tasks.incremental_kline_update",
        "schedule": crontab(hour=18, minute=0),
    },
    # 交易日 17:30 → 因子计算
    "daily-factor-compute": {
        "task": "app.tasks.factor_tasks.compute_daily_factors",
        "schedule": crontab(hour=17, minute=30),
    },
    # 交易日 17:00 → 策略信号生成
    "generate-daily-signals": {
        "task": "app.tasks.signal_tasks.generate_all_signals",
        "schedule": crontab(hour=17, minute=0),
    },
    # 交易日 09:25 → T+1持仓解锁
    "unlock-t1-positions": {
        "task": "app.tasks.trading_tasks.unlock_t1_positions",
        "schedule": crontab(hour=9, minute=25),
    },
    # 交易日 09:30-15:00 → 实时撮合（每5分钟）
    "match-orders": {
        "task": "app.tasks.trading_tasks.match_orders",
        "schedule": crontab(hour="9,10,11,13,14", minute="*/5"),
    },
    # 交易日 15:30 → 净值和分快照
    "daily-nav-snapshot": {
        "task": "app.tasks.trading_tasks.generate_daily_nav",
        "schedule": crontab(hour=15, minute=30),
    },
    # 每周五 16:00 → 打分排名
    "weekly-scoring-rank": {
        "task": "app.tasks.factor_tasks.generate_scoring_rank",
        "schedule": crontab(day_of_week=5, hour=16, minute=0),
    },
    # 每周日 02:00 → 交易日历更新
    "update-trade-calendar": {
        "task": "app.tasks.data_tasks.update_trade_calendar",
        "schedule": crontab(day_of_week="sunday", hour=2),
    },
    # 每周六 03:00 → 股票基础信息更新
    "update-stock-basic": {
        "task": "app.tasks.data_tasks.update_stock_basic",
        "schedule": crontab(day_of_week="saturday", hour=3),
    },
}
```

---

## 12. 开发里程碑

| 阶段 | 内容 | 核心交付 | 关键依赖 |
|------|------|---------|---------|
| **M0：基础环境** | Docker Compose 搭建、PostgreSQL 建表 + Alembic 迁移、FastAPI 基础骨架 | 可运行空壳系统 | FastAPI + SQLAlchemy |
| **M1：数据基座** | AData/Baostock/AkShare 三层集成、全量K线入库、增量更新定时任务、交易日历 | 完整历史K线 + 数据API | AData + Baostock + AkShare |
| **M2：股票管理** | 股票池（动态筛选/ST标识）+ 自选股分组 + 基本面同步 | 股票管理API | Baostock 基本面 |
| **M3：策略回测** | Monaco 编辑器前端 + Hikyuu 适配层 + 异步回测Celery任务 + 结果可视化 | 可写策略并回测 | Hikyuu + MyTT + Monaco |
| **M4：信号交易** | 五档信号状态机 + 信号日志 + 模拟交易6表引擎（含T+1/涨跌停/费用） | 完整纸上交易闭环 | - |
| **M5：因子选股** | 多因子计算 + IC/IR分析 + 打分排名 + 因子可视化 | 因子选股功能 | Qlib范式 + MyTT |
| **M6：实时推送** | 东方财富 WebSocket 解析器 + Redis广播 + 前端实时看板 | 实时行情推送 | websockets + Redis |
| **M7：完善优化** | 净值曲线、多账户隔离、告警监控、参数优化、文档 | 生产可用 | - |

---

## 13. 风险与应对措施

| 风险类别 | 具体风险 | 影响 | 应对措施 |
|---------|---------|------|---------|
| **数据源不稳定** | AData/Baostock 接口变更或IP封禁 | 数据拉取中断 | ① 三层自动回退 ② 请求间隔≥0.3s + 随机UA ③ 本地缓存 ④ 告警通知 |
| **数据质量** | 多源复权因子微小差异 | 回测收益失真 | 以 AData 为基准，切换时重新计算复权因子 |
| **实时行情断连** | 东方财富 WS 协议变更或网络中断 | 盘中行情中断 | ① 断线自动重连 ② 心跳保活 ③ 备选 AllTick |
| **回测性能** | 大股票池回测超时 | 用户体验差 | Hikyuu C++内核 + Celery多Worker并行 + 限制最大范围 |
| **安全注入** | 用户策略代码恶意注入 | 系统被攻击 | sandbox exec + 禁用 `__import__` + 30s超时 + 子进程隔离 |
| **磁盘膨胀** | 全量K线数据增长（5000股×10年≈18GB） | 磁盘不足 | 分区表 + 允许用户配置保留年限 + 磁盘监控告警 |
| **Hikyuu兼容** | Hikyuu API 版本变更 | 回测失败 | 锁定版本 + 适配层隔离 + 核心路径单元测试 |
| **合规风险** | 高频采集涉嫌违反数据源条款 | 法律风险 | 限制频率 + 声明仅个人学习研究 + 禁止商业用途 |
| **A股规则遗漏** | T+1/涨跌停处理不准确 | 回测失真 | Hikyuu内置规则 + 覆盖边界场景的完整单元测试 |

---

## 14. 开源项目集成总结

| 开源项目 | 版本建议 | 在 Leek Quant 中的角色 | 集成方式 |
|---------|---------|----------------------|---------|
| **Hikyuu** | ≥2.1.0 | C++高性能A股回测引擎 | `pip install hikyuu`，适配层调用 |
| **MyTT** | 最新 | 通达信兼容技术指标库 | 复制 `MyTT.py` 到项目，策略可直接 `import MyTT` |
| **AData** | ≥3.0.0 | A股历史K线/股票列表主源 | `pip install adata`，数据模块 Tier1 |
| **Baostock** | ≥0.8.8 | K线备源 + 基本面（三表/估值） | `pip install baostock`，Tier2 |
| **AkShare** | ≥1.14.0 | 分钟K线、全市场数据兜底 | `pip install akshare`，Tier3 |
| **Qlib** | 参考范式 | 因子表达式引擎设计参考 | 轻量复刻因子定义+计算范式，不直接依赖 |
| **东方财富WS** | - | 免费实时行情推送 | 自建解析器 + Redis Pub/Sub 广播 |
| **QuantDinger** | 母版 | FastAPI/React/Celery 骨架 | 裁剪非A股模块，保留核心架构 |
| **TradingView LWC** | ≥4.0 | 专业K线图表 | `npm install lightweight-charts` |
| **Monaco Editor** | ≥0.47 | 策略代码编辑器 | `npm install @monaco-editor/react` |
| **shadcn/ui** | 最新 | UI 组件库 | `npx shadcn-ui@latest init` |

---

> 本文档以实用落地为首要原则，最大化复用 Hikyuu、MyTT、AData 等成熟开源项目，聚焦 A 股差异化能力，最小化自研工作量。基于 QuantDinger 骨架裁剪，保留其优秀的前后端与部署模板，替换存储层为统一 PostgreSQL，集成 Hikyuu 高性能回测内核，构建完整的纯 A 股量化交易平台。
>
> **免责声明**：本平台仅供量化研究学习与模拟交易使用，不构成任何投资建议。
>
> *最后更新：2026-05-15*
