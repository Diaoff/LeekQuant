# Leek Quant 策略编写指南

## 策略结构

策略是一个 Python 函数 `generate_signal(ctx)`，接收一个 `ctx` 上下文对象，返回一个信号字典。

### 最小示例

```python
def generate_signal(ctx):
    close = ctx.close("000001.SZ")
    ma5 = MA(close, 5)
    ma20 = MA(close, 20)

    if CROSS(ma5, ma20)[-1]:
        return {"signal": "买入", "reason": "MA5金叉MA20"}
    if CROSS(ma20, ma5)[-1]:
        return {"signal": "卖出", "reason": "MA5死叉MA20"}

    return {"signal": "观望", "reason": "无信号"}
```

### 返回值规范

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `signal` | str | 是 | 五档信号之一：`买入` `增持` `减仓` `卖出` `观望` |
| `reason` | str | 推荐 | 信号原因描述，会记录到回测和信号日志中 |
| `target_position` | float | 否 | 目标仓位 0.0~1.0，不填则使用信号状态机默认值 |
| `confidence` | float | 否 | 信号置信度 0.0~1.0，用于信号排序与展示；不填则按默认处理 |

## ctx 上下文 API

### 行情数据获取

```python
ctx.close("000001.SZ")       # 收盘价序列 (numpy array)
ctx.high("000001.SZ")        # 最高价序列
ctx.low("000001.SZ")         # 最低价序列
ctx.open("000001.SZ")        # 开盘价序列
ctx.volume("000001.SZ")      # 成交量序列
ctx.amount("000001.SZ")      # 成交额序列
ctx.turn("000001.SZ")        # 换手率序列
ctx.pct_chg("000001.SZ")     # 涨跌幅序列
```

所有数据返回 `numpy.ndarray`，长度与回测周期一致，最旧的数据在索引 0，最新的在索引 -1。

### 策略配置

```python
ctx.ts_codes      # 当前股票池列表
ctx.initial_cash  # 初始资金
ctx.start_date    # 回测开始日期
ctx.end_date      # 回测结束日期
```

### 多股票轮动

策略会对股票池中的每只股票独立调用 `generate_signal`。如果需要多股票联合判断，可以使用 `ctx.close_all()`：

```python
def generate_signal(ctx):
    # 获取所有股票的收盘价
    closes = {code: ctx.close(code) for code in ctx.ts_codes}
    # 最近的收盘价
    latest = {code: c[-1] for code, c in closes.items()}
    # 选最便宜的
    target = min(latest, key=latest.get)

    code = ctx.ts_codes[0]  # 当前迭代的股票
    if code == target:
        return {"signal": "买入", "reason": f"最低价 {latest[code]:.2f}"}
    return {"signal": "卖出", "reason": f"调仓至 {target}"}
```

## 内置指标库 MyTT

