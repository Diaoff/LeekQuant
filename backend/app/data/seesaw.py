"""跷跷板效应（高切低）避险库核心逻辑。

当大盘进入弱势（down）状态时，从 defensive_pool 表取出**人工维护**的避险标的，
按 ``sort_order`` 优先级等权配置，供用户切换交易偏好。

设计约定（与用户纠偏一致）：避险库由用户在平台手动维护（入选哪些标的、是否
启用、库内优先级 ``sort_order`` 均来自人工配置），**引擎不计算、也不依赖任何
质量分（β / 股息率 / PE / FCF 等）**。回测 overlay（全池等权）、实时切换
（rank_defensive_pool）与实时推荐（get_seesaw_recommendations）三端共用同一张
``defensive_pool`` 表、同一套人工优先级，保证「推荐 / 执行 / 回测选股」同源。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.libs.MyTT import MA, CROSS, REF, RET
import logging

logger = logging.getLogger(__name__)


# ── 数据模型 ──────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class DefensivePoolItem:
    id: int
    ts_code: str
    name: str
    note: str | None = None
    tags: str | None = None
    sort_order: int = 0
    enabled: bool = True
    created_at: Any = None
    updated_at: Any = None


@dataclass(slots=True)
class DefensiveRules:
    index_code: str = "000300.SH"
    ma_short: int = 5
    ma_long: int = 20
    ma_long2: int = 60
    drop_threshold: Decimal = Decimal("-0.03")   # 单日跌幅触发阈值
    high_window: int = 20                        # 距N日高点
    high_drop_pct: Decimal = Decimal("-0.05")    # 距高点跌幅触发阈值
    vol_expand_thresh: Decimal | None = None     # 成交量放大倍数（可选）
    ma_cross_enabled: bool = True                # 是否启用均线死叉检测
    symmetric_recovery: bool = True              # P2 退出对称化：True=进入/退出镜像（默认），False=旧行为（可复现 #206 基线）
    enabled: bool = True


@dataclass(slots=True)
class MarketSignalRecord:
    id: int | None = None
    index_code: str = "000300.SH"
    state: str = "neutral"                       # up | neutral | down
    trigger_time: Any = None
    close_price: Decimal | None = None
    prev_close: Decimal | None = None
    change_pct: Decimal | None = None
    ma20_gap: Decimal | None = None
    ma60_gap: Decimal | None = None
    drop_from_high: Decimal | None = None
    condition_detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RecommendationItem:
    ts_code: str
    name: str
    score: float
    beta: float | None = None
    dividend_yield: float | None = None
    pe_ttm: float | None = None
    reason: str = ""


# ── 大盘状态检测 ───────────────────────────────────────────────────────────────


def _safe_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def classify_market_state(
    closes: "np.ndarray",
    rules: DefensiveRules,
) -> tuple[str, dict[str, Any]]:
    """纯函数版大盘状态判定（无 DB / 无异步）。

    接收已对齐的收盘价序列 ``closes``（float 数组，按时间升序），返回
    ``(state, condition_detail)``。``detect_market_state`` 从数据库取数后调用本函数，
    回测引擎则在内存 K 线（截至 td-1，防前视）上直接调用本函数，保证判定逻辑唯一。

    状态判定（按优先级）：
    - up：收盘 > MA20 且 > MA60（趋势向上），或任一对称看涨信号成立
    - down：以下任一条件成立
      1. 收盘 < MA20 且 收盘 < MA60（双均线下方，滞后锁）
      2. 单日跌幅 < drop_threshold
      3. 距N日高点跌幅 > high_drop_pct
      4. MA5 下穿 MA20（死叉）
    - neutral：其他情况

    P2 退出对称化（``symmetric_recovery=True`` 默认开启）
    ---------------------------------------------------
    此前进入 down 只需 4 个弱条件之一，但离开 down 的“up”需“收盘>MA20 且
    MA5>MA20 且 收盘>MA60”三重严格确认——即**进敏退钝**。大跌后即便反弹，
    价格仍长期低于被砸低的双均线，于是持续被判为 down、踏空反弹。

    对称模式下，退出 down 的条件与进入镜像：只要价格不再“双均线下方”（涨破
    任一均线）、或单日反弹、或自近期低点回升超阈值、或出现金叉，即释放 down。
    结构位（双均线上/下方）冲突时结构优先，纯瞬时信号冲突时取中性，避免抖振。

    设 ``symmetric_recovery=False`` 可完全回退到旧判定（用于复现 #206 基线做 A/B）。
    """
    if len(closes) < 5:
        return "neutral", {"reason": "too_few_bars"}

    last_close = float(closes[-1])
    prev_close = float(closes[-2]) if len(closes) > 1 else last_close
    change_pct = (last_close - prev_close) / prev_close if prev_close > 0 else 0.0

    detail: dict[str, Any] = {
        "close": last_close,
        "change_pct": round(change_pct, 6),
        "days": len(closes),
    }

    # 计算均线
    ma_short_arr = MA(closes, rules.ma_short)
    ma_long_arr = MA(closes, rules.ma_long)
    ma_long2_arr = MA(closes, rules.ma_long2) if rules.ma_long2 != rules.ma_long else ma_long_arr

    ma_s = float(RET(ma_short_arr, 1))
    ma_l = float(RET(ma_long_arr, 1))
    ma_l2 = float(RET(ma_long2_arr, 1))

    detail["ma_short"] = ma_s
    detail["ma_long"] = ma_l
    detail["ma_long2"] = ma_l2

    # 均线偏离度
    ma20_gap = (last_close - ma_l) / ma_l if ma_l > 0 else 0.0
    ma60_gap = (last_close - ma_l2) / ma_l2 if ma_l2 > 0 else 0.0
    detail["ma20_gap"] = round(ma20_gap, 6)
    detail["ma60_gap"] = round(ma60_gap, 6)

    # 距 N 日高点跌幅 / 自 N 日低点回升
    high_window = min(rules.high_window, len(closes) - 1)
    recent_high = float(np.max(closes[-high_window:])) if high_window > 0 else last_close
    recent_low = float(np.min(closes[-high_window:])) if high_window > 0 else last_close
    drop_from_high = (last_close - recent_high) / recent_high if recent_high > 0 else 0.0
    rebound_from_low = (last_close - recent_low) / recent_low if recent_low > 0 else 0.0
    detail["drop_from_high"] = round(drop_from_high, 6)
    detail["recent_high"] = recent_high
    detail["rebound_from_low"] = round(rebound_from_low, 6)
    detail["recent_low"] = recent_low

    # ── 均线交叉（死叉 / 金叉） ──
    ma_cross_down = False
    ma_cross_up = False
    if rules.ma_cross_enabled and len(closes) >= rules.ma_long + 1:
        # 死叉：长均线自上而下穿越短均线（MA20 上穿 MA5）
        dcross = CROSS(ma_long_arr, ma_short_arr)
        # 金叉：短均线自下而上穿越长均线（MA5 上穿 MA20）
        gcross = CROSS(ma_short_arr, ma_long_arr)
        if isinstance(dcross, np.ndarray) and len(dcross) > 0:
            ma_cross_down = bool(np.any(dcross[-min(5, len(dcross)):]))
        if isinstance(gcross, np.ndarray) and len(gcross) > 0:
            ma_cross_up = bool(np.any(gcross[-min(5, len(gcross)):]))

    # ── 对称模式（默认） ──
    if rules.symmetric_recovery:
        bearish: list[str] = []
        bullish: list[str] = []

        if last_close < ma_l and last_close < ma_l2 and ma_l > 0 and ma_l2 > 0:
            bearish.append("price_below_ma20_and_ma60")
        if change_pct < float(rules.drop_threshold):
            bearish.append(f"single_day_drop:{round(change_pct, 4)}")
        if drop_from_high < float(rules.high_drop_pct):
            bearish.append(f"drop_from_high:{round(drop_from_high, 4)}")
        if ma_cross_down:
            bearish.append("ma_death_cross")

        if last_close > ma_l and last_close > ma_l2 and ma_l > 0 and ma_l2 > 0:
            bullish.append("price_above_ma20_and_ma60")
        if change_pct > -float(rules.drop_threshold):
            bullish.append(f"single_day_gain:{round(change_pct, 4)}")
        if rebound_from_low > -float(rules.high_drop_pct):
            bullish.append(f"rebound_from_low:{round(rebound_from_low, 4)}")
        if ma_cross_up:
            bullish.append("ma_golden_cross")

        detail["bearish_conditions"] = bearish
        detail["bullish_conditions"] = bullish

        if bearish and not bullish:
            detail["down_conditions"] = bearish
            return "down", detail
        if bullish and not bearish:
            detail["up_conditions"] = bullish
            return "up", detail
        if bearish and bullish:
            # 冲突：结构位优先（双均线下方/上方），否则中性避免抖振
            if "price_below_ma20_and_ma60" in bearish:
                detail["down_conditions"] = bearish
                return "down", detail
            if "price_above_ma20_and_ma60" in bullish:
                detail["up_conditions"] = bullish
                return "up", detail
            return "neutral", detail
        return "neutral", detail

    # ── 旧行为（symmetric_recovery=False，用于复现 #206 基线） ──
    # 先检查 up 条件（短期趋势强劲）
    if last_close > ma_l and ma_s > ma_l and ma_l2 > 0:
        if last_close > ma_l2:
            return "up", detail

    down_conditions: list[str] = []

    if last_close < ma_l and last_close < ma_l2 and ma_l > 0 and ma_l2 > 0:
        down_conditions.append("price_below_ma20_and_ma60")
    if change_pct < float(rules.drop_threshold):
        down_conditions.append(f"single_day_drop:{round(change_pct, 4)}")
    if drop_from_high < float(rules.high_drop_pct):
        down_conditions.append(f"drop_from_high:{round(drop_from_high, 4)}")
    if ma_cross_down:
        down_conditions.append("ma_death_cross")

    if down_conditions:
        detail["down_conditions"] = down_conditions
        return "down", detail

    return "neutral", detail


async def detect_market_state(
    session: AsyncSession,
    rules: DefensiveRules,
) -> tuple[str, dict[str, Any]]:
    """检测大盘当前状态，返回 (state, condition_detail)。

    从数据库取最近 120 日指数 K 线后委托纯函数 :func:`classify_market_state`
    完成判定，保证与回测引擎内内存判定逻辑一致。
    """
    # 获取最近 120 日 K 线（足够计算 MA60）
    result = await session.execute(
        text(
            """
            SELECT trade_date, close FROM daily_kline
            WHERE ts_code = :index_code
            ORDER BY trade_date DESC
            LIMIT :limit
            """
        ),
        {"index_code": rules.index_code, "limit": 120},
    )
    rows = list(reversed([dict(r) for r in result.mappings().all()]))
    if len(rows) < 30:
        return "neutral", {"reason": "kline_data_insufficient", "rows": len(rows)}

    closes = np.array([_safe_decimal(r["close"]) or Decimal("0") for r in rows], dtype=float)
    if len(closes) < 5:
        return "neutral", {"reason": "too_few_bars"}

    return classify_market_state(closes, rules)


async def detect_state_change(
    session: AsyncSession,
    rules: DefensiveRules,
) -> tuple[str, MarketSignalRecord | None]:
    """检测市场状态是否发生变化，记录新状态并返回变更。

    返回 (new_state, latest_signal_record)。
    """
    new_state, detail = await detect_market_state(session, rules)

    # 获取最新记录
    from app.data.repository.seesaw import get_latest_market_signal
    latest = await get_latest_market_signal(session, rules.index_code)

    # 构造新记录
    change_pct = _safe_decimal(detail.get("change_pct"))
    ma20_gap = _safe_decimal(detail.get("ma20_gap"))
    ma60_gap = _safe_decimal(detail.get("ma60_gap"))
    drop_from_high = _safe_decimal(detail.get("drop_from_high"))

    signal = MarketSignalRecord(
        index_code=rules.index_code,
        state=new_state,
        close_price=_safe_decimal(detail.get("close")),
        prev_close=_safe_decimal(detail.get("prev_close", 0)),
        change_pct=change_pct,
        ma20_gap=ma20_gap,
        ma60_gap=ma60_gap,
        drop_from_high=drop_from_high,
        condition_detail=detail,
    )

    # 写入数据库
    from app.data.repository.seesaw import insert_market_signal
    await insert_market_signal(session, signal)
    signal.id = 1  # approximate; id assigned by DB

    return new_state, signal


# ── 推荐引擎 ───────────────────────────────────────────────────────────────────


async def get_seesaw_recommendations(
    session: AsyncSession,
    market_state: str,
    rules: DefensiveRules,
    limit: int = 10,
) -> list[RecommendationItem]:
    """当 market_state == 'down' 时，返回避险库（defensive_pool 表）全部启用标的。

    排序与权重完全来自人工维护的 ``sort_order``，**引擎不计算、也不依赖任何质量分
    （β / 股息率 / PE / FCF 等）**——与回测 overlay（全池等权）及实时切换
    （``rank_defensive_pool``）共用同一张 ``defensive_pool`` 表、同一套人工优先级，
    保证「推荐 / 执行 / 回测选股」三端同源。

    此函数直接委托 :func:`rank_defensive_pool` 取表全库（按 sort_order 升序），
    包装为 ``RecommendationItem``：``score`` 固定 ``1.0`` 表示「等权入选」（非质量分
    排序），``reason`` 统一为人工维护说明，便于前端展示「等权买入」。

    ``limit`` 仅作软上限（内部取全库，最多 500 只），确保推荐清单与实际切换/等权
    买入的标的一致，不被展示截断误导。
    """
    if market_state != "down":
        return []

    from app.data.repository.seesaw import list_defensive_pool

    # 取表全库（enabled），按 sort_order 升序；limit 仅作软上限
    pool_items = await list_defensive_pool(
        session, enabled_only=True, limit=max(int(limit), 500)
    )
    if not pool_items:
        return []

    return [
        RecommendationItem(
            ts_code=item.ts_code,
            name=item.name,
            score=1.0,
            beta=None,
            dividend_yield=None,
            pe_ttm=None,
            reason="避险库标的（人工维护，按优先级等权买入）",
        )
        for item in pool_items
    ]


# ── 触发日志 ───────────────────────────────────────────────────────────────────


async def record_seesaw_trigger(
    session: AsyncSession,
    market_state: str,
    index_code: str,
    recommendations: list[RecommendationItem],
) -> int:
    """记录跷跷板触发事件。仅在首次转入 down 状态时调用。"""
    from app.data.repository.seesaw import insert_seesaw_trigger

    rec_list = [
        {
            "ts_code": r.ts_code,
            "name": r.name,
            "score": r.score,
            "beta": r.beta,
            "reason": r.reason,
        }
        for r in recommendations
    ]
    return await insert_seesaw_trigger(session, market_state, index_code, rec_list)


async def rank_defensive_pool(
    session: AsyncSession,
    limit: int = 20,
) -> list[str]:
    """返回避险库内的股票列表，按人工配置的优先级（sort_order）排序。

    避险库（``defensive_pool`` 表）由用户在平台中手动维护：哪些标的入选、
    是否启用、以及库内优先顺序（``sort_order``）均来自人工配置，引擎不计算、
    也不依赖任何质量分或自动排名。回测引擎在启动时调用一次，按 ``sort_order``
    升序取前 ``limit`` 只作为避险配置标的。

    注意：此函数不依赖大盘状态、不做质量分计算，排序完全来自人工配置。
    """
    from app.data.repository.seesaw import list_defensive_pool

    pool_items = await list_defensive_pool(session, enabled_only=True, limit=limit)
    if not pool_items:
        return []
    return [item.ts_code for item in pool_items]
