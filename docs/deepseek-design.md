# Leek Quant 开发架构文档（DeepSeek 版）

> **版本**：2.0  
> **设计原则**：本地优先、隐私至上、专注A股、深度复用成熟开源组件  
> **基础项目**：QuantDinger（裁剪其非A股部分，保留前端/后端/部署骨架）

---

## 1. 项目概述

Leek Quant 是一个面向个人投资者的**纯A股量化交易平台**，提供从数据获取、策略开发、高性能回测、多因子选股到模拟交易的全流程工具。系统运行在用户本地，所有敏感数据与策略代码完全由用户掌控。

**核心差异化**：
- 纯A股支持，内建 T+1、涨跌停、真实费用模型
- 五档操作信号（买入/增持/减仓/卖出/观望）
- 多因子股票打分与排名，打通“选股→策略执行”闭环
- 完整模拟交易体系（委托、成交、持仓、资金流水、净值快照）
- 深度集成 Hikyuu、MyTT、AData 等开源项目，避免重复造轮子

---

## 2. 技术栈总览

| 层级 | 技术选型 | 说明 |
|:---|:---|:---|
| **前端** | React 18 + Vite + Tailwind CSS + shadcn/ui | 现代响应式UI，组件化开发 |
| **图表** | TradingView Lightweight Charts | 专业K线图，支持信号标注 |
| **编辑器** | Monaco Editor | 与VSCode同源，代码高亮/补全 |
| **后端** | FastAPI (Python 3.11+) | 高性能异步API，自动生成文档 |
| **ORM** | SQLAlchemy 2.0 + Alembic | 异步支持，迁移管理 |
| **数据库** | **PostgreSQL 15+** | 统一存储，分区表、JSONB、分析函数 |
| **任务队列** | Celery + Redis | 数据拉取、回测、因子计算等异步任务 |
| **回测引擎** | **Hikyuu** | C++内核，原生支持A股规则 |
| **技术指标** | **MyTT** | 通达信/同花顺兼容，零依赖 |
| **数据源** | AData(主) + Baostock(备) + AkShare(兜底) | 三层回退，全免费 |
| **实时行情** | 东方财富 WebSocket（自建解析） | 低延迟推送，Redis广播 |
| **因子框架** | 参考 **Qlib** 因子表达式 | 轻量复刻，存入PostgreSQL |
| **部署** | Docker Compose | 一键启动全部服务 |

---

## 3. 系统架构图

```
┌──────────────────────────────────────────────────────────────┐
│                       Frontend (React)                       │
│  ┌──────┐ ┌──────┐ ┌─────────────────┐ ┌──────────────────┐ │
│  │自选股│ │股票池│ │策略编辑器(Monaco│ │回测报告/打分榜   │ │
│  │看板  │ │管理  │ │ + MyTT 提示)   │ │(TradingView图表) │ │
│  └──────┘ └──────┘ └─────────────────┘ └──────────────────┘ │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTP REST / WebSocket
┌──────────────────────────┴───────────────────────────────────┐
│                    Backend API (FastAPI)                     │
│  ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌───────────────┐ │
│  │用户/认证 │ │数据查询API│ │策略CRUD  │ │回测/信号/模拟 │ │
│  └──────────┘ └───────────┘ └──────────┘ └───────────────┘ │
└──────────┬───────────────────────────┬───────────────────────┘
           │                           │
           │ 异步任务                   │ 直接调用
           ▼                           ▼
┌──────────────────────┐   ┌──────────────────────────┐
│  Celery Workers       │   │  Hikyuu Engine (C++/Py)  │
│  - 数据增量拉取       │   │  - 策略回测执行          │
│  - 定时信号生成       │   │  - 绩效报告生成          │
│  - 因子计算/打分      │   │  - A股规则内置           │
│  - 模拟交易撮合       │   │                          │
└──────────┬───────────┘   └──────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────┐
│              PostgreSQL (主数据库)                       │
│  - 股票基础/K线(分区)/基本面     - 回测结果/信号日志    │
│  - 自选股/股票池/策略源码        - 因子值/打分排名      │
│  - 模拟交易6表 (账户/持仓/委托/成交/流水/净值)         │
└──────────────────────────────────────────────────────────┘
```

---

## 4. 数据库设计（PostgreSQL）

### 4.1 数据库选型理由
- **统一存储**：替代 QuantDinger 的 DuckDB + Parquet + SQLite 组合，简化运维
- **分区表**：K线数据按年分区，加速时间范围查询
- **JSONB**：灵活存储策略配置、回测曲线、财务原始数据
- **分析函数**：窗口函数、聚合直接计算因子IC、排名，减少数据搬运
- **ACID**：保证金融数据一致性

