# Leek Quant 开发架构文档

> **版本**：v1.0（WorkBuddy 综合版）  
> **设计原则**：本地优先 · 隐私至上 · 专注A股 · 深度复用开源  
> **基础框架**：QuantDinger（裁剪非A股部分，保留前端/后端/部署骨架）  
> **参考来源**：综合 DeepSeek、豆包、Gemini、Grok、通义、龙猫 六份设计方案精华

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

Leek Quant 是面向个人投资者和小型量化团队的**纯A股量化交易平台**，从 QuantDinger（多市场AI量化平台）做减法，保留并深化A股特有能力：

- **本地优先、隐私至上**：数据、策略、账户完全保留在用户本地，无需注册外部账号
- **Docker Compose 一键部署**：开箱即用，无需复杂环境配置
- **最大化复用开源**：深度集成 Hikyuu、MyTT、AData、Baostock、AkShare、Qlib，极少自研

### 1.2 核心差异化能力

| 能力 | 说明 |
|------|------|
| **A股规则内置** | T+1、涨跌停、印花税/佣金/过户费、停牌处理，完整覆盖 |
| **五档操作信号** | 买入/增持/减仓/卖出/观望，配合状态机自动映射实际操作 |
| **多因子选股** | 估值/成长/质量/动量四类因子，IC/IR分析，打分排名 |
| **完整模拟交易** | 委托→成交→持仓→流水→净值快照，全链路6表体系 |
| **三层数据兜底** | AData→Baostock→AkShare 自动回退，数据不中断 |

---

## 2. 技术栈总览

| 层级 | 技术选型 | 说明 |
|------|---------|------|
| **前端框架** | React 18 + Vite + TypeScript | 现代SPA，热模块替换 |
| **UI组件库** | Tailwind CSS + shadcn/ui | 设计一致，高度可定制 |
| **K线图表** | TradingView Lightweight Charts | 专业金融图表，Canvas渲染，百万级数据流畅 |
| **代码编辑器** | Monaco Editor | VSCode同源，支持Python语法+MyTT自动补全 |
| **状态管理** | Zustand | 轻量简洁，适合中型应用 |
| **后端框架** | FastAPI (Python 3.11+) | 高性能异步，自动生成OpenAPI文档 |
| **ORM** | SQLAlchemy 2.0 (async) + Alembic | 异步支持，迁移管理 |
| **数据库** | **PostgreSQL 15+** | 统一存储：分区表+JSONB+窗口函数 |
| **消息队列** | Celery + Redis | 回测/因子计算/数据拉取 异步执行 |
| **实时推送** | Redis Pub/Sub + FastAPI WebSocket | 行情广播，前端实时刷新 |
| **回测引擎** | **Hikyuu** | C++内核，Python绑定，原生A股规则支持 |
| **技术指标** | **MyTT** | 通达信/同花顺兼容，单文件零依赖 |
| **历史数据** | AData(主) + Baostock(备) + AkShare(兜底) | 三层免费回退 |
| **实时行情** | 东方财富 WebSocket（自建解析） | 低延迟推送 |
| **因子框架** | 参考 Qlib 表达式范式 | 轻量复刻，存入PostgreSQL |
| **部署** | Docker Compose | 一键启动全部服务 |

---

## 3. 系统架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         前端层 (React + Vite)                        │
│  ┌─────────┐ ┌──────────┐ ┌────────────────────┐ ┌───────────────┐ │
│  │自选股   │ │股票池管理 │ │策略编辑器           │ │回测/因子/模拟 │ │
│  │实时看板 │ │动态筛选  │ │Monaco + MyTT提示    │ │TradingView图表│ │
│  └─────────┘ └──────────┘ └────────────────────┘ └───────────────┘ │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ HTTP REST / WebSocket
┌───────────────────────────────┴─────────────────────────────────────┐
│                        API层 (FastAPI)                               │
│  ┌──────────┐ ┌────────────┐ ┌────────────┐ ┌─────────────────────┐│
│  │用户/认证  │ │数据查询API │ │策略CRUD    │ │回测/信号/模拟交易API ││
│  └──────────┘ └────────────┘ └────────────┘ └─────────────────────┘│
└──────────────┬──────────────────────────┬────────────────────────────┘
               │ 提交异步任务              │ 直接调用（同步查询）
               ▼                          ▼
┌──────────────────────────┐   ┌──────────────────────────────────┐
│    Celery Workers         │   │  计算内核                         │
│  - 历史K线增量拉取         │   │  ┌────────────────────────────┐  │
│  - 定时信号生成            │   │  │ Hikyuu（C++回测引擎）        │  │
│  - 因子计算/打分           │   │  │ - A股T+1、涨跌停、费用模型  │  │
│  - 模拟交易撮合            │   │  │ - 策略绩效报告              │  │
│  - 实时行情解析            │   │  └────────────────────────────┘  │
└──────────────┬───────────┘   │  ┌────────────────────────────┐  │
               │               │  │ MyTT（技术指标库）           │  │
               │               │  │ - 通达信/同花顺兼容          │  │
               │               │  │ - 策略内 import 直接使用     │  │
               │               │  └────────────────────────────┘  │
               │               └──────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────┐
│              存储层                                                │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  PostgreSQL 15+                                            │  │
│  │  - 市场/K线（分区表） - 回测结果/信号日志                   │  │
│  │  - 自选股/股票池/策略 - 因子值/打分排名                     │  │
│  │  - 模拟交易6表（账户/持仓/委托/成交/流水/净值）             │  │
│  └───────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Redis                                                    │   │
│  │  - Celery Broker/Result Backend                           │   │
│  │  - 实时行情 Pub/Sub 广播                                  │   │
│  │  - 热点数据缓存                                           │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
               ▲
               │ 数据采集
┌──────────────┴──────────────────────────────────────────────────┐
│              数据源层                                             │
│  Tier1: AData（日/周/月K线、全市场列表）                          │
│     ↓ 失败回退                                                   │
│  Tier2: Baostock（日K线备源 + 财务三表 + 估值指标）              │
│     ↓ 失败回退                                                   │
│  Tier3: AkShare（分钟K线 + 全市场补充数据）                      │
│                                                                  │
│  实时独立通道: 东方财富 WebSocket → 自建解析器 → Redis Pub/Sub   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. PostgreSQL 完整表设计

### 4.1 设计原则

- **分区表**：`daily_kline` 按年 `PARTITION BY RANGE(trade_date)`，加速时间范围查询
- **JSONB**：策略配置、回测结果曲线、财务报表等半结构化数据
- **ACID**：金融数据强一致性保障
- **user_id 隔离**：所有用户相关表带 `user_id`，支持多账户隔离
- **复合索引**：`(ts_code, trade_date)`、`(account_id, trade_date)` 等高频查询路径建索引

### 4.2 基础/市场数据（4张）

#### `stock_basic` — 股票基础信息

```sql
CREATE TABLE stock_basic (
    ts_code      VARCHAR(10) PRIMARY KEY,   -- 例如 '600000.SH'
    symbol       VARCHAR(6)  NOT NULL,       -- '600000'
    name         VARCHAR(20) NOT NULL,
    market       VARCHAR(10),               -- '主板' / '创业板' / '科创板'
    industry     VARCHAR(50),
    area         VARCHAR(20),
    list_date    DATE,
    delist_date  DATE,
    is_st        BOOLEAN     DEFAULT FALSE, -- ST/SST/ST* 标记
    is_delisted  BOOLEAN     DEFAULT FALSE, -- 退市标记
    updated_at   TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX idx_stock_basic_st ON stock_basic(is_st, is_delisted);
```

