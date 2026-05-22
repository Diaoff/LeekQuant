from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.sim.service import SignalOrderRequest, generate_order_from_signal, match_order, snapshot_daily_nav, unlock_t1_positions


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

    assert session.params[3]["stamp_tax"] == Decimal("3.0000")
    assert session.params[5]["net_income"] == Decimal("5991.9400")


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
    unlock_idx = find_statement_index(session, "WITH buy_trades")
    assert session.params[unlock_idx]["prev_date"] == buy_trade_date
    sell_income_idx = find_statement_index(session, "available_cash = available_cash + :net_income")
    assert session.params[sell_income_idx]["net_income"] == Decimal("100903.2552")
    nav_refresh_idx = find_statement_index(session, "FROM daily_kline dk", start=sell_income_idx)
    nav_upsert_idx = find_statement_index(session, "INSERT INTO sim_daily_nav", start=nav_refresh_idx)
    assert nav_refresh_idx < nav_upsert_idx
    assert session.params[nav_upsert_idx]["position_value"] == Decimal("0.0000")
    assert session.params[nav_upsert_idx]["daily_return"] == Decimal("0.01903745")


@pytest.mark.asyncio
async def test_unlock_t1_uses_previous_trade_date():
    session = ScriptedSession([FakeResult([calendar_row(pretrade_date=date(2026, 5, 20))]), FakeResult([], rowcount=2)])

    updated = await unlock_t1_positions(session, trade_date=date(2026, 5, 21))

    assert updated == 2
    assert session.params[1]["prev_date"] == date(2026, 5, 20)
    assert "LEAST(p.shares, p.available_shares + b.volume)" in session.statements[1]


@pytest.mark.asyncio
async def test_nav_snapshot_upserts_total_asset_and_returns_serialized_values():
    session = ScriptedSession(
        [
            FakeResult([], rowcount=1),
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
    assert session.params[5]["daily_return"] == Decimal("0.01000000")
    assert "FROM daily_kline dk" in session.statements[0]
    assert "ON CONFLICT (account_id, nav_date)" in session.statements[5]


@pytest.mark.asyncio
async def test_nav_snapshot_refreshes_positions_from_daily_close_before_account_assets():
    session = ScriptedSession(
        [
            FakeResult([], rowcount=2),
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
    assert "UPDATE sim_accounts" in session.statements[1]


@pytest.mark.asyncio
async def test_nav_snapshot_keeps_old_position_value_when_daily_kline_missing():
    session = ScriptedSession(
        [
            FakeResult([], rowcount=0),
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
