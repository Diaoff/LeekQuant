import { expect, test } from '@playwright/test'

const apiBase = 'http://localhost:8000'

const historicalResult = {
  id: 71,
  strategy_id: 9,
  strategy_name: '多因子轮动',
  target_type: 'all',
  target_value: null,
  target_label: '创业板、科创板',
  benchmark_code: '000300.SH',
  start_date: '2024-01-02',
  end_date: '2025-06-30',
  initial_cash: '250000',
  status: 'success',
  total_return: '0.12',
  annual_return: '0.08',
  sharpe_ratio: '1.1',
  max_drawdown: '-0.06',
  annual_vol: '0.15',
  win_rate: '0.55',
  trade_count: 12,
  params_snapshot: {
    start_date: '2000-01-01',
    end_date: '2000-12-31',
    initial_cash: 1,
    benchmark_code: 'SNAPSHOT',
    target: { type: 'market', value: ['创业板', '科创板'] },
    filters: { exclude_st: false, exclude_loss_pe: true },
    config: {
      stop_loss_pct: 0.055,
      take_profit_pct: 0.12,
      trailing_stop_pct: 0.025,
      time_stop_days: 18,
      rebalance_mode: 'ranked',
      max_positions: 8,
      slippage_pct: 0.001,
      fee_config: { commission_rate: 0.00025, minimum: 5 },
    },
  },
  performance: { risk_config: { stop_loss_pct: 0.99 } },
  trade_records: [],
  equity_curve: [],
  daily_returns: [],
  kline_data: null,
  stock_names: {},
  error_message: null,
  created_at: '2025-07-01T00:00:00Z',
  finished_at: '2025-07-01T00:01:00Z',
}

test('reruns a historical backtest from its snapshot and preserves unknown config', async ({ page }) => {
  let listCalls = 0
  let submitted: Record<string, unknown> | null = null

  await page.route(`${apiBase}/api/backtests?limit=20&offset=0`, async (route) => {
    listCalls += 1
    const pending = {
      ...historicalResult,
      id: 72,
      status: 'pending',
      total_return: null,
      annual_return: null,
      sharpe_ratio: null,
      max_drawdown: null,
      trade_count: null,
      created_at: '2025-07-01T00:02:00Z',
    }
    await route.fulfill({ json: { items: listCalls > 1 ? [pending, historicalResult] : [historicalResult], total: listCalls > 1 ? 2 : 1 } })
  })
  await page.route(`${apiBase}/api/backtests/71`, async (route) => route.fulfill({ json: historicalResult }))
  await page.route(`${apiBase}/api/watchlist/groups`, async (route) => route.fulfill({ json: [{ group_name: '核心池', item_count: 6 }] }))
  await page.route(`${apiBase}/api/backtests`, async (route) => {
    submitted = route.request().postDataJSON() as Record<string, unknown>
    await route.fulfill({ status: 201, json: { backtest_id: 72, strategy_id: 9, task_id: 'task-72', status: 'pending' } })
  })

  await page.goto('/backtests')
  await page.getByRole('button', { name: '查看' }).click()
  await page.getByRole('button', { name: '重新运行' }).click()

  const dialog = page.getByRole('dialog', { name: /重新运行/ })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByLabel('开始日期')).toHaveValue('2024-01-02')
  await expect(dialog.getByLabel('结束日期')).toHaveValue('2025-06-30')
  await expect(dialog.getByLabel('初始资金')).toHaveValue('250000')
  await expect(dialog.getByLabel('基准代码（可选）')).toHaveValue('000300.SH')
  await expect(dialog.getByLabel('创业板')).toBeChecked()
  await expect(dialog.getByLabel('科创板')).toBeChecked()
  await expect(dialog.getByLabel('排除 ST')).not.toBeChecked()
  await expect(dialog.getByLabel(/排除亏损市盈率/)).toBeChecked()
  await expect(dialog.getByRole('spinbutton', { name: '止损 %（可选）', exact: true })).toHaveValue('5.5')
  await expect(dialog.getByRole('spinbutton', { name: '止盈 %（可选）', exact: true })).toHaveValue('12')
  await expect(dialog.getByRole('spinbutton', { name: '移动止损 %（可选）', exact: true })).toHaveValue('2.5')
  await expect(dialog.getByRole('spinbutton', { name: '最大持仓天数（可选）', exact: true })).toHaveValue('18')
  await expect(dialog.getByLabel('调仓模式')).toHaveValue('ranked')
  await expect(dialog.getByLabel('最大持仓数')).toHaveValue('8')

  await dialog.getByRole('spinbutton', { name: '止损 %（可选）', exact: true }).fill('6.25')
  await dialog.getByLabel('最大持仓数').fill('10')
  await dialog.getByRole('button', { name: '重新运行' }).click()

  await expect(page.getByRole('status').filter({ hasText: '回测任务已提交（#72）' })).toBeVisible()
  await expect(page.getByText('pending', { exact: true })).toBeVisible()
  expect(submitted).toMatchObject({
    strategy_id: 9,
    start_date: '2024-01-02',
    end_date: '2025-06-30',
    initial_cash: 250000,
    benchmark_code: '000300.SH',
    target_type: 'market',
    target_value: ['创业板', '科创板'],
    exclude_st: false,
    exclude_loss_pe: true,
    config: {
      stop_loss_pct: 0.0625,
      take_profit_pct: 0.12,
      trailing_stop_pct: 0.025,
      time_stop_days: 18,
      rebalance_mode: 'ranked',
      max_positions: 10,
      slippage_pct: 0.001,
      fee_config: { commission_rate: 0.00025, minimum: 5 },
    },
  })
})

test('supports legacy flat snapshots and keeps edits visible after submit failure', async ({ page }) => {
  const legacyResult = {
    ...historicalResult,
    id: 73,
    target_type: 'market',
    target_value: '创业板',
    benchmark_code: '',
    params_snapshot: {
      target_type: 'market',
      target_value: '创业板',
      exclude_st: false,
      exclude_loss_pe: false,
      config: { stop_loss_pct: 0.07 },
    },
    performance: null,
  }

  await page.route(`${apiBase}/api/backtests?limit=20&offset=0`, async (route) => {
    await route.fulfill({ json: { items: [legacyResult], total: 1 } })
  })
  await page.route(`${apiBase}/api/backtests/73`, async (route) => route.fulfill({ json: legacyResult }))
  await page.route(`${apiBase}/api/watchlist/groups`, async (route) => route.fulfill({ json: [] }))
  await page.route(`${apiBase}/api/backtests`, async (route) => {
    await route.fulfill({ status: 503, json: { detail: 'backtest worker unavailable' } })
  })

  await page.goto('/backtests')
  await page.getByRole('button', { name: '查看' }).click()
  await page.getByRole('button', { name: '重新运行' }).click()

  const dialog = page.getByRole('dialog', { name: /重新运行/ })
  await expect(dialog.getByLabel('创业板')).toBeChecked()
  await expect(dialog.getByLabel('排除 ST')).not.toBeChecked()
  await expect(dialog.getByLabel(/排除亏损市盈率/)).not.toBeChecked()
  const stopLoss = dialog.getByRole('spinbutton', { name: '止损 %（可选）', exact: true })
  await expect(stopLoss).toHaveValue('7')
  await stopLoss.fill('8.5')
  await dialog.getByRole('button', { name: '重新运行' }).click()

  await expect(dialog.getByRole('alert')).toContainText('backtest worker unavailable')
  await expect(stopLoss).toHaveValue('8.5')
  await expect(dialog).toBeVisible()
})
