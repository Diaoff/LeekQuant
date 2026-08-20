# finally-design.md 剩余差异修复与技术债整改文档

> ⚠️ **状态更新（2026-08）**：M5 多因子选股功能已整体移除代码，本文档中 R-008「因子分析 MVP」等涉及因子的条目现已失效，仅作历史记录。其余整改项状态以代码现状为准。

> 版本：v0.2  
> 日期：2026-06-02  
> 适用范围：`docs/finally-design.md` 与当前 Leek Quant 代码实现之间仍未完全闭环的差异、缺失项和技术债。  
> 说明：本版移除已修复问题的展开描述，仅保留剩余整改项和已完成归档，避免重复立项。

---

## 1. 背景与当前结论

`finally-design.md` 是 Leek Quant 的目标架构与阶段性设计文档。当前代码已实现 M0-M6a 的大部分能力，包括 PostgreSQL 统一存储、基础数据、策略与回测、五档信号、模拟交易、Redis Pub/Sub 实时通道、逐股票 K 线缺口检测和盘中持仓调仓。（注：文中提到的「因子 MVP」对应 M5 多因子选股，该功能后续已废弃并移除代码。）

本文件用于跟踪剩余整改项。已完成事项不再作为待办展开，仅在“已完成归档”中保留证据路径。

当前整改清单中的 R-001 到 R-010 均已完成。后续新发现的差异应新增编号，不再复用本轮已归档事项。

整改原则保持不变：

- 纯 A 股、本地优先、隐私优先。
- PostgreSQL 作为统一持久化存储，Redis 作为任务、缓存和实时广播组件。
- 金融金额、价格和费率使用 `Decimal` / PostgreSQL `NUMERIC`。
- 所有交易日判断查询 `trade_calendar`，不得硬编码节假日。
- 优先修复安全、数据完整性、资金/交易语义一致性问题。

---

## 2. 剩余问题分级

当前无剩余待整改项。

---

## 3. 待整改项

当前无待整改项。

---

## 4. 已完成归档

| 编号 | 原优先级 | 事项 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| R-001 | P0 | 策略执行安全沙箱 | Done | `SAFE_BUILTINS` 已显式注入策略 sandbox；信号任务不再使用 inline 执行；`test_strategy_runtime.py` 覆盖 dangerous builtin / import / 文件读取拒绝。 |
| R-002 | P0 | 移除 Hikyuu 适配器并统一回测引擎 | Done | 回测结果写入 `performance.engine = "python_native"`；`test_backtest_engine_selection.py` 覆盖。 |
| R-003 | P0 | 逐股票 K 线缺口检测与修复 | Done | `infer_incremental_kline_ranges()`、`incremental_kline_update()` 已存在；`test_data_service.py` 和 `test_tasks.py` 覆盖 gap 场景。 |
| R-004 | P1 | `alert_events` 告警闭环 | Done | 已实现 `GET /api/system/alerts` 和 `POST /api/system/alerts/{alert_id}/resolve`；`test_api_data.py` 覆盖过滤查询、resolve 成功和 404。 |
| R-005 | P1 | 涨跌停语义统一 | Done | 回测不再在规则层阻断涨停买入/跌停卖出；撮合阶段记录 `match_status = BLOCKED` 和原因；`test_adapter.py`、`test_signals.py`、`test_sim_trading.py` 覆盖。 |
| R-006 | P1 | 盘中持仓调仓文档同步 | Done | `finally-design.md` 已记录 `generate_intraday_position_signals` 任务、交易窗口和不默认 beat 调度策略。 |
| R-007 | P1 | API/Celery/Docker 文档同步 | Done | `finally-design.md` 已同步当前任务路径、alerts 查询/resolve 路径、local compose、celery command 和 Redis/生产安全说明。 |
| R-008 | P2 | 因子分析 MVP 与完整能力边界 | Done | `finally-design.md` 已区分因子 MVP 与 M7+ 表达式/图表增强能力。 |
| R-009 | P2 | 生产安全配置说明 | Done | `finally-design.md` 已说明 Redis 密码、强 `SECRET_KEY`、受控 CORS、反向代理 TLS 等生产要求。 |
| R-010 | P1 | `sync_kline` 测试隔离 | Done | `sync_kline()` 支持注入 `session_factory`；`commit_each=True` 单测使用 fake session factory，不再连接真实 PostgreSQL；`test_data_service.py`、`test_tasks.py` 覆盖。 |

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

R-004 专项核查结果：

```text
28 passed
```

命令：

```bash
python3 -m pytest backend/tests/test_api_data.py -q
```

R-005 专项核查结果：

```text
145 passed
```

命令：

```bash
python3 -m pytest backend/tests/test_adapter.py backend/tests/test_signals.py backend/tests/test_sim_trading.py -q
```

R-010 专项核查结果：

```text
30 passed
```

命令：

```bash
python3 -m pytest backend/tests/test_data_service.py backend/tests/test_tasks.py -q
```

全量目标命令建议在合并前重新运行一次。

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
| R-004 | P1 | `alert_events` 告警闭环 | Done | 保持 `test_api_data.py` 通过。 |
| R-005 | P1 | 涨跌停语义统一 | Done | 保持 `test_adapter.py`、`test_signals.py`、`test_sim_trading.py` 通过。 |
| R-006 | P1 | 盘中调仓文档同步 | Done | 保持 `finally-design.md` 任务说明与代码一致。 |
| R-007 | P1 | API/Celery/Docker 文档同步 | Done | 保持 API 路径与文档一致。 |
| R-008 | P2 | 因子分析展示边界 | Done | 保持 MVP/M7+ 边界清晰。 |
| R-009 | P2 | 生产部署安全文档 | Done | 保持 local/prod 配置边界清晰。 |
| R-010 | P1 | `sync_kline` 测试隔离 | Done | 保持 `test_data_service.py` 和 `test_tasks.py` 通过。 |
