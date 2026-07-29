"""One-off: 重新投递卡在 pending 的 backtest_results 行。

背景：回测任务曾使用 asyncio.run，导致引擎绑定的事件循环与 worker 持久循环不一致，
抛 "different loop" 后任务被判成功、DB 行停留在 pending。部署 run_async 修复后，用本脚本重投。

用法：
    python backend/scripts/requeue_pending_backtests.py            # 实际重投
    python backend/scripts/requeue_pending_backtests.py --dry-run  # 仅列出，不重投
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.backtest.tasks import run_backtest_task  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402


async def fetch_pending() -> list[int]:
    async with async_session_factory() as session:
        result = await session.execute(
            text("SELECT id FROM backtest_results WHERE status = 'pending' ORDER BY id")
        )
        return [int(r["id"]) for r in result.mappings().all()]


async def main() -> int:
    parser = argparse.ArgumentParser(description="重新投递卡住的 pending 回测。")
    parser.add_argument("--dry-run", action="store_true", help="只列出，不重投。")
    args = parser.parse_args()

    ids = await fetch_pending()
    print(f"found {len(ids)} pending backtest(s): {ids}")
    if args.dry_run:
        return 0

    for bid in ids:
        task_id = uuid4().hex
        async with async_session_factory() as session:
            await session.execute(
                text(
                    "UPDATE backtest_results "
                    "SET task_id = :task_id, status = 'pending', "
                    "    error_message = NULL, finished_at = NULL "
                    "WHERE id = :id"
                ),
                {"task_id": task_id, "id": bid},
            )
            await session.commit()
        run_backtest_task.apply_async(kwargs={"backtest_id": bid}, task_id=task_id)
        print(f"re-enqueued backtest {bid} as task {task_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
