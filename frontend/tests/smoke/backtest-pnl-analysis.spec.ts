import { expect, test } from '@playwright/test'

const apiBase = 'http://localhost:8000'

const rankings = [
  {
    ts_code: '600000.SH', closed_lot_count: 2, winning_lot_count: 2, losing_lot_count: 0,
    matched_cost: 20000, gross_pnl: 2500, total_fees: 50, net_pnl: 2450,
    return_rate: 0.1225, win_rate: 1, avg_holding_days: 8.5,
  },
  {
    ts_code: '000001.SZ', closed_lot_count: 1, winning_lot_count: 0, losing_lot_count: 1,
    matched_cost: 10000, gross_pnl: -900, total_fees: 30, net_pnl: -930,
    return_rate: -0.093, win_rate: 0, avg_holding_days: 4,
  },
]

const result = {
  id: 42,
  strategy_id: 7,
  strategy_name: '均线策略',
  target_label: '测试股票池',
  benchmark_code: '000300.SH',
  start_date: '2026-01-01',
  end_date: '2026-03-31',
  initial_cash: '100000',
  status: 'success',
  total_return: '0.0152',
  annual_return: '0.062',
  sharpe_ratio: '1.2',
  max_drawdown: '-0.03',
  annual_vol: '0.1',
  win_rate: '0.66',
  trade_count: 4,
  performance: {
    pnl_analysis: {
      closed_lot_count: 3,
      winning_lot_count: 2,
      losing_lot_count: 1,
      breakeven_lot_count: 0,
      stock_count: 2,
      matched_cost: 30000,
      gross_pnl: 1600,
      entry_fees: 30,
      exit_fees: 50,
      total_fees: 80,
      net_pnl: 1520,
      return_rate: 0.05067,
      win_rate: 0.6667,
      avg_holding_days: 7,
      closed_lots: [],
      stock_rankings: rankings,
    },
  },
  trade_records: [
    { ts_code: '600000.SH', trade_date: '2026-01-05', direction: '买入', price: 10, volume: 1000, amount: 10000, commission: 5, stamp_tax: 0, transfer_fee: 0.1, total_fee: 5.1, action: 'BUY' },
    { ts_code: '600000.SH', trade_date: '2026-01-20', direction: '卖出', price: 11, volume: 1000, amount: 11000, commission: 5, stamp_tax: 5.5, transfer_fee: 0.1, total_fee: 10.6, action: 'SELL_ALL', pnl: 984.3, holding_days: 15 },
    { ts_code: '000001.SZ', trade_date: '2026-02-02', direction: '买入', price: 12, volume: 800, amount: 9600, commission: 5, stamp_tax: 0, transfer_fee: 0.1, total_fee: 5.1, action: 'BUY' },
    { ts_code: '000001.SZ', trade_date: '2026-02-06', direction: '卖出', price: 11, volume: 800, amount: 8800, commission: 5, stamp_tax: 4.4, transfer_fee: 0.1, total_fee: 9.5, action: 'SELL_ALL', pnl: -814.6, holding_days: 4 },
  ],
  equity_curve: [
    { date: '2026-01-01', total_asset: 100000, cash: 100000 },
    { date: '2026-03-31', total_asset: 101520, cash: 101520 },
  ],
  daily_returns: null,
  kline_data: {
    '600000.SH': [
      { date: '2026-01-05', open: 9.8, high: 10.2, low: 9.7, close: 10, volume: 10000 },
      { date: '2026-01-20', open: 10.8, high: 11.2, low: 10.7, close: 11, volume: 12000 },
    ],
  },
  stock_names: { '600000.SH': '浦发银行', '000001.SZ': '平安银行' },
  error_message: null,
  created_at: '2026-04-01T00:00:00Z',
  finished_at: '2026-04-01T00:01:00Z',
}

test('backtest detail renders aggregate P&L rankings and collapsed trades', async ({ page }) => {
  await page.route(`${apiBase}/api/backtests?limit=20&offset=0`, async (route) => {
    await route.fulfill({ json: { items: [result], total: 1 } })
  })
  await page.route(`${apiBase}/api/backtests/42`, async (route) => {
    await route.fulfill({ json: result })
  })

  await page.goto('/backtests')
  await page.getByRole('button', { name: '查看' }).click()

  await expect(page.getByRole('heading', { name: '盈亏分析' })).toBeVisible()
  await expect(page.getByText('+¥1,520')).toBeVisible()
  await expect(page.getByText('浦发银行')).toBeVisible()
  await expect(page.getByText('平安银行')).toBeVisible()

  const tradeDisclosure = page.getByRole('button', { name: '交易明细 (4笔)' })
  await expect(tradeDisclosure).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByRole('columnheader', { name: /股票代码/ })).toHaveCount(0)

  await page.getByRole('button', { name: '收益率', exact: true }).click()
  await expect(page.getByText('+12.25%')).toBeVisible()

  await page.getByRole('button', { name: '查看 浦发银行 交易 K 线' }).click()
  await expect(page.getByText('600000.SH · 2026-01-20 · 卖出 · ¥11')).toBeVisible()

  await tradeDisclosure.press('Enter')
  await expect(tradeDisclosure).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByRole('columnheader', { name: /股票代码/ })).toBeVisible()
})

test('historical backtest prompts for rerun and keeps trades collapsed', async ({ page }) => {
  const historicalResult = {
    ...result,
    id: 43,
    performance: { total_fees: 25.2 },
  }
  await page.route(`${apiBase}/api/backtests?limit=20&offset=0`, async (route) => {
    await route.fulfill({ json: { items: [historicalResult], total: 1 } })
  })
  await page.route(`${apiBase}/api/backtests/43`, async (route) => {
    await route.fulfill({ json: historicalResult })
  })

  await page.goto('/backtests')
  await page.getByRole('button', { name: '查看' }).click()

  await expect(page.getByText(/历史结果暂无精确盈亏归因.*重新运行回测/)).toBeVisible()
  await expect(page.getByRole('heading', { name: '盈亏分析' })).toHaveCount(0)

  const tradeDisclosure = page.getByRole('button', { name: '交易明细 (4笔)' })
  await expect(tradeDisclosure).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByRole('columnheader', { name: /股票代码/ })).toHaveCount(0)

  await tradeDisclosure.click()
  await expect(tradeDisclosure).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByRole('columnheader', { name: /股票代码/ })).toBeVisible()
})