### 4.2 核心表定义

#### 4.2.1 市场与基础数据
```sql
CREATE TABLE stock_basic (
    ts_code     VARCHAR(10) PRIMARY KEY,   -- 例如 '600000.SH'
    symbol      VARCHAR(6) NOT NULL,
    name        VARCHAR(20) NOT NULL,
    industry    VARCHAR(50),
    area        VARCHAR(20),
    list_date   DATE,
    is_st       BOOLEAN DEFAULT FALSE,
    is_delisted BOOLEAN DEFAULT FALSE
);

CREATE TABLE daily_kline (
    ts_code     VARCHAR(10) NOT NULL,
    trade_date  DATE NOT NULL,
    open        NUMERIC(12,3),
    high        NUMERIC(12,3),
    low         NUMERIC(12,3),
    close       NUMERIC(12,3),
    volume      BIGINT,
    amount      NUMERIC(20,2),
    adj_factor  NUMERIC(12,6),            -- 复权因子
    data_source VARCHAR(20) DEFAULT 'adata',
    PRIMARY KEY (ts_code, trade_date)
) PARTITION BY RANGE (trade_date);

CREATE TABLE trade_calendar (
    cal_date    DATE PRIMARY KEY,
    is_trade_day BOOLEAN NOT NULL
);

CREATE TABLE stock_fundamentals (
    ts_code        VARCHAR(10) NOT NULL,
    report_date    DATE NOT NULL,         -- 报告期
    pe_ttm         NUMERIC(10,2),
    pb             NUMERIC(10,2),
    ps_ttm         NUMERIC(10,2),
    market_cap     NUMERIC(16,2),
    dividend_yield NUMERIC(10,4),
    revenue_growth NUMERIC(10,4),
    debt_to_equity NUMERIC(10,4),
    current_ratio  NUMERIC(10,4),
    free_cash_flow NUMERIC(16,2),
    income_statement  JSONB,
    balance_sheet     JSONB,
    cashflow_statement JSONB,
    PRIMARY KEY (ts_code, report_date)
);
```

#### 4.2.2 用户与策略
```sql
CREATE TABLE users (
    id       SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(200) NOT NULL
);

CREATE TABLE watchlist (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER REFERENCES users(id),
    ts_code    VARCHAR(10) REFERENCES stock_basic(ts_code),
    group_name VARCHAR(50) DEFAULT '默认',
    added_at   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE stock_pools (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id),
    pool_name   VARCHAR(100) NOT NULL,
    filters     JSONB,                  -- 筛选条件定义
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE stock_pool_items (
    pool_id  INTEGER REFERENCES stock_pools(id) ON DELETE CASCADE,
    ts_code  VARCHAR(10) REFERENCES stock_basic(ts_code),
    PRIMARY KEY (pool_id, ts_code)
);

CREATE TABLE strategies (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id),
    name        VARCHAR(100) NOT NULL,
    code        TEXT NOT NULL,          -- Python源码
    config      JSONB,                 -- 参数配置
    status      VARCHAR(20) DEFAULT 'draft',
    created_at  TIMESTAMP DEFAULT NOW()
);
```

#### 4.2.3 回测与信号
```sql
CREATE TABLE backtest_results (
    id              SERIAL PRIMARY KEY,
    strategy_id     INTEGER REFERENCES strategies(id),
    pool_id         INTEGER REFERENCES stock_pools(id),
    start_date      DATE,
    end_date        DATE,
    initial_capital NUMERIC(20,2),
    total_return    NUMERIC(12,4),
    sharpe_ratio    NUMERIC(8,4),
    max_drawdown    NUMERIC(8,4),
    annual_vol      NUMERIC(8,4),
    win_rate        NUMERIC(6,4),
    trade_records   JSONB,
    equity_curve    JSONB,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE signal_log (
    id             SERIAL PRIMARY KEY,
    strategy_id    INTEGER REFERENCES strategies(id),
    ts_code        VARCHAR(10),
    trade_date     DATE NOT NULL,
    signal         VARCHAR(10) CHECK (signal IN ('买入','增持','减仓','卖出','观望')),
    target_ratio   NUMERIC(5,4),        -- 目标仓位比例
    reason         TEXT,
    market_snapshot JSONB,
    created_at     TIMESTAMP DEFAULT NOW()
);
```

#### 4.2.4 因子与打分
```sql
CREATE TABLE factor_values (
    ts_code     VARCHAR(10),
    trade_date  DATE,
    factor_name VARCHAR(50),
    value       NUMERIC(18,6),
    PRIMARY KEY (ts_code, trade_date, factor_name)
);

CREATE TABLE scoring_rank (
    trade_date DATE,
    ts_code    VARCHAR(10),
    score      NUMERIC(12,4),
    rank       INTEGER,
    PRIMARY KEY (trade_date, ts_code)
);
```

