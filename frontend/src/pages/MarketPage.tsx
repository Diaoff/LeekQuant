import React from 'react'
import { RefreshCw, AlertTriangle, ChevronUp, ChevronDown } from 'lucide-react'
import { fetchJson, formatMarketCap, formatNumber } from '../App'

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

type TabKey = 'basic' | 'daily'

export default function MarketPage() {
  const [tab, setTab] = React.useState<TabKey>('basic')
  const [stocks, setStocks] = React.useState<StockBasic[]>([])
  const [totalStocks, setTotalStocks] = React.useState(0)
  const [dailyRows, setDailyRows] = React.useState<MarketStock[]>([])
  const [dailyTotal, setDailyTotal] = React.useState(0)
  const [loading, setLoading] = React.useState(true)
  const [dailyLoading, setDailyLoading] = React.useState(false)
  const [syncing, setSyncing] = React.useState(false)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [query, setQuery] = React.useState('')
  const [dailyQuery, setDailyQuery] = React.useState('')
  const [sortKey, setSortKey] = React.useState<keyof MarketStock>('ts_code')
  const [sortDir, setSortDir] = React.useState<'asc' | 'desc'>('asc')
  const [dailyPage, setDailyPage] = React.useState(1)

  const loadStocks = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchJson<StocksApiResponse>('/api/stocks?page_size=200')
      setStocks(data.items)
      setTotalStocks(data.total)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadDaily = React.useCallback(async () => {
    setDailyLoading(true)
    setError(null)
    try {
      const data = await fetchJson<StocksApiResponse>(`/api/stocks?page=${dailyPage}&page_size=50&exclude_delisted=true`)
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
  }, [dailyPage])

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

  const filteredStocks = query.length > 0
    ? stocks.filter((s) => s.ts_code.includes(query.toUpperCase()) || s.symbol.includes(query.toUpperCase()) || (s.name ?? '').includes(query))
    : stocks

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

  const filteredDaily = dailyQuery.length > 0
    ? sortedDaily.filter((r) => r.ts_code.includes(dailyQuery.toUpperCase()) || r.name.includes(dailyQuery))
    : sortedDaily

  React.useEffect(() => { void loadStocks() }, [loadStocks])
  React.useEffect(() => { void loadDaily() }, [loadDaily])

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

      <section className="overflow-hidden rounded-lg border border-line bg-white shadow-sm">
        <div className="flex items-center gap-3 border-b border-line px-4 py-3">
          <div className="flex rounded-md border border-line p-0.5">
            <button
              onClick={() => setTab('basic')}
              className={`rounded-sm px-3 py-1.5 text-sm font-medium transition ${tab === 'basic' ? 'bg-surface text-ink' : 'text-slate-500 hover:text-ink'}`}
            >
              股票列表
            </button>
            <button
              onClick={() => setTab('daily')}
              className={`rounded-sm px-3 py-1.5 text-sm font-medium transition ${tab === 'daily' ? 'bg-surface text-ink' : 'text-slate-500 hover:text-ink'}`}
            >
              最新行情
            </button>
          </div>
          <input
            value={tab === 'basic' ? query : dailyQuery}
            onChange={(e) => (tab === 'basic' ? setQuery(e.target.value) : setDailyQuery(e.target.value))}
            placeholder="输入代码或名称"
            className="ml-auto h-9 rounded-md border border-line bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
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

        {tab === 'basic' && (
          <>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-line text-left text-sm">
                <thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-600">
                  <tr><th className="px-4 py-3">代码</th><th className="px-4 py-3">名称</th><th className="px-4 py-3">行业</th><th className="px-4 py-3">上市日期</th><th className="px-4 py-3">交易所</th><th className="px-4 py-3">最新价</th></tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {loading ? <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">加载中</td></tr> : filteredStocks.length === 0 ? <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">暂无数据</td></tr> : filteredStocks.map((stock) => (
                    <tr key={stock.ts_code} className="hover:bg-slate-50/60">
                      <td className="whitespace-nowrap px-4 py-3 font-mono font-medium">{stock.ts_code}</td>
                      <td className="whitespace-nowrap px-4 py-3 font-medium">{stock.name}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-700">{stock.industry ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-700">{stock.list_date ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-700">{stock.exchange ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums">{stock.latest_close ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="border-t border-line px-4 py-3 text-sm text-slate-500">
              共 {formatNumber(totalStocks)} 只股票，当前展示 {formatNumber(filteredStocks.length)} 条
            </div>
          </>
        )}

        {tab === 'daily' && (
          <>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-line text-left text-sm">
                <thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-600">
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
                        className="cursor-pointer select-none px-4 py-3 hover:bg-slate-100"
                        onClick={() => handleDailySort(key as keyof MarketStock)}
                      >
                        <div className="flex items-center gap-1">
                          {label}
                          {sortKey === key && (sortDir === 'asc' ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />)}
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {dailyLoading ? <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">加载中</td></tr> : filteredDaily.length === 0 ? <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">暂无数据，请先同步 K 线</td></tr> : filteredDaily.map((row) => (
                    <tr key={row.ts_code} className="hover:bg-slate-50/60">
                      <td className="whitespace-nowrap px-4 py-3 font-mono font-medium">{row.ts_code}</td>
                      <td className="whitespace-nowrap px-4 py-3 font-medium">{row.name}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums">{row.latest_close ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums">{row.pe_ttm ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums">{row.pb ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums">{row.market_cap ? formatMarketCap(row.market_cap) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between border-t border-line px-4 py-3">
              <span className="text-sm text-slate-500">
                共 {formatNumber(dailyTotal)} 只，当前展示 {formatNumber(filteredDaily.length)} 行
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
    </>
  )
}
