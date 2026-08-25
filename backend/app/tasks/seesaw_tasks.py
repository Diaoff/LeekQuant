"""跷跷板效应 Celery 任务：每日检测大盘状态并生成推荐。"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.asyncio_runtime import run_async
from app.data.repository.seesaw import get_defensive_rules, insert_market_signal
from app.data.seesaw import (
    detect_market_state,
    get_seesaw_recommendations,
    record_seesaw_trigger,
)
from app.sim.seesaw_switch import apply_seesaw_transition
from app.db.session import async_session_factory
from app.tasks.beat_lock import with_beat_lock
from app.tasks.celery_app import celery_app
from app.tasks.tracking import _run_tracked, with_session

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.seesaw_tasks.check_market_state",
    bind=True,
    max_retries=2,
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
@with_beat_lock("app.tasks.seesaw_tasks.check_market_state")
def check_market_state(self) -> dict[str, Any]:
    """每日盘后（16:05）及盘后二次确认（14:30）检测大盘状态。

    状态变化时记录 market_signal_log。
    转入 down 时生成跷跷板推荐并记录 seesaw_trigger_log。
    """
    async def run(session_factory) -> dict[str, Any]:
        async with session_factory() as session:
            rules = await get_defensive_rules(session)
            if not rules.enabled:
                return {"status": "skipped", "reason": "rules_disabled"}

            # 检测状态
            new_state, detail = await detect_market_state(session, rules)

            # 写入信号日志
            from app.data.seesaw import MarketSignalRecord
            from app.data.repository.seesaw import get_latest_market_signal
            latest = await get_latest_market_signal(session, rules.index_code)
            latest_state = latest.state if latest else None

            change_pct = detail.get("change_pct")
            signal = MarketSignalRecord(
                index_code=rules.index_code,
                state=new_state,
                close_price=detail.get("close"),
                prev_close=detail.get("prev_close"),
                change_pct=change_pct,
                ma20_gap=detail.get("ma20_gap"),
                ma60_gap=detail.get("ma60_gap"),
                drop_from_high=detail.get("drop_from_high"),
                condition_detail=detail,
            )
            await insert_market_signal(session, signal)

            result: dict[str, Any] = {
                "index_code": rules.index_code,
                "new_state": new_state,
                "prev_state": latest_state,
                "detail": {k: v for k, v in detail.items() if k != "recent_high"},
            }

            # 状态切换检测：首次转入 down
            if new_state == "down" and latest_state not in (None, "down"):
                recs = await get_seesaw_recommendations(session, new_state, rules, limit=20)
                if recs:
                    trigger_id = await record_seesaw_trigger(
                        session, new_state, rules.index_code, recs
                    )
                    result["triggered"] = True
                    result["trigger_id"] = trigger_id
                    result["recommended_count"] = len(recs)
                    result["top_recommended"] = [r.ts_code for r in recs[:5]]
                    logger.info(
                        "Seesaw triggered: state=down, index=%s, recommended=%d stocks",
                        rules.index_code, len(recs),
                    )
                else:
                    result["triggered"] = False
                    result["reason"] = "no_pool_items_with_fcf"
            else:
                result["triggered"] = False

            # 实时闭环：把大盘状态变迁应用到启用跷跷板的模拟盘账户
            # （默认所有账户 seesaw_enabled=false → 仅推荐，不会自动交易）。
            result["seesaw_sim"] = await apply_seesaw_transition(
                session,
                new_state=new_state,
                prev_state=latest_state,
                rules=rules,
                trade_date=date.today(),
            )

            # 显式提交：保证 market_signal_log / seesaw_trigger_log / 切换结果落库
            # （check_market_state 此前未提交，实时历史记录会丢失）。
            await session.commit()
            return result

    return run_async(_run_tracked("check_market_state", self.request.id, {}, run))