#### `daily_kline` — 日线K线（按年分区）

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
    adj_factor   NUMERIC(12,6),             -- 复权因子（前复权 = close * adj_factor）
    is_suspended BOOLEAN     DEFAULT FALSE, -- 停牌标记
    data_source  VARCHAR(20) DEFAULT 'adata',
    PRIMARY KEY (ts_code, trade_date)
) PARTITION BY RANGE (trade_date);

-- 按年建分区（可用 pg_partman 自动化）
CREATE TABLE daily_kline_2020 PARTITION OF daily_kline FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');
CREATE TABLE daily_kline_2021 PARTITION OF daily_kline FOR VALUES FROM ('2021-01-01') TO ('2022-01-01');
CREATE TABLE daily_kline_2022 PARTITION OF daily_kline FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');
CREATE TABLE daily_kline_2023 PARTITION OF daily_kline FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE daily_kline_2024 PARTITION OF daily_kline FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE daily_kline_2025 PARTITION OF daily_kline FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE daily_kline_2026 PARTITION OF daily_kline FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
```

#### `trade_calendar` — 交易日历

```sql
CREATE TABLE trade_calendar (
    cal_date       DATE    PRIMARY KEY,
    is_open        BOOLEAN NOT NULL,      -- 是否交易日
    pretrade_date  DATE,                  -- 上一个交易日（停牌续接用）
    is_weekend     BOOLEAN DEFAULT FALSE,
    is_holiday     BOOLEAN DEFAULT FALSE
);
```

#### `stock_fundamentals` — 基本面数据

```sql
CREATE TABLE stock_fundamentals (
    ts_code            VARCHAR(10) NOT NULL,
    report_date        DATE        NOT NULL,    -- 报告期（季报/年报）
    pe_ttm             NUMERIC(10,2),
    pb                 NUMERIC(10,2),
    ps_ttm             NUMERIC(10,2),
    roe                NUMERIC(10,4),
    market_cap         NUMERIC(16,2),           -- 总市值（元）
    float_market_cap   NUMERIC(16,2),           -- 流通市值
    dividend_yield     NUMERIC(10,4),
    revenue_growth     NUMERIC(10,4),           -- 营收同比增速
    net_profit_growth  NUMERIC(10,4),           -- 净利润同比增速
    debt_to_equity     NUMERIC(10,4),
    gross_margin       NUMERIC(10,4),
    income_statement   JSONB,                   -- 利润表原始数据
    balance_sheet      JSONB,                   -- 资产负债表
    cashflow_statement JSONB,                   -- 现金流量表
    PRIMARY KEY (ts_code, report_date)
);
```

### 4.3 用户与策略（5张）

```sql
-- 用户表
CREATE TABLE users (
    id            SERIAL      PRIMARY KEY,
    username      VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(200) NOT NULL,
    created_at    TIMESTAMP   DEFAULT NOW()
);

-- 自选股（分组管理）
CREATE TABLE watchlist (
    id         SERIAL      PRIMARY KEY,
    user_id    INTEGER     REFERENCES users(id) ON DELETE CASCADE,
    ts_code    VARCHAR(10) REFERENCES stock_basic(ts_code),
    group_name VARCHAR(50) DEFAULT '默认',
    added_at   TIMESTAMP   DEFAULT NOW(),
    UNIQUE(user_id, ts_code, group_name)
);

-- 股票池定义
CREATE TABLE stock_pools (
    id          SERIAL      PRIMARY KEY,
    user_id     INTEGER     REFERENCES users(id) ON DELETE CASCADE,
    pool_name   VARCHAR(100) NOT NULL,
    description TEXT,
    filters     JSONB,               -- 筛选条件 JSON（市值范围、行业、排除ST等）
    created_at  TIMESTAMP   DEFAULT NOW(),
    updated_at  TIMESTAMP   DEFAULT NOW()
);

-- 股票池成员（动态刷新）
CREATE TABLE stock_pool_items (
    pool_id    INTEGER REFERENCES stock_pools(id) ON DELETE CASCADE,
    ts_code    VARCHAR(10) REFERENCES stock_basic(ts_code),
    added_at   TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (pool_id, ts_code)
);

-- 策略（Python源码 + 配置）
CREATE TABLE strategies (
    id          SERIAL      PRIMARY KEY,
    user_id     INTEGER     REFERENCES users(id) ON DELETE CASCADE,
    name        VARCHAR(100) NOT NULL,
    description TEXT,
    code        TEXT        NOT NULL,   -- Python源码，import MyTT、from hikyuu import * 等
    config      JSONB,                 -- 参数配置：均线周期、仓位、止损等
    version     INTEGER     DEFAULT 1,
    status      VARCHAR(20) DEFAULT 'draft',  -- draft / active / archived
    created_at  TIMESTAMP   DEFAULT NOW(),
    updated_at  TIMESTAMP   DEFAULT NOW()
);
```

### 4.4 回测与信号（2张）

```sql
-- 回测结果
CREATE TABLE backtest_results (
    id              SERIAL    PRIMARY KEY,
    strategy_id     INTEGER   REFERENCES strategies(id),
    user_id         INTEGER   REFERENCES users(id),
    pool_id         INTEGER   REFERENCES stock_pools(id),
    start_date      DATE,
    end_date        DATE,
    initial_capital NUMERIC(20,2),
    -- 绩效指标
    total_return    NUMERIC(12,4),   -- 总收益率
    annual_return   NUMERIC(12,4),   -- 年化收益率
    sharpe_ratio    NUMERIC(8,4),    -- 夏普比率
    max_drawdown    NUMERIC(8,4),    -- 最大回撤
    annual_vol      NUMERIC(8,4),    -- 年化波动率
    win_rate        NUMERIC(6,4),    -- 胜率
    -- JSON存储大对象
    equity_curve    JSONB,           -- 净值曲线 [{date, nav}, ...]
    trade_records   JSONB,           -- 交易记录列表
    params_snapshot JSONB,           -- 回测时参数快照
    task_status     VARCHAR(20) DEFAULT 'pending',  -- pending/running/done/failed
    created_at      TIMESTAMP   DEFAULT NOW()
);

