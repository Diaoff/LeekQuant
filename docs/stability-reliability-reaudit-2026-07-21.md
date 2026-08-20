> ⚠️ **后续状态（2026-08）**：本复检报告基于 2026-07-21 的代码，当时仍引用 `app/factor/expression.py` 等 M5 模块（见复检范围）。此后 **M5 多因子选股功能已整体移除代码**，故文中涉及因子引擎 / M5 的「完成」「就绪」结论仅反映当时状态，当前已不成立。其余稳定性结论（回测规模化、可观测性、数据层治理等）仍有效。

# Leek Quant 稳定性与可靠性复检报告

- **复检日期**：2026-07-21（基于 2026-07-20 首轮审计后的修复代码）
- **复检范围**：`backend/` 当前代码 + 新增模块（`core/logging.py`、`core/middleware.py`、`data/circuit_breaker.py`、`api/metrics.py`、`factor/expression.py`、`realtime/eastmoney_ws.py`、`realtime/ws_producer.py`、`tasks/beat_lock.py`）+ 新增测试
- **方法**：逐条验证首轮 18 项风险 + 新增模块核查；只读审计，未修改代码。关键结论已直接核对源码（`logging.py`、`circuit_breaker.py`、`strategy_runtime.py`、`fetcher.py`）确认。
- **对比基准**：`docs/stability-reliability-audit-2026-07-20.md`

---

## 1. 一句话总评

**首轮 3 项 HIGH 风险中 2 项已彻底解决（回测规模化、可观测性），第 3 项（数据层）优先级与重试已修复但熔断器在主路径失效（部分解决）。12 项中风险 9 项已修复，3 项遗留。修复同时暴露 1 个新高危：全局无鉴权（M7）。整体稳定性/可靠性由"核心可用、短板明显"提升至"生产级基本就绪，鉴权与熔断器实效为最后两个硬伤"。**

---

## 2. 修复情况总览（首轮风险 → 现状）

| 首轮风险 | 严重度 | 现状 | 证据 |
|---|---|---|---|
| HIGH-1 回测每(股票×日)spawn 子进程 | 高 | ✅ FIXED | `config.py` `strategy_default_inline=True`；`strategy_runtime.py:208-214` 默认内联执行；`adapter.py` 单次向量化 `BacktestRunner.run()`；`backtests.py` 新增取消接口（revoke + `status='cancelled'`） |
| HIGH-2 零可观测性 | 高 | ✅ FIXED | `logging.py` structlog 结构化日志（JSON/Console）；`middleware.py` 请求ID+指标中间件；`main.py` 全局异常处理器返回带 `request_id` 的 500；`api/metrics.py` `GET /metrics`（Prometheus） |
| HIGH-3 数据层回退/重试/熔断 | 高 | 🟡 PARTIAL | 优先级已改 `AData(1)→Baostock(2)→AkShare(3)→EastMoney(10)→Tencent(20)`；`_RETRYABLE` 含 `URLError`（`fetcher.py:36`）；但熔断在主路径失效（见 NEW-1） |
| M1 回测无复权 | 中 | ✅ FIXED | 四源均取前复权（AData `adj=True`/Baostock `adjustflag="2"`/AkShare `adjust="qfq"`/EastMoney `fqt="1"`）；`test_backtest_adjust.py` 已断言 |
| M2 前视偏差 | 中 | ✅ FIXED | `adapter.py:320` 窗口剔除当日 bar；`adapter.py:332` 成交价取次日开盘；`config.py` `backtest_fill_price_mode="next_open"`；`test_backtest_lookahead.py` 已断言 |
| M3 重试被包壳失效 | 中 | ✅ FIXED | `fetcher.py:36,203-218` 指数退避重试（含 `URLError`） |
| M4 实时无重放 | 中 | ✅ FIXED | `bus.py` 双写 Redis Stream + `open_subscription(replay_from=...)` 回放；`/ws/realtime?replay_from=` |
| M5 告警噪声 | 中 | 🟡 PARTIAL | `service.py` 聚合为每股票一条告警，但缺 `adj_factor` 仍逐股报警（见 NEW-5） |
| M6 涨跌停单滞留待成交 | 中 | ✅ FIXED | `sim/service.py:1093-1167` BLOCKED 自动转 `rejected` 并解冻资金/股份；`trading_tasks.py` 清理任务 |
| M7 T+1 解锁依赖日 beat | 中 | ✅ FIXED | `trading_tasks.py` `unlock_t1_daily`（`@with_beat_lock` + `max_retries=3` + `autoretry_for`） |
| M8 无年度分区维护 | 中 | ✅ FIXED | `202607200001` 新增 `daily_kline_2028/2029/2030` + `daily_kline_default` |
| M9 级联误删 9 表 | 中 | ❌ UNFIXED | `repository.py:69-133` 范围未收窄（见 NEW-4） |
| M10 代理环境变量线程不安全 | 中 | ❌ UNFIXED | `fetcher.py:140-141` 仍直接 `os.environ[k]=v`（见 NEW-3） |
| M11 加仓重置止损基准 | 中 | ✅ FIXED | 加仓采用加权 `avg_cost`，不再重置基准 |
| M12 流水非独立复式账 | 中 | ✅ FIXED | 改为按账户账面价值记账（轻量修复） |
| M13 beat 锁 Redis 宕 fail-open | 中 | ❌ UNFIXED | `beat_lock.py` 仍 fail-open（见 NEW-6） |
| M14 WS 每 300s 粗暴断流 | 中 | ✅ FIXED | `ws_producer.py` 改为优雅 `task.cancel()` + `close()` + 指数退避重连 |
| M15 风控止损轮询粒度 | 中 | 🟡 PARTIAL | WS 实时路径已实现（`--mode redis`），默认仍 `snapshot` 轮询（30s） |

