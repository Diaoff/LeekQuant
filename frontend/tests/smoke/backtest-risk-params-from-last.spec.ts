import { expect, test } from '@playwright/test'

const apiBase = 'http://localhost:8000'

const lastBacktest = {
  id: 101,
  strategy_id: 1,
  strategy_name: '双均线',
  target_type: 'all',
  target_value: null,
  target_label: '全市场',
  benchmark_code: '',
  start_date: '2024-01-01',
  end_date: '2024-12-31',
  initial_cash: '100000',
  status: 'success',
  total_return: '0.15',
  annual_return: '0.15',
  sharpe_ratio: '1.2',
  max_drawdown: '-0.10',
  annual_vol: '0.20',
  win_rate: '0.45',
  trade_count: 30,
  params_snapshot: {
    start_date: '2024-01-01',
    end_date: '2024-12-31',
    initial_cash: 100000,
    benchmark_code: '',
    config: {
      stop_loss_pct: 0.08,
      take_profit_pct: 0.20,
      trailing_stop_pct: 0.03,
      time_stop_days: 15,
    },
    filters: { exclude_st: true, exclude_loss_pe: true },
    target: { type: 'all', value: null },
  },
  performance: null,
  trade_records: null,
  equity_curve: null,
  daily_returns: null,
  kline_data: null,
  stock_names: null,
  error_message: null,
  created_at: '2025-07-01T12:00:00Z',
  finished_at: '2025-07-01T12:05:00Z',
}

const lastBacktestForOtherStrategy = {
  id: 102,
  strategy_id: 2,
  strategy_name: '其他策略',
  target_type: 'all',
  target_value: null,
  target_label: '全市场',
  benchmark_code: '',
  start_date: '2024-01-01',
  end_date: '2024-12-31',
  initial_cash: '100000',
  status: 'success',
  total_return: '0.05',
  annual_return: '0.05',
  sharpe_ratio: '0.8',
  max_drawdown: '-0.05',
  annual_vol: '0.15',
  win_rate: '0.40',
  trade_count: 10,
  params_snapshot: {
    start_date: '2024-01-01',
    end_date: '2024-12-31',
    initial_cash: 100000,
    benchmark_code: '',
    config: {
      stop_loss_pct: 0.99,
      take_profit_pct: 0.99,
      trailing_stop_pct: 0.99,
      time_stop_days: 99,
    },
    filters: { exclude_st: true, exclude_loss_pe: true },
    target: { type: 'all', value: null },
  },
  performance: null,
  trade_records: null,
  equity_curve: null,
  daily_returns: null,
  kline_data: null,
  stock_names: null,
  error_message: null,
  created_at: '2025-07-01T14:00:00Z',
  finished_at: '2025-07-01T14:05:00Z',
}

test('new backtest modal pre-fills risk params from the last backtest for that strategy', async ({ page }) => {
  let submitted: Record<string, unknown> | null = null

  await page.route(`${apiBase}/api/strategies`, async (route) => {
    await route.fulfill({
      json: [
        { id: 1, name: '双均线', description: null, status: 'active', created_at: '2025-01-01T00:00:00Z', updated_at: '2025-01-01T00:00:00Z' },
        { id: 2, name: '其他策略', description: null, status: 'active', created_at: '2025-01-02T00:00:00Z', updated_at: '2025-01-02T00:00:00Z' },
      ],
    })
  })
  await page.route(`${apiBase}/api/watchlist/groups`, async (route) => {
    await route.fulfill({ json: [{ group_name: '核心池', item_count: 6 }] })
  })
  await page.route(`${apiBase}/api/backtests?limit=20`, async (route) => {
    await route.fulfill({ json: { items: [lastBacktestForOtherStrategy, lastBacktest], total: 2, page: 1, page_size: 20 } })
  })
  await page.route(`${apiBase}/api/backtests`, async (route) => {
    submitted = route.request().postDataJSON() as Record<string, unknown>
    await route.fulfill({ status: 201, json: { backtest_id: 103, strategy_id: 1, task_id: 'task-103', status: 'pending' } })
  })

  await page.goto('/strategy')
  await page.getByRole('button', { name: '回测' }).first().click()

  const dialog = page.getByRole('dialog', { name: '回测参数设置' })
  await expect(dialog).toBeVisible()

  await expect(dialog.getByRole('spinbutton', { name: '止损 %（可选）', exact: true })).toHaveValue('8')
  await expect(dialog.getByRole('spinbutton', { name: '止盈 %（可选）', exact: true })).toHaveValue('20')
  await expect(dialog.getByRole('spinbutton', { name: '移动止损 %（可选）', exact: true })).toHaveValue('3')
  await expect(dialog.getByRole('spinbutton', { name: '最大持仓天数（可选）', exact: true })).toHaveValue('15')

  await dialog.getByRole('button', { name: '确认回测' }).click()

  await expect(page.getByRole('status').filter({ hasText: '回测任务已提交' })).toBeVisible()
  expect(submitted).toMatchObject({
    strategy_id: 1,
    config: {
      stop_loss_pct: 0.08,
      take_profit_pct: 0.2,
      trailing_stop_pct: 0.03,
      time_stop_days: 15,
    },
  })
})

test('new backtest modal uses default empty risk params when no history exists', async ({ page }) => {
  await page.route(`${apiBase}/api/strategies`, async (route) => {
    await route.fulfill({
      json: [
        { id: 1, name: '双均线', description: null, status: 'active', created_at: '2025-01-01T00:00:00Z', updated_at: '2025-01-01T00:00:00Z' },
      ],
    })
  })
  await page.route(`${apiBase}/api/watchlist/groups`, async (route) => {
    await route.fulfill({ json: [] })
  })
  await page.route(`${apiBase}/api/backtests?limit=20`, async (route) => {
    await route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 20 } })
  })

  await page.goto('/strategy')
  await page.getByRole('button', { name: '回测' }).first().click()

  const dialog = page.getByRole('dialog', { name: '回测参数设置' })
  await expect(dialog).toBeVisible()

  await expect(dialog.getByRole('spinbutton', { name: '止损 %（可选）', exact: true })).toHaveValue('')
  await expect(dialog.getByRole('spinbutton', { name: '止盈 %（可选）', exact: true })).toHaveValue('')
  await expect(dialog.getByRole('spinbutton', { name: '移动止损 %（可选）', exact: true })).toHaveValue('')
  await expect(dialog.getByRole('spinbutton', { name: '最大持仓天数（可选）', exact: true })).toHaveValue('')
})