-- 五档信号日志
CREATE TABLE signal_log (
    id              BIGSERIAL   PRIMARY KEY,
    strategy_id     INTEGER     REFERENCES strategies(id),
    user_id         INTEGER     REFERENCES users(id),
    ts_code         VARCHAR(10) NOT NULL,
    trade_date      DATE        NOT NULL,
    signal          VARCHAR(10) CHECK (signal IN ('买入','增持','减仓','卖出','观望')),
    target_ratio    NUMERIC(5,4),        -- 目标仓位比例 0.0~1.0
    current_ratio   NUMERIC(5,4),        -- 触发时当前仓位
    reason          TEXT,                -- 信号触发原因描述
    market_snapshot JSONB,               -- 触发时行情快照（OHLCV、指标值）
    created_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX idx_signal_log_date ON signal_log(trade_date DESC);
CREATE INDEX idx_signal_log_code ON signal_log(ts_code, trade_date DESC);
```

### 4.5 因子与打分（3张）

```sql
-- 因子值
CREATE TABLE factor_values (
    ts_code      VARCHAR(10),
    trade_date   DATE,
    factor_name  VARCHAR(50),
    value        NUMERIC(18,6),
    PRIMARY KEY (ts_code, trade_date, factor_name)
);

CREATE INDEX idx_factor_date ON factor_values(trade_date, factor_name);

-- 打分排名（每日全市场或指定股票池）
CREATE TABLE scoring_rank (
    trade_date       DATE,
    ts_code          VARCHAR(10),
    total_score      NUMERIC(12,4),
    rank             INTEGER,
    factor_breakdown JSONB,          -- 各因子得分明细 {"pe_rank": 0.8, "roe_rank": 0.75, ...}
    PRIMARY KEY (trade_date, ts_code)
);

-- 因子有效性分析（IC/IR）
CREATE TABLE factor_analysis (
    id            SERIAL     PRIMARY KEY,
    factor_name   VARCHAR(50) NOT NULL,
    period_start  DATE,
    period_end    DATE,
    ic            NUMERIC(10,6),     -- 信息系数（因子值与下期收益相关系数）
    ic_mean       NUMERIC(10,6),     -- IC均值
    ic_std        NUMERIC(10,6),     -- IC标准差
    ir            NUMERIC(10,6),     -- 信息比率 = IC均值/IC标准差
    icir          NUMERIC(10,6),     -- 同 ir，部分文献叫法不同
    details       JSONB,             -- 分层回测收益等详细数据
    calc_at       TIMESTAMP  DEFAULT NOW()
);
```

### 4.6 模拟交易（6张完整体系）

```sql
-- 1. 模拟账户
CREATE TABLE sim_accounts (
    id              SERIAL      PRIMARY KEY,
    user_id         INTEGER     REFERENCES users(id) ON DELETE CASCADE,
    strategy_id     INTEGER     REFERENCES strategies(id),
    name            VARCHAR(100) NOT NULL,
    initial_capital NUMERIC(20,2) NOT NULL,    -- 初始资金
    cash            NUMERIC(20,2) NOT NULL,    -- 当前可用现金
    frozen_cash     NUMERIC(20,2) DEFAULT 0,   -- 委托冻结资金
    total_asset     NUMERIC(20,2),             -- 总资产（现金+持仓市值）
    created_at      TIMESTAMP   DEFAULT NOW(),
    updated_at      TIMESTAMP   DEFAULT NOW()
);

-- 2. 当前持仓
CREATE TABLE sim_positions (
    id              SERIAL      PRIMARY KEY,
    account_id      INTEGER     REFERENCES sim_accounts(id) ON DELETE CASCADE,
    ts_code         VARCHAR(10) NOT NULL,
    shares          INTEGER     NOT NULL,          -- 总持股数
    available_shares INTEGER    DEFAULT 0,          -- 可卖出股数（T+1：当日买入不可卖）
    avg_cost        NUMERIC(12,3) NOT NULL,         -- 平均成本价
    current_price   NUMERIC(12,3),                 -- 最新价（定时更新）
    market_value    NUMERIC(20,2),                 -- 当前市值
    unrealized_pnl  NUMERIC(20,2),                 -- 浮动盈亏
    profit_rate     NUMERIC(10,4),                 -- 盈亏比例
    updated_at      TIMESTAMP   DEFAULT NOW(),
    UNIQUE(account_id, ts_code)
);

-- 3. 委托单
CREATE TABLE sim_orders (
    id            BIGSERIAL   PRIMARY KEY,
    account_id    INTEGER     REFERENCES sim_accounts(id),
    ts_code       VARCHAR(10) NOT NULL,
    direction     VARCHAR(4)  CHECK (direction IN ('买入','卖出')),
    order_type    VARCHAR(10) DEFAULT '限价',   -- 限价 / 市价
    price         NUMERIC(12,3),               -- 委托价（市价单为NULL）
    volume        INTEGER     NOT NULL,         -- 委托数量
    filled_volume INTEGER     DEFAULT 0,        -- 已成交数量
    status        VARCHAR(10) DEFAULT '未报',   -- 未报/待成交/部分成交/全部成交/已撤单
    signal_id     BIGINT      REFERENCES signal_log(id),
    submit_time   TIMESTAMP   DEFAULT NOW(),
    cancel_time   TIMESTAMP
);

CREATE INDEX idx_sim_orders_account ON sim_orders(account_id, status);

-- 4. 成交记录
CREATE TABLE sim_trades (
    id           BIGSERIAL   PRIMARY KEY,
    order_id     BIGINT      REFERENCES sim_orders(id),
    account_id   INTEGER     REFERENCES sim_accounts(id),
    ts_code      VARCHAR(10) NOT NULL,
    direction    VARCHAR(4)  CHECK (direction IN ('买入','卖出')),
    price        NUMERIC(12,3) NOT NULL,        -- 成交价
    volume       INTEGER     NOT NULL,           -- 成交数量
    amount       NUMERIC(20,2),                 -- 成交金额（price * volume）
    stamp_tax    NUMERIC(12,4) DEFAULT 0,       -- 印花税（卖出时 0.05% 或 0.1%）
    commission   NUMERIC(12,4) DEFAULT 0,       -- 佣金（双向，万2.5，最低5元）
    transfer_fee NUMERIC(12,4) DEFAULT 0,       -- 过户费（双向，0.001%）
    total_fee    NUMERIC(12,4),                 -- 总费用
    trade_time   TIMESTAMP   DEFAULT NOW()
);

-- 5. 资金流水
CREATE TABLE sim_cash_flow (
    id               BIGSERIAL   PRIMARY KEY,
    account_id       INTEGER     REFERENCES sim_accounts(id),
    flow_type        VARCHAR(20),               -- 买入/卖出/分红/利息/手续费/充值
    amount           NUMERIC(20,2),             -- 变动金额（正为入，负为出）
    balance_after    NUMERIC(20,2),             -- 变动后余额
    related_trade_id BIGINT      REFERENCES sim_trades(id),
    remark           TEXT,
    created_at       TIMESTAMP   DEFAULT NOW()
);

-- 6. 每日净值快照
CREATE TABLE sim_daily_nav (
    id             BIGSERIAL  PRIMARY KEY,
    account_id     INTEGER    REFERENCES sim_accounts(id),
    nav_date       DATE       NOT NULL,
    total_asset    NUMERIC(20,2),              -- 当日总资产
    cash           NUMERIC(20,2),              -- 当日现金
    position_value NUMERIC(20,2),              -- 当日持仓市值
    daily_return   NUMERIC(12,8),              -- 日收益率
    cumulative_nav NUMERIC(12,4),              -- 累计净值（初始=1.0）
    max_drawdown   NUMERIC(8,4),               -- 截至当日最大回撤
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
| **Tier 3** | **AkShare** | 分钟K线、全市场补充数据 | 覆盖面最广，社区维护 |
| **实时独立** | **东方财富 WebSocket** | 盘中实时行情 | 自建解析，不走历史数据通道 |

### 5.2 三层回退实现

```python
# backend/app/data/fetcher.py
import asyncio
import time
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
        adjust: str = "qfq"  # 前复权
    ) -> Optional[pd.DataFrame]:
        sources = [
            ("adata",    self._fetch_adata),
            ("baostock", self._fetch_baostock),
            ("akshare",  self._fetch_akshare),
        ]

        for source_name, fetch_func in sources:
            for attempt in range(3):  # 每源最多重试3次
                try:
                    df = await fetch_func(ts_code, start_date, end_date, adjust)
                    if df is not None and len(df) > 0:
                        if source_name != "adata":
                            logger.warning(f"[{ts_code}] 使用备用数据源: {source_name}")
                        return self._normalize(df, source_name)
                except Exception as e:
                    wait = (2 ** attempt) + random.uniform(0, 0.5)
                    logger.warning(f"[{ts_code}] {source_name} 第{attempt+1}次失败: {e}，等待{wait:.1f}s")
                    await asyncio.sleep(wait)

            logger.error(f"[{ts_code}] {source_name} 连续失败，切换下一源")

        raise ValueError(f"[{ts_code}] 所有数据源均失败，请检查网络或数据源状态")

    async def _fetch_adata(self, ts_code, start_date, end_date, adjust):
        """AData Tier1"""
        import adata
        loop = asyncio.get_event_loop()
        # adata 为同步库，使用线程池隔离
        df = await loop.run_in_executor(
            None,
            lambda: adata.stock.market.get_market(
                stock_code=ts_code.split('.')[0],
                start_date=start_date,
                k_type=1
            )
        )
        return df

    async def _fetch_baostock(self, ts_code, start_date, end_date, adjust):
        """Baostock Tier2"""
        import baostock as bs
        loop = asyncio.get_event_loop()
        def _sync():
            bs.login()
            rs = bs.query_history_k_data_plus(
                ts_code.lower().replace('.', '.'),
                "date,open,high,low,close,volume,amount,adjustflag",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="2"  # 2=前复权
            )
            df = rs.get_data()
            bs.logout()
            return df
        return await loop.run_in_executor(None, _sync)

    async def _fetch_akshare(self, ts_code, start_date, end_date, adjust):
        """AkShare Tier3（兜底）"""
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
                adjust=adjust
            )
        )
        return df

    def _normalize(self, df: pd.DataFrame, source: str) -> pd.DataFrame:
        """将不同数据源字段统一为标准格式"""
        col_map = {
            # adata 列名映射
            "trade_date": "trade_date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume", "amount": "amount",
            # akshare 列名映射
            "日期": "trade_date", "开盘": "open", "最高": "high",
            "最低": "low", "收盘": "close", "成交量": "volume", "成交额": "amount",
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
from app.db import get_session
from app.data.fetcher import ChinaStockDataFetcher

@shared_task
def incremental_kline_update():
    """每交易日 18:00 触发，增量更新全市场K线"""
    with get_session() as db:
        # 查询每只股票最新日期
        latest_dates = db.execute("""
            SELECT ts_code, MAX(trade_date) as last_date
            FROM daily_kline
            GROUP BY ts_code
        """).fetchall()

        # 查询今日是否交易日
        today = get_latest_trade_date(db)

        fetcher = ChinaStockDataFetcher()
        for ts_code, last_date in latest_dates:
            if last_date >= today:
                continue  # 已是最新，跳过
            try:
                df = fetcher.fetch_kline(
                    ts_code,
                    start_date=str(last_date + timedelta(days=1)),
                    end_date=str(today)
                )
                # INSERT ... ON CONFLICT DO NOTHING 保证幂等
                bulk_upsert_kline(db, ts_code, df)
            except Exception as e:
                logger.error(f"[{ts_code}] 增量更新失败: {e}")
```

**增量更新策略要点：**
- 记录每只股票 `daily_kline` 中的 `MAX(trade_date)`，仅拉取之后数据
- `INSERT ... ON CONFLICT (ts_code, trade_date) DO NOTHING` 保证幂等，可安全重试
- 请求间隔至少 0.3s，避免触发反爬
- Celery Beat 在每个交易日 18:00 触发，非交易日自动跳过
- 停牌股票保留 `is_suspended=TRUE` 记录，不跳过（方便回测处理）

### 5.4 实时行情架构

```python
# backend/app/realtime/eastmoney_ws.py
import asyncio
import websockets
import json
import redis.asyncio as aioredis

class EastMoneyWSParser:
    """东方财富 WebSocket 实时行情解析器"""

    WS_URL = "wss://push2.eastmoney.com/api/qt/stock/sse"  # 示例，需按实际抓包调整

    def __init__(self, redis_url: str):
        self.redis = aioredis.from_url(redis_url)
        self.subscribed_codes: set[str] = set()

    async def start(self, stock_codes: list[str]):
        self.subscribed_codes = set(stock_codes)
        while True:  # 断线自动重连
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
                    # 广播到 Redis Pub/Sub
                    await self.redis.publish(
                        f"realtime:{tick['ts_code']}",
                        json.dumps(tick)
                    )

    def _parse(self, raw: str) -> dict | None:
        """解析东方财富推送的行情数据（具体格式需按抓包结果实现）"""
        try:
            data = json.loads(raw)
            return {
                "ts_code":  data.get("f12"),   # 股票代码
                "price":    data.get("f2"),    # 最新价
                "change":   data.get("f3"),    # 涨跌幅
                "volume":   data.get("f5"),    # 成交量
                "amount":   data.get("f6"),    # 成交额
                "high":     data.get("f15"),
                "low":      data.get("f16"),
                "open":     data.get("f17"),
                "ts":       int(time.time() * 1000)
            }
        except Exception:
            return None
```

---

## 6. 回测引擎集成方案（Hikyuu）

### 6.1 集成优势

| 特性 | 说明 |
|------|------|
| **C++ 内核** | pybind11 零拷贝调用，单机百万K线秒级回测 |
| **原生A股规则** | 内置 T+1、涨跌停、ST标记、分红除权 |
| **组件化设计** | Signal / MoneyManager / StopLoss / TradeCost 可拼装 |
| **Python 友好** | 用户策略直接用 Python 编写，可 `from MyTT import *` |
| **参数优化** | 内置网格搜索优化器 |

### 6.2 Hikyuu 适配层设计

```python
# backend/app/backtest/hikyuu_adapter.py
import hikyuu as hk
from hikyuu import crtTM, SYS_Simple, SM, Query
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

executor = ThreadPoolExecutor(max_workers=4)  # 回测并发数

class HikyuuBacktestAdapter:
    """PostgreSQL 数据 → Hikyuu 引擎 → 结果序列化"""

    def __init__(self, db_session):
        self.db = db_session

    async def run_async(self, config: dict) -> dict:
        """异步包装（Celery Worker 中调用）"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(executor, self._run_sync, config)

    def _run_sync(self, config: dict) -> dict:
        """同步回测执行"""
        strategy_code = config["strategy_code"]
        ts_codes       = config["stock_pool"]
        start_date     = config["start_date"]    # "2022-01-01"
        end_date       = config["end_date"]
        initial_capital= config.get("initial_capital", 100000)

        # 1. 从 PostgreSQL 加载 K 线数据到 Hikyuu
        sm = self._build_stock_manager(ts_codes, start_date, end_date)

        # 2. 执行用户策略代码，提取 Signal 组件
        signal_obj = self._compile_user_signal(strategy_code)

        # 3. 构建 A 股交易环境
        tm = crtTM(
            init_cash=initial_capital,
            cost=hk.TC_FixedSpread(
                buy_cost=0.00025,    # 万2.5佣金
                sell_cost=0.00025,
                min_cost=5.0,        # 最低5元佣金
                stamp_tax=0.0005,    # 印花税 0.05%（2023年减半）
                transfer_fee=0.00001 # 过户费 0.001%
            )
        )

        # 4. 组装并运行回测系统
        results = {}
        for ts_code in ts_codes:
            try:
                stock = sm[ts_code]
                sys = SYS_Simple(tm=crtTM(init_cash=initial_capital), sg=signal_obj)
                sys.run(stock, Query(start_date, end_date))
                results[ts_code] = self._extract_result(sys, tm)
            except Exception as e:
                results[ts_code] = {"error": str(e)}

        return self._aggregate_results(results, initial_capital)

    def _build_stock_manager(self, ts_codes, start_date, end_date):
        """从 PostgreSQL 构建 Hikyuu StockManager"""
        # 查询 daily_kline，转换为 Hikyuu 需要的格式
        # Hikyuu 支持自定义数据驱动，通过 StockManager 注册
        sm = SM  # 全局 StockManager 单例
        for ts_code in ts_codes:
            klines = self.db.execute("""
                SELECT trade_date, open, high, low, close, volume, amount, adj_factor
                FROM daily_kline
                WHERE ts_code = :code
                  AND trade_date BETWEEN :start AND :end
                  AND is_suspended = FALSE
                ORDER BY trade_date
            """, {"code": ts_code, "start": start_date, "end": end_date}).fetchall()

            k_data = hk.KData()
            for row in klines:
                k_data.append(hk.KRecord(
                    row.trade_date, row.open, row.high, row.low,
                    row.close, row.volume, row.amount
                ))
            sm.add_temp_stock(ts_code, k_data)
        return sm

    def _compile_user_signal(self, strategy_code: str):
        """
        安全执行用户策略代码，提取 Signal 对象。
        策略约定：代码中须定义 create_signal() 函数，返回 hikyuu.SignalBase 实例。
        示例策略：
            from MyTT import *
            from hikyuu import *
            def create_signal():
                return SG_Cross(MA(C, 5), MA(C, 20))
        """
        sandbox = {
            "__builtins__": {},  # 禁用内建（安全隔离）
            "hikyuu": hk,
        }
        # 注入 MyTT（仅允许指标函数）
        try:
            import MyTT
            sandbox["MyTT"] = MyTT
            sandbox.update({k: getattr(MyTT, k) for k in dir(MyTT) if not k.startswith('_')})
        except ImportError:
            pass

        exec(strategy_code, sandbox)
        if "create_signal" not in sandbox:
            raise ValueError("策略代码必须定义 create_signal() 函数")
        return sandbox["create_signal"]()

    def _extract_result(self, sys, tm) -> dict:
        """从 Hikyuu System 提取绩效指标"""
        perf = sys.tm.performance
        return {
            "total_return":  float(perf.total_return),
            "annual_return": float(perf.annual_return),
            "sharpe_ratio":  float(perf.sharpe_ratio),
            "max_drawdown":  float(perf.max_drawdown),
            "win_rate":      float(perf.win_rate),
            "trade_count":   perf.trade_count,
            "equity_curve":  [
                {"date": str(r.datetime.date()), "nav": float(r.total_assets / tm.init_cash)}
                for r in sys.tm.get_trade_list()
            ],
        }

    def _aggregate_results(self, results: dict, initial_capital: float) -> dict:
        """多股票回测结果聚合（等权组合）"""
        # TODO: 加权组合绩效计算
        return {
            "per_stock": results,
            "summary": {
                "initial_capital": initial_capital,
                "stock_count": len(results),
            }
        }
```

### 6.3 异步回测任务流程

```
用户提交回测请求
      ↓
FastAPI 创建 backtest_results 记录（status=pending）
      ↓
提交 Celery 任务（返回 task_id 给前端）
      ↓
Celery Worker 执行（HikyuuBacktestAdapter.run_async）
      ↓
写入 backtest_results（status=done，equity_curve/trade_records）
      ↓
前端轮询 /api/backtest/{id}/status 或 WebSocket 推送通知
```

---

## 7. 五档信号生成逻辑与状态机

### 7.1 信号定义

```python
# backend/app/signal/types.py
from enum import Enum
from pydantic import BaseModel

class SignalType(str, Enum):
    BUY    = "买入"   # 全仓买入（空仓→满仓）
    ADD    = "增持"   # 加仓（空仓时等同买入）
    REDUCE = "减仓"   # 部分卖出（降低仓位）
    SELL   = "卖出"   # 清仓
    WAIT   = "观望"   # 不操作

class Signal(BaseModel):
    signal_type:  SignalType
    ts_code:      str
    trade_date:   str
    target_ratio: float = 0.0   # 目标仓位占账户总资产比例 0.0~1.0
    reason:       str   = ""
    price:        float = 0.0   # 建议价格（回测用，实盘以市价为准）
```

### 7.2 状态机转移规则

| 当前仓位 | 买入 | 增持 | 观望 | 减仓 | 卖出 |
|---------|------|------|------|------|------|
| **0%（空仓）** | 买入→目标仓位 | **等同买入**→50% | 无操作 | 无操作 | 无操作 |
| **25%** | 加仓→100% | 加仓→50% | 无操作 | 无操作 | 清仓→0% |
| **50%** | 加仓→100% | 无操作 | 无操作 | 减仓→25% | 清仓→0% |
| **100%（满仓）** | 无操作 | 无操作 | 无操作 | 减仓→50% | 清仓→0% |

### 7.3 状态机实现

```python
# backend/app/signal/state_machine.py

class SignalStateMachine:
    """五档信号状态机，根据当前持仓状态映射实际操作"""

    def __init__(self, current_ratio: float = 0.0):
        self.current_ratio = current_ratio  # 当前仓位比例 0.0~1.0

    def execute(self, signal: Signal) -> tuple[str, float]:
        """
        返回 (操作类型, 目标仓位)
        操作类型：BUY / SELL_PARTIAL / SELL_ALL / HOLD
        """
        s = signal.signal_type
        r = self.current_ratio
        t = signal.target_ratio

        if s == SignalType.WAIT:
            return "HOLD", r

        if r == 0.0:  # 空仓
            if s in (SignalType.BUY, SignalType.ADD):
                target = t if t > 0 else (1.0 if s == SignalType.BUY else 0.5)
                return "BUY", target
            else:
                return "HOLD", 0.0

        else:  # 有仓位
            if s == SignalType.BUY:
                return ("BUY", min(t, 1.0)) if t > r else ("HOLD", r)
            elif s == SignalType.ADD:
                new_ratio = min(r + 0.25, 1.0)
                return ("BUY", new_ratio) if new_ratio > r else ("HOLD", r)
            elif s == SignalType.REDUCE:
                new_ratio = max(t, r - 0.25, 0.0)
                return "SELL_PARTIAL", new_ratio
            elif s == SignalType.SELL:
                return "SELL_ALL", 0.0

        return "HOLD", r

    def apply_cn_rules(self, action: str, position_date: str, today: str) -> str:
        """
        A股规则过滤：
        - T+1：当日买入的股票不能当日卖出
        - 涨停：买入委托可能无法成交
        - 停牌：暂停交易
        """
        if action.startswith("SELL") and position_date == today:
            return "HOLD"  # T+1 限制，不能当日卖出
        return action
```

### 7.4 信号生成触发机制

```python
# backend/app/tasks/signal_tasks.py
@shared_task
def generate_daily_signals(strategy_id: int, user_id: int):
    """
    每交易日 17:00 触发，为所有激活策略生成信号。
    流程：获取最新K线 → 计算MyTT指标 → 执行策略 → 写signal_log
    """
    with get_session() as db:
        strategy = db.query(Strategy).get(strategy_id)
        pool_codes = get_pool_stocks(db, strategy.pool_id)

        for ts_code in pool_codes:
            klines = get_klines(db, ts_code, days=250)  # 取250日数据供MyTT计算
            np_close = np.array([k.close for k in klines])

            # 执行策略代码生成信号
            signal = run_strategy_code(strategy.code, ts_code, np_close, klines[-1])
            if signal:
                # 写入 signal_log
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
                    }
                ))
        db.commit()
```

---

## 8. 模拟交易引擎工作流

### 8.1 整体流程

```
策略信号 (signal_log)
    ↓
模拟交易引擎 (Celery Task)
    ↓
┌─────────────────────────────────────────────────────────────┐
│  规则校验层                                                    │
│  ✓ 交易日检查（trade_calendar）                               │
│  ✓ T+1 校验（available_shares vs total_shares）             │
│  ✓ 涨跌停价格检查（超出涨跌幅限制则拒绝/挂单）                  │
│  ✓ 停牌检查（is_suspended）                                  │
│  ✓ 资金/持仓充足性校验                                        │
└─────────────────────────────────────────────────────────────┘
    ↓ 通过校验
创建委托单 (sim_orders, status=待成交)
    ↓
模拟撮合引擎
    ├── 限价单：price >= ask1（买入）或 price <= bid1（卖出）时成交
    └── 市价单：以最新价/收盘价成交
    ↓ 成交
生成成交记录 (sim_trades)
    ├── 印花税 = amount × 0.0005（仅卖出，2023年后减半）
    ├── 佣金 = max(amount × 0.00025, 5.0)（双向）
    └── 过户费 = amount × 0.00001（双向）
    ↓
更新持仓 (sim_positions)
    ├── 买入：增加 shares，avg_cost 加权平均，available_shares 次日才增加（T+1）
    └── 卖出：减少 available_shares 和 shares
    ↓
更新账户资金 (sim_accounts)
    ├── 买入：cash -= amount + fees
    └── 卖出：cash += amount - fees
    ↓
记录资金流水 (sim_cash_flow)
    ↓
（每日收盘后）生成净值快照 (sim_daily_nav)
    ├── total_asset = cash + Σ(shares × close_price)
    ├── daily_return = (total_asset / prev_total_asset) - 1
    └── cumulative_nav = cumulative_nav_prev × (1 + daily_return)
```

### 8.2 交易费用计算

```python
# backend/app/trading/cost_calculator.py

class AShareCostCalculator:
    """A股标准交易费用计算"""

    STAMP_TAX_RATE = 0.0005      # 印花税 0.05%（仅卖出）
    COMMISSION_RATE = 0.00025    # 佣金 万2.5（双向）
    MIN_COMMISSION = 5.0         # 最低佣金 5元
    TRANSFER_FEE_RATE = 0.00001  # 过户费 0.001%（沪市，双向）

    def calculate(self, direction: str, price: float, volume: int, market: str = "SH") -> dict:
        amount = price * volume
        commission = max(amount * self.COMMISSION_RATE, self.MIN_COMMISSION)
        stamp_tax = amount * self.STAMP_TAX_RATE if direction == "卖出" else 0.0
        # 深市无过户费，沪市双向收取
        transfer_fee = amount * self.TRANSFER_FEE_RATE if market == "SH" else 0.0
        total_fee = commission + stamp_tax + transfer_fee

        return {
            "amount": amount,
            "commission": round(commission, 4),
            "stamp_tax": round(stamp_tax, 4),
            "transfer_fee": round(transfer_fee, 4),
            "total_fee": round(total_fee, 4),
            "net_amount": amount + total_fee if direction == "买入" else amount - total_fee,
        }
```

### 8.3 T+1 持仓管理

```python
def process_buy_trade(db, account_id: int, ts_code: str, volume: int, price: float, trade_date: str):
    """买入成交后更新持仓（T+1规则：当日买入不计入 available_shares）"""
    pos = db.query(SimPosition).filter_by(account_id=account_id, ts_code=ts_code).first()
    if pos is None:
        pos = SimPosition(account_id=account_id, ts_code=ts_code, shares=0, available_shares=0, avg_cost=price)
        db.add(pos)

    # 更新总持股数和均价
    total_cost = pos.avg_cost * pos.shares + price * volume
    pos.shares += volume
    pos.avg_cost = total_cost / pos.shares
    # 注意：available_shares 不增加（次日 Celery Beat 任务统一解锁）
    pos.updated_at = datetime.now()
    db.commit()


@shared_task
def unlock_t1_positions():
    """每交易日开盘前 9:25，解锁昨日买入的持仓"""
    yesterday = get_prev_trade_date(date.today())
    with get_session() as db:
        # 查找所有昨日买入成交，将其 volume 加到 available_shares
        trades = db.query(SimTrade).filter(
            SimTrade.direction == "买入",
            func.date(SimTrade.trade_time) == yesterday
        ).all()
        for trade in trades:
            pos = db.query(SimPosition).filter_by(
                account_id=trade.account_id, ts_code=trade.ts_code
            ).first()
            if pos:
                pos.available_shares += trade.volume
        db.commit()
```

---

## 9. 多因子打分模块设计

### 9.1 因子体系

| 因子类别 | 代表因子 | 数据来源 | 更新频率 |
|---------|---------|---------|---------|
| **估值** | PE_TTM、PB、PS_TTM、PCF | Baostock 基本面 | 每季报更新 |
| **成长** | 营收增速、净利润增速、ROE增速 | Baostock 财务三表 | 每季报更新 |
| **质量** | ROE、毛利率、资产负债率、现金流质量 | Baostock 财务三表 | 每季报更新 |
| **动量** | 1M/3M/6M价格动量、RSI6、BIAS6 | daily_kline + MyTT | 每交易日 |
| **波动** | 20日波动率、最大日跌幅 | daily_kline | 每交易日 |

### 9.2 因子计算架构（参考 Qlib 表达式）

```python
# backend/app/factor/engine.py
from MyTT import *
import numpy as np
import pandas as pd

# 因子表达式注册表（Qlib 风格声明式）
FACTOR_REGISTRY = {
    # 技术/动量因子（使用 MyTT 计算）
    "RSI6":        lambda df: RSI(df["close"].values, N=6)[-1],
    "BIAS6":       lambda df: BIAS(df["close"].values, L=6)[-1],
    "MA5_slope":   lambda df: (df["close"].values[-1] / df["close"].values[-5] - 1),
    "MOM1M":       lambda df: (df["close"].values[-1] / df["close"].values[-21] - 1),
    "MOM3M":       lambda df: (df["close"].values[-1] / df["close"].values[-63] - 1),
    "VOL20":       lambda df: np.std(df["close"].pct_change().values[-20:]),
    # 估值因子（来自 stock_fundamentals）
    "PE_RANK":     "fundamental_pe_ttm",    # 特殊标记，从基本面表取
    "PB_RANK":     "fundamental_pb",
    "ROE_RANK":    "fundamental_roe",
}

class FactorEngine:
    def compute_technical_factors(self, ts_code: str, klines: pd.DataFrame) -> dict:
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
        self,
        factor_df: pd.DataFrame,
        weights: dict[str, float] | None = None
    ) -> pd.Series:
        """
        跨截面标准化 + 加权综合打分
        1. 去极值（Winsorize 99%）
        2. Z-Score 标准化
        3. 方向调整（PE取负，小PE好）
        4. 加权求和
        """
        default_weights = {
            "PE_RANK": -1.0,   # PE越小越好，取负
            "PB_RANK": -1.0,
            "ROE_RANK": 1.0,   # ROE越大越好
            "MOM3M": 1.0,
            "VOL20": -0.5,     # 低波动加分
        }
        w = weights or default_weights

        normalized = {}
        for col in factor_df.columns:
            s = factor_df[col].copy()
            # 去极值
            lower, upper = s.quantile(0.01), s.quantile(0.99)
            s = s.clip(lower, upper)
            # Z-Score
            normalized[col] = (s - s.mean()) / (s.std() + 1e-8)

        score = sum(normalized[f] * w.get(f, 1.0) for f in normalized)
        return score.rank(pct=True)  # 转为百分位排名
```

### 9.3 IC/IR 分析

```python
# backend/app/factor/analysis.py

def compute_ic_ir(
    db,
    factor_name: str,
    start_date: str,
    end_date: str,
    forward_days: int = 5
) -> dict:
    """
    IC = corr(因子值, forward_days日后收益率)
    IR = IC_mean / IC_std
    """
    # 加载因子值
    factor_df = pd.read_sql("""
        SELECT fv.ts_code, fv.trade_date, fv.value as factor_val,
               (dk_future.close / dk_now.close - 1) as forward_return
        FROM factor_values fv
        JOIN daily_kline dk_now ON fv.ts_code=dk_now.ts_code AND fv.trade_date=dk_now.trade_date
        JOIN daily_kline dk_future ON fv.ts_code=dk_future.ts_code
            AND dk_future.trade_date = (
                SELECT cal_date FROM trade_calendar
                WHERE cal_date > fv.trade_date AND is_open=TRUE
                ORDER BY cal_date LIMIT 1 OFFSET :fwd-1
            )
        WHERE fv.factor_name = :fname
          AND fv.trade_date BETWEEN :start AND :end
    """, db.bind, params={"fname": factor_name, "start": start_date, "end": end_date, "fwd": forward_days})

    ic_series = factor_df.groupby("trade_date").apply(
        lambda g: g["factor_val"].corr(g["forward_return"])
    ).dropna()

    return {
        "factor_name": factor_name,
        "ic_mean": float(ic_series.mean()),
        "ic_std":  float(ic_series.std()),
        "ir":      float(ic_series.mean() / (ic_series.std() + 1e-8)),
        "ic_gt_0_pct": float((ic_series > 0).mean()),  # IC大于0的比例
    }
```

---

## 10. 前端页面与组件规划

### 10.1 页面结构

| 路由 | 页面名 | 核心功能 |
|------|--------|---------|
| `/` | **首页 Dashboard** | 大盘指数概览、自选股涨跌看板、今日信号摘要、模拟账户净值 |
| `/market` | **市场/股票池** | 全市场列表（ST/退市标识）、动态筛选、股票详情K线 |
| `/watchlist` | **自选股** | 分组管理、实时行情推送、快捷交易面板 |
| `/strategy` | **策略中心** | 策略列表、Monaco代码编辑器、回测配置与结果 |
| `/signals` | **信号中心** | 五档信号日志、按策略/股票/日期筛选、信号统计 |
| `/simulation` | **模拟交易** | 账户概览、持仓、委托、成交、流水、净值曲线 |
| `/factor` | **因子选股** | 因子列表、IC/IR分析图表、打分排行榜、权重配置 |
| `/settings` | **系统设置** | 数据更新状态、定时任务监控、多账户管理、告警配置 |

### 10.2 核心组件

#### Monaco 编辑器（策略编辑）

```typescript
// frontend/src/components/StrategyEditor.tsx
import Editor, { Monaco } from "@monaco-editor/react";

const MYTT_COMPLETIONS = [
  { label: "MA", detail: "MA(CLOSE, N) - 简单移动平均" },
  { label: "EMA", detail: "EMA(CLOSE, N) - 指数移动平均" },
  { label: "MACD", detail: "MACD(CLOSE, SHORT, LONG, M) - MACD指标" },
  { label: "RSI", detail: "RSI(CLOSE, N) - 相对强弱指标" },
  { label: "BOLL", detail: "BOLL(CLOSE, N, P) - 布林带" },
  { label: "KDJ", detail: "KDJ(CLOSE, HIGH, LOW) - 随机指标" },
  { label: "BIAS", detail: "BIAS(CLOSE, L) - 乖离率" },
  { label: "ATR", detail: "ATR(CLOSE, HIGH, LOW, N) - 平均真实波幅" },
  // ... 更多 MyTT 函数
];

function setupMyTTCompletions(monaco: Monaco) {
  monaco.languages.registerCompletionItemProvider("python", {
    provideCompletionItems: (model, position) => ({
      suggestions: MYTT_COMPLETIONS.map(item => ({
        label: item.label,
        kind: monaco.languages.CompletionItemKind.Function,
        detail: item.detail,
        insertText: item.label,
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

export function StrategyEditor({ value, onChange }) {
  return (
    <Editor
      height="60vh"
      language="python"
      theme="vs-dark"
      value={value}
      onChange={onChange}
      beforeMount={setupMyTTCompletions}
      options={{
        fontSize: 14,
        minimap: { enabled: false },
        suggestOnTriggerCharacters: true,
      }}
    />
  );
}
```

#### TradingView K线图（信号叠加）

```typescript
// frontend/src/components/KLineChart.tsx
import { createChart, CrosshairMode } from "lightweight-charts";
import { useEffect, useRef } from "react";

export function KLineChart({ klineData, signals }) {
  const chartRef = useRef(null);

  useEffect(() => {
    const chart = createChart(chartRef.current, {
      width: 900, height: 500,
      crosshair: { mode: CrosshairMode.Normal },
      layout: { background: { color: "#1a1a2e" }, textColor: "#eee" },
      grid: { vertLines: { color: "#2d2d44" }, horzLines: { color: "#2d2d44" } },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#ff3b30",     // A股：红涨
      downColor: "#34c759",   // A股：绿跌
      borderVisible: false,
    });
    candleSeries.setData(klineData);

    // 叠加信号标记
    const markers = signals.map(sig => ({
      time: sig.trade_date,
      position: sig.signal === "买入" || sig.signal === "增持" ? "belowBar" : "aboveBar",
      color: sig.signal === "买入" ? "#ff3b30" : sig.signal === "卖出" ? "#34c759" : "#ffd700",
      shape: sig.signal === "买入" ? "arrowUp" : "arrowDown",
      text: sig.signal,
    }));
    candleSeries.setMarkers(markers);

    return () => chart.remove();
  }, [klineData, signals]);

  return <div ref={chartRef} />;
}
```

#### 实时行情组件（WebSocket）

```typescript
// frontend/src/hooks/useRealtimePrice.ts
import { useEffect, useState } from "react";

export function useRealtimePrice(tsCodes: string[]) {
  const [prices, setPrices] = useState<Record<string, any>>({});

  useEffect(() => {
    const ws = new WebSocket(`ws://localhost:8000/ws/realtime`);
    ws.onopen = () => ws.send(JSON.stringify({ subscribe: tsCodes }));
    ws.onmessage = (e) => {
      const data = JSON.parse(e.data);
      setPrices(prev => ({ ...prev, [data.ts_code]: data }));
    };
    ws.onerror = () => setTimeout(() => ws.close(), 3000);
    return () => ws.close();
  }, [tsCodes.join(",")]);

  return prices;
}
```

---

## 11. Docker Compose 部署配置

```yaml
# docker-compose.yml
version: '3.8'

