# Leek Quant 技术架构与开发文档

> 版本：v1.0  
> 日期：2026-05-15  
> 定位：基于 QuantDinger 裁剪的纯 A 股、本地优先、隐私至上的量化研究与模拟交易平台。  
> 核心策略：最大化复用 MyTT、AData、Baostock、AkShare、Qlib 范式等成熟能力；回测采用 Python-native 引擎，确保与平台五档信号、风控和模拟交易口径一致。

---

## 1. 项目定位与边界

Leek Quant 从 QuantDinger 的多市场、多资产、AI 量化平台做减法，只保留并强化 A 股研究、回测、信号、模拟交易与因子选股能力。平台默认部署在用户本地或私有服务器，数据、策略源码、账户记录和回测结果均保留在本地 PostgreSQL 与 Redis 中，不依赖中心化云端服务。

### 1.1 目标用户

- 个人投资者：需要本地化 A 股数据、策略回测、模拟交易训练。
- 独立量化研究者：需要可编程策略、因子研究、批量回测与 IC/IR 分析。
- 小型团队：需要多用户、多模拟账户隔离，但不需要复杂企业权限体系。

### 1.2 保留与裁剪

| 来源 | 保留 | 裁剪 |
| --- | --- | --- |
| QuantDinger | FastAPI / React / Celery / Docker Compose 骨架、前后端分离形态、异步任务模式 | 非 A 股市场、跨资产交易、云端托管、多市场行情适配、复杂 AI 训练平台 |
| Python-native BacktestRunner | A 股回测、五档信号、费用、风控和交易限制 | 不引入外部回测内核，保持结果口径一致 |
| Qlib | 因子表达式范式、因子计算与评估思路 | 不引入完整 Qlib 数据目录和重型训练流水线 |

### 1.3 核心功能范围

- A 股市场：全市场股票列表、动态筛选、ST / 退市标识。
- 历史 K 线：日 / 周 / 月，增量拉取，复权，停牌和退市数据完整保留。
- 自选股：按用户和分组管理，实时行情看板。
- 实时股价推送：东方财富 WebSocket 自建解析，或 AllTick 作为替代通道。
- 策略编辑：Monaco Editor 编写 Python 源码，内置 MyTT 函数提示。
- 五档信号：买入、增持、减仓、卖出、观望。
- A 股规则回测：T+1、涨跌停、印花税、佣金、过户费、停牌处理。
- 多因子打分：估值、成长、质量、动量、波动等，支持 IC / IR 分析。
- 模拟交易：完整委托、成交、持仓、资金流水、净值快照。
- 轻量用户系统：多用户、多模拟账户隔离。
- 定时任务与监控：数据更新、任务状态、异常告警。
- 交易日历服务：所有交易日判断统一从数据库查询。

---

## 2. 总体系统架构

Leek Quant 采用前后端分离 + 异步任务 + 统一 PostgreSQL 存储架构。历史数据、用户数据、策略、回测、因子和模拟交易全部进入 PostgreSQL；Redis 只承担队列、缓存和实时广播职责，避免 DuckDB + Parquet + SQLite 的多存储维护成本。

```mermaid
flowchart TB
    subgraph FE["前端层：React + Vite + Tailwind + shadcn/ui"]
        Dashboard["Dashboard / 自选股看板"]
        Market["全市场筛选"]
        StrategyUI["策略中心：Monaco Editor"]
        ChartUI["K线 / 净值 / 因子图表<br/>TradingView Lightweight Charts"]
        SimUI["模拟交易页面"]
    end

    subgraph API["API层：FastAPI"]
        Auth["轻量用户认证<br/>JWT / Session（M7+ 延后）"]
        DataAPI["行情 / 自选股 API"]
        StrategyAPI["策略 CRUD / 回测 API"]
        SignalAPI["信号 / 模拟交易 API"]
        WSAPI["WebSocket 推送网关"]
    end

    subgraph TASK["异步任务层：Celery + Redis"]
        DataWorker["数据 Worker<br/>股票列表 / K线 / 基本面 / 日历"]
        BacktestWorker["回测 Worker<br/>Python-native BacktestRunner"]
        FactorWorker["因子 Worker<br/>因子计算 / ICIR / 排名"]
        SimWorker["模拟交易 Worker<br/>撮合 / T+1 / 净值"]
        Beat["Celery Beat<br/>定时调度"]
    end

    subgraph ENGINE["计算与开源集成层"]
        BacktestEngine["BacktestRunner<br/>A股回测引擎"]
        MyTT["MyTT<br/>通达信/同花顺指标"]
        QlibLite["Qlib-like 因子表达式"]
        EastMoney["东方财富 WebSocket 解析器"]
    end

    subgraph DATA["数据源层"]
        AData["Tier1 AData<br/>股票列表 / 日周月K"]
        Baostock["Tier2 Baostock<br/>K线备源 / 财务三表 / 估值"]
        AkShare["Tier3 AkShare<br/>分钟线 / 补充数据 / 日历"]
        AllTick["AllTick<br/>实时行情备选"]
    end

    subgraph STORE["存储层"]
        PG[("PostgreSQL<br/>统一持久化存储")]
        Redis[("Redis<br/>Celery Broker / Cache / PubSub")]
    end

    Dashboard --> DataAPI
    Market --> DataAPI
    StrategyUI --> StrategyAPI
    ChartUI --> DataAPI
    SimUI --> SignalAPI
    FE --> WSAPI

    Auth --> PG
    DataAPI --> PG
    StrategyAPI --> PG
    SignalAPI --> PG
    WSAPI --> Redis

    Beat --> DataWorker
    Beat --> FactorWorker
    Beat --> SimWorker
    DataWorker --> AData
    DataWorker --> Baostock
    DataWorker --> AkShare
    DataWorker --> PG
    BacktestWorker --> BacktestEngine
    BacktestWorker --> MyTT
    BacktestWorker --> PG
    FactorWorker --> QlibLite
    FactorWorker --> MyTT
    FactorWorker --> PG
    SimWorker --> PG
    EastMoney --> Redis
    AllTick --> Redis
    Redis --> WSAPI
```

### 2.1 服务职责

| 服务 | 技术 | 职责 |
| --- | --- | --- |
| frontend | React + Vite + Tailwind + shadcn/ui | 页面、图表、策略编辑器、实时看板 |
| backend | FastAPI + SQLAlchemy + Alembic | REST API、WebSocket、认证、任务提交 |
| celery_worker | Celery | 数据拉取、回测、因子、模拟交易等耗时任务 |
| celery_beat | Celery Beat | 定时触发交易日历、K线更新、信号生成、净值快照 |
| realtime_risk_guard | Python asyncio | 模拟盘实时止盈/止损守护进程 |
| realtime_ws | Python asyncio / websockets | 东方财富流式解析服务（M6b 待实现；M6a 实时订阅由 backend `/ws/realtime` 提供） |
| postgres | PostgreSQL 15+ | 统一持久化存储 |
| redis | Redis 7 | Celery Broker / Result Backend、热点缓存、Pub/Sub |

### 2.2 关键架构决策

| 决策 | 方案 | 原因 |
| --- | --- | --- |
| 存储统一 | PostgreSQL 替代 DuckDB + Parquet + SQLite | 简化部署、事务一致、便于用户数据和行情数据联查 |
| K线分区 | `daily_kline` 按 `trade_date` 年分区 | 全市场多年 K 线数据量大，按日期范围查询频繁 |
| 数据源回退 | AData -> Baostock -> AkShare | 主源优先，备源补基本面，兜底源补分钟线和全市场数据 |
| 实时独立 | 东方财富 WebSocket / AllTick 不进入历史数据链路 | 历史拉取和盘中推送故障域隔离 |
| 回测异步 | Python-native BacktestRunner 只在 Celery Worker 调用 | 避免阻塞 FastAPI 事件循环，便于控制 CPU 资源 |
| 因子计算 | 简单聚合下推 PostgreSQL，复杂计算由 Worker 执行 | 减少数据搬运，保留 Python 灵活性 |
| 模拟交易 | 委托 -> 成交 -> 持仓 -> 流水 -> 净值 6 表闭环 | 便于审计、复盘、还原账户状态 |

---

## 3. PostgreSQL 完整表设计

### 3.1 命名与约束约定

- 股票代码统一为 `ts_code`，格式建议为 `600000.SH`、`000001.SZ`、`688001.SH`。
- 金额、价格、费率使用 `NUMERIC`，避免金融计算中的浮点误差。
- 用户相关数据必须带 `user_id` 或可经 `account_id` 追溯到 `user_id`。
- 核心时间字段使用 `TIMESTAMP WITH TIME ZONE`；交易日使用 `DATE`。
- 大对象如回测曲线、策略参数、财务原始报表使用 `JSONB`。
- 所有定时任务写入 `task_runs`，用于监控、告警和前端展示。

### 3.2 用户与权限

