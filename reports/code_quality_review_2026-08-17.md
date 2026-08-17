# Leek Quant — 代码质量与性能评估报告

> 评估日期：2026-08-17
> 评估范围：后端 `backend/app`（约 17.8k 行 Python，80 个模块）、前端 `frontend/src`（约 8.4k 行 TS/TSX）、测试（`backend/tests` 48 文件 / 636 测试函数 / 18.7k 行；`frontend/tests` 5 个 e2e smoke）、Alembic 迁移（21 个）。

---

## 0. 总体结论

项目整体处于**中上水平**：分层清晰、异步 SQLAlchemy 配置专业、可观测性中间件到位、Celery 任务已做过并发/超时/断路器的工程化改造，测试广度在同类个人量化项目中属于较好水平。

但存在 **3 个高严重度问题**（认证缺失、回测缓存失效缺失、核心指标库无测试）会直接威胁**数据安全、结果正确性、可维护性**，应优先处理。其余多为中低优先度的结构性债务与性能打磨。

一句话概括：**“骨架与关键路径的工程素养不错，但安全边界、缓存一致性、底层依赖测试这三块有明显的‘没做完’痕迹。”**

---

## 1. 代码质量（Code Quality）

### 1.1 已做到的亮点（值得保留）
- **无裸 `except:`、无 `print()`、无 TODO/FIXME 残留**——基础纪律良好。
- 普遍使用 `structlog` 结构化日志、`from __future__ import annotations`、`@dataclass(slots=True)`、`Decimal` 精确计算（A 股金额/比例用 Decimal，正确）。
- 核心模块（sim/service、backtest/adapter、kline_cache）有完整 docstring 与中文注释，关键算法（如缓存 v1→v2 迁移、idle 连接失效）有根因说明。

### 1.2 问题与改进（按严重度）

| 严重度 | 问题 | 位置 / 证据 | 改进建议 |
|---|---|---|---|
| 中 | **巨型模块（God Module）** 单文件过大，职责过多，难以单测与维护 | `sim/service.py` 1676 行、`backtest/adapter.py` 1538 行、`data/repository.py` 1046 行、`data/service.py` 1016 行、`data/stock_service.py` 947 行 | 按职责拆分：sim 拆为 `orders / positions / nav / account`；repository 按实体拆子模块；data/service 拆出 `validation` 与 `quality` 独立包 |
| 中 | **重复辅助函数** 同一小工具在多处各自实现 | `_as_decimal` 在 data/service 与 sim/service 各一份；序列化逻辑分散（`sim.serialize_rows` vs `api/backtests._serialize_kline_rows` vs `api/signals` 内联） | 抽出 `app/core/convert.py` 与 `app/core/serialize.py` 统一收口 |
| 低 | **50+ 处宽泛 `except Exception` 多未记录日志/吞错** 削弱可观测性，故障静默 | 如 `data/service.py:523/536/570/574`、`data/providers.py:334/420/698/704`、`realtime/risk_guard.py` 多处 | 至少 `logger.warning("...", exc_info=True)`；区分“预期可恢复”与“需告警”的异常，避免一律吞掉 |
| 低 | 部分核心算法（如信号→动作状态机、回测 fill 价格推断）缺高层设计注释 | `backtest/adapter.py`、`backtest/signals.py` | 补充模块级“设计意图 + 边界条件”说明，方便后续维护者 |

---

## 2. 架构设计（Architecture）

### 2.1 已做到的亮点
- 清晰分层：`api / backtest / data / realtime / sim / tasks / core / db / libs`，依赖方向基本单向。
- **数据库层专业**：`db/session.py` 用 `AsyncAdaptedQueuePool` + `pool_pre_ping` + `pool_recycle=1800` + `statement_timeout=60s` + `idle_in_transaction_session_timeout=30s`，配置到位。
- **可观测性中间件** `core/middleware.py`：RequestID 注入 + Prometheus 指标，且对路径做了模板折叠（避免 label 基数爆炸）——这是教科书级做法。
- Celery 增量 K 线更新已重构为“纯编排父任务 + 每批独立子任务”，根治了全局 25/30 分钟 `SoftTimeLimitExceeded`（见工作记忆）。

### 2.2 问题与改进（按严重度）