services:
  # ── 数据层 ──────────────────────────────────────────────
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

  # ── 应用层 ──────────────────────────────────────────────
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: leek-backend
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    environment:
      DATABASE_URL:   postgresql+asyncpg://leek:${DB_PASSWORD:-leek_quant_2025}@postgres:5432/leek_quant
      REDIS_URL:      redis://:${REDIS_PASSWORD:-leek_redis_2025}@redis:6379/0
      SECRET_KEY:     ${SECRET_KEY:-changeme-in-production}
      ENVIRONMENT:    development
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

  # ── Celery Workers ────────────────────────────────────
  celery_worker:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: leek-celery-worker
    command: celery -A app.celery_app worker --loglevel=info --concurrency=4 -Q default,backtest,data
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
    command: celery -A app.celery_app beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
    environment:
      DATABASE_URL: postgresql+asyncpg://leek:${DB_PASSWORD:-leek_quant_2025}@postgres:5432/leek_quant
      REDIS_URL:    redis://:${REDIS_PASSWORD:-leek_redis_2025}@redis:6379/0
    volumes:
      - ./backend:/app
    depends_on:
      - celery_worker
    restart: unless-stopped

  # ── 实时行情守护进程 ──────────────────────────────────
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

  # ── 前端 ────────────────────────────────────────────────
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