#### 4.2.5 模拟交易全套表
```sql
CREATE TABLE sim_accounts (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id),
    strategy_id     INTEGER REFERENCES strategies(id),
    name            VARCHAR(100),
    initial_capital NUMERIC(20,2) NOT NULL,
    cash            NUMERIC(20,2) NOT NULL,
    frozen_cash     NUMERIC(20,2) DEFAULT 0,
    total_asset     NUMERIC(20,2),
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE sim_positions (
    id              SERIAL PRIMARY KEY,
    account_id      INTEGER REFERENCES sim_accounts(id) ON DELETE CASCADE,
    ts_code         VARCHAR(10) NOT NULL,
    shares          INTEGER NOT NULL,
    avg_cost        NUMERIC(12,3) NOT NULL,
    current_price   NUMERIC(12,3),
    market_value    NUMERIC(20,2),
    unrealized_pnl  NUMERIC(20,2),
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(account_id, ts_code)
);

CREATE TABLE sim_orders (
    id            SERIAL PRIMARY KEY,
    account_id    INTEGER REFERENCES sim_accounts(id),
    ts_code       VARCHAR(10) NOT NULL,
    direction     VARCHAR(4) CHECK (direction IN ('买入','卖出')),
    order_type    VARCHAR(10) DEFAULT '限价',
    price         NUMERIC(12,3),
    volume        INTEGER NOT NULL,
    filled_volume INTEGER DEFAULT 0,
    status        VARCHAR(10) DEFAULT '未报',
    submit_time   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE sim_trades (
    id           SERIAL PRIMARY KEY,
    order_id     INTEGER REFERENCES sim_orders(id),
    account_id   INTEGER REFERENCES sim_accounts(id),
    ts_code      VARCHAR(10) NOT NULL,
    direction    VARCHAR(4),
    price        NUMERIC(12,3) NOT NULL,
    volume       INTEGER NOT NULL,
    amount       NUMERIC(20,2),
    stamp_tax    NUMERIC(12,4),
    commission   NUMERIC(12,4),
    transfer_fee NUMERIC(12,4),
    trade_time   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE sim_cash_flow (
    id               SERIAL PRIMARY KEY,
    account_id       INTEGER REFERENCES sim_accounts(id),
    type             VARCHAR(20),
    amount           NUMERIC(20,2),
    balance          NUMERIC(20,2),
    related_trade_id INTEGER REFERENCES sim_trades(id),
    remark           TEXT,
    created_at       TIMESTAMP DEFAULT NOW()
);

CREATE TABLE sim_daily_nav (
    id             SERIAL PRIMARY KEY,
    account_id     INTEGER REFERENCES sim_accounts(id),
    nav_date       DATE NOT NULL,
    total_asset    NUMERIC(20,2),
    daily_return   NUMERIC(12,8),
    cumulative_nav NUMERIC(12,4),
    UNIQUE(account_id, nav_date)
);
```

---

## 5. 核心模块详细设计

### 5.1 数据源多层回退与增量更新

**历史K线与基本面数据获取**采用三层回退策略：

| 层级 | 数据源 | 覆盖内容 | 特点 |
|:---|:---|:---|:---|
| Tier 1 | AData | 日/周/月K线、股票列表 | 专注A股，免费，接口稳定 |
| Tier 2 | Baostock | 日K线、财务三表、估值指标 | 基本面数据齐全，免费 |
| Tier 3 | AkShare | 分钟K线、补充全市场数据 | 覆盖面广，兜底方案 |

回退逻辑伪代码：
```python
class CNStockDataSource:
    def get_kline(self, ts_code, start, end):
        for source in [AData, Baostock, AkShare]:
            data = source.fetch(ts_code, start, end)
            if data is not None:
                if source != AData:
                    log_warning(f"Fallback to {source}")
                return data
        raise DataUnavailableError
```

**增量更新机制**：  
- 记录每只股票在 `daily_kline` 中的最大 `trade_date`
- Celery Beat 每交易日下午 18:00 触发更新任务，仅拉取最大日期之后的数据
- 插入时使用 `INSERT ... ON CONFLICT DO NOTHING` 保证幂等

**实时行情**：  
- 自建东方财富 WebSocket 解析器，直接读取实时数据流
- 解析后的数据通过 Redis Pub/Sub 广播，FastAPI WebSocket 转发给前端
- 实时数据不落库，仅用于盘中展示和信号计算

