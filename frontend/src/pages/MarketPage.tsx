import React from 'react'
import { RefreshCw, AlertTriangle, ChevronUp, ChevronDown, RotateCcw, Star, Loader2, X } from 'lucide-react'
import { fetchJson, formatMarketCap, formatNumber } from '../lib/utils'
import Skeleton from '../components/Skeleton'

interface StockBasic {
  ts_code: string
  symbol: string
  name: string
  industry: string | null
  list_date: string | null
  exchange: string | null
  is_delisted: boolean
  is_st: boolean
  latest_close: string | null
  latest_trade_date: string | null
  pe_ttm: string | null
  pb: string | null
  market_cap: string | null
  daily_kline_count: number
}

interface MarketStock {
  ts_code: string
  symbol: string
  name: string
  latest_close: string | null
  change_pct: string | null
  volume: string | null
  amount: string | null
  pe_ttm: string | null
  pb: string | null
  market_cap: string | null
}

interface StocksApiResponse {
  items: StockBasic[]
  page: number
  page_size: number
  total: number
}

interface WatchlistGroupOption {
  group_name: string
  item_count: number
}

type TabKey = 'basic' | 'daily'

type RowActionStock = {
  ts_code: string
  name: string
}

type MarketFilterState = {
  market: string
  exchange: string
  industry: string
  excludeSt: boolean
  excludeDelisted: boolean
  peMin: string
  peMax: string
  pbMin: string
  pbMax: string
  marketCapMinYi: string
  marketCapMaxYi: string
}

const DEFAULT_FILTERS: MarketFilterState = {
  market: '',
  exchange: '',
  industry: '',
  excludeSt: false,
  excludeDelisted: true,
  peMin: '',
  peMax: '',
  pbMin: '',
  pbMax: '',
  marketCapMinYi: '',
  marketCapMaxYi: '',
}

const MARKET_OPTIONS = ['主板', '创业板', '科创板', '北交所']
const EXCHANGE_OPTIONS = ['SH', 'SZ', 'BJ']
const DEFAULT_GROUP_NAME = '默认'

const defaultWatchlistGroups = (): WatchlistGroupOption[] => [{ group_name: DEFAULT_GROUP_NAME, item_count: 0 }]

const normalizeWatchlistGroups = (groups: WatchlistGroupOption[]): WatchlistGroupOption[] => {
  const nextGroups = groups.length > 0 ? groups : defaultWatchlistGroups()
  if (nextGroups.some((group) => group.group_name === DEFAULT_GROUP_NAME)) return nextGroups
  return [...defaultWatchlistGroups(), ...nextGroups]
}

const numericValue = (value: string): number | null => {
  const trimmed = value.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : Number.NaN
}

const marketCapYiToYuan = (value: string): string | null => {
  const parsed = numericValue(value)
  if (parsed === null || Number.isNaN(parsed)) return null
  return String(Math.round(parsed * 100000000))
}

const appendNumericParam = (search: URLSearchParams, key: string, value: string) => {
  const parsed = numericValue(value)
  if (parsed !== null && !Number.isNaN(parsed)) search.set(key, value.trim())
}

const buildStocksUrl = (params: {
  page: number
  pageSize: number
  query: string
  filters: MarketFilterState
}) => {
  const search = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.pageSize),
    exclude_st: String(params.filters.excludeSt),
    exclude_delisted: String(params.filters.excludeDelisted),
  })
  const query = params.query.trim()
  const industry = params.filters.industry.trim()
  if (query) search.set('query', query)
  if (params.filters.market) search.set('market', params.filters.market)
  if (params.filters.exchange) search.set('exchange', params.filters.exchange)
  if (industry) search.set('industry', industry)
  appendNumericParam(search, 'pe_min', params.filters.peMin)
  appendNumericParam(search, 'pe_max', params.filters.peMax)
  appendNumericParam(search, 'pb_min', params.filters.pbMin)
  appendNumericParam(search, 'pb_max', params.filters.pbMax)
  const marketCapMin = marketCapYiToYuan(params.filters.marketCapMinYi)
  const marketCapMax = marketCapYiToYuan(params.filters.marketCapMaxYi)
  if (marketCapMin) search.set('market_cap_min', marketCapMin)
  if (marketCapMax) search.set('market_cap_max', marketCapMax)
  return `/api/stocks?${search.toString()}`
}

