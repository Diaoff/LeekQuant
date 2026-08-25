"""跷跷板实时闭环（模拟盘自动切换）单元测试。

不依赖真实 Postgres：用 AsyncMock 替换下单与内部 DB 读写，覆盖
- 纯函数 _decide_switch_direction 的状态机分支；
- apply_seesaw_transition 的编排（enter/exit/no-op/空账户）；
- _set_account_seesaw_mode 的 config 合并与 JSONB 写回。
"""
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sim import seesaw_switch as sw


class FakeResult:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def one(self):
        return self._one

    def one_or_none(self):
        return self._one


# ── 纯函数：切换方向决策 ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "new_state,prev_state,current_mode,expected",
    [
        ("down", "up", "normal", "enter_defensive"),
        ("down", "neutral", "normal", "enter_defensive"),
        ("up", "down", "defensive", "exit_defensive"),
        ("neutral", "down", "defensive", "exit_defensive"),
        # 已处于 down，prev 也是 down（持续弱势）→ 不重复进入
        ("down", "down", "normal", None),
        # 持续强势
        ("up", "up", "normal", None),
        # 大盘恢复，但账户并未处于避险（模式守卫）→ 不切
        ("up", "down", "normal", None),
        # 进入 down，但账户已是 defensive（遗漏事件后自愈）→ 不切
        ("down", "up", "defensive", None),
    ],
)
def test_decide_switch_direction(new_state, prev_state, current_mode, expected):
    assert sw._decide_switch_direction(new_state, prev_state, current_mode) == expected


# ── 编排：apply_seesaw_transition ──────────────────────────────────────────────


async def _fake_lister(accounts):
    async def _lister(session):
        return accounts
    return _lister


async def _fake_loader(codes):
    async def _loader(session, limit=200):
        return codes
    return _loader


@pytest.mark.asyncio
@patch.object(sw, "_set_account_seesaw_mode", new_callable=AsyncMock)
@patch.object(sw, "_list_positions", new_callable=AsyncMock)
@patch.object(sw, "generate_order_from_signal", new_callable=AsyncMock)
async def test_apply_enters_defensive_equal_weight_buy(
    mock_gen, mock_positions, mock_set_mode
):
    mock_gen.return_value = {"order": {"id": 1}}
    # 账户持有 1 只权益股；避险库 3 只
    mock_positions.return_value = [
        {"ts_code": "000001.SH", "shares": 100, "available_shares": 100}
    ]
    session = MagicMock()
    session.commit = AsyncMock()

    accounts = [{"id": 10, "user_id": 1, "config": {"seesaw_enabled": True, "seesaw_mode": "normal"}}]
    result = await sw.apply_seesaw_transition(
        session,
        new_state="down",
        prev_state="up",
        rules=None,
        trade_date=date(2026, 8, 24),
        account_lister=await _fake_lister(accounts),
        pool_loader=await _fake_loader(["600519.SH", "601398.SH", "000001.SH"]),
    )

    assert result["switched"][0]["direction"] == "enter_defensive"
    assert result["switched"][0]["new_mode"] == "defensive"
    # 1 次卖出（权益）+ 3 次买入（避险库）
    assert mock_gen.call_count == 4

    sell_calls = [c for c in mock_gen.call_args_list if c.kwargs["request"].signal_type == "卖出"]
    buy_calls = [c for c in mock_gen.call_args_list if c.kwargs["request"].signal_type == "买入"]
    assert len(sell_calls) == 1
    assert sell_calls[0].kwargs["request"].ts_code == "000001.SH"
    assert sell_calls[0].kwargs["request"].target_position == Decimal("0")
    assert len(buy_calls) == 3
    # 等权：目标 = 1/3，quantize 到 0.0001
    for c in buy_calls:
        assert c.kwargs["request"].target_position == Decimal("0.3333")
    assert mock_set_mode.call_count == 1
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
@patch.object(sw, "_set_account_seesaw_mode", new_callable=AsyncMock)
@patch.object(sw, "_list_positions", new_callable=AsyncMock)
@patch.object(sw, "generate_order_from_signal", new_callable=AsyncMock)
async def test_apply_exits_defensive_to_cash(mock_gen, mock_positions, mock_set_mode):
    mock_gen.return_value = {"order": {"id": 1}}
    mock_positions.return_value = [
        {"ts_code": "600519.SH", "shares": 200, "available_shares": 200}
    ]
    session = MagicMock()
    session.commit = AsyncMock()

    accounts = [{"id": 11, "user_id": 1, "config": {"seesaw_enabled": True, "seesaw_mode": "defensive"}}]
    result = await sw.apply_seesaw_transition(
        session,
        new_state="up",
        prev_state="down",
        rules=None,
        trade_date=date(2026, 8, 24),
        account_lister=await _fake_lister(accounts),
        pool_loader=await _fake_loader([]),
    )

    assert result["switched"][0]["direction"] == "exit_defensive"
    assert result["switched"][0]["new_mode"] == "normal"
    # 仅清仓避险持仓，不入新仓
    assert mock_gen.call_count == 1
    assert mock_gen.call_args_list[0].kwargs["request"].signal_type == "卖出"
    assert mock_gen.call_args_list[0].kwargs["request"].ts_code == "600519.SH"


