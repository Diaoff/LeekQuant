import React from 'react'
import { AlertTriangle, Filter, Loader2, Plus, RefreshCw, Search, SlidersHorizontal, Trash2, X, Zap } from 'lucide-react'
import { fetchJson, formatDate, formatDateTime, formatNumber } from '../lib/utils'
import Skeleton from '../components/Skeleton'

interface SignalLog {
  id: number
  strategy_name: string | null
  ts_code: string
  stock_name: string | null
  trade_date: string
  signal_type: string
  target_position: string
  current_position: string
  action: string | null
  confidence: string | null
  reason: string | null
  snapshot: Record<string, unknown> | null
  created_at: string
}

interface SignalResponse {
  items: SignalLog[]
  page: number
  page_size: number
  total: number
  summary: SignalSummary
}

interface SignalSummary {
  buy_count: number
  add_count: number
  reduce_count: number
  sell_count: number
  hold_count: number
  blocked_count: number
}

interface WatchlistGroupOption {
  group_name: string
  item_count: number
}

interface WatchlistBatchResponse {
  group_name: string
  added_count: number
  skipped_count: number
  items: { ts_code: string }[]
  errors: { ts_code: string; error: string }[]
}

const signalTypes = ['', '买入', '增持', '减仓', '卖出', '观望']
const DEFAULT_GROUP_NAME = '默认'

function signalTone(type: string) {
  if (type === '买入' || type === '增持') return 'bg-red-50 text-red-700 border-red-200'
  if (type === '减仓' || type === '卖出') return 'bg-emerald-50 text-emerald-700 border-emerald-200'
  return 'bg-slate-100 text-slate-700 border-slate-200'
}

function actionTone(action: string | null) {
  if (action === 'BLOCKED') return 'text-warn'
  if (action?.startsWith('SELL')) return 'text-emerald-600'
  if (action === 'BUY') return 'text-red-600'
  return 'text-muted'
}

