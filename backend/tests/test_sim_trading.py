import json
import asyncio
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.realtime.models import RealtimeTick
from app.realtime.risk_guard import GuardPosition, RealtimeRiskGuard, trigger_realtime_stop_order, write_risk_guard_heartbeat
from app.sim.service import SignalOrderRequest, _fee_config, _global_fee_config, generate_order_from_signal, match_order, snapshot_daily_nav, unlock_t1_positions


class FakeResult:
    def __init__(self, rows=None, scalar=None, rowcount=1):
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0]

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        if self._scalar is not None:
            return self._scalar
        return self._rows[0] if self._rows else None


class ScriptedSession:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []
        self.params = []
        self.commits = 0

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        if not self.results:
            return FakeResult([])
        return self.results.pop(0)

    async def commit(self):
        self.commits += 1


def account_row(**overrides):
    row = {
        "id": 1,
        "user_id": 1,
        "strategy_id": None,
        "name": "M4",
        "initial_cash": Decimal("100000.0000"),
        "available_cash": Decimal("100000.0000"),
        "frozen_cash": Decimal("0.0000"),
        "total_asset": Decimal("100000.0000"),
        "status": "active",
        "config": {},
        "created_at": datetime(2026, 5, 21),
        "updated_at": datetime(2026, 5, 21),
    }
    row.update(overrides)
    return row


def calendar_row(**overrides):
    row = {
        "cal_date": date(2026, 5, 21),
        "is_open": True,
        "pretrade_date": date(2026, 5, 20),
        "nexttrade_date": date(2026, 5, 22),
    }
    row.update(overrides)
    return row


def kline_row(**overrides):
    row = {
        "ts_code": "000001.SZ",
        "trade_date": date(2026, 5, 21),
        "open": Decimal("10.0000"),
        "high": Decimal("10.2000"),
        "low": Decimal("9.9000"),
        "close": Decimal("10.0000"),
        "pre_close": Decimal("9.8000"),
        "is_suspended": False,
        "is_limit_up": False,
        "is_limit_down": False,
        "is_st": False,
        "market": "主板",
    }
    row.update(overrides)
    return row


def signal_row(action="BUY", **overrides):
    row = {
        "id": 7,
        "user_id": 1,
        "strategy_id": None,
        "account_id": 1,
        "ts_code": "000001.SZ",
        "trade_date": date(2026, 5, 21),
        "signal_type": "买入",
        "target_position": Decimal("1"),
        "current_position": Decimal("0"),
        "action": action,
        "confidence": None,
        "reason": None,
        "snapshot": {},
        "created_at": datetime(2026, 5, 21),
    }
    row.update(overrides)
    return row


def find_statement_index(session, needle: str, start: int = 0) -> int:
    for idx, statement in enumerate(session.statements[start:], start=start):
        if needle in statement:
            return idx
    raise AssertionError(f"statement containing {needle!r} not found")


def find_param_index(session, key: str, value: object, start: int = 0) -> int:
    for idx, params in enumerate(session.params[start:], start=start):
        if params.get(key) == value:
            return idx
    raise AssertionError(f"params containing {key}={value!r} not found")


def order_row(**overrides):
    row = {
        "id": 9,
        "account_id": 1,
        "signal_id": 7,
        "ts_code": "000001.SZ",
        "direction": "买入",
        "order_type": "限价",
        "price": Decimal("10.0000"),
        "volume": 9900,
        "filled_volume": 0,
        "frozen_amount": Decimal("99025.7400"),
        "status": "待成交",
        "reject_reason": None,
        "submit_time": datetime(2026, 5, 21),
        "update_time": datetime(2026, 5, 21),
        "cancel_time": None,
    }
    row.update(overrides)
    return row


def sell_match_results(*, account_id=1, ts_code="000001.SZ", price=Decimal("10.0000"), volume=1000, trade_date=date(2026, 5, 21), with_kline=True):
    kline_result = (
        FakeResult([
            kline_row(
                ts_code=ts_code,
                trade_date=trade_date,
                low=price - Decimal("0.0100"),
                high=price + Decimal("0.0100"),
                close=price,
            )
        ])
        if with_kline
        else FakeResult([])
    )
    return [
        FakeResult([order_row(account_id=account_id, ts_code=ts_code, direction="卖出", price=price, volume=volume, frozen_amount=Decimal("0.0000"), user_id=1, config={})]),
        FakeResult([calendar_row(cal_date=trade_date)]),
        kline_result,
        FakeResult([
            {
                "id": 12,
                "order_id": 9,
                "account_id": account_id,
                "ts_code": ts_code,
                "direction": "卖出",
                "price": price,
                "volume": volume,
                "amount": price * Decimal(volume),
                "stamp_tax": Decimal("0.0000"),
                "commission": Decimal("5.0000"),
                "transfer_fee": Decimal("0.1000"),
                "total_fee": Decimal("5.1000"),
                "trade_time": datetime(2026, 5, 21),
            }
        ]),
        FakeResult([]),
        FakeResult([]),
        FakeResult([]),
        FakeResult([]),
        FakeResult([]),
        FakeResult([]),
    ]


def test_sim_fee_config_merges_account_over_global_over_defaults():
    global_config = _global_fee_config(
        {
            "user_trading_fee_config": {
                "commission_rate": "0.0003",
                "min_commission": "4.0",
                "waive_min_commission": True,
            }
        }
    )
    account_config = {"fee_config": {"commission_rate": "0.0002"}}

    result = _fee_config(account_config, global_config)

    assert result.commission_rate == Decimal("0.0002")
    assert result.min_commission == Decimal("4.0")
    assert result.stamp_tax_rate == Decimal("0.0005")
    assert result.transfer_fee_rate == Decimal("0.00001")
    assert result.waive_min_commission is True


@pytest.mark.asyncio
async def test_signal_buy_creates_lot_sized_order_and_freezes_cash():
    session = ScriptedSession(
        [
            FakeResult([account_row()]),
            FakeResult([calendar_row()]),
            FakeResult([]),
            FakeResult([kline_row()]),
            FakeResult([signal_row()]),
            FakeResult([order_row()]),
            FakeResult([]),
            FakeResult([]),
        ]
    )

    result = await generate_order_from_signal(
        session,
        user_id=1,
        account_id=1,
        request=SignalOrderRequest(ts_code="000001.SZ", signal_type="买入", trade_date=date(2026, 5, 21)),
    )

    assert result["action"] == "BUY"
    assert result["order"]["volume"] == 9900
    assert result["order"]["frozen_amount"] == "99025.7400"
    assert session.params[5]["frozen_amount"] == Decimal("99025.7400")
    assert "UPDATE sim_accounts" in session.statements[6]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_signal_blocks_non_trade_day_but_logs_signal():
    session = ScriptedSession(
        [
            FakeResult([account_row()]),
            FakeResult([calendar_row(is_open=False)]),
            FakeResult([]),
            FakeResult([signal_row(action="BLOCKED")]),
        ]
    )

    result = await generate_order_from_signal(
        session,
        user_id=1,
        account_id=1,
        request=SignalOrderRequest(ts_code="000001.SZ", signal_type="买入", trade_date=date(2026, 5, 21)),
    )

    assert result["action"] == "BLOCKED"
    assert result["reason"] == "非交易日"
    assert result["order"] is None
    assert "INSERT INTO signal_log" in session.statements[3]