@pytest.mark.asyncio
@patch.object(sw, "_set_account_seesaw_mode", new_callable=AsyncMock)
@patch.object(sw, "_list_positions", new_callable=AsyncMock)
@patch.object(sw, "generate_order_from_signal", new_callable=AsyncMock)
async def test_apply_no_switch_when_already_target_mode(
    mock_gen, mock_positions, mock_set_mode
):
    mock_gen.return_value = {"order": {"id": 1}}
    session = MagicMock()
    session.commit = AsyncMock()

    # 大盘恢复，但账户本就是 normal（未避险）→ 不应切换
    accounts = [{"id": 12, "user_id": 1, "config": {"seesaw_enabled": True, "seesaw_mode": "normal"}}]
    result = await sw.apply_seesaw_transition(
        session,
        new_state="up",
        prev_state="down",
        rules=None,
        trade_date=date(2026, 8, 24),
        account_lister=await _fake_lister(accounts),
        pool_loader=await _fake_loader([]),
    )
    assert result["switched"] == []
    assert result["skipped"] == 1
    mock_gen.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@patch.object(sw, "_set_account_seesaw_mode", new_callable=AsyncMock)
@patch.object(sw, "_list_positions", new_callable=AsyncMock)
@patch.object(sw, "generate_order_from_signal", new_callable=AsyncMock)
async def test_apply_no_switch_when_no_eligible_accounts(
    mock_gen, mock_positions, mock_set_mode
):
    session = MagicMock()
    session.commit = AsyncMock()
    result = await sw.apply_seesaw_transition(
        session,
        new_state="down",
        prev_state="up",
        rules=None,
        trade_date=date(2026, 8, 24),
        account_lister=await _fake_lister([]),
        pool_loader=await _fake_loader([]),
    )
    assert result["switched"] == []
    assert result["skipped"] == 0
    mock_gen.assert_not_called()
    # 无切换不提交
    session.commit.assert_not_awaited()


# ── _set_account_seesaw_mode：config 合并与 JSONB 写回 ──────────────────────────


@pytest.mark.asyncio
async def test_set_account_seesaw_mode_merges_config():
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            FakeResult(one={"config": {"seesaw_enabled": True, "fee_config": {"commission_rate": 0.0003}}}),
            FakeResult(),
        ]
    )
    await sw._set_account_seesaw_mode(session, 5, "defensive")

    assert session.execute.call_count == 2
    update_call = session.execute.call_args_list[1]
    params = update_call.kwargs.get("params") or update_call.args[1]
    cfg = params["cfg"]
    assert isinstance(cfg, str)
    assert '"seesaw_mode": "defensive"' in cfg
    # 其他 config 字段保留
    assert '"seesaw_enabled": true' in cfg
    assert "commission_rate" in cfg
    assert params["aid"] == 5