策略运行环境中自动注入 [MyTT](https://github.com/mpquant/MyTT) 全部函数，共 28+ 个技术指标，语法与通达信/同花顺兼容。

### 常用指标

| 函数 | 说明 | 参数 |
|------|------|------|
| `MA(S, N)` | N日移动平均 | `S` 为序列 |
| `EMA(S, N)` | 指数移动平均 | |
| `MACD(CLOSE)` | MACD指标 | 返回 (DIF, DEA, MACD) |
| `KDJ(CLOSE, HIGH, LOW)` | KDJ指标 | 返回 (K, D, J) |
| `RSI(CLOSE, N=24)` | RSI指标 | |
| `BOLL(CLOSE)` | 布林带 | 返回 (UPPER, MID, LOWER) |
| `CROSS(S1, S2)` | 金叉穿越 | 返回 bool 序列 |
| `HHV(S, N)` | N周期最高值 | |
| `LLV(S, N)` | N周期最低值 | |
| `REF(S, N)` | 引用N周期前值 | |

### 完整列表

**核心函数**：`RD` `RET` `ABS` `LN` `POW` `SQRT` `SIN` `COS` `TAN` `MAX` `MIN` `IF` `REF` `DIFF` `STD` `SUM` `CONST` `HHV` `LLV` `HHVBARS` `LLVBARS` `MA` `EMA` `SMA` `WMA` `DMA` `AVEDEV` `SLOPE` `FORCAST` `LAST`

**应用层函数**：`COUNT` `EVERY` `EXIST` `FILTER` `BARSLAST` `BARSLASTCOUNT` `BARSSINCEN` `CROSS` `LONGCROSS` `VALUEWHEN` `BETWEEN` `TOPRANGE` `LOWRANGE`

**技术指标**：`MACD` `KDJ` `RSI` `WR` `BIAS` `BOLL` `PSY` `CCI` `ATR` `BBI` `DMI` `TAQ` `KTN` `TRIX` `VR` `CR` `EMV` `DPO` `BRAR` `DFMA` `MTM` `MASS` `ROC` `EXPMA` `OBV` `MFI` `ASI` `XSII`

## 策略示例

### 双均线策略

```python
def generate_signal(ctx):
    code = ctx.ts_codes[0]
    close = ctx.close(code)
    ma10 = MA(close, 10)
    ma30 = MA(close, 30)

    # 趋势强度：短期均线与长期均线的相对距离，作为置信度
    dist = (ma10[-1] - ma30[-1]) / ma30[-1]
    conf = float(max(0.0, min(1.0, abs(dist) * 20)))

    if CROSS(ma10, ma30)[-1]:
        return {"signal": "买入", "reason": "10日均线上穿30日均线", "confidence": round(conf, 2)}
    if CROSS(ma30, ma10)[-1]:
        return {"signal": "卖出", "reason": "10日均线下穿30日均线", "confidence": round(conf, 2)}

    if close[-1] > ma10[-1]:
        return {"signal": "增持", "reason": "价格在均线上方", "confidence": round(conf, 2)}
    if close[-1] < ma10[-1]:
        return {"signal": "减仓", "reason": "价格在均线下方", "confidence": round(conf, 2)}

    return {"signal": "观望", "reason": "无信号", "confidence": 0.0}
```

### MACD 金叉策略

```python
def generate_signal(ctx):
    code = ctx.ts_codes[0]
    close = ctx.close(code)
    dif, dea, macd = MACD(close)

    # 柱强绝对值越大，置信度越高
    conf = float(max(0.0, min(1.0, abs(macd[-1]) * 5)))

    if CROSS(dif, dea)[-1] and macd[-1] > 0:
        return {"signal": "买入", "reason": "MACD零轴上金叉", "confidence": round(conf, 2)}
    if CROSS(dea, dif)[-1] and macd[-1] < 0:
        return {"signal": "卖出", "reason": "MACD零轴下死叉", "confidence": round(conf, 2)}

    return {"signal": "观望", "reason": "无信号", "confidence": 0.0}
```

### RSI 超买超卖

```python
def generate_signal(ctx):
    code = ctx.ts_codes[0]
    close = ctx.close(code)
    rsi = RSI(close, 14)

    if rsi[-1] < 30:
        return {"signal": "买入", "reason": f"RSI超卖 {rsi[-1]:.1f}"}
    if rsi[-1] > 70:
        return {"signal": "卖出", "reason": f"RSI超买 {rsi[-1]:.1f}"}

    if rsi[-1] < 50:
        return {"signal": "增持", "reason": f"RSI偏多 {rsi[-1]:.1f}"}
    return {"signal": "减仓", "reason": f"RSI偏空 {rsi[-1]:.1f}"}
```

### 布林带策略

```python
def generate_signal(ctx):
    code = ctx.ts_codes[0]
    close = ctx.close(code)
    upper, mid, lower = BOLL(close, 20, 2)

    if close[-1] <= lower[-1]:
        return {"signal": "买入", "reason": "触及布林下轨"}
    if close[-1] >= upper[-1]:
        return {"signal": "卖出", "reason": "触及布林上轨"}

    if close[-1] > mid[-1]:
        return {"signal": "增持", "reason": "价格在中轨上方"}
    return {"signal": "减仓", "reason": "价格在中轨下方"}
```

## 注意事项

1. **数据长度**：前 N 个周期会因窗口计算产生 `NaN`，确保策略回测段包含足够长的预热期
2. **`[-1]` 索引**：始终使用 `[-1]` 获取最新值，序列末尾是最新数据
3. **信号互斥**：每只股票每次只返回一个信号，多个条件成立时取优先级最高的
4. **性能**：策略执行有超时限制（默认 2s），避免复杂循环或大计算量操作
5. **沙箱安全**：策略运行在隔离的 Python 沙箱中，无法访问文件系统、网络或系统命令
