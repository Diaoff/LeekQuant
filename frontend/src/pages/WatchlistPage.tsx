import React from 'react'
import { AlertTriangle, Search, Loader2 } from 'lucide-react'
import { fetchJson, formatNumber } from '../lib/utils'
import Skeleton from '../components/Skeleton'

interface WatchlistItem {
  id: number
  ts_code: string
  name: string
  group_name: string
  added_at: string
  note: string | null
  latest_close: string | null
  latest_trade_date: string | null
}

interface WatchlistGroup {
  group_name: string
  items: WatchlistItem[]
}

interface WatchlistGroupOption {
  group_name: string
  item_count: number
}

interface StockBasic {
  ts_code: string
  symbol: string
  name: string
}

interface StocksApiResponse {
  items: StockBasic[]
  page: number
  page_size: number
  total: number
}

export default function WatchlistPage() {
  const [groups, setGroups] = React.useState<WatchlistGroup[]>([])
  const [groupOptions, setGroupOptions] = React.useState<WatchlistGroupOption[]>([])
  const [allStocks, setAllStocks] = React.useState<StockBasic[]>([])
  const [loading, setLoading] = React.useState(true)
  const [adding, setAdding] = React.useState(false)
  const [searching, setSearching] = React.useState(false)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [query, setQuery] = React.useState('')
  const [filterQuery, setFilterQuery] = React.useState('')
  const [searchResults, setSearchResults] = React.useState<StockBasic[]>([])
  const [selectedGroupName, setSelectedGroupName] = React.useState('默认')
  const [newGroupName, setNewGroupName] = React.useState('')

  const flatItems = React.useMemo(() => groups.flatMap((g) => g.items), [groups])

  const loadWatchlist = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchJson<WatchlistGroup[]>('/api/watchlist')
      setGroups(data)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadGroups = React.useCallback(async () => {
    try {
      const data = await fetchJson<WatchlistGroupOption[]>('/api/watchlist/groups')
      setGroupOptions(data)
      if (data.length > 0 && !data.some((group) => group.group_name === selectedGroupName)) {
        setSelectedGroupName(data[0].group_name)
      }
    } catch {
      setGroupOptions([])
    }
  }, [selectedGroupName])

  const loadStocks = React.useCallback(async () => {
    try {
      const data = await fetchJson<StocksApiResponse>('/api/stocks?page_size=200')
      setAllStocks(data.items)
    } catch {
      // silently fail
    }
  }, [])

  const searchStock = React.useCallback(async (keyword: string) => {
    if (keyword.length === 0) {
      setSearchResults([])
      return
    }
    setSearching(true)
    try {
      if (allStocks.length > 0) {
        setSearchResults(allStocks.filter((s) => s.ts_code.includes(keyword.toUpperCase()) || s.symbol.includes(keyword.toUpperCase()) || s.name.includes(keyword)))
      } else {
        const results = await fetchJson<StocksApiResponse>(`/api/stocks?query=${encodeURIComponent(keyword)}&page_size=20`)
        setSearchResults(results.items)
      }
    } catch {
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }, [allStocks])

  const addStock = React.useCallback(async (stock: StockBasic) => {
    setAdding(true)
    setError(null)
    setNotice(null)
    try {
      const groupName = newGroupName.trim() || selectedGroupName || '默认'
      await fetchJson('/api/watchlist', {
        method: 'POST',
        body: JSON.stringify({ ts_code: stock.ts_code, group_name: groupName }),
      })
      setNotice(`已添加 ${stock.ts_code} ${stock.name} 到 ${groupName}`)
      setSearchResults([])
      setQuery('')
      setNewGroupName('')
      await loadWatchlist()
      await loadGroups()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setAdding(false)
    }
  }, [loadGroups, loadWatchlist, newGroupName, selectedGroupName])

  const removeStock = React.useCallback(async (id: number, tsCode: string) => {
    setError(null)
    setNotice(null)
    try {
      await fetchJson(`/api/watchlist/${id}`, { method: 'DELETE' })
      setNotice(`已移除 ${tsCode}`)
      await loadWatchlist()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }, [loadWatchlist])

  const filteredGroups = filterQuery.length > 0
    ? groups.map((g) => ({
        ...g,
        items: g.items.filter((s) => s.ts_code.includes(filterQuery.toUpperCase()) || s.name.includes(filterQuery)),
      })).filter((g) => g.items.length > 0)
    : groups

  React.useEffect(() => { void loadWatchlist() }, [loadWatchlist])
  React.useEffect(() => { void loadStocks() }, [loadStocks])
  React.useEffect(() => { void loadGroups() }, [loadGroups])

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

      <section className="rounded-lg border border-line bg-panel p-4 shadow-sm">
        <h2 className="text-base font-semibold text-ink">添加自选</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-600">加入分组</label>
            <select
              value={selectedGroupName}
              onChange={(e) => setSelectedGroupName(e.target.value)}
              className="w-full h-10 rounded-md border border-line bg-panel px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
            >
              {groupOptions.length === 0 && <option value="默认">默认</option>}
              {groupOptions.map((group) => (
                <option key={group.group_name} value={group.group_name}>{group.group_name} ({group.item_count} 只)</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-600">新分组（可选）</label>
            <input
              value={newGroupName}
              onChange={(e) => setNewGroupName(e.target.value)}
              placeholder="直接输入新分组名"
              className="w-full h-10 rounded-md border border-line bg-panel px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
            />
          </div>
        </div>
        <div className="relative mt-3">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            value={query}
            onChange={(e) => { setQuery(e.target.value); void searchStock(e.target.value) }}
            placeholder="输入股票代码或名称"
            className="w-full h-10 rounded-md border border-line bg-panel pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
          />
          {searching && <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-slate-400" />}
        </div>

        {searchResults.length > 0 && (
          <div className="mt-2 max-h-40 overflow-y-auto rounded-md border border-line bg-panel">
            {searchResults.map((s) => (
              <button
                key={s.ts_code}
                onClick={() => void addStock(s)}
                disabled={adding}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-rowHover disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span>
                  <span className="font-mono font-medium">{s.ts_code}</span>{' '}
                  <span className="text-muted">{s.name}</span>
                </span>
                {adding ? <Loader2 className="h-3 w-3 animate-spin text-slate-400" /> : <span className="text-xs font-medium text-accent">添加</span>}
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
        <div className="flex items-center gap-3 border-b border-line px-4 py-3">
          <h2 className="text-base font-semibold text-ink">自选股列表</h2>
          <input
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            placeholder="筛选"
            className="ml-auto h-9 rounded-md border border-line bg-panel px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
          />
          <span className="text-sm text-muted">{formatNumber(flatItems.length)} 只</span>
        </div>
        {loading ? (
          <div className="px-4 py-4"><Skeleton.Table rows={5} columns={5} /></div>
        ) : filteredGroups.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-muted">暂无自选股</div>
        ) : (
          filteredGroups.map((group) => (
            <div key={group.group_name}>
              <div className="border-t border-line px-4 py-2 text-sm font-medium text-muted">{group.group_name}</div>
              <table className="min-w-full divide-y divide-line text-left text-sm">
                <thead className="bg-tableHead text-xs font-semibold uppercase text-muted">
                  <tr><th className="px-4 py-3">代码</th><th className="px-4 py-3">名称</th><th className="px-4 py-3">最新价</th><th className="px-4 py-3">加入时间</th><th className="px-4 py-3">操作</th></tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {group.items.map((stock) => (
                    <tr key={stock.id} className="hover:bg-rowHover">
                      <td className="whitespace-nowrap px-4 py-3 font-mono font-medium">{stock.ts_code}</td>
                      <td className="whitespace-nowrap px-4 py-3 font-medium">{stock.name}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums">{stock.latest_close ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-muted">{new Date(stock.added_at).toLocaleDateString('zh-CN')}</td>
                      <td className="px-4 py-3">
                        <button onClick={() => void removeStock(stock.id, stock.ts_code)} className="text-sm font-medium text-red-600 hover:underline">移除</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))
        )}
      </section>
    </>
  )
}
