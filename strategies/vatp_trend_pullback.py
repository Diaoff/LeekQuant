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
     ⚠️ 硬止损已升级为「ATR 挂钩」：止损距离 = 入场 ATR × 倍数，波动越大止损
     越远，作为「自包含兜底」（当回测把全局出场关 0 时生效）。推荐保留引擎全局
     出场主导（详见文末说明）——实测其 R:R 优于策略内部出场。

为什么自包含实现止损/止盈
-------------------------
ctx 通过 `ctx.entry_price` / `ctx.entry_date` 暴露引擎权威入场价（真实成交价），
并通过 `ctx.state`（按股票隔离、跨 bar 持久）供策略记录峰值/入场 ATR/冷却日等。
引擎全局风控（回测风险参数）虽可用，但要求用户在界面上正确配置；为避免"忘设
风险参数就裸奔"，本策略在 generate_signal 内部实现自包含软止损/止盈，保证策略
"复制即用、自带风控"。注意：入场价必须用引擎权威值，不可在 ctx 上自存——因为
BacktestContext 每天重建，自定义属性下一天即丢失。

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
    "benchmark_rs_filter": False,      # 是否额外要求"个股强于大盘"（个股近期涨幅 >= 大盘）。⚠️默认关：实测(#200)开启后会排除能大涨的领涨股、反而拉低总收益，故默认关闭以保收益；开启=更稳健/更低收益
    "bench_rsi_max": 0,                # 大盘 RSI 上限；超过视为市场超买顶部，不追（回避 euphoria 后的回撤）。0=不限（默认关，理由同上）

    # ---- P0优化：入场过滤三重加强（目标：止损占比从60%降至45%以下）----
    # 1. 大级别趋势斜率确认：长期均线(MA40)本身必须上扬，而非仅价格在均线上方。
    #    原策略只检查 price>MA40 且 MA20>MA40，但MA40可能走平甚至微降（震荡市），
    #    此时"趋势"是假的，入场后极易被打止损。要求MA40[-1] > MA40[-N] 确认真正的上行斜率。
    "trend_ma_slope_enabled": True,    # 是否启用大级别均线斜率确认
    "trend_ma_slope_lookback": 5,      # 斜率回看根数(约1周)。MA40[-1] > MA40[-N] 才放行

    # 2. RSI超卖回调确认：只在RSI处于回调低位且拐头向上时买入。
    #    原策略 RSI 区间 40~70 太宽——RSI=65 是追高、RSI=42 可能还在下跌中继。
    #    收紧到超卖区间(28~52)并要求拐头，确保"回调到位+开始反弹"，避免接飞刀。
    "rsi_oversold_enabled": True,      # 是否启用RSI超卖回调确认（启用后覆盖原rsi_min/rsi_max）
    "rsi_oversold_max": 52,            # RSI上限：高于此不算回调(追高区)，不买
    "rsi_oversold_min": 28,            # RSI下限：低于此可能趋势破坏，不接
    "rsi_turn_up_required": True,      # 要求RSI[-1] > RSI[-2]（拐头向上确认反弹启动）

    # 3. ATR收敛过滤：波动率处于收敛/下降状态时入场。
    #    原策略只检查 ATR% < 6%（绝对值），但高波动后刚降到6%以下时，波动率仍在
    #    高位震荡，止损易被扫。要求当前ATR <= 近N日ATR均值（波动率不扩张），
    #    收敛状态下的突破/反弹更可靠，止损被噪声触发的概率更低。
    "atr_convergence_enabled": True,   # 是否启用ATR收敛过滤
    "atr_conv_period": 10,             # ATR均值回看周期(约2周)
    "atr_conv_ratio_max": 1.0,         # 当前ATR / 近N日ATR均值 <= 此值视为收敛。1.0=不高于均值；0.9=更严格

    # ---- 入场：趋势内浅回调 ----
    "pullback_pct": 0.03,      # 价格相对 fast_ma 的最大正向偏离(回调深度阈值)，超过则不视为回调。↓=只在更浅的回调买
    "rev_confirm": True,       # 是否需要反转确认(今收>昨收 且 收回到 fast_ma 上方)。False=仅价格触线即买(信号多、质量降)
    "ma_slope_min": -9.0,       # MA20 斜率下限(每根 frac)；低于此(拐头/走平)不买。默认-9=关闭（#200 实测开启后降收益）。↑=要求更强上扬(保守模式)
    "support_tag_pct": 9.0,     # 支撑确认：最近3根最低价需回踩到 fast_ma 的该幅度内。默认9=关闭（#200 实测开启后排除大涨标的、降收益）。↓=更严格(保守模式)
    "vol_spike_max": 1.2,      # 近5日均量 / 近20日均量 的上限；超过说明近期放量(疑似派发)，不买。=9 表示不限量能
    "rsi_len": 14,             # RSI 周期
    "rsi_max": 70,             # RSI 上限，超过视为超买顶部，不买
    "rsi_min": 40,             # RSI 下限，低于此视为弱势，不买(避免在下行反弹中接刀)

    # ---- 波动率过滤(控制回撤的关键) ----
    "atr_len": 20,             # ATR 周期
    "atr_pct_max": 0.06,       # ATR/收盘价的上限；超过说明波动过大、止损易被扫，跳过。↓更保守(回撤更低)、↑信号更多。回退到 #198 水平(0.05 实测略降收益)

    # ---- 出场(策略内自包含) ----
    # 硬止损升级为「ATR 挂钩」：止损距离 = 入场 ATR × stop_loss_atr_multiple，
    # 波动越大止损越远，避免被正常噪声扫损（压低止损触发率、提升胜率的核心）。
    # stop_loss_pct 仅作 fallback：当未启用 ATR 挂钩(倍数=0)时使用固定比例。
    "stop_loss_pct": 0.06,     # 固定硬止损比例(fallback,仅倍数=0时生效)。⚠️回测全局风险参数 stop_loss_pct 应设 0 或≥动态上限，交本策略内部主导，否则引擎全局固定止损会抢先
    "stop_loss_atr_multiple": 3.0,  # 硬止损 = 入场ATR × 该倍数(波动率自适应)。0=禁用、退回固定 stop_loss_pct。↑=止损更宽、被扫更少但单笔亏更多；↓=更紧
    "take_profit_pct": 0.10,   # 固定止盈比例(1.67R)。↑=让利润多跑、胜率略降；↓=落袋更快、胜率略升
    "trailing_activation_pct": 0.06,  # 移动止盈激活阈值(浮盈达到该比例后才启动跟踪)
    "trailing_stop_pct": 0.06, # 移动止盈回撤比例(从峰值回落该比例则离场)。负责"让盈利多跑、截断回落"
    "time_stop_days": 20,      # 持仓超过该交易日数且仍浮亏，强制时间止损(避免资金长期被套)

    # ---- 仓位管理(风险预算) ----
    "risk_per_trade": 0.02,    # 单笔最大可承受权益亏损(分数)。默认2%
    "max_position_size": 0.20, # 单股最大仓位(占权益)。默认20%(同#198)；与 stop_loss_pct 配合→单笔最坏亏 ~1.2% 权益
    "max_portfolio_exposure": 0.60,  # 组合总敞口上限。默认60%(同#198)。实证:提高至0.65反而降收益(创业板高相关,多持放大区间亏损),故回退

    # ---- 其他 ----
    "exit_trend_break": True,  # 趋势结构破坏(价格跌破 fast_ma 且 fast_ma<trend_ma)时立即清仓(第二道保护)
    "cooldown_days": 5,        # 同一股票两次买入的最小间隔(交易日)，避免短期重复追高
    "allow_scale_in": False,   # 是否允许盈利加仓(回调再加一次)。True=放大趋势收益但提高集中度

    # ---- 同天多候选择优（引擎按 confidence 降序买入，调 confidence 即调优先级）----
    # 实证（#206 平仓因子分析，n=108）：回调缩量(低量比)候选 +1.07% vs 放量(高量比) -0.95%；
    # 低动量(刚回调到位) +0.64% vs 高动量 -0.51%。"热门/放量/追高"候选反而更差——
    # 与 VATP"低吸趋势内浅回调"逻辑一致。故对高量比/高动量候选施加 confidence 惩罚，
    # 让同一天的买入名额优先分配给"缩量冷门回调"。引擎 max_daily_buys 限制下每天
    # 候选几十只只买 2 只，排序即选股，惩罚系数是收益的直接影响项。
    "selection_penalty_enabled": False,  # 是否启用择优惩罚。False=恢复纯 confidence 排序(等同 #206, 默认)。#208 实测开启后 +5.10% < #206 +5.79%, 故默认关闭
    "vol_ratio_penalty": 0.30,   # 量比惩罚强度：量比每超过 1.0 一个单位，confidence 乘 (1-k)。缩量(量比<1)无惩罚
    "momentum_penalty": 0.40,    # 动量惩罚强度：20日涨幅每 10% 一档，confidence 乘 (1-k)。动量≤0 无惩罚

    # ---- 绩优择优（ctx.fundamentals：最近一期已公告财报，防前视由引擎保证）----
    # 读 roe(净资产收益率%) 与 net_profit_growth(净利润同比%)，达标则 confidence 加分，
    # 让同天 max_daily_buys 买入名额优先分配给"绩优股"。加分而非硬过滤（VATP 多道过滤后
    # 候选已少，硬过滤过度淘汰）。需先跑 scripts/backfill_fundamentals.py 补财务数据，
    # 数据缺失时 ctx.fundamentals=None → 不加分不惩罚，自动降级为 #206 行为。
    "fundamental_bonus_enabled": False,  # 是否启用绩优加分。默认关=等同 #206 基线；需先补财务数据并 A/B 验证真实效果
    "fundamental_roe_min": 10.0,         # ROE 门槛(%)：净资产收益率(平均)≥ 该值才算绩优
    "fundamental_growth_min": 0.0,       # 净利润同比门槛(%)：YOYPNI ≥ 该值才算绩优
    "fundamental_bonus": 0.05,           # 达标时 confidence 加分幅度
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
    st = ctx.state                      # 跨 bar 持久的策略私有状态(引擎保证按股票隔离)

    # ============================ 已持仓：出场管理 ============================
    if holding:
        # 入场价/入场日用引擎权威值(真实成交价)，不再用 ctx 自定义属性近似——
        # BacktestContext 每天重建，set 在 ctx 上的属性下一天会丢失，导致出场失效。
        entry = ctx.entry_price
        if entry is None or entry <= 0:
            entry = price
        entry_d = ctx.entry_date or ctx.trade_date
        peak = max(st.get("peak", entry), price)
        st["peak"] = peak

        # 1) 时间止损：持有时长超限且仍浮亏
        held_days = (ctx.trade_date - entry_d).days
        if held_days >= P["time_stop_days"] and price < entry:
            return {"signal_type": "卖出", "confidence": 0.7, "reason": "时间止损"}

        # 2) 硬止损（ATR 挂钩：波动越大止损越远，避免被正常噪声扫损）
        entry_atr = st.get("entry_atr", 0.0) or 0.0
        if P["stop_loss_atr_multiple"] > 0 and entry_atr > 0:
            stop_line = entry - entry_atr * P["stop_loss_atr_multiple"]
            reason_sl = f"硬止损(ATR{P['stop_loss_atr_multiple']:.1f}×)"
        else:
            stop_line = entry * (1 - P["stop_loss_pct"])
            reason_sl = "硬止损(固定)"
        if price <= stop_line:
            return {"signal_type": "卖出", "confidence": 0.9, "reason": reason_sl}

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

    # --- P0优化：大级别趋势斜率确认（MA40本身必须上扬）---
    # 仅 price>MA40 不够：震荡市中MA40可能走平/微降，此时"趋势"是假的。
    # 要求 MA40[-1] > MA40[-lookback]，确认真正的上行斜率，过滤横盘假突破。
    if P["trend_ma_slope_enabled"]:
        lb = P["trend_ma_slope_lookback"]
        if len(ma_trend) > lb and float(ma_trend[-1-lb]) > 0:
            if float(ma_trend[-1]) <= float(ma_trend[-1-lb]):
                return {"signal_type": "观望", "confidence": 0.0, "reason": "大级别均线未上扬"}

    # --- 趋势斜率：MA20 必须向上（避免横盘/拐头时入场）---
    if len(ma_fast) >= 2 and float(ma_fast[-2]) > 0:
        if (float(ma_fast[-1]) / float(ma_fast[-2]) - 1.0) < P["ma_slope_min"]:
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
            # 大盘超买过滤：大盘 RSI 过高(接近顶部)时不追，回避市场 euphoria 后的回撤
            if P["bench_rsi_max"] > 0 and bench.bar_count >= P["rsi_len"] + 1:
                b_rsi = float(RSI(b_close, P["rsi_len"])[-1])
                if b_rsi > P["bench_rsi_max"]:
                    return {"signal_type": "观望", "confidence": 0.0, "reason": "大盘超买"}
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

    # --- P0优化：ATR收敛过滤（波动率处于收敛/不扩张状态）---
    # 原逻辑只检查ATR%绝对值<6%，但高波动后刚降到6%以下时，波动率仍在高位震荡，
    # 止损易被噪声扫。要求当前ATR <= 近N日ATR均值（波动率不扩张），
    # 收敛状态下的反弹更可靠，从源头降低止损触发率。
    if P["atr_convergence_enabled"] and len(atr) > P["atr_conv_period"]:
        atr_recent = [float(a) for a in atr[-P["atr_conv_period"]:]]
        atr_avg = sum(atr_recent) / len(atr_recent)
        if atr_avg > 0 and (float(atr[-1]) / atr_avg) > P["atr_conv_ratio_max"]:
            return {"signal_type": "观望", "confidence": 0.0, "reason": "ATR未收敛"}

    # --- P0优化：RSI超卖回调确认（替换原简单区间过滤）---
    # 原逻辑 RSI∈[40,70] 太宽：65是追高、42可能还在下跌中继。
    # 新逻辑：RSI必须落在超卖回调区间[28,52]，且拐头向上(RSI[-1]>RSI[-2])，
    # 确保"回调到位+反弹启动"，从源头减少止损触发。
    rsi = RSI(close, P["rsi_len"])
    rsi_v = float(rsi[-1])
    if P["rsi_oversold_enabled"]:
        if rsi_v > P["rsi_oversold_max"] or rsi_v < P["rsi_oversold_min"]:
            return {"signal_type": "观望", "confidence": 0.0}
        if P["rsi_turn_up_required"] and len(rsi) >= 2:
            if float(rsi[-1]) <= float(rsi[-2]):
                return {"signal_type": "观望", "confidence": 0.0, "reason": "RSI未拐头"}
    else:
        # 兼容原逻辑（rsi_oversold_enabled=False 时退回原区间过滤）
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

    # --- 支撑确认：最近 3 根最低价需回踩到 fast_ma 附近（确实测试过支撑），
    #     避免在"尚未回踩"的半山腰接刀。贴合支撑入场能让固定 6% 止损更安全。---
    if low is not None and len(low) >= 3:
        recent_low = min(float(low[-1]), float(low[-2]), float(low[-3]))
        if recent_low > ma_fast_v * (1 + P["support_tag_pct"]):
            return {"signal_type": "观望", "confidence": 0.0}

    # 冷却期：同一股票冷却内不重复买入
    last_buy = st.get("last_buy")
    if last_buy is not None and (ctx.trade_date - last_buy).days < P["cooldown_days"]:
        return {"signal_type": "观望", "confidence": 0.0}

    # 组合敞口预算
    if expo >= P["max_portfolio_exposure"]:
        return {"signal_type": "观望", "confidence": 0.0}

    # --- 仓位管理：风险预算 + 单股/组合上限 ---
    # 注：此前试过"ATR 挂钩止损距离"算仓位(波动越大仓位越小)，实测把仓位算小、拉低整体收益，
    # 故回退到 #198 经验式：固定风险预算 / 固定止损比例 → 单股封顶 max_position_size。
    risk_pos = P["risk_per_trade"] / P["stop_loss_pct"] if P["stop_loss_pct"] > 0 else P["max_position_size"]
    target = min(P["max_position_size"], risk_pos, P["max_portfolio_exposure"] - expo)
    if target <= 0.001:
        return {"signal_type": "观望", "confidence": 0.0}
    target = round(min(target, 1.0), 4)

    # 记录策略私有状态（跨 bar 持久，引擎保证）——入场 ATR(波动率自适应止损基准)、
    # 移动止盈峰值初始化、冷却时间。入场价/入场日直接用引擎权威 ctx.entry_price/entry_date。
    st["peak"] = price
    st["entry_atr"] = float(atr[-1]) if atr is not None else 0.0
    st["last_buy"] = ctx.trade_date

    conf = float(min(1.0, 0.5 + (P["pullback_pct"] - dev) / P["pullback_pct"] * 0.5))

    # ---- 同天多候选择优：高量比/高动量候选施加 confidence 惩罚（排序靠后）----
    # 量比 = 近5日均量/近20日均量（缩量=抛压衰竭；放量=疑似派发，惩罚）
    # 动量 = 20日涨幅（刚回调到位=低动量买点更佳；追高=惩罚）
    vol_ratio_now = 1.0
    if vol is not None and len(vol) >= 20:
        _v5 = float(MA(vol, 5)[-1])
        _v20 = float(MA(vol, 20)[-1])
        if _v20 > 0:
            vol_ratio_now = _v5 / _v20
    mom20 = 0.0
    if n > 21 and float(close[-21]) > 0:
        mom20 = price / float(close[-21]) - 1.0
    if P["selection_penalty_enabled"]:
        conf *= (1.0 - P["vol_ratio_penalty"] * max(0.0, vol_ratio_now - 1.0))
        conf *= (1.0 - P["momentum_penalty"] * max(0.0, mom20))
        conf = float(min(1.0, conf))

    # ---- 绩优择优：最近一期已公告财报达标则 confidence 加分（排序优先）----
    # ctx.fundamentals 由引擎按 announce_date <= 决策日 防前视截取；数据缺失(未补数/次新股)
    # 时为 None → 不加分不惩罚，等同基线。roe/net_profit_growth 单位均为 %。
    if P["fundamental_bonus_enabled"]:
        _f = ctx.fundamentals
        if (_f is not None and _f.roe is not None and _f.net_profit_growth is not None
                and _f.roe >= P["fundamental_roe_min"]
                and _f.net_profit_growth >= P["fundamental_growth_min"]):
            conf += P["fundamental_bonus"]
            conf = float(min(1.0, conf))

    return {
        "signal_type": "买入",
        "target_position": target,
        "confidence": round(conf, 2),
        "reason": f"趋势回调买入 偏离{dev:.2%} ATR%{atr_pct:.2%} RSI{rsi_v:.0f} 量比{vol_ratio_now:.2f} 动量{mom20:.1%}",
    }


