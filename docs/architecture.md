# Leek Quant 技术架构

## 系统分层

```
┌──────────────────────────────────────────────────────┐
│                   前端层 (React 18 + Vite)              │
│  Dashboard │ Market │ Strategy │ Backtest │ Sim │
└──────────────────────┬───────────────────────────────┘
                       │ HTTP / WebSocket
┌──────────────────────▼───────────────────────────────┐
│                  API层 (FastAPI)                       │
│  11个路由模块 + 3个WebSocket端点 + Pydantic v2校验    │
└──────────────────────┬───────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────┐
│             异步任务层 (Celery + Redis)                 │
│  DataWorker │ BacktestWorker │ SimWk   │
│  5个队列 + 9个定时任务 (Celery Beat)                  │
└──────┬──────────────┬──────────────┬─────────────────┘
       │              │              │
┌──────▼──────┐ ┌────▼─────┐ ┌──────▼────┐
│   AData     │ │Backtest  │ │  MyTT     │
│  Baostock   │ │ Runner   │ │ (28指标)   │
│  AkShare    │ │          │ │           │
└──────┬──────┘ └────┬─────┘ └──────┬────┘
       │              │              │
┌──────▼──────────────▼──────────────▼────────────────┐
│                 存储层                                │
│  PostgreSQL 15+ (26张表, 年分区)                     │
│  Redis 7 (队列 / 缓存 / Pub/Sub)                    │
└─────────────────────────────────────────────────────┘
```

## 后端架构

### 模块组织

| 模块 | 路径 | 职责 |
|------|------|------|
| API | `backend/app/api/` | 11个路由文件，处理HTTP/WS请求 |
| 回测 | `backend/app/backtest/` | BacktestRunner引擎、费用模型、信号状态机、策略沙箱 |
| 数据 | `backend/app/data/` | 三层数据回退(fetcher)、数据标准化、校验 |
| 因子（⚠️ 已废弃） | _(原 `backend/app/factor/`，代码已移除)_ | M5 多因子选股已废弃，相关包、API、任务与前端页均删除 |
| 实时 | `backend/app/realtime/` | 东方财富WS解析、Redis Pub/Sub、风险守卫 |
| 模拟交易 | `backend/app/sim/` | 6表闭环：账户/持仓/委托/成交/流水/净值 |
| 任务 | `backend/app/tasks/` | Celery配置、数据/信号/交易任务 |

### 数据流

**回测流程**
```
策略源码 → StrategyRuntime(沙箱执行) → Signal(五档状态机)
→ BacktestRunner(T+1/涨跌停/费用) → TradeRecords + EquityCurve + Metrics
```

**实时行情流**
```
EastMoney WS → eastmoney_ws.py(解析) → ws_producer.py(Redis Pub)
→ /ws/realtime(后端推送) → useRealtimeTicks(前端Hooks) → 前端UI
```

**因子计算流（⚠️ 已废弃，仅作历史参考）**
```
factor_definitions(DB) + daily_kline → service.py(计算标准化)
→ factor_values → scoring_rank(Top N) → factor_analysis(IC/IR)
```
> M5 多因子选股功能代码已整体移除，上述链路当前不存在。

## 数据库设计

### 约 26 张核心业务表，统一存储在 PostgreSQL（注：因子表 `factor_*` / `scoring_rank` / `factor_analysis` 为旧迁移残留下已废弃；`data_update_state` 与 `stock_pools` 表已删除）

| 类别 | 表 | 说明 |
|------|-----|------|
| 基础 | `stock_basic` | 全市场5000+股票信息 |
| 行情 | `daily_kline` (年分区) | 日K线、复权因子 |
| 日历 | `trade_calendar` | 交易日历(含前后交易日索引) |
| 数据源 | `data_source_config` | 数据源配置（`data_update_state` 表已删除，增量同步不再依赖它） |
| 自选股 | `watchlist_groups`, `watchlist_items` | 分组自选股管理 |
| 策略 | `strategies` | 策略源码与配置 |
| 回测 | `backtest_results` | 回测结果(JSONB存储净值曲线) |
| 信号 | `signal_log` | 五档信号历史 |
| 模拟交易 | `sim_accounts/positions/orders/trades/cash_flow/daily_nav` | 6表闭环 |
| 因子（⚠️ 已废弃） | `factor_definitions/values/scoring_rank/analysis/factor_score_runs` | 由早期迁移创建但 M5 代码已移除，当前无功能使用 |

## Celery 任务调度

### 队列（5个）

| 队列 | 用途 |
|------|------|
| `default` | 通用任务 |
| `data` | 数据拉取 |
| `backtest` | 回测执行 |
| `trading` | 模拟交易处理 |

### 定时任务（9个，通过 Celery Beat 驱动）

| 任务 | 时间 | 说明 |
|------|------|------|
| update-stock-basic | 周六 3:00 AM | 更新股票基础信息 |
| update-trade-calendar | 周日 2:00 AM | 更新交易日历 |
| incremental-kline-update | 每日 17:00 | 增量拉取K线 |
| generate-all-signals | 每日 12:00 | 生成全市场信号 |
| sync-fundamentals | 每日 19:30 | 同步财务数据 |
| unlock-t1-positions | 每日 9:25 AM | T+1解锁 |
| match-pending-orders | 每日 17:05 | 撮合挂单 |
| snapshot-nav-daily | 每日 15:20 | 净值快照 |

## Docker 服务编排

| 服务 | 镜像 | 说明 |
|------|------|------|
| `postgres` | 15-alpine | 主存储，健康检查 |
| `redis` | 7-alpine | 队列/缓存/PubSub，LRU淘汰 |
| `backend` | python:3.12-slim | FastAPI应用 |
| `celery_worker` | python:3.12-slim | 5队列任务处理 |
| `celery_beat` | python:3.12-slim | 定时任务调度 |
| `realtime_risk_guard` | python:3.12-slim | 实时止盈止损守护 |
| `realtime_ws` | python:3.12-slim | 东方财富WS生产者 |
| `frontend` | nginx:1.25-alpine | React SPA |

## 关键技术决策

1. **统一 PostgreSQL**：所有数据（市场数据/模拟交易/系统状态）存储在一个 PostgreSQL 中，避免多存储维护（早期规划的因子数据已随 M5 废弃）
2. **三层数据回退**：AData → Baostock → AkShare 自动故障切换，增量拉取进度不再依赖已删除的 `data_update_state` 表
3. **Python-native 回测**：BacktestRunner 纯 Python 实现，与平台信号/风控/费用口径一致
4. **5级信号状态机**：买入/增持/减仓/卖出/观望 → 状态机映射为 BUY/SELL_PARTIAL/SELL_ALL/HOLD/BLOCKED
5. **模拟交易闭环**：6表体系确保资金守恒，所有操作可审计可重放
6. **东方财富 WS 自建解析**：无需第三方库依赖，支持指数退避重连和动态code订阅