```sql
CREATE TABLE users (
    id              BIGSERIAL PRIMARY KEY,
    username        VARCHAR(64) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    display_name    VARCHAR(64),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

轻量用户系统只做本地多账户隔离，不做复杂 RBAC。后续如需要团队协作，可增加 `roles` 与 `user_permissions`，但首版避免过度设计。

### 3.3 基础与市场数据

#### 3.3.1 股票基础信息：`stock_basic`

```sql
CREATE TABLE stock_basic (
    ts_code        VARCHAR(10) PRIMARY KEY,
    symbol         VARCHAR(6) NOT NULL,
    name           VARCHAR(64) NOT NULL,
    market         VARCHAR(16),              -- 主板 / 创业板 / 科创板 / 北交所
    exchange       VARCHAR(8),               -- SH / SZ / BJ
    industry       VARCHAR(64),
    area           VARCHAR(32),
    list_date      DATE,
    delist_date    DATE,
    is_st          BOOLEAN NOT NULL DEFAULT FALSE,
    is_delisted    BOOLEAN NOT NULL DEFAULT FALSE,
    data_source    VARCHAR(20) NOT NULL DEFAULT 'adata',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_stock_basic_market ON stock_basic(market);
CREATE INDEX idx_stock_basic_industry ON stock_basic(industry);
CREATE INDEX idx_stock_basic_status ON stock_basic(is_st, is_delisted);
```

`is_st` 与 `is_delisted` 每日更新，市场筛选和回测默认可配置是否排除 ST / 退市股票。退市股票不删除，历史 K 线继续保留，保证历史回测完整。

#### 3.3.2 日线 K 线：`daily_kline`

```sql
CREATE TABLE daily_kline (
    ts_code         VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
    trade_date      DATE NOT NULL,
    open            NUMERIC(12,4),
    high            NUMERIC(12,4),
    low             NUMERIC(12,4),
    close           NUMERIC(12,4),
    pre_close       NUMERIC(12,4),
    volume          BIGINT,                  -- 股
    amount          NUMERIC(20,4),           -- 元
    turnover_rate   NUMERIC(12,6),
    adj_factor      NUMERIC(18,8),           -- 复权因子
    is_suspended    BOOLEAN NOT NULL DEFAULT FALSE,
    is_limit_up     BOOLEAN NOT NULL DEFAULT FALSE,
    is_limit_down   BOOLEAN NOT NULL DEFAULT FALSE,
    data_source     VARCHAR(20) NOT NULL,
    raw_payload     JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ts_code, trade_date)
) PARTITION BY RANGE (trade_date);

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

CREATE INDEX idx_daily_kline_date ON daily_kline(trade_date);
CREATE INDEX idx_daily_kline_code_date_desc ON daily_kline(ts_code, trade_date DESC);
```

周线和月线不建议首版单独拉取。首选从日线聚合生成物化视图，避免多源数据口径不一致。

> **M7+ 延后项**：周线/月线物化视图首版未实现。当前前端图表可直接从日线数据聚合展示周线/月线，无需数据库物化视图。

```sql
CREATE MATERIALIZED VIEW weekly_kline AS
SELECT
    ts_code,
    DATE_TRUNC('week', trade_date)::DATE AS week_start,
    (ARRAY_AGG(open ORDER BY trade_date))[1] AS open,
    MAX(high) AS high,
    MIN(low) AS low,
    (ARRAY_AGG(close ORDER BY trade_date DESC))[1] AS close,
    SUM(volume) AS volume,
    SUM(amount) AS amount,
    (ARRAY_AGG(adj_factor ORDER BY trade_date DESC))[1] AS adj_factor
FROM daily_kline
WHERE is_suspended = FALSE
GROUP BY ts_code, DATE_TRUNC('week', trade_date);

CREATE MATERIALIZED VIEW monthly_kline AS
SELECT
    ts_code,
    DATE_TRUNC('month', trade_date)::DATE AS month_start,
    (ARRAY_AGG(open ORDER BY trade_date))[1] AS open,
    MAX(high) AS high,
    MIN(low) AS low,
    (ARRAY_AGG(close ORDER BY trade_date DESC))[1] AS close,
    SUM(volume) AS volume,
    SUM(amount) AS amount,
    (ARRAY_AGG(adj_factor ORDER BY trade_date DESC))[1] AS adj_factor
