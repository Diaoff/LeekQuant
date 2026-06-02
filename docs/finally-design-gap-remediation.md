# finally-design.md 差异修复与技术债整改文档

> 版本：v0.1  
> 日期：2026-06-01  
> 适用范围：`docs/finally-design.md` 与当前 Leek Quant 代码实现之间的差异、缺失项和技术债整改。  
> 目标：把文档-代码核查结论转化为可执行修复路线，确保后续实现、测试和验收口径一致。

---

## 1. 背景与目标

`finally-design.md` 是 Leek Quant 的目标架构与阶段性设计文档。当前代码已实现 M0-M6a 的大部分能力，包括 PostgreSQL 统一存储、基础数据、策略与回测、五档信号、模拟交易、因子 MVP、Redis Pub/Sub 实时通道和盘中持仓调仓。

本文件不替代 `finally-design.md`，而是作为整改执行文档，解决以下问题：

- 代码实现与文档规范不一致。
- 文档未记录已实现功能。
- 核心模块存在安全、异常处理、监控和数据质量技术债。
- 部分金融交易语义未完全统一，可能影响回测、模拟交易和复盘口径。

整改原则：

- 保持纯 A 股、本地优先、隐私优先的产品定位。
- 保持 PostgreSQL 作为统一持久化存储，Redis 作为任务、缓存和实时广播组件。
- 金融金额、价格和费率继续使用 `Decimal` / PostgreSQL `NUMERIC`，不得引入浮点金额计算。
- 所有交易日判断必须查询 `trade_calendar`，不得硬编码节假日。
- 优先修复安全、数据完整性、资金/交易语义一致性问题。

---

## 2. 问题分级与修复顺序

| 优先级 | 问题 | 修复目标 |
| --- | --- | --- |
| P0 | 策略执行安全不足 | 策略执行隔离、超时、异常可观测，避免直接在 Worker 主进程无约束 `exec`。 |
| P0 | Hikyuu 适配器 async 调用风险 | 已移除 Hikyuu 适配器，回测统一使用 Python-native 引擎，消除 async 流程内 `asyncio.run()` 风险。 |
| P0 | 增量 K 线按全局最大日期推断 | 改为逐股票缺口检测，避免部分股票缺数据被全局日期掩盖。 |
| P1 | 数据源失败与数据质量告警不足 | 失败、复权缺失、异常涨跌幅写入 `alert_events` 并可查询。 |
| P1 | 涨跌停语义不一致 | 固定“下单/撮合/信号记录”语义，文档和代码保持一致。 |
| P1 | 盘中持仓调仓未写入文档 | 补充任务行为、触发窗口、风控边界、撮合规则。 |
| P1 | API / Celery / Docker 文档与实现不一致 | 同步实际路径、任务名、调度时间和本地/生产部署差异。 |
| P2 | 因子分析展示不完整 | 明确 MVP 与 M7+ 完整因子研究能力边界。 |
| P2 | 生产安全配置说明不足 | 明确 Redis 密码、监听地址、CORS、生产 compose 建议。 |

---

## 3. P0 修复项

### 3.1 策略执行安全沙箱

**现状**

- 文档要求用户策略不应直接在 FastAPI 进程内 `exec`，推荐 Celery Worker 子进程隔离、限制运行时间、内存、文件系统写入和网络访问，并将异常写入回测结果和任务记录。见 `docs/finally-design.md:1023-1030`。
- 当前 Python-native 回测引擎在 `backend/app/backtest/adapter.py:402-422` 直接执行 `exec(self.config.source_code, sandbox)`，且捕获所有异常后返回 `None`。
- 日终信号和盘中调仓也复用 `_exec_strategy`，位置为 `backend/app/tasks/signal_tasks.py`。

**风险**

- 任意策略代码可访问 Python 运行时能力，存在文件、网络、进程和资源滥用风险。
- 长循环或高内存策略可能阻塞 Celery Worker。
- 策略异常被吞掉后表现为“无信号”，无法复盘具体失败原因。