const collectFilterErrors = (filters: MarketFilterState): string[] => {
  const errors: string[] = []
  const validateRange = (label: string, minValue: string, maxValue: string) => {
    const min = numericValue(minValue)
    const max = numericValue(maxValue)
    if (Number.isNaN(min) || Number.isNaN(max)) {
      errors.push(`${label}请输入有效数字`)
      return
    }
    if (min !== null && max !== null && min > max) errors.push(`${label}下限不能大于上限`)
  }
  validateRange('PE', filters.peMin, filters.peMax)
  validateRange('PB', filters.pbMin, filters.pbMax)
  validateRange('总市值', filters.marketCapMinYi, filters.marketCapMaxYi)
  return errors
}

const filterSummary = (filters: MarketFilterState): string[] => {
  const summary: string[] = []
  if (filters.market) summary.push(filters.market)
  if (filters.exchange) summary.push(filters.exchange)
  if (filters.industry.trim()) summary.push(filters.industry.trim())
  if (filters.excludeSt) summary.push('排除 ST')
  if (!filters.excludeDelisted) summary.push('包含退市')
  if (filters.peMin || filters.peMax) summary.push(`PE ${filters.peMin || '-'}-${filters.peMax || '-'}`)
  if (filters.pbMin || filters.pbMax) summary.push(`PB ${filters.pbMin || '-'}-${filters.pbMax || '-'}`)
  if (filters.marketCapMinYi || filters.marketCapMaxYi) summary.push(`市值 ${filters.marketCapMinYi || '-'}-${filters.marketCapMaxYi || '-'} 亿`)
  return summary
}