**后端 Dockerfile 关键依赖：**

```dockerfile
# backend/Dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
```

```text
# backend/requirements.txt（关键依赖）
fastapi[standard]>=0.111.0
uvicorn[standard]>=0.29.0
sqlalchemy[asyncio]>=2.0.0
asyncpg>=0.29.0
alembic>=1.13.0
celery[redis]>=5.3.0
redis>=5.0.0
pydantic>=2.0.0
pydantic-settings>=2.0.0

# A股数据源
adata>=3.0.0
baostock>=0.8.8
akshare>=1.14.0

# 回测引擎（注意平台依赖，可能需源码编译）
hikyuu>=2.1.0

# 技术指标（单文件，直接放入项目或 pip 安装）
# MyTT - 从 https://github.com/mpquant/MyTT 获取 MyTT.py

# 因子计算辅助
numpy>=1.26.0
pandas>=2.1.0
scipy>=1.13.0

# 工具
websockets>=12.0
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
httpx>=0.27.0
```

**定时任务配置（Celery Beat）：**

```python
# backend/app/celery_config.py
from celery.schedules import crontab

CELERYBEAT_SCHEDULE = {
    # 交易日 18:00 增量更新K线
    "incremental-kline-update": {
        "task": "app.tasks.data_tasks.incremental_kline_update",
        "schedule": crontab(hour=18, minute=0),
    },
    # 交易日 17:30 生成当日因子值
    "daily-factor-compute": {
        "task": "app.tasks.factor_tasks.compute_daily_factors",
        "schedule": crontab(hour=17, minute=30),
    },
    # 交易日 17:00 生成策略信号
    "generate-daily-signals": {
        "task": "app.tasks.signal_tasks.generate_all_signals",
        "schedule": crontab(hour=17, minute=0),
    },
    # 每交易日 09:25 解锁T+1持仓
    "unlock-t1-positions": {
        "task": "app.tasks.trading_tasks.unlock_t1_positions",
        "schedule": crontab(hour=9, minute=25),
    },
    # 每交易日 15:30 生成净值快照
    "daily-nav-snapshot": {
        "task": "app.tasks.trading_tasks.generate_daily_nav",
        "schedule": crontab(hour=15, minute=30),
    },
    # 每周日 02:00 更新交易日历
    "update-trade-calendar": {
        "task": "app.tasks.data_tasks.update_trade_calendar",
        "schedule": crontab(day_of_week="sunday", hour=2),
    },
    # 每周六 03:00 更新全市场股票基础信息
    "update-stock-basic": {
        "task": "app.tasks.data_tasks.update_stock_basic",
        "schedule": crontab(day_of_week="saturday", hour=3),
    },
}
```

