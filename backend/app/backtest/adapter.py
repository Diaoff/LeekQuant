"""Python-native backtest engine.

Reads K-line data from PostgreSQL, executes user strategy code with MyTT
injected, and simulates daily trading using candle-path price inference.

Supports stop-loss, take-profit, trailing stop, and time-based stop.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import numpy as np

from app.backtest.cost import AShareCostCalculator, CostResult, FeeConfig
from app.backtest.signals import SignalInput, SignalOutput, apply_cn_rules, map_signal_to_action
from app.backtest.strategy_runtime import (StrategyExecutionResult, compile_strategy, execute_compiled_signal, execute_compiled_script)
from app.libs.MyTT import clear_ema_cache, clear_roll_cache

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class KBar:
    ts_code: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    pre_close: Decimal
    volume: int
    amount: Decimal
    adj_factor: Decimal | None
    is_suspended: bool
    is_limit_up: bool
    is_limit_down: bool
    turnover_rate: Decimal | None = None


@dataclass(slots=True)
class Position:
    ts_code: str
    shares: int = 0
    avg_cost: Decimal = Decimal("0")


@dataclass(slots=True)
class TradeRecord:
    ts_code: str
    trade_date: date
    direction: str              # "买入" / "卖出"
    price: Decimal
    volume: int
    amount: Decimal
    cost: CostResult
    signal_type: str            # 原始信号类型：买入/增持/减仓/卖出/观望

    action: str = ""            # 实际执行动作：BUY / SELL_PARTIAL / SELL_ALL / HOLD
    signal_reason: str = ""     # 信号原因描述（如"MA5上穿MA20金叉"）
    target_position: float = 0.0  # 目标仓位比例
    position_before: float = 0.0  # 交易前持仓比例（0.0~1.0）
    position_after: float = 0.0   # 交易后持仓比例（0.0~1.0）
    pnl: Decimal = Decimal("0")   # 本笔盈亏（买入时为0，卖出时计算）
    balance_before: Decimal = Decimal("0")  # 交易前总资产
    balance_after: Decimal = Decimal("0")   # 交易后总资产
    holding_days: int = 0       # 持仓天数（仅卖出时有意义）
    exit_reason: str = ""       # 卖出原因: "策略信号" / "止损" / "止盈" / "移动止盈" / "时间止损"


@dataclass(slots=True)
class _LotEntry:
    ts_code: str
    shares: int
    cost: Decimal
    entry_date: date
    entry_fee: Decimal = Decimal("0")


@dataclass(slots=True)
class _ClosedLot:
    ts_code: str
    shares: int
    entry_price: Decimal
    entry_date: date
    exit_price: Decimal
    exit_date: date
    entry_fee: Decimal
    exit_fee: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    return_rate: Decimal
    holding_days: int
    exit_reason: str

    @property
    def pnl(self) -> Decimal:
        return self.net_pnl


class SellDirection(str):
    """Detailed sell label that remains compatible with legacy '卖出' checks."""

    def __eq__(self, other: object) -> bool:
        if other == "卖出":
            return True
        return super().__eq__(other)

    __hash__ = str.__hash__


@dataclass(slots=True)
class BacktestConfig:
    strategy_id: int
    source_code: str
    stock_pool: list[str]
    start_date: date
    end_date: date
    initial_cash: Decimal = Decimal("100000")
    fee_config: FeeConfig = field(default_factory=FeeConfig)
    benchmark_code: str | None = None

    # 额外注入策略上下文的序列（指数 / 行业 / 板块等）。
    # 键为策略内访问名（如 "bench" / "sector"），值为 daily_kline 中的 ts_code。
    # 这些序列与股票池使用同一张表、同一套加载逻辑，字段完全一致。
    # 未配置时为空 dict；若某 code 在库中无数据，则对应视图安静降级为空。
    extra_series: dict[str, str] = field(default_factory=dict)

    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    trailing_stop_pct: float = 0.0
    trailing_activation_pct: float = 0.0
    time_stop_days: int = 0
    slippage_pct: float = 0.001

    # Rebalancing: when multiple stocks trigger buy signals on the same day,
    # "ranked" mode sells low-scored existing positions to fund higher-priority
    # new buys.  Disabled by default for backward compat.
    rebalance_mode: str = "disabled"  # "disabled" | "ranked"
    max_positions: int = 0  # 0 = unlimited, capped at this when rebalance_mode="ranked"

    # 单日最大买入只数：限制每个交易日实际建仓（买入）的股票数量，避免
    # 在一天内集中建仓导致过早满仓，使资金分配更平滑、仓位控制更合理。
    # 0 = 不限制（默认）。统计以成交发生日（fill date）为准，同一交易日
    # 对同一只股票重复买入只计 1 只。
    max_daily_buys: int = 0  # 0 = unlimited

    # Rebalance v2 settings (weekly, ranked, equal-weight)
    rebalance_version: int = 1
    rebalance_frequency: str = "weekly"
    weighting_method: str = "equal"
    rank_buffer_pct: float = 0.2
    score_max_age_sessions: int = 5

    execution_timeframe: str = "1D"
    signal_timeframe: str = "1D"
    strategy_mode: str = "signal"

    # ── 避险切换（跷跷板）：基准走弱时把资金动态配置到避险库 ──
    # 由回测提交参数 params_snapshot.defensive_switch 控制，默认关闭，
    # 不影响既有回测。开启时引擎在时序中监测基准状态，down 则清仓策略、
    # 从避险库等权买入全部标的；defensive_pick_k=0 表示全池等权（前端默认），
    # >0 为内部/进阶择优选前 K 只（前端不暴露）。
    # 转强（up/neutral）则清仓避险、回归策略。
    defensive_switch_enabled: bool = False
    defensive_pool_codes: list[str] = field(default_factory=list)  # 全部启用避险池标的（不限制前几）
    defensive_rules: dict[str, Any] = field(default_factory=dict)  # DefensiveRules 字段子集
    defensive_benchmark_code: str | None = None
    # ── 避险 V3：一键清仓全部持仓 + 池内等权买入（不计算质量分/自动排名）──
    # defensive_pick_k：0（默认，前端不暴露）= 全池等权买入；
    # >0 = 内部/进阶择优选前 K 只（按近 N 日抗跌性）。当前前端固定走全池等权。
    defensive_pick_k: int = 0             # 0=全池等权（前端默认）；>0=内部择优选前 K 只
    defensive_pick_ret_window: int = 10   # 抗跌评估窗口：近 N 日区间涨幅，跌幅最小（涨幅最高）者最抗跌


@dataclass(slots=True)
class _SignalCandidate:
    ts_code: str
    bar: KBar
    action: SignalOutput
    signal: dict[str, Any] | None
    exit_reason: str | None = None
    buy_priority_score: Decimal = Decimal("0")
    buy_priority_source: str = "default"
    turnover_rate: Decimal | None = None
    fill_bar: KBar | None = None  # next day's bar for fill price (None = fallback)


@dataclass
class _StockArrays:
    """Precomputed per-stock numpy arrays.

    Built once per stock (in ``BacktestRunner.run``) so that each
    ``BacktestContext`` property access becomes an O(1) array slice instead of
    a per-bar Python-level rebuild over ~60 bars. This is the core of the
    Phase 1 backtest speed-up.
    """

    close: "np.ndarray"
    open: "np.ndarray"
    high: "np.ndarray"
    low: "np.ndarray"
    volume: "np.ndarray"
    amount: "np.ndarray"
    dates: list

    @classmethod
    def from_klines(cls, klines: list[KBar]) -> "_StockArrays":
        return cls(
            close=np.array([float(k.close) for k in klines], dtype=np.float64),
            open=np.array([float(k.open) for k in klines], dtype=np.float64),
            high=np.array([float(k.high) for k in klines], dtype=np.float64),
            low=np.array([float(k.low) for k in klines], dtype=np.float64),
            volume=np.array([k.volume for k in klines]),
            amount=np.array([float(k.amount) for k in klines], dtype=np.float64),
            dates=[k.trade_date for k in klines],
        )


class _SeriesView:
    """Read-only, trade-date-aligned view over a :class:`_StockArrays`.

    Exposes the prefix of each array **up to and including** a target
    ``trade_date`` so user strategies can read benchmark / index / extra
    series without look-ahead bias. Every property returns an O(1) numpy
    slice over the underlying arrays.

    Used for ``ctx.benchmark`` and ``ctx.extra[name]``.
    """

    def __init__(self, arrays: "_StockArrays", trade_date: date) -> None:
        self._a = arrays
        self._td = trade_date
        cut = len(arrays.dates) - 1
        while cut >= 0 and arrays.dates[cut] > trade_date:
            cut -= 1
        self._cut = cut

    @property
    def close(self) -> "np.ndarray":
        return self._a.close[: self._cut + 1]

    @property
    def open(self) -> "np.ndarray":
        return self._a.open[: self._cut + 1]

    @property
    def high(self) -> "np.ndarray":
        return self._a.high[: self._cut + 1]

    @property
    def low(self) -> "np.ndarray":
        return self._a.low[: self._cut + 1]

    @property
    def volume(self) -> "np.ndarray":
        return self._a.volume[: self._cut + 1]

    @property
    def amount(self) -> "np.ndarray":
        return self._a.amount[: self._cut + 1]

    @property
    def dates(self) -> list:
        return self._a.dates[: self._cut + 1]

    @property
    def bar_count(self) -> int:
        return self._cut + 1


@dataclass(slots=True, frozen=True)
class _FundamentalsSnapshot:
    """最近一期已公告财报的只读快照（引擎按决策日防前视截取）。

    属性缺失时为 None；策略侧只需读标量（如 ``ctx.fundamentals.roe``），
    无需关心日期逻辑——防前视由引擎保证（``announce_date <= 决策日``）。
    """

    ts_code: str
    report_date: date | None = None
    announce_date: date | None = None
    roe: Decimal | None = None
    revenue_growth: Decimal | None = None
    net_profit_growth: Decimal | None = None
    gross_margin: Decimal | None = None
    net_profit: Decimal | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "_FundamentalsSnapshot":
        return cls(
            ts_code=str(row.get("ts_code") or ""),
            report_date=row.get("report_date"),
            announce_date=row.get("announce_date"),
            roe=row.get("roe"),
            revenue_growth=row.get("revenue_growth"),
            net_profit_growth=row.get("net_profit_growth"),
            gross_margin=row.get("gross_margin"),
            net_profit=row.get("net_profit"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "report_date": self.report_date.isoformat() if self.report_date else None,
            "announce_date": self.announce_date.isoformat() if self.announce_date else None,
            "roe": float(self.roe) if self.roe is not None else None,
            "revenue_growth": float(self.revenue_growth) if self.revenue_growth is not None else None,
            "net_profit_growth": float(self.net_profit_growth) if self.net_profit_growth is not None else None,
            "gross_margin": float(self.gross_margin) if self.gross_margin is not None else None,
            "net_profit": float(self.net_profit) if self.net_profit is not None else None,
        }


class BacktestContext:
    """Context object exposed to user strategy code."""

    def __init__(
        self,
        klines: list[KBar],
        positions: dict[str, Position],
        total_asset: Decimal,
        current_price: Decimal | None = None,
        runner: 'BacktestRunner | None' = None,
        ts_code: str | None = None,
        benchmark_arrays: "_StockArrays | None" = None,
        all_klines: "dict[str, list[KBar]] | None" = None,
        extra_arrays: "dict[str, _StockArrays] | None" = None,
        fundamental_rows: "dict[str, list[dict[str, Any]]] | None" = None,
    ):
        # Legacy / test path: a full (or window) klines list is supplied and the
        # whole thing is exposed as the context window.
        self._arrays = _StockArrays.from_klines(klines)
        self._lo = 0
        self._hi = len(klines)
        self._idx = len(klines)
        self._current_position: float = 0.0
        self.current_price = float(current_price) if current_price is not None else None
        self._runner = runner
        self._ts_code = ts_code
        self._benchmark_arrays = benchmark_arrays
        self._all_klines = all_klines
        self._extra_arrays = extra_arrays or {}
        self._fundamental_rows = fundamental_rows or {}

    @classmethod
    def from_arrays(
        cls,
        arrays: "_StockArrays",
        idx: int,
        lookback: int,
        positions: dict[str, Position],
        total_asset: Decimal,
        current_price: Decimal | None = None,
        runner: 'BacktestRunner | None' = None,
        ts_code: str | None = None,
        benchmark_arrays: "_StockArrays | None" = None,
        all_klines: "dict[str, list[KBar]] | None" = None,
        extra_arrays: "dict[str, _StockArrays] | None" = None,
        fundamental_rows: "dict[str, list[dict[str, Any]]] | None" = None,
    ) -> "BacktestContext":
        """Hot-path constructor: zero-copy slice window [idx-lookback, idx).

        The per-stock ``arrays`` are built once in ``BacktestRunner.run``; this
        only records the slice bounds so each property returns an O(1) view,
        eliminating the per-bar numpy rebuild (Phase 1).
        """
        obj = cls.__new__(cls)
        obj._arrays = arrays
        obj._lo = max(0, idx - lookback)
        obj._hi = idx
        obj._idx = idx
        obj._current_position = 0.0
        obj.current_price = float(current_price) if current_price is not None else None
        obj._runner = runner
        obj._ts_code = ts_code
        obj._benchmark_arrays = benchmark_arrays
        obj._all_klines = all_klines
        obj._extra_arrays = extra_arrays or {}
        obj._fundamental_rows = fundamental_rows or {}
        return obj

    @property
    def close(self) -> np.ndarray:
        return self._arrays.close[self._lo:self._hi]

    @property
    def open(self) -> np.ndarray:
        return self._arrays.open[self._lo:self._hi]

    @property
    def high(self) -> np.ndarray:
        return self._arrays.high[self._lo:self._hi]

    @property
    def low(self) -> np.ndarray:
        return self._arrays.low[self._lo:self._hi]

    @property
    def volume(self) -> np.ndarray:
        return self._arrays.volume[self._lo:self._hi]

    @property
    def amount(self) -> np.ndarray:
        return self._arrays.amount[self._lo:self._hi]

    @property
    def trade_date(self) -> date:
        if not self._arrays.dates:
            return date.today()
        return self._arrays.dates[self._idx - 1]

    @property
    def bar_count(self) -> int:
        """Number of bars accumulated so far (len of the current window).

        Useful for cooldown/timing logic in user strategies (e.g. "add only
        once every N bars"). The value equals the window length at the current
        bar (``hi - lo``).
        """
        return self._hi - self._lo

    @property
    def current_position(self) -> float:
        return self._current_position

    @current_position.setter
    def current_position(self, value: float) -> None:
        self._current_position = value

    @property
    def stock_position_weight(self) -> float:
        if not self._runner or not self._ts_code or not self._arrays.dates:
            return 0.0
        pos = self._runner.positions.get(self._ts_code)
        if not pos or pos.shares <= 0:
            return 0.0
        observable_price = Decimal(str(self._arrays.close[self._idx - 1]))
        nav = self._runner._calc_total_asset(self._runner._all_klines, self.trade_date)
        if nav <= 0:
            return 0.0
        return float(pos.shares * observable_price / nav)

    @property
    def portfolio_exposure(self) -> float:
        if not self._runner:
            return 0.0
        nav = self._runner._calc_total_asset(self._runner._all_klines, self.trade_date)
        if nav <= 0:
            return 0.0
        total_market_value = Decimal("0")
        for ts_code, pos in self._runner.positions.items():
            if pos.shares <= 0:
                continue
            klines = self._runner._all_klines.get(ts_code, [])
            price = None
            for k in reversed(klines):
                if k.trade_date <= self.trade_date and k.close:
                    price = k.close
                    break
            if price:
                total_market_value += price * pos.shares
        return float(total_market_value / nav)

    @property
    def position_shares(self) -> int:
        if not self._runner or not self._ts_code:
            return 0
        pos = self._runner.positions.get(self._ts_code)
        return pos.shares if pos else 0

    @property
    def entry_price(self) -> float | None:
        """当前持仓的权威入场价（引擎真实成交价，非近似）。

        无持仓或引擎未记录时返回 ``None``。策略出场逻辑应优先用此值计算
        止损/止盈距离，而不是在 ctx 上自存入场价——因为 BacktestContext 每天
        重建，自定义属性无法跨 bar 持久。
        """
        if not self._runner or not self._ts_code:
            return None
        p = self._runner._entry_prices.get(self._ts_code)
        return float(p) if p is not None else None

    @property
    def entry_date(self) -> "date | None":
        """当前持仓的权威入场日期（引擎记录）。无持仓返回 ``None``。"""
        if not self._runner or not self._ts_code:
            return None
        return self._runner._entry_dates.get(self._ts_code)

    @property
    def state(self) -> "dict[str, Any]":
        """跨 bar 持久的策略私有状态（按 ``ts_code`` 隔离）。

        返回一个 dict，对同一只股票在回测全程的各根 bar 间持续存在，可用于
        记录峰值(移动止盈)、入场 ATR、最近买入日(冷却)等。切勿依赖在 ctx 上
        直接 set 自定义属性——它们会在下一天丢失。
        """
        if not self._runner or not self._ts_code:
            return {}
        return self._runner._ctx_state.setdefault(self._ts_code, {})

    @property
    def cash(self) -> float:
        if not self._runner:
            return 0.0
        return float(self._runner.cash)

    @property
    def ts_code(self) -> str | None:
        """当前股票代码。策略可据此区分标的、读取对应基本面等。"""
        return self._ts_code

    @property
    def benchmark(self) -> "_SeriesView | None":
        """基准 / 指数序列窗口（由 ``config.benchmark_code`` 注入）。

        返回按当前 ``trade_date`` 对齐的只读视图；未配置时为 ``None``。
        例：``MA(ctx.benchmark.close, 20)[-1]`` 取基准 20 日线。
        """
        if self._benchmark_arrays is None:
            return None
        return _SeriesView(self._benchmark_arrays, self.trade_date)

    @property
    def all_klines(self) -> "dict[str, list[KBar]] | None":
        """全市场已加载 K 线（只读），用于横截面 / 跨标的比较。

        仅含回测股票池（及 ``extra_series``）内的标的，不含未入选股票。
        """
        return self._all_klines

    @property
    def extra(self) -> "dict[str, _SeriesView]":
        """``extra_series`` 注入的额外序列，键为配置中的访问名。

        例：``ctx.extra.get("sector")`` 取行业指数窗口视图；未配置时为空 dict。
        """
        return {name: _SeriesView(arr, self.trade_date) for name, arr in self._extra_arrays.items()}

    @property
    def fundamentals(self) -> "_FundamentalsSnapshot | None":
        """最近一期已公告财报快照（防前视）。

        按 ``announce_date <= 决策日(trade_date)`` 取最近一期；无已公告财报时
        返回 ``None``。策略可读 ``ctx.fundamentals.roe`` / ``.net_profit_growth``
        / ``.revenue_growth`` 等标量做"绩优择优"。

        防前视语义：``ctx.trade_date`` 返回的是窗口末根日期（td-1，策略看不到
        当日 bar），故 ``announce_date == 决策日`` 的财报同样可见——与
        ``ctx.benchmark`` 的对齐口径完全一致。
        """
        if not self._ts_code:
            return None
        rows = self._fundamental_rows.get(self._ts_code)
        if not rows:
            return None
        td = self.trade_date
        # rows 已按 (announce_date, report_date) 升序排序；二分找最后一个 announce_date <= td
        lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi) // 2
            if rows[mid]["announce_date"] <= td:
                lo = mid + 1
            else:
                hi = mid
        if lo == 0:
            return None
        return _FundamentalsSnapshot.from_row(rows[lo - 1])


class ScriptContext:
    """Context for on_bar() callback in script strategy mode."""

    def __init__(self, runner: "BacktestRunner", ts_code: str, bar: KBar, total_asset: Decimal):
        self._runner = runner
        self._ts_code = ts_code
        self._bar = bar
        self._total_asset = total_asset
        self._action_taken = False

        self.ts_code = ts_code
        self.date = bar.trade_date
        self.open = float(bar.open)
        self.high = float(bar.high)
        self.low = float(bar.low)
        self.close = float(bar.close)
        self.volume = bar.volume

        pos = runner.positions.get(ts_code)
        self.position = float(pos.shares * bar.close / total_asset) if pos and total_asset > 0 else 0.0
        self.shares = pos.shares if pos else 0
        self.cash = float(runner.cash)
        self.portfolio_value = float(total_asset)

    def buy(self, pct: float = 1.0) -> None:
        if self._action_taken:
            return
        self._action_taken = True
        self._runner._script_pending_action = ("BUY", self._ts_code, pct, self._bar, "策略信号: buy")

    def sell(self, pct: float = 0.0) -> None:
        if self._action_taken:
            return
        self._action_taken = True
        if pct <= 0 or self.shares <= 0:
            self._runner._script_pending_action = ("SELL_ALL", self._ts_code, 0.0, self._bar, "策略信号: sell")
        else:
            self._runner._script_pending_action = ("SELL_PARTIAL", self._ts_code, pct, self._bar, "策略信号: sell")

    def hold(self) -> None:
        self._action_taken = True


class BacktestRunner:
    """Day-by-day backtest engine using closing-price matching."""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.calculator = AShareCostCalculator(config.fee_config)
        self.cash = config.initial_cash
        self.positions: dict[str, Position] = {}
        self.trades: list[TradeRecord] = []
        self.equity_curve: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []
        self.strategy_errors: list[dict[str, Any]] = []
        self._entry_dates: dict[str, date] = {}
        self._entry_prices: dict[str, Decimal] = {}
        self._highest_since_entry: dict[str, Decimal] = {}
        # 避险切换（跷跷板）状态——在 __init__ 预置默认值，
        # 保证直接调用 _compute_results()（如单测）也不会因属性缺失而报错。
        self._defensive_mode = False
        self._defensive_pool_codes: list[str] = []
        self._defensive_rules: dict[str, Any] = dict(config.defensive_rules or {})
        self._defensive_episodes: list[dict[str, Any]] = []
        self._defensive_switch_active = False
        self._defensive_benchmark_arrays = None
        # V3: 择优选股参数
        self._defensive_pick_k = max(0, int(config.defensive_pick_k or 0))
        self._defensive_pick_ret_window = max(2, int(config.defensive_pick_ret_window or 10))
        # 记录进入避险时被"保留"的强势持仓代码（用于退出时延后清仓）
        self._defensive_kept_strategy = set()
        self._lowest_since_entry: dict[str, Decimal] = {}
        self._open_lots: dict[str, list[_LotEntry]] = {}
        self._closed_lots: list[_ClosedLot] = []
        self._script_pending_action: tuple[str, str, float, KBar, str] | None = None
        self._compiled_strategy = compile_strategy(config.source_code)
        self._script_mode = config.strategy_mode == "script"
        self._slippage = Decimal(str(config.slippage_pct))
        self._all_klines: dict[str, list[KBar]] = {}
        # Per-stock day index for O(1) bar lookup instead of O(n) list comprehension.
        self._stock_day_index: dict[str, int] = {code: 0 for code in config.stock_pool}
        # 跨 bar 持久的策略私有状态（按 ts_code 隔离），供策略记录 peak/entry_atr/
        # last_buy 等需要在持仓期间持续累积的数据。BacktestContext.state 暴露它。
        self._ctx_state: dict[str, dict] = {}
        # Rebalance v2 planner (initialized lazily in run())
        self.rebalance_planner: Any = None
        self._stored_rebalance_plan: Any = None
        # 单日最大买入只数计数：key 为成交日（fill date），value 为该日已买入的
        # 不同股票集合。仅在 BUY 实际增加持仓份额时计入，避免无成交的占位 BUY
        # 错误地占用限额。
        self._daily_bought: dict[date, set[str]] = {}

    @staticmethod
    def _infer_candle_path(open_: Decimal, high: Decimal, low: Decimal, close: Decimal) -> list[Decimal]:
        if close >= open_:
            return [open_, low, high, close]
        else:
            return [open_, high, low, close]

    def _check_exit_conditions(
        self,
        ts_code: str,
        day_high: Decimal,
        day_low: Decimal,
        bar: KBar,
    ) -> str | None:
        """Check risk-exit triggers for an open position on signal day `bar`.

        Triggers are evaluated against the day's intraday high/low so that a
        stop is fired when price *touches* the threshold during the session,
        not only when the open happens to gap past it (the previous behavior
        used bar.open and could miss intraday touches). The actual fill price
        is still resolved later at the next day's open (next_open mode), so the
        decision date (td) and the fill date (td+1) stay consistent with the
        no-lookahead execution model.
        """
        entry_price = self._entry_prices.get(ts_code)
        if entry_price is None or entry_price <= 0:
            return None
        if self.config.stop_loss_pct > 0:
            # Touched the stop when the day's low fell enough below entry.
            loss_pct = float((entry_price - day_low) / entry_price)
            if loss_pct >= self.config.stop_loss_pct:
                return "止损"
        if self.config.take_profit_pct > 0:
            # Touched the target when the day's high rose enough above entry.
            profit_pct = float((day_high - entry_price) / entry_price)
            if profit_pct >= self.config.take_profit_pct:
                return "止盈"
        if self.config.trailing_stop_pct > 0 and self.config.trailing_activation_pct > 0:
            highest = self._highest_since_entry.get(ts_code, entry_price)
            activated = float((highest - entry_price) / entry_price) >= self.config.trailing_activation_pct
            if activated:
                # Trail fires when price retracts from the peak by the trail %,
                # using the day's low as the worst intraday point.
                trail_pct = float((highest - day_low) / highest)
                if trail_pct >= self.config.trailing_stop_pct:
                    return "移动止盈"
        if self.config.time_stop_days > 0:
            entry_date = self._entry_dates.get(ts_code)
            if entry_date is not None and (bar.trade_date - entry_date).days >= self.config.time_stop_days:
                # Time stop only fires while still underwater (floating loss).
                pnl_pct = float((day_low - entry_price) / entry_price)
                if pnl_pct <= 0:
                    return "时间止损"
        return None

    def run(
        self,
        all_klines: dict[str, list[KBar]],
        benchmark_klines: list[KBar] | None = None,
        extra_klines: dict[str, list[KBar]] | None = None,
        fundamentals: "dict[str, list[dict[str, Any]]] | None" = None,
        defensive_klines: dict[str, list[KBar]] | None = None,
        defensive_benchmark_klines: list[KBar] | None = None,
    ) -> dict[str, Any]:
        """Run backtest for all stocks in the pool.

        ``benchmark_klines`` / ``extra_klines`` (optional) are precomputed
        series injected into every strategy context so user code can read
        benchmark / index / sector data via ``ctx.benchmark`` / ``ctx.extra``.

        ``fundamentals`` (optional) maps ``ts_code`` -> list of financial
        snapshot rows (each containing ``announce_date``/``report_date`` and
        fields like ``roe``/``net_profit_growth``); exposed via
        ``ctx.fundamentals`` as the most recent announced report (no-lookahead).
        """
        clear_ema_cache()  # Phase 2: 清空逐股 EMA 整段缓存，避免跨回测内存堆积
        clear_roll_cache()  # Phase 3: 清空逐股 rolling(MA/HHV/LLV/STD/SUM/REF) 整段缓存
        # 股票池（策略）K 线，单独保留用于交易日历推导，避免避险库日期污染时序
        self._stock_all_klines = all_klines
        self._all_klines = all_klines
        # 避险库 K 线并入统一行情宇宙：估值(_calc_total_asset)与成交(_find_bar)
        # 即可覆盖避险股，无需改动既有执行/估值路径。
        if defensive_klines:
            merged = dict(all_klines)
            merged.update(defensive_klines)
            all_klines = merged
            self._all_klines = all_klines
        self._benchmark_arrays = _StockArrays.from_klines(benchmark_klines) if benchmark_klines else None
        self._defensive_benchmark_arrays = (
            _StockArrays.from_klines(defensive_benchmark_klines)
            if defensive_benchmark_klines else self._benchmark_arrays
        )
        self._extra_arrays = {
            name: _StockArrays.from_klines(kl) for name, kl in (extra_klines or {}).items()
        }

        # ── 避险切换（跷跷板）状态初始化 ──
        self._defensive_mode = False
        self._defensive_pool_codes = list(self.config.defensive_pool_codes)
        self._defensive_rules = dict(self.config.defensive_rules or {})
        self._defensive_episodes: list[dict[str, Any]] = []
        self._defensive_switch_active = (
            self.config.defensive_switch_enabled
            and bool(self._defensive_benchmark_arrays)
            and bool(self._defensive_pool_codes)
        )
        # V3: 择优选股参数（run() 阶段再次落位，供直接单测构造 BacktestRunner 使用）
        self._defensive_pick_k = max(0, int(self.config.defensive_pick_k or 0))
        self._defensive_pick_ret_window = max(2, int(self.config.defensive_pick_ret_window or 10))
        self._defensive_kept_strategy: set[str] = set()
        if self.config.defensive_switch_enabled and not self._defensive_switch_active:
            logger.warning(
                "defensive switch enabled but inactive (benchmark=%s, pool_size=%d) — "
                "need both a benchmark series and a non-empty defensive pool",
                self.config.defensive_benchmark_code, len(self._defensive_pool_codes),
            )
        # 预处理基本面：剔除无公告日(防前视锚点缺失)的行，按 (announce_date, report_date) 排序
        self._fundamental_rows: "dict[str, list[dict[str, Any]]]" = {}
        for _code, _rows in (fundamentals or {}).items():
            _clean = [r for r in _rows if r.get("announce_date") is not None]
            if _clean:
                _clean.sort(key=lambda r: (r["announce_date"], r.get("report_date") or date.min))
                self._fundamental_rows[_code] = _clean
        self._stock_day_index = {code: 0 for code in self.config.stock_pool}
        trading_dates = self._get_trading_dates(self._stock_all_klines)
        if not trading_dates:
            raise ValueError("no trading dates found in the specified range")

        # Initialize rebalance v2 planner if ranked mode with v2+
        self.rebalance_planner = None
        self._stored_rebalance_plan = None
        if self.config.rebalance_mode == "ranked" and self.config.rebalance_version >= 2:
            from app.backtest.rebalance import WeeklyRebalancePlanner
            self.rebalance_planner = WeeklyRebalancePlanner(self.config, self)

        lookback = 60

        # Phase 1: build per-stock numpy arrays once so the inner loop only
        # does O(1) slices instead of rebuilding arrays every bar.
        stock_arrays: dict[str, _StockArrays] = {}
        for _code in self.config.stock_pool:
            _kl = all_klines.get(_code)
            if _kl:
                stock_arrays[_code] = _StockArrays.from_klines(_kl)

        for i, td in enumerate(trading_dates):
            # Next trading day's date — used to look up the fill bar so that
            # orders generated "as of td" (using data through td-1) execute
            # at td+1's open. This eliminates the lookahead bias where the
            # strategy saw td's close and filled at td's intraday prices.
            next_td = trading_dates[i + 1] if i + 1 < len(trading_dates) else None

            # Execute stored rebalance plan at the open of the fill date
            if self._stored_rebalance_plan is not None and self.rebalance_planner is not None and not self._defensive_mode:
                fill_bar_map: dict[str, KBar] = {}
                for ts_code in set(self.config.stock_pool):
                    bar = self._find_bar(ts_code, td)
                    if bar is not None:
                        fill_bar_map[ts_code] = bar
                self.rebalance_planner.execute(self._stored_rebalance_plan, fill_bar_map)
                self._stored_rebalance_plan = None

            total_asset = self._calc_total_asset(all_klines, td)
            self.equity_curve.append({
                "date": td.isoformat(),
                "total_asset": float(total_asset),
                "cash": float(self.cash),
                "defensive": self._defensive_mode,
            })

            # ── 避险切换 overlay：基准走弱→清仓策略、配置避险库；转强→回归 ──
            if self._defensive_switch_active:
                self._run_defensive_overlay(td, next_td, total_asset)

            candidates: list[_SignalCandidate] = []
            for ts_code in self.config.stock_pool:
                if self._defensive_mode:
                    # 避险模式下跳过策略选股，仅持有避险库（切换由 overlay 直接执行）
                    continue
                klines = all_klines.get(ts_code, [])
                if not klines:
                    continue

                # Locate td's bar via a monotonic forward pointer (O(1) amortized)
                # instead of an O(n) list comprehension per stock per day. The
                # pointer only advances because both trading_dates and each
                # stock's klines are sorted chronologically.
                idx = self._stock_day_index.get(ts_code, 0)
                if idx < 0:
                    # A previous gap day may have left a negative placeholder;
                    # never let the pointer go negative or the scan below would
                    # read klines[-1]. Restart from the beginning for this stock.
                    idx = 0
                while idx < len(klines) and klines[idx].trade_date <= td:
                    idx += 1
                idx -= 1  # back to the last bar with trade_date <= td
                if idx < 0 or idx >= len(klines):
                    # This stock has no data at or before td (pre-listing or a
                    # gap). Reset the pointer and skip — correctness is preserved
                    # because the next real-data day restarts the scan.
                    self._stock_day_index[ts_code] = 0
                    continue
                self._stock_day_index[ts_code] = idx
                if klines[idx].trade_date != td:
                    # Stock has data up to some day before td but none on td.
                    continue

                bar = klines[idx]
                if bar.is_suspended:
                    continue

                # Strategy window EXCLUDES td's bar (index idx) to avoid
                # lookahead bias. Strategy sees data through td-1 only.
                # O(1) slice window via precomputed arrays (Phase 1); skip the
                # very first bar where the window would be empty.
                if idx < 1:
                    continue

                # Look up the fill bar (td+1's bar) for this stock using
                # the next element in the sorted klines list (O(1)).
                fill_bar: KBar | None = None
                if next_td is not None and idx + 1 < len(klines) and klines[idx + 1].trade_date == next_td:
                    fill_bar = klines[idx + 1]

                price_path = self._infer_candle_path(bar.open, bar.high, bar.low, bar.close)

                if ts_code in self.positions and self.positions[ts_code].shares > 0:
                    cur_high = self._highest_since_entry.get(ts_code, bar.high)
                    cur_low = self._lowest_since_entry.get(ts_code, bar.low)
                    self._highest_since_entry[ts_code] = max(cur_high, bar.high)
                    self._lowest_since_entry[ts_code] = min(cur_low, bar.low)

                exit_reason = self._check_exit_conditions(ts_code, bar.high, bar.low, bar)
                if exit_reason:
                    if self.rebalance_planner is not None:
                        self.rebalance_planner.on_exit(ts_code, exit_reason)
                    exec_action = SignalOutput(action="SELL_ALL", target_position=0.0)
                    candidates.append(_SignalCandidate(
                        ts_code=ts_code,
                        bar=bar,
                        action=exec_action,
                        signal=None,
                        exit_reason=exit_reason,
                        fill_bar=fill_bar,
                    ))
                    continue

                if self._script_mode:
                    ctx = ScriptContext(self, ts_code, bar, total_asset)
                    try:
                        self._exec_strategy_script(ctx)
                    except Exception as exc:
                        self.strategy_errors.append({
                            "strategy_id": self.config.strategy_id,
                            "ts_code": ts_code,
                            "trade_date": td.isoformat(),
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        })
                    if self._script_pending_action:
                        action_type, action_ts_code, pct, action_bar, reason = self._script_pending_action
                        self._script_pending_action = None
                        if action_ts_code != ts_code:
                            continue
                        if action_type == "BUY":
                            exec_action = SignalOutput(action="BUY", target_position=min(pct, 1.0))
                        elif action_type == "SELL_ALL":
                            exec_action = SignalOutput(action="SELL_ALL", target_position=0.0)
                        else:
                            exec_action = SignalOutput(action="SELL_PARTIAL", target_position=pct)
                        signal_data = {"signal_type": reason, "current_position": ctx.position, "target_position": pct}
                        reason_text, blocked = self._apply_rules(exec_action, ts_code, bar, self.positions.get(ts_code), td)
                        if blocked:
                            self.signals.append({
                                "ts_code": ts_code,
                                "trade_date": td.isoformat(),
                                "signal_type": reason,
                                "action": "BLOCKED",
                                "reason": reason_text,
                            })
                            continue
                        candidates.append(_SignalCandidate(
                            ts_code=ts_code, bar=bar, action=exec_action, signal=signal_data, exit_reason=None,
                            fill_bar=fill_bar,
                        ))
                        if self.rebalance_planner is not None:
                            if action_type in ("SELL_ALL", "SELL_PARTIAL"):
                                self.rebalance_planner.on_exit(ts_code, reason)
                            else:
                                has_position = ts_code in self.positions and self.positions[ts_code].shares > 0
                                self.rebalance_planner.on_signal(ts_code, reason, td, has_position)
                    continue

                ctx = BacktestContext.from_arrays(
                    stock_arrays[ts_code], idx, lookback,
                    self.positions, total_asset, runner=self, ts_code=ts_code,
                    benchmark_arrays=self._benchmark_arrays,
                    all_klines=self._all_klines,
                    extra_arrays=self._extra_arrays,
                    fundamental_rows=self._fundamental_rows,
                )
                signal_result = self._exec_strategy(ctx, total_asset)
                if not signal_result.ok:
                    self.strategy_errors.append({
                        "strategy_id": self.config.strategy_id,
                        "ts_code": ts_code,
                        "trade_date": td.isoformat(),
                        **signal_result.to_error_dict(),
                    })
                    continue
                signal = signal_result.signal
                if signal is None:
                    continue

                # 全局出场权威：当回测配置了全局出场(任一>0)时，全局规则为出场唯一权威，
                # 忽略策略内部"卖出/减仓"信号，避免策略内部出场(校准弱于全局)干扰、劣化结果。
                # 仅当全局出场全关(自包含模式)时，才交由策略内部出场逻辑。
                # 历史教训：#198 的 +10% 实际依赖"策略卖出被静默降级 + 内部出场失效"这一 bug，
                # 修复后策略内部出场开始干扰全局，同源码同配置重跑(#204)塌到 -6.85%。
                global_exit_active = (
                    self.config.stop_loss_pct > 0 or self.config.take_profit_pct > 0
                    or self.config.trailing_stop_pct > 0 or self.config.time_stop_days > 0
                )
                if (global_exit_active and self.positions.get(ts_code)
                        and self.positions[ts_code].shares > 0
                        and signal.get("signal_type") in ("卖出", "减仓")):
                    continue

                # 用引擎权威持仓权重作为 current_position，而非信任策略回报——
                # 策略通常不会返回 current_position，若用默认值 0.0，会让"卖出"信号
                # 在 map_signal_to_action 里被判为 cur<=0 而静默降级成 HOLD，
                # 导致持仓永远无法平仓（历史上依赖引擎全局出场兜底，此 bug 被掩盖）。
                action_info = map_signal_to_action(SignalInput(
                    signal_type=signal.get("signal_type"),
                    current_position=ctx.stock_position_weight,
                    target_position=signal.get("target_position"),
                ))

                reason, blocked = self._apply_rules(
                    action_info, ts_code, bar, self.positions.get(ts_code), td
                )
                if blocked:
                    self.signals.append({
                        "ts_code": ts_code,
                        "trade_date": td.isoformat(),
                        "signal_type": signal.get("signal_type"),
                        "action": "BLOCKED",
                        "reason": reason,
                    })
                    continue

                candidates.append(self._build_signal_candidate(ts_code, bar, action_info, signal, td, fill_bar))

                # Notify rebalance planner of buy/add signals
                if self.rebalance_planner is not None:
                    signal_type = signal.get("signal_type") if signal else None
                    if signal_type:
                        has_position = ts_code in self.positions and self.positions[ts_code].shares > 0
                        self.rebalance_planner.on_signal(ts_code, signal_type, td, has_position)

            # ---- Phase 1: execute all sell candidates (forced exits + strategy sells) ----
            # Sells come first so they free up cash before buys are evaluated.
            for candidate in sorted(candidates, key=self._candidate_sort_key):
                if candidate.action.action not in ("SELL_ALL", "SELL_PARTIAL"):
                    continue
                match_blocked_reason = self._execute_action(
                    candidate.action,
                    candidate.ts_code,
                    candidate.bar,
                    total_asset,
                    candidate.signal,
                    exit_reason=candidate.exit_reason,
                    fill_bar=candidate.fill_bar,
                )
                signal_record = self._candidate_signal_record(candidate)
                if match_blocked_reason:
                    signal_record["match_status"] = "BLOCKED"
                    signal_record["reason"] = match_blocked_reason
                self.signals.append(signal_record)

            # ---- Phase 2: rebalance (sell low-scored positions to fund buys) ----
            buy_candidates = [c for c in candidates if c.action.action == "BUY"]
            if self.config.rebalance_mode == "ranked" and buy_candidates:
                self._rebalance_for_buys(buy_candidates, total_asset)

            # ---- Phase 3: execute all buy candidates ----
            for candidate in sorted(buy_candidates, key=self._candidate_sort_key):
                ts_code = candidate.ts_code
                trade_date = self._effective_trade_date(candidate.bar, candidate.fill_bar)
                if not self._daily_buy_allowed(ts_code, trade_date):
                    signal_record = self._candidate_signal_record(candidate)
                    signal_record["action"] = "BUY"
                    signal_record["match_status"] = "BLOCKED"
                    signal_record["reason"] = f"达到每日最大买入只数限制 ({self.config.max_daily_buys})"
                    self.signals.append(signal_record)
                    continue
                pos_before = self.positions.get(ts_code)
                shares_before = pos_before.shares if pos_before else 0
                match_blocked_reason = self._execute_action(
                    candidate.action,
                    candidate.ts_code,
                    candidate.bar,
                    total_asset,
                    candidate.signal,
                    exit_reason=candidate.exit_reason,
                    fill_bar=candidate.fill_bar,
                )
                signal_record = self._candidate_signal_record(candidate)
                if match_blocked_reason:
                    signal_record["match_status"] = "BLOCKED"
                    signal_record["reason"] = match_blocked_reason
                else:
                    pos_after = self.positions.get(ts_code)
                    # 仅在实际增加持仓份额时计入当日买入只数（占位无成交的 BUY 不占用限额）
                    if pos_after is not None and pos_after.shares > shares_before:
                        self._record_daily_buy(ts_code, trade_date)
                self.signals.append(signal_record)

            # ---- Phase 4: weekly rebalance check (v2) ----
            if self.rebalance_planner is not None and not self._defensive_mode and self.rebalance_planner.should_run_weekly(td, trading_dates):
                plan = self.rebalance_planner.plan(td, all_klines, total_asset, trading_dates)
                if plan is not None:
                    plan.fill_date = next_td
                    self._stored_rebalance_plan = plan

        return self._compute_results()

    def _build_signal_candidate(
        self,
        ts_code: str,
        bar: KBar,
        action: SignalOutput,
        signal: dict[str, Any],
        td: date,
        fill_bar: KBar | None = None,
    ) -> _SignalCandidate:
        priority_score = Decimal("0")
        priority_source = "default"

        if signal.get("signal_type") in ("买入", "增持"):
            confidence = self._decimal_or_none(signal.get("confidence"))
            if confidence is not None:
                priority_score = confidence
                priority_source = "confidence"

        return _SignalCandidate(
            ts_code=ts_code,
            bar=bar,
            action=action,
            signal=signal,
            buy_priority_score=priority_score,
            buy_priority_source=priority_source,
            turnover_rate=bar.turnover_rate,
            fill_bar=fill_bar,
        )

    @staticmethod
    def _decimal_or_none(value: Any) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))

    @staticmethod
    def _candidate_sort_key(candidate: _SignalCandidate) -> tuple[int, int, Decimal, Decimal, Decimal, str]:
        signal_type = candidate.signal.get("signal_type") if candidate.signal else candidate.exit_reason
        if candidate.exit_reason or signal_type in ("卖出", "减仓"):
            group = 0
        elif signal_type in ("买入", "增持"):
            group = 1
        else:
            group = 2
        if candidate.exit_reason:
            source_order = 0
        elif group == 0:
            source_order = 1
        else:
            source_order = 0 if candidate.buy_priority_source == "confidence" else 1
        target_position = Decimal(str(candidate.action.target_position or 0))
        turnover = -candidate.turnover_rate if candidate.turnover_rate is not None else Decimal("-1")
        return group, source_order, -candidate.buy_priority_score, turnover, -target_position, candidate.ts_code

    @staticmethod
    def _candidate_signal_record(candidate: _SignalCandidate) -> dict[str, Any]:
        signal = candidate.signal or {}
        record = {
            "ts_code": candidate.ts_code,
            "trade_date": candidate.bar.trade_date.isoformat(),
            "signal_type": signal.get("signal_type") or candidate.exit_reason,
            "action": candidate.action.action,
            "target_position": candidate.action.target_position,
        }
        if candidate.exit_reason:
            record["exit_reason"] = candidate.exit_reason
        if signal.get("signal_type") in ("买入", "增持"):
            record.update({
                "buy_priority_score": str(candidate.buy_priority_score),
                "buy_priority_source": candidate.buy_priority_source,
                "turnover_rate": str(candidate.turnover_rate) if candidate.turnover_rate is not None else None,
            })
        return record

    def _get_trading_dates(self, all_klines: dict[str, list[KBar]]) -> list[date]:
        dates: set[date] = set()
        for klines in all_klines.values():
            for k in klines:
                if self.config.start_date <= k.trade_date <= self.config.end_date:
                    dates.add(k.trade_date)
        return sorted(dates)

    @staticmethod
    def _adjust_price(price: Decimal, adj_factor: Decimal | None, mode: str) -> Decimal:
        """Apply adjustment factor to price based on mode.

        Providers fetch前复权 (qfq) prices by default (see BACKTEST_ADJUST_MODE
        in app.core.config). When prices are already qfq-adjusted, adj_factor
        is stored for audit/reference only — multiplying again would
        double-adjust. The adj_factor field is plumbed through DB → KBar
        for future use cases (e.g., converting between raw/adjusted prices
        for display).

        Args:
            price: Raw or qfq price from KBar.
            adj_factor: Cumulative adjustment factor (latest=1), or None
                when provider does not expose it (AData/EastMoney).
            mode: One of "qfq" (default, prices already adjusted),
                "hfq" (后复权, future use), or "none" (no adjustment).

        Returns:
            Adjusted price. In qfq mode, returns price unchanged (already
            adjusted by provider).
        """
        if mode == "none":
            return price
        # In qfq mode, providers already return前复权 prices; adj_factor is
        # stored for audit only. Returning price unchanged avoids the
        # double-adjustment bug (qfq_price * adj_factor would be wrong).
        return price

    def _calc_total_asset(self, all_klines: dict[str, list[KBar]], td: date) -> Decimal:
        position_value = Decimal("0")
        for ts_code, pos in self.positions.items():
            if pos.shares <= 0:
                continue
            klines = all_klines.get(ts_code, [])
            price = None
            adj_factor: Decimal | None = None
            for k in reversed(klines):
                if k.trade_date <= td and k.close:
                    price = k.close
                    adj_factor = k.adj_factor
                    break
            if price:
                adjusted = self._adjust_price(price, adj_factor, self._adjust_mode())
                position_value += adjusted * pos.shares
        return self.cash + position_value

    def _adjust_mode(self) -> str:
        """Read BACKTEST_ADJUST_MODE from settings, defaulting to 'qfq'.

        Imported lazily to avoid creating asyncio primitives at module import
        (mirrors the ws_producer pattern).
        """
        try:
            from app.core.config import get_settings
            return get_settings().backtest_adjust_mode
        except Exception:
            return "qfq"

    def _exec_strategy(self, ctx: BacktestContext, total_asset: Decimal) -> StrategyExecutionResult:
        try:
            ctx.current_position = self._position_ratio(total_asset)
        except AttributeError:
            pass
        return execute_compiled_signal(self._compiled_strategy, ctx)

    def _find_bar(self, ts_code: str, td: date) -> KBar | None:
        """Find the KBar for *ts_code* on trading day *td*.

        Scans the pre-loaded klines (sorted by trade_date).  Called only
        during rebalancing, so linear scan is acceptable.
        """
        klines = self._all_klines.get(ts_code, [])
        for k in klines:
            if k.trade_date == td:
                return k
        return None

    def _position_score(self, ts_code: str, td: date) -> Decimal:
        """Return a score for an existing position on day *td*.

        Uses the buy_priority_score from the latest available signal.
        Defaults to Decimal("0") — lowest score, making it a replacement candidate.
        """
        return Decimal("0")

    def _rebalance_for_buys(
        self,
        buy_candidates: list[_SignalCandidate],
        total_asset: Decimal,
    ) -> None:
        """Sell low-scored positions to free cash for high-priority buy signals.

        Only sells positions whose score is *strictly lower* than the lowest
        buy-candidate score, so high-scored holdings are never replaced by
        lower-scored new signals.
        """
        if not buy_candidates:
            return

        td = buy_candidates[0].bar.trade_date
        buy_scores = sorted({c.buy_priority_score for c in buy_candidates})
        min_buy_score = buy_scores[0] if buy_scores else Decimal("0")

        # Score existing positions, filter out ones already held by a buy candidate
        # (we don't want to sell a stock we're about to buy more of).
        buy_ts_codes = {c.ts_code for c in buy_candidates}
        scored: list[tuple[str, Decimal]] = []
        for ts_code, pos in list(self.positions.items()):
            if pos.shares <= 0 or ts_code in buy_ts_codes:
                continue
            score = self._position_score(ts_code, td)
            if score >= min_buy_score:
                continue  # worth keeping
            scored.append((ts_code, score))

        # Sort by score ascending (worst first), then by ts_code for determinism.
        scored.sort(key=lambda x: (x[1], x[0]))

        # Sell from the lowest-scored positions until cash suffices for all buys.
        # We use a rough estimate: the sum of (target_position * total_asset) for
        # stocks not already held at or above target.
        needed = Decimal("0")
        for c in buy_candidates:
            target_value = total_asset * Decimal(str(c.action.target_position))
            pos = self.positions.get(c.ts_code)
            cur = pos.avg_cost * pos.shares if pos else Decimal("0")
            needed += max(target_value - cur, Decimal("0"))

        if self.cash >= needed:
            return  # No rebalance required.

        for ts_code, score in scored:
            if self.cash >= needed:
                break
            bar = self._find_bar(ts_code, td)
            if bar is None or bar.is_limit_down:
                continue
            # Sell the full position.  _execute_action handles lot tracking,
            # trade recording, and cash update.
            action = SignalOutput(action="SELL_ALL", target_position=0.0)
            self._execute_action(
                action,
                ts_code,
                bar,
                total_asset,
                signal={"signal_type": "调仓", "reason": f"调仓卖出: 评分{score}低于新信号{min_buy_score}"},
                exit_reason="调仓",
                fill_bar=None,
            )
            self.signals.append({
                "ts_code": ts_code,
                "trade_date": td.isoformat(),
                "signal_type": "调仓",
                "action": "SELL_ALL",
                "target_position": 0.0,
                "exit_reason": "调仓",
            })

    def _exec_strategy_script(self, ctx: ScriptContext) -> None:
        """Execute script-mode on_bar() using the pre-compiled strategy."""
        execute_compiled_script(self._compiled_strategy, ctx)

    def _position_ratio(self, total_asset: Decimal) -> float:
        if total_asset <= 0:
            return 0.0
        pos_value = sum(
            p.avg_cost * p.shares for p in self.positions.values() if p.shares > 0
        )
        return float(pos_value / total_asset)

    def _book_asset(self) -> Decimal:
        """Total asset using avg_cost (book value) for consistent position ratio."""
        return self.cash + sum(
            p.avg_cost * p.shares for p in self.positions.values() if p.shares > 0
        )

    def _apply_rules(
        self,
        action: SignalOutput,
        ts_code: str,
        bar: KBar,
        position: Position | None,
        td: date,
    ) -> tuple[str, bool]:
        if bar.is_suspended:
            return "停牌", True
        if action.action.startswith("SELL"):
            pos = self.positions.get(ts_code)
            if not pos or pos.shares <= 0:
                return "无持仓", True
            # T+1 is enforced in _execute_action against the effective fill
            # date. A next-open order signaled on the buy date may be legal
            # when it actually fills on the following trading day.
        return "", False

    def _execute_action(
        self,
        action: SignalOutput,
        ts_code: str,
        bar: KBar,
        total_asset: Decimal,
        signal: dict | None = None,
        exit_reason: str | None = None,
        fill_bar: KBar | None = None,
    ) -> str | None:
        # Apply adjustment factor to bar prices. In qfq mode (default), this
        # is a no-op since providers already return前复权 prices; adj_factor
        # is consulted for audit/future extensibility.
        adjust_mode = self._adjust_mode()
        adj_open = self._adjust_price(bar.open, bar.adj_factor, adjust_mode)
        adj_high = self._adjust_price(bar.high, bar.adj_factor, adjust_mode)
        adj_low = self._adjust_price(bar.low, bar.adj_factor, adjust_mode)
        adj_close = self._adjust_price(bar.close, bar.adj_factor, adjust_mode)

        # Determine fill price based on BACKTEST_FILL_PRICE_MODE.
        # - "next_open" (default): fill at next day's open (fill_bar.open).
        #   Eliminates lookahead bias — strategy decides using data through
        #   td-1 and fills at td+1's open. Falls back to bar.close when
        #   fill_bar is unavailable (e.g., last trading day).
        # - "current_intraday": legacy behavior — simulate intraday fill on
        #   signal day using candle-path inference. Retained for backward
        #   compat with tests that don't pass fill_bar.
        # - "current_close": fill at signal day's close.
        fill_mode = self._fill_price_mode()
        slippage = Decimal(str(self.config.slippage_pct))

        if fill_mode == "next_open" and fill_bar is not None and fill_bar.open is not None:
            fill_price = self._adjust_price(fill_bar.open, fill_bar.adj_factor, adjust_mode)
            trade_date = fill_bar.trade_date
        elif fill_mode == "current_close":
            fill_price = adj_close
            trade_date = bar.trade_date
        else:
            # Legacy "current_intraday" path or fill_bar unavailable.
            price_path = self._infer_candle_path(adj_open, adj_high, adj_low, adj_close)
            if action.action == "BUY":
                fill_price = price_path[1] if adj_close >= adj_open else price_path[0]
            elif action.action in ("SELL_ALL", "SELL_PARTIAL"):
                fill_price = price_path[2] if adj_close >= adj_open else price_path[1]
            else:
                fill_price = adj_close
            trade_date = bar.trade_date

        if action.action == "BUY":
            if bar.is_limit_up:
                return "涨停不可买入"
            price = fill_price * (1 + slippage)
            target_value = total_asset * Decimal(str(action.target_position))
            current_value = Decimal("0")
            pos = self.positions.get(ts_code)
            if pos:
                current_value = pos.avg_cost * pos.shares
            delta_value = max(target_value - current_value, Decimal("0"))
            if delta_value <= 0 or price <= 0:
                return None
            raw_shares = int(delta_value / price)
            volume = (raw_shares // 100) * 100
            if volume <= 0:
                return None
            cost = self.calculator.calculate("买入", price * volume)
            total_cost = price * volume + cost.total_fee
            if total_cost > self.cash:
                affordable = int(self.cash / price) // 100 * 100
                volume = affordable
                while volume > 0:
                    cost = self.calculator.calculate("买入", price * volume)
                    total_cost = price * volume + cost.total_fee
                    if total_cost <= self.cash:
                        break
                    volume -= 100
                if volume <= 0:
                    return None

            balance_before = self._book_asset()
            pos_ratio_before = self._position_ratio(balance_before) if balance_before > 0 else 0

            self.cash -= total_cost
            if ts_code not in self.positions:
                self.positions[ts_code] = Position(ts_code=ts_code)
            pos = self.positions[ts_code]
            total_shares = pos.shares + volume
            pos.avg_cost = (pos.avg_cost * pos.shares + price * volume) / total_shares if total_shares > 0 else price
            pos.shares = total_shares

            balance_after = self._book_asset()
            pos_ratio_after = self._position_ratio(balance_after) if balance_after > 0 else 0

            self._entry_dates[ts_code] = trade_date
            self._entry_prices[ts_code] = price
            self._highest_since_entry[ts_code] = bar.high
            self._lowest_since_entry[ts_code] = bar.low
            if ts_code not in self._open_lots:
                self._open_lots[ts_code] = []
            self._open_lots[ts_code].append(_LotEntry(
                ts_code=ts_code,
                shares=volume,
                cost=price,
                entry_date=trade_date,
                entry_fee=cost.total_fee,
            ))
            sig_reason = (signal.get("reason", "") if signal else "") or "信号触发: 买入"

            self.trades.append(TradeRecord(
                ts_code=ts_code,
                trade_date=trade_date,
                direction="买入",
                price=price,
                volume=volume,
                amount=price * volume,
                cost=cost,
                signal_type="买入",
                action="BUY",
                signal_reason=sig_reason,
                target_position=float(action.target_position),
                position_before=pos_ratio_before,
                position_after=pos_ratio_after,
                pnl=Decimal("0"),
                balance_before=balance_before,
                balance_after=balance_after,
                holding_days=0,
                exit_reason="",
            ))
            return None

        elif action.action in ("SELL_ALL", "SELL_PARTIAL"):
            if bar.is_limit_down:
                return "跌停不可卖出"
            price = fill_price * (1 - slippage)
            pos = self.positions.get(ts_code)
            if not pos or pos.shares <= 0:
                return None

            lots = self._open_lots.get(ts_code, [])
            eligible_shares = sum(
                lot.shares for lot in lots if lot.entry_date < trade_date
            )
            if eligible_shares <= 0:
                return "T+1 当日买入不可卖出"

            if action.action == "SELL_ALL":
                volume = pos.shares
            else:
                target_value = total_asset * Decimal(str(action.target_position))
                current_value = pos.avg_cost * pos.shares
                sell_value = max(current_value - target_value, Decimal("0"))
                if sell_value <= 0 or price <= 0:
                    return None
                raw_shares = int(sell_value / price)
                volume = min(pos.shares, (raw_shares // 100) * 100)

            volume = min(volume, eligible_shares)

            if volume <= 0:
                return None

            cost = self.calculator.calculate("卖出", price * volume)
            net_amount = price * volume - cost.total_fee

            balance_before = self._book_asset()
            pos_ratio_before = self._position_ratio(balance_before) if balance_before > 0 else 0

            self.cash += net_amount
            pos.shares -= volume

            balance_after = self._book_asset()
            pos_ratio_after = self._position_ratio(balance_after) if balance_after > 0 else 0

            entry_date = self._entry_dates.get(ts_code, trade_date)
            holding_days = (trade_date - entry_date).days

            remaining_sell = volume
            remaining_exit_fee = cost.total_fee
            matched_net_pnl = Decimal("0")
            while remaining_sell > 0 and lots:
                lot = lots[0]
                if lot.entry_date >= trade_date:
                    break
                lot_shares = min(remaining_sell, lot.shares)
                entry_fee = (
                    lot.entry_fee
                    if lot_shares == lot.shares
                    else lot.entry_fee * Decimal(lot_shares) / Decimal(lot.shares)
                )
                exit_fee = (
                    remaining_exit_fee
                    if lot_shares == remaining_sell
                    else remaining_exit_fee * Decimal(lot_shares) / Decimal(remaining_sell)
                )
                matched_cost = lot.cost * lot_shares
                gross_pnl = (price - lot.cost) * lot_shares
                net_pnl = gross_pnl - entry_fee - exit_fee
                return_rate = net_pnl / matched_cost if matched_cost > 0 else Decimal("0")
                self._closed_lots.append(_ClosedLot(
                    ts_code=ts_code,
                    shares=lot_shares,
                    entry_price=lot.cost,
                    entry_date=lot.entry_date,
                    exit_price=price,
                    exit_date=trade_date,
                    entry_fee=entry_fee,
                    exit_fee=exit_fee,
                    gross_pnl=gross_pnl,
                    net_pnl=net_pnl,
                    return_rate=return_rate,
                    holding_days=(trade_date - lot.entry_date).days,
                    exit_reason=exit_reason or "策略信号",
                ))
                matched_net_pnl += net_pnl
                lot.entry_fee -= entry_fee
                lot.shares -= lot_shares
                remaining_exit_fee -= exit_fee
                remaining_sell -= lot_shares
                if lot.shares <= 0:
                    lots.pop(0)

            pnl = matched_net_pnl

            if pos.shares <= 0:
                self._entry_dates.pop(ts_code, None)
                self._entry_prices.pop(ts_code, None)
                self._highest_since_entry.pop(ts_code, None)
                self._lowest_since_entry.pop(ts_code, None)
                self._open_lots.pop(ts_code, None)

            reason = exit_reason or "策略信号"
            sig_type = signal.get("signal_type", "卖出") if signal else reason
            sig_reason = (
                (signal.get("reason", "") if signal else "")
                or (f"风险控制: {reason}" if exit_reason else f"信号触发: {sig_type}")
            )
            direction = "卖出"
            self.trades.append(TradeRecord(
                ts_code=ts_code,
                trade_date=trade_date,
                direction=direction,
                price=price,
                volume=volume,
                amount=price * volume,
                cost=cost,
                signal_type=sig_type,
                action=action.action,
                signal_reason=sig_reason,
                target_position=float(action.target_position),
                position_before=pos_ratio_before,
                position_after=pos_ratio_after,
                pnl=pnl,
                balance_before=balance_before,
                balance_after=balance_after,
                holding_days=holding_days,
                exit_reason=reason,
            ))

            if pos.shares == 0:
                del self.positions[ts_code]
            return None

        return None

    # ------------------------------------------------------------------
    # 单日最大买入只数（max_daily_buys）
    # ------------------------------------------------------------------

    def _effective_trade_date(self, bar: KBar, fill_bar: KBar | None) -> date:
        """成交发生日：next_open 模式且 fill_bar 可用时为 fill_bar 的交易日，
        否则为信号当日的交易日。限额按成交日统计，与无未来函数执行模型一致。
        """
        if self._fill_price_mode() == "next_open" and fill_bar is not None:
            return fill_bar.trade_date
        return bar.trade_date

    def _daily_buy_allowed(self, ts_code: str, trade_date: date) -> bool:
        """该股票在 trade_date 当日是否仍可下单买入。

        - max_daily_buys <= 0 表示不限制。
        - 同一交易日已买入过的股票不重复占用限额（只计 1 只）。
        - 否则当该日已买入股票数达到上限时返回 False。
        """
        if self.config.max_daily_buys <= 0:
            return True
        bought = self._daily_bought.setdefault(trade_date, set())
        if ts_code in bought:
            return True
        return len(bought) < self.config.max_daily_buys

    def _record_daily_buy(self, ts_code: str, trade_date: date) -> None:
        """记录 trade_date 当日买入了一只股票（仅在实际增加持仓份额后调用）。"""
        self._daily_bought.setdefault(trade_date, set()).add(ts_code)

    def _fill_price_mode(self) -> str:
        """Read BACKTEST_FILL_PRICE_MODE from settings, defaulting to 'next_open'.

        Imported lazily to avoid creating asyncio primitives at module import
        (mirrors the ws_producer pattern).
        """
        try:
            from app.core.config import get_settings
            return get_settings().backtest_fill_price_mode
        except Exception:
            return "next_open"

    # ------------------------------------------------------------------
    # 避险切换（跷跷板）overlay 实现
    # ------------------------------------------------------------------

    def _detect_benchmark_state(self, td: date) -> str:
        """基于内存基准 K 线（截至 td，与策略可见的 ``ctx.benchmark`` 对齐，防前视）
        判定大盘状态，返回 ``'up' | 'neutral' | 'down'``。

        复用 seesaw 的纯函数 :func:`classify_market_state`，保证与实时判定逻辑一致。
        """
        arrays = self._defensive_benchmark_arrays
        if arrays is None or not arrays.dates:
            return "neutral"
        # 取截至 td（含）的收盘价序列
        idx = None
        for i, d in enumerate(arrays.dates):
            if d <= td:
                idx = i
            else:
                break
        if idx is None:
            return "neutral"
        closes = arrays.close[: idx + 1]
        try:
            from app.data.seesaw import DefensiveRules, classify_market_state
        except Exception:
            return "neutral"
        rules = DefensiveRules()
        for k, v in self._defensive_rules.items():
            if k in DefensiveRules.__dataclass_fields__:
                if k in ("drop_threshold", "high_drop_pct", "vol_expand_thresh"):
                    try:
                        v = Decimal(str(v))
                    except Exception:
                        continue
                try:
                    setattr(rules, k, v)
                except Exception:
                    continue
        if len(closes) < rules.ma_long + 1:
            return "neutral"
        state, _detail = classify_market_state(closes, rules)
        return state

    def _stock_ret_n(self, code: str, td: date, period: int = 10) -> float | None:
        """返回 code 截至 td 的 period 日区间涨幅；数据不足返回 None。"""
        kl = self._all_klines.get(code, [])
        closes = [float(k.close) for k in kl if k.trade_date <= td and k.close is not None]
        if len(closes) < period + 1:
            return None
        ref = closes[-period - 1]
        if ref <= 0:
            return None
        return closes[-1] / ref - 1.0

    def _defensive_pick_best(self, pool_codes: list[str], td: date, top_k: int) -> list[str]:
        """从避险池候选里择优：按近 N 日相对强度（抗跌性）排序，取涨幅最高（最抗跌）的前 top_k 只。

        大盘弱时跌幅最小者 = 最抗跌，作为避险标的的理想候选。数据不足的标的垫底（
        新上市/停牌），不会入选除非候选不足。
        """
        if top_k <= 0 or top_k >= len(pool_codes):
            return pool_codes
        window = self._defensive_pick_ret_window
        scored = []
        for c in pool_codes:
            r = self._stock_ret_n(c, td, window)
            scored.append((r if r is not None else -99.0, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _r, c in scored[:top_k]]

    def _execute_defensive_sell(self, code: str, td: date, next_td: "date | None",
                                total_asset: Decimal, reason: str) -> None:
        """避险路径下的清仓（不改 ctx.state，仅交易与信号记录）。"""
        bar = self._find_bar(code, td)
        if bar is None:
            return
        fill = self._find_bar(code, next_td) if next_td is not None else None
        self._execute_action(
            SignalOutput(action="SELL_ALL", target_position=0.0),
            code, bar, total_asset,
            signal={"signal_type": "避险切换", "reason": reason},
            exit_reason="避险切换",
            fill_bar=fill,
        )
        self.signals.append({
            "ts_code": code,
            "trade_date": td.isoformat(),
            "signal_type": "避险切换",
            "action": "SELL_ALL",
            "target_position": 0.0,
            "exit_reason": "避险切换",
            "reason": reason,
        })

    def _execute_defensive_buy(self, code: str, td: date, next_td: "date | None",
                               weight: float, total_asset: Decimal, reason: str) -> None:
        bar = self._find_bar(code, td)
        if bar is None:
            return
        fill = self._find_bar(code, next_td) if next_td is not None else None
        self._execute_action(
            SignalOutput(action="BUY", target_position=float(weight)),
            code, bar, total_asset,
            signal={"signal_type": "避险买入", "reason": reason, "target_position": float(weight)},
            fill_bar=fill,
        )
        self.signals.append({
            "ts_code": code,
            "trade_date": td.isoformat(),
            "signal_type": "避险买入",
            "action": "BUY",
            "target_position": float(weight),
            "exit_reason": "",
            "reason": reason,
        })

    def _run_defensive_overlay(self, td: date, next_td: "date | None", total_asset: Decimal) -> None:
        """避险切换 overlay（V3）：

        - 非避险态 + 大盘 down     → 进入避险：
            * 一键清仓全部策略持仓（不保留、无逐股判断）
            * 从避险池等权买入全部标的（defensive_pick_k<=0）；>0 时择优选前 K 只
        - 避险态 + 大盘 down       → 维持（持有避险股，等待转强）
        - 避险态 + 大盘 非down     → 一键清仓避险股并退出避险态（记录分段）
        - 非避险态 + 大盘 非down   → 正常策略（无动作）

        执行直接调用 :meth:`_execute_action`（不走候选管道），不受
        ``max_daily_buys``/再平衡规划器干扰；成交均在 ``next_td`` 开盘发生，
        与既有 next_open 模型一致，无未来函数。
        """
        state = self._detect_benchmark_state(td)
        is_down = state == "down"

        # 避险池候选 = 全部启用标的（不限制前几）
        pool_codes = [c for c in self._defensive_pool_codes if c in self._all_klines]

        if not self._defensive_mode:
            if not is_down:
                return  # 正常策略，无动作
            # ── 进入避险：一键清仓全部策略持仓 ──
            for code in list(self.positions.keys()):
                bar = self._find_bar(code, td)
                if bar is None:
                    continue
                self._execute_defensive_sell(code, td, next_td, total_asset, "基准走弱(避险)")
            # 等权买入全部避险池标的（defensive_pick_k<=0）；>0 时择优选前 K 只
            chosen: list[str] = []
            if pool_codes:
                chosen = self._defensive_pick_best(pool_codes, td, self._defensive_pick_k)
                chosen = [c for c in chosen if not (self.positions.get(c) and self.positions[c].shares > 0)]
                if chosen:
                    weight = 1.0 / len(chosen)
                    for code in chosen:
                        self._execute_defensive_buy(code, td, next_td, weight, total_asset, "基准走弱-配置避险库")
            # 记录实际买入的避险标的（按池内优先级排序，剔除未被选入者）
            _chosen_set = set(chosen)
            holdings = [c for c in pool_codes if c in _chosen_set]
            self._defensive_episodes.append({
                "entry_trade_date": next_td,
                "exit_trade_date": None,
                "holdings": holdings,
            })
            self._defensive_mode = True
            return

        # ── 避险态 ──
        if is_down:
            return  # 维持避险：持有避险股，等待大盘转强

        # ── 避险态 → 退出（大盘转非down）：一键清仓避险股并回归策略 ──
        for code in list(self.positions.keys()):
            if code in self._defensive_pool_codes:
                bar = self._find_bar(code, td)
                if bar is None:
                    continue
                self._execute_defensive_sell(code, td, next_td, total_asset, "基准转强(回归策略)")
        if self._defensive_episodes and self._defensive_episodes[-1].get("exit_trade_date") is None:
            self._defensive_episodes[-1]["exit_trade_date"] = next_td
        self._defensive_mode = False
        self._defensive_kept_strategy.clear()

    def _build_defensive_stats(self) -> dict[str, Any]:
        """汇总避险切换统计：分段收益、链式累计、对总收益的贡献、明细。"""
        initial = float(self.config.initial_cash)
        equity_by_date = {e["date"]: e["total_asset"] for e in self.equity_curve}
        last_date = self.equity_curve[-1]["date"] if self.equity_curve else None
        episodes: list[dict[str, Any]] = []
        chained = 1.0
        contribution = 0.0
        # 构建基准收盘价按日期的快速查找表
        bm_close_by_date: dict[str, float] = {}
        if self._defensive_benchmark_arrays is not None:
            for d, c in zip(self._defensive_benchmark_arrays.dates, self._defensive_benchmark_arrays.close):
                bm_close_by_date[str(d)] = float(c)
        for ep in self._defensive_episodes:
            entry = ep.get("entry_trade_date")
            exit_ = ep.get("exit_trade_date")
            if entry is None:
                continue
            entry_iso = entry.isoformat() if hasattr(entry, "isoformat") else str(entry)
            exit_iso = exit_.isoformat() if (exit_ is not None and hasattr(exit_, "isoformat")) else last_date
            ev_in = equity_by_date.get(entry_iso)
            ev_out = equity_by_date.get(exit_iso) if exit_iso else None
            if ev_in is None or ev_in <= 0:
                continue
            ep_return = (ev_out - ev_in) / ev_in if ev_out is not None else 0.0
            chained *= (1 + ep_return)
            contribution += (ev_out - ev_in) if ev_out is not None else 0.0
            # 同期基准涨跌幅
            bm_entry_close = bm_close_by_date.get(entry_iso)
            bm_exit_close = bm_close_by_date.get(str(exit_iso)) if exit_iso else None
            bm_return_pct: float | None = None
            if bm_entry_close and bm_entry_close > 0 and bm_exit_close is not None and bm_exit_close > 0:
                bm_return_pct = round((bm_exit_close - bm_entry_close) / bm_entry_close * 100, 4)
            episodes.append({
                "entry_date": entry_iso,
                "exit_date": exit_iso,
                "holdings": ep.get("holdings", []),
                "return_pct": round(float(ep_return) * 100, 4),
                "benchmark_return_pct": bm_return_pct,
            })
        defensive_days = sum(1 for e in self.equity_curve if e.get("defensive"))
        return {
            "enabled": self.config.defensive_switch_enabled,
            "active": self._defensive_switch_active,
            "benchmark_code": self.config.defensive_benchmark_code or self.config.benchmark_code,
            # 实际买入只数：pick_k==0 表示全池等权；否则取 min(pick_k, 池长)
            "pool_size": (
                len(self._defensive_pool_codes)
                if self._defensive_pick_k == 0
                else min(self._defensive_pick_k, len(self._defensive_pool_codes))
            ),
            "periods": len(episodes),
            "days": defensive_days,
            "return_pct": round(float(chained - 1) * 100, 4),
            "contribution_pct": round(float(contribution) / initial * 100, 4) if initial > 0 else 0.0,
            "final_mode": "defensive" if self._defensive_mode else "normal",
            "detail": episodes,
        }

    def _compute_results(self) -> dict[str, Any]:
        if not self.equity_curve:
            pnl_analysis = self._build_pnl_analysis()
            return {
                "total_return": 0,
                "annual_return": 0,
                "sharpe_ratio": 0,
                "sortino_ratio": 0,
                "calmar_ratio": 0,
                "max_drawdown": 0,
                "annual_vol": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "avg_win": 0,
                "avg_loss": 0,
                "max_consecutive_losses": 0,
                "avg_holding_days": 0,
                "total_fees": 0,
                "trade_count": 0,
                "monthly_returns": {},
                "daily_returns": [],
                "pnl_analysis": pnl_analysis,
                "closed_lots": pnl_analysis["closed_lots"],
                "stock_rankings": pnl_analysis["stock_rankings"],
                "performance": {
                    "monthly_returns": {},
                    "daily_returns": [],
                    "pnl_analysis": pnl_analysis,
                },
                "trade_records": [],
                "equity_curve": [],
                "defensive": self._build_defensive_stats(),
            }

        initial = float(self.config.initial_cash)
        final = self.equity_curve[-1]["total_asset"]
        total_return = (final - initial) / initial if initial > 0 else 0

        dates = [e["date"] for e in self.equity_curve]
        values = [e["total_asset"] for e in self.equity_curve]

        daily_returns = []
        for i in range(1, len(values)):
            if values[i - 1] > 0:
                daily_returns.append((values[i] - values[i - 1]) / values[i - 1])

        annual_return = 0.0
        if len(dates) >= 2:
            from datetime import date as date_cls
            d0 = date_cls.fromisoformat(dates[0])
            d1 = date_cls.fromisoformat(dates[-1])
            years = max((d1 - d0).days / 365.25, 0.01)
            annual_return = ((1 + total_return) ** (1 / years)) - 1

        sharpe = 0.0
        sortino = 0.0
        if daily_returns:
            import statistics
            mean_r = statistics.mean(daily_returns)
            std_r = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 0
            if std_r > 0:
                sharpe = mean_r / std_r * (252 ** 0.5)
            downside = [r for r in daily_returns if r < 0]
            if downside:
                downside_std = (sum(r ** 2 for r in downside) / len(daily_returns)) ** 0.5
                if downside_std > 0:
                    sortino = mean_r / downside_std * (252 ** 0.5)

        max_dd = 0.0
        peak = values[0]
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        calmar = annual_return / max_dd if max_dd > 0 else 0.0

        annual_vol = 0.0
        if daily_returns and len(daily_returns) > 1:
            import statistics
            annual_vol = statistics.stdev(daily_returns) * (252 ** 0.5)

        gross_profit = Decimal("0")
        gross_loss = Decimal("0")
        win_count = 0
        total_closed = len(self._closed_lots)
        pnl_list: list[Decimal] = []
        holding_days_list: list[int] = []
        for lot in self._closed_lots:
            pnl_list.append(lot.pnl)
            holding_days_list.append(lot.holding_days)
            if lot.pnl > 0:
                gross_profit += lot.pnl
                win_count += 1
            else:
                gross_loss += lot.pnl

        win_rate = win_count / total_closed if total_closed > 0 else 0
        profit_factor = float(gross_profit / abs(gross_loss)) if gross_loss != 0 else float("inf") if gross_profit > 0 else 0
        avg_win = float(gross_profit / win_count) if win_count > 0 else 0
        avg_loss = float(gross_loss / (total_closed - win_count)) if total_closed - win_count > 0 else 0

        max_consec_losses = 0
        consec = 0
        for lot in self._closed_lots:
            if lot.pnl < 0:
                consec += 1
                max_consec_losses = max(max_consec_losses, consec)
            else:
                consec = 0

        avg_holding = sum(holding_days_list) / len(holding_days_list) if holding_days_list else 0

        total_fees = sum(
            t.cost.total_fee for t in self.trades
        )
        pnl_analysis = self._build_pnl_analysis()

        monthly_returns: dict[str, float] = {}
        if len(dates) >= 2:
            from datetime import date as date_cls
            month_start_val = values[0]
            current_month = dates[0][:7]
            for i, d in enumerate(dates):
                month_key = d[:7]
                if month_key != current_month:
                    if month_start_val > 0:
                        monthly_returns[current_month] = (values[i - 1] - month_start_val) / month_start_val
                    month_start_val = values[i - 1]
                    current_month = month_key
            if month_start_val > 0 and values:
                monthly_returns[current_month] = (values[-1] - month_start_val) / month_start_val

        defensive_stats = self._build_defensive_stats()

        return {
            "total_return": round(total_return, 8),
            "defensive": defensive_stats,
            "annual_return": round(annual_return, 8),
            "sharpe_ratio": round(sharpe, 8),
            "sortino_ratio": round(sortino, 8),
            "calmar_ratio": round(calmar, 8),
            "max_drawdown": round(max_dd, 8),
            "annual_vol": round(annual_vol, 8),
            "win_rate": round(win_rate, 8),
            "profit_factor": round(profit_factor, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "max_consecutive_losses": max_consec_losses,
            "avg_holding_days": round(avg_holding, 1),
            "total_fees": round(float(total_fees), 2),
            "trade_count": len(self.trades),
            "monthly_returns": {k: round(v, 6) for k, v in monthly_returns.items()},
            "daily_returns": [round(r, 6) for r in daily_returns],
            "pnl_analysis": pnl_analysis,
            "closed_lots": pnl_analysis["closed_lots"],
            "stock_rankings": pnl_analysis["stock_rankings"],
            "performance": {
                "defensive": defensive_stats,
                "initial_cash": float(initial),
                "final_asset": final,
                "total_return_pct": round(total_return * 100, 4),
                "annual_return_pct": round(annual_return * 100, 4),
                "sharpe_ratio": round(sharpe, 4),
                "sortino_ratio": round(sortino, 4),
                "calmar_ratio": round(calmar, 4),
                "max_drawdown_pct": round(max_dd * 100, 4),
                "annual_vol_pct": round(annual_vol * 100, 4),
                "win_rate_pct": round(win_rate * 100, 4),
                "profit_factor": round(profit_factor, 2),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "max_consecutive_losses": max_consec_losses,
                "avg_holding_days": round(avg_holding, 1),
                "total_fees": round(float(total_fees), 2),
                "strategy_error_count": len(self.strategy_errors),
                "monthly_returns": {k: round(v, 6) for k, v in monthly_returns.items()},
                "daily_returns": [round(r, 6) for r in daily_returns],
                # 仅保留标量汇总，closed_lots / stock_rankings 已拆分到独立表
                "pnl_analysis": {
                    "closed_lot_count": pnl_analysis["closed_lot_count"],
                    "winning_lot_count": pnl_analysis["winning_lot_count"],
                    "losing_lot_count": pnl_analysis["losing_lot_count"],
                    "breakeven_lot_count": pnl_analysis["breakeven_lot_count"],
                    "stock_count": pnl_analysis["stock_count"],
                    "matched_cost": pnl_analysis["matched_cost"],
                    "gross_pnl": pnl_analysis["gross_pnl"],
                    "entry_fees": pnl_analysis["entry_fees"],
                    "exit_fees": pnl_analysis["exit_fees"],
                    "total_fees": pnl_analysis["total_fees"],
                    "net_pnl": pnl_analysis["net_pnl"],
                    "return_rate": pnl_analysis["return_rate"],
                    "win_rate": pnl_analysis["win_rate"],
                    "avg_holding_days": pnl_analysis["avg_holding_days"],
                },
            },
            "trade_records": [
                {
                    "ts_code": t.ts_code,
                    "trade_date": t.trade_date.isoformat(),
                    "direction": self._serialize_direction(t),
                    "price": float(t.price),
                    "volume": t.volume,
                    "amount": float(t.amount),
                    "commission": float(t.cost.commission),
                    "stamp_tax": float(t.cost.stamp_tax),
                    "transfer_fee": float(t.cost.transfer_fee),
                    "total_fee": float(t.cost.total_fee),
                    "action": t.action,
                    "signal_reason": t.signal_reason,
                    "target_position": t.target_position,
                    "position_before": t.position_before,
                    "position_after": t.position_after,
                    "pnl": float(t.pnl),
                    "balance_before": float(t.balance_before),
                    "balance_after": float(t.balance_after),
                    "holding_days": t.holding_days,
                    "exit_reason": t.exit_reason,
                }
                for t in self.trades
            ],
            "equity_curve": self.equity_curve,
            "signal_log": self.signals,
            "strategy_errors": self.strategy_errors,
            "execution_assumptions": {
                "execution_timeframe": self.config.execution_timeframe,
                "signal_timeframe": self.config.signal_timeframe,
                "price_path_simulation": True,
                "slippage_pct": self.config.slippage_pct,
                "stop_loss_pct": self.config.stop_loss_pct,
                "take_profit_pct": self.config.take_profit_pct,
                "trailing_stop_pct": self.config.trailing_stop_pct,
                "time_stop_days": self.config.time_stop_days,
            },
        }

    def _build_pnl_analysis(self) -> dict[str, Any]:
        stock_totals: dict[str, dict[str, Any]] = {}
        closed_lots: list[dict[str, Any]] = []
        total_entry_fees = Decimal("0")
        total_exit_fees = Decimal("0")
        total_matched_cost = Decimal("0")
        total_gross_pnl = Decimal("0")
        total_net_pnl = Decimal("0")
        total_holding_days = 0
        winning_lots = 0
        losing_lots = 0

        for lot in self._closed_lots:
            matched_cost = lot.entry_price * lot.shares
            total_entry_fees += lot.entry_fee
            total_exit_fees += lot.exit_fee
            total_matched_cost += matched_cost
            total_gross_pnl += lot.gross_pnl
            total_net_pnl += lot.net_pnl
            total_holding_days += lot.holding_days
            winning_lots += int(lot.net_pnl > 0)
            losing_lots += int(lot.net_pnl < 0)

            aggregate = stock_totals.setdefault(lot.ts_code, {
                "ts_code": lot.ts_code,
                "closed_lot_count": 0,
                "winning_lot_count": 0,
                "losing_lot_count": 0,
                "matched_cost": Decimal("0"),
                "gross_pnl": Decimal("0"),
                "total_fees": Decimal("0"),
                "net_pnl": Decimal("0"),
                "holding_days": 0,
            })
            aggregate["closed_lot_count"] += 1
            aggregate["winning_lot_count"] += int(lot.net_pnl > 0)
            aggregate["losing_lot_count"] += int(lot.net_pnl < 0)
            aggregate["matched_cost"] += matched_cost
            aggregate["gross_pnl"] += lot.gross_pnl
            aggregate["total_fees"] += lot.entry_fee + lot.exit_fee
            aggregate["net_pnl"] += lot.net_pnl
            aggregate["holding_days"] += lot.holding_days

            closed_lots.append({
                "ts_code": lot.ts_code,
                "entry_date": lot.entry_date.isoformat(),
                "exit_date": lot.exit_date.isoformat(),
                "entry_price": float(lot.entry_price),
                "exit_price": float(lot.exit_price),
                "shares": lot.shares,
                "entry_fee": float(lot.entry_fee),
                "exit_fee": float(lot.exit_fee),
                "gross_pnl": float(lot.gross_pnl),
                "net_pnl": float(lot.net_pnl),
                "return_rate": float(lot.return_rate),
                "holding_days": lot.holding_days,
                "exit_reason": lot.exit_reason,
            })

        stock_rankings: list[dict[str, Any]] = []
        for aggregate in stock_totals.values():
            count = aggregate.pop("closed_lot_count")
            holding_days = aggregate.pop("holding_days")
            matched_cost = aggregate["matched_cost"]
            net_pnl = aggregate["net_pnl"]
            wins = aggregate["winning_lot_count"]
            stock_rankings.append({
                **aggregate,
                "closed_lot_count": count,
                "return_rate": float(net_pnl / matched_cost) if matched_cost > 0 else 0.0,
                "win_rate": wins / count if count else 0.0,
                "avg_holding_days": holding_days / count if count else 0.0,
            })
        stock_rankings.sort(key=lambda item: (-item["net_pnl"], item["ts_code"]))
        for ranking in stock_rankings:
            for key in ("matched_cost", "gross_pnl", "total_fees", "net_pnl"):
                ranking[key] = float(ranking[key])

        lot_count = len(self._closed_lots)
        total_fees = total_entry_fees + total_exit_fees
        return {
            "closed_lot_count": lot_count,
            "winning_lot_count": winning_lots,
            "losing_lot_count": losing_lots,
            "breakeven_lot_count": lot_count - winning_lots - losing_lots,
            "stock_count": len(stock_rankings),
            "matched_cost": float(total_matched_cost),
            "gross_pnl": float(total_gross_pnl),
            "entry_fees": float(total_entry_fees),
            "exit_fees": float(total_exit_fees),
            "total_fees": float(total_fees),
            "net_pnl": float(total_net_pnl),
            "return_rate": float(total_net_pnl / total_matched_cost) if total_matched_cost > 0 else 0.0,
            "win_rate": winning_lots / lot_count if lot_count else 0.0,
            "avg_holding_days": total_holding_days / lot_count if lot_count else 0.0,
            "closed_lots": closed_lots,
            "stock_rankings": stock_rankings,
        }

    @staticmethod
    def _serialize_direction(trade: TradeRecord) -> str:
        if trade.action == "SELL_ALL":
            return SellDirection("全部卖出")
        if trade.action == "SELL_PARTIAL":
            return SellDirection("部分卖出")
        return trade.direction
