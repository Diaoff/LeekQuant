# finally-design.md 剩余差异修复与技术债整改文档

> 版本：v0.2  
> 日期：2026-06-02  
> 适用范围：`docs/finally-design.md` 与当前 Leek Quant 代码实现之间仍未完全闭环的差异、缺失项和技术债。  
> 说明：本版移除已修复问题的展开描述，仅保留剩余整改项和已完成归档，避免重复立项。

---

## 1. 背景与当前结论

`finally-design.md` 是 Leek Quant 的目标架构与阶段性设计文档。当前代码已实现 M0-M6a 的大部分能力，包括 PostgreSQL 统一存储、基础数据、策略与回测、五档信号、模拟交易、因子 MVP、Redis Pub/Sub 实时通道、逐股票 K 线缺口检测和盘中持仓调仓。

本文件用于跟踪剩余整改项。已完成事项不再作为待办展开，仅在“已完成归档”中保留证据路径。

当前剩余问题集中在：

- `alert_events` 已能写入和查询，但缺少 resolve 操作，告警闭环不完整。
- 模拟交易涨跌停语义已基本统一到撮合阶段阻断，但回测规则层仍提前阻断，复盘口径仍需选择并统一。
- `sync_kline(commit_each=True)` 相关测试会触发真实 PostgreSQL 连接，测试隔离不足。

整改原则保持不变：

- 纯 A 股、本地优先、隐私优先。
- PostgreSQL 作为统一持久化存储，Redis 作为任务、缓存和实时广播组件。
- 金融金额、价格和费率使用 `Decimal` / PostgreSQL `NUMERIC`。
- 所有交易日判断查询 `trade_calendar`，不得硬编码节假日。
- 优先修复安全、数据完整性、资金/交易语义一致性问题。

---

## 2. 剩余问题分级

| 编号 | 优先级 | 事项 | 当前状态 | 修复目标 |
| --- | --- | --- | --- | --- |
| R-004 | P1 | `alert_events` 告警闭环 | Partial | 在现有写入和查询基础上补 resolve repository/API，并覆盖测试。 |
| R-005 | P1 | 涨跌停语义统一 | Partial | 明确并统一回测、模拟交易、信号记录的涨跌停处理口径。 |
| R-010 | P1 | `sync_kline` 测试隔离 | Pending | `commit_each=True` 分支测试不应连接真实 PostgreSQL。 |

---

## 3. 待整改项

### 3.1 R-004：`alert_events` 告警闭环

**当前状态**

- `alert_events` 表已存在。
- 数据同步和质量异常路径已有 `create_alert()` 写入。
- 已实现 `GET /api/system/alerts`，支持 level / category / is_resolved / limit / offset 查询。
- 尚未实现 `resolve_alert_event()` 和 `POST /api/system/alerts/{id}/resolve`。

**风险**

- 告警只能查看，不能关闭或标记已处理。
- 前端/运维无法区分新告警和已处理历史告警。
- 文档中的“告警闭环”验收口径尚未完全满足。

**修复方案**

1. 在告警 repository 中新增 resolve 方法：
   - 输入：`alert_id`
   - 行为：将 `is_resolved = TRUE`，`resolved_at = NOW()`
   - 返回：更新后的告警记录；不存在时返回 404 或等价业务错误。
2. 在 `backend/app/api/system.py` 新增：
   - `POST /api/system/alerts/{alert_id}/resolve`
3. 补充 API 测试：
   - 查询支持 resolved 过滤。
   - resolve 成功返回 `is_resolved = true`。
   - resolve 不存在 ID 返回 404。

**验收标准**

- 告警能写入、查询、过滤、标记处理。
- resolve 操作不影响历史 payload 和创建时间。

---

### 3.2 R-005：涨跌停语义统一

**当前状态**

