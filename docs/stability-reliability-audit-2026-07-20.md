# Leek Quant 稳定性与可靠性审计报告

- **审计日期**：2026-07-20
- **审计范围**：`backend/` 全部源码（约 3.2 万行）、Alembic 迁移、`docker-compose.yml`、测试套件
- **审计方法**：逐文件阅读核心模块，交叉核查数据流、并发路径、约束实现；只读审计，未运行或修改任何代码
- **当前进度基线**：M0–M6a 完成；M6b（WS 流式）代码与容器已部署但文档标注 Pending；M7（认证/因子引擎/监控）部分待办

---

## 1. 一句话总评

**核心资金路径（模拟交易、T+1、实时 WS 端点、Celery 队列、DB 约束）已达到生产级；最大短板集中在三处：回测规模化（进程风暴）、可观测性（日志/异常/指标近乎空白）、数据层治理（回退顺序不符设计、无熔断/限流、无复权）。**

---

## 2. 维度评分卡

| # | 维度 | 评级 | 关键结论 |
|---|---|---|---|
| 1 | 模拟交易一致性 | ✅ 扎实 | 行锁 + 原子条件更新防重复成交；资产由持仓重算对账；6 表约束完备 |
| 2 | T+1 / A股规则 | ✅ 扎实 | FIFO lot 实现正确，测试最充分 |
| 3 | 实时 WS 端点 | ✅ 扎实 | 背压队列 + 心跳 + 安全关闭 + 指数退避重连（生产级） |
| 4 | Celery 任务队列 | ✅ 扎实 | acks_late + reject_on_worker_lost + 幂等 UPSERT |
| 5 | DB 约束 / 分区 | ✅ 扎实 | 6 表 sim + 按年 RANGE 分区（2020–2030）+ 全 FK/CHECK |
| 6 | 因子引擎 (M5) | ✅ 完成 | 定义/表达式/IC-IR/迁移/测试齐全 |
| 7 | 回测规模化 | 🔴 高危 | 每(股票×日) spawn 子进程 → 规模灾难、不可取消 |
| 8 | 可观测性 | 🔴 高危 | 无结构化日志、无全局异常处理器、无指标导出 |
| 9 | 数据层治理 | ⚠️ 中等 | 三级回退未兑现、无熔断/限流、无复权 |
| 10 | 认证 / M7 监控 | ⚠️ 待办 | 全 API 无鉴权，M7 监控缺失 |

---

## 3. 高优风险（High）

### 3.1 🔴 回测/信号进程风暴（不可扩展、不可取消）
- **位置**：`app/backtest/strategy_runtime.py:194-254`、`app/backtest/adapter.py:364-397`、`app/backtest/tasks.py:366-567`、`app/signals/signal_tasks.py:432-486`
- **问题**：`execute_strategy` 默认 `multiprocessing.get_context("spawn").Process(...)`；`BacktestRunner.run` 对每个 (股票 × 交易日) 调用一次。1000 股 × 250 日 ≈ 25 万次进程创建；任务在进程内跑，无进程池复用。规模化回测必触发 `task_time_limit=1800` 硬杀或耗尽资源。
- **不可取消**：`backtest_results.status` 虽含 `'cancelled'` 枚举（`202605180002_m3_strategy_backtest.py:63`），但无任何代码路径设置它；软超时 `task_soft_time_limit=1500` 未被任务内捕获做优雅清理，实际由硬杀 worker 收场。
- **建议**：引入进程池（或可选 `allow_inline=True` 内联执行）复用 worker；为回测任务增加取消/revoke 端点与状态机；超标量回测建议改为批量向量化或分片任务。

### 3.2 🔴 可观测性近乎空白
- **位置**：`app/core/config.py`（全文件无 `logging` 配置）、`app/main.py`（无全局异常处理器）
- **问题**：
  - 无 `logging.basicConfig/dictConfig` → 全仓 `logger` 仅默认 WARNING 级、默认 stderr 格式器，**生产环境 INFO/DEBUG 全丢**，无结构化（JSON）、无请求 ID、无访问日志。
  - 无 `@app.exception_handler`、无自定义错误中间件（仅 CORS）→ 未捕获异常仅 stderr traceback，无结构化错误响应、无敏感信息过滤。
  - 无指标导出（Prometheus 等）、无分布式追踪。