**统计**：HIGH 3 项 → 2 FIXED / 1 PARTIAL；MEDIUM 15 项 → 9 FIXED / 3 UNFIXED / 3 PARTIAL。

---

## 3. 三项 HIGH 风险处置详情

### HIGH-1 ✅ 已解决 — 回测规模化 + 可取消
- 默认 `strategy_default_inline=True`（`strategy_runtime.py:208-214`），回测不再为每个 (股票×日) 创建子进程；内核改为单次进程内向量化 `BacktestRunner.run()`。
- 新增 `POST /api/backtests/{id}/cancel`：`celery.control.revoke(terminate=True, SIGTERM)` + `UPDATE backtest_results SET status='cancelled'`，可终止且状态可追踪。
- 进程隔离路径（`allow_inline=False`）仍保留，用于不可信策略沙箱。

### HIGH-2 ✅ 已解决 — 可观测性
- `logging.py`：structlog + 标准库桥接，支持 JSON/Console，`log_level`/`log_format` 配置化，自动降噪第三方日志。
- `middleware.py`：请求 ID 注入 + 指标中间件；`main.py` 注册全局 `Exception` 处理器，统一返回带 `request_id` 的结构化 500（脱敏）。
- `api/metrics.py`：`/metrics` 暴露 Prometheus 指标（`metrics_enabled` 开关）。

### HIGH-3 🟡 部分解决 — 数据层
- **已修复**：provider 优先级顺序（AData→Baostock→AkShare 优先）、`_RETRYABLE` 纳入 `URLError` 并加指数退避重试。
- **未解决（NEW-1）**：熔断器在主路径失效。详见第 4 节。

---

## 4. 新增 / 仍遗留风险

