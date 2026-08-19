from __future__ import annotations

import json
import signal
import threading
from datetime import date, timedelta
from decimal import Decimal
from types import FrameType
from typing import Any, Protocol
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.data.models import DailyKline, StockBasic, StockFundamental, TradeCalendarDay
from app.data.normalizers import (
    dataframe_records,
    normalize_daily_kline,
    normalize_stock_basic,
    normalize_stock_fundamental,
    normalize_ts_code,
    normalize_trade_calendar,
)
import logging
logger = logging.getLogger(__name__)


class DataProviderError(RuntimeError):
    pass


class ProviderCapability:
    STOCK_BASIC = "stock_basic"
    TRADE_CALENDAR = "trade_calendar"
    DAILY_KLINE = "daily_kline"
    FUNDAMENTALS = "fundamentals"
    REALTIME_QUOTE = "realtime_quote"


METHOD_CAPABILITIES = {
    "fetch_stock_basic": ProviderCapability.STOCK_BASIC,
    "fetch_trade_calendar": ProviderCapability.TRADE_CALENDAR,
    "fetch_daily_kline": ProviderCapability.DAILY_KLINE,
    "fetch_stock_fundamentals": ProviderCapability.FUNDAMENTALS,
}


class DataProvider(Protocol):
    name: str
    display_name: str
    capabilities: frozenset[str]
    priority_default: int

    def fetch_stock_basic(self) -> list[StockBasic]: ...

    def fetch_trade_calendar(self, start_date: date, end_date: date) -> list[TradeCalendarDay]: ...

    def fetch_daily_kline(self, ts_code: str, start_date: date, end_date: date) -> list[DailyKline]: ...

    def fetch_stock_fundamentals(
        self, ts_codes: list[str], start_date: date, end_date: date
    ) -> list[StockFundamental]: ...


PROVIDER_REGISTRY: dict[str, type[DataProvider]] = {}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
MOOTDX_DAILY_TIMEOUT_SECONDS = 12


def register_provider(provider_cls: type[DataProvider]) -> type[DataProvider]:
    existing = PROVIDER_REGISTRY.get(provider_cls.name)
    if existing is not None and existing is not provider_cls:
        raise RuntimeError(f"duplicate data provider registered: {provider_cls.name}")
    PROVIDER_REGISTRY[provider_cls.name] = provider_cls
    return provider_cls


def provider_metadata() -> list[dict[str, Any]]:
    providers = sorted(PROVIDER_REGISTRY.values(), key=lambda cls: cls.priority_default)
    return [
        {
            "name": cls.name,
            "display_name": cls.display_name,
            "priority": cls.priority_default,
            "enabled": getattr(cls, "enabled_default", True),
            "capabilities": sorted(cls.capabilities),
        }
        for cls in providers
    ]


def provider_supports(provider: DataProvider | type[DataProvider], capability: str) -> bool:
    capabilities = getattr(provider, "capabilities", None)
    if capabilities is None:
        return True
    return capability in capabilities


def _unsupported(provider_name: str, capability: str) -> DataProviderError:
    return DataProviderError(f"{provider_name} does not support {capability}")


def _date_arg(value: date) -> str:
    return value.strftime("%Y%m%d")


def _parse_ohlc_adaptive(item: list) -> tuple[float, float, float, float] | None:
    """从腾讯 K 线行自适应识别 (open, close, high, low) 列序。

    个股 qfqday 实测为 [日期,开,收,高,低,量]；指数 day 可能为 [日期,开,高,低,收,量]
    等变体。按 high>=max(o,c) 且 low<=min(o,c) 的 OHLC 合法性遍历候选顺序，返回
    第一个合法解；全部不合法（停牌日 0/负值、或列序完全不匹配）返回 None。
    """
    try:
        f = [float(x) for x in item[1:5]]
    except (ValueError, TypeError):
        return None
    # 候选列序：(open, close, high, low) 在 item[1:5] 中的位置组合
    candidates = (
        (f[0], f[1], f[2], f[3]),  # 开,收,高,低 (个股 qfqday)
        (f[0], f[3], f[1], f[2]),  # 开,高,低,收 → (o,f[0] c,f[3] h,f[1] l,f[2])
        (f[0], f[1], f[3], f[2]),  # 开,收,低,高 → (o,f[0] c,f[1] h,f[3] l,f[2])
        (f[0], f[2], f[1], f[3]),  # 开,高,收,低 → (o,f[0] c,f[2] h,f[1] l,f[3])
        (f[0], f[3], f[2], f[1]),  # 开,低,收,高
        (f[0], f[2], f[3], f[1]),  # 开,低,高,收
    )
    for o, c, h, l in candidates:
        if o > 0 and c > 0 and h > 0 and l > 0 and h >= o and h >= c and l <= o and l <= c:
            return o, c, h, l
    return None