---

## 12. 开发里程碑

| 阶段 | 内容 | 核心交付物 | 关键开源依赖 |
|------|------|-----------|------------|
| **M0：基础环境** | Docker Compose搭建、PostgreSQL建表+迁移、基础API框架 | 可运行的空壳系统 | FastAPI + SQLAlchemy + Alembic |
| **M1：数据基座** | AData/Baostock/AkShare三层数据源集成、全量K线入库、增量更新定时任务、交易日历 | 完整历史K线数据 | AData + Baostock + AkShare |
| **M2：股票管理** | 股票池（动态筛选+ST/退市标识）+ 自选股分组API + 基本面数据同步 | 股票池与自选股API | Baostock基本面 |
| **M3：策略回测** | Monaco编辑器前端 + Hikyuu适配层 + 回测Celery任务 + 结果可视化 | 用户可编写策略并回测 | Hikyuu + MyTT + Monaco |
| **M4：信号交易** | 五档信号状态机 + 信号日志 + 模拟交易6表引擎（含T+1/涨跌停/费用） | 完整纸上交易闭环 | - |
| **M5：因子选股** | 多因子计算（MyTT技术+Baostock基本面）+ IC/IR分析 + 打分排行榜 | 因子选股功能 | Qlib范式 + MyTT + Pandas |
| **M6：实时推送** | 东方财富WebSocket解析器 + Redis广播 + 前端实时看板 | 实时行情推送 | websockets + Redis |
| **M7：完善优化** | 净值曲线完善、多账户隔离、数据完整性告警、参数敏感性、README文档 | 生产可用状态 | - |

