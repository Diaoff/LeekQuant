from __future__ import annotations

from datetime import date
from typing import Protocol

from app.data.models import DailyKline, StockBasic, StockFundamental, TradeCalendarDay
from app.data.normalizers import (
    dataframe_records,
    normalize_daily_kline,
    normalize_stock_basic,
    normalize_stock_fundamental,
    normalize_trade_calendar,
)


class DataProviderError(RuntimeError):
    pass


class DataProvider(Protocol):
    name: str

    def fetch_stock_basic(self) -> list[StockBasic]: ...

    def fetch_trade_calendar(self, start_date: date, end_date: date) -> list[TradeCalendarDay]: ...

    def fetch_daily_kline(self, ts_code: str, start_date: date, end_date: date) -> list[DailyKline]: ...

    def fetch_stock_fundamentals(
        self, ts_codes: list[str], start_date: date, end_date: date
    ) -> list[StockFundamental]: ...


def _date_arg(value: date) -> str:
    return value.strftime("%Y%m%d")


class ADataProvider:
    name = "adata"

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
        frame = adata.stock.market.get_market(
            stock_code=symbol,
            k_type=1,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
        return [normalize_daily_kline(row, self.name, ts_code=ts_code) for row in dataframe_records(frame)]

    def fetch_stock_fundamentals(
        self, ts_codes: list[str], start_date: date, end_date: date
    ) -> list[StockFundamental]:
        raise DataProviderError("adata fundamentals adapter is not available")


class BaostockProvider:
    name = "baostock"

    def _run(self, fn):
        try:
            import baostock as bs  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DataProviderError("baostock is not installed") from exc

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
            fields = "date,code,open,high,low,close,preclose,volume,amount,turn,tradestatus"
            result = bs.query_history_k_data_plus(
                bs_code,
                fields,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                frequency="d",
                adjustflag="3",
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
            normalized.append(normalize_daily_kline(row, self.name, ts_code=ts_code))
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


class AkShareProvider:
    name = "akshare"

    def fetch_stock_basic(self) -> list[StockBasic]:
        try:
            import akshare as ak  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DataProviderError("akshare is not installed") from exc

        frame = ak.stock_info_a_code_name()
        return [normalize_stock_basic(row, self.name) for row in dataframe_records(frame)]

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
        frame = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=_date_arg(start_date),
            end_date=_date_arg(end_date),
            adjust="",
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