def _http_json(url: str, params: dict[str, Any] | None = None, timeout: int = 15) -> dict[str, Any]:
    query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
    full_url = f"{url}?{query}" if query else url
    # 东财 push2/push2his 接口对无 Referer 的请求直接断开连接（RemoteDisconnected）——
    # 必须带 quote 页 Referer + 浏览器 UA 才能通过风控。
    headers = {
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "*/*",
    }
    request = Request(full_url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="ignore")
    except URLError as exc:
        # Wrap as ConnectionError so fetcher's _RETRYABLE catches it (was DataProviderError
        # which was non-retryable, masking transient network errors).
        from requests.exceptions import ConnectionError as ReqConnectionError
        raise ReqConnectionError(f"http request failed for {url}: {exc}") from exc

    body = body.strip()
    if body.startswith(("jQuery", "callback")):
        body = body[body.find("(") + 1 : body.rfind(")")]
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise DataProviderError(f"invalid json response from {url}: {body[:120]}") from exc


def _run_with_alarm_timeout(call, timeout_seconds: int, message: str):
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM"):
        return call()

    def raise_timeout(_signum: int, _frame: FrameType | None) -> None:
        raise TimeoutError(message)

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        return call()
    except TimeoutError as exc:
        raise DataProviderError(message) from exc
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def _market_suffix_from_symbol(symbol: str) -> str:
    return "SH" if symbol.startswith(("6", "9")) else "SZ"


def _eastmoney_secid(ts_code: str) -> str:
    symbol, suffix = normalize_ts_code(ts_code).split(".", 1)
    market = "1" if suffix == "SH" else "0"
    return f"{market}.{symbol}"


def _decimal_yi(value: Any) -> Decimal | None:
    if value in (None, "", "-"):
        return None
    return Decimal(str(value)) * Decimal("100000000")


def _market_value(value: Any) -> Any:
    return None if value in (None, "", "-") else value