**修复方案**

1. 新增策略执行运行时模块，建议路径：`backend/app/backtest/strategy_runtime.py`。
2. 将 `generate_signal(ctx)` 执行迁移到独立子进程：
   - 输入：`source_code`、序列化后的 `BacktestContext`、允许函数白名单。
   - 输出：`{"ok": true, "signal": {...}}` 或 `{"ok": false, "error_type": "...", "error_message": "...", "traceback": "..."}`。
   - 默认超时：单次信号 2 秒，回测批量可配置但不得无限制。
3. 子进程环境只暴露：
   - `ctx`
   - `MyTT` 白名单函数
   - 必要数学函数，例如 `abs`、`min`、`max`、`sum`、`len`、`round`
4. 禁止或不暴露：
   - `open`
   - `__import__`
   - `eval`
   - `exec`
   - `compile`
   - `input`
   - 网络、文件和进程模块
5. 调整回测、日终信号、盘中调仓的策略调用入口，统一使用该 runtime。
6. 异常落库规则：
   - 回测：写入 `backtest_results.error_message` 和 `task_runs.error_message`。
   - 日终信号：加入任务返回 `errors`，包含 `strategy_id`、`ts_code`、`error_type`。
   - 盘中调仓：加入任务返回 `errors`，包含 `account_id`、`ts_code`、`error_type`。

**代码改动范围**

- `backend/app/backtest/adapter.py`
- `backend/app/backtest/tasks.py`
- `backend/app/tasks/signal_tasks.py`
- 新增 `backend/app/backtest/strategy_runtime.py`
- 新增或扩展策略运行测试文件

**测试要求**

- 正常策略返回买入/观望信号。
- 抛异常策略必须被记录，不能静默变成无信号。
- 无限循环策略被超时终止。
- 尝试读取文件、导入网络模块、调用危险 builtin 的策略被拒绝。
- 回测、日终信号、盘中调仓三条路径均覆盖策略异常。

**验收标准**

- 不再存在业务路径直接调用用户策略 `exec` 且无隔离的情况。
- 所有策略执行失败都能在任务结果或业务结果中定位策略、股票、账户和错误原因。

---

### 3.2 Hikyuu 适配器 async 调用与引擎职责

**现状**

- 历史文档曾将 Hikyuu 作为回测核心适配层。
- 当前代码已移除 `backend/app/backtest/hikyuu_adapter.py`，回测任务只调用 Python-native `BacktestRunner`。
- 结果统一写入 `performance.engine = "python_native"`。

**风险**

- 历史 Hikyuu 适配器存在嵌套事件循环风险。
- 两套回测口径并存会造成结果不可比。

**修复方案**

1. 移除 Hikyuu 依赖、适配器和专属测试。
2. 回测任务统一使用 Python-native `BacktestRunner`。
3. 结果中写入 `performance.engine = "python_native"`。
4. 更新文档，明确 Hikyuu 不再是 v1 依赖或验收项。

**代码改动范围**

- `backend/app/backtest/tasks.py`
- `backend/app/backtest/adapter.py`
- `docs/finally-design.md`

**测试要求**

- 回测正常落库，结果标记 `python_native`。
- 代码库不存在业务路径导入 `app.backtest.hikyuu_adapter`。
- `requirements.txt` 不再依赖 `hikyuu`。

**验收标准**

- 回测任务不再存在 Hikyuu 嵌套事件循环风险。
- 文档和结果字段明确说明当前使用 Python-native 回测引擎。

---

### 3.3 逐股票增量 K 线缺口修复

**现状**

- 文档要求增量更新按每只股票自身最新 `trade_date` 推断，见 `docs/finally-design.md:785-809`。
- 当前 `infer_incremental_kline_window()` 使用全表 `MAX(trade_date)` 推断全局窗口，见 `backend/app/data/service.py:390-413`。