- **建议**：接入 `dictConfig` 结构化日志 + 请求 ID 中间件；注册全局异常处理器（统一错误响应、脱敏）；暴露 `/metrics`（Prometheus）；将现有 `/health`、`task_runs` 心跳、`alert_events` 纳入监控面板。

### 3.3 🔴 数据层：三级回退未兑现 + 无熔断/限流
- **位置**：`app/data/fetcher.py:31,148-165`、`app/data/providers.py:34-36,111-112,493-529`
- **问题**：
  - 设计承诺 `AData(T1)→Baostock(T2)→AkShare(T3)`，但 `priority_default` 实际为 EastMoney HTTP 优先（priority=1），AData 排第 4（priority=10）。
  - 重试机制失效：`_RETRYABLE = (ReqConnectionError, TimeoutError, OSError)`，但 `_http_json` 把 `URLError` 包成 `DataProviderError`（非 `_RETRYABLE`），网络错误走 `except Exception` 直接跳源，3 次重试循环对网络故障基本不触发。
  - grep 全仓 `circuit|breaker|429|rate_limit` **零匹配**：无熔断、无每源 QPS 上限、无 429 检测、无全局限流（`record_update_failure` 累加的 `failure_count` 仅用于 `/api/health` 展示，不驱动任何跳过逻辑）。
- **建议**：按设计修正 provider 优先级；将网络错误纳入可重试集并加指数退避；实现每源熔断（基于 `failure_count` 短时跳过）+ 全局限流。

---

## 4. 中优风险（Medium）

| # | 风险 | 位置 | 说明 |
|---|---|---|---|
| M1 | 回测无复权 | `adapter.py:508-521`、`providers.py` 均不填 `adj_factor` | 用原始 `close`，分红/送转除权日制造假阴线，信号与 P&L 失真 |
| M2 | 前视偏差（乐观成交） | `adapter.py:583-584` | 看到当日 `close` 后假设以当日最低价买入，半日~1 日前瞻 |
| M3 | 重试因包壳失效 | `fetcher.py:31,162`、`providers.py:111-112` | 见 3.3 |
| M4 | 实时缺重放 | `bus.py:113-117`、`api/realtime.py:264` | `realtime_ws` 生产者宕机窗口 tick 永久丢失（订阅默认不带 `replay_from`） |
| M5 | 告警噪声 | `service.py:101-136` | 所有 provider 不填 `adj_factor`，几乎每行非停牌 K 线都触发 `missing_adj_factor` 告警刷屏 |
| M6 | 涨跌停订单滞留 | `service.py:1094-1115` | 涨停买/跌停卖仅写 reason，状态仍"待成交"无终态，无自动转拒绝/过期 |
| M7 | T+1 解锁依赖每日 beat | `service.py:1486-1523`、`celery_app.py:85-88` | beat 未跑/失败则当日买入永不解锁 |
| M8 | 无年度分区维护任务 | `202607200001_add_daily_kline_partitions.py` | 2031+ 新数据落 `DEFAULT` 分区，无界增长、丧失按年剪枝 |
| M9 | `delete_unsupported_stock_data` 级联过宽 | `repository.py:69-133` | 对 9 张表 DELETE，规则变更可能误删 sim/factor 真实数据 |
| M10 | 代理环境变量线程不安全 | `fetcher.py:104-120` | `concurrency>1` 下多线程交错改写进程级 `os.environ`，可能用错代理 |
| M11 | 加仓止损基准重置 | `adapter.py:631-634` | 金字塔加仓把移动止损基准重置为最新一笔买入价，偏乐观 |
| M12 | `sim_cash_flow.balance_after` 非独立复式账 | `service.py:992-1004` | 为账户现金快照副本，流水不具独立纠偏能力 |
| M13 | beat 锁 Redis 不可用时 fail-open | `beat_lock.py:67-75` | HA 下多 beat 可能重复跑（数据多为 UPSERT 幂等，影响有限） |
| M14 | 实时 WS 每 300s 粗暴断流 | `ws_producer.py:118-119` | `task.cancel()` 重建流制造短暂断连间隙 |
| M15 | 风控止损为轮询粒度 | `risk_guard.py:599-620` | 默认 `snapshot` 模式每 15s 用 HTTP 快照，非逐 tick |

