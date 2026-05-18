"""Final test: use data guaranteed to produce golden cross."""
from decimal import Decimal
from datetime import date
from app.backtest.adapter import BacktestConfig, BacktestRunner, KBar

# 构造明确的数据：前20天下跌，后10天上涨（必然金叉）
source_code = '''
def generate_signal(ctx):
    close = ctx.close
    ma5 = MA(close, 5)
    ma20 = MA(close, 20)
    
    if len(close) < 21:
        return {"signal_type": "观望"}
    
    # 金叉买入
    if ma5[-1] > ma20[-1] and ma5[-2] <= ma20[-2]:
        return {"signal_type": "买入", "target_position": 1.0}
    # 死叉卖出
    elif ma5[-1] < ma20[-1] and ma5[-2] >= ma20[-2]:
        return {"signal_type": "卖出"}
    # 持有
    elif ma5[-1] > ma20[-1]:
        return {"signal_type": "观望"}
    else:
        return {"signal_type": "观望"}
'''

all_klines = {}
klines = []

# 前20天：价格从12下降到8（MA5 < MA20）
for i in range(20):
    d = date(2026, 4, 1 + i)
    price = 12.0 - i * 0.2  # 12 -> 8
    klines.append(KBar(
        ts_code='000001.SZ',
        trade_date=d,
        open=Decimal(str(round(price * 0.99, 4))),
        high=Decimal(str(round(price * 1.01, 4))),
        low=Decimal(str(round(price * 0.98, 4))),
        close=Decimal(str(round(price, 4))),
        pre_close=Decimal(str(round(klines[-1].close, 4))) if klines else Decimal('11.88'),
        volume=1000000,
        amount=Decimal(str(round(price * 1000000, 2))),
        adj_factor=Decimal('1.0'),
        is_suspended=False,
        is_limit_up=False,
        is_limit_down=False,
    ))

# 后10天：价格从8.2上升到11（MA5会穿过MA20形成金叉）
for i in range(10):
    d = date(2026, 4, 21 + i)
    price = 8.2 + i * 0.3  # 8.2 -> 10.9 (超过MA20约9.5)
    prev_close = klines[-1].close
    klines.append(KBar(
        ts_code='000001.SZ',
        trade_date=d,
        open=Decimal(str(round(price * 0.995, 4))),
        high=Decimal(str(round(price * 1.02, 4))),
        low=Decimal(str(round(price * 0.98, 4))),
        close=Decimal(str(round(price, 4))),
        pre_close=prev_close,
        volume=1000000,
        amount=Decimal(str(round(price * 1000000, 2))),
        adj_factor=Decimal('1.0'),
        is_suspended=False,
        is_limit_up=False,
        is_limit_down=False,
    ))

all_klines['000001.SZ'] = klines

config = BacktestConfig(
    strategy_id=1,
    source_code=source_code,
    stock_pool=['000001.SZ'],
    start_date=date(2026, 4, 1),
    end_date=date(2026, 4, 30),
    initial_cash=Decimal('100000'),
)

print("=== 数据概览 ===")
print(f"前5天收盘价: {[float(k.close) for k in klines[:5]]}")
print(f"第20天(金叉前): {float(klines[19].close):.2f}")
print(f"第21-25天: {[float(k.close) for k in klines[20:25]]}")

runner = BacktestRunner(config)
results = runner.run(all_klines)

print('\n=== 回测结果 ===')
print(f'Total Return: {results["total_return"]:.4%}')
print(f'Annual Return: {results["annual_return"]:.4%}')
print(f'Trade Count: {results["trade_count"]}')
print(f'Max Drawdown: {results["max_drawdown"]:.4%}')
print(f'Sharpe Ratio: {results["sharpe_ratio"]:.4f}')

print(f'\n=== 引擎记录的信号 (非HOLD) ===')
non_hold = [s for s in runner.signals if s.get('action') != 'HOLD']
print(f'总信号数: {len(runner.signals)} | 非HOLD信号数: {len(non_hold)}')
for s in non_hold[:15]:
    print(f'  {s["trade_date"]} | {s["signal_type"]:4} → {s["action"]:12} | target={s.get("target_position")}')

print('\n=== 交易记录 ===')
if results['trade_records']:
    for t in results['trade_records']:
        print(f'{t["trade_date"]} | {t["direction"]:4} | {t["ts_code"]} | {t["volume"]}股 @ {float(t["price"]):.2f} | 费用:{float(t["cost"]["total_fee"]):.2f}')
else:
    print('无交易记录')

print(f'\n=== 权益曲线 ===')
ec = results['equity_curve']
print(f'总点数: {len(ec)}')
if len(ec) > 1:
    print(f'初始: {ec[0]["date"]} ¥{ec[0]["total_asset"]:,.2f}')
    print(f'最终: {ec[-1]["date"]} ¥{ec[-1]["total_asset"]:,.2f}')
    change = (ec[-1]['total_asset'] - ec[0]['total_asset']) / ec[0]['total_asset'] * 100
    print(f'变化: {change:+.2f}%')