**风险**

- 如果部分股票停更、缺历史、补录失败或新上市，全局最大日期会掩盖单股缺口。
- 回测和因子计算可能基于不完整数据。

**修复方案**

1. 保留现有全局快速增量任务，用于常规每日更新。
2. 新增逐股票缺口检测服务：
   - 输入：可选 `ts_codes`、`start_date`、`end_date`、`limit`。
   - 查询每只股票自身 `MAX(daily_kline.trade_date)`。
   - 根据 `stock_basic.list_date` 和 `trade_calendar` 推断应补窗口。
3. 新增逐股票修复任务：
   - 任务名建议：`repair_kline_gaps`
   - 队列：`data`
   - 支持并发上限配置。
4. `data_update_state` 增加或复用字段记录：
   - `data_type = 'daily_kline_gap_repair'`
   - `ts_code`
   - `last_success_at`
   - `failure_count`
5. 前端任务页增加“修复 K 线缺口”入口，或先提供 API。

**代码改动范围**

- `backend/app/data/service.py`
- `backend/app/tasks/data_tasks.py`
- `backend/app/api/tasks.py`
- `backend/app/data/repository.py`
- 可选：`frontend/src/pages/StatusPage.tsx`

**测试要求**

- 股票 A 已到最新，股票 B 缺 10 天，任务只补 B。
- 新上市股票 `list_date` 晚于默认起始日期时，从 `list_date` 开始。
- 非交易日不请求 K 线。
- 重复运行幂等，`daily_kline` 不产生重复。

**验收标准**

- 可列出和修复逐股票缺口。
- 不再只有全局最大日期一种增量判断口径。

---

## 4. P1 修复项

### 4.1 数据质量、异常处理与告警闭环

**现状**

- 文档要求数据源失败写 `alert_events`，复权缺失记录告警，异常涨跌幅等待二次源校验。见 `docs/finally-design.md:771-783`。
- 当前 fallback 在 `backend/app/data/fetcher.py:123-155` 收集错误并抛出 `DataProviderError`。
- 当前 K 线校验在 `backend/app/data/validators.py:26-57` 只覆盖基础 OHLC、成交量和成交额。
- `alert_events` 表已在迁移中创建，见 `backend/alembic/versions/202605150001_m0_foundation.py:151-163`，但缺少统一写入和查询 API。

**风险**

- 数据源持续失败、复权缺失、异常价格无法沉淀为可查询事件。
- 用户只能看到任务失败，无法按股票、数据源、错误类别复盘。

**修复方案**

1. 新增告警 repository：
   - `create_alert_event(session, level, category, title, message, payload)`
   - `resolve_alert_event(session, alert_id)`
   - `list_alert_events(session, filters)`
2. 新增 API：
   - `GET /api/system/alerts`
   - `POST /api/system/alerts/{id}/resolve`
3. 数据源失败写入：
   - category: `data_source`
   - level: `warning` 或 `error`
   - payload: provider、method、ts_code、date_range、error
4. 数据质量异常写入：
   - 复权因子缺失：`category = data_quality`
   - 涨跌幅超板块限制：`category = price_anomaly`
   - OHLC 非法：`category = data_validation`
5. 对同一 provider/method/ts_code/date 的重复告警做去重或短时间合并，避免刷屏。

**代码改动范围**

- `backend/app/data/repository.py`
- `backend/app/data/fetcher.py`
- `backend/app/data/validators.py`
- 新增 `backend/app/api/system.py` 或扩展现有 tasks/status API
- `backend/app/main.py`

**测试要求**

- 模拟所有 provider 失败后生成告警。
- 复权因子为空但行情可入库时生成 warning。
- 异常涨跌幅生成 warning 并保留原始 payload。
- alerts API 支持分页、按 resolved 过滤、resolve 操作。

**验收标准**

- 任务失败或数据异常能在 `alert_events` 和 API 中追踪。
- 告警不会破坏原有数据同步事务一致性。