**交易日历**：  
- 从 AkShare 获取 A 股交易日历，存入 `trade_calendar` 表
- 所有需要判断交易日的模块（增量更新、回测天数、信号生成）统一查询此表

### 5.2 回测引擎集成（Hikyuu）

Hikyuu 是一个使用 C++ 实现的高性能回测系统，天然支持A股规则。Leek Quant 将其作为独立模块嵌入，而不自己开发回测内核。

**集成方案**：
```python
import hikyuu as hk
from hikyuu import System, SignalBase

class HikyuuBacktestService:
    def __init__(self):
        self.sm = hk.StockManager.instance()
        self.sm.init()  # 指定数据加载方式，我们将从 PostgreSQL 加载

    def run(self, strategy_code: str, pool: list, start, end, capital):
        # 1. 将 PostgreSQL 数据转为 Hikyuu 需要的格式（内存或临时文件）
        # 2. 动态执行用户策略，提取 Signal, MoneyManager 等组件
        # 3. 组装 System，设置交易环境为 CN
        # 4. 运行回测，收集 TradeRecord 和 Performance
        # 5. 结果序列化为 JSON，存入 backtest_results
        pass
```

用户在前端 Monaco 编辑器中编写的策略代码必须遵循 Hikyuu 的组件约定（例如定义 `signal()` 函数返回一个 `SignalBase` 实例），同时允许自由使用 MyTT 函数计算指标。这样既保留了灵活性，又继承了 Hikyuu 的高性能与规则保障。

### 5.3 五档信号体系

标准信号定义（Python 模型）：
```python
class SignalType(str, Enum):
    BUY = "买入"
    ADD = "增持"
    REDUCE = "减仓"
    SELL = "卖出"
    WAIT = "观望"

class Signal(BaseModel):
    type: SignalType
    target_ratio: float = 0.0   # 目标仓位占比 (0~1)
    reason: str = ""
    price: float = 0.0
```

**状态机规则**：
- 当前持仓为0且信号为“增持” → 转为“买入”执行
- 当前持仓大于0且信号为“减仓”且目标仓位小于当前 → 卖出部分
- 信号为“观望” → 不调整持仓
- 信号为“卖出” → 清仓
- 涨跌停或 T+1 规则可能导致信号无法立即执行，产生委托排队

所有信号写入 `signal_log`，包含触发时的行情快照，供复盘分析。

### 5.4 模拟交易引擎

模拟交易通过 Celery Worker 独立运行，与信号生成联动。完整流程如下：

1. **信号触发**：策略定期生成信号，写入 `signal_log`。
2. **委托创建**：模拟引擎读取最新信号，根据当前持仓和资金生成 `sim_orders`。  
   - 例如，空仓收到“买入”信号，委托买入目标市值的股票。
3. **委托检查**：
   - 涨停板：买单无法成交（废单或挂单等待）
   - 跌停板：卖单无法成交
   - T+1 卖出检查：检查 `sim_positions` 中是否有当日买入的份额，禁止卖出
   - 资金/持仓充足性校验
4. **模拟成交**：对可成交委托生成 `sim_trades`，计算费用：
   - 印花税：卖出时 0.05%
   - 佣金：买卖双向，万2.5
   - 过户费：买卖双向，0.001%
   - 更新 `sim_positions`（新增/减持/清仓）
   - 更新 `sim_accounts`（现金变化、冻结解冻）
   - 写入 `sim_cash_flow` 记录费用
5. **日终处理**：
   - 用当日收盘价更新 `sim_positions.market_value` 和 `unrealized_pnl`
   - 计算 `total_asset`，写入 `sim_daily_nav`
6. **绩效展示**：前端读取 `sim_daily_nav` 绘制净值曲线，与基准对比。

### 5.5 多因子打分模块

**因子计算**：
- 预置因子：PE、PB、ROE、营收增长率、20日动量、波动率等
- 计算逻辑优先使用 PostgreSQL 聚合（如窗口函数），复杂计算下放到 Celery Worker
- 因子值定时（每周/每月）计算，存入 `factor_values`

**因子有效性分析**：
- 计算每个因子的 IC（信息系数）和 IR（信息比率）
- 对因子进行分层回测，生成分组收益曲线
- 结果存入 `factor_analysis` 表，前端提供可视化

**打分合成**：
- 用户可自定义因子权重（或等权、IC加权）
- 每期对全市场或指定股票池进行打分，结果写入 `scoring_rank`
- 策略中可通过 API 获取打分排名，如 `get_top_stocks(n=5)` 实现“买入得分前五”的逻辑

### 5.6 前端页面规划

