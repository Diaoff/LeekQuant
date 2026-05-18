"""Quick test: verify strategy execution produces trades."""
from decimal import Decimal
from datetime import date
import random
from app.backtest.adapter import BacktestConfig, BacktestRunner, KBar

source_code = '''
def generate_signal(ctx):
    close = ctx.close
    ma5 = MA(close, 5)
    ma20 = MA(close, 20)
    
    if len(close) < 21:
        return {"signal_type": "观望"}
    
    if ma5[-1] > ma20[-1] and ma5[-2] <= ma20[-2]:
        if ctx.current_position < 0.5:
            return {"signal_type": "买入", "target_position": 0.8}
        return {"signal_type": "增持", "target_position": 1.0}
    elif ma5[-1] < ma20[-1] and ma5[-2] >= ma20[-2]:
        return {"signal_type": "卖出"}
    elif ma5[-1] > ma20[-1]:
        return {"signal_type": "观望"}
    else:
        return {"signal_type": "减仓", "target_position": 0.3}
'''

random.seed(42)
all_klines = {}
base_price = 10.0
klines = []
for i in range(30):
    d = date(2026, 4, 1 + i)
    change = random.uniform(-0.03, 0.03)
    base_price *= (1 + change)
    o = base_price * random.uniform(0.995, 1.005)
    h = max(o, base_price) * random.uniform(1.0, 1.02)
    l = min(o, base_price) * random.uniform(0.98, 1.0)
    c = base_price
    klines.append(KBar(
        ts_code='000001.SZ',
        trade_date=d,
        open=Decimal(str(round(o, 4))),
        high=Decimal(str(round(h, 4))),
        low=Decimal(str(round(l, 4))),
        close=Decimal(str(round(c, 4))),
        pre_close=Decimal(str(round(klines[-1].close if klines else c, 4))) if klines else Decimal('0'),
        volume=random.randint(1000000, 10000000),
        amount=Decimal(str(round(c * random.randint(1000000, 10000000), 2))),
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

runner = BacktestRunner(config)
results = runner.run(all_klines)

print('=== 回测结果 ===')
print(f'Total Return: {results["total_return"]:.4%}')
print(f'Trade Count: {results["trade_count"]}')
print(f'Max Drawdown: {results["max_drawdown"]:.4%}')
print(f'Sharpe Ratio: {results["sharpe_ratio"]:.4f}')
print(f'Win Rate: {results["win_rate"]:.4%}')

print('\n=== 交易记录 ===')
for t in results['trade_records'][:10]:
    print(f'{t["trade_date"]} | {t["direction"]:4} | {t["ts_code"]} | {t["volume"]:6}股 @ {float(t["price"]):8.2f} | 费用:{float(t["cost"]["total_fee"]):8.2f}')

print(f'\n=== 权益曲线 ===')
ec = results['equity_curve']
print(f'Total points: {len(ec)}')
if ec:
    print(f'First: {ec[0]["date"]} ¥{ec[0]["total_asset"]:,.2f}')
    print(f'Last:  {ec[-1]["date"]} ¥{ec[-1]["total_asset"]:,.2f}')