---

### 4.2 涨跌停下单与撮合语义统一

**现状**

- 文档第 6.4 同时表达“涨停/跌停信号可生成委托，撮合可能不成交”和“规则过滤信号写 `BLOCKED`”，见 `docs/finally-design.md:1095-1106`。
- 当前模拟交易在下单阶段调用 `apply_cn_rules(..., enforce_price_limits=False)`，位置为 `backend/app/sim/service.py:687-695`。
- 撮合阶段会按涨跌停拦截，位置为 `backend/app/sim/service.py:949-957`。

**推荐口径**

采用“信号可记录、委托可生成、撮合阶段按涨跌停阻断或保留待成交”的模拟交易口径。

**修复方案**

1. 修改 `finally-design.md` 第 6.4：
   - 非交易日、停牌、T+1 可在下单前 `BLOCKED`。
   - 涨停买入、跌停卖出默认允许生成委托，但撮合时不成交或返回限价未触达。
2. `signal_log.action` 语义改为“状态机/下单前规则结果”，撮合失败不反写 signal。
3. `sim_orders.reject_reason` 或撮合返回中明确记录涨跌停原因。
4. 如果未来要改成下单前拦截，则必须将 `enforce_price_limits=True` 并同步测试。

**代码改动范围**

- `docs/finally-design.md`
- `backend/app/sim/service.py`
- `backend/tests/test_sim_trading.py`
- `backend/tests/test_signals.py`

**测试要求**

- 涨停买入信号能生成委托，但撮合失败或保持待成交，原因明确。
- 跌停卖出信号同上。
- 停牌仍在规则层阻断。
- T+1 未解锁卖出仍阻断。

**验收标准**

- 文档、测试、代码对涨跌停的解释一致。

---

### 4.3 盘中持仓调仓文档补齐

**现状**

- `generate_intraday_position_signals_for_date()` 已实现盘中持仓调仓，位置为 `backend/app/tasks/signal_tasks.py:667-787`。
- Celery task 注册为 `app.tasks.signal_tasks.generate_intraday_position_signals`，位置为 `backend/app/tasks/signal_tasks.py:803-813`。
- `finally-design.md` 未明确记录该任务。

**当前实现行为**

- 只扫描 active 模拟账户中已有持仓。
- 账户必须绑定 active 策略。
- 仅在交易日和盘中窗口运行：`09:25-11:30`、`13:00-15:00`。
- 只接受 `增持`、`减仓`、`卖出`、`观望`，忽略 `买入`。
- 增持使用 `ask1`，减仓/卖出使用 `bid1`，缺失回退最新价。
- 调用 `generate_order_from_signal(..., auto_match=True, auto_match_mode="limit")`。
- 同一账户同一股票已有待成交/部分成交委托时跳过。

**修复方案**

1. 在 `finally-design.md` 第 6 章或第 7 章新增“盘中持仓调仓”小节。
2. 在第 11.4 Celery Beat 任务中补充是否自动调度：
   - 推荐 v1 不默认高频 beat，先手动/API 触发或低频调度。
   - 若自动调度，建议每 1-5 分钟运行，必须有去重和交易窗口检查。
3. 增加 API 入口或任务入口文档：
   - 可选：`POST /api/signals/intraday-position`
   - 或通过 Celery task 内部触发。
4. 明确实时止盈/止损优先级高于策略调仓。

**测试要求**

- 非交易日、非交易时段返回 skipped。
- 无持仓、无策略、无实时行情、已有待成交委托均有明确 skip reason。
- 增持、减仓、卖出分别使用正确盘口价。
- 不新开仓。

**验收标准**

- 文档能指导运维和前端理解该任务，不再是“隐藏功能”。

---

### 4.4 API、任务与文档路径同步

**现状**