| 页面 | 核心组件 | 功能 |
|:---|:---|:---|
| **自选股看板** | 实时行情表格 | 分组展示自选股，涨跌幅、现价、成交量 |
| **股票池管理** | 筛选条件构建器 | 可视化创建动态池，预览成分股 |
| **策略工作台** | Monaco 编辑器 + 参数面板 | 编写/修改策略代码，调整参数，一键回测 |
| **回测详情** | TradingView K线图 + 指标卡片 | 资金曲线、回撤阴影、交易信号标注 |
| **信号日志** | 列表 + 详情抽屉 | 查看历史信号，可跳转到对应K线位置 |
| **打分排行榜** | 表格 + 多因子雷达图 | 排名展示，个股多维度因子评分 |
| **模拟交易** | 持仓清单 + 净值曲线 | 模拟账户总资产走势，交易记录一览 |

所有前端组件复用 QuantDinger 中的 React + Tailwind + shadcn/ui 基础，图表使用 TradingView Lightweight Charts。

---

## 6. 部署方案

基于 Docker Compose 一键部署，包含以下服务：

```yaml
services:
  db:
    image: postgres:15
    environment:
      POSTGRES_DB: leek_quant
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql
    ports:
      - "5432:5432"

  redis:
    image: redis:7-alpine

  backend:
    build: ./backend
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:${DB_PASSWORD}@db/leek_quant
    volumes:
      - ./backend:/app
    ports:
      - "8000:8000"
    depends_on: [db, redis]

  celery_worker:
    build: ./backend
    command: celery -A app.tasks worker --loglevel=info
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:${DB_PASSWORD}@db/leek_quant
    depends_on: [db, redis]

  celery_beat:
    build: ./backend
    command: celery -A app.tasks beat --loglevel=info
    depends_on: [db, redis]

  frontend:
    build: ./frontend
    command: npm run dev
    ports:
      - "5173:5173"
    depends_on: [backend]

volumes:
  pgdata:
```

---

## 7. 开发里程碑

| 阶段 | 内容 | 交付物 |
|:---|:---|:---|
| P0 | 基础设施：Docker环境、PostgreSQL建表、数据源集成（AData/Baostock）、K线增量拉取 | 可运行后端，数据查询API |
| P1 | 股票池与自选股API；策略CRUD + Monaco编辑器；集成 Hikyuu 回测引擎，实现回测任务流程 | 用户可写策略并回测 |
| P2 | 五档信号日志生成与查询；完整模拟交易6表实现与引擎（含T+1/涨跌停/费用） | 纸上交易闭环 |
| P3 | 多因子打分模块：因子计算、IC分析、排行榜；实时行情WebSocket推送；前端可视化完善 | 平台核心功能齐备 |
| P4 | 参数敏感性分析、多账户隔离、数据校验告警、文档与教程 | 生产级可用 |

---

## 8. 集成开源项目总结

| 项目 | 在 Leek Quant 中的角色 | 集成位置 |
|:---|:---|:---|
| **Hikyuu** | 回测引擎核心 | Celery Worker，适配层调用 |
| **MyTT** | 技术指标库 | 策略编辑器内置，用户代码可 import |
| **AData** | A股历史K线、股票列表主源 | 数据获取模块 Tier1 |
| **Baostock** | K线备源 + 基本面数据提供 | 数据获取模块 Tier2 |
| **AkShare** | 分钟K线、全市场数据兜底 | 数据获取模块 Tier3 |
| **Qlib** | 因子表达式与计算范式参考 | 因子计算模块轻量复刻 |
| **东方财富WS** | 免费实时行情推送 | 独立 WebSocket 服务，Redis 广播 |
| **QuantDinger** | 前后端骨架、部署模板 | 裁剪非A股代码，保留核心框架 |

---

## 9. 风险与应对

| 风险 | 应对措施 |
|:---|:---|
| 免费数据源不稳定 | 三层回退机制；每日数据完整性校验；可扩展付费源接口 |
| Hikyuu API 变更 | 固定版本，编写适配层隔离直接依赖 |
| 回测性能瓶颈 | Hikyuu 本身为 C++ 实现，单机百万K线秒级；必要时多进程并行 |
| A股规则实现遗漏 | 建立全面的单元测试覆盖 T+1、涨跌停、除权除息、费用计算 |
| 用户策略安全性 | 策略在隔离进程执行，限制文件/网络访问，超时中断 |

---

*本文档基于 QuantDinger 架构裁剪，并深度集成 Hikyuu、MyTT、AData 等成熟开源组件，旨在以最小自研成本构建一个功能完整、数据安全、专注A股的量化交易平台。*