# ===================== 推荐回测风险参数（保留引擎全局出场） =====================
# ⚠️ 重要经验（来自 #198 vs #199 对比）：引擎全局出场 proven 优于策略内部出场。
#   #198（全局 6%/10%/拖6%/时间20日，ON）=> +10.09% / 夏普 0.676；
#   #199（全局全关 0，交策略内部 ATR 出场）=> -15.16% / 夏普 -1.16。
#   原因：内部出场 R:R 劣于简单固定规则（ATR 3×≈15% 宽止损让亏票跑太远、trailing 偏紧
#   截断赢家）。故**推荐保留引擎全局出场作为主导**，策略专注"入场质量"（它的真正优势）。
#   策略内部的硬止损/止盈/移动止盈仅作「自包含兜底」（当回测把全局出场关 0 时才生效）。
#
# 推荐配置（与 #198 一致，已验证稳健）：
#   stop_loss_pct           = 0.06
#   take_profit_pct         = 0.10
#   trailing_activation_pct = 0.06
#   trailing_stop_pct       = 0.06
#   time_stop_days          = 20
#   max_positions           = 4      # 与 max_portfolio_exposure/max_position_size 配合(4×20%=80%→被60%敞口上限夹到约3只)
#   max_daily_buys          = 2
#   slippage_pct            = 0.001  (默认)
#
# ⚠️ 若把全局出场关 0（交策略内部主导），请知悉其 R:R 当前弱于上述全局规则，收益会劣化。
#
# ⚠️ 启用大盘择时闸门与领涨过滤：回测配置的「基准代码(benchmark_code)」务必设为 000300.SH
#    （沪深300，低波动弱相关，择时分离度高）；否则 ctx.benchmark 为 None，闸门自动降级。
#    设置路径：回测配置 → 基准代码。