- 文档列出 `GET /api/system/tasks`、`GET /api/system/alerts`、`POST /api/data/sync`，见 `docs/finally-design.md:1324-1355`。
- 当前任务 API 是：
  - `GET /api/tasks/recent`，见 `backend/app/api/tasks.py:402-414`
  - `POST /api/tasks/data/sync-all-kline`，见 `backend/app/api/tasks.py:246-278`
  - `POST /api/tasks/data/incremental-kline`，见 `backend/app/api/tasks.py:281-306`
  - `POST /api/data/sync/stock-basic`，见 `backend/app/api/data.py:37-43`
  - `POST /api/data/sync/trade-calendar`，见 `backend/app/api/data.py:46-58`
  - `POST /api/data/sync/kline`，见 `backend/app/api/data.py:61-87`
- `GET /api/system/alerts` 尚未实现。

**修复方案**

二选一，推荐 A：

- A. 更新文档为当前真实 API，并将 `/api/system/*` 标为 M7+。
- B. 增加兼容路由：
  - `GET /api/system/tasks` 代理到 `/api/tasks/recent`
  - `GET /api/system/alerts` 查询 `alert_events`
  - `POST /api/data/sync` 根据 body 分发 stock/kline/calendar

**推荐实现**

采用 B 中的 alerts，并采用 A 更新其余路径。原因：任务 API 已被前端使用，增加大量别名会扩大维护面；alerts 是真实缺口，应该补齐。

**测试要求**

- OpenAPI 包含最终文档声明的路径。
- 前端 Status 页面 API 与文档一致。
- alerts 路由有分页和 resolved 过滤。

**验收标准**

- `finally-design.md` 第 9 章路径与代码一致，或明确标注兼容/延后状态。

---

### 4.5 Celery Beat 与 Docker Compose 文档同步

**现状**

- 文档第 11.4 的任务名和时间与代码不同，见 `docs/finally-design.md:1632-1681`。
- 当前 beat 配置在 `backend/app/tasks/celery_app.py:51-88`。
- 当前 Docker Compose 增加了 `realtime_risk_guard` 服务，见 `docker-compose.yml:116-137`。
- 当前 Redis 默认无密码且绑定本地端口，见 `docker-compose.yml:23-31`。

**修复方案**

1. 更新 `finally-design.md` 第 11.4 为当前实际任务：
   - `app.tasks.data_tasks.update_stock_basic`
   - `app.tasks.data_tasks.update_trade_calendar`
   - `app.tasks.data_tasks.incremental_kline_update`
   - `app.tasks.signal_tasks.generate_all_signals`
   - `app.tasks.factor_tasks.compute_daily_factors`
   - `app.tasks.data_tasks.sync_fundamentals`
   - `app.tasks.trading_tasks.unlock_t1_daily`
   - `app.tasks.trading_tasks.match_pending_orders`
   - `app.tasks.trading_tasks.snapshot_nav_daily`
2. 明确文档中的 intraday match 和 weekly scoring 为未实现或 M7+。
3. 更新 Docker 文档：
   - local compose：无 Redis 密码、127.0.0.1 端口绑定、含 `realtime_risk_guard`。
   - production compose：必须设置 Redis 密码、强 `SECRET_KEY`、受控 CORS、反向代理 TLS。
4. 修正文档中 celery command：
   - 当前为 `celery -A app.tasks.celery_app:celery_app ...`

**测试要求**

- `docker compose config` 成功。
- Celery worker 能加载所有队列。
- Celery beat 中任务名均能被 worker 识别。

**验收标准**

- 部署文档不再混用旧任务路径和当前 Compose。

---

## 5. P2 修复项

### 5.1 因子分析 MVP 与完整能力边界

**现状**

- 文档已注明首版使用 Python 直接计算 8 个内置因子，而非完整 Qlib-like 表达式引擎，见 `docs/finally-design.md:1234-1239`。
- 当前多因子打分在 `backend/app/factor/service.py:365-447`。
- IC/IR 分析在 `backend/app/factor/service.py:615-702`。
- 文档第 8.5 提到 IC 时间序列、IC 分布、分层收益、多空组合收益和因子衰减曲线，见 `docs/finally-design.md:1308-1320`，前端和 API 目前不完整。