export default function MarketPage() {
  const [tab, setTab] = React.useState<TabKey>('basic')
  const [stocks, setStocks] = React.useState<StockBasic[]>([])
  const [totalStocks, setTotalStocks] = React.useState(0)
  const [dailyRows, setDailyRows] = React.useState<MarketStock[]>([])
  const [dailyTotal, setDailyTotal] = React.useState(0)
  const [loading, setLoading] = React.useState(true)
  const [dailyLoading, setDailyLoading] = React.useState(false)
  const [syncing, setSyncing] = React.useState(false)
  const [addingWatchlistCode, setAddingWatchlistCode] = React.useState<string | null>(null)
  const [syncingKlineCode, setSyncingKlineCode] = React.useState<string | null>(null)
  const [watchlistGroups, setWatchlistGroups] = React.useState<WatchlistGroupOption[]>(defaultWatchlistGroups)
  const [watchlistModalStock, setWatchlistModalStock] = React.useState<RowActionStock | null>(null)
  const [selectedWatchlistGroups, setSelectedWatchlistGroups] = React.useState<string[]>([DEFAULT_GROUP_NAME])
  const [notice, setNotice] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [query, setQuery] = React.useState('')
  const [dailyQuery, setDailyQuery] = React.useState('')
  const [sortKey, setSortKey] = React.useState<keyof MarketStock>('ts_code')
  const [sortDir, setSortDir] = React.useState<'asc' | 'desc'>('asc')
  const [dailyPage, setDailyPage] = React.useState(1)
  const [filters, setFilters] = React.useState<MarketFilterState>(DEFAULT_FILTERS)

  const activeFilterSummary = React.useMemo(() => filterSummary(filters), [filters])
  const filterErrors = React.useMemo(() => collectFilterErrors(filters), [filters])

  const loadWatchlistGroups = React.useCallback(async () => {
    try {
      const data = await fetchJson<WatchlistGroupOption[]>('/api/watchlist/groups')
      setWatchlistGroups(normalizeWatchlistGroups(data))
    } catch {
      setWatchlistGroups(defaultWatchlistGroups())
    }
  }, [])

  const loadStocks = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchJson<StocksApiResponse>(buildStocksUrl({ page: 1, pageSize: 200, query, filters }))
      setStocks(data.items)
      setTotalStocks(data.total)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [filters, query])

  const loadDaily = React.useCallback(async () => {
    setDailyLoading(true)
    setError(null)
    try {
      const data = await fetchJson<StocksApiResponse>(buildStocksUrl({ page: dailyPage, pageSize: 50, query: dailyQuery, filters }))
      const rows: MarketStock[] = data.items.map((item) => ({
        ts_code: item.ts_code,
        symbol: item.symbol,
        name: item.name,
        latest_close: item.latest_close,
        change_pct: null,
        volume: null,
        amount: null,
        pe_ttm: item.pe_ttm,
        pb: item.pb,
        market_cap: item.market_cap,
      }))
      setDailyRows(rows)
      setDailyTotal(data.total)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setDailyLoading(false)
    }
  }, [dailyPage, dailyQuery, filters])

  const syncDaily = React.useCallback(async () => {
    setSyncing(true)
    setNotice(null)
    setError(null)
    try {
      await fetchJson('/api/data/sync/kline', { method: 'POST', body: JSON.stringify({}) })
      setNotice('K 线同步完成')
      await loadDaily()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSyncing(false)
    }
  }, [loadDaily])

  const refreshActiveTab = React.useCallback(async () => {
    if (tab === 'basic') await loadStocks()
    else await loadDaily()
  }, [loadDaily, loadStocks, tab])

  const openWatchlistModal = React.useCallback((stock: RowActionStock) => {
    setWatchlistModalStock(stock)
    setSelectedWatchlistGroups([DEFAULT_GROUP_NAME])
    setNotice(null)
    setError(null)
    void loadWatchlistGroups()
  }, [loadWatchlistGroups])

  const closeWatchlistModal = React.useCallback(() => {
    if (addingWatchlistCode) return
    setWatchlistModalStock(null)
  }, [addingWatchlistCode])

  const toggleWatchlistGroup = React.useCallback((groupName: string) => {
    setSelectedWatchlistGroups((current) => (
      current.includes(groupName)
        ? current.filter((name) => name !== groupName)
        : [...current, groupName]
    ))
  }, [])

  const addToWatchlist = React.useCallback(async () => {
    if (!watchlistModalStock || selectedWatchlistGroups.length === 0) return
    setAddingWatchlistCode(watchlistModalStock.ts_code)
    setNotice(null)
    setError(null)
    try {
      await Promise.all(selectedWatchlistGroups.map((groupName) => (
        fetchJson('/api/watchlist', {
          method: 'POST',
          body: JSON.stringify({ ts_code: watchlistModalStock.ts_code, group_name: groupName }),
        })
      )))
      const targetText = selectedWatchlistGroups.length === 1
        ? `${selectedWatchlistGroups[0]}自选`
        : `${selectedWatchlistGroups.length} 个自选分类`
      setNotice(`已添加 ${watchlistModalStock.ts_code} 到 ${targetText}`)
      setWatchlistModalStock(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setAddingWatchlistCode(null)
    }
  }, [selectedWatchlistGroups, watchlistModalStock])

  const syncRowKline = React.useCallback(async (stock: RowActionStock) => {
    setSyncingKlineCode(stock.ts_code)
    setNotice(null)
    setError(null)
    try {
      await fetchJson('/api/data/sync/kline', {
        method: 'POST',
        body: JSON.stringify({ ts_codes: [stock.ts_code] }),
      })
      setNotice(`${stock.ts_code} K线同步完成`)
      await refreshActiveTab()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSyncingKlineCode(null)
    }
  }, [refreshActiveTab])

  const handleDailySort = (key: keyof MarketStock) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const sortedDaily = React.useMemo(() => {
    const closeVal = (r: MarketStock) => Number(r.latest_close) || 0
    const keyVal = (r: MarketStock, k: keyof MarketStock) => {
      if (k === 'latest_close') return closeVal(r)
      return r[k]
    }
    return [...dailyRows].sort((a, b) => {
      const va = keyVal(a, sortKey)
      const vb = keyVal(b, sortKey)
      if (typeof va === 'number' && typeof vb === 'number') return sortDir === 'asc' ? va - vb : vb - va
      return sortDir === 'asc' ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va))
    })
  }, [dailyRows, sortKey, sortDir])

  const updateFilters = (patch: Partial<MarketFilterState>) => {
    setFilters((current) => ({ ...current, ...patch }))
    setDailyPage(1)
  }

  const resetFilters = () => {
    setFilters(DEFAULT_FILTERS)
    setDailyPage(1)
  }

  const renderRowActions = (stock: RowActionStock) => {
    const adding = addingWatchlistCode === stock.ts_code
    const rowSyncing = syncingKlineCode === stock.ts_code
    return (
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={() => openWatchlistModal(stock)}
          disabled={adding}
          className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-line px-2.5 text-xs font-semibold text-ink transition hover:bg-rowHover disabled:cursor-not-allowed disabled:opacity-60"
          title={`选择 ${stock.ts_code} 的自选分类`}
        >
          <Star className="h-3.5 w-3.5" />
          {adding ? '添加中' : '加自选'}
        </button>
        <button
          type="button"
          onClick={() => void syncRowKline(stock)}
          disabled={rowSyncing}
          className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-accent px-2.5 text-xs font-semibold text-accent transition hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-60"
          title={`同步 ${stock.ts_code} K线`}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${rowSyncing ? 'animate-spin' : ''}`} />
          同步K线
        </button>
      </div>
    )
  }

  React.useEffect(() => { void loadStocks() }, [loadStocks])
  React.useEffect(() => { void loadDaily() }, [loadDaily])
  React.useEffect(() => { void loadWatchlistGroups() }, [loadWatchlistGroups])

  React.useEffect(() => {
    if (!watchlistModalStock) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeWatchlistModal()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [closeWatchlistModal, watchlistModalStock])

  return (
    <>
      {(notice || error) && (
        <section className={`rounded-lg border p-4 text-sm ${error ? 'border-red-200 bg-red-50 text-red-900' : 'border-emerald-200 bg-emerald-50 text-emerald-900'}`} role="status">
          <div className="flex items-start gap-2">
            {error ? <AlertTriangle className="mt-0.5 h-4 w-4" /> : null}
            <span className="break-words">{error ?? notice}</span>
          </div>
        </section>
      )}

      <section className="overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
        <div className="flex items-center gap-3 border-b border-line px-4 py-3">
          <div className="flex rounded-md border border-line p-0.5">
            <button
              onClick={() => setTab('basic')}
              className={`rounded-sm px-3 py-1.5 text-sm font-medium transition ${tab === 'basic' ? 'bg-surface text-ink' : 'text-muted hover:text-ink'}`}
            >
              股票列表
            </button>
            <button
              onClick={() => setTab('daily')}
              className={`rounded-sm px-3 py-1.5 text-sm font-medium transition ${tab === 'daily' ? 'bg-surface text-ink' : 'text-muted hover:text-ink'}`}
            >
              最新行情
            </button>
          </div>
          {activeFilterSummary.length > 0 && (
            <div className="hidden min-w-0 flex-1 items-center gap-1 md:flex">
              {activeFilterSummary.slice(0, 4).map((item) => (
                <span key={item} className="max-w-32 truncate rounded-md border border-line bg-surface px-2 py-1 text-xs font-medium text-muted">
                  {item}
                </span>
              ))}
              {activeFilterSummary.length > 4 && <span className="text-xs text-muted">+{activeFilterSummary.length - 4}</span>}
            </div>
          )}
          <input
            value={tab === 'basic' ? query : dailyQuery}
            onChange={(e) => {
              if (tab === 'basic') setQuery(e.target.value)
              else {
                setDailyQuery(e.target.value)
                setDailyPage(1)
              }
            }}
            placeholder="输入代码或名称"
            className="ml-auto h-9 w-40 rounded-md border border-line bg-panel px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent sm:w-56"
          />
          {tab === 'daily' && (
            <button
              type="button"
              onClick={() => void syncDaily()}
              disabled={syncing}
              className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw className={`h-4 w-4 ${syncing ? 'animate-spin' : ''}`} />
              同步 K 线
            </button>
          )}
        </div>

        <div className="border-b border-line bg-surface/40 px-4 py-3">
          <div className="grid gap-3 lg:grid-cols-12">
            <label className="space-y-1 lg:col-span-2">
              <span className="block text-xs font-medium text-muted">市场板块</span>
              <select
                value={filters.market}
                onChange={(event) => updateFilters({ market: event.target.value })}
                className="h-9 w-full rounded-md border border-line bg-panel px-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
              >
                <option value="">全部</option>
                {MARKET_OPTIONS.map((market) => <option key={market} value={market}>{market}</option>)}
              </select>
            </label>
            <label className="space-y-1 lg:col-span-2">
              <span className="block text-xs font-medium text-muted">交易所</span>
              <select
                value={filters.exchange}
                onChange={(event) => updateFilters({ exchange: event.target.value })}
                className="h-9 w-full rounded-md border border-line bg-panel px-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
              >
                <option value="">全部</option>
                {EXCHANGE_OPTIONS.map((exchange) => <option key={exchange} value={exchange}>{exchange}</option>)}
              </select>
            </label>
            <label className="space-y-1 lg:col-span-2">
              <span className="block text-xs font-medium text-muted">行业</span>
              <input
                value={filters.industry}
                onChange={(event) => updateFilters({ industry: event.target.value })}
                placeholder="行业名称"
                className="h-9 w-full rounded-md border border-line bg-panel px-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
              />
            </label>
            <label className="space-y-1 lg:col-span-2">
              <span className="block text-xs font-medium text-muted">PE</span>
              <div className="grid grid-cols-2 gap-2">
                <input value={filters.peMin} onChange={(event) => updateFilters({ peMin: event.target.value })} inputMode="decimal" placeholder="最小" className="h-9 min-w-0 rounded-md border border-line bg-panel px-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent" />
                <input value={filters.peMax} onChange={(event) => updateFilters({ peMax: event.target.value })} inputMode="decimal" placeholder="最大" className="h-9 min-w-0 rounded-md border border-line bg-panel px-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent" />
              </div>
            </label>
            <label className="space-y-1 lg:col-span-2">
              <span className="block text-xs font-medium text-muted">PB</span>
              <div className="grid grid-cols-2 gap-2">
                <input value={filters.pbMin} onChange={(event) => updateFilters({ pbMin: event.target.value })} inputMode="decimal" placeholder="最小" className="h-9 min-w-0 rounded-md border border-line bg-panel px-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent" />
                <input value={filters.pbMax} onChange={(event) => updateFilters({ pbMax: event.target.value })} inputMode="decimal" placeholder="最大" className="h-9 min-w-0 rounded-md border border-line bg-panel px-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent" />
              </div>
            </label>
            <label className="space-y-1 lg:col-span-2">
              <span className="block text-xs font-medium text-muted">总市值（亿）</span>
              <div className="grid grid-cols-2 gap-2">
                <input value={filters.marketCapMinYi} onChange={(event) => updateFilters({ marketCapMinYi: event.target.value })} inputMode="decimal" placeholder="最小" className="h-9 min-w-0 rounded-md border border-line bg-panel px-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent" />
                <input value={filters.marketCapMaxYi} onChange={(event) => updateFilters({ marketCapMaxYi: event.target.value })} inputMode="decimal" placeholder="最大" className="h-9 min-w-0 rounded-md border border-line bg-panel px-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent" />
              </div>
            </label>
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-3">
              <label className="inline-flex h-9 items-center gap-2 rounded-md border border-line bg-panel px-3 text-sm font-medium text-ink">
                <input type="checkbox" checked={filters.excludeSt} onChange={(event) => updateFilters({ excludeSt: event.target.checked })} className="h-4 w-4 accent-accent" />
                排除 ST
              </label>
              <label className="inline-flex h-9 items-center gap-2 rounded-md border border-line bg-panel px-3 text-sm font-medium text-ink">
                <input type="checkbox" checked={filters.excludeDelisted} onChange={(event) => updateFilters({ excludeDelisted: event.target.checked })} className="h-4 w-4 accent-accent" />
                排除退市
              </label>
              {filterErrors.length > 0 && <span className="text-sm text-red-600">{filterErrors[0]}</span>}
            </div>
            <button type="button" onClick={resetFilters} className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-line px-3 text-sm font-semibold text-ink hover:bg-rowHover">
              <RotateCcw className="h-4 w-4" />
              重置筛选
            </button>
          </div>
        </div>

        {tab === 'basic' && (
          <>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-line text-left text-sm">
                <thead className="bg-tableHead text-xs font-semibold uppercase text-muted">
                  <tr><th className="px-4 py-3">代码</th><th className="px-4 py-3">名称</th><th className="px-4 py-3">行业</th><th className="px-4 py-3">上市日期</th><th className="px-4 py-3">交易所</th><th className="px-4 py-3">最新价</th><th className="px-4 py-3">K线</th><th className="px-4 py-3 text-right">操作</th></tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {loading ? (<tr><td colSpan={8} className="px-4 py-4"><Skeleton.Table rows={5} columns={8} /></td></tr>) : stocks.length === 0 ? <tr><td colSpan={8} className="px-4 py-8 text-center text-muted">暂无数据</td></tr> : stocks.map((stock) => (
                    <tr key={stock.ts_code} className="hover:bg-rowHover">
                      <td className="whitespace-nowrap px-4 py-3 font-mono font-medium">{stock.ts_code}</td>
                      <td className="whitespace-nowrap px-4 py-3 font-medium">{stock.name}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-muted">{stock.industry ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-muted">{stock.list_date ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-muted">{stock.exchange ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums">{stock.latest_close ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums text-muted">{stock.daily_kline_count ?? 0}</td>
                      <td className="whitespace-nowrap px-4 py-3">{renderRowActions(stock)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="border-t border-line px-4 py-3 text-sm text-muted">
              共 {formatNumber(totalStocks)} 只股票，当前展示 {formatNumber(stocks.length)} 条
            </div>
          </>
        )}

        {tab === 'daily' && (
          <>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-line text-left text-sm">
                <thead className="bg-tableHead text-xs font-semibold uppercase text-muted">
                  <tr>
                    {[
                      ['ts_code', '代码'],
                      ['name', '名称'],
                      ['latest_close', '最新价'],
                      ['pe_ttm', '市盈率'],
                      ['pb', '市净率'],
                      ['market_cap', '总市值'],
                    ].map(([key, label]) => (
                      <th
                        key={key}
                        className="cursor-pointer select-none px-4 py-3 hover:bg-rowHover"
                        onClick={() => handleDailySort(key as keyof MarketStock)}
                      >
                        <div className="flex items-center gap-1">
                          {label}
                          {sortKey === key && (sortDir === 'asc' ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />)}
                        </div>
                      </th>
                    ))}
                    <th className="px-4 py-3 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {dailyLoading ? (<tr><td colSpan={7} className="px-4 py-4"><Skeleton.Table rows={5} columns={7} /></td></tr>) : sortedDaily.length === 0 ? <tr><td colSpan={7} className="px-4 py-8 text-center text-muted">暂无数据，请先同步 K 线</td></tr> : sortedDaily.map((row) => (
                    <tr key={row.ts_code} className="hover:bg-rowHover">
                      <td className="whitespace-nowrap px-4 py-3 font-mono font-medium">{row.ts_code}</td>
                      <td className="whitespace-nowrap px-4 py-3 font-medium">{row.name}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums">{row.latest_close ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums">{row.pe_ttm ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums">{row.pb ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums">{row.market_cap ? formatMarketCap(row.market_cap) : '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3">{renderRowActions(row)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between border-t border-line px-4 py-3">
              <span className="text-sm text-muted">
                共 {formatNumber(dailyTotal)} 只，当前展示 {formatNumber(sortedDaily.length)} 行
              </span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setDailyPage((p) => Math.max(1, p - 1))}
                  disabled={dailyPage <= 1}
                  className="h-8 rounded-md border border-line px-3 text-sm disabled:opacity-40"
                >
                  上一页
                </button>
                <span className="text-sm tabular-nums">第 {dailyPage} 页</span>
                <button
                  onClick={() => setDailyPage((p) => p + 1)}
                  className="h-8 rounded-md border border-line px-3 text-sm"
                >
                  下一页
                </button>
              </div>
            </div>
          </>
        )}
      </section>

      {watchlistModalStock && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4 py-6" role="dialog" aria-modal="true" aria-labelledby="market-watchlist-title" onMouseDown={closeWatchlistModal}>
          <div className="w-full max-w-md rounded-lg border border-line bg-panel shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
            <div className="flex items-start justify-between gap-4 border-b border-line px-6 py-5">
              <div className="min-w-0">
                <h2 id="market-watchlist-title" className="text-lg font-bold text-ink">选择自选分类</h2>
                <p className="mt-1 truncate text-sm text-muted">
                  {watchlistModalStock.name} · <span className="font-mono">{watchlistModalStock.ts_code}</span>
                </p>
              </div>
              <button
                type="button"
                onClick={closeWatchlistModal}
                disabled={addingWatchlistCode === watchlistModalStock.ts_code}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-muted hover:bg-rowHover hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="关闭"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="max-h-80 overflow-y-auto px-6 py-4">
              <div className="divide-y divide-line rounded-md border border-line">
                {watchlistGroups.map((group) => {
                  const checked = selectedWatchlistGroups.includes(group.group_name)
                  return (
                    <label key={group.group_name} className="flex min-h-12 cursor-pointer items-center gap-3 px-4 py-3 hover:bg-rowHover">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleWatchlistGroup(group.group_name)}
                        disabled={addingWatchlistCode === watchlistModalStock.ts_code}
                        className="h-4 w-4 accent-accent"
                      />
                      <span className="min-w-0 flex-1 truncate text-sm font-semibold text-ink">{group.group_name}</span>
                      <span className="text-xs text-muted">{formatNumber(group.item_count)} 只</span>
                    </label>
                  )
                })}
              </div>
            </div>

            <div className="flex items-center justify-between gap-3 border-t border-line px-6 py-4">
              <span className="text-sm text-muted">已选择 {formatNumber(selectedWatchlistGroups.length)} 个分类</span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={closeWatchlistModal}
                  disabled={addingWatchlistCode === watchlistModalStock.ts_code}
                  className="h-9 rounded-md border border-line px-3 text-sm font-semibold text-ink hover:bg-rowHover disabled:cursor-not-allowed disabled:opacity-50"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={() => void addToWatchlist()}
                  disabled={addingWatchlistCode === watchlistModalStock.ts_code || selectedWatchlistGroups.length === 0}
                  className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {addingWatchlistCode === watchlistModalStock.ts_code && <Loader2 className="h-4 w-4 animate-spin" />}
                  确认加入
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

    </>
  )
}
