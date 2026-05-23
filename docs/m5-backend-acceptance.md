# M5 验收记录

日期：2026-05-24

## 验收边界

本轮验收 M5 多因子选股的后端、任务链路、真实 PostgreSQL/Alembic 集成和前端 `/factor` smoke。不包含股票池导入、实时行情或 WebSocket。

验收标准：

- `factor_definitions`、`factor_values`、`scoring_rank`、`factor_analysis` 四表由 Alembic 迁移创建。
- 8 个内置因子可幂等 seed 到 `factor_definitions`。
- 单日因子计算在一个事务内完成 seed、`factor_values`、`scoring_rank` 写入。
- `/api/factors/rank?page_size=N` 作为 Top N 查询方式。
- 排行榜支持 `scope_type=all` 与 `scope_type=watchlist_group&scope_value=<group_name>`。
- `watchlist_group` 缺少 `scope_value` 时，API 返回 422，任务入口拒绝入队，前端阻止提交。
- IC/IR 样例可写入 `factor_analysis`，重复运行走 upsert。
- `POST /api/tasks/factors/compute` 与 `POST /api/tasks/factors/analyze` 创建 `task_runs` 并调用 Celery `apply_async`。
- 直接调用 Celery task function 可走 `_run_tracked`，并把真实 `task_runs` 从 `pending` 更新到 `success/result`。
- `/factor` 页面可渲染、发起初始 API 请求，并在 Top N 修改后刷新排行榜请求。

## 验收命令

M5 后端专项：

```bash
./.venv/bin/python -m pytest backend/tests/test_m5_factors.py backend/tests/test_factor_api.py backend/tests/test_factor_tasks.py backend/tests/test_m5_factor_migration.py
```

M5 真实集成：

```bash
./.venv/bin/python -m pytest backend/tests/test_m5_integration.py
```

前端：

```bash
cd frontend && npm run typecheck
cd frontend && npm run build
cd frontend && npm run test:smoke
```

编译检查：

```bash
./.venv/bin/python -m py_compile backend/app/factor/definitions.py backend/app/factor/service.py backend/app/api/factors.py backend/app/api/tasks.py backend/app/tasks/factor_tasks.py backend/alembic/versions/202605220003_m5_factors.py backend/tests/test_m5_integration.py
```

## 真实集成数据形状

`backend/tests/test_m5_integration.py` 使用真实 PostgreSQL 和 Redis，默认不 skip。夹具会：

- 执行 `alembic upgrade head`。
- 插入 3 只隔离样本股票、交易日历、76 日 K 线、基本面和一个自选分组。
- 直接调用因子计算，断言 `factor_values` 和 `scoring_rank` 写入。
- 调用 IC/IR 分析，断言 `factor_analysis` 写入。
- 通过 FastAPI `TestClient` 查询 `/api/factors` 与 `/api/factors/rank`。
- 创建 pending `task_runs` 后直接调用 Celery task function，验证 `_run_tracked` 更新为 `success` 并写入 result。

## 验证结果

2026-05-24 本轮执行结果：

- M5 后端专项：19 passed。
- M5 真实集成：2 passed，9 个 MyTT RSI 运行时 warning。
- 编译检查：passed。
- 前端 typecheck：passed。
- 前端 build：passed，保留 Vite/Monaco 大 chunk warning。
- 前端 smoke：1 passed。

非 M5 回归探针：

- `backend/tests/test_hikyuu_adapter.py backend/tests/test_trade_details.py`：38 passed, 2 failed。
- `backend/tests/test_hikyuu_adapter.py` 本轮通过。
- `backend/tests/test_trade_details.py::TestMultiTradeSequenceTracking::test_four_trade_sequence_tracking` 仍失败，未产生卖出记录。
- `backend/tests/test_trade_details.py::TestTradeRecordIntegration::test_complete_lifecycle_buy_hold_sell` 仍失败，未产生卖出记录。

## 已知非 M5 风险

上述 `trade_details` 失败位于回测交易明细路径，不涉及 M5 因子迁移、计算、排行榜、IC/IR、API、任务闭环或前端 `/factor` smoke，本轮记录为非 M5 阻塞项。