**修复方案**

1. 将文档第 8.5 拆成：
   - 已实现 MVP：IC、IC mean/std、IR/ICIR、IC>0 比例、分组收益。
   - M7+：IC 时间序列图、分布直方图、多空组合收益、衰减曲线、动态表达式引擎。
2. 如果要补完整能力：
   - API 增加 `GET /api/factors/analysis/{factor_name}/series`
   - 前端 Factor 页面展示 IC time series 和 group returns。
3. 因子表达式引擎保持 M7+，不要在当前 MVP 中隐式支持未解析表达式。

**测试要求**

- IC/IR 无样本时返回可理解错误。
- 样本足够时写入 `factor_analysis` 并幂等 upsert。
- 前端能展示已有字段。

**验收标准**

- 文档不再暗示完整 Qlib 表达式引擎已实现。

---

### 5.2 生产安全配置

**现状**

- 文档第 11 章偏生产样例，代码中的 `docker-compose.yml` 偏本地开发。
- 当前 `redis` 未设置密码，绑定本地端口，见 `docker-compose.yml:23-31`。

**修复方案**

1. 保留当前 local compose。
2. 新增 `docs/deployment-production.md` 或在 `finally-design.md` 中新增生产部署说明：
   - Redis 必须设置密码。
   - PostgreSQL 密码必须从 `.env` 注入。
   - `SECRET_KEY` 不允许使用默认值。
   - 后端只暴露给反向代理。
   - CORS 必须限定域名。
   - 生产启用 HTTPS。
3. 可选新增 `docker-compose.prod.yml`。

**测试要求**

- `docker compose -f docker-compose.yml config` 成功。
- 若新增 prod compose，`docker compose -f docker-compose.yml -f docker-compose.prod.yml config` 成功。

**验收标准**

- 本地与生产部署配置边界清晰。

---

## 6. 金融逻辑整改清单

### 6.1 必须保持的正确实现

以下当前实现符合设计，应在后续改动中保持：

- 最大回撤按运行峰值计算：`backend/app/backtest/adapter.py:659-665`。
- 费用模型包含佣金、最低佣金、卖出印花税和过户费：`backend/app/backtest/cost.py:12-15`、`backend/app/backtest/cost.py:89-99`。
- 买入成交后 `available_shares = 0`，符合 T+1：`backend/app/sim/service.py:1003-1008`。
- T+1 解锁按交易日和当日买入量重算可卖数量：`backend/app/sim/service.py:1302-1331`。
- K 线 upsert 使用 `COALESCE(EXCLUDED.adj_factor, daily_kline.adj_factor)` 保留旧复权因子：`backend/app/data/repository.py:117-132`。

### 6.2 需要统一口径的实现

- 涨跌停：固定为“可生成委托，撮合阶段拦截或待成交”。
- 盘中调仓：只对已有持仓执行，不进行全市场扫描和新开仓。
- 实时止盈/止损：优先级高于策略调仓，且重复卖出委托必须去重。
- 因子计算：不得使用未来数据计算因子值；未来收益只用于 IC/IR 标签。

### 6.3 建议补充测试

- 涨停买入委托与跌停卖出委托的撮合行为。
- 停牌委托阻断。
- 当日买入后盘中策略卖出触发 T+1 阻断。
- 盘中调仓遇到已有待成交委托时跳过。
- 缺少实时行情时盘中调仓不下单。
- 因子计算只读取 `trade_date <= run_date` 的基本面/K 线。

---

## 7. 文档同步任务

完成代码修复后，同步更新以下文档段落：