@pytest.mark.asyncio
async def test_signal_buy_below_one_lot_records_hold_without_order():
    session = ScriptedSession(
        [
            FakeResult([account_row()]),
            FakeResult([calendar_row(cal_date=date(2026, 5, 29))]),
            FakeResult([]),
            FakeResult([kline_row(ts_code="605499.SH", trade_date=date(2026, 5, 29), close=Decimal("260.0000"))]),
            FakeResult([signal_row(action="HOLD", ts_code="605499.SH", trade_date=date(2026, 5, 29))]),
        ]
    )

    result = await generate_order_from_signal(
        session,
        user_id=1,
        account_id=1,
        request=SignalOrderRequest(
            ts_code="605499.SH",
            signal_type="买入",
            trade_date=date(2026, 5, 29),
            target_position=Decimal("0.1"),
        ),
    )

    assert result["action"] == "HOLD"
    assert result["order"] is None
    assert result["reason"] == "目标买入金额或可用资金不足一手，未下单"
    assert session.params[4]["action"] == "HOLD"
    assert session.params[4]["reason"] == "目标买入金额或可用资金不足一手，未下单"
    assert json.loads(session.params[4]["snapshot"])["no_order_reason"] == "目标买入金额或可用资金不足一手，未下单"
    assert not any("INSERT INTO sim_orders" in statement for statement in session.statements)


@pytest.mark.asyncio
async def test_signal_sell_uses_available_shares_only():
    position = {
        "id": 3,
        "account_id": 1,
        "ts_code": "000001.SZ",
        "shares": 1000,
        "available_shares": 600,
        "frozen_shares": 0,
        "avg_cost": Decimal("8.0000"),
        "current_price": Decimal("10.0000"),
        "market_value": Decimal("10000.0000"),
    }
    session = ScriptedSession(
        [
            FakeResult([account_row(total_asset=Decimal("10000.0000"), available_cash=Decimal("0.0000"))]),
            FakeResult([calendar_row()]),
            FakeResult([position]),
            FakeResult([kline_row()]),
            FakeResult([signal_row(action="SELL_ALL")]),
            FakeResult([order_row(direction="卖出", volume=600, frozen_amount=Decimal("0.0000"))]),
            FakeResult([]),
        ]
    )

    result = await generate_order_from_signal(
        session,
        user_id=1,
        account_id=1,
        request=SignalOrderRequest(ts_code="000001.SZ", signal_type="卖出", trade_date=date(2026, 5, 21)),
    )

    assert result["action"] == "SELL_ALL"
    assert result["order"]["volume"] == 600
    assert session.params[6]["volume"] == 600
    assert "available_shares = available_shares - :volume" in session.statements[6]