@register_provider
class EastMoneyHttpProvider:
    """HTTP snapshot provider for EastMoney.

    Used as a degraded fallback when primary A-share providers (AData/Baostock/AkShare)
    are unavailable. Design contract: priority order AData→Baostock→AkShare→EastMoney.
    """
    name = "eastmoney_http"
    priority_default = 10
    display_name = "EastMoney HTTP"
    capabilities = frozenset({
        ProviderCapability.STOCK_BASIC,
        ProviderCapability.DAILY_KLINE,
        ProviderCapability.FUNDAMENTALS,
    })

    def fetch_stock_basic(self) -> list[StockBasic]:
        rows: list[dict[str, Any]] = []
        page = 1
        page_size = 1000
        while True:
            payload = _http_json(
                "https://push2.eastmoney.com/api/qt/clist/get",
                {
                    "pn": page,
                    "pz": page_size,
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f12",
                    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                    "fields": "f12,f13,f14,f100",
                },
            )
            data = payload.get("data") or {}
            diff = data.get("diff") or []
            if not diff:
                break
            rows.extend(diff)
            if len(rows) >= int(data.get("total") or 0):
                break
            page += 1

        records: list[StockBasic] = []
        for row in rows:
            symbol = str(row.get("f12") or "").strip().zfill(6)
            name = str(row.get("f14") or "").strip()
            if not symbol or not name:
                continue
            suffix = "SH" if int(row.get("f13") or 0) == 1 else _market_suffix_from_symbol(symbol)
            records.append(
                normalize_stock_basic(
                    {
                        "ts_code": f"{symbol}.{suffix}",
                        "name": name,
                        "industry": row.get("f100"),
                    },
                    self.name,
                )
            )
        return records

    def fetch_trade_calendar(self, start_date: date, end_date: date) -> list[TradeCalendarDay]:
        raise _unsupported(self.name, ProviderCapability.TRADE_CALENDAR)

    def fetch_daily_kline(self, ts_code: str, start_date: date, end_date: date) -> list[DailyKline]:
        payload = _http_json(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            {
                "secid": _eastmoney_secid(ts_code),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "fqt": "1",  # 前复权 (qfq): returns adjusted prices directly
                "beg": _date_arg(start_date),
                "end": _date_arg(end_date),
            },
        )
        klines = ((payload.get("data") or {}).get("klines") or []) if isinstance(payload, dict) else []
        records: list[DailyKline] = []
        for item in klines:
            parts = str(item).split(",")
            if len(parts) < 11:
                continue
            row = {
                "trade_date": parts[0],
                "open": parts[1],
                "close": parts[2],
                "high": parts[3],
                "low": parts[4],
                "volume": parts[5],
                "amount": parts[6],
                "turnover_rate": parts[10],
            }
            records.append(normalize_daily_kline(row, self.name, ts_code=ts_code))
        return records

    def fetch_stock_fundamentals(
        self, ts_codes: list[str], start_date: date, end_date: date
    ) -> list[StockFundamental]:
        records: list[StockFundamental] = []
        for ts_code in ts_codes:
            payload = _http_json(
                "https://push2.eastmoney.com/api/qt/stock/get",
                {
                    "secid": _eastmoney_secid(ts_code),
                    "fields": "f57,f58,f116,f117,f162,f167",
                },
            )
            data = payload.get("data") or {}
            if not data:
                continue
            row = {
                "ts_code": ts_code,
                "report_date": end_date,
                "pe_ttm": _market_value(data.get("f162")),
                "pb": _market_value(data.get("f167")),
                "market_cap": _market_value(data.get("f116")),
                "float_market_cap": _market_value(data.get("f117")),
            }
            records.append(normalize_stock_fundamental(row, self.name, ts_code=ts_code, report_date=end_date))
        return records

    def fetch_realtime_quote(self, ts_codes: list[str]) -> dict[str, Decimal]:
        if not ts_codes:
            return {}
        wanted = set(ts_codes)
        result: dict[str, Decimal] = {}
        page = 1
        page_size = 100
        total: int | None = None
        while len(result) < len(wanted):
            payload = _http_json(
                "https://push2.eastmoney.com/api/qt/clist/get",
                {
                    "pn": page,
                    "pz": page_size,
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f12",
                    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                    "fields": "f12,f2",
                },
            )
            data = payload.get("data") or {}
            if total is None:
                total = int(data.get("total") or 0)
            diff = data.get("diff") or []
            if not diff:
                break
            for item in diff:
                symbol = str(item.get("f12") or "").strip().zfill(6)
                ts_code = None
                for candidate in wanted:
                    if candidate.startswith(symbol):
                        ts_code = candidate
                        break
                if ts_code is None or ts_code in result:
                    continue
                try:
                    price = Decimal(str(item.get("f2", "0")))
                    if price > 0:
                        result[ts_code] = price
                except Exception:
                    logger.debug("silent except in fetch_realtime_quote")
                    pass
            if page * page_size >= total:
                break
            page += 1
            import time
            time.sleep(0.3)
        return result


