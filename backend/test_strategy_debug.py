"""Debug test: trace strategy execution to find why zero trades."""
from decimal import Decimal
from datetime import date
import random
from app.backtest.adapter import BacktestConfig, BacktestRunner, KBar, BacktestContext
from app.libs import MyTT

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
    change = random.uniform(-0.05, 0.05)  # 增加波动以触发交叉
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

# 手动模拟回测引擎的逻辑，带调试输出
print("=== 手动追踪策略执行 ===")
config = BacktestConfig(
    strategy_id=1,
    source_code=source_code,
    stock_pool=['000001.SZ'],
    start_date=date(2026, 4, 1),
    end_date=date(2026, 4, 30),
    initial_cash=Decimal('100000'),
)

sandbox = {"ctx": None}
for name in dir(MyTT):
    if not name.startswith("_"):
        sandbox[name] = getattr(MyTT, name)

exec(source_code, sandbox)
func = sandbox.get("generate_signal")
print(f"Strategy function found: {func is not None}")

lookback = 60
signals_found = 0
for i, bar in enumerate(klines):
    window = klines[max(0, i-lookback+1):i+1]
    if len(window) < 21:
        continue
    
    ctx = BacktestContext(window, {}, Decimal('100000'))
    
    try:
        ctx.current_position = 0.0  # 模拟空仓
        result = func(ctx)
        
        if result and isinstance(result, dict):
            sig_type = result.get('signal_type', 'UNKNOWN')
            target = result.get('target_position', 'N/A')
            
            if sig_type != '观望':
                signals_found += 1
                print(f"Day {i+1} ({bar.trade_date}): {sig_type} (target={target}) | MA5={float(ctx.close[-1]):.2f} MA20=?")
                
                # 只显示前10个非观望信号
                if signals_found >= 10:
                    print("... (more signals)")
                    break
        else:
            if i in [20, 21, 22, 25, 28]:  # 抽样几个关键日
                print(f"Day {i+1} ({bar.trade_date}): 返回 None 或非dict -> {result}")
    except Exception as e:
        print(f"Day {i+1} ({bar.trade_date}): 异常 -> {e}")
        import traceback
        traceback.print_exc()

print(f"\n=== 总计非观望信号数: {signals_found} ===")

# 现在运行真正的回测引擎
print("\n\n=== 运行完整回测引擎 ===")
runner = BacktestRunner(config)
results = runner.run(all_klines)

print(f'Total Return: {results["total_return"]:.4%}')
print(f'Trade Count: {results["trade_count"]}')
print(f'Signals generated: {len(runner.signals)}')

if runner.signals:
    print('\n=== 引擎记录的信号 (前10个) ===')
    for s in runner.signals[:10]:
        print(f'  {s}')

print('\n=== 交易记录 ===')
for t in results['trade_records'][:10]:
    print(f'{t["trade_date"]} | {t["direction"]:4} | {t["volume"]}股 @ {float(t["price"]):.2f}')
