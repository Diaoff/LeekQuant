# -*- coding: utf-8 -*-
"""
波动率自适应趋势回调策略 (VATP - Volatility-Adaptive Trend Pullback)
======================================================================

设计目标
--------
针对回测中发现的问题（190→191：仓位从 0.5 降到 0.3 反而收益下滑，说明原策略
对仓位高度敏感、信号质量与出场机制存在缺陷），本策略以两条硬指标为目标：

  1. 显著压低最大回撤（目标 < 现有版本的 47%~54%，争取 15% 以内）
  2. 提升胜率（目标 > 现有版本的 37%~39%，争取 50%+）

核心思路：只在大趋势向上的环境下，做"趋势内的浅回调"——这是高胜率、低回撤的
经典形态。配合三道风控把单笔与组合风险钉死：

  A. 趋势过滤：价格 > MA(fast_ma) > MA(trend_ma)，且价格显著高于 trend_ma 根
     前的价位（已确立上行趋势）。杜绝在震荡/下跌段频繁被打止损。
  B. 入场质量：价格回落到 fast_ma 附近（浅回调）+ 反转 K 线（今收>昨收 且
     收回到 fast_ma 上方）+ 回调日缩量（抛压衰竭）。RSI 不处于极端。
  C. 波动率过滤：ATR% 过高的标的固定止损易被扫，直接跳过——这是控制回撤的
     关键开关。
  D. 仓位管理：单笔风险预算（risk_per_trade）÷ 止损比例 = 理论仓位，再受
     单股上限与组合总敞口上限夹逼。每只股票最多亏 ~1.2% 权益，组合同时止损
     的最坏情形也被敞口上限锁死。
  E. 出场：策略内自包含 硬止损 / 移动止盈 / 固定止盈 / 时间止损 / 趋势破位清仓。
     五道出场互相独立，确保任何行情下都有明确的退出路径。

为什么自包含实现止损/止盈
-------------------------
ctx 不暴露持仓成本价，引擎的全局风控（回测风险参数）虽可用，但要求用户在界面上
正确配置。为避免"忘设风险参数就裸奔"，本策略用信号日收盘近似记录入场价，在
generate_signal 内部实现软止损/止盈。近似价与真实成交价（次日开盘）仅有隔夜
跳空之差，对回测结论影响极小，却能保证策略"复制即用、自带风控"。

⚠️ 强烈建议同时在回测风险参数中设置相同的止损/止盈值，作为双保险
（详见文末「推荐回测风险参数」）。

可调参数（在下方 PARAMS 中修改，保存即生效）
-------------------------------------------
所有参数均为可直接改的常量。默认值是针对 A 股日线、目标"低回撤+高胜率"的
经验起点；调优方向见每个参数注释。

关键约束：策略上下文 ctx 仅暴露最近 60 根 K 线（滑窗），因此 trend_ma 必须
< 60 且留余量，否则长均线在窗口边缘无有效值。

进阶：ctx.benchmark（大盘指数窗口，同样按当前交易日对齐、天然防前视）可用于
大盘择时闸门——只在市场处于多头时开仓、弱势市道直接观望，这是降低回撤最划算
的一步。需回测配置 benchmark_code（如 000300.SH）才能生效；未配置则自动降级为
"不约束"。ctx.extra / ctx.all_klines 也已在引擎层就绪，可按需接入行业/横截面数据。
"""