| 严重度 | 问题 | 位置 / 证据 | 改进建议 |
|---|---|---|---|
| **高** | **无认证 / 授权**：仅依赖 `X-User-ID` 请求头，缺省 `user_id=1`，任何人都可读取/操作他人账户、策略、模拟盘 | `api/backtests.py:_extract_user_id`（缺省返回 1）、各 API 直接信任该值；AGENTS.md M7“认证”标记为待处理 | 实现真实鉴权（登录态/JWT 或网关鉴权），将 `user_id` 作为强制可信上下文注入，禁止客户端自由指定；所有按 user 的查询强制带 `user_id` 过滤（目前多数已带，但入口不可信） |
| **高** | **回测 K 线缓存失效从未触发**：`invalidate_cache` 已定义，但全代码库（除 venv）无任何调用点 → 数据刷新后最多 1 小时返回**陈旧原始 K 线** | `backtest/kline_cache.py:137`（定义）、`backtest/tasks.py`（仅 get/set） | 在数据增量/全量同步完成、或 adj_factor 变更后，调用 `invalidate_cache`；或改为“按数据版本号”做 key 一部分 |
| 中 | **大量原始 SQL（`text()`）**：sim 49 处、repository 32、stock_service 24、api/backtests 19；失去 ORM 类型安全、与迁移解耦困难、难以单测 | 见各文件 `text(...)` 计数 | 高频复用查询迁到 SQLAlchemy Core/ORM；SQL 集中到 `data/sql/` 资源文件；统一参数化约定（目前 `where_sql` 拼接是安全的，但可读性差） |
| 中 | **职责耦合**：API 层混入编排（celery `inspect` 健康检查、user_id 提取）与业务序列化；`data/service` 同时承担编排+校验+质量检查 | `api/backtests.py:29-50`、`data/service.py` | 抽出 `auth` 依赖（FastAPI `Depends`）、`worker_health` 服务层；service 按“编排/校验/质量”分层 |
| 中 | **模块边界与文档脱节**：AGENTS.md 称 M5“多因子打分、IC/IR 分析 ✅ 完成”，但代码中**不存在 `app/factor` 包，也未发现 IC/IR 评分实现** | `find backend/app -name '*.py'` 无 factor；`grep` IC/IR/多因子无结果（命中均为 adj_factor 列名或 celery） | 核实 M5 真实落地情况；若未实现应修正里程碑文档，避免误导；若已并入他处需补测试 |
| 低 | **每次请求同步调用 `celery inspect`**：提交/查询回测时都做一次 `control.inspect(timeout=1.0)` 网络往返 | `api/backtests.py:_backtest_worker_available` | 结果加短时缓存（5–10s TTL），或用心跳/注册表替代实时 inspect |

---

## 3. 性能分析（Performance）

### 3.1 已做到的亮点
- **回测 K 线缓存** `kline_cache.py`：缓存“原始行 dict”（规避 slots dataclass pickle 损坏）、`v2` 前缀版本化、1h TTL、非致命降级（Redis 不可用仅 cache miss），设计扎实。
- **增量同步并发**：`sync_kline` 在 `commit_each and concurrency>1` 时用 `asyncio.Semaphore` + `asyncio.gather` 并发；每股票 `wait_for` 超时；电路断路器运行中 recheck（每 50 只重筛）。
- DB 层超时/回收/探活配置到位（见 2.1）。

### 3.2 问题与改进（按严重度）

| 严重度 | 问题 | 位置 / 证据 | 改进建议 |
|---|---|---|---|
| 中 | **Redis 客户端每次调用新建**：`get/set/invalidate` 每次都 `redis.asyncio.from_url(...)` + `aclose()`，无连接池复用 → 连接抖动、TLS/握手开销 | `backtest/kline_cache.py:82-92/118-129/141-151` | 模块级维护一个共享 `redis.asyncio.Redis`（连接池），所有操作复用；用 `async with` 或 `client` 单例 |
| 中 | **`commit_each=False` 默认路径持单 session 串行 + 长事务/idle 风险**：代码注释已自述“4000+ 股票十几分钟，主 session TCP 被服务端 idle 关闭” | `data/service.py:622-627`（默认串行）、`:641-684`（idle 注释） | 大数据量同步默认走 `commit_each=True`（已为该路径做 session 生命周期对齐）；或对默认路径分批提交、周期性 `await session.execute(text("SELECT 1"))` 保活 |
| 低 | **默认同步路径串行**：`concurrency` 逻辑仅在 `commit_each` 启用，全市场 5000+ 股票若走默认会非常慢 | `data/service.py:612-627` | 明确并发为默认策略，串行仅作降级；用 `TaskGroup` 替代 `gather` 以获得更好取消语义 |
| 低 | **前端无路由级代码分割**：`grep "lazy("` 全项目无结果，大页面（BacktestPage 1509、SimulationPage 951、MarketPage 858 行）全部打进初始包 | `frontend/src/pages/*` | 引入 `React.lazy` + `Suspense` 做路由懒加载；Vite `manualChunks` 拆分 vendor（React/Monaco/图表） |
| 低 | **API 健康检查阻塞请求路径**：每次回测提交都同步 `inspect(timeout=1.0)` | 见 2.2 低 | 缓存健康结果 |