export default function SignalsPage() {
  const [signals, setSignals] = React.useState<SignalLog[]>([])
  const [total, setTotal] = React.useState(0)
  const [summary, setSummary] = React.useState<SignalSummary>({
    buy_count: 0,
    add_count: 0,
    reduce_count: 0,
    sell_count: 0,
    hold_count: 0,
    blocked_count: 0,
  })
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [selected, setSelected] = React.useState<SignalLog | null>(null)
  const [triggering, setTriggering] = React.useState(false)
  const [triggerMsg, setTriggerMsg] = React.useState<string | null>(null)
  const [clearing, setClearing] = React.useState(false)
  const [selectedTsCodes, setSelectedTsCodes] = React.useState<Set<string>>(() => new Set())
  const [watchlistGroups, setWatchlistGroups] = React.useState<WatchlistGroupOption[]>([
    { group_name: DEFAULT_GROUP_NAME, item_count: 0 },
  ])
  const [watchlistModalOpen, setWatchlistModalOpen] = React.useState(false)
  const [watchlistGroupName, setWatchlistGroupName] = React.useState(DEFAULT_GROUP_NAME)
  const [newWatchlistGroupName, setNewWatchlistGroupName] = React.useState('')
  const [watchlistBusy, setWatchlistBusy] = React.useState(false)
  const [watchlistMessage, setWatchlistMessage] = React.useState<string | null>(null)
  const [watchlistError, setWatchlistError] = React.useState<string | null>(null)

  const [strategies, setStrategies] = React.useState<{ id: number; name: string }[]>([])

  React.useEffect(() => {
    void fetchJson<{ id: number; name: string }[]>('/api/strategies').then(setStrategies).catch(() => {})
  }, [])

  const [filters, setFilters] = React.useState({
    ts_code: '',
    signal_type: '',
    start_date: '',
    end_date: '',
    strategy_id: '',
  })

  const loadGroups = React.useCallback(async () => {
    try {
      const data = await fetchJson<WatchlistGroupOption[]>('/api/watchlist/groups')
      const nextGroups = data.length > 0 ? data : [{ group_name: DEFAULT_GROUP_NAME, item_count: 0 }]
      setWatchlistGroups(nextGroups)
      setWatchlistGroupName((current) => nextGroups.some((group) => group.group_name === current) ? current : DEFAULT_GROUP_NAME)
    } catch {
      setWatchlistGroups([{ group_name: DEFAULT_GROUP_NAME, item_count: 0 }])
      setWatchlistGroupName(DEFAULT_GROUP_NAME)
    }
  }, [])

  const visibleTsCodes = React.useMemo(() => {
    const codes: string[] = []
    const seen = new Set<string>()
    signals.forEach((signal) => {
      if (!seen.has(signal.ts_code)) {
        seen.add(signal.ts_code)
        codes.push(signal.ts_code)
      }
    })
    return codes
  }, [signals])

  const selectedCount = selectedTsCodes.size
  const allVisibleSelected = visibleTsCodes.length > 0 && visibleTsCodes.every((code) => selectedTsCodes.has(code))

  const loadSignals = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams({ page_size: '80' })
    Object.entries(filters).forEach(([key, value]) => {
      if (value.trim()) params.set(key, value.trim())
    })
    try {
      const data = await fetchJson<SignalResponse>(`/api/signals?${params.toString()}`)
      setSignals(data.items)
      setTotal(data.total)
      setSummary(data.summary)
      setSelected((current) => data.items.find((item) => item.id === current?.id) ?? data.items[0] ?? null)
      const visibleCodes = new Set(data.items.map((item) => item.ts_code))
      setSelectedTsCodes((current) => new Set([...current].filter((code) => visibleCodes.has(code))))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [filters])

  const triggerSignals = React.useCallback(async () => {
    setTriggering(true)
    setTriggerMsg(null)
    try {
      await fetchJson('/api/signals/trigger', { method: 'POST' })
      setTriggerMsg('信号生成已提交')
      setTimeout(() => void loadSignals(), 500)
    } catch (caught) {
      setTriggerMsg(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setTriggering(false)
    }
  }, [loadSignals])

  const clearSignals = React.useCallback(async () => {
    if (!window.confirm('确定清空所有信号？此操作不可撤销。')) return
    setClearing(true)
    try {
      await fetchJson('/api/signals/clear', { method: 'DELETE' })
      void loadSignals()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setClearing(false)
    }
  }, [loadSignals])

  React.useEffect(() => {
    void loadSignals()
  }, [loadSignals])

  React.useEffect(() => {
    void loadGroups()
  }, [loadGroups])

  const toggleTsCode = React.useCallback((tsCode: string) => {
    setSelectedTsCodes((current) => {
      const next = new Set(current)
      if (next.has(tsCode)) next.delete(tsCode)
      else next.add(tsCode)
      return next
    })
  }, [])

  const toggleVisibleTsCodes = React.useCallback(() => {
    setSelectedTsCodes((current) => {
      const next = new Set(current)
      if (visibleTsCodes.length > 0 && visibleTsCodes.every((code) => next.has(code))) {
        visibleTsCodes.forEach((code) => next.delete(code))
      } else {
        visibleTsCodes.forEach((code) => next.add(code))
      }
      return next
    })
  }, [visibleTsCodes])

  const openWatchlistModal = React.useCallback(() => {
    if (selectedCount === 0) return
    setWatchlistMessage(null)
    setWatchlistError(null)
    setNewWatchlistGroupName('')
    setWatchlistModalOpen(true)
  }, [selectedCount])

  const closeWatchlistModal = React.useCallback(() => {
    if (watchlistBusy) return
    setWatchlistModalOpen(false)
    setWatchlistError(null)
  }, [watchlistBusy])

  const addSelectedToWatchlist = React.useCallback(async () => {
    const groupName = newWatchlistGroupName.trim() || watchlistGroupName.trim()
    if (!groupName || selectedTsCodes.size === 0) return
    setWatchlistBusy(true)
    setWatchlistError(null)
    setWatchlistMessage(null)
    try {
      const result = await fetchJson<WatchlistBatchResponse>('/api/watchlist/batch', {
        method: 'POST',
        body: JSON.stringify({ ts_codes: [...selectedTsCodes], group_name: groupName }),
      })
      const skippedText = result.skipped_count > 0 ? `，${formatNumber(result.skipped_count)} 只未加入` : ''
      setWatchlistMessage(`已加入 ${formatNumber(result.added_count)} 只股票到 ${result.group_name}${skippedText}`)
      setSelectedTsCodes(new Set())
      setWatchlistModalOpen(false)
      setNewWatchlistGroupName('')
      setWatchlistGroupName(result.group_name)
      await loadGroups()
    } catch (caught) {
      setWatchlistError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setWatchlistBusy(false)
    }
  }, [loadGroups, newWatchlistGroupName, selectedTsCodes, watchlistGroupName])

  return (
    <div className="space-y-5">
      <section className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">信号中心</h1>
          <p className="mt-1 text-sm text-muted">五档信号、动作结果、阻塞原因和策略快照。</p>
        </div>
        <div className="flex items-center gap-2">
          {triggerMsg && (
            <span className={`text-sm ${triggerMsg === '信号生成已提交' ? 'text-emerald-600' : 'text-red-600'}`}>
              {triggerMsg}
            </span>
          )}
          <button
            type="button"
            onClick={() => void triggerSignals()}
            disabled={triggering}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 text-sm text-amber-800 hover:bg-amber-100 disabled:opacity-50"
          >
            <Zap className={`h-4 w-4 ${triggering ? 'animate-spin' : ''}`} />
            {triggering ? '生成中...' : '生成信号'}
          </button>
          <button
            type="button"
            onClick={() => void clearSignals()}
            disabled={clearing}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-red-200 bg-red-50 px-3 text-sm text-red-700 hover:bg-red-100 disabled:opacity-50"
          >
            <Trash2 className={`h-4 w-4 ${clearing ? 'animate-pulse' : ''}`} />
            {clearing ? '清空中...' : '清空'}
          </button>
          <button
            type="button"
            onClick={() => void loadSignals()}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-line bg-panel px-3 text-sm text-ink hover:bg-rowHover"
          >
            <RefreshCw className="h-4 w-4" />
            刷新
          </button>
        </div>
      </section>

      <section className="rounded-lg border border-line bg-panel p-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-medium text-ink">
          <SlidersHorizontal className="h-4 w-4 text-muted" />
          筛选
        </div>
        <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-5">
          <label className="space-y-1 text-xs text-muted">
            股票
            <div className="flex h-9 items-center gap-2 rounded-md border border-line bg-surface px-2">
              <Search className="h-4 w-4" />
              <input
                value={filters.ts_code}
                onChange={(event) => setFilters((prev) => ({ ...prev, ts_code: event.target.value.toUpperCase() }))}
                placeholder="000001.SZ"
                className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none"
              />
            </div>
          </label>
          <label className="space-y-1 text-xs text-muted">
            信号
            <select
              value={filters.signal_type}
              onChange={(event) => setFilters((prev) => ({ ...prev, signal_type: event.target.value }))}
              className="h-9 w-full rounded-md border border-line bg-surface px-2 text-sm text-ink outline-none"
            >
              {signalTypes.map((type) => (
                <option key={type || 'all'} value={type}>{type || '全部'}</option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-xs text-muted">
            开始日期
            <input
              type="date"
              value={filters.start_date}
              onChange={(event) => setFilters((prev) => ({ ...prev, start_date: event.target.value }))}
              className="h-9 w-full rounded-md border border-line bg-surface px-2 text-sm text-ink outline-none"
            />
          </label>
          <label className="space-y-1 text-xs text-muted">
            结束日期
            <input
              type="date"
              value={filters.end_date}
              onChange={(event) => setFilters((prev) => ({ ...prev, end_date: event.target.value }))}
              className="h-9 w-full rounded-md border border-line bg-surface px-2 text-sm text-ink outline-none"
            />
          </label>
          <label className="space-y-1 text-xs text-muted">
            策略
            <select
              value={filters.strategy_id}
              onChange={(event) => setFilters((prev) => ({ ...prev, strategy_id: event.target.value }))}
              className="h-9 w-full rounded-md border border-line bg-surface px-2 text-sm text-ink outline-none"
            >
              <option value="">全部策略</option>
              {strategies.map((s) => (
                <option key={s.id} value={String(s.id)}>{s.name}</option>
              ))}
            </select>
          </label>
        </div>
      </section>

      {error && (
        <section className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4" />
            <span>{error}</span>
          </div>
        </section>
      )}

      {watchlistMessage && (
        <section className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
          {watchlistMessage}
        </section>
      )}

      <section className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
        {[
          { label: '买入', value: summary.buy_count, tone: 'text-red-600' },
          { label: '增持', value: summary.add_count, tone: 'text-red-600' },
          { label: '减仓', value: summary.reduce_count, tone: 'text-emerald-600' },
          { label: '卖出', value: summary.sell_count, tone: 'text-emerald-600' },
          { label: '观望', value: summary.hold_count, tone: 'text-muted' },
          { label: 'BLOCKED', value: summary.blocked_count, tone: 'text-warn' },
        ].map((item) => (
          <div key={item.label} className="rounded-lg border border-line bg-panel p-4">
            <div className="text-xs text-muted">{item.label}</div>
            <div className={`mt-2 text-xl font-semibold ${item.tone}`}>{formatNumber(item.value)}</div>
          </div>
        ))}
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="overflow-hidden rounded-lg border border-line bg-panel">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Filter className="h-4 w-4 text-muted" />
                信号列表
              </div>
              {selectedCount > 0 && (
                <span className="rounded-md bg-accent/10 px-2 py-1 text-xs font-medium text-accent">
                  已选 {formatNumber(selectedCount)} 只股票
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted">{formatNumber(total)} 条</span>
              <button
                type="button"
                onClick={openWatchlistModal}
                disabled={selectedCount === 0}
                className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-accent px-3 text-sm font-semibold text-accent hover:bg-rowHover disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Plus className="h-4 w-4" />
                加入自选
              </button>
            </div>
          </div>
          {loading ? (
            <div className="p-4"><Skeleton.Table rows={8} columns={9} /></div>
          ) : signals.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted">暂无信号</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-[1060px] w-full text-left text-sm">
                <thead className="bg-tableHead text-xs text-muted">
                  <tr>
                    <th className="w-12 px-4 py-3 font-medium">
                      <input
                        type="checkbox"
                        checked={allVisibleSelected}
                        onChange={toggleVisibleTsCodes}
                        aria-label="选择当前页全部股票"
                        className="h-4 w-4 rounded border-line accent-accent"
                      />
                    </th>
                    <th className="px-4 py-3 font-medium">日期</th>
                    <th className="px-4 py-3 font-medium">股票</th>
                    <th className="px-4 py-3 font-medium">信号</th>
                    <th className="px-4 py-3 font-medium">动作</th>
                    <th className="px-4 py-3 font-medium">目标仓位</th>
                    <th className="px-4 py-3 font-medium">置信度</th>
                    <th className="px-4 py-3 font-medium">策略</th>
                    <th className="px-4 py-3 font-medium">原因</th>
                  </tr>
                </thead>
                <tbody>
                  {signals.map((signal) => (
                    <tr
                      key={signal.id}
                      onClick={() => setSelected(signal)}
                      className={`cursor-pointer border-t border-line hover:bg-rowHover ${selected?.id === signal.id ? 'bg-rowAlt' : ''}`}
                    >
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          checked={selectedTsCodes.has(signal.ts_code)}
                          onChange={() => toggleTsCode(signal.ts_code)}
                          onClick={(event) => event.stopPropagation()}
                          aria-label={`选择 ${signal.ts_code}`}
                          className="h-4 w-4 rounded border-line accent-accent"
                        />
                      </td>
                      <td className="px-4 py-3 text-muted">{formatDate(signal.trade_date)}</td>
                      <td className="px-4 py-3">
                        <div className="font-medium text-ink">{signal.ts_code}</div>
                        <div className="text-xs text-muted">{signal.stock_name ?? '暂无名称'}</div>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex rounded-md border px-2 py-1 text-xs ${signalTone(signal.signal_type)}`}>
                          {signal.signal_type}
                        </span>
                      </td>
                      <td className={`px-4 py-3 font-medium ${actionTone(signal.action)}`}>{signal.action ?? '暂无'}</td>
                      <td className="px-4 py-3 text-muted">{formatNumber(Number(signal.target_position) * 100, 2)}%</td>
                      <td className="px-4 py-3 text-muted">{signal.confidence != null ? formatNumber(Number(signal.confidence) * 100, 2) + '%' : '暂无'}</td>
                      <td className="px-4 py-3 text-muted">
                        <div>{signal.strategy_name ?? '未绑定策略'}</div>
                      </td>
                      <td className="max-w-[260px] truncate px-4 py-3 text-muted">{signal.reason ?? String(signal.snapshot?.blocked_reason ?? '')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <aside className="rounded-lg border border-line bg-panel p-4">
          <h2 className="text-sm font-semibold text-ink">快照详情</h2>
          {selected ? (
            <div className="mt-4 space-y-4 text-sm">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="text-xs text-muted">创建时间</div>
                  <div className="mt-1 text-ink">{formatDateTime(selected.created_at)}</div>
                </div>
                <div>
                  <div className="text-xs text-muted">置信度</div>
                  <div className="mt-1 text-ink">{selected.confidence ? `${formatNumber(Number(selected.confidence) * 100, 2)}%` : '暂无'}</div>
                </div>
              </div>
              <div>
                <div className="text-xs text-muted">说明</div>
                <div className="mt-1 rounded-md bg-surface p-3 text-ink">{selected.reason || '暂无'}</div>
              </div>
              <pre className="max-h-[420px] overflow-auto rounded-md bg-surface p-3 text-xs leading-5 text-ink">
                {JSON.stringify(selected.snapshot ?? {}, null, 2)}
              </pre>
            </div>
          ) : (
            <div className="mt-8 text-center text-sm text-muted">选择一条信号查看快照</div>
          )}
        </aside>
      </section>

      {watchlistModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4 py-6" role="dialog" aria-modal="true" aria-labelledby="signals-watchlist-title" onMouseDown={closeWatchlistModal}>
          <div className="w-full max-w-md rounded-lg border border-line bg-panel shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
            <div className="flex items-start justify-between gap-4 border-b border-line px-6 py-5">
              <div className="min-w-0">
                <h2 id="signals-watchlist-title" className="text-lg font-bold text-ink">加入自选</h2>
                <p className="mt-1 text-sm text-muted">已选择 {formatNumber(selectedCount)} 只股票</p>
              </div>
              <button
                type="button"
                onClick={closeWatchlistModal}
                disabled={watchlistBusy}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-muted hover:bg-rowHover hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="关闭"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="space-y-4 px-6 py-5">
              <label className="block space-y-1 text-sm text-muted">
                目标分组
                <select
                  value={watchlistGroupName}
                  onChange={(event) => setWatchlistGroupName(event.target.value)}
                  disabled={watchlistBusy || newWatchlistGroupName.trim().length > 0}
                  className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:ring-2 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {watchlistGroups.map((group) => (
                    <option key={group.group_name} value={group.group_name}>
                      {group.group_name}（{formatNumber(group.item_count)}）
                    </option>
                  ))}
                </select>
              </label>

              <label className="block space-y-1 text-sm text-muted">
                新建分组
                <input
                  value={newWatchlistGroupName}
                  onChange={(event) => setNewWatchlistGroupName(event.target.value)}
                  disabled={watchlistBusy}
                  placeholder="输入后优先加入新分组"
                  className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:ring-2 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-50"
                />
              </label>

              {watchlistError && (
                <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900">
                  {watchlistError}
                </div>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-line px-6 py-4">
              <button
                type="button"
                onClick={closeWatchlistModal}
                disabled={watchlistBusy}
                className="h-9 rounded-md border border-line px-3 text-sm font-semibold text-ink hover:bg-rowHover disabled:cursor-not-allowed disabled:opacity-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void addSelectedToWatchlist()}
                disabled={watchlistBusy || selectedCount === 0 || !(newWatchlistGroupName.trim() || watchlistGroupName.trim())}
                className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {watchlistBusy && <Loader2 className="h-4 w-4 animate-spin" />}
                {watchlistBusy ? '加入中...' : '确认加入'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