@register_provider
class TencentHttpProvider:
    name = "tencent_http"
    display_name = "Tencent Finance HTTP"
    capabilities = frozenset({
        ProviderCapability.DAILY_KLINE,
        ProviderCapability.FUNDAMENTALS,
        ProviderCapability.REALTIME_QUOTE,
    })
    priority_default = 20

    def fetch_stock_basic(self) -> list[StockBasic]:
        raise _unsupported(self.name, ProviderCapability.STOCK_BASIC)

    def fetch_trade_calendar(self, start_date: date, end_date: date) -> list[TradeCalendarDay]:
        raise _unsupported(self.name, ProviderCapability.TRADE_CALENDAR)

    def fetch_daily_kline(self, ts_code: str, start_date: date, end_date: date) -> list[DailyKline]:
        """腾讯 ifzq fqkline 日 K（前复权）。

        2026-08-19 实测：东财 push2his 对本机 IP 风控断连(RemoteDisconnected)，
        Baostock/新浪/通达信主站1 均不可达，唯腾讯 qt.gtimg/ifzq.gtimg.cn 可用——
        故把腾讯补为 K 线回退源。返回 ``qfqday`` 每行 [date, open, close, high, low,
        volume(手), ...]，前复权价直接使用（adj_factor 留 None）。

        ⚠️ 腾讯接口单次最多返回 640 条（超出时只返回区间内最近 640 根），
        拉长历史（如 2017 上市至今）必须**按 2 年窗口分段请求**再拼接去重。
        """
        symbol = ts_code.split(".", 1)[0]
        prefix = "sh" if ts_code.upper().endswith("SH") else "sz"
        key = f"{prefix}{symbol}"

        def _fetch_segment(seg_start: date, seg_end: date) -> list[list]:
            url = (
                "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
                f"?param={key},day,{seg_start.isoformat()},{seg_end.isoformat()},640,qfq"
            )
            payload = _http_json(url, timeout=10)
            data = (payload.get("data") or {}).get(key) or {}
            return data.get("qfqday") or data.get("day") or []

        seg_start = start_date
        raw: list[list] = []
        while seg_start <= end_date:
            seg_end = min(seg_start + timedelta(days=730), end_date)  # 2年窗口 ≤ ~490 交易日
            raw.extend(_fetch_segment(seg_start, seg_end))
            seg_start = seg_end + timedelta(days=1)

        # 去重（分段边界可能重叠；腾讯可能重复返回首末行）
        seen_dates: set[str] = set()
        records: list[DailyKline] = []
        for item in raw:
            if not item or len(item) < 6:
                continue
            d = str(item[0])
            if d in seen_dates:
                continue
            seen_dates.add(d)
            try:
                vol = int(float(item[5])) * 100
            except (ValueError, TypeError):
                continue
            # OHLC 列序自适应：个股 qfqday=[日期,开,收,高,低,量]，指数 day 可能
            # 是 [日期,开,高,低,收,量] 等变体——按"high>=max(o,c) 且 low<=min(o,c)"
            # 的合法性自动识别正确顺序；全不合法(停牌0/负)则跳过该行。
            ohlc = _parse_ohlc_adaptive(item)
            if ohlc is None:
                continue
            o, c, h, l = ohlc
            try:
                records.append(normalize_daily_kline({
                    "trade_date": d,
                    "open": str(o),
                    "close": str(c),
                    "high": str(h),
                    "low": str(l),
                    "volume": vol,
                }, self.name, ts_code=ts_code))
            except (ValueError, TypeError):
                continue
        return records

    def fetch_stock_fundamentals(
        self, ts_codes: list[str], start_date: date, end_date: date
    ) -> list[StockFundamental]:
        if not ts_codes:
            return []
        prefixed = []
        normalized_codes = []
        for ts_code in ts_codes:
            symbol, suffix = normalize_ts_code(ts_code).split(".", 1)
            normalized_codes.append(f"{symbol}.{suffix}")
            prefixed.append(("sh" if suffix == "SH" else "sz") + symbol)

        body = self._request_tencent(prefixed)
        records: list[StockFundamental] = []
        wanted = {code.split(".", 1)[0]: code for code in normalized_codes}
        for line in body.strip().split(";"):
            if not line.strip() or '"' not in line:
                continue
            key = line.split("=")[0].split("_")[-1]
            symbol = key[2:]
            ts_code = wanted.get(symbol)
            vals = line.split('"')[1].split("~")
            if ts_code is None or len(vals) < 53:
                continue
            row = {
                "ts_code": ts_code,
                "report_date": end_date,
                "pe_ttm": _market_value(vals[39]),
                "pb": _market_value(vals[46]),
                "market_cap": _decimal_yi(vals[44]),
                "float_market_cap": _decimal_yi(vals[45]),
            }
            records.append(normalize_stock_fundamental(row, self.name, ts_code=ts_code, report_date=end_date))
        return records

    def fetch_realtime_quote(self, ts_codes: list[str]) -> dict[str, Decimal]:
        if not ts_codes:
            return {}
        prefixed: list[str] = []
        wanted: dict[str, str] = {}
        for ts_code in ts_codes:
            symbol, suffix = normalize_ts_code(ts_code).split(".", 1)
            wanted[symbol] = ts_code
            prefixed.append(("sh" if suffix == "SH" else "sz") + symbol)

        body = self._request_tencent(prefixed)
        result: dict[str, Decimal] = {}
        for line in body.strip().split(";"):
            if not line.strip() or '"' not in line:
                continue
            key = line.split("=")[0].split("_")[-1]
            symbol = key[2:]
            ts_code = wanted.get(symbol)
            vals = line.split('"')[1].split("~")
            if ts_code is None or len(vals) < 4:
                continue
            try:
                price = Decimal(vals[3])
                if price > 0:
                    result[ts_code] = price
            except Exception:
                logger.debug("silent except in fetch_realtime_quote")
                pass
        return result

    @staticmethod
    def _request_tencent(prefixed: list[str]) -> str:
        chunk_size = 200
        parts: list[str] = []
        for i in range(0, len(prefixed), chunk_size):
            chunk = prefixed[i : i + chunk_size]
            url = "https://qt.gtimg.cn/q=" + ",".join(chunk)
            request = Request(url, headers={"User-Agent": UA})
            try:
                with urlopen(request, timeout=10) as resp:
                    parts.append(resp.read().decode("gbk", errors="ignore"))
            except URLError:
                logger.debug("silent except in _request_tencent")
                pass
        return ";".join(parts)