| ID | 严重度 | 说明 | 证据 |
|---|---|---|---|
| **NEW-1** | 🔴 高 | 熔断器在主路径失效：`fetcher.py:187` 仅当传入 `data_type` 才建 breaker；`fetcher.py:194` 需同时传 `session`+`data_type` 才启用；`_breaker_sync_check`（`fetcher.py:225-239`）在运行中的事件循环内直接 `return False`（fail-open）。数据刷新为 async 主路径 → 熔断实际从不触发，provider 持续失败时仍打满重试。 | `fetcher.py:187,194,225-239` |
| **NEW-2** | 🔴 高 | 全局无鉴权：grep `get_current_user`/`Depends`/`HTTPBearer`/`Authorization` 全仓零命中，所有 API（回测/模拟交易/实时 WS/策略）完全开放，任意可访问者均可提交回测与推送交易指令。 | `backend/app/api/*` |
| NEW-3 | ⚠️ 中 | 代理 env 线程不安全（即 M10）：`_data_proxy_ctx` 改写全局 `os.environ`，async/多线程下代理可能泄漏到并发请求。 | `fetcher.py:140-141` |
| NEW-4 | ⚠️ 中 | 级联误删（即 M9）：`delete_unsupported_stock_data` 仍级联 9 表，上游 `stock_basic` 异常可触发大批量误删。 | `repository.py:69-133` |
| NEW-5 | ⚠️ 低 | 告警噪声（即 M5）：仍逐股票报 `missing_adj_factor`，规模大时刷屏。 | `data/service.py:107` |
| NEW-6 | ⚠️ 中 | beat 锁 Redis 宕 fail-open：多 beat worker 在 Redis 抖动时并发执行日级任务（T+1 解锁/撮合/NAV）。 | `tasks/beat_lock.py` |

---

## 5. 设计 vs 实现对齐更新

| 设计项 | 状态 | 说明 |
|---|---|---|
| M6b 实时 WS 流式 | ✅ 已完成 | `bus.py` Stream 双写 + `realtime.py` WS 端点 + `ws_producer.py` 优雅重连 + 回放；AGENTS.md 仍标 Pending，建议更新 |
| M5 因子表达式引擎 | ✅ 已完成 | `factor/expression.py` 安全表达式求值（tokenize→AST→numpy，无 `eval`） |
| M7 接口鉴权 | ❌ 未做 | 全局无认证（NEW-2，安全高危） |
| M7 监控/指标 | ✅ 基本完成 | `/metrics` + 结构化日志 + 全局异常处理器 |

---

## 6. 更新后评分卡

| # | 维度 | 首轮 | 复检 |
|---|---|---|---|
| 1 | 模拟交易一致性 | ✅ | ✅ 不变 |
| 2 | T+1 / A股规则 | ✅ | ✅ 不变 |
| 3 | 实时 WS 端点 | ✅ | ✅ 升级（回放+优雅重连） |
| 4 | Celery 任务队列 | ✅ | ✅ 不变 |
| 5 | DB 约束 / 分区 | ✅ | ✅ 升级（分区补至 2030+DEFAULT） |
| 6 | 因子引擎 (M5) | ✅ | ✅ 升级（表达式引擎） |
| 7 | 回测规模化 | 🔴 | ✅ 已解决 |
| 8 | 可观测性 | 🔴 | ✅ 已解决 |
| 9 | 数据层治理 | ⚠️ | ⚠️ 部分（熔断实效 NEW-1） |
| 10 | 认证 / M7 监控 | ⚠️ | 🔴 鉴权缺失（NEW-2） |

---

## 7. 优先处理顺序（建议）

1. **NEW-2 鉴权**：接入 API Key / JWT（`Depends(get_current_user)`），区分读写权限 —— 安全硬伤。
2. **NEW-1 熔断实效**：将熔断判定移入 async 数据服务层（或改用 `asyncio.run_coroutine_threadsafe`），并默认对所有 kline 刷新传入 `session`/`data_type`，使 `failure_count` 真正驱动短跳。
3. **NEW-3 / NEW-4**：代理改用 per-request session 参数（非全局 `os.environ`）；`delete_unsupported_stock_data` 加最小保留阈值 + dry-run 审计。
4. **NEW-6**：beat 锁 Redis 不可用时退化为本地单例锁 + 告警，而非无锁放行。
5. **NEW-5 / M5 残留**：缺失 `adj_factor` 告警改为每数据源每批次一条聚合。
6. **文档**：更新 AGENTS.md —— M6b 实际已完成，M7 监控基本完成、鉴权仍为待办。

---

*本报告为只读复检结论，具体修复方案可另行立项。首轮报告见 `docs/stability-reliability-audit-2026-07-20.md`。*