---

## 13. 风险与应对措施

| 风险类别 | 具体风险 | 影响 | 应对措施 |
|---------|---------|------|---------|
| **数据源** | AData/Baostock 接口变更或IP封禁 | 无法拉取数据，回测中断 | ① 三层自动回退 ② 请求间隔≥0.3s + 随机User-Agent ③ 本地缓存已有数据 ④ 告警通知 |
| **数据质量** | 多源复权因子微小差异 | 回测收益失真 | 以 AData 为基准，切换数据源时重新计算复权因子 |
| **实时行情** | 东方财富 WebSocket 协议变更 | 盘中行情中断 | ① 断线自动重连 ② 心跳保活 ③ AllTick 作为备选付费方案 |
| **回测性能** | 大股票池回测超时 | 用户体验差 | ① Hikyuu C++内核（百万K线秒级）② Celery 多Worker并行 ③ 限制最大回测范围 |
| **安全** | 用户策略代码注入 | 系统被攻击 | ① sandbox exec，禁用 `__import__` ② 超时中断（30s） ③ 独立子进程执行 |
| **存储** | 全量K线数据膨胀（5000股×10年≈18GB） | 磁盘不足 | ① 分区表 + 压缩 ② 允许用户配置数据保留年限 ③ 监控磁盘告警 |
| **Hikyuu兼容** | Hikyuu API版本变更 | 回测模块失效 | ① requirements.txt 锁定版本 ② 适配层隔离 ③ 单元测试覆盖核心路径 |
| **合规** | 高频采集涉嫌违反数据源条款 | 法律风险 | ① 限制请求频率 ② 声明仅用于个人学习研究 ③ 禁止商业用途 |
| **A股规则遗漏** | T+1/涨跌停处理不准确 | 回测失真 | ① Hikyuu内置规则 ② 建立完整单元测试套件，覆盖边界场景 |