# ============================ 可调参数 ============================
PARAMS = {
    # ---- 趋势过滤 ----
    "trend_ma": 40,            # 长期均线周期(必须<60)。↑调大=只做强趋势、信号更少更干净；↓调小=信号更多但噪声大
    "fast_ma": 20,             # 中期均线周期(回调参考/多空分界)。默认20
    "min_up_pct": 0.05,        # 价格需比 trend_ma 根前高出至少该比例，才算"已确立上行趋势"。↑更严格

    # ---- 大盘择时闸门（新增：ctx.benchmark）----
    # 仅在市场处于多头时开仓，弱势市道直接观望。这是控制回撤最划算的一步，
    # 依赖回测配置里的 benchmark_code（如 000300.SH）。若回测未配置 benchmark，
    # 闸门自动降级为"不约束"（策略仍可正常运行）。
    "benchmark_trend_filter": True,    # 是否启用大盘择时闸门。False=忽略大盘、只按个股信号交易
    "benchmark_ma": 20,                # 大盘均线周期(<60)。价格需在其上方且均线上扬才视为多头
    "benchmark_min_up_pct": 0.0,       # 大盘需较 benchmark_ma 根前上涨至少该比例，否则视为弱势。↑更严格
    "benchmark_rs_filter": False,      # 是否额外要求"个股强于大盘"（个股近期涨幅 >= 大盘），专挑领涨股

    # ---- 入场：趋势内浅回调 ----
    "pullback_pct": 0.03,      # 价格相对 fast_ma 的最大正向偏离(回调深度阈值)，超过则不视为回调。↓=只在更浅的回调买
    "rev_confirm": True,       # 是否需要反转确认(今收>昨收 且 收回到 fast_ma 上方)。False=仅价格触线即买(信号多、质量降)
    "vol_spike_max": 1.2,      # 近5日均量 / 近20日均量 的上限；超过说明近期放量(疑似派发)，不买。=9 表示不限量能
    "rsi_len": 14,             # RSI 周期
    "rsi_max": 70,             # RSI 上限，超过视为超买顶部，不买
    "rsi_min": 40,             # RSI 下限，低于此视为弱势，不买(避免在下行反弹中接刀)

    # ---- 波动率过滤(控制回撤的关键) ----
    "atr_len": 20,             # ATR 周期
    "atr_pct_max": 0.06,       # ATR/收盘价的上限；超过说明波动过大、固定止损易被扫，跳过。↓更保守(回撤更低)、↑信号更多

    # ---- 出场(策略内自包含) ----
    "stop_loss_pct": 0.06,     # 硬止损比例(相对近似入场价)。⚠️必须与回测风险参数 stop_loss_pct 保持一致
    "take_profit_pct": 0.10,   # 固定止盈比例(1.67R)。↑=让利润多跑、胜率略降；↓=落袋更快、胜率略升
    "trailing_activation_pct": 0.06,  # 移动止盈激活阈值(浮盈达到该比例后才启动跟踪)
    "trailing_stop_pct": 0.06, # 移动止盈回撤比例(从峰值回落该比例则离场)。负责"让盈利多跑、截断回落"
    "time_stop_days": 20,      # 持仓超过该交易日数且仍浮亏，强制时间止损(避免资金长期被套)

    # ---- 仓位管理(风险预算) ----
    "risk_per_trade": 0.02,    # 单笔最大可承受权益亏损(分数)。默认2%
    "max_position_size": 0.20, # 单股最大仓位(占权益)。默认20%；与 stop_loss_pct 配合→单笔最坏亏 ~1.2% 权益
    "max_portfolio_exposure": 0.60,  # 组合总敞口上限。默认60%，是控制最大回撤的总闸

    # ---- 其他 ----
    "exit_trend_break": True,  # 趋势结构破坏(价格跌破 fast_ma 且 fast_ma<trend_ma)时立即清仓(第二道保护)
    "cooldown_days": 5,        # 同一股票两次买入的最小间隔(交易日)，避免短期重复追高
    "allow_scale_in": False,   # 是否允许盈利加仓(回调再加一次)。True=放大趋势收益但提高集中度
}