@register_provider
class MootdxProvider:
    name = "mootdx"
    display_name = "Mootdx"
    capabilities = frozenset({ProviderCapability.DAILY_KLINE})
    priority_default = 5
    enabled_default = False

    def fetch_stock_basic(self) -> list[StockBasic]:
        raise _unsupported(self.name, ProviderCapability.STOCK_BASIC)

    def fetch_trade_calendar(self, start_date: date, end_date: date) -> list[TradeCalendarDay]:
        raise _unsupported(self.name, ProviderCapability.TRADE_CALENDAR)

    def fetch_daily_kline(self, ts_code: str, start_date: date, end_date: date) -> list[DailyKline]:
        try:
            from mootdx.quotes import Quotes  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DataProviderError("mootdx is not installed") from exc

        symbol = normalize_ts_code(ts_code).split(".", 1)[0]
        offset = min(max((end_date - start_date).days + 30, 10), 800)
        try:
            frame = _run_with_alarm_timeout(
                lambda: Quotes.factory(market="std").bars(symbol=symbol, frequency=9, offset=offset),
                MOOTDX_DAILY_TIMEOUT_SECONDS,
                f"mootdx daily kline timed out after {MOOTDX_DAILY_TIMEOUT_SECONDS}s for {ts_code}",
            )
        except DataProviderError:
            raise
        except Exception as exc:
            raise DataProviderError(f"mootdx daily kline failed for {ts_code}: {exc}") from exc
        if frame is None or frame.empty:
            return []

        records: list[DailyKline] = []
        for row in dataframe_records(frame):
            trade_date = row.get("datetime")
            parsed_date = normalize_daily_kline(
                {
                    "trade_date": trade_date,
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "volume": int(float(row.get("vol") or row.get("volume") or 0) * 100),
                    "amount": row.get("amount"),
                },
                self.name,
                ts_code=ts_code,
            )
            if start_date <= parsed_date.trade_date <= end_date:
                records.append(parsed_date)
        return records

    def fetch_stock_fundamentals(
        self, ts_codes: list[str], start_date: date, end_date: date
    ) -> list[StockFundamental]:
        raise _unsupported(self.name, ProviderCapability.FUNDAMENTALS)


class ADataProvider:
    name = "adata"
    display_name = "AData"
    capabilities = frozenset({ProviderCapability.STOCK_BASIC, ProviderCapability.DAILY_KLINE})
    priority_default = 1

    def fetch_stock_basic(self) -> list[StockBasic]:
        try:
            import adata  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DataProviderError("adata is not installed") from exc

        frame = adata.stock.info.all_code()
        return [normalize_stock_basic(row, self.name) for row in dataframe_records(frame)]

    def fetch_trade_calendar(self, start_date: date, end_date: date) -> list[TradeCalendarDay]:
        raise DataProviderError("adata trade calendar adapter is not available")

    def fetch_daily_kline(self, ts_code: str, start_date: date, end_date: date) -> list[DailyKline]:
        try:
            import adata  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DataProviderError("adata is not installed") from exc

        symbol = ts_code.split(".", 1)[0]
        # adjust_type=1: 前复权 (qfq) prices (adata >= 2.9 API；旧版 adj=True 已废弃，
        # 会抛 TypeError: get_market() got an unexpected keyword argument 'adj')。
        # AData 不单独暴露 adj_factor，adj_factor 保持 None —— 回测直接用 qfq 价格，
        # 修复除权日产生假阴线、误触发卖出信号的问题。
        frame = adata.stock.market.get_market(
            stock_code=symbol,
            k_type=1,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            adjust_type=1,
        )
        return [normalize_daily_kline(row, self.name, ts_code=ts_code) for row in dataframe_records(frame)]

    def fetch_stock_fundamentals(
        self, ts_codes: list[str], start_date: date, end_date: date
    ) -> list[StockFundamental]:
        raise DataProviderError("adata fundamentals adapter is not available")