- 模拟交易下单路径已调用 `apply_cn_rules(..., enforce_price_limits=False)`，涨停买入/跌停卖出允许生成委托。
- 模拟交易撮合阶段会按涨跌停阻断，订单保持待成交，并写入 `reject_reason`。
- `finally-design.md` 已说明“涨停买入、跌停卖出不在下单前写 BLOCKED；撮合阶段不成交”。
- Python-native 回测 `_apply_rules()` 仍在规则层直接阻断：
  - 买入涨停返回“涨停不可买入”。
  - 卖出跌停返回“跌停不可卖出”。

**风险**

- 模拟交易与回测对同一信号可能产生不同解释。
- 复盘时难以判断“策略没有生成交易”还是“生成交易后因撮合规则未成交”。
- `signal_log.action`、回测交易记录、模拟委托状态的语义边界仍不完全一致。

**推荐口径**

统一采用当前 `finally-design.md` 和模拟交易已实现的口径：

- 信号可以记录。
- 委托可以生成。
- 非交易日、停牌、T+1 可在下单前阻断。
- 涨停买入、跌停卖出在撮合阶段阻断或保持待成交，并记录原因。

**修复方案**

1. 调整回测规则层：
   - `_apply_rules()` 不再因涨停买入/跌停卖出直接阻断。
   - 保留停牌、无持仓、T+1 等下单前阻断规则。
2. 在回测成交逻辑中引入撮合阶段涨跌停判断：
   - 买入遇涨停不成交，记录 skipped / blocked reason。
   - 卖出遇跌停不成交，记录 skipped / blocked reason。
3. 统一测试命名和断言：
   - `test_signals.py` 保留 `enforce_price_limits=False` 的规则层用例。
   - `test_sim_trading.py` 保持撮合阶段阻断用例。
   - `test_adapter.py` 增加或更新回测涨跌停撮合语义测试。

**验收标准**

- 文档、回测、模拟交易对涨跌停解释一致。
- 涨停买入/跌停卖出不会在信号/下单前被误标为策略无动作。
- 撮合失败原因可在订单或回测交易结果中复盘。

---

### 3.3 R-010：`sync_kline(commit_each=True)` 测试隔离

**当前状态**

目标测试命令当前结果为 `170 passed, 1 failed`。失败项：

```text
backend/tests/test_data_service.py::test_sync_kline_reports_progress_on_completion
```

失败原因：

- `sync_kline(..., commit_each=True, concurrency=2)` 分支内部使用 `async_session_factory()` 创建真实 worker session。
- 测试只替换了部分 repository 方法，没有替换 per-stock session factory 或 `_is_st_stock()`。
- 在沙箱环境中连接本地 PostgreSQL 被拒绝，报 `PermissionError: [Errno 1] Operation not permitted`。

**风险**

- 单元测试依赖真实数据库，CI 或沙箱环境不稳定。
- `commit_each=True` 分支的并发/进度逻辑可测性不足。

**修复方案**

1. 选择一个明确实现方式：
   - 推荐：在 `sync_kline()` 增加仅内部使用的可注入 session factory 参数，例如 `session_factory=None`，默认使用 `async_session_factory`。
   - 测试中注入 fake session factory，避免真实 DB 连接。
2. 或者在测试中 monkeypatch `app.data.service.async_session_factory` / `_is_st_stock()`，但长期可维护性弱于显式注入。
3. 保持生产行为不变：
   - 未传入 session factory 时仍使用真实 `async_session_factory()`。
   - `commit_each=False` 路径不受影响。

**验收标准**

- `test_sync_kline_reports_progress_on_completion` 不再连接真实 PostgreSQL。
- 逐股票 gap 和增量 K 线相关测试稳定通过。

---

## 4. 已完成归档