---

## 4. 测试覆盖（Test Coverage）

### 4.1 已做到的亮点
- **后端广度好**：636 个测试函数 / 48 文件 / 18.7k 行测试代码，针对迁移、回测引擎、信号、实时行情、Celery 循环守卫、task-run 对账有大量专项测试，且很多带 `pragma: no cover` 显式标注不可达分支——测试素养较高。
- `conftest.py` 提供 fixtures，覆盖 `backtest / api / tasks / realtime / data / sim / core / main / db`。

### 4.2 问题与改进（按严重度）

| 严重度 | 问题 | 证据 | 改进建议 |
|---|---|---|---|
| **高** | **安全相关路径零覆盖**：因为认证尚未实现，故无鉴权/越权测试 | `api/backtests._extract_user_id` 缺省 1，无对应测试 | 实现认证后补“越权访问他人资源应 403/404”用例；用 property 测试验证所有 user 作用域查询都带 `user_id` |
| 中 | **核心指标库 `libs/MyTT.py`（287 行，信号计算根基）无专门测试** | `backend/tests/` 无 mytt/indicator 测试文件 | 针对 MA/EMA/MACD/KDJ/RSI 等已知公式补数值断言测试；这是“静默影响信号正确性”的高风险区 |
| 中 | **基础设施无单测**：`core/`（config/middleware/asyncio_runtime/logging）、`db/session` 仅间接覆盖 | `from app.core` 9 处引用但无 `test_core_*` | 补 config 校验、middleware 指标 cardinality、asyncio_runtime 桥接的单元测试 |
| 中 | **`app/factor` 缺失**：M5 功能既无实现也无可测对象 | 见 2.2 | 落地后必须配套 IC/IR、因子打分、排名的测试 |
| 低 | **`api/metrics.py`、`api/system.py`（健康检查/Prometheus）无专门测试** | 无 `test_metrics`/`test_system` | 补健康检查与指标暴露的冒烟测试 |
| 低 | **前端覆盖极薄**：仅 5 个 Playwright smoke e2e，无组件/单元/hook 测试，却支撑 8.4k 行前端 | `frontend/tests/smoke/*`（5 个） | 引入 Vitest 对 `lib/`（如 mytt-completions、数据格式化）、hooks、关键页面逻辑做单元测试 |

---

## 5. 优先级总排序（从高到低）

**🔴 高（影响安全 / 数据正确性 / 可信度，应立即处理）**
1. 认证与授权缺失（多租户隔离形同虚设）
2. 回测 K 线缓存失效未触发（刷新后最多 1h 返回陈旧数据）
3. 核心指标库 `MyTT` 无测试（信号计算错误静默）

**🟠 中（结构性债务 / 性能打磨）**
4. 巨型模块拆分（sim/adapter/repository/service）
5. 原始 SQL 集中化 / 逐步迁 Core-ORM
6. Redis 客户端连接池复用
7. 大数据量同步的 idle 连接 / 长事务风险（默认 `commit_each=True` 或分批保活）
8. 前端路由代码分割 + vendor 拆分
9. 模块边界与 AGENTS.md 里程碑文档一致性（factor 模块）

**🟢 低（ hygiene / 可维护性）**
10. 宽泛 `except` 日志化，避免静默吞错
11. celery `inspect` 健康检查结果缓存
12. 重复辅助函数收口到 `core/convert`、`core/serialize`
13. 前端单元测试补强（Vitest）
14. API metrics/system 端点补测试

---

## 6. 落地建议（分阶段）

- **本周（高优先）**：补认证依赖 + 越权测试；在 K 线同步完成钩子里调用 `invalidate_cache`；为 `MyTT` 核心指标补数值测试。
- **本月（中优先）**：拆分 `sim/service` 与 `data/repository`；`kline_cache` 改为共享连接池；前端加 `React.lazy` + `manualChunks`；修正里程碑文档与 factor 模块现状。
- **持续（低优先）**：异常日志化、SQL 集中化、重复函数收口、前端 Vitest 单测。

---

*注：本报告基于静态代码走查与 `grep`/`wc` 量化统计，未运行动态压测。性能结论中的“潜在瓶颈”建议以 `pg_stat_statements` + Prometheus 实际指标二次确认。*