class BaostockProvider:
    name = "baostock"
    display_name = "Baostock"
    capabilities = frozenset({
        ProviderCapability.STOCK_BASIC,
        ProviderCapability.TRADE_CALENDAR,
        ProviderCapability.DAILY_KLINE,
        ProviderCapability.FUNDAMENTALS,
    })
    priority_default = 2

    def __init__(self) -> None:
        # baostock module maintains global login state; serialize login/logout
        # across threads (provider is used as singleton in data/service.py).
        self._lock = threading.Lock()

    def _run(self, fn):
        try:
            import baostock as bs  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DataProviderError("baostock is not installed") from exc

        with self._lock:
            login_result = bs.login()
            if getattr(login_result, "error_code", "0") != "0":
                raise DataProviderError(f"baostock login failed: {getattr(login_result, 'error_msg', '')}")
            try:
                return fn(bs)
            finally:
                bs.logout()

    def fetch_stock_basic(self) -> list[StockBasic]:
        def query(bs):
            result = bs.query_all_stock()
            rows: list[dict[str, str]] = []
            while result.error_code == "0" and result.next():
                rows.append(dict(zip(result.fields, result.get_row_data(), strict=False)))
            if result.error_code != "0":
                raise DataProviderError(f"baostock stock basic failed: {result.error_msg}")
            return rows

        rows = self._run(query)
        return [normalize_stock_basic(row, self.name) for row in rows if row.get("code")]

    def fetch_trade_calendar(self, start_date: date, end_date: date) -> list[TradeCalendarDay]:
        def query(bs):
            result = bs.query_trade_dates(start_date.isoformat(), end_date.isoformat())
            rows: list[dict[str, str]] = []
            while result.error_code == "0" and result.next():
                rows.append(dict(zip(result.fields, result.get_row_data(), strict=False)))
            if result.error_code != "0":
                raise DataProviderError(f"baostock trade calendar failed: {result.error_msg}")
            return rows

        return [normalize_trade_calendar(row, self.name) for row in self._run(query)]

    def fetch_daily_kline(self, ts_code: str, start_date: date, end_date: date) -> list[DailyKline]:
        def query(bs):
            code, suffix = ts_code.split(".", 1)
            bs_code = f"{suffix.lower()}.{code}"
            # adjustflag="2" = 前复权 (qfq): returns adjusted prices directly.
            # adj_factor included for audit/reference (e.g., converting between
            # raw and adjusted prices for display). Backtest uses the qfq
            # prices as-is — multiplying again would double-adjust.
            fields = "date,code,open,high,low,close,preclose,volume,amount,turn,tradestatus,adj_factor"
            result = bs.query_history_k_data_plus(
                bs_code,
                fields,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                frequency="d",
                adjustflag="2",
            )
            rows: list[dict[str, str]] = []
            while result.error_code == "0" and result.next():
                rows.append(dict(zip(result.fields, result.get_row_data(), strict=False)))
            if result.error_code != "0":
                raise DataProviderError(f"baostock kline failed: {result.error_msg}")
            return rows

        normalized: list[DailyKline] = []
        for row in self._run(query):
            row["trade_date"] = row.get("date")
            row["pre_close"] = row.get("preclose")
            row["turnover_rate"] = row.get("turn")
            # adj_factor key already matches normalize_daily_kline's lookup
            raw_tradestatus = row.get("tradestatus")
            if raw_tradestatus is None or str(raw_tradestatus).strip() == "":
                is_suspended: bool | None = None
            else:
                # baostock: tradestatus "1" = trading, "0" = suspended
                is_suspended = str(raw_tradestatus).strip() != "1"
            normalized.append(normalize_daily_kline(row, self.name, ts_code=ts_code, is_suspended=is_suspended))
        return normalized

    def fetch_stock_fundamentals(
        self, ts_codes: list[str], start_date: date, end_date: date
    ) -> list[StockFundamental]:
        records: list[StockFundamental] = []

        def query(bs):
            rows: list[dict[str, str]] = []
            for ts_code in ts_codes:
                code, suffix = ts_code.split(".", 1)
                bs_code = f"{suffix.lower()}.{code}"
                result = bs.query_history_k_data_plus(
                    bs_code,
                    "date,code,peTTM,pbMRQ,psTTM,pcfNcfTTM",
                    start_date=start_date.isoformat(),
                    end_date=end_date.isoformat(),
                    frequency="d",
                    adjustflag="3",
                )
                while result.error_code == "0" and result.next():
                    row = dict(zip(result.fields, result.get_row_data(), strict=False))
                    row["ts_code"] = ts_code
                    row["report_date"] = row.get("date")
                    rows.append(row)
                if result.error_code != "0":
                    raise DataProviderError(f"baostock fundamentals failed for {ts_code}: {result.error_msg}")
            return rows

        for row in self._run(query):
            records.append(normalize_stock_fundamental(row, self.name))
        return records

    # ---- 季频财务数据（盈利能力 / 成长能力）----
    # Baostock 的 query_profit_data / query_growth_data 只能按 (year, quarter) 逐季度
    # 查询（无 year=0 全量模式），返回财报行含 pubDate(公告日)/statDate(报告期)。
    # 公告日 pubDate 供回测引擎做防前视（announce_date <= 决策日才可见）。
    # 新方法不进 METHOD_CAPABILITIES，不参与 fetch_with_fallback 回退链——
    # 由补数脚本直接调用，避免污染现有估值数据（fetch_stock_fundamentals）的语义。
    _QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}

    def fetch_profit_data(
        self, ts_codes: list[str], start_date: date, end_date: date
    ) -> list[StockFundamental]:
        """季频盈利能力：ROE(roeAvg)、净利率、毛利(gpMargin)、净利润等。"""
        return self._fetch_quarterly(ts_codes, start_date, end_date, kind="profit", source="baostock_profit")

    def fetch_growth_data(
        self, ts_codes: list[str], start_date: date, end_date: date
    ) -> list[StockFundamental]:
        """季频成长能力：营收同比(YOYNI)、净利润同比(YOYPNI)等。"""
        return self._fetch_quarterly(ts_codes, start_date, end_date, kind="growth", source="baostock_growth")

    def _fetch_quarterly(
        self,
        ts_codes: list[str],
        start_date: date,
        end_date: date,
        *,
        kind: str,
        source: str,
    ) -> list[StockFundamental]:
        # 枚举 (year, quarter)：从 start_date 前一年到 end_date 当年，覆盖区间内全部报告期
        # （回测起点需"最近一期已公告财报"，故多取一年前置缓冲）。
        quarters: list[tuple[int, int]] = []
        for y in range(start_date.year - 1, end_date.year + 1):
            quarters.extend((y, q) for q in (1, 2, 3, 4))
        method = "query_profit_data" if kind == "profit" else "query_growth_data"

        def query(bs):
            out: list[StockFundamental] = []
            for ts_code in ts_codes:
                code, suffix = ts_code.split(".", 1)
                bs_code = f"{suffix.lower()}.{code}"
                for year, quarter in quarters:
                    result = getattr(bs, method)(code=bs_code, year=year, quarter=quarter)
                    if result.error_code != "0":
                        raise DataProviderError(
                            f"baostock {kind} failed for {ts_code} {year}Q{quarter}: {result.error_msg}"
                        )
                    while result.next():
                        row = dict(zip(result.fields, result.get_row_data(), strict=False))
                        row["ts_code"] = ts_code
                        out.append(normalize_stock_fundamental(row, source, ts_code=ts_code))
            return out

        return self._run(query)