FROM daily_kline
WHERE is_suspended = FALSE
GROUP BY ts_code, DATE_TRUNC('month', trade_date);
```

#### 3.3.3 交易日历：`trade_calendar`

```sql
CREATE TABLE trade_calendar (
    cal_date        DATE PRIMARY KEY,
    is_open         BOOLEAN NOT NULL,
    pretrade_date   DATE,
    nexttrade_date  DATE,
    is_weekend      BOOLEAN NOT NULL DEFAULT FALSE,
    is_holiday      BOOLEAN NOT NULL DEFAULT FALSE,
    source          VARCHAR(20) NOT NULL DEFAULT 'akshare',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

交易日历是系统基础服务，数据增量、T+1 解锁、回测区间、信号生成、净值快照都必须通过此表判断。

#### 3.3.4 基本面：`stock_fundamentals`

```sql
CREATE TABLE stock_fundamentals (
    ts_code             VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
    report_date         DATE NOT NULL,
    announce_date       DATE,
    pe_ttm              NUMERIC(12,4),
    pb                  NUMERIC(12,4),
    ps_ttm              NUMERIC(12,4),
    pcf_ttm             NUMERIC(12,4),
    roe                 NUMERIC(12,6),
    roa                 NUMERIC(12,6),
    market_cap          NUMERIC(20,4),
    float_market_cap    NUMERIC(20,4),
    dividend_yield      NUMERIC(12,6),
    revenue             NUMERIC(20,4),
    net_profit          NUMERIC(20,4),
    revenue_growth      NUMERIC(12,6),
    net_profit_growth   NUMERIC(12,6),
    gross_margin        NUMERIC(12,6),
    debt_to_equity      NUMERIC(12,6),
    current_ratio       NUMERIC(12,6),
    free_cash_flow      NUMERIC(20,4),
    income_statement    JSONB,
    balance_sheet       JSONB,
    cashflow_statement  JSONB,
    data_source         VARCHAR(20) NOT NULL DEFAULT 'baostock',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ts_code, report_date)
);

CREATE INDEX idx_fundamentals_report_date ON stock_fundamentals(report_date DESC);
CREATE INDEX idx_fundamentals_code_date ON stock_fundamentals(ts_code, report_date DESC);
```

### 3.4 用户、自选股与策略

> **设计决策**：首版已移除 `stock_pools` / `stock_pool_items` 表。回测目标筛选改为 `backtest_results.params_snapshot.target` 配置（支持 `all` / `market` / `watchlist_group` 三种作用域），因子排行改为 `scoring_rank.scope_type` + `scope_value` 作用域。股票池的动态筛选和因子导入功能延后至 M7+。

```sql
CREATE TABLE watchlist (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    group_name    VARCHAR(64) NOT NULL DEFAULT '默认',
    ts_code       VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
    sort_order    INTEGER NOT NULL DEFAULT 0,
    note          TEXT,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, group_name, ts_code)
);

CREATE INDEX idx_watchlist_user_group ON watchlist(user_id, group_name, sort_order);

CREATE TABLE strategies (
    id             BIGSERIAL PRIMARY KEY,
    user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name           VARCHAR(100) NOT NULL,
    description    TEXT,
    source_code    TEXT NOT NULL,
    config         JSONB NOT NULL DEFAULT '{}'::JSONB,
    version        INTEGER NOT NULL DEFAULT 1,
    status         VARCHAR(20) NOT NULL DEFAULT 'draft',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at    TIMESTAMPTZ,
    CHECK (status IN ('draft', 'active', 'paused', 'archived'))
);

CREATE INDEX idx_strategies_user_status ON strategies(user_id, status);
```

`strategies.config` 示例（含回测目标筛选和风控配置）：

```json
{
  "fee_config": {"commission_rate": 0.00025, "min_commission": 5.0},
  "risk_config": {"stop_loss_pct": 0.08, "take_profit_pct": 0.20}
}
```

`backtest_results.params_snapshot.target` 示例：

```json
{
  "type": "market",
  "value": ["主板", "创业板"],
  "filters": {"exclude_st": true, "exclude_loss_pe": true}
}
```

### 3.5 回测与五档信号

```sql
CREATE TABLE backtest_results (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    strategy_id      BIGINT REFERENCES strategies(id) ON DELETE SET NULL,
    task_id          VARCHAR(128),
    start_date       DATE NOT NULL,
    end_date         DATE NOT NULL,
    initial_cash     NUMERIC(20,4) NOT NULL,
    benchmark_code   VARCHAR(16),
    params_snapshot  JSONB NOT NULL DEFAULT '{}'::JSONB,
    total_return     NUMERIC(14,8),
    annual_return    NUMERIC(14,8),
    sharpe_ratio     NUMERIC(14,8),
    max_drawdown     NUMERIC(14,8),
    annual_vol       NUMERIC(14,8),
    win_rate         NUMERIC(14,8),
    trade_count      INTEGER,
    performance      JSONB NOT NULL DEFAULT '{}'::JSONB,
    trade_records    JSONB NOT NULL DEFAULT '[]'::JSONB,
    equity_curve     JSONB NOT NULL DEFAULT '[]'::JSONB,
    status           VARCHAR(20) NOT NULL DEFAULT 'pending',
    error_message    TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    CHECK (status IN ('pending', 'running', 'success', 'failed', 'cancelled'))
);

CREATE INDEX idx_backtest_user_created ON backtest_results(user_id, created_at DESC);
CREATE INDEX idx_backtest_strategy ON backtest_results(strategy_id, created_at DESC);

CREATE TABLE signal_log (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    strategy_id      BIGINT REFERENCES strategies(id) ON DELETE SET NULL,
    account_id       BIGINT,
    ts_code          VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
    trade_date       DATE NOT NULL,
    signal_type      VARCHAR(10) NOT NULL,
    target_position  NUMERIC(8,6) NOT NULL DEFAULT 0,
    current_position NUMERIC(8,6) NOT NULL DEFAULT 0,
    action           VARCHAR(32),              -- BUY / ADD / REDUCE / SELL / HOLD / BLOCKED
    confidence       NUMERIC(8,6),
    reason           TEXT,
    snapshot         JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (signal_type IN ('买入', '增持', '减仓', '卖出', '观望'))
);

CREATE INDEX idx_signal_user_date ON signal_log(user_id, trade_date DESC);
CREATE INDEX idx_signal_code_date ON signal_log(ts_code, trade_date DESC);
CREATE INDEX idx_signal_strategy_date ON signal_log(strategy_id, trade_date DESC);
```

### 3.6 因子与打分

```sql
CREATE TABLE factor_definitions (
    name             VARCHAR(64) PRIMARY KEY,
    display_name     VARCHAR(100),
    category         VARCHAR(32) NOT NULL,     -- valuation / growth / quality / momentum / volatility
    expression       TEXT NOT NULL,
    direction        SMALLINT NOT NULL DEFAULT 1, -- 1 越大越好，-1 越小越好
    default_weight   NUMERIC(10,6) NOT NULL DEFAULT 1,
    enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    description      TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE factor_values (
    ts_code          VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
    trade_date       DATE NOT NULL,
    factor_name      VARCHAR(64) NOT NULL REFERENCES factor_definitions(name),
    value            NUMERIC(20,8),
    normalized_value NUMERIC(20,8),
    percentile_rank  NUMERIC(10,8),
    data_source      VARCHAR(20) NOT NULL DEFAULT 'computed',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ts_code, trade_date, factor_name)
);

CREATE INDEX idx_factor_values_date_name ON factor_values(trade_date, factor_name);
CREATE INDEX idx_factor_values_name_date ON factor_values(factor_name, trade_date DESC);

CREATE TABLE scoring_rank (
    id               BIGSERIAL PRIMARY KEY,
    trade_date       DATE NOT NULL,
    ts_code          VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
    scope_type       VARCHAR(32) NOT NULL DEFAULT 'all',
    scope_value      VARCHAR(128),
    total_score      NUMERIC(20,8) NOT NULL,
    rank             INTEGER NOT NULL,
    percentile_rank  NUMERIC(10,8),
    factor_breakdown JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (scope_type IN ('all', 'watchlist_group'))
);

CREATE UNIQUE INDEX uq_scoring_rank_scope ON scoring_rank(
    trade_date,
    ts_code,
    scope_type,
    COALESCE(scope_value, '')
);
CREATE INDEX idx_scoring_rank_date_rank ON scoring_rank(trade_date DESC, scope_type, rank);
CREATE INDEX idx_scoring_rank_scope_date ON scoring_rank(scope_type, scope_value, trade_date DESC, rank);

CREATE TABLE factor_analysis (
    id               BIGSERIAL PRIMARY KEY,
    factor_name      VARCHAR(64) NOT NULL REFERENCES factor_definitions(name),
    period_start     DATE NOT NULL,
    period_end       DATE NOT NULL,
    forward_days     INTEGER NOT NULL DEFAULT 5,
    ic               NUMERIC(14,8),
    ic_mean          NUMERIC(14,8),
    ic_std           NUMERIC(14,8),
    ir               NUMERIC(14,8),
    icir             NUMERIC(14,8),
    ic_gt_0_pct      NUMERIC(10,8),
    group_returns    JSONB NOT NULL DEFAULT '{}'::JSONB,
    details          JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_factor_analysis_name_period ON factor_analysis(factor_name, period_start, period_end);
```

### 3.7 模拟交易 6 表完整体系

#### 3.7.1 模拟账户：`sim_accounts`

```sql
CREATE TABLE sim_accounts (
    id                BIGSERIAL PRIMARY KEY,
    user_id           BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    strategy_id        BIGINT REFERENCES strategies(id) ON DELETE SET NULL,
    name              VARCHAR(100) NOT NULL,
    initial_cash      NUMERIC(20,4) NOT NULL,
    available_cash    NUMERIC(20,4) NOT NULL,
    frozen_cash       NUMERIC(20,4) NOT NULL DEFAULT 0,
    total_asset       NUMERIC(20,4) NOT NULL,
    status            VARCHAR(20) NOT NULL DEFAULT 'active',
    config            JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('active', 'paused', 'closed'))
);

CREATE INDEX idx_sim_accounts_user_status ON sim_accounts(user_id, status);
```

#### 3.7.2 当前持仓：`sim_positions`

```sql
CREATE TABLE sim_positions (
    id                BIGSERIAL PRIMARY KEY,
    account_id        BIGINT NOT NULL REFERENCES sim_accounts(id) ON DELETE CASCADE,
    ts_code           VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
    shares            INTEGER NOT NULL DEFAULT 0,
    available_shares  INTEGER NOT NULL DEFAULT 0, -- T+1 可卖数量
    frozen_shares     INTEGER NOT NULL DEFAULT 0,
    avg_cost          NUMERIC(12,4) NOT NULL DEFAULT 0,
    current_price     NUMERIC(12,4),
    market_value      NUMERIC(20,4) NOT NULL DEFAULT 0,
    unrealized_pnl    NUMERIC(20,4) NOT NULL DEFAULT 0,
    profit_rate       NUMERIC(14,8) NOT NULL DEFAULT 0,
    first_buy_date    DATE,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (account_id, ts_code),
    CHECK (shares >= 0),
    CHECK (available_shares >= 0),
    CHECK (frozen_shares >= 0)
);

CREATE INDEX idx_sim_positions_account ON sim_positions(account_id);
```

#### 3.7.3 委托单：`sim_orders`

```sql
CREATE TABLE sim_orders (
    id                BIGSERIAL PRIMARY KEY,
    account_id        BIGINT NOT NULL REFERENCES sim_accounts(id) ON DELETE CASCADE,
    signal_id         BIGINT REFERENCES signal_log(id) ON DELETE SET NULL,
    ts_code           VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
    direction         VARCHAR(4) NOT NULL,
    order_type        VARCHAR(10) NOT NULL DEFAULT '限价',
    price             NUMERIC(12,4),
    volume            INTEGER NOT NULL,
    filled_volume     INTEGER NOT NULL DEFAULT 0,
    frozen_amount     NUMERIC(20,4) NOT NULL DEFAULT 0,
    status            VARCHAR(20) NOT NULL DEFAULT '待成交',
    reject_reason     TEXT,  -- 最近一次撮合未成交原因，保留待成交时也会写入
    submit_time       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    update_time       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cancel_time       TIMESTAMPTZ,
    CHECK (direction IN ('买入', '卖出')),
    CHECK (order_type IN ('限价', '市价')),
    CHECK (status IN ('待成交', '部分成交', '全部成交', '已撤单', '已拒绝', '已过期')),
    CHECK (volume > 0),
    CHECK (filled_volume >= 0)
);

CREATE INDEX idx_sim_orders_account_status ON sim_orders(account_id, status);
CREATE INDEX idx_sim_orders_code_time ON sim_orders(ts_code, submit_time DESC);
```

#### 3.7.4 成交记录：`sim_trades`

```sql
CREATE TABLE sim_trades (
    id                BIGSERIAL PRIMARY KEY,
    order_id          BIGINT NOT NULL REFERENCES sim_orders(id) ON DELETE CASCADE,
    account_id        BIGINT NOT NULL REFERENCES sim_accounts(id) ON DELETE CASCADE,
    ts_code           VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
    direction         VARCHAR(4) NOT NULL,
    price             NUMERIC(12,4) NOT NULL,
    volume            INTEGER NOT NULL,
    amount            NUMERIC(20,4) NOT NULL,
    stamp_tax         NUMERIC(20,4) NOT NULL DEFAULT 0,
    commission        NUMERIC(20,4) NOT NULL DEFAULT 0,
    transfer_fee      NUMERIC(20,4) NOT NULL DEFAULT 0,
    total_fee         NUMERIC(20,4) NOT NULL DEFAULT 0,
    trade_time        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (direction IN ('买入', '卖出')),
    CHECK (volume > 0)
);

CREATE INDEX idx_sim_trades_account_time ON sim_trades(account_id, trade_time DESC);
CREATE INDEX idx_sim_trades_order ON sim_trades(order_id);
```

#### 3.7.5 资金流水：`sim_cash_flow`

```sql
CREATE TABLE sim_cash_flow (
    id                BIGSERIAL PRIMARY KEY,
    account_id        BIGINT NOT NULL REFERENCES sim_accounts(id) ON DELETE CASCADE,
    related_trade_id  BIGINT REFERENCES sim_trades(id) ON DELETE SET NULL,
    flow_type         VARCHAR(20) NOT NULL,
    amount            NUMERIC(20,4) NOT NULL,      -- 正数入账，负数出账
    balance_after     NUMERIC(20,4) NOT NULL,
    remark            TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (flow_type IN ('买入', '卖出', '手续费', '分红', '利息', '充值', '调整', '冻结', '解冻'))
);

CREATE INDEX idx_cash_flow_account_time ON sim_cash_flow(account_id, created_at DESC);
```

#### 3.7.6 每日净值快照：`sim_daily_nav`

```sql
CREATE TABLE sim_daily_nav (
    id                BIGSERIAL PRIMARY KEY,
    account_id        BIGINT NOT NULL REFERENCES sim_accounts(id) ON DELETE CASCADE,
    nav_date          DATE NOT NULL,
    total_asset       NUMERIC(20,4) NOT NULL,
    available_cash    NUMERIC(20,4) NOT NULL,
    frozen_cash       NUMERIC(20,4) NOT NULL DEFAULT 0,
    position_value    NUMERIC(20,4) NOT NULL DEFAULT 0,
    daily_return      NUMERIC(14,8) NOT NULL DEFAULT 0,
    cumulative_nav    NUMERIC(14,8) NOT NULL DEFAULT 1,
    max_drawdown      NUMERIC(14,8) NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (account_id, nav_date)
);

CREATE INDEX idx_sim_daily_nav_account_date ON sim_daily_nav(account_id, nav_date DESC);
```

### 3.8 数据状态、任务与告警

```sql
CREATE TABLE data_update_state (
    id                BIGSERIAL PRIMARY KEY,
    data_type         VARCHAR(32) NOT NULL,       -- stock_basic / daily_kline / fundamentals / calendar
    ts_code           VARCHAR(10),
    source            VARCHAR(20),
    last_trade_date   DATE,
    last_success_at   TIMESTAMPTZ,
    last_failure_at   TIMESTAMPTZ,
    failure_count     INTEGER NOT NULL DEFAULT 0,
    error_message     TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (data_type, ts_code, source)
);

CREATE TABLE task_runs (
    id                BIGSERIAL PRIMARY KEY,
    task_name         VARCHAR(128) NOT NULL,
    task_id           VARCHAR(128),
    status            VARCHAR(20) NOT NULL,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at       TIMESTAMPTZ,
    duration_ms       INTEGER,
    payload           JSONB NOT NULL DEFAULT '{}'::JSONB,
    result            JSONB NOT NULL DEFAULT '{}'::JSONB,
    error_message     TEXT,
    CHECK (status IN ('pending', 'running', 'success', 'failed', 'cancelled'))
);

CREATE INDEX idx_task_runs_name_started ON task_runs(task_name, started_at DESC);

CREATE TABLE alert_events (
    id                BIGSERIAL PRIMARY KEY,
    level             VARCHAR(16) NOT NULL,
    category          VARCHAR(32) NOT NULL,
    title             VARCHAR(200) NOT NULL,
    message           TEXT,
    payload           JSONB NOT NULL DEFAULT '{}'::JSONB,
    is_resolved       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at       TIMESTAMPTZ,
    CHECK (level IN ('info', 'warning', 'error', 'critical'))
);
```

---

## 4. 数据源多层回退与增量拉取设计

### 4.1 数据源职责

| 层级 | 数据源 | 主要用途 | 失败后动作 |
| --- | --- | --- | --- |
| Tier1 | AData | A 股股票列表、日 / 周 / 月 K 线、基础行情 | 记录失败，切 Baostock |
| Tier2 | Baostock | 日 K 线备源、财务三表、估值、ROE 等基本面 | 记录失败，切 AkShare |
| Tier3 | AkShare | 分钟 K 线、交易日历、全市场补充数据、兜底 K 线 | 失败后告警并等待重试 |
| 实时独立 | 东方财富 WebSocket | 盘中实时价、涨跌幅、成交量、成交额 | 重连；必要时切 AllTick |

### 4.2 标准化数据模型

所有数据源返回后必须进入统一字段模型，再批量写入 PostgreSQL：

```python
NormalizedKline = {
    "ts_code": "600000.SH",
    "trade_date": "2026-05-15",
    "open": 10.01,
    "high": 10.55,
    "low": 9.98,
    "close": 10.30,
    "pre_close": 10.00,
    "volume": 123456789,
    "amount": 1234567890.12,
    "adj_factor": 1.23456789,
    "is_suspended": False,
    "data_source": "adata",
    "raw_payload": {}
}
```

字段转换集中放在 `backend/app/data/normalizer.py`，禁止各业务模块直接依赖 AData、Baostock、AkShare 的原始列名。

### 4.3 回退流程

```mermaid
flowchart TD
    Start["请求 K线: ts_code + start/end + adjust"] --> CheckState["读取 data_update_state 与源健康状态"]
    CheckState --> AData["尝试 AData"]
    AData --> ValidateA{"字段/日期/成交量校验通过?"}
    ValidateA -->|是| Normalize["标准化字段 + 复权因子校准"]
    ValidateA -->|否| Bao["尝试 Baostock"]
    Bao --> ValidateB{"校验通过?"}
    ValidateB -->|是| Normalize
    ValidateB -->|否| Ak["尝试 AkShare"]
    Ak --> ValidateC{"校验通过?"}
    ValidateC -->|是| Normalize
    ValidateC -->|否| Fail["记录失败 + alert_events + Celery retry"]
    Normalize --> Upsert["INSERT ... ON CONFLICT DO UPDATE/NOTHING"]
    Upsert --> State["更新 data_update_state"]
```

### 4.4 数据校验规则

- `trade_date` 必须是交易日历中的开市日，停牌记录可保留但需 `is_suspended = TRUE`。
- `high >= GREATEST(open, close, low)`，`low <= LEAST(open, close, high)`。
- `volume >= 0`，`amount >= 0`。
- 非停牌日 `open/high/low/close` 不应为空。
- 同一股票相邻交易日涨跌幅超出板块限制时标记异常，等待二次源校验。
- 复权因子为空时先按未复权入库，并记录 `alert_events`，后续补齐。

### 4.5 增量拉取

增量更新只拉取每只股票最新 `trade_date` 之后的数据。

```python
def get_incremental_range(db, ts_code: str) -> tuple[date, date] | None:
    last_date = db.scalar("""
        SELECT MAX(trade_date)
        FROM daily_kline
        WHERE ts_code = :ts_code
    """, {"ts_code": ts_code})

    latest_trade_date = get_latest_trade_date(db)
    if last_date is None:
        list_date = db.scalar(
            "SELECT list_date FROM stock_basic WHERE ts_code = :ts_code",
            {"ts_code": ts_code},
        )
        return list_date, latest_trade_date

    start = get_next_trade_date(db, last_date)
    if start > latest_trade_date:
        return None
    return start, latest_trade_date
```

写入采用幂等 upsert：

```sql
INSERT INTO daily_kline (
    ts_code, trade_date, open, high, low, close, pre_close,
    volume, amount, adj_factor, is_suspended, data_source, raw_payload
)
VALUES (...)
ON CONFLICT (ts_code, trade_date)
DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    pre_close = EXCLUDED.pre_close,
    volume = EXCLUDED.volume,
    amount = EXCLUDED.amount,
    adj_factor = COALESCE(EXCLUDED.adj_factor, daily_kline.adj_factor),
    is_suspended = EXCLUDED.is_suspended,
    data_source = EXCLUDED.data_source,
    raw_payload = EXCLUDED.raw_payload,
    updated_at = NOW();
```

### 4.6 停牌、退市与复权

- 停牌日保留记录：价格沿用 `pre_close` 或上一个交易日收盘价，`volume = 0`，`amount = 0`，`is_suspended = TRUE`。
- 退市股票不再增量拉取最新行情，但保留 `stock_basic.is_delisted = TRUE` 和历史数据。
- 复权以 `adj_factor` 为基础，前端和回测可按配置生成前复权、后复权或不复权序列。
- 数据源切换时，以 AData 为主基准；如果备用源补齐缺口，记录 `data_source`，后续主源恢复后可校准。

### 4.7 实时行情推送

实时行情不写入 `daily_kline`，只进入 Redis 和前端 WebSocket：

```mermaid
sequenceDiagram
    participant EM as 东方财富 WebSocket
    participant Parser as backend 实时行情解析
    participant Redis as Redis Pub/Sub
    participant API as FastAPI WebSocket
    participant UI as React 自选股看板

    EM->>Parser: 推送原始行情
    Parser->>Parser: 解析/标准化 tick
    Parser->>Redis: PUBLISH realtime:{ts_code}
    API->>Redis: SUBSCRIBE realtime:*
    API->>UI: 推送 tick JSON
```

标准 tick：

```json
{
  "ts_code": "600000.SH",
  "price": 10.32,
  "change": 0.12,
  "change_pct": 1.18,
  "volume": 12345600,
  "amount": 126000000.0,
  "bid1": 10.31,
  "ask1": 10.32,
  "ts": 1778835600000
}
```

---

## 5. Python-native 回测引擎方案

### 5.1 集成原则

- Python-native `BacktestRunner` 是 v1 回测内核，不引入 Hikyuu 外部依赖。
- 回测逻辑与五档信号、费用、涨跌停、停牌和风险控制使用同一套平台规则。
- 用户策略源码保存在 `strategies.source_code`，回测时由 Worker 在隔离进程中编译和运行。
- 回测结果统一序列化为 `backtest_results.performance`、`trade_records`、`equity_curve`。

### 5.2 适配层边界

```text
FastAPI
  POST /api/backtests
    -> 创建 backtest_results(status=pending)
    -> celery.send_task("run_backtest", backtest_id)

Celery Worker
  -> 加载 strategy / daily_kline
  -> BacktestRunner
       1. PostgreSQL K线 -> KBar
       2. 用户 Python 策略 -> 五档信号
       3. A股费用、涨跌停、停牌、风控 -> 平台规则
       4. run
       5. 结果提取 -> JSONB
  -> 更新 backtest_results(status=success/failed)
```

### 5.3 适配器接口

```python
runner = BacktestRunner(config)
results = runner.run(all_klines)
results["engine"] = "python_native"
results["performance"]["engine"] = "python_native"
```

### 5.4 数据对接

回测任务从 PostgreSQL 读取日 K 数据并转换为 `KBar`，不通过外部回测数据驱动。

```python
async def load_market_data(session, ts_code: str, start: date, end: date) -> list[KBar]:
    result = await session.execute(text("""
        SELECT trade_date, open, high, low, close, volume, amount, adj_factor
        FROM daily_kline
        WHERE ts_code = :ts_code
          AND trade_date BETWEEN :start AND :end
        ORDER BY trade_date
    """), {"ts_code": ts_code, "start": start, "end": end})

    return [
        KBar(...)
        for row in result.mappings().all()
    ]
```

### 5.5 A 股规则适配

| 规则 | 回测处理 | 模拟交易处理 |
| --- | --- | --- |
| T+1 | 平台回测规则禁止当日买入后当日卖出 | `available_shares` 次交易日 09:25 解锁 |
| 涨跌停 | 根据信号日 `pre_close`、ST 状态、板块规则判断是否可成交 | 买入涨停、卖出跌停时挂单或延后 |
| 印花税 | 卖出收取，费率配置化 | `sim_trades.stamp_tax` 记录 |
| 佣金 | 双向收取，最低 5 元 | `sim_trades.commission` 记录 |
| 过户费 | 按市场配置，默认沪深双向可配置 | `sim_trades.transfer_fee` 记录 |
| 停牌 | 停牌日不生成成交 | `is_suspended` 拦截委托 |
| ST / 退市 | 策略可排除，涨跌幅限制不同 | 下单前按 `stock_basic` 状态校验 |

费用模型配置建议：

```json
{
  "commission_rate": 0.00025,
  "min_commission": 5.0,
  "stamp_tax_rate": 0.0005,
  "transfer_fee_rate": 0.00001,
  "slippage_bps": 5,
  "limit_up_down": {
    "main": 0.10,
    "st": 0.05,
    "star": 0.20,
    "chinext": 0.20
  }
}
```

### 5.6 策略代码约定

用户在 Monaco Editor 中写 Python 策略。首版提供两种接口：

#### 简化信号接口

```python
from MyTT import *

def generate_signal(ctx):
    close = ctx.close
    ma5 = MA(close, 5)[-1]
    ma20 = MA(close, 20)[-1]

    if ma5 > ma20:
        return {
            "signal_type": "买入",
            "target_position": 1.0,
            "reason": "MA5 > MA20"
        }
    return {"signal_type": "观望", "target_position": ctx.current_position}
```

### 5.7 运行安全

用户策略不应直接在 FastAPI 进程内 `exec`。推荐：

- 在 Celery Worker 中启动隔离子进程执行策略。
- 限制运行时间、内存、文件系统写入和网络访问。
- 策略上下文只暴露 `ctx`、MyTT 指标函数、白名单数学库。
- 策略执行异常写入 `backtest_results.error_message` 和 `task_runs`。

---

## 6. 五档信号生成逻辑与状态机

### 6.1 信号定义

| 信号 | 语义 | 默认目标仓位 |
| --- | --- | --- |
| 买入 | 空仓建仓或持仓时加到强目标仓位 | 1.00 |
| 增持 | 看多但非强信号，增加部分仓位 | 当前仓位 + 0.25，空仓时 0.50 |
| 减仓 | 降低风险暴露，保留部分仓位 | 当前仓位 - 0.25 |
| 卖出 | 清仓 | 0.00 |
| 观望 | 不调整 | 当前仓位 |

### 6.2 状态机

```mermaid
stateDiagram-v2
    [*] --> Empty
    Empty --> Long: 买入/增持
    Empty --> Empty: 观望/减仓/卖出
    Long --> Long: 买入/增持/观望/减仓
    Long --> Empty: 卖出
```

| 当前仓位 | 买入 | 增持 | 减仓 | 卖出 | 观望 |
| --- | --- | --- | --- | --- | --- |
| 空仓 | 买入至目标仓位 | 视为买入 | 无操作 | 无操作 | 无操作 |
| 低仓 | 增加至目标仓位 | 增加部分仓位 | 无操作或降至目标 | 清仓 | 维持 |
| 中仓 | 增加至目标仓位 | 视目标而定 | 降低部分仓位 | 清仓 | 维持 |
| 满仓 | 无操作或目标更高时维持 | 无操作 | 降低部分仓位 | 清仓 | 维持 |

### 6.3 伪代码

```python
def map_signal_to_action(signal_type: str, current_ratio: float, target_ratio: float | None):
    if signal_type == "观望":
        return "HOLD", current_ratio

    if current_ratio <= 0:
        if signal_type in ("买入", "增持"):
            default_target = 1.0 if signal_type == "买入" else 0.5
            return "BUY", target_ratio or default_target
        return "HOLD", 0.0

    if signal_type == "买入":
        target = target_ratio or 1.0
        return ("BUY", target) if target > current_ratio else ("HOLD", current_ratio)

    if signal_type == "增持":
        target = target_ratio or min(current_ratio + 0.25, 1.0)
        return ("BUY", target) if target > current_ratio else ("HOLD", current_ratio)

    if signal_type == "减仓":
        target = target_ratio if target_ratio is not None else max(current_ratio - 0.25, 0.0)
        return ("SELL_PARTIAL", target) if target < current_ratio else ("HOLD", current_ratio)

    if signal_type == "卖出":
        return "SELL_ALL", 0.0

    raise ValueError(f"unknown signal_type: {signal_type}")
```

### 6.4 A 股规则过滤

状态机只决定“想做什么”，规则过滤决定“现在能不能做”：

1. 非交易日：信号记录为 `HOLD` 或顺延到下一个交易日。
2. 停牌：不生成成交，模拟交易中委托保持待成交或过期。
3. T+1：当日买入形成的持仓不可用于卖出。
4. 涨停：买入信号可生成委托，但撮合可能不成交。
5. 跌停：卖出信号可生成委托，但撮合可能不成交。
6. ST：涨跌幅限制按 5% 校验。

下单前被规则过滤的信号仍写入 `signal_log`，`action = 'BLOCKED'`，`snapshot` 记录原因，便于复盘。涨停买入、跌停卖出不在下单前写 `BLOCKED`：信号和委托照常生成，撮合阶段不成交，`sim_orders.status` 保持 `待成交`，`reject_reason` 记录最近一次撮合阻断原因。

### 6.5 盘中持仓调仓

盘中持仓调仓用于模拟账户已有持仓的实时再平衡，不承担全市场扫描和盘中新开仓职责。任务入口为 `app.tasks.signal_tasks.generate_intraday_position_signals`，内部调用 `generate_intraday_position_signals_for_date()`。

任务行为：

1. 仅扫描 active 模拟账户中的已有持仓。
2. 账户必须绑定 active 策略。
3. 仅接受 `增持`、`减仓`、`卖出`、`观望`；`买入` 信号在盘中调仓中跳过，避免从空仓发起盘中新开仓。
4. 同一账户同一股票已有待成交或部分成交委托时跳过，避免重复冻结资金或持仓。
5. 无实时行情、无历史 K 线、已有待成交委托、信号不支持等情况写入任务返回的 `order_skip_reasons`。
6. 策略异常写入任务返回的 `errors`，包含账户、策略和股票上下文。

触发窗口：

1. 只在交易日运行，交易日判断必须查询 `trade_calendar.is_open`。
2. 只在 `09:25-11:30`、`13:00-15:00` 生效，窗口外直接返回 `skipped`。
3. 首版不默认加入高频 Celery Beat；可通过 Celery task 手动触发或由内部运维入口触发。
4. 若后续启用自动调度，建议 1-5 分钟频率，并保留交易窗口检查、待成交委托去重和任务结果审计。

风控边界：

1. 实时止盈/止损由 `realtime_risk_guard` 负责，优先级高于策略调仓。
2. 盘中调仓复用模拟交易统一下单规则，包括 T+1、停牌、资金/持仓、100 股交易单位和涨跌停限制。
3. 当日买入形成的持仓不可被盘中调仓卖出，卖出数量最多为 `available_shares`。
4. 涨停买入、跌停卖出允许生成委托，但撮合阶段阻断或保持待成交，阻断原因写入订单结果或 `sim_orders.reject_reason`。

---

## 7. 模拟交易引擎工作流

模拟交易是平台的核心闭环，必须满足可审计、可重放、账户资产守恒。

```mermaid
flowchart LR
    Signal["signal_log<br/>五档信号"] --> Order["sim_orders<br/>委托"]
    Order --> Rule["下单前规则校验<br/>交易日/T+1/停牌/资金"]
    Rule --> Match["撮合引擎<br/>市价/限价/最新价/收盘价"]
    Match --> Pending["sim_orders<br/>待成交/reject_reason"]
    Match --> Trade["sim_trades<br/>成交"]
    Trade --> Position["sim_positions<br/>持仓更新"]
    Trade --> Cash["sim_cash_flow<br/>资金流水"]
    Position --> Account["sim_accounts<br/>现金/资产更新"]
    Cash --> Account
    Account --> Nav["sim_daily_nav<br/>每日净值"]
```

### 7.1 下单流程

1. 策略或用户生成信号。
2. 状态机计算目标仓位。
3. 根据账户总资产、当前价格、最小交易单位 100 股计算委托数量。
4. 买入冻结资金，卖出冻结可用持仓。
5. 插入 `sim_orders(status='待成交')`。

买入数量：

```python
target_value = account.total_asset * target_position
current_value = position.market_value if position else 0
delta_value = max(target_value - current_value, 0)
raw_shares = int(delta_value / price)
volume = raw_shares // 100 * 100
```

卖出数量：

```python
target_value = account.total_asset * target_position
current_value = position.market_value
sell_value = max(current_value - target_value, 0)
raw_shares = int(sell_value / price)
volume = min(position.available_shares, raw_shares // 100 * 100)
```

### 7.2 撮合模式

| 模式 | 适用场景 | 成交价 |
| --- | --- | --- |
| 最新价撮合 | 实时行情在线、盘中模拟 | Redis tick 最新价或买一/卖一 |
| 收盘价撮合 | 日线策略、收盘后信号 | 当日或下个交易日收盘价 |
| 开盘价撮合 | 收盘信号次日执行 | 下个交易日开盘价 |
| 限价撮合 | 用户指定价格 | 买入价 >= 卖一或当日 low <= price；卖出价 <= 买一或当日 high >= price |

首版可实现“收盘价撮合”和“最新价撮合”，订单簿五档深度作为增强项。

### 7.2.1 盘中调仓撮合

盘中持仓调仓通过 `generate_order_from_signal(..., auto_match=True, auto_match_mode="limit")` 生成委托并立即尝试限价撮合。该路径不改变模拟交易的统一账户闭环，成交后仍写入 `sim_orders`、`sim_trades`、`sim_positions`、`sim_cash_flow`，并由日终净值任务刷新 `sim_daily_nav`。

盘中委托价格取实时盘口一档：

| 信号 | 委托方向 | 价格来源 |
| --- | --- | --- |
| 增持 | 买入 | `ask1`，缺失时回退最新价 |
| 减仓 | 卖出部分 | `bid1`，缺失时回退最新价 |
| 卖出 | 清仓 | `bid1`，缺失时回退最新价 |
| 观望 | 不下单 | 不适用 |

撮合仍执行 A 股规则：T+1 未解锁持仓不能卖出，停牌不能成交，买入数量按 100 股取整，资金不足不下单，涨跌停在撮合阶段阻断或保持待成交。

### 7.3 费用模型

默认配置：

```python
class AShareCostCalculator:
    commission_rate = 0.00025
    min_commission = 5.0
    stamp_tax_rate = 0.0005
    transfer_fee_rate = 0.00001

    def calculate(self, direction: str, amount: Decimal) -> dict:
        commission = max(amount * self.commission_rate, Decimal("5.0"))
        stamp_tax = amount * self.stamp_tax_rate if direction == "卖出" else Decimal("0")
        transfer_fee = amount * self.transfer_fee_rate
        total_fee = commission + stamp_tax + transfer_fee
        return {
            "commission": commission,
            "stamp_tax": stamp_tax,
            "transfer_fee": transfer_fee,
            "total_fee": total_fee,
        }
```

费率必须配置化，因为印花税和过户费规则可能变化。

### 7.4 T+1 解锁

- 买入成交后增加 `shares`，但不增加 `available_shares`。
- 下一个交易日 09:25 定时任务根据前一交易日买入成交记录解锁。
- 卖出时只允许使用 `available_shares`。

```python
def unlock_t1_positions(db, trade_date: date):
    prev_date = get_prev_trade_date(db, trade_date)
    buy_trades = query_buy_trades(db, prev_date)
    for trade in buy_trades:
        pos = get_position(db, trade.account_id, trade.ts_code)
        pos.available_shares += trade.volume
```

### 7.5 日终净值

每个交易日收盘后：

1. 用 `daily_kline.close` 或实时收盘价更新持仓市值。
2. `total_asset = available_cash + frozen_cash + SUM(position_value)`。
3. `daily_return = total_asset / prev_total_asset - 1`。
4. `cumulative_nav = prev_nav * (1 + daily_return)`。
5. 写入 `sim_daily_nav`。

净值快照只追加或同日覆盖，不依赖前端计算。

---

## 8. 多因子打分模块设计

### 8.1 因子分类

| 类别 | 因子示例 | 数据来源 | 更新频率 |
| --- | --- | --- | --- |
| 估值 | PE_TTM、PB、PS_TTM、PCF、股息率 | Baostock / stock_fundamentals | 季报 / 每日估值 |
| 成长 | 营收增速、净利润增速、ROE 增速 | Baostock 财务三表 | 季报 |
| 质量 | ROE、ROA、毛利率、资产负债率、现金流质量 | Baostock 财务三表 | 季报 |
| 动量 | 1M / 3M / 6M / 12M 收益率 | daily_kline | 每交易日 |
| 技术 | RSI6、BIAS、MACD、BOLL | MyTT + daily_kline | 每交易日 |
| 波动 | 20 日波动率、最大回撤、ATR | daily_kline / MyTT | 每交易日 |

### 8.2 因子表达式

参考 Qlib 表达式范式，但首版不依赖完整 Qlib 运行时。

> **首版简化实现**：当前版本使用 Python 直接计算因子值（如 `pe_ttm`, `pb`, `roe`, `revenue_growth`, `mom_20d`, `mom_60d`, `rsi6`, `vol_20d`），而非 Qlib-like 表达式引擎。完整的表达式解析与动态计算能力计划在 M7+ 实现。

```yaml
factors:
  PE_INV:
    category: valuation
    expression: "-PE_TTM"
    direction: 1
    weight: 1.0
  ROE:
    category: quality
    expression: "fundamental.roe"
    direction: 1
    weight: 1.0
  MOM_3M:
    category: momentum
    expression: "Ref($close, 0) / Ref($close, 63) - 1"
    direction: 1
    weight: 0.8
  RSI6:
    category: technical
    expression: "MyTT.RSI($close, 6)"
    direction: -1
    weight: 0.3
```

实现上分两层：

- PostgreSQL 层：收益率、均值、排名、分组等简单窗口函数。
- Python Worker 层：MyTT 指标、复杂表达式、缺失值处理、IC/IR。

### 8.3 计算流程

```mermaid
flowchart TD
    Def["factor_definitions"] --> Load["加载 K线 + 基本面"]
    Load --> Calc["计算原始因子值"]
    Calc --> Clean["缺失值处理 + 去极值 Winsorize"]
    Clean --> Norm["横截面 Z-Score / 百分位排名"]
    Norm --> Save["写 factor_values"]
    Save --> Score["权重合成 total_score"]
    Score --> Rank["写 scoring_rank"]
    Save --> Analyze["计算 IC / IR"]
    Analyze --> FA["写 factor_analysis"]
```

### 8.4 标准化与打分

```python
def normalize_factor(series, direction: int):
    s = series.copy()
    lower, upper = s.quantile(0.01), s.quantile(0.99)
    s = s.clip(lower, upper)
    z = (s - s.mean()) / (s.std() + 1e-8)
    return z * direction

def score_cross_section(factor_df, weights):
    score = 0
    breakdown = {}
    for factor_name, weight in weights.items():
        normalized = normalize_factor(
            factor_df[factor_name],
            direction=get_factor_direction(factor_name),
        )
        contribution = normalized * weight
        breakdown[factor_name] = contribution
        score += contribution
    return score.rank(pct=True), breakdown
```

### 8.5 IC / IR 分析

- IC：每个交易日横截面上，因子值与未来 N 日收益的相关系数。
- IR：IC 均值 / IC 标准差，用于评估稳定性。
- ICIR：与 IR 同义或按团队口径单独保留。

分析结果进入 `factor_analysis`。当前 M5 MVP 与 M7+ 完整因子研究能力边界如下：

| 能力 | M5 MVP 当前实现 | M7+ 完整能力 |
| --- | --- | --- |
| 因子来源 | 8 个内置因子，Python Worker 直接计算 | Qlib-like 表达式解析、用户自定义动态因子 |
| 因子值 | 写入 `factor_values`，支持单日横截面查询 | 支持更完整的时序查询、导出、版本化因子定义 |
| 打分排行 | `scoring_rank` 按 DB 权重合成 Top N | 支持股票池、行业中性、更多作用域和组合导出 |
| IC/IR 指标 | `ic`、`ic_mean`、`ic_std`、`ir/icir`、`ic_gt_0_pct` | 多周期对比、Rank IC、滚动窗口稳定性分析 |
| IC 序列 | 存入 `factor_analysis.details.ic_by_date`，前端用迷你折线展示 | 独立 `GET /api/factors/analysis/{factor_name}/series` 和完整交互图 |
| 分组收益 | `group_returns` 五分组均值，前端条形展示 | 多空组合收益、分层累计收益、换手与容量分析 |
| 图表展示 | `/factor` 页面展示定义、排行、单因子值、IC/IR 卡片、迷你 IC 曲线、分组收益 | IC 分布直方图、多空组合收益曲线、因子衰减曲线、分层收益曲线 |

M5 MVP 不隐式支持未解析的 `factor_definitions.expression` 动态执行。表达式字段用于记录因子定义和后续 M7+ 扩展，当前计算逻辑只处理内置因子集合。

M7+ 因子研究增强计划：

- 因子分析序列 API：`GET /api/factors/analysis/{factor_name}/series`。
- IC 分布直方图、Rank IC、多周期 IC/IR 对比。
- 分层累计收益、多空组合收益、因子衰减曲线。
- 动态表达式引擎、表达式校验、因子运行审计。

---

## 9. 后端 API 与任务规划

### 9.1 REST API

| 模块 | 方法与路径 | 说明 |
| --- | --- | --- |
| 认证 | `POST /api/auth/login` | 登录获取 token（M7+ 延后） |
| 股票 | `GET /api/stocks` | 全市场列表，支持 ST / 行业 / 市值筛选 |
| 股票 | `GET /api/stocks/{ts_code}/klines` | 日 / 周 / 月 K 线 |
| 自选股 | `GET /api/watchlist` | 自选股分组查询 |
| 自选股 | `POST /api/watchlist` | 添加自选 |
| 策略 | `GET /api/strategies` | 策略列表 |
| 策略 | `POST /api/strategies` | 创建策略 |
| 回测 | `POST /api/backtests` | 提交异步回测（目标筛选通过 params_snapshot.target 配置） |
| 回测 | `GET /api/backtests/{id}` | 查询回测状态和结果 |
| 信号 | `GET /api/signals` | 五档信号日志 |
| 模拟交易 | `GET /api/sim/accounts` | 模拟账户列表 |
| 模拟交易 | `POST /api/sim/accounts` | 创建模拟账户 |
| 模拟交易 | `GET /api/sim/accounts/{id}/positions` | 持仓 |
| 模拟交易 | `GET /api/sim/accounts/{id}/orders` | 委托 |
| 模拟交易 | `GET /api/sim/accounts/{id}/trades` | 成交 |
| 模拟交易 | `GET /api/sim/accounts/{id}/nav` | 净值曲线 |
| 因子 | `GET /api/factors` | 因子定义 |
| 因子 | `GET /api/factors/rank` | 打分排行榜（支持 scope_type=all / watchlist_group） |
| 因子 | `GET /api/factors/values` | 单因子横截面值 |
| 因子 | `GET /api/factors/analysis` | 因子 IC/IR 分析结果 |
| 任务 | `GET /api/tasks/recent` | 最近任务运行状态 |
| 任务 | `GET /api/tasks/{task_id}` | 单个 Celery 任务状态 |
| 实时行情 | `GET /api/realtime/snapshot` | 东方财富 HTTP 快照，参数 `ts_codes` |
| 实时风控 | `GET /api/realtime/risk-guard/status` | 实时止盈/止损守护状态 |
| 数据源 | `GET /api/data/sources` | 数据源配置 |
| 数据源 | `PUT /api/data/sources` | 更新数据源启用状态 |
| 数据源 | `POST /api/data/sources/check` | 检查全部数据源 |
| 数据 | `GET /api/data/status` | 数据更新状态 |
| 数据 | `POST /api/data/sync/stock-basic` | 手动同步股票基础信息 |
| 数据 | `POST /api/data/sync/trade-calendar` | 手动同步交易日历 |
| 数据 | `POST /api/data/sync/kline` | 手动同步样例 K 线 |
| 数据任务 | `POST /api/tasks/data/sync-all-kline` | 触发全量 K 线同步 |
| 数据任务 | `POST /api/tasks/data/incremental-kline` | 触发增量 K 线同步 |
| 数据任务 | `POST /api/tasks/data/fundamentals` | 触发基本面同步 |
| 任务 | `POST /api/tasks/factors/compute` | 触发因子计算 |
| 任务 | `POST /api/tasks/factors/analyze` | 触发 IC/IR 分析 |
| 偏好 | `GET /api/preferences/trading-fee` | 交易费率偏好 |
| 偏好 | `GET /api/preferences/kline-sync` | K 线同步偏好 |
| 系统 | `GET /api/system/alerts` | 告警列表，支持 level / category / is_resolved / limit / offset |

`POST /api/auth/login`、`GET /api/system/tasks`、`POST /api/data/sync` 不是当前实现路径，保留为 M7+ 兼容入口候选。

### 9.2 WebSocket

| 路径 | 用途 | 消息 | 状态 |
| --- | --- | --- | --- |
| `/ws/realtime` | 实时行情订阅 | subscribe / unsubscribe / tick | ✅ M6a 已实现 |
| `/ws/tasks` | 回测、数据任务状态通知 | task_status | M6b 待实现 |
| `/ws/signals` | 策略信号推送 | signal_created | M6b 待实现 |

订阅示例：

```json
{
  "action": "subscribe",
  "ts_codes": ["600000.SH", "000001.SZ"]
}
```

### 9.3 Celery 队列

| 队列 | 任务 |
| --- | --- |
| data | 股票列表更新、K线增量、基本面、交易日历 |
| backtest | Python-native 回测、参数敏感性分析 |
| factor | 因子计算、IC/IR、打分排名 |
| trading | 信号执行、模拟撮合、T+1 解锁、净值快照 |
| default | 轻量后台任务 |

当前 Celery 应用入口为 `app.tasks.celery_app:celery_app`。`app.tasks.data_tasks.*` 路由到 `data`，`app.tasks.factor_tasks.*` 路由到 `factor`，`app.tasks.trading_tasks.*` 和 `app.tasks.signal_tasks.*` 路由到 `trading`，`app.tasks.run_backtest` 路由到 `backtest`。

---

## 10. 前端页面与组件规划

前端面向研究和交易管理场景，应保持信息密度适中、操作路径短、图表和表格优先，不做营销式落地页。

### 10.1 页面路由

| 路由 | 页面 | 核心能力 |
| --- | --- | --- |
| `/` | Dashboard | 大盘概览、自选股涨跌、今日信号、模拟账户净值 |
| `/market` | 市场 | 全市场列表、ST / 退市标识、筛选、股票详情 |
| `/watchlist` | 自选股 | 分组管理、实时行情表、批量移动 |
| `/strategy` | 策略中心 | 策略列表、Monaco 编辑器、参数面板、回测入口 |
| `/backtests` | 回测列表 | 回测历史、状态、绩效概览 |
| `/backtests/:id` | 回测详情 | 绩效指标、净值曲线、回撤、交易记录、信号叠加 |
| `/backtests/compare` | 回测对比 | 多回测结果对比 |
| `/signals` | 信号中心 | 五档信号日志、筛选、统计、快照详情 |
| `/simulation` | 模拟交易 | 账户、持仓、委托、成交、流水、净值 |
| `/factor` | 因子选股 | 因子库、IC/IR 图表、排行榜、权重配置 |
| `/status` | 任务监控 | Celery 任务状态、失败原因、数据更新状态 |
| `/preferences` | 偏好设置 | 用户偏好、数据源配置、费率配置 |
| `/sources` | 数据源 | 数据源配置（同 PreferencesPage） |

### 10.2 核心组件

| 组件 | 技术 | 说明 |
| --- | --- | --- |
| `KLineChart` | TradingView Lightweight Charts | K 线、成交量、信号箭头、复权切换 |
| `StrategyEditor` | Monaco Editor | Python 语法、MyTT 补全、策略模板 |
| `StockTable` | shadcn/ui Table + 虚拟滚动 | 全市场股票、因子排行、排序筛选 |
| `WatchlistBoard` | WebSocket Hook | 实时价格、涨跌幅、分组 |
| `BacktestReport` | Charts + Table | 绩效卡片、净值、回撤、交易明细 |
| `SimAccountPanel` | shadcn Tabs | 持仓 / 委托 / 成交 / 流水 / 净值 |
| `FactorRankTable` | Table + 权重控件 | 多因子分解、排行、导出到自选股 |
| `TaskMonitor` | Status Badge + Log Drawer | Celery 任务状态、失败原因 |

### 10.3 Monaco Editor MyTT 提示

```typescript
const MYTT_COMPLETIONS = [
  { label: "MA", detail: "MA(CLOSE, N) - 简单移动平均", insertText: "MA(CLOSE, ${1:N})" },
  { label: "EMA", detail: "EMA(CLOSE, N) - 指数移动平均", insertText: "EMA(CLOSE, ${1:N})" },
  { label: "MACD", detail: "MACD(CLOSE, SHORT, LONG, M)", insertText: "MACD(CLOSE, ${1:12}, ${2:26}, ${3:9})" },
  { label: "RSI", detail: "RSI(CLOSE, N) - 相对强弱指标", insertText: "RSI(CLOSE, ${1:6})" },
  { label: "BOLL", detail: "BOLL(CLOSE, N, P) - 布林带", insertText: "BOLL(CLOSE, ${1:20}, ${2:2})" },
  { label: "KDJ", detail: "KDJ(CLOSE, HIGH, LOW)", insertText: "KDJ(CLOSE, HIGH, LOW)" },
  { label: "Signal", detail: "返回五档信号", insertText: "return {\"signal_type\": \"买入\", \"target_position\": 1.0, \"reason\": \"\"}" }
];
```

### 10.4 A 股视觉约定

- 红涨绿跌，所有图表、涨跌标签、K 线保持一致。
- ST / 退市股票在表格中使用明确状态标签。
- 信号图标：买入 / 增持在 K 线下方，减仓 / 卖出在 K 线上方，观望不画箭头。
- 回测和模拟交易必须提供“参数快照”和“数据源”说明，避免复盘时口径不清。

---

## 11. Docker Compose 部署配置

### 11.1 `docker-compose.yml`

```yaml
services:
  postgres:
    image: ${IMAGE_PREFIX:-}postgres:15-alpine
    container_name: leek-quant-postgres
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-leek_quant}
      POSTGRES_USER: ${POSTGRES_USER:-leek_user}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-change-me}
      TZ: ${TZ:-Asia/Shanghai}
    ports:
      - "${POSTGRES_PORT:-127.0.0.1:5432}:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    image: ${IMAGE_PREFIX:-}redis:7-alpine
    container_name: leek-quant-redis
    command: ["redis-server", "--maxmemory", "128mb", "--maxmemory-policy", "allkeys-lru"]
    ports:
      - "${REDIS_PORT:-127.0.0.1:6379}:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
      args:
        BASE_IMAGE: ${IMAGE_PREFIX:-}python:3.12-slim-bookworm
        BUILD_REGION: ${BUILD_REGION:-cn}
    container_name: leek-quant-backend
    environment:
      ENVIRONMENT: ${ENVIRONMENT:-local}
      REDIS_URL: ${REDIS_URL:-redis://redis:6379/0}
      DATABASE_URL: ${CONTAINER_DATABASE_URL:-postgresql+asyncpg://leek_user:change-me@postgres:5432/leek_quant}
      BACKEND_CORS_ORIGINS: ${BACKEND_CORS_ORIGINS:-http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080}
      TZ: ${TZ:-Asia/Shanghai}
    ports:
      - "${BACKEND_PORT:-127.0.0.1:8000}:8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped

  celery_worker:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: leek-quant-celery-worker
    command: ["celery", "-A", "app.tasks.celery_app:celery_app", "worker", "--loglevel=INFO", "-Q", "default,data,backtest,factor,trading"]
    environment:
      ENVIRONMENT: ${ENVIRONMENT:-local}
      REDIS_URL: ${REDIS_URL:-redis://redis:6379/0}
      DATABASE_URL: ${CONTAINER_DATABASE_URL:-postgresql+asyncpg://leek_user:change-me@postgres:5432/leek_quant}
      BACKEND_CORS_ORIGINS: ${BACKEND_CORS_ORIGINS:-http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080}
      TZ: ${TZ:-Asia/Shanghai}
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
    container_name: leek-quant-celery-beat
    command: ["celery", "-A", "app.tasks.celery_app:celery_app", "beat", "--loglevel=INFO"]
    environment:
      ENVIRONMENT: ${ENVIRONMENT:-local}
      REDIS_URL: ${REDIS_URL:-redis://redis:6379/0}
      DATABASE_URL: ${CONTAINER_DATABASE_URL:-postgresql+asyncpg://leek_user:change-me@postgres:5432/leek_quant}
      BACKEND_CORS_ORIGINS: ${BACKEND_CORS_ORIGINS:-http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080}
      TZ: ${TZ:-Asia/Shanghai}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  realtime_risk_guard:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: leek-quant-realtime-risk-guard
    environment:
      ENVIRONMENT: ${ENVIRONMENT:-local}
      REDIS_URL: ${REDIS_URL:-redis://redis:6379/0}
      DATABASE_URL: ${CONTAINER_DATABASE_URL:-postgresql+asyncpg://leek_user:change-me@postgres:5432/leek_quant}
      TZ: ${TZ:-Asia/Shanghai}
    command: ["python", "-m", "app.realtime.risk_guard", "--mode", "${REALTIME_RISK_GUARD_MODE:-snapshot}", "--refresh-interval", "${REALTIME_RISK_GUARD_INTERVAL:-15}"]
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
      args:
        NODE_IMAGE: ${NODE_IMAGE:-node:20-alpine}
        RUNTIME_IMAGE: ${IMAGE_PREFIX:-}nginx:1.25-alpine
        VITE_API_BASE_URL: ${VITE_API_BASE_URL:-http://localhost:8000}
    container_name: leek-quant-frontend
    ports:
      - "${FRONTEND_PORT:-8080}:80"
    depends_on:
      backend:
        condition: service_healthy
    restart: unless-stopped

volumes:
  postgres-data:
```

当前根目录 `docker-compose.yml` 是本地开发配置：PostgreSQL、Redis、Backend 端口默认绑定 `127.0.0.1`，Redis 默认无密码，前端默认暴露 `8080`，并包含独立 `realtime_risk_guard` 服务。生产部署必须覆盖本地默认值：设置强 `POSTGRES_PASSWORD` 和受控 `BACKEND_CORS_ORIGINS`，Redis 应启用密码或放入私有网络且不公开端口，外部访问应经反向代理和 TLS，数据库与 Redis 不应绑定公网地址。生产 Compose 建议见 `docs/deployment-production.md`。

### 11.1.1 生产安全边界

本地 Compose 的 Redis 无密码是开发便利，不是生产安全配置。生产部署必须至少满足：

1. Redis 不暴露公网；优先删除 Redis `ports`，仅允许 backend、Celery 和 `realtime_risk_guard` 通过 Docker 网络访问。
2. 若 Redis 需要跨网络访问，必须使用 `redis-server --requirepass ${REDIS_PASSWORD}`，并将 backend、`celery_worker`、`celery_beat`、`realtime_risk_guard` 的 `REDIS_URL` 改为 `redis://:${REDIS_PASSWORD}@redis:6379/0`。
3. PostgreSQL 不暴露公网；`POSTGRES_PASSWORD` 必须从生产环境注入，不能使用 `change-me`。
4. Backend 不直接公网暴露；使用反向代理承载 HTTPS/TLS，backend 端口只绑定 `127.0.0.1` 或仅在 Docker 网络内监听。
5. `BACKEND_CORS_ORIGINS` 必须限定为真实生产前端域名，不使用通配符。
6. 使用 `docker-compose.prod.yml` 覆盖本地默认值，并在部署前执行 `docker compose -f docker-compose.yml -f docker-compose.prod.yml config`。

### 11.2 后端 Dockerfile

```dockerfile
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 11.3 关键 Python 依赖

```txt
fastapi[standard]>=0.111.0
uvicorn[standard]>=0.29.0
pydantic>=2.0.0
pydantic-settings>=2.0.0
sqlalchemy[asyncio]>=2.0.0
asyncpg>=0.29.0
alembic>=1.13.0
celery[redis]>=5.3.0
redis>=5.0.0

adata>=3.0.0
baostock>=0.8.8
akshare>=1.14.0
numpy>=1.26.0
pandas>=2.1.0
scipy>=1.13.0
websockets>=12.0
httpx>=0.27.0
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
```

MyTT 可直接将 `MyTT.py` 复制进 `backend/app/libs/`，策略执行白名单导入该模块，前端 Monaco 使用同一份函数元数据生成补全。

### 11.4 Celery Beat 定时任务

```python
from celery.schedules import crontab

beat_schedule = {
    "update-stock-basic-weekly": {
        "task": "app.tasks.data_tasks.update_stock_basic",
        "schedule": crontab(day_of_week="saturday", hour=3, minute=0),
    },
    "update-trade-calendar-weekly": {
        "task": "app.tasks.data_tasks.update_trade_calendar",
        "schedule": crontab(day_of_week="sunday", hour=2, minute=0),
    },
    "incremental-kline-daily": {
        "task": "app.tasks.data_tasks.incremental_kline_update",
        "schedule": crontab(hour=17, minute=0),
    },
    "generate-signals-daily": {
        "task": "app.tasks.signal_tasks.generate_all_signals",
        "schedule": crontab(hour=12, minute=0),
    },
    "compute-factors-daily": {
        "task": "app.tasks.factor_tasks.compute_daily_factors",
        "schedule": crontab(hour=17, minute=30),
    },
    "update-fundamentals-daily": {
        "task": "app.tasks.data_tasks.sync_fundamentals",
        "schedule": crontab(hour=19, minute=30),
    },
    "unlock-t1-positions-daily": {
        "task": "app.tasks.trading_tasks.unlock_t1_daily",
        "schedule": crontab(hour=9, minute=25),
    },
    "match-pending-orders-daily": {
        "task": "app.tasks.trading_tasks.match_pending_orders",
        "schedule": crontab(hour=17, minute=5),
    },
    "snapshot-sim-nav-daily": {
        "task": "app.tasks.trading_tasks.snapshot_nav_daily",
        "schedule": crontab(hour=15, minute=30),
    },
}
```

`app.tasks.signal_tasks.generate_intraday_position_signals` 已注册到 Celery，并路由到 `trading` 队列。首版不放入默认 `beat_schedule`，避免自动高频触发行情拉取和盘中撮合；需要时由运维手动触发或在启用去重、窗口检查和任务审计后加入低频盘中调度。

任务内部必须先查 `trade_calendar`，非交易日直接跳过，不能仅靠 crontab 判断。

---

## 12. 开发里程碑

| 阶段 | 目标 | 交付物 | 验证 |
| --- | --- | --- | --- |
| M0 基础环境 | Docker Compose、PostgreSQL、Redis、FastAPI、React 空壳 | 可一键启动，健康检查通过 | `docker compose up`，API `/health` |
| M1 数据基座 | 股票列表、交易日历、日线 K 线全量和增量 | `stock_basic`、`daily_kline`、`trade_calendar` 有数据 | 随机股票 K 线与源数据比对 |
| M2 自选股与策略 | 自选分组、策略 CRUD、基础前端页面 | 自选分组、自选股 API、市场页、策略管理 | 自选股分组管理 |
| M3 策略与回测 | Monaco 编辑、MyTT 提示、Python-native 异步回测 | 策略 CRUD、回测任务、结果页 | 双均线策略跑通 |
| M4 信号与模拟交易 | 五档信号、模拟交易 6 表、T+1 / 涨跌停 / 费用 | 信号中心、模拟账户闭环 | 资金守恒和 T+1 单测 |
| M5 多因子 | 内置因子定义、计算、IC/IR MVP、排行榜 | 因子页、排行榜、IC/IR 卡片、迷你 IC 曲线、分组收益 | IC 计算样例验证 |
| M6a HTTP 快照实时 | 东方财富 HTTP 快照、Redis 广播、WebSocket 订阅、前端实时看板 | 自选股实时刷新 | ✅ 已通过验收 |
| M6b WebSocket 流式 | 东方财富 WebSocket 流式推送、任务/信号 WebSocket 通道、断线重连 | 断线重连测试 | 待实现 |
| M7 优化完善 | 参数敏感性、多账户优化、数据监控告警、认证系统、股票池、完整因子研究、因子表达式引擎、周/月线物化视图、文档 | 系统设置页、任务监控、README、因子研究图表 | 全链路 smoke test |

---

## 13. 测试与验收策略

### 13.1 单元测试

- 数据标准化：AData、Baostock、AkShare 的字段映射。
- 交易日历：前后交易日、非交易日跳过。
- A 股费用：买入、卖出、最低佣金、印花税、过户费。
- T+1：当日买入不可卖，下一交易日解锁。
- 状态机：五档信号在空仓、半仓、满仓下的动作。
- 因子标准化：去极值、方向调整、缺失值处理。

### 13.2 集成测试

- 全量拉取单只股票历史 K 线并回测。
- 增量拉取幂等执行两次，数据无重复。
- 策略提交回测，Celery 完成后前端可读取结果。
- 信号生成后模拟交易创建委托并撮合成交。
- 日终净值生成后账户资产守恒。

### 13.3 验收标准

- Docker Compose 一键启动所有服务。
- 至少 20 只 A 股完成历史 K 线拉取与增量更新。
- 策略编辑器能运行一个 MyTT 双均线策略。
- Python-native 回测结果写入 `backtest_results`，`performance.engine = "python_native"`。
- 模拟交易能完成买入、T+1 解锁、卖出、费用记录、净值快照。
- 因子排行榜能按配置输出 Top N 股票。
- WebSocket 能对自选股推送实时 tick。

---

## 14. 风险与应对措施

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 免费数据源不稳定 | K 线或基本面中断 | AData -> Baostock -> AkShare 自动回退；失败告警；本地缓存 |
| 多源复权口径差异 | 回测收益失真 | 记录 `data_source`；以 AData 为基准；源切换后复权校准 |
| 东方财富协议变化 | 实时行情中断 | 自建解析模块隔离；心跳重连；AllTick 可配置备用 |
| 用户策略安全 | 任意代码执行风险 | 子进程隔离、超时、白名单模块、禁网络和写文件 |
| 大规模回测超时 | 用户体验差 | Celery 队列、并发限制、任务进度、分批执行 |
| PostgreSQL 数据膨胀 | 磁盘占用增长 | 年分区、索引控制、可配置数据保留、磁盘告警 |
| A 股规则遗漏 | 回测 / 模拟不真实 | T+1、涨跌停、ST、费用、停牌边界测试 |
| 因子未来函数 | 因子结果虚高 | 因子表达式只允许历史窗口；IC 计算严格用未来收益标签 |
| 合规风险 | 数据源条款或投资建议风险 | 限频、仅本地研究使用、明确免责声明、不接实盘交易 |

---

## 15. 开源项目集成总结

| 项目 | 用途 | 集成方式 |
| --- | --- | --- |
| MyTT | 通达信 / 同花顺兼容指标 | 后端策略运行白名单导入；前端 Monaco 补全 |
| AData | A 股历史 K 线、股票列表 | 数据层 Tier1 主源 |
| Baostock | K 线备源、财务三表、估值 | 数据层 Tier2，基本面主源 |
| AkShare | 分钟 K 线、交易日历、全市场补充 | 数据层 Tier3 兜底 |
| Qlib | 因子表达式和因子评估范式 | 轻量复刻表达式与 IC/IR 流程，不引入完整平台 |
| 东方财富 WebSocket | 免费实时行情 | 首版使用 HTTP 快照轮询（M6a），WebSocket 流式推送为 M6b |
| AllTick | 实时行情备用 | 配置 token 后作为东方财富失败时的替代 |
| QuantDinger | 项目母版 | 裁剪非 A 股模块，保留 FastAPI / React / Celery / Compose 骨架 |
| TradingView Lightweight Charts | K 线、净值曲线、信号叠加 | 前端图表组件 |
| Monaco Editor | Python 策略编辑 | 前端策略中心 |
| shadcn/ui | UI 基础组件 | 表格、弹窗、Tabs、Toast、表单 |

---

## 16. 免责声明

Leek Quant 仅用于量化研究、策略学习、历史回测和模拟交易，不提供任何投资建议，不直接支持实盘自动交易。用户应自行确认数据源使用条款、交易规则变化和策略风险。