def generate_signal(ctx):
    """五档信号策略入口。返回 dict: signal_type / target_position / confidence / reason。"""
    close = ctx.close
    high = ctx.high
    low = ctx.low
    vol = ctx.volume
    n = len(close)
    P = PARAMS

    # ---------- 数据不足保护 ----------
    min_bars = P["trend_ma"] + 5
    if n < min_bars:
        return {"signal_type": "观望", "confidence": 0.0}

    ma_fast = MA(close, P["fast_ma"])
    ma_trend = MA(close, P["trend_ma"])
    ma_fast_v = float(ma_fast[-1])
    ma_trend_v = float(ma_trend[-1])
    price = float(close[-1])

    # 持仓状态（ctx 在每只股票上跨 bar 持久）
    w = ctx.stock_position_weight       # 该股票当前权重(占权益)
    expo = ctx.portfolio_exposure       # 组合总敞口(占权益)
    holding = w > 0.001

    # ============================ 已持仓：出场管理 ============================
    if holding:
        entry = getattr(ctx, "_vap_entry", None)
        if entry is None:
            entry = price
            ctx._vap_entry = entry
            ctx._vap_entry_date = ctx.trade_date
            ctx._vap_peak = price
        entry_d = getattr(ctx, "_vap_entry_date", ctx.trade_date)
        peak = max(getattr(ctx, "_vap_peak", entry), price)
        ctx._vap_peak = peak

        # 1) 时间止损：持有时长超限且仍浮亏
        held_days = (ctx.trade_date - entry_d).days
        if held_days >= P["time_stop_days"] and price < entry:
            return {"signal_type": "卖出", "confidence": 0.7, "reason": "时间止损"}

        # 2) 硬止损
        if price <= entry * (1 - P["stop_loss_pct"]):
            return {"signal_type": "卖出", "confidence": 0.9, "reason": "硬止损"}

        # 3) 移动止盈（激活后从峰值回落）
        if peak >= entry * (1 + P["trailing_activation_pct"]) and \
           price <= peak * (1 - P["trailing_stop_pct"]):
            return {"signal_type": "卖出", "confidence": 0.75, "reason": "移动止盈"}

        # 4) 固定止盈
        if price >= entry * (1 + P["take_profit_pct"]):
            return {"signal_type": "卖出", "confidence": 0.7, "reason": "固定止盈"}

        # 5) 趋势破位清仓（第二道保护）
        if P["exit_trend_break"] and price < ma_fast_v and ma_fast[-1] < ma_trend_v:
            return {"signal_type": "卖出", "confidence": 0.8, "reason": "趋势破位清仓"}

        # 可选：盈利加仓（仅在 fast_ma 上方、未超配时）
        if P["allow_scale_in"] and w < P["max_position_size"] * 0.9:
            if price > ma_fast_v and ma_fast_v > ma_trend_v:
                target = min(P["max_position_size"], P["max_portfolio_exposure"] - expo + w)
                if target > w + 0.001:
                    return {"signal_type": "增持", "target_position": round(target, 4),
                            "confidence": 0.5, "reason": "盈利加仓"}

        return {"signal_type": "观望", "confidence": 0.0}

    # ============================ 空仓：入场过滤 ============================
    # --- 趋势过滤 ---
    if price <= ma_trend_v:
        return {"signal_type": "观望", "confidence": 0.0}
    if ma_fast_v <= ma_trend_v:
        return {"signal_type": "观望", "confidence": 0.0}
    # 价格需显著高于 trend_ma 根前，确认上行趋势已确立
    ref = float(close[-P["trend_ma"]]) if n > P["trend_ma"] else float(close[0])
    if ref > 0 and (price - ref) / ref < P["min_up_pct"]:
        return {"signal_type": "观望", "confidence": 0.0}

    # --- 大盘择时闸门（ctx.benchmark）：只在市场多头时开仓 ---
    # ctx.benchmark 返回按当前 trade_date 对齐的只读窗口（末根=上一交易日，防前视），
    # 未配置 benchmark_code 时为 None，此时闸门降级放行。
    if P["benchmark_trend_filter"]:
        bench = ctx.benchmark
        bm = P["benchmark_ma"]
        if bench is not None and bench.bar_count >= bm + 1:
            b_close = bench.close
            b_ma = float(MA(b_close, bm)[-1])
            b_price = float(b_close[-1])
            b_ref = float(b_close[-bm]) if bench.bar_count > bm else float(b_close[0])
            # 大盘多头判定：价格在均线上方（趋势向上）且较 bm 根前上涨达标
            if b_price <= b_ma:
                return {"signal_type": "观望", "confidence": 0.0, "reason": "大盘空头(价在均线下)"}
            if b_ref > 0 and (b_price - b_ref) / b_ref < P["benchmark_min_up_pct"]:
                return {"signal_type": "观望", "confidence": 0.0, "reason": "大盘弱势(涨幅不足)"}
            # 个股相对大盘强势过滤（可选）：只挑领涨股，回避逆市下跌股
            if P["benchmark_rs_filter"] and n >= bm and bench.bar_count >= bm:
                stock_ret = (price - float(close[-bm])) / float(close[-bm]) if float(close[-bm]) > 0 else 0.0
                bench_ret = (b_price - b_ref) / b_ref if b_ref > 0 else 0.0
                if stock_ret < bench_ret:
                    return {"signal_type": "观望", "confidence": 0.0, "reason": "个股弱于大盘"}

    # --- 波动率过滤（控制回撤关键）---
    atr = ATR(close, high, low, P["atr_len"])
    atr_pct = float(atr[-1]) / price if price > 0 else 0.0
    if atr_pct > P["atr_pct_max"]:
        return {"signal_type": "观望", "confidence": 0.0}

    # --- RSI 不在极端 ---
    rsi = RSI(close, P["rsi_len"])
    rsi_v = float(rsi[-1])
    if rsi_v > P["rsi_max"] or rsi_v < P["rsi_min"]:
        return {"signal_type": "观望", "confidence": 0.0}

    # --- 入场：趋势内浅回调 + 反转确认 ---
    dev = (price - ma_fast_v) / ma_fast_v if ma_fast_v > 0 else 1.0
    if dev > P["pullback_pct"]:
        return {"signal_type": "观望", "confidence": 0.0}

    rev = (close[-1] > close[-2]) and (close[-1] >= ma_fast_v)
    if P["rev_confirm"] and not rev:
        return {"signal_type": "观望", "confidence": 0.0}

    # 量能过滤：近期量能未明显放大（避免接高位派发的放量；回调缩量为佳）
    if vol is not None and len(vol) >= 20:
        vol_ma5 = float(MA(vol, 5)[-1])
        vol_ma20 = float(MA(vol, 20)[-1])
        if vol_ma20 > 0 and vol_ma5 > vol_ma20 * P["vol_spike_max"]:
            return {"signal_type": "观望", "confidence": 0.0}

    # 冷却期：同一股票冷却内不重复买入
    last_buy = getattr(ctx, "_vap_last_buy", None)
    if last_buy is not None and (ctx.trade_date - last_buy).days < P["cooldown_days"]:
        return {"signal_type": "观望", "confidence": 0.0}

    # 组合敞口预算
    if expo >= P["max_portfolio_exposure"]:
        return {"signal_type": "观望", "confidence": 0.0}

    # --- 仓位管理：风险预算 + 单股/组合上限 ---
    risk_pos = P["risk_per_trade"] / P["stop_loss_pct"] if P["stop_loss_pct"] > 0 else P["max_position_size"]
    target = min(P["max_position_size"], risk_pos, P["max_portfolio_exposure"] - expo)
    if target <= 0.001:
        return {"signal_type": "观望", "confidence": 0.0}
    target = round(min(target, 1.0), 4)

    # 记录近似入场价（用于策略内自包含止损/止盈）与冷却时间
    ctx._vap_entry = price
    ctx._vap_entry_date = ctx.trade_date
    ctx._vap_peak = price
    ctx._vap_last_buy = ctx.trade_date

    conf = float(min(1.0, 0.5 + (P["pullback_pct"] - dev) / P["pullback_pct"] * 0.5))
    return {
        "signal_type": "买入",
        "target_position": target,
        "confidence": round(conf, 2),
        "reason": f"趋势回调买入 偏离{dev:.2%} ATR%{atr_pct:.2%} RSI{rsi_v:.0f}",
    }


# ===================== 推荐回测风险参数（双保险） =====================
# 在回测配置的「风险参数」中设置以下值，与本策略 PARAMS 中的止损/止盈保持一致：
#   stop_loss_pct           = 0.06
#   take_profit_pct         = 0.10
#   trailing_activation_pct = 0.06
#   trailing_stop_pct       = 0.06
#   time_stop_days          = 20
#   max_positions           = 4      # 与 max_portfolio_exposure/max_position_size 配合(4×20%=80%→被60%敞口上限夹到约3只)
#   max_daily_buys          = 2
#   slippage_pct            = 0.001  (默认)
#
# 若忘记设置，本策略仍靠内部自包含止损/止盈退出，但建议两者保持一致以避免重复触发。
#
# ⚠️ 启用大盘择时闸门：回测配置的「基准代码(benchmark_code)」务必设为 000300.SH
#    （沪深300）；否则 ctx.benchmark 为 None，闸门自动降级为"不约束"。
#    设置路径：回测配置 → 基准代码。也可换 399006.SZ(创业板指) 等。