> ⚠️ **免责声明**：本平台仅供量化研究学习与模拟交易使用，不构成任何投资建议，禁止用于实盘交易。

---

## 14. 开源项目集成总结

| 开源项目 | 版本建议 | 在 Leek Quant 中的角色 | 集成位置 | 集成方式 |
|---------|---------|----------------------|---------|---------|
| **Hikyuu** | ≥2.1.0 | 回测引擎核心 | Celery Worker | `pip install hikyuu`；适配层调用 |
| **MyTT** | 最新 | 技术指标函数库 | 策略编辑器内置 | 复制 `MyTT.py` 到后端；前端注册补全 |
| **AData** | ≥3.0.0 | A股历史K线主数据源 | 数据获取 Tier1 | `pip install adata` |
| **Baostock** | ≥0.8.8 | K线备源 + 基本面数据 | 数据获取 Tier2 | `pip install baostock` |
| **AkShare** | ≥1.14.0 | 兜底数据源 + 分钟线 | 数据获取 Tier3 | `pip install akshare` |
| **Qlib** | 参考范式 | 因子表达式定义规范 | 因子计算模块 | 轻量复刻，不直接依赖 |
| **东方财富WS** | - | 免费实时行情推送 | 独立守护进程 | 自建解析器 + Redis广播 |
| **QuantDinger** | 母版 | FastAPI/React/Celery骨架 | 全栈 | 裁剪非A股代码，保留架构 |
| **TradingView Charts** | ≥4.0 | 专业K线图表 | 前端 | `npm install lightweight-charts` |
| **Monaco Editor** | ≥0.47 | 代码编辑器 | 前端策略页 | `npm install @monaco-editor/react` |
| **shadcn/ui** | 最新 | UI组件库 | 前端全局 | `npx shadcn-ui@latest init` |

---

*本文档综合了 DeepSeek、豆包、Gemini、Grok、通义千问、龙猫 六份方案的精华，以实用落地为首要原则。最大化复用 Hikyuu、MyTT、AData 等成熟开源项目，聚焦A股差异化能力，减少自研工作量。*

*最后更新：2026-05-15*