---

## 5. 设计 vs 实现差异

| 设计承诺 | 实际状态 | 严重度 |
|---|---|---|
| 三级回退 AData→Baostock→AkShare | 实际 EastMoney HTTP 优先，AData 第 4 | 中 |
| 熔断 / 限流 | 完全未实现 | 中 |
| 复权价格（前/后复权） | `adj_factor` 永不填充，回测用原始价 | 中-高 |
| M6b WS 流式（标注 Pending） | 代码 + docker 服务已部署；risk_guard 默认 `snapshot`(HTTP) 模式 | 文档过时 |
| M7 认证 | `users` 表硬编码 `local` 用户，所有 API 路由无 `Depends(get_current_user)` | 高（待办） |
| M7 监控/指标 | 仅 health + task_runs + alert_events；无结构化日志/指标 | 高（待办） |
| 因子引擎 (M5) | 已完整实现 | 已完成 |
| 回测规模化 | 设计"Python-native"，但每(股票,日) spawn 进程 | 高 |

---

## 6. 测试覆盖分析

- **已有**（约 40 个测试文件）：T+1（6 类场景，`test_backtest_t1_lot.py` 等）、回退顺序、幂等 UPSERT、实时并发/竞态、回测成本/信号/适配器、sim API、因子、迁移。
- **缺口**（与本次风险对应）：
  - 无 WS 重连测试（grep `reconnect` 零命中）
  - 无分区剪枝/存在性测试（grep `partition` 零命中）
  - 无前视偏差测试、无 adj_factor/复权测试
  - 无 process-spawning 规模测试（最严重风险因此未被发现）
  - 无代理环境变量并发测试、无熔断测试（N/A，因不存在）

---

## 7. 已扎实的部分（正向）

- **并发安全**：sim 交易用 `FOR UPDATE` 行锁 + 原子条件更新（`WHERE available_cash >= :frozen`），`rowcount==0` 即抛并发占用；`match_order` 防重复成交。
- **资产对账**：`refresh_account_assets` 以 `total_asset = available_cash + frozen_cash + SUM(market_value)` 为唯一真相源；NAV 先刷市值再重算。
- **WS 端点韧性**：背压队列、心跳、安全关闭、`_safe_send_json` 吞异常、`stream()` 指数退避 + 3 备用 URL 轮询 + 断线重订阅。
- **Celery 健壮性**：`task_acks_late + reject_on_worker_lost`、`max_retries=3 + retry_backoff`、`worker_max_tasks_per_child=50`、`worker_prefetch_multiplier=1`、4 语义队列；分布式 beat 锁用 Lua 安全释放。
- **策略沙箱**：`resource.RLIMIT_CPU/AS` 限制 + `SAFE_BUILTINS` 白名单 + 超时 `terminate()+kill()`。
- **DB 约束**：M4/M3/M2 迁移含全 FK + CHECK + 必要 UNIQUE/索引；`daily_kline` 按年 RANGE 分区（2020–2030）。

---

## 8. 优先修复路线图

| 优先级 | 项 | 对应风险 |
|---|---|---|
| P0 | 回测改用进程池/可选内联执行 + 增加取消端点 | 3.1 |
| P0 | 接入结构化日志 + 全局异常处理器 + `/metrics` 指标 | 3.2 |
| P1 | 修正 provider 优先级 + 网络错误重试 + 熔断/限流 | 3.3, M3, M5 |
| P1 | 实现前/后复权（填充 `adj_factor`） | M1, M2 |
| P2 | 补关键回归测试：WS 重连、分区剪枝、前视偏差、规模 | 第 6 节 |
| P2 | 涨跌停订单终态 + T+1 解锁容错 + 分区维护任务 | M6, M7, M8 |
| P3 | 收紧 `delete_unsupported_stock_data` 级联范围；代理环境变量线程安全 | M9, M10 |
| 文档 | 更新 AGENTS.md：M6b 实际已完成，M7 监控/认证仍为待办 | 第 5 节 |

---

*本报告为只读审计结论，具体修复方案可另行立项。*