| 编号 | 原优先级 | 事项 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| R-001 | P0 | 策略执行安全沙箱 | Done | `SAFE_BUILTINS` 已显式注入策略 sandbox；信号任务不再使用 inline 执行；`test_strategy_runtime.py` 覆盖 dangerous builtin / import / 文件读取拒绝。 |
| R-002 | P0 | 移除 Hikyuu 适配器并统一回测引擎 | Done | 回测结果写入 `performance.engine = "python_native"`；`test_backtest_engine_selection.py` 覆盖。 |
| R-003 | P0 | 逐股票 K 线缺口检测与修复 | Done | `infer_incremental_kline_ranges()`、`incremental_kline_update()` 已存在；`test_data_service.py` 和 `test_tasks.py` 覆盖 gap 场景。 |
| R-006 | P1 | 盘中持仓调仓文档同步 | Done | `finally-design.md` 已记录 `generate_intraday_position_signals` 任务、交易窗口和不默认 beat 调度策略。 |
| R-007 | P1 | API/Celery/Docker 文档同步 | Mostly Done | `finally-design.md` 已同步当前任务路径、alerts 查询路径、local compose、celery command 和 Redis/生产安全说明；剩余 resolve API 归入 R-004。 |
| R-008 | P2 | 因子分析 MVP 与完整能力边界 | Done | `finally-design.md` 已区分因子 MVP 与 M7+ 表达式/图表增强能力。 |
| R-009 | P2 | 生产安全配置说明 | Done | `finally-design.md` 已说明 Redis 密码、强 `SECRET_KEY`、受控 CORS、反向代理 TLS 等生产要求。 |

---

## 5. 金融逻辑保持项

以下实现当前符合设计，后续整改不得破坏：

- 最大回撤按运行峰值计算。
- 费用模型包含佣金、最低佣金、卖出印花税和过户费。
- 买入成交后 `available_shares = 0`，符合 T+1。
- T+1 解锁按交易日和当日买入量重算可卖数量。
- K 线 upsert 使用 `COALESCE(EXCLUDED.adj_factor, daily_kline.adj_factor)` 保留旧复权因子。
- 因子计算不得使用未来数据计算因子值；未来收益只用于 IC/IR 标签。

---

## 6. 建议验收命令

当前本地可用命令为：

```bash
python3 -m pytest backend/tests/test_strategy_runtime.py backend/tests/test_data_service.py backend/tests/test_tasks.py backend/tests/test_api_data.py backend/tests/test_sim_trading.py backend/tests/test_signals.py backend/tests/test_signal_tasks.py backend/tests/test_backtest_engine_selection.py -q
```

R-001 专项核查结果：

```text
27 passed
```

命令：

```bash
python3 -m pytest backend/tests/test_strategy_runtime.py backend/tests/test_signal_tasks.py backend/tests/test_backtest_engine_selection.py -q
```

全量目标命令最近一次核查结果：

```text
170 passed, 1 failed
```

失败项为 R-010 中记录的 `test_sync_kline_reports_progress_on_completion`。

涉及前端或 WebSocket 时追加：

```bash
npm run test:smoke
npx playwright test
```

涉及部署时追加：

```bash
docker compose config
```

---

## 7. 当前状态表

| 编号 | 优先级 | 事项 | 状态 | 下一步验收 |
| --- | --- | --- | --- | --- |
| R-001 | P0 | 策略执行安全沙箱 | Done | 保持 `test_strategy_runtime.py` 通过。 |
| R-002 | P0 | 移除 Hikyuu 适配器并统一回测引擎 | Done | 保持 `test_backtest_engine_selection.py` 通过。 |
| R-003 | P0 | 逐股票 K 线缺口修复 | Done | 保持 gap 相关测试通过。 |
| R-004 | P1 | `alert_events` 告警闭环 | Partial | resolve repository/API 和 404 测试通过。 |
| R-005 | P1 | 涨跌停语义统一 | Partial | 回测和模拟交易同口径测试通过。 |
| R-006 | P1 | 盘中调仓文档同步 | Done | 保持 `finally-design.md` 任务说明与代码一致。 |
| R-007 | P1 | API/Celery/Docker 文档同步 | Mostly Done | resolve API 补齐后重新核查 OpenAPI 与文档。 |
| R-008 | P2 | 因子分析展示边界 | Done | 保持 MVP/M7+ 边界清晰。 |
| R-009 | P2 | 生产部署安全文档 | Done | 保持 local/prod 配置边界清晰。 |
| R-010 | P1 | `sync_kline` 测试隔离 | Pending | 目标测试命令全绿。 |