| 文档位置 | 更新内容 |
| --- | --- |
| `finally-design.md` 第 5 章 | 明确当前 Python-native 回测引擎策略和策略沙箱约束。 |
| `finally-design.md` 第 6 章 | 修正涨跌停和 `BLOCKED` 语义，新增盘中持仓调仓说明。 |
| `finally-design.md` 第 7 章 | 补实时风控守护进程、盘中调仓与撮合关系。 |
| `finally-design.md` 第 8 章 | 区分因子 MVP 与 M7+ 完整分析能力。 |
| `finally-design.md` 第 9 章 | 同步实际 API 路径、WebSocket 状态和 alerts 缺口。 |
| `finally-design.md` 第 11 章 | 同步当前 Docker Compose、Celery command 和 beat schedule。 |
| `README.md` | 增加盘中调仓、实时风控、生产安全简述。 |

---

## 8. 分阶段实施建议

### Phase A：安全与数据完整性

1. 策略执行沙箱。
2. 移除 Hikyuu 适配器并统一引擎来源标记。
3. 逐股票 K 线缺口检测与修复。

**完成判定**

- 回测和信号路径策略异常可观测。
- 回测任务不出现嵌套 event loop 风险。
- 可检测并修复单股 K 线缺口。

### Phase B：监控、语义和文档一致性

1. `alert_events` 写入与查询 API。
2. 涨跌停语义统一。
3. 盘中调仓和实时风控写入 `finally-design.md`。
4. API/Celery/Docker 文档同步。

**完成判定**

- 数据源失败和数据质量异常可在 API 查询。
- 涨跌停相关测试与文档一致。
- 文档能准确描述当前服务、任务和部署方式。

### Phase C：增强项

1. 因子分析前端图表增强。
2. 任务/信号 WebSocket。
3. 生产 compose 或生产部署文档。
4. Qlib-like 表达式引擎。

**完成判定**

- M7+ 能力从“隐含目标”变成明确计划和验收项。

---

## 9. 全量验收命令建议

整改完成后至少运行：

```bash
./.venv/bin/pytest backend/tests/test_signals.py -q
./.venv/bin/pytest backend/tests/test_sim_trading.py backend/tests/test_sim_api.py -q
./.venv/bin/pytest backend/tests/test_signal_tasks.py -q
./.venv/bin/pytest backend/tests/test_realtime.py backend/tests/test_realtime_integration.py -q
./.venv/bin/pytest backend/tests/test_factor_tasks.py backend/tests/test_factor_api.py backend/tests/test_m5_factors.py -q
./.venv/bin/pytest backend/tests/test_data_service.py backend/tests/test_data_fetcher.py backend/tests/test_data_normalizers.py backend/tests/test_data_providers.py -q
./.venv/bin/pytest backend/tests/test_tasks.py backend/tests/test_backtest_risk_config.py backend/tests/test_backtest_engine_selection.py -q
```

如果涉及前端或 WebSocket：

```bash
npm run test:smoke
npx playwright test
```

如果涉及部署：

```bash
docker compose config
```

---

## 10. 完成状态跟踪模板

| 编号 | 优先级 | 事项 | 状态 | PR/提交 | 验收测试 |
| --- | --- | --- | --- | --- | --- |
| R-001 | P0 | 策略执行沙箱 | Pending |  |  |
| R-002 | P0 | 移除 Hikyuu 适配器并统一回测引擎 | Done |  | `test_backtest_engine_selection.py` |
| R-003 | P0 | 逐股票 K 线缺口修复 | Pending |  |  |
| R-004 | P1 | alert_events 告警闭环 | Pending |  |  |
| R-005 | P1 | 涨跌停语义统一 | Pending |  |  |
| R-006 | P1 | 盘中调仓文档同步 | Pending |  |  |
| R-007 | P1 | API/Celery/Docker 文档同步 | Pending |  |  |
| R-008 | P2 | 因子分析展示边界 | Pending |  |  |
| R-009 | P2 | 生产部署安全文档 | Pending |  |  |
