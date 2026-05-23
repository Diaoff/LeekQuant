import { expect, test } from '@playwright/test'

const apiBase = 'http://localhost:8000'

test('factor page renders, loads initial APIs, blocks missing group submit, and refreshes Top N', async ({ page }) => {
  const initialRequests = new Set<string>()
  const rankRequests: string[] = []
  const computeRequests: unknown[] = []

  await page.route(`${apiBase}/api/factors?enabled_only=false`, async (route) => {
    initialRequests.add('factors')
    await route.fulfill({
      json: [
        {
          name: 'roe',
          display_name: 'ROE',
          category: 'quality',
          expression: 'stock_fundamentals.roe',
          direction: 1,
          default_weight: '1.200000',
          enabled: true,
          description: '净资产收益率',
          created_at: '2026-05-22T00:00:00Z',
          updated_at: '2026-05-22T00:00:00Z',
        },
      ],
    })
  })
  await page.route(`${apiBase}/api/watchlist/groups`, async (route) => {
    initialRequests.add('groups')
    await route.fulfill({
      json: [{ group_name: '价值', item_count: 2 }],
    })
  })
  await page.route(`${apiBase}/api/factors/rank**`, async (route) => {
    initialRequests.add('rank')
    rankRequests.push(route.request().url())
    await route.fulfill({
      json: {
        items: [
          {
            id: 1,
            trade_date: '2026-05-22',
            ts_code: '000001.SZ',
            stock_name: '平安银行',
            scope_type: 'all',
            scope_value: null,
            total_score: '0.90000000',
            rank: 1,
            percentile_rank: '1.00000000',
            factor_breakdown: { roe: { normalized_value: '1.0', weight: '1.2' } },
            created_at: '2026-05-22T00:00:00Z',
            updated_at: '2026-05-22T00:00:00Z',
          },
        ],
        page: 1,
        page_size: 50,
        total: 1,
      },
    })
  })
  await page.route(`${apiBase}/api/factors/analysis**`, async (route) => {
    initialRequests.add('analysis')
    await route.fulfill({
      json: {
        items: [],
        page: 1,
        page_size: 50,
        total: 0,
      },
    })
  })
  await page.route(`${apiBase}/api/factors/values**`, async (route) => {
    await route.fulfill({
      json: {
        items: [],
        page: 1,
        page_size: 100,
        total: 0,
      },
    })
  })
  await page.route(`${apiBase}/api/tasks/factors/compute`, async (route) => {
    computeRequests.push(route.request().postDataJSON())
    await route.fulfill({ json: { task_id: 'smoke-task', status: 'pending' } })
  })

  await page.goto('/factor')

  await expect(page.getByRole('heading', { name: '因子选股', level: 1 })).toBeVisible()
  await expect(page.getByText('平安银行')).toBeVisible()
  await expect
    .poll(() => Array.from(initialRequests).sort())
    .toEqual(['analysis', 'factors', 'groups', 'rank'])

  await page.getByLabel('Top N').fill('25')
  await expect
    .poll(() => rankRequests.at(-1) ?? '')
    .toContain('page_size=25')

  await page.getByLabel('范围').selectOption('watchlist_group')
  await expect(page.getByRole('button', { name: /计算因子/ })).toBeDisabled()
  expect(computeRequests).toEqual([])
})

test('factor page renders with reachable backend factor APIs', async ({ page, request }) => {
  const probes = await Promise.all([
    request.get(`${apiBase}/api/factors?enabled_only=false`, { timeout: 2_000 }).catch(() => null),
    request.get(`${apiBase}/api/factors/rank?page_size=5&scope_type=all`, { timeout: 2_000 }).catch(() => null),
    request.get(`${apiBase}/api/factors/analysis?page_size=5`, { timeout: 2_000 }).catch(() => null),
    request.get(`${apiBase}/api/watchlist/groups`, { timeout: 2_000 }).catch(() => null),
  ])
  test.skip(probes.some((response) => response === null || !response.ok()), 'local backend factor APIs are not reachable')
  const [factorResponse, rankResponse, analysisResponse, groupsResponse] = probes
  if (!factorResponse || !rankResponse || !analysisResponse || !groupsResponse) return
  const backendBodies = {
    factors: await factorResponse.body(),
    rank: await rankResponse.body(),
    analysis: await analysisResponse.body(),
    groups: await groupsResponse.body(),
  }

  const responses: string[] = []
  await page.route(`${apiBase}/api/factors**`, async (route) => {
    const url = route.request().url()
    const body = url.includes('/rank') ? backendBodies.rank : url.includes('/analysis') ? backendBodies.analysis : backendBodies.factors
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'application/json' },
      body,
    })
  })
  await page.route(`${apiBase}/api/watchlist/groups`, async (route) => {
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'application/json' },
      body: backendBodies.groups,
    })
  })
  page.on('response', (response) => {
    if (response.url().startsWith(`${apiBase}/api/factors`)) {
      responses.push(`${response.request().method()} ${response.url()} ${response.status()}`)
    }
  })

  await page.goto('/factor')

  await expect(page.getByRole('heading', { name: '因子选股', level: 1 })).toBeVisible()
  await expect(page.getByRole('button', { name: /计算因子/ })).toBeVisible()
  await expect
    .poll(() => responses.filter((entry) => entry.endsWith(' 200')).length)
    .toBeGreaterThanOrEqual(3)
})
