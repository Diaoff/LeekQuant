import React from 'react'
import { AlertTriangle, Search, Loader2, Plus, X, Menu, Pencil, Trash2, Settings2, Check } from 'lucide-react'
import { fetchJson, formatNumber } from '../lib/utils'
import Skeleton from '../components/Skeleton'
import WatchlistSparkline from '../components/WatchlistSparkline'

interface WatchlistItem {
  id: number
  ts_code: string
  name: string
  group_name: string
  added_at: string
  note: string | null
  latest_close: string | null
  pre_close: string | null
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
  market?: string | null
  exchange?: string | null
}

interface StocksApiResponse {
  items: StockBasic[]
  page: number
  page_size: number
  total: number
}

const DEFAULT_GROUP_NAME = '默认'

function encodePath(value: string) {
  return encodeURIComponent(value)
}

function marketBadge(stock: StockBasic) {
  if (stock.exchange === 'SH') return '沪A'
  if (stock.exchange === 'SZ') return '深A'
  if (stock.exchange === 'BJ') return '北A'
  return stock.market ?? 'A股'
}

export default function WatchlistPage() {
  const [groups, setGroups] = React.useState<WatchlistGroup[]>([])
  const [groupOptions, setGroupOptions] = React.useState<WatchlistGroupOption[]>([])
  const [loading, setLoading] = React.useState(true)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [activeGroupName, setActiveGroupName] = React.useState(DEFAULT_GROUP_NAME)
  const [groupModalOpen, setGroupModalOpen] = React.useState(false)
  const [searchOpen, setSearchOpen] = React.useState(false)
  const [query, setQuery] = React.useState('')
  const [searchResults, setSearchResults] = React.useState<StockBasic[]>([])
  const [searching, setSearching] = React.useState(false)
  const [addingCode, setAddingCode] = React.useState<string | null>(null)
  const [editingGroupName, setEditingGroupName] = React.useState<string | null>(null)
  const [editingValue, setEditingValue] = React.useState('')
  const [newGroupName, setNewGroupName] = React.useState('')
  const [groupBusy, setGroupBusy] = React.useState(false)
  const [groupError, setGroupError] = React.useState<string | null>(null)

  const flatItems = React.useMemo(() => groups.flatMap((g) => g.items), [groups])
  const activeGroup = React.useMemo(() => groups.find((group) => group.group_name === activeGroupName), [activeGroupName, groups])
  const activeItems = activeGroup?.items ?? []

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
      const nextGroups = data.length > 0 ? data : [{ group_name: DEFAULT_GROUP_NAME, item_count: 0 }]
      setGroupOptions(nextGroups)
      if (!nextGroups.some((group) => group.group_name === activeGroupName)) {
        setActiveGroupName(nextGroups[0].group_name)
      }
    } catch {
      setGroupOptions([{ group_name: DEFAULT_GROUP_NAME, item_count: 0 }])
    }
  }, [activeGroupName])

  const refreshAll = React.useCallback(async () => {
    await Promise.all([loadWatchlist(), loadGroups()])
  }, [loadGroups, loadWatchlist])

  const searchStock = React.useCallback(async (keyword: string) => {
    const trimmed = keyword.trim()
    if (trimmed.length === 0) {
      setSearchResults([])
      return
    }
    setSearching(true)
    try {
      const results = await fetchJson<StocksApiResponse>(`/api/stocks?query=${encodeURIComponent(trimmed)}&page_size=30&exclude_delisted=true`)
      setSearchResults(results.items)
    } catch {
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }, [])

  const addStock = React.useCallback(async (stock: StockBasic) => {
    setAddingCode(stock.ts_code)
    setError(null)
    setNotice(null)
    try {
      await fetchJson('/api/watchlist', {
        method: 'POST',
        body: JSON.stringify({ ts_code: stock.ts_code, group_name: activeGroupName }),
      })
      setNotice(`已添加 ${stock.ts_code} ${stock.name} 到 ${activeGroupName}`)
      await refreshAll()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setAddingCode(null)
    }
  }, [activeGroupName, refreshAll])

  const removeStock = React.useCallback(async (id: number, tsCode: string) => {
    setError(null)
    setNotice(null)
    try {
      await fetchJson(`/api/watchlist/${id}`, { method: 'DELETE' })
      setNotice(`已移除 ${tsCode}`)
      await refreshAll()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }, [refreshAll])

  const createGroup = React.useCallback(async () => {
    const name = newGroupName.trim()
    if (!name) return
    setGroupBusy(true)
    setGroupError(null)
    try {
      await fetchJson('/api/watchlist/groups', {
        method: 'POST',
        body: JSON.stringify({ group_name: name }),
      })
      setNewGroupName('')
      setActiveGroupName(name)
      await refreshAll()
    } catch (caught) {
      setGroupError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setGroupBusy(false)
    }
  }, [newGroupName, refreshAll])

  const renameGroup = React.useCallback(async (oldName: string) => {
    const nextName = editingValue.trim()
    if (!nextName) return
    setGroupBusy(true)
    setGroupError(null)
    try {
      await fetchJson(`/api/watchlist/groups/${encodePath(oldName)}`, {
        method: 'PATCH',
        body: JSON.stringify({ group_name: nextName }),
      })
      if (activeGroupName === oldName) setActiveGroupName(nextName)
      setEditingGroupName(null)
      setEditingValue('')
      await refreshAll()
    } catch (caught) {
      setGroupError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setGroupBusy(false)
    }
  }, [activeGroupName, editingValue, refreshAll])

  const deleteGroup = React.useCallback(async (groupName: string) => {
    if (groupName === DEFAULT_GROUP_NAME) return
    const confirmed = window.confirm(`删除分组「${groupName}」？该组股票将移动到「${DEFAULT_GROUP_NAME}」。`)
    if (!confirmed) return
    setGroupBusy(true)
    setGroupError(null)
    try {
      await fetchJson(`/api/watchlist/groups/${encodePath(groupName)}`, { method: 'DELETE' })
      if (activeGroupName === groupName) setActiveGroupName(DEFAULT_GROUP_NAME)
      await refreshAll()
    } catch (caught) {
      setGroupError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setGroupBusy(false)
    }
  }, [activeGroupName, refreshAll])

  React.useEffect(() => { void refreshAll() }, [refreshAll])

  React.useEffect(() => {
    if (!searchOpen && !groupModalOpen) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setSearchOpen(false)
        setGroupModalOpen(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [groupModalOpen, searchOpen])

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
        <div className="flex items-center gap-3 border-b border-line px-4 pt-3">
          <div className="flex min-w-0 flex-1 items-end gap-5 overflow-x-auto">
            {groupOptions.map((group) => {
              const active = group.group_name === activeGroupName
              return (
                <button
                  key={group.group_name}
                  type="button"
                  onClick={() => setActiveGroupName(group.group_name)}
                  className={`relative h-11 shrink-0 text-base font-semibold transition ${active ? 'text-accent' : 'text-muted hover:text-ink'}`}
                >
                  {group.group_name}
                  {active && <span className="absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-accent" />}
                </button>
              )
            })}
          </div>
          <button
            type="button"
            onClick={() => setGroupModalOpen(true)}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md text-muted hover:bg-rowHover hover:text-ink"
            aria-label="分组管理"
          >
            <Settings2 className="h-5 w-5" />
          </button>
          <button
            type="button"
            onClick={() => setSearchOpen(true)}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md text-accent hover:bg-rowHover"
            aria-label="搜索并添加股票"
          >
            <Search className="h-6 w-6" />
          </button>
        </div>

        {loading ? (
          <div className="px-4 py-4"><Skeleton.Table rows={8} columns={7} /></div>
        ) : activeItems.length === 0 ? (
          <div className="px-4 py-16 text-center text-sm text-muted">
            当前分组暂无自选股
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-line text-left text-sm">
              <thead className="bg-tableHead text-xs font-semibold uppercase text-muted">
                <tr>
                  <th className="px-4 py-3">名称</th>
                  <th className="px-4 py-3">K线</th>
                  <th className="px-4 py-3">价格</th>
                  <th className="px-4 py-3">加入时间</th>
                  <th className="px-4 py-3">备注</th>
                  <th className="px-4 py-3">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {activeItems.map((stock) => (
                  <tr key={stock.id} className="hover:bg-rowHover">
                    <td className="whitespace-nowrap px-4 py-3">
                      <div className="font-semibold text-ink">{stock.name}</div>
                      <div className="font-mono text-xs text-muted">{stock.ts_code}</div>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3">
                      <WatchlistSparkline
                        latestClose={stock.latest_close ? Number(stock.latest_close) : null}
                        preClose={stock.pre_close ? Number(stock.pre_close) : null}
                        tsCode={stock.ts_code}
                      />
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-base tabular-nums">{stock.latest_close ?? '—'}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-muted">{new Date(stock.added_at).toLocaleDateString('zh-CN')}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-muted">{stock.note || '输入备注'}</td>
                    <td className="px-4 py-3">
                      <button onClick={() => void removeStock(stock.id, stock.ts_code)} className="text-sm font-semibold text-red-600 hover:underline">移除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="flex items-center justify-between border-t border-line px-4 py-3 text-sm text-muted">
          <span>{activeGroupName} · {formatNumber(activeItems.length)} 只</span>
          <span>全部自选 {formatNumber(flatItems.length)} 只</span>
        </div>
      </section>

      {groupModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4 py-6" role="dialog" aria-modal="true" aria-labelledby="watchlist-group-title" onMouseDown={() => setGroupModalOpen(false)}>
          <div className="w-full max-w-lg rounded-2xl border border-line bg-panel shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
            <div className="border-b border-line px-7 py-5 text-center">
              <h2 id="watchlist-group-title" className="text-xl font-bold text-ink">分组管理</h2>
            </div>

            <div className="px-7">
              {groupOptions.map((group) => {
                const editing = editingGroupName === group.group_name
                return (
                  <div key={group.group_name} className="flex min-h-16 items-center gap-4 border-b border-line">
                    <Menu className="h-5 w-5 shrink-0 text-muted" />
                    {editing ? (
                      <input
                        value={editingValue}
                        onChange={(event) => setEditingValue(event.target.value)}
                        className="h-10 min-w-0 flex-1 rounded-md border border-line bg-panel px-3 text-base font-semibold focus:outline-none focus:ring-2 focus:ring-accent"
                        autoFocus
                      />
                    ) : (
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-lg font-bold text-ink">{group.group_name}</div>
                        <div className="text-xs text-muted">{formatNumber(group.item_count)} 只</div>
                      </div>
                    )}
                    {editing ? (
                      <button type="button" onClick={() => void renameGroup(group.group_name)} disabled={groupBusy} className="flex h-10 w-10 items-center justify-center rounded-md text-accent hover:bg-rowHover disabled:opacity-50" aria-label="保存分组名称">
                        <Check className="h-5 w-5" />
                      </button>
                    ) : (
                      <button type="button" onClick={() => { setEditingGroupName(group.group_name); setEditingValue(group.group_name) }} className="flex h-10 w-10 items-center justify-center rounded-md text-orange-500 hover:bg-rowHover" aria-label="编辑分组">
                        <Pencil className="h-5 w-5" />
                      </button>
                    )}
                    <button type="button" onClick={() => void deleteGroup(group.group_name)} disabled={group.group_name === DEFAULT_GROUP_NAME || groupBusy} className="flex h-10 w-10 items-center justify-center rounded-md text-muted hover:bg-rowHover hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-35" aria-label="删除分组">
                      <Trash2 className="h-5 w-5" />
                    </button>
                  </div>
                )
              })}
            </div>

            {groupError && <div className="mx-7 mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900">{groupError}</div>}

            <div className="flex items-center gap-3 px-7 py-5">
              <input
                value={newGroupName}
                onChange={(event) => setNewGroupName(event.target.value)}
                placeholder="新建分组名称"
                className="h-11 min-w-0 flex-1 rounded-md border border-line bg-panel px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
              />
              <button type="button" onClick={() => void createGroup()} disabled={groupBusy || newGroupName.trim().length === 0} className="inline-flex h-11 items-center justify-center gap-2 rounded-md border border-accent px-4 text-sm font-bold text-accent hover:bg-rowHover disabled:cursor-not-allowed disabled:opacity-50">
                <Plus className="h-5 w-5" />
                新建分组
              </button>
            </div>
          </div>
        </div>
      )}

      {searchOpen && (
        <div className="fixed inset-0 z-50 bg-black/45" onMouseDown={() => setSearchOpen(false)}>
          <aside className="ml-auto flex h-full w-full max-w-md flex-col border-l border-line bg-panel p-6 shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
            <div className="relative">
              <input
                value={query}
                onChange={(event) => { setQuery(event.target.value); void searchStock(event.target.value) }}
                placeholder="输入股票代码或名称"
                className="h-14 w-full rounded-xl border-2 border-accent bg-panel pl-4 pr-12 text-lg font-semibold focus:outline-none"
                autoFocus
              />
              {query ? (
                <button type="button" onClick={() => { setQuery(''); setSearchResults([]) }} className="absolute right-3 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-full bg-line text-muted" aria-label="清空搜索">
                  <X className="h-4 w-4" />
                </button>
              ) : null}
            </div>

            <div className="mt-5 flex-1 overflow-y-auto">
              {searching ? (
                <div className="flex items-center justify-center py-12 text-muted">
                  <Loader2 className="h-6 w-6 animate-spin" />
                </div>
              ) : searchResults.length === 0 ? (
                <div className="py-12 text-center text-sm text-muted">输入关键词搜索股票</div>
              ) : (
                <div className="divide-y divide-line">
                  {searchResults.map((stock) => (
                    <div key={stock.ts_code} className="flex items-center gap-4 py-4">
                      <span className="shrink-0 rounded-lg border border-orange-500/40 bg-orange-500/10 px-3 py-1 text-sm font-bold text-orange-500">
                        {marketBadge(stock)}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-lg font-bold text-ink">{stock.name}</div>
                        <div className="font-mono text-sm text-muted">{stock.ts_code}</div>
                      </div>
                      <button type="button" onClick={() => void addStock(stock)} disabled={addingCode === stock.ts_code} className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border-2 border-red-500 text-orange-500 hover:bg-red-500/10 disabled:opacity-50" aria-label={`添加 ${stock.name}`}>
                        {addingCode === stock.ts_code ? <Loader2 className="h-5 w-5 animate-spin" /> : <Plus className="h-7 w-7" />}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </aside>
        </div>
      )}
    </>
  )
}