class AkShareProvider:
    name = "akshare"
    display_name = "AkShare"
    capabilities = frozenset({
        ProviderCapability.STOCK_BASIC,
        ProviderCapability.TRADE_CALENDAR,
        ProviderCapability.DAILY_KLINE,
        ProviderCapability.FUNDAMENTALS,
        ProviderCapability.REALTIME_QUOTE,
    })
    priority_default = 3

    def fetch_stock_basic(self) -> list[StockBasic]:
        try:
            import akshare as ak  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DataProviderError("akshare is not installed") from exc

        result: list[StockBasic] = []
        frame = ak.stock_info_a_code_name()
        result.extend(normalize_stock_basic(row, self.name) for row in dataframe_records(frame))

        try:
            for func_name in ("stock_info_sh_delist", "stock_info_sz_delist"):
                try:
                    delist_frame = getattr(ak, func_name)()
                except Exception:
                    logger.debug("silent except in fetch_stock_basic")
                    continue
                for row in dataframe_records(delist_frame):
                    basic = normalize_stock_basic(row, self.name)
                    if basic.is_delisted or basic.delist_date is not None:
                        result.append(basic)
        except Exception:
            logger.debug("silent except in fetch_stock_basic")
            pass

        return result

    def fetch_trade_calendar(self, start_date: date, end_date: date) -> list[TradeCalendarDay]:
        try:
            import akshare as ak  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DataProviderError("akshare is not installed") from exc

        frame = ak.tool_trade_date_hist_sina()
        rows = dataframe_records(frame)
        days = [normalize_trade_calendar(row, self.name) for row in rows]
        return [day for day in days if start_date <= day.cal_date <= end_date]

    def fetch_daily_kline(self, ts_code: str, start_date: date, end_date: date) -> list[DailyKline]:
        try:
            import akshare as ak  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DataProviderError("akshare is not installed") from exc

        symbol = ts_code.split(".", 1)[0]
        # adjust="qfq" = 前复权: returns adjusted prices directly. Fixes
        # the bug where ex-dividend days produced fake阴线 and triggered
        # spurious sell signals in backtest.
        # stock_zh_a_hist doesn't expose adj_factor as a separate column,
        # so adj_factor stays None — backtest uses the qfq prices as-is.
        frame = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=_date_arg(start_date),
            end_date=_date_arg(end_date),
            adjust="qfq",
        )
        return [normalize_daily_kline(row, self.name, ts_code=ts_code) for row in dataframe_records(frame)]

    def fetch_stock_fundamentals(
        self, ts_codes: list[str], start_date: date, end_date: date
    ) -> list[StockFundamental]:
        try:
            import akshare as ak  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DataProviderError("akshare is not installed") from exc

        records: list[StockFundamental] = []
        latest_report_date = end_date
        try:
            frame = ak.stock_zh_a_spot_em()
        except Exception as exc:
            raise DataProviderError(f"akshare market snapshot failed: {exc}") from exc

        wanted = {code.split(".", 1)[0]: code for code in ts_codes}
        for row in dataframe_records(frame):
            symbol = str(row.get("代码") or row.get("code") or "").strip().zfill(6)
            ts_code = wanted.get(symbol)
            if not ts_code:
                continue
            data = dict(row)
            data["ts_code"] = ts_code
            data["report_date"] = latest_report_date
            records.append(normalize_stock_fundamental(data, self.name, ts_code=ts_code, report_date=latest_report_date))
        return records

    def fetch_realtime_quote(self, ts_codes: list[str]) -> dict[str, Decimal]:
        if not ts_codes:
            return {}
        try:
            import akshare as ak  # type: ignore[import-not-found]
        except ImportError:
            logger.debug("silent except in fetch_realtime_quote")
            return {}
        try:
            frame = ak.stock_zh_a_spot_em()
        except Exception:
            logger.debug("silent except in fetch_realtime_quote")
            return {}
        wanted = {code.split(".", 1)[0]: code for code in ts_codes}
        result: dict[str, Decimal] = {}
        for row in dataframe_records(frame):
            symbol = str(row.get("代码") or row.get("code") or "").strip().zfill(6)
            ts_code = wanted.get(symbol)
            if not ts_code or ts_code in result:
                continue
            try:
                price = Decimal(str(row.get("最新价", "0")))
                if price > 0:
                    result[ts_code] = price
            except Exception:
                logger.debug("silent except in fetch_realtime_quote")
                pass
        return result

register_provider(ADataProvider)
register_provider(BaostockProvider)
register_provider(AkShareProvider)