@pytest.mark.asyncio
async def test_match_buy_keeps_bought_shares_unavailable_until_t1():
    session = ScriptedSession(
        [
            FakeResult([order_row(user_id=1, config={})]),
            FakeResult([calendar_row()]),
            FakeResult([kline_row()]),
            FakeResult([
                {
                    "id": 11,
                    "order_id": 9,
                    "account_id": 1,
                    "ts_code": "000001.SZ",
                    "direction": "买入",
                    "price": Decimal("10.0000"),
                    "volume": 9900,
                    "amount": Decimal("99000.0000"),
                    "stamp_tax": Decimal("0.0000"),
                    "commission": Decimal("24.7500"),
                    "transfer_fee": Decimal("0.9900"),
                    "total_fee": Decimal("25.7400"),
                    "trade_time": datetime(2026, 5, 21),
                }
            ]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
        ]
    )

    result = await match_order(session, user_id=1, order_id=9, trade_date=date(2026, 5, 21))

    assert result["status"] == "全部成交"
    assert "FOR UPDATE OF o, a" in session.statements[0]
    assert session.params[4]["avg_cost"] == Decimal("10.0026")
    assert "available_shares, frozen_shares" in session.statements[4]
    assert session.params[5]["refund"] == Decimal("0.0000")
    assert session.commits == 1


@pytest.mark.asyncio
async def test_match_open_mode_uses_open_price():
    session = ScriptedSession(
        [
            FakeResult([order_row(user_id=1, config={})]),
            FakeResult([calendar_row()]),
            FakeResult([kline_row(open=Decimal("9.5000"), close=Decimal("10.0000"))]),
            FakeResult([
                {
                    "id": 11,
                    "order_id": 9,
                    "account_id": 1,
                    "ts_code": "000001.SZ",
                    "direction": "买入",
                    "price": Decimal("9.5000"),
                    "volume": 9900,
                    "amount": Decimal("94050.0000"),
                    "stamp_tax": Decimal("0.0000"),
                    "commission": Decimal("23.5125"),
                    "transfer_fee": Decimal("0.9405"),
                    "total_fee": Decimal("24.4530"),
                    "trade_time": datetime(2026, 5, 21),
                }
            ]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
        ]
    )

    await match_order(session, user_id=1, order_id=9, trade_date=date(2026, 5, 21), match_mode="open")

    assert session.params[3]["price"] == Decimal("9.5000")
    assert session.params[3]["amount"] == Decimal("94050.0000")


@pytest.mark.asyncio
async def test_match_limit_mode_uses_order_price_when_touched():
    session = ScriptedSession(
        [
            FakeResult([order_row(user_id=1, config={}, price=Decimal("10.1000"))]),
            FakeResult([calendar_row()]),
            FakeResult([kline_row(low=Decimal("9.9000"), high=Decimal("10.2000"), close=Decimal("10.0000"))]),
            FakeResult([
                {
                    "id": 11,
                    "order_id": 9,
                    "account_id": 1,
                    "ts_code": "000001.SZ",
                    "direction": "买入",
                    "price": Decimal("10.1000"),
                    "volume": 9900,
                    "amount": Decimal("99990.0000"),
                    "stamp_tax": Decimal("0.0000"),
                    "commission": Decimal("24.9975"),
                    "transfer_fee": Decimal("0.9999"),
                    "total_fee": Decimal("25.9974"),
                    "trade_time": datetime(2026, 5, 21),
                }
            ]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
        ]
    )

    await match_order(session, user_id=1, order_id=9, trade_date=date(2026, 5, 21), match_mode="limit")

    assert session.params[3]["price"] == Decimal("10.1000")


@pytest.mark.asyncio
async def test_match_limit_mode_blocks_when_price_not_touched():
    session = ScriptedSession(
        [
            FakeResult([order_row(user_id=1, config={}, price=Decimal("10.3000"))]),
            FakeResult([calendar_row()]),
            FakeResult([kline_row(low=Decimal("9.9000"), high=Decimal("10.2000"))]),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await match_order(session, user_id=1, order_id=9, trade_date=date(2026, 5, 21), match_mode="limit")

    assert exc.value.status_code == 409
    assert exc.value.detail == "限价未触达"


@pytest.mark.asyncio
async def test_match_order_falls_back_to_order_price_when_daily_kline_missing():
    session = ScriptedSession(
        [
            FakeResult([order_row(direction="卖出", volume=600, frozen_amount=Decimal("0.0000"), user_id=1, config={}, price=Decimal("10.5000"))]),
            FakeResult([calendar_row()]),
            FakeResult([]),
            FakeResult([
                {
                    "id": 12,
                    "order_id": 9,
                    "account_id": 1,
                    "ts_code": "000001.SZ",
                    "direction": "卖出",
                    "price": Decimal("10.5000"),
                    "volume": 600,
                    "amount": Decimal("6300.0000"),
                    "stamp_tax": Decimal("3.1500"),
                    "commission": Decimal("5.0000"),
                    "transfer_fee": Decimal("0.0630"),
                    "total_fee": Decimal("8.2130"),
                    "trade_time": datetime(2026, 5, 21),
                }
            ]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
        ]
    )

    result = await match_order(session, user_id=1, order_id=9, trade_date=date(2026, 5, 21))

    assert result["status"] == "全部成交"
    assert result["match_mode_used"] == "order_price_fallback"
    assert session.params[3]["price"] == Decimal("10.5000")
    assert session.params[3]["amount"] == Decimal("6300.0000")


@pytest.mark.asyncio
async def test_match_order_missing_daily_kline_requires_order_price():
    session = ScriptedSession(
        [
            FakeResult([order_row(user_id=1, config={}, price=None)]),
            FakeResult([calendar_row()]),
            FakeResult([]),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await match_order(session, user_id=1, order_id=9, trade_date=date(2026, 5, 21))

    assert exc.value.status_code == 404
    assert exc.value.detail == "daily kline not found and order price is missing"


@pytest.mark.asyncio
async def test_match_sell_charges_stamp_tax_only_on_sell():
    session = ScriptedSession(
        [
            FakeResult([order_row(direction="卖出", volume=600, frozen_amount=Decimal("0.0000"), user_id=1, config={})]),
            FakeResult([calendar_row()]),
            FakeResult([kline_row()]),
            FakeResult([
                {
                    "id": 12,
                    "order_id": 9,
                    "account_id": 1,
                    "ts_code": "000001.SZ",
                    "direction": "卖出",
                    "price": Decimal("10.0000"),
                    "volume": 600,
                    "amount": Decimal("6000.0000"),
                    "stamp_tax": Decimal("3.0000"),
                    "commission": Decimal("5.0000"),
                    "transfer_fee": Decimal("0.0600"),
                    "total_fee": Decimal("8.0600"),
                    "trade_time": datetime(2026, 5, 21),
                }
            ]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
        ]
    )

    await match_order(session, user_id=1, order_id=9, trade_date=date(2026, 5, 21))

    assert "current_price = CAST(:price AS NUMERIC)" in session.statements[4]
    assert "market_value = (shares - :volume) * CAST(:price AS NUMERIC)" in session.statements[4]
    assert session.params[3]["stamp_tax"] == Decimal("3.0000")
    sell_income_idx = find_statement_index(session, "available_cash = available_cash + :net_income")
    assert session.params[sell_income_idx]["net_income"] == Decimal("5991.9400")


@pytest.mark.asyncio
async def test_match_full_sell_keeps_today_cleared_position():
    session = ScriptedSession(
        [
            FakeResult([order_row(direction="卖出", volume=1000, frozen_amount=Decimal("0.0000"), user_id=1, config={})]),
            FakeResult([calendar_row()]),
            FakeResult([kline_row()]),
            FakeResult([
                {
                    "id": 12,
                    "order_id": 9,
                    "account_id": 1,
                    "ts_code": "000001.SZ",
                    "direction": "卖出",
                    "price": Decimal("10.0000"),
                    "volume": 1000,
                    "amount": Decimal("10000.0000"),
                    "stamp_tax": Decimal("5.0000"),
                    "commission": Decimal("5.0000"),
                    "transfer_fee": Decimal("0.1000"),
                    "total_fee": Decimal("10.1000"),
                    "trade_time": datetime(2026, 5, 21),
                }
            ]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
        ]
    )

    await match_order(session, user_id=1, order_id=9, trade_date=date(2026, 5, 21))

    sell_update_idx = find_statement_index(session, "SET shares = shares - :volume")
    assert "WHEN shares - :volume <= 0" in session.statements[sell_update_idx]
    assert session.params[sell_update_idx]["amount"] == Decimal("10000.0000")
    assert session.params[sell_update_idx]["total_fee"] == Decimal("10.1000")
    assert all("DELETE FROM sim_positions" not in statement for statement in session.statements)
    assert find_statement_index(session, "available_cash = available_cash + :net_income") > sell_update_idx


@pytest.mark.asyncio
async def test_match_blocks_limit_up_buy():
    session = ScriptedSession(
        [
            FakeResult([order_row(user_id=1, config={})]),
            FakeResult([calendar_row()]),
            FakeResult([kline_row(is_limit_up=True)]),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await match_order(session, user_id=1, order_id=9, trade_date=date(2026, 5, 21))

    assert exc.value.status_code == 409
    assert exc.value.detail == "涨停不可买入"
    assert len(session.statements) == 3
    assert session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("market", "ts_code", "pre_close", "close"),
    [
        ("主板", "000001.SZ", Decimal("10.0000"), Decimal("11.0000")),
        ("创业板", "300001.SZ", Decimal("10.0000"), Decimal("12.0000")),
        ("科创板", "688001.SH", Decimal("10.0000"), Decimal("12.0000")),
        ("北交所", "830001.BJ", Decimal("10.0000"), Decimal("13.0000")),
    ],
)
async def test_match_blocks_computed_limit_up_buy_by_market(market, ts_code, pre_close, close):
    session = ScriptedSession(
        [
            FakeResult([order_row(ts_code=ts_code, user_id=1, config={})]),
            FakeResult([calendar_row()]),
            FakeResult([kline_row(ts_code=ts_code, market=market, pre_close=pre_close, close=close)]),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await match_order(session, user_id=1, order_id=9, trade_date=date(2026, 5, 21))

    assert exc.value.status_code == 409
    assert exc.value.detail == "涨停不可买入"
    assert len(session.statements) == 3
    assert session.commits == 0


@pytest.mark.asyncio
async def test_match_blocks_computed_st_limit_up_buy():
    session = ScriptedSession(
        [
            FakeResult([order_row(user_id=1, config={})]),
            FakeResult([calendar_row()]),
            FakeResult([kline_row(pre_close=Decimal("10.0000"), close=Decimal("10.5000"), is_st=True)]),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await match_order(session, user_id=1, order_id=9, trade_date=date(2026, 5, 21))

    assert exc.value.status_code == 409
    assert exc.value.detail == "涨停不可买入"
    assert len(session.statements) == 3
    assert session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("market", "ts_code", "pre_close", "close"),
    [
        ("主板", "000001.SZ", Decimal("10.0000"), Decimal("9.0000")),
        ("创业板", "300001.SZ", Decimal("10.0000"), Decimal("8.0000")),
        ("科创板", "688001.SH", Decimal("10.0000"), Decimal("8.0000")),
        ("北交所", "830001.BJ", Decimal("10.0000"), Decimal("7.0000")),
    ],
)
async def test_match_blocks_computed_limit_down_sell_by_market(market, ts_code, pre_close, close):
    session = ScriptedSession(
        [
            FakeResult([order_row(direction="卖出", ts_code=ts_code, volume=600, frozen_amount=Decimal("0.0000"), user_id=1, config={})]),
            FakeResult([calendar_row()]),
            FakeResult([kline_row(ts_code=ts_code, market=market, pre_close=pre_close, close=close)]),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await match_order(session, user_id=1, order_id=9, trade_date=date(2026, 5, 21))

    assert exc.value.status_code == 409
    assert exc.value.detail == "跌停不可卖出"
    assert len(session.statements) == 3
    assert session.commits == 0


@pytest.mark.asyncio
async def test_signal_generation_creates_order_on_computed_limit_up_buy():
    session = ScriptedSession(
        [
            FakeResult([account_row()]),
            FakeResult([calendar_row()]),
            FakeResult([]),
            FakeResult([kline_row(pre_close=Decimal("10.0000"), close=Decimal("10.5000"), is_st=True)]),
            FakeResult([signal_row(action="BUY")]),
            FakeResult([order_row(volume=9500, frozen_amount=Decimal("99775.9350"))]),
            FakeResult([]),
            FakeResult([]),
        ]
    )

    result = await generate_order_from_signal(
        session,
        user_id=1,
        account_id=1,
        request=SignalOrderRequest(ts_code="000001.SZ", signal_type="买入", trade_date=date(2026, 5, 21)),
    )

    assert result["action"] == "BUY"
    assert result["reason"] == ""
    assert result["signal"]["action"] == "BUY"
    assert result["order"]["status"] == "待成交"
    assert result["order"]["volume"] == 9500
    assert session.params[4]["action"] == "BUY"
    assert "blocked_reason" not in session.params[4]["snapshot"]
    assert session.params[5]["volume"] == 9500
    assert session.params[5]["frozen_amount"] == Decimal("99775.9350")


@pytest.mark.asyncio
async def test_signal_generation_creates_order_on_computed_limit_down_sell():
    position = {
        "id": 3,
        "account_id": 1,
        "ts_code": "000001.SZ",
        "shares": 1000,
        "available_shares": 1000,
        "frozen_shares": 0,
        "avg_cost": Decimal("10.0000"),
        "current_price": Decimal("9.0000"),
        "market_value": Decimal("9000.0000"),
    }
    session = ScriptedSession(
        [
            FakeResult([account_row(total_asset=Decimal("10000.0000"), available_cash=Decimal("1000.0000"))]),
            FakeResult([calendar_row()]),
            FakeResult([position]),
            FakeResult([kline_row(pre_close=Decimal("10.0000"), close=Decimal("9.0000"))]),
            FakeResult([signal_row(action="SELL_ALL", signal_type="卖出", current_position=Decimal("0.900000"))]),
            FakeResult([order_row(direction="卖出", volume=1000, frozen_amount=Decimal("0.0000"))]),
            FakeResult([]),
        ]
    )

    result = await generate_order_from_signal(
        session,
        user_id=1,
        account_id=1,
        request=SignalOrderRequest(ts_code="000001.SZ", signal_type="卖出", trade_date=date(2026, 5, 21)),
    )

    assert result["action"] == "SELL_ALL"
    assert result["reason"] == ""
    assert result["signal"]["action"] == "SELL_ALL"
    assert result["order"]["status"] == "待成交"
    assert result["order"]["direction"] == "卖出"
    assert session.params[4]["action"] == "SELL_ALL"
    assert "blocked_reason" not in session.params[4]["snapshot"]
    assert "available_shares = available_shares - :volume" in session.statements[6]


@pytest.mark.asyncio
async def test_realtime_risk_guard_limit_up_take_profit_creates_sell_order():
    trade_date = date(2026, 5, 22)
    position = {
        "id": 3,
        "account_id": 1,
        "ts_code": "000001.SZ",
        "shares": 1000,
        "available_shares": 1000,
        "frozen_shares": 0,
        "avg_cost": Decimal("10.0000"),
        "current_price": Decimal("10.0000"),
        "market_value": Decimal("10000.0000"),
    }
    session = ScriptedSession(
        [
            FakeResult([], rowcount=1),
            FakeResult([account_row(total_asset=Decimal("11000.0000"), available_cash=Decimal("1000.0000"))]),
            FakeResult([calendar_row(cal_date=trade_date, pretrade_date=date(2026, 5, 21))]),
            FakeResult([position]),
            FakeResult([kline_row(trade_date=trade_date, pre_close=Decimal("10.0000"), close=Decimal("11.0000"), is_limit_up=True)]),
            FakeResult([]),
            FakeResult([signal_row(action="SELL_ALL", signal_type="卖出", trade_date=trade_date, current_position=Decimal("0.909091"))]),
            FakeResult([order_row(direction="卖出", price=Decimal("10.9900"), volume=1000, frozen_amount=Decimal("0.0000"))]),
            FakeResult([]),
            *sell_match_results(price=Decimal("10.9900"), volume=1000, trade_date=trade_date),
        ]
    )

    result = await trigger_realtime_stop_order(
        session,
        position=GuardPosition(
            account_id=1,
            user_id=1,
            strategy_id=None,
            ts_code="000001.SZ",
            avg_cost=Decimal("10.0000"),
            shares=1000,
            available_shares=1000,
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.08"),
        ),
        tick=RealtimeTick(ts_code="000001.SZ", price=Decimal("11.0000"), bid1=Decimal("10.9900")),
        trade_date=trade_date,
    )

    assert result is not None
    assert result["action"] == "SELL_ALL"
    assert result["order"]["direction"] == "卖出"
    assert "current_price = CAST(:price AS NUMERIC)" in session.statements[0]
    assert "market_value = shares * CAST(:price AS NUMERIC)" in session.statements[0]
    assert session.params[0]["price"] == Decimal("11.0000")
    assert session.params[6]["reason"] == "止盈"
    assert json.loads(session.params[6]["snapshot"])["source"] == "realtime_risk_guard"
    assert session.params[7]["price"] == Decimal("10.9900")
    assert result["match"]["status"] == "全部成交"
    assert result["match"]["match_mode_used"] == "limit"
    assert session.params[12]["price"] == Decimal("10.9900")


@pytest.mark.asyncio
async def test_realtime_risk_guard_falls_back_to_latest_available_kline_for_order_rules():
    trade_date = date(2026, 6, 1)
    latest_kline_date = date(2026, 5, 29)
    position = {
        "id": 3,
        "account_id": 4,
        "ts_code": "000539.SZ",
        "shares": 1000,
        "available_shares": 1000,
        "frozen_shares": 0,
        "avg_cost": Decimal("8.8000"),
        "current_price": Decimal("8.8000"),
        "market_value": Decimal("8800.0000"),
    }
    session = ScriptedSession(
        [
            FakeResult([], rowcount=1),
            FakeResult([account_row(id=4, total_asset=Decimal("9770.0000"), available_cash=Decimal("970.0000"))]),
            FakeResult([calendar_row(cal_date=trade_date, pretrade_date=latest_kline_date)]),
            FakeResult([position]),
            FakeResult([]),
            FakeResult([{"pretrade_date": latest_kline_date}]),
            FakeResult([]),
            FakeResult([kline_row(ts_code="000539.SZ", trade_date=latest_kline_date, pre_close=Decimal("9.0000"), close=Decimal("9.3000"))]),
            FakeResult([]),
            FakeResult([signal_row(action="SELL_ALL", signal_type="卖出", trade_date=trade_date, current_position=Decimal("0.900716"))]),
            FakeResult([order_row(account_id=4, direction="卖出", price=Decimal("9.7700"), volume=1000, frozen_amount=Decimal("0.0000"))]),
            FakeResult([]),
            *sell_match_results(account_id=4, ts_code="000539.SZ", price=Decimal("9.7700"), volume=1000, trade_date=trade_date),
        ]
    )

    result = await trigger_realtime_stop_order(
        session,
        position=GuardPosition(
            account_id=4,
            user_id=1,
            strategy_id=None,
            ts_code="000539.SZ",
            avg_cost=Decimal("8.8000"),
            shares=1000,
            available_shares=1000,
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.08"),
        ),
        tick=RealtimeTick(ts_code="000539.SZ", price=Decimal("9.7700"), bid1=Decimal("9.7700")),
        trade_date=trade_date,
    )

    assert result is not None
    assert result["action"] == "SELL_ALL"
    assert result["order"]["direction"] == "卖出"
    assert "ORDER BY dk.trade_date DESC" in session.statements[7]
    assert session.params[7]["trade_date"] == trade_date
    assert session.params[10]["price"] == Decimal("9.7700")
    assert result["match"]["status"] == "全部成交"
    assert result["match"]["match_mode_used"] == "limit"


@pytest.mark.asyncio
async def test_realtime_risk_guard_uses_realtime_price_when_daily_kline_missing():
    trade_date = date(2026, 6, 1)
    position = {
        "id": 3,
        "account_id": 4,
        "ts_code": "000539.SZ",
        "shares": 1000,
        "available_shares": 1000,
        "frozen_shares": 0,
        "avg_cost": Decimal("8.8000"),
        "current_price": Decimal("8.8000"),
        "market_value": Decimal("8800.0000"),
    }
    session = ScriptedSession(
        [
            FakeResult([], rowcount=1),
            FakeResult([account_row(id=4, total_asset=Decimal("9770.0000"), available_cash=Decimal("970.0000"))]),
            FakeResult([calendar_row(cal_date=trade_date, pretrade_date=date(2026, 5, 29))]),
            FakeResult([position]),
            FakeResult([]),
            FakeResult([{"pretrade_date": date(2026, 5, 29)}]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([signal_row(action="SELL_ALL", signal_type="卖出", trade_date=trade_date, current_position=Decimal("0.900716"))]),
            FakeResult([order_row(account_id=4, direction="卖出", price=Decimal("9.7700"), volume=1000, frozen_amount=Decimal("0.0000"))]),
            FakeResult([]),
            *sell_match_results(account_id=4, ts_code="000539.SZ", price=Decimal("9.7700"), volume=1000, trade_date=trade_date, with_kline=False),
        ]
    )

    result = await trigger_realtime_stop_order(
        session,
        position=GuardPosition(
            account_id=4,
            user_id=1,
            strategy_id=None,
            ts_code="000539.SZ",
            avg_cost=Decimal("8.8000"),
            shares=1000,
            available_shares=1000,
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.08"),
        ),
        tick=RealtimeTick(ts_code="000539.SZ", price=Decimal("9.7700"), bid1=Decimal("9.7700")),
        trade_date=trade_date,
    )

    assert result is not None
    assert result["action"] == "SELL_ALL"
    assert result["order"]["direction"] == "卖出"
    snapshot = json.loads(session.params[9]["snapshot"])
    assert snapshot["source"] == "realtime_risk_guard"
    assert snapshot["kline_fallback"] == "realtime_order_price"
    assert session.params[10]["price"] == Decimal("9.7700")
    assert result["match"]["status"] == "全部成交"
    assert result["match"]["match_mode_used"] == "order_price_fallback"


@pytest.mark.asyncio
async def test_realtime_risk_guard_does_not_duplicate_pending_sell_order():
    trade_date = date(2026, 5, 22)
    position = {
        "id": 3,
        "account_id": 1,
        "ts_code": "000001.SZ",
        "shares": 1000,
        "available_shares": 1000,
        "frozen_shares": 0,
        "avg_cost": Decimal("10.0000"),
        "current_price": Decimal("10.0000"),
        "market_value": Decimal("10000.0000"),
    }
    session = ScriptedSession(
        [
            FakeResult([], rowcount=1),
            FakeResult([account_row(total_asset=Decimal("11000.0000"), available_cash=Decimal("1000.0000"))]),
            FakeResult([calendar_row(cal_date=trade_date, pretrade_date=date(2026, 5, 21))]),
            FakeResult([position]),
            FakeResult([kline_row(trade_date=trade_date, pre_close=Decimal("10.0000"), close=Decimal("11.0000"), is_limit_up=True)]),
            FakeResult([{"id": 99}]),
        ]
    )

    result = await trigger_realtime_stop_order(
        session,
        position=GuardPosition(
            account_id=1,
            user_id=1,
            strategy_id=None,
            ts_code="000001.SZ",
            avg_cost=Decimal("10.0000"),
            shares=1000,
            available_shares=1000,
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.08"),
        ),
        tick=RealtimeTick(ts_code="000001.SZ", price=Decimal("11.0000"), bid1=Decimal("10.9900")),
        trade_date=trade_date,
    )

    assert result is not None
    assert result["action"] == "HOLD"
    assert result["reason"] == "已有待成交卖出委托"
    assert not any("INSERT INTO sim_orders" in statement for statement in session.statements)


@pytest.mark.asyncio
async def test_realtime_risk_guard_ignores_tick_below_take_profit():
    session = ScriptedSession([])

    result = await trigger_realtime_stop_order(
        session,
        position=GuardPosition(
            account_id=1,
            user_id=1,
            strategy_id=None,
            ts_code="000001.SZ",
            avg_cost=Decimal("10.0000"),
            shares=1000,
            available_shares=1000,
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.08"),
        ),
        tick=RealtimeTick(ts_code="000001.SZ", price=Decimal("10.7000")),
        trade_date=date(2026, 5, 22),
    )

    assert result is None
    assert session.statements == []


@pytest.mark.asyncio
async def test_realtime_risk_guard_skips_sellable_shares_below_one_lot():
    session = ScriptedSession([])

    result = await trigger_realtime_stop_order(
        session,
        position=GuardPosition(
            account_id=1,
            user_id=1,
            strategy_id=None,
            ts_code="000001.SZ",
            avg_cost=Decimal("10.0000"),
            shares=1000,
            available_shares=99,
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.08"),
        ),
        tick=RealtimeTick(ts_code="000001.SZ", price=Decimal("11.0000")),
        trade_date=date(2026, 5, 22),
    )

    assert result is None
    assert session.statements == []


@pytest.mark.asyncio
async def test_realtime_risk_guard_keeps_processing_after_position_failure(monkeypatch):
    triggered: list[int] = []

    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeSessionFactory:
        def __call__(self):
            return FakeSessionContext()

    async def fake_trigger(session, *, position, tick, trade_date):
        if position.account_id == 1:
            raise RuntimeError("boom")
        triggered.append(position.account_id)
        return {"action": "SELL_ALL"}

    from app.realtime import risk_guard

    monkeypatch.setattr(risk_guard, "trigger_realtime_stop_order", fake_trigger)
    guard = RealtimeRiskGuard(session_factory=FakeSessionFactory())  # type: ignore[arg-type]
    guard.positions_by_code = {
        "000001.SZ": [
            GuardPosition(1, 1, None, "000001.SZ", Decimal("10.0000"), 1000, 1000, Decimal("0.05"), Decimal("0.08")),
            GuardPosition(2, 1, None, "000001.SZ", Decimal("10.0000"), 1000, 1000, Decimal("0.05"), Decimal("0.08")),
        ]
    }

    result = await guard.handle_tick(
        RealtimeTick(ts_code="000001.SZ", price=Decimal("11.0000")),
        trade_date=date(2026, 5, 22),
    )

    assert triggered == [2]
    assert result == [
        {"action": "BLOCKED", "reason": "boom", "order": None, "ts_code": "000001.SZ", "account_id": 1},
        {"action": "SELL_ALL"},
    ]


@pytest.mark.asyncio
async def test_realtime_risk_guard_snapshot_polling_fetches_position_quotes(monkeypatch):
    from app.realtime import risk_guard

    triggered: list[tuple[str, date]] = []

    class FakeProvider:
        def __init__(self, ts_codes):
            self.ts_codes = ts_codes

        async def fetch_snapshot(self):
            return [RealtimeTick(ts_code="000001.SZ", price=Decimal("11.0000"))]

    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeSessionFactory:
        def __call__(self):
            return FakeSessionContext()

    async def fake_unlock(session, *, trade_date):
        return 1

    async def fake_load_positions(session):
        return {
            "000001.SZ": [
                GuardPosition(
                    account_id=1,
                    user_id=1,
                    strategy_id=None,
                    ts_code="000001.SZ",
                    avg_cost=Decimal("10.0000"),
                    shares=1000,
                    available_shares=1000,
                    stop_loss_pct=Decimal("0.05"),
                    take_profit_pct=Decimal("0.08"),
                )
            ]
        }

    async def fake_handle_tick(self, tick, *, trade_date):
        triggered.append((tick.ts_code, trade_date))
        raise asyncio.CancelledError()

    monkeypatch.setattr(risk_guard, "EastMoneyRealtimeProvider", FakeProvider)
    monkeypatch.setattr(risk_guard, "unlock_t1_positions", fake_unlock)
    monkeypatch.setattr(risk_guard, "load_guard_positions", fake_load_positions)
    monkeypatch.setattr(RealtimeRiskGuard, "handle_tick", fake_handle_tick)

    guard = RealtimeRiskGuard(session_factory=FakeSessionFactory(), refresh_interval_seconds=0.01)  # type: ignore[arg-type]

    with pytest.raises(asyncio.CancelledError):
        await guard.run_snapshot_polling(trade_date=date(2026, 5, 22))

    assert triggered == [("000001.SZ", date(2026, 5, 22))]


@pytest.mark.asyncio
async def test_realtime_risk_guard_snapshot_polling_continues_after_snapshot_error(monkeypatch):
    from app.realtime import risk_guard

    calls = 0
    triggered: list[str] = []

    class FakeProvider:
        def __init__(self, ts_codes):
            self.ts_codes = ts_codes

        async def fetch_snapshot(self):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("snapshot down")
            return [RealtimeTick(ts_code="000001.SZ", price=Decimal("11.0000"))]

    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeSessionFactory:
        def __call__(self):
            return FakeSessionContext()

    async def fake_unlock(session, *, trade_date):
        return 1

    async def fake_load_positions(session):
        return {
            "000001.SZ": [
                GuardPosition(1, 1, None, "000001.SZ", Decimal("10.0000"), 1000, 1000, Decimal("0.05"), Decimal("0.08"))
            ]
        }

    async def fake_sleep(_seconds):
        if calls >= 2:
            raise asyncio.CancelledError()

    async def fake_handle_tick(self, tick, *, trade_date):
        triggered.append(tick.ts_code)

    monkeypatch.setattr(risk_guard, "EastMoneyRealtimeProvider", FakeProvider)
    monkeypatch.setattr(risk_guard, "unlock_t1_positions", fake_unlock)
    monkeypatch.setattr(risk_guard, "load_guard_positions", fake_load_positions)
    monkeypatch.setattr(risk_guard.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(RealtimeRiskGuard, "handle_tick", fake_handle_tick)

    guard = RealtimeRiskGuard(session_factory=FakeSessionFactory(), refresh_interval_seconds=0.01)  # type: ignore[arg-type]

    with pytest.raises(asyncio.CancelledError):
        await guard.run_snapshot_polling(trade_date=date(2026, 5, 22))

    assert calls == 2
    assert triggered == ["000001.SZ"]


@pytest.mark.asyncio
async def test_realtime_risk_guard_heartbeat_upserts_task_run():
    session = ScriptedSession([FakeResult([], rowcount=0), FakeResult([], rowcount=1)])

    await write_risk_guard_heartbeat(
        session,
        refresh_interval_seconds=30,
        loaded_positions=2,
        tracked_symbols=1,
        trade_date=date(2026, 5, 22),
        last_blocked_reason="未返回实时行情",
        missing_ticks=["000001.SZ"],
    )

    assert "UPDATE task_runs" in session.statements[0]
    assert "INSERT INTO task_runs" in session.statements[1]
    assert session.params[0]["task_name"] == "realtime_risk_guard"
    payload = json.loads(session.params[0]["result"])
    assert payload["loaded_positions"] == 2
    assert payload["tracked_symbols"] == 1
    assert payload["last_blocked_reason"] == "未返回实时行情"
    assert payload["missing_ticks"] == ["000001.SZ"]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_realtime_risk_guard_snapshot_polling_records_missing_tick(monkeypatch):
    from app.realtime import risk_guard

    heartbeat_payloads: list[dict[str, object]] = []

    class FakeProvider:
        def __init__(self, ts_codes):
            self.ts_codes = ts_codes

        async def fetch_snapshot(self):
            return [RealtimeTick(ts_code="000001.SZ", price=Decimal("11.0000"))]

    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeSessionFactory:
        def __call__(self):
            return FakeSessionContext()

    async def fake_unlock(session, *, trade_date):
        return 0

    async def fake_load_positions(session):
        return {
            "000001.SZ": [
                GuardPosition(1, 1, None, "000001.SZ", Decimal("10.0000"), 1000, 1000, Decimal("0.05"), Decimal("0.08"))
            ],
            "000002.SZ": [
                GuardPosition(1, 1, None, "000002.SZ", Decimal("10.0000"), 1000, 1000, Decimal("0.05"), Decimal("0.08"))
            ],
        }

    async def fake_handle_tick(self, tick, *, trade_date):
        return []

    async def fake_heartbeat(session, **kwargs):
        heartbeat_payloads.append(kwargs)

    async def fake_sleep(_seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(risk_guard, "EastMoneyRealtimeProvider", FakeProvider)
    monkeypatch.setattr(risk_guard, "unlock_t1_positions", fake_unlock)
    monkeypatch.setattr(risk_guard, "load_guard_positions", fake_load_positions)
    monkeypatch.setattr(risk_guard, "write_risk_guard_heartbeat", fake_heartbeat)
    monkeypatch.setattr(risk_guard.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(RealtimeRiskGuard, "handle_tick", fake_handle_tick)

    guard = RealtimeRiskGuard(session_factory=FakeSessionFactory(), refresh_interval_seconds=0.01)  # type: ignore[arg-type]

    with pytest.raises(asyncio.CancelledError):
        await guard.run_snapshot_polling(trade_date=date(2026, 5, 22))

    assert heartbeat_payloads[0]["loaded_positions"] == 2
    assert heartbeat_payloads[0]["tracked_symbols"] == 2
    assert heartbeat_payloads[0]["last_blocked_reason"] == "未返回实时行情"
    assert heartbeat_payloads[0]["missing_ticks"] == ["000002.SZ"]


@pytest.mark.asyncio
async def test_daily_sim_trading_buy_t1_sell_nav_closed_loop():
    buy_trade_date = date(2026, 5, 21)
    sell_trade_date = date(2026, 5, 22)
    buy_order = order_row(id=201, signal_id=101, volume=9900, frozen_amount=Decimal("99025.7400"), user_id=1, config={})
    buy_position_locked = {
        "id": 3,
        "account_id": 1,
        "ts_code": "000001.SZ",
        "shares": 9900,
        "available_shares": 0,
        "frozen_shares": 0,
        "avg_cost": Decimal("10.0026"),
        "current_price": Decimal("10.0000"),
        "market_value": Decimal("99000.0000"),
    }
    buy_position_unlocked = {**buy_position_locked, "available_shares": 9900, "current_price": Decimal("10.2000"), "market_value": Decimal("100980.0000")}
    sell_order = order_row(
        id=301,
        signal_id=102,
        direction="卖出",
        price=Decimal("10.2000"),
        volume=9900,
        frozen_amount=Decimal("0.0000"),
        user_id=1,
        config={},
    )
    session = ScriptedSession(
        [
            FakeResult([account_row()]),
            FakeResult([calendar_row(cal_date=buy_trade_date, pretrade_date=date(2026, 5, 20), nexttrade_date=sell_trade_date)]),
            FakeResult([]),
            FakeResult([kline_row(trade_date=buy_trade_date, close=Decimal("10.0000"))]),
            FakeResult([signal_row(id=101, action="BUY", trade_date=buy_trade_date)]),
            FakeResult([buy_order]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([buy_order]),
            FakeResult([calendar_row(cal_date=buy_trade_date, pretrade_date=date(2026, 5, 20), nexttrade_date=sell_trade_date)]),
            FakeResult([kline_row(trade_date=buy_trade_date, close=Decimal("10.0000"))]),
            FakeResult([
                {
                    "id": 211,
                    "order_id": 201,
                    "account_id": 1,
                    "ts_code": "000001.SZ",
                    "direction": "买入",
                    "price": Decimal("10.0000"),
                    "volume": 9900,
                    "amount": Decimal("99000.0000"),
                    "stamp_tax": Decimal("0.0000"),
                    "commission": Decimal("24.7500"),
                    "transfer_fee": Decimal("0.9900"),
                    "total_fee": Decimal("25.7400"),
                    "trade_time": datetime(2026, 5, 21),
                }
            ]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([account_row(available_cash=Decimal("974.2600"), total_asset=Decimal("99974.2600"))]),
            FakeResult([calendar_row(cal_date=buy_trade_date, pretrade_date=date(2026, 5, 20), nexttrade_date=sell_trade_date)]),
            FakeResult([buy_position_locked]),
            FakeResult([kline_row(trade_date=buy_trade_date, close=Decimal("10.0000"))]),
            FakeResult([signal_row(id=102, action="BLOCKED", signal_type="卖出", trade_date=buy_trade_date)]),
            FakeResult([calendar_row(cal_date=sell_trade_date, pretrade_date=buy_trade_date, nexttrade_date=date(2026, 5, 25))]),
            FakeResult([], rowcount=1),
            FakeResult([account_row(available_cash=Decimal("974.2600"), total_asset=Decimal("99974.2600"))]),
            FakeResult([calendar_row(cal_date=sell_trade_date, pretrade_date=buy_trade_date, nexttrade_date=date(2026, 5, 25))]),
            FakeResult([buy_position_unlocked]),
            FakeResult([kline_row(trade_date=sell_trade_date, close=Decimal("10.2000"), pre_close=Decimal("10.0000"))]),
            FakeResult([signal_row(id=103, action="SELL_ALL", signal_type="卖出", trade_date=sell_trade_date)]),
            FakeResult([sell_order]),
            FakeResult([]),
            FakeResult([sell_order]),
            FakeResult([calendar_row(cal_date=sell_trade_date, pretrade_date=buy_trade_date, nexttrade_date=date(2026, 5, 25))]),
            FakeResult([kline_row(trade_date=sell_trade_date, close=Decimal("10.2000"), pre_close=Decimal("10.0000"))]),
            FakeResult([
                {
                    "id": 311,
                    "order_id": 301,
                    "account_id": 1,
                    "ts_code": "000001.SZ",
                    "direction": "卖出",
                    "price": Decimal("10.2000"),
                    "volume": 9900,
                    "amount": Decimal("100980.0000"),
                    "stamp_tax": Decimal("50.4900"),
                    "commission": Decimal("25.2450"),
                    "transfer_fee": Decimal("1.0098"),
                    "total_fee": Decimal("76.7448"),
                    "trade_time": datetime(2026, 5, 22),
                }
            ]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([], rowcount=1),
            FakeResult([]),
            FakeResult([]),
            FakeResult([account_row(available_cash=Decimal("101877.5152"), total_asset=Decimal("101877.5152"))]),
            FakeResult(scalar=Decimal("0.0000")),
            FakeResult([{"total_asset": Decimal("99974.2600"), "cumulative_nav": Decimal("1.00000000"), "max_drawdown": Decimal("0.00000000")}]),
            FakeResult([
                {
                    "id": 401,
                    "account_id": 1,
                    "nav_date": sell_trade_date,
                    "total_asset": Decimal("101877.5152"),
                    "available_cash": Decimal("101877.5152"),
                    "frozen_cash": Decimal("0.0000"),
                    "position_value": Decimal("0.0000"),
                    "daily_return": Decimal("0.01903745"),
                    "cumulative_nav": Decimal("1.01903745"),
                    "max_drawdown": Decimal("0.00000000"),
                    "created_at": datetime(2026, 5, 22),
                }
            ]),
        ]
    )

    buy_signal = await generate_order_from_signal(
        session,
        user_id=1,
        account_id=1,
        request=SignalOrderRequest(ts_code="000001.SZ", signal_type="买入", trade_date=buy_trade_date),
    )
    buy_match = await match_order(session, user_id=1, order_id=201, trade_date=buy_trade_date)
    same_day_sell = await generate_order_from_signal(
        session,
        user_id=1,
        account_id=1,
        request=SignalOrderRequest(ts_code="000001.SZ", signal_type="卖出", trade_date=buy_trade_date),
    )
    unlocked = await unlock_t1_positions(session, trade_date=sell_trade_date)
    sell_signal = await generate_order_from_signal(
        session,
        user_id=1,
        account_id=1,
        request=SignalOrderRequest(ts_code="000001.SZ", signal_type="卖出", trade_date=sell_trade_date),
    )
    sell_match = await match_order(session, user_id=1, order_id=301, trade_date=sell_trade_date)
    nav = await snapshot_daily_nav(session, account_id=1, nav_date=sell_trade_date)

    assert buy_signal["action"] == "BUY"
    assert buy_match["status"] == "全部成交"
    assert same_day_sell["action"] == "BLOCKED"
    assert same_day_sell["reason"] == "T+1 当日买入不可卖出"
    assert unlocked == 1
    assert sell_signal["action"] == "SELL_ALL"
    assert sell_match["status"] == "全部成交"
    assert all("DELETE FROM sim_positions" not in statement for statement in session.statements)
    assert nav["total_asset"] == "101877.5152"

    buy_freeze_idx = find_param_index(session, "remark", "买入委托冻结 000001.SZ")
    assert session.params[buy_freeze_idx]["amount"] == Decimal("99025.7400")
    buy_refund_idx = find_statement_index(session, "available_cash = available_cash + :refund")
    assert session.params[buy_refund_idx]["refund"] == Decimal("0.0000")
    buy_position_idx = find_statement_index(session, "account_id, ts_code, shares, available_shares, frozen_shares")
    assert ":volume, 0, 0" in session.statements[buy_position_idx]
    assert session.params[buy_position_idx]["trade_date"] == buy_trade_date
    t1_signal_idx = find_statement_index(session, "INSERT INTO signal_log", start=buy_position_idx + 1)
    assert session.params[t1_signal_idx]["action"] == "BLOCKED"
    assert "T+1 当日买入不可卖出" in session.params[t1_signal_idx]["snapshot"]
    unlock_idx = find_statement_index(session, "WITH today_buys")
    assert session.params[unlock_idx]["trade_date"] == sell_trade_date
    sell_income_idx = find_statement_index(session, "available_cash = available_cash + :net_income")
    sell_position_update_idx = find_statement_index(session, "SET shares = shares - :volume", start=unlock_idx)
    assert "WHEN shares - :volume <= 0" in session.statements[sell_position_update_idx]
    assert session.params[sell_income_idx]["net_income"] == Decimal("100903.2552")
    nav_refresh_idx = find_statement_index(session, "FROM daily_kline dk", start=sell_income_idx)
    nav_upsert_idx = find_statement_index(session, "INSERT INTO sim_daily_nav", start=nav_refresh_idx)
    assert nav_refresh_idx < nav_upsert_idx
    assert session.params[nav_upsert_idx]["position_value"] == Decimal("0.0000")
    assert session.params[nav_upsert_idx]["daily_return"] == Decimal("0.01903745")


@pytest.mark.asyncio
async def test_unlock_t1_unlocks_older_locked_buys():
    session = ScriptedSession([FakeResult([calendar_row(pretrade_date=date(2026, 5, 20))]), FakeResult([], rowcount=2)])

    updated = await unlock_t1_positions(session, trade_date=date(2026, 5, 21))

    assert updated == 2
    assert session.params[1]["trade_date"] == date(2026, 5, 21)
    assert "WITH today_buys AS" in session.statements[1]
    assert "trade_time::DATE = :trade_date" in session.statements[1]
    assert "p.shares - p.frozen_shares - COALESCE(tb.volume, 0)" in session.statements[1]
    assert "WHERE p.shares > 0" in session.statements[1]
    assert "p.available_shares <> s.expected_available" in session.statements[1]


@pytest.mark.asyncio
async def test_nav_snapshot_upserts_total_asset_and_returns_serialized_values():
    session = ScriptedSession(
        [
            FakeResult([], rowcount=1),
            FakeResult([]),
            FakeResult([]),
            FakeResult([account_row(total_asset=Decimal("101000.0000"))]),
            FakeResult(scalar=Decimal("1000.0000")),
            FakeResult([{"total_asset": Decimal("100000.0000"), "cumulative_nav": Decimal("1.00000000"), "max_drawdown": Decimal("0.00000000")}]),
            FakeResult([
                {
                    "id": 1,
                    "account_id": 1,
                    "nav_date": date(2026, 5, 21),
                    "total_asset": Decimal("101000.0000"),
                    "available_cash": Decimal("100000.0000"),
                    "frozen_cash": Decimal("0.0000"),
                    "position_value": Decimal("1000.0000"),
                    "daily_return": Decimal("0.01000000"),
                    "cumulative_nav": Decimal("1.01000000"),
                    "max_drawdown": Decimal("0.00000000"),
                    "created_at": datetime(2026, 5, 21),
                }
            ]),
        ]
    )

    result = await snapshot_daily_nav(session, account_id=1, nav_date=date(2026, 5, 21))

    assert result["total_asset"] == "101000.0000"
    assert session.params[6]["daily_return"] == Decimal("0.01000000")
    assert "FROM daily_kline dk" in session.statements[0]
    assert "ON CONFLICT (account_id, nav_date)" in session.statements[6]


@pytest.mark.asyncio
async def test_nav_snapshot_refreshes_positions_from_daily_close_before_account_assets():
    session = ScriptedSession(
        [
            FakeResult([], rowcount=2),
            FakeResult([]),
            FakeResult([]),
            FakeResult([account_row(total_asset=Decimal("110000.0000"))]),
            FakeResult(scalar=Decimal("10000.0000")),
            FakeResult([]),
            FakeResult([
                {
                    "id": 1,
                    "account_id": 1,
                    "nav_date": date(2026, 5, 21),
                    "total_asset": Decimal("110000.0000"),
                    "available_cash": Decimal("100000.0000"),
                    "frozen_cash": Decimal("0.0000"),
                    "position_value": Decimal("10000.0000"),
                    "daily_return": Decimal("0E-8"),
                    "cumulative_nav": Decimal("1.00000000"),
                    "max_drawdown": Decimal("0E-8"),
                    "created_at": datetime(2026, 5, 21),
                }
            ]),
        ]
    )

    await snapshot_daily_nav(session, account_id=1, nav_date=date(2026, 5, 21))

    assert session.params[0] == {"account_id": 1, "nav_date": date(2026, 5, 21)}
    assert "current_price = dk.close" in session.statements[0]
    assert "market_value = p.shares * dk.close" in session.statements[0]
    assert "profit_rate = CASE" in session.statements[0]
    assert "UPDATE sim_accounts" in session.statements[2]


@pytest.mark.asyncio
async def test_nav_snapshot_keeps_old_position_value_when_daily_kline_missing():
    session = ScriptedSession(
        [
            FakeResult([], rowcount=0),
            FakeResult([]),
            FakeResult([]),
            FakeResult([account_row(total_asset=Decimal("105000.0000"))]),
            FakeResult(scalar=Decimal("5000.0000")),
            FakeResult([]),
            FakeResult([
                {
                    "id": 1,
                    "account_id": 1,
                    "nav_date": date(2026, 5, 21),
                    "total_asset": Decimal("105000.0000"),
                    "available_cash": Decimal("100000.0000"),
                    "frozen_cash": Decimal("0.0000"),
                    "position_value": Decimal("5000.0000"),
                    "daily_return": Decimal("0E-8"),
                    "cumulative_nav": Decimal("1.00000000"),
                    "max_drawdown": Decimal("0E-8"),
                    "created_at": datetime(2026, 5, 21),
                }
            ]),
        ]
    )

    result = await snapshot_daily_nav(session, account_id=1, nav_date=date(2026, 5, 21))

    assert result["position_value"] == "5000.0000"
    assert session.params[0]["nav_date"] == date(2026, 5, 21)
    assert session.commits == 1
