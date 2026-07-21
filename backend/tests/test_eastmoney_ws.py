"""Tests for EastMoney WebSocket protocol adapter."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.realtime.eastmoney_ws import EastMoneyWSClient
from app.realtime.models import RealtimeTick


def test_secid_mapping_sz() -> None:
    assert EastMoneyWSClient._secid("000001.SZ") == "0.000001"


def test_secid_mapping_sh() -> None:
    assert EastMoneyWSClient._secid("600000.SH") == "1.600000"


def test_secid_mapping_bj() -> None:
    assert EastMoneyWSClient._secid("430047.BJ") == "0.430047"


def test_build_subscribe_message_format() -> None:
    client = EastMoneyWSClient(["000001.SZ", "600000.SH"])
    msg = json.loads(client._build_subscribe_message())

    assert "uid" in msg
    assert msg["deepest"] == 10
    assert "f2" in msg["fields"]
    assert "f31" in msg["fields"]
    assert msg["secids"] == "0.000001,1.600000"


def test_parse_message_valid_tick() -> None:
    client = EastMoneyWSClient(["000001.SZ"])
    row = {
        "f12": "000001",
        "f13": 0,
        "f2": 10.68,
        "f3": -0.19,
        "f4": -0.02,
        "f5": 552123,
        "f6": 589901234,
        "f31": 10.67,
        "f32": 10.68,
    }
    raw = json.dumps({"data": {"diff": [row]}})

    result = client._parse_message(raw)

    assert len(result) == 1
    tick = result[0]
    assert tick.ts_code == "000001.SZ"
    assert tick.price == Decimal("10.68")
    assert tick.change == Decimal("-0.02")
    assert tick.change_pct == Decimal("-0.19")
    assert tick.volume == 552123
    assert tick.amount == Decimal("589901234")
    assert tick.bid1 == Decimal("10.67")
    assert tick.ask1 == Decimal("10.68")


def test_parse_message_heartbeat_ignored() -> None:
    client = EastMoneyWSClient(["000001.SZ"])

    assert client._parse_message(json.dumps({"type": "ping"})) == []
    assert client._parse_message(json.dumps({"Type": "pong"})) == []
    assert client._parse_message(json.dumps({"type": "heartbeat"})) == []


def test_parse_message_bad_json_returns_none() -> None:
    client = EastMoneyWSClient(["000001.SZ"])

    assert client._parse_message("{bad json") == []
    assert client._parse_message("") == []


def test_parse_message_bytes_input() -> None:
    client = EastMoneyWSClient(["000001.SZ"])
    raw = json.dumps({"data": {"diff": [{"f12": "000001", "f13": 0, "f2": 10.0, "f4": 0.1, "f3": 1.0, "f5": 100, "f6": 1000}]}})

    result = client._parse_message(raw.encode("utf-8"))

    assert len(result) == 1
    assert result[0].ts_code == "000001.SZ"


def test_parse_message_empty_diff_returns_none() -> None:
    client = EastMoneyWSClient(["000001.SZ"])

    assert client._parse_message(json.dumps({"data": {"diff": []}})) == []
    assert client._parse_message(json.dumps({"data": {}})) == []
    assert client._parse_message(json.dumps({})) == []


def test_parse_message_filters_unsubscribed_codes() -> None:
    client = EastMoneyWSClient(["000001.SZ"])
    raw = json.dumps({"data": {"diff": [{"f12": "600000", "f13": 1, "f2": 15.0, "f4": 0.5, "f3": 3.0, "f5": 200, "f6": 3000}]}})

    result = client._parse_message(raw)

    assert result == []


def test_parse_message_non_dict_msg_returns_none() -> None:
    client = EastMoneyWSClient(["000001.SZ"])

    assert client._parse_message(json.dumps("hello")) == []
    assert client._parse_message(json.dumps(42)) == []


def test_parse_message_missing_price_returns_none() -> None:
    client = EastMoneyWSClient(["000001.SZ"])
    raw = json.dumps({"data": {"diff": [{"f12": "000001", "f13": 0}]}})

    result = client._parse_message(raw)

    assert result == []


@pytest.mark.asyncio
async def test_stream_yields_valid_ticks() -> None:
    client = EastMoneyWSClient(["000001.SZ"])

    tick_data = {
        "data": {
            "diff": [
                {
                    "f12": "000001",
                    "f13": 0,
                    "f2": 10.68,
                    "f3": -0.19,
                    "f4": -0.02,
                    "f5": 552123,
                    "f6": 589901234,
                    "f31": 10.67,
                    "f32": 10.68,
                }
            ]
        }
    }

    messages = [json.dumps(tick_data), json.dumps(tick_data)]

    class FakeWS:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, msg):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            if messages:
                return messages.pop(0)
            raise StopAsyncIteration

    with patch("app.realtime.eastmoney_ws.websockets.connect", return_value=FakeWS()):
        ticks = []
        async for tick in client.stream():
            ticks.append(tick)
            if len(ticks) >= 2:
                break

    assert len(ticks) == 2
    assert all(t.ts_code == "000001.SZ" for t in ticks)


@pytest.mark.asyncio
async def test_ws_producer_publishes_to_bus() -> None:
    from app.realtime.ws_producer import run_ws_producer

    published: list[RealtimeTick] = []

    class FakeBus:
        async def publish(self, tick: RealtimeTick) -> int:
            published.append(tick)
            return 1

    tick_data = {
        "data": {
            "diff": [
                {
                    "f12": "000001",
                    "f13": 0,
                    "f2": 10.68,
                    "f3": -0.19,
                    "f4": -0.02,
                    "f5": 552123,
                    "f6": 589901234,
                    "f31": 10.67,
                    "f32": 10.68,
                }
            ]
        }
    }

    messages = [json.dumps(tick_data), json.dumps(tick_data)]

    class FakeWS:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, msg):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            if messages:
                return messages.pop(0)
            raise StopAsyncIteration

    with patch("app.realtime.eastmoney_ws.websockets.connect", return_value=FakeWS()):
        try:
            await asyncio.wait_for(run_ws_producer(["000001.SZ"], bus=FakeBus()), timeout=2.0)
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass

    assert len(published) >= 1
    assert published[0].ts_code == "000001.SZ"
