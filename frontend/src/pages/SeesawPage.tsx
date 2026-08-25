import React, { useEffect, useState, useCallback } from 'react'
import {
  AlertTriangle,
  Minus,
  Clock,
  ArrowDown,
  ArrowUp,
  ChevronRight,

  Loader2,
  Plus,
  Search,
  Settings,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  X,
} from 'lucide-react'
import { fetchJson, formatNumber } from '../lib/utils'
import Skeleton from '../components/Skeleton'

// ── Types ─────────────────────────────────────────────────────────────────────

interface PoolItem {
  id: number
  ts_code: string
  name: string
  note: string | null
  tags: string | null
  sort_order: number
  enabled: boolean
}

interface MarketState {
  index_code: string
  state: 'up' | 'neutral' | 'down'
  detail: Record<string, unknown>
  rules: Record<string, unknown>
}

interface Recommendation {
  ts_code: string
  name: string
  score: number
  beta: number | null
  dividend_yield: number | null
  pe_ttm: number | null
  reason: string
}

interface TriggerLog {
  id: number
  trigger_time: string
  market_state: string
  index_code: string
  recommended_count: number
  recommendations: Recommendation[]
  subsequent_perf: unknown
}

interface RulesConfig {
  index_code: string
  ma_short: number
  ma_long: number
  ma_long2: number
  drop_threshold: number
  high_window: number
  high_drop_pct: number
  ma_cross_enabled: boolean
  enabled: boolean
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function stateBadge(state: string) {
  const map: Record<string, { cls: string; label: string; icon: React.ReactNode }> = {
    up: { cls: 'bg-emerald-50 border-emerald-200 text-emerald-700', label: '强势', icon: <TrendingUp className="h-4 w-4" /> },
    neutral: { cls: 'bg-amber-50 border-amber-200 text-amber-700', label: '震荡', icon: <Minus className="h-4 w-4" /> },
    down: { cls: 'bg-red-50 border-red-200 text-red-700', label: '弱势', icon: <TrendingDown className="h-4 w-4" /> },
  }
  const s = map[state] ?? map.neutral
  return <span className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm font-semibold ${s.cls}`}>{s.icon}{s.label}</span>
}

function pct(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`
}

function num(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined) return '—'
  return value.toFixed(digits)
}

// ── Components ────────────────────────────────────────────────────────────────

function MarketStateCard({ state, loading, onRefresh }: { state: MarketState | null; loading: boolean; onRefresh: () => void }) {
  if (loading) return <Skeleton.Card />
  return (
    <section className={`rounded-lg border p-5 shadow-sm ${state?.state === 'down' ? 'border-red-200 bg-red-50/50' : state?.state === 'up' ? 'border-emerald-200 bg-emerald-50/50' : 'border-line bg-panel'}`}>
      <div className="mb-4 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-base font-semibold text-ink">
          <TrendingUp className="h-5 w-5 text-accent" />大盘状态
        </h3>
        <button onClick={onRefresh} className="rounded-md border border-line px-3 py-1.5 text-sm text-muted hover:bg-rowHover">刷新</button>
      </div>
      <div className="flex items-center gap-6">
        <div className="text-3xl font-bold text-ink">{state?.index_code ?? '—'}</div>
        {state && stateBadge(state.state)}
      </div>
      {state && (() => {
        const d = state.detail as Record<string, unknown>
        const r = state.rules as Record<string, unknown>
        const closeVal = typeof d.close === 'number' ? d.close : undefined
        const changeVal = typeof d.change_pct === 'number' ? d.change_pct : undefined
        const gapVal = typeof d.ma20_gap === 'number' ? d.ma20_gap : undefined
        const dropVal = typeof d.drop_from_high === 'number' ? d.drop_from_high : undefined
        const maLong = typeof r.ma_long === 'number' ? r.ma_long : 20
        const highWindow = typeof r.high_window === 'number' ? r.high_window : 20
        return (
        <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
          <div className="rounded-md border border-line bg-panel p-3">
            <div className="text-muted">收盘价</div>
            <div className="mt-1 font-mono text-lg font-semibold text-ink">{closeVal !== undefined ? num(closeVal) : '—'}</div>
          </div>
          <div className="rounded-md border border-line bg-panel p-3">
            <div className="text-muted">当日涨跌</div>
            <div className={`mt-1 font-mono text-lg font-semibold ${(changeVal ?? 0) > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
              {changeVal !== undefined ? pct(changeVal) : '—'}
            </div>
          </div>
          <div className="rounded-md border border-line bg-panel p-3">
            <div className="text-muted">距MA{maLong}偏离</div>
            <div className={`mt-1 font-mono text-lg font-semibold ${(gapVal ?? 0) > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
              {gapVal !== undefined ? pct(gapVal) : '—'}
            </div>
          </div>
          <div className="rounded-md border border-line bg-panel p-3">
            <div className="text-muted">距{highWindow}日高点</div>
            <div className={`mt-1 font-mono text-lg font-semibold ${(dropVal ?? 0) < 0 ? 'text-emerald-600' : 'text-red-600'}`}>
              {dropVal !== undefined ? pct(dropVal) : '—'}
            </div>
          </div>
        </div>
        )
      })()}
      {state?.state === 'down' && (() => {
        const conds = state.detail?.down_conditions
        if (!Array.isArray(conds)) return null
        return (
        <div className="mt-3 flex flex-wrap gap-2">
          {conds.map((c) => (
            <span key={c} className="rounded-full border border-red-200 bg-red-100 px-2.5 py-0.5 text-xs font-medium text-red-700">{c}</span>
          ))}
        </div>
        )
      })()}
    </section>
  )
}

function PoolSection({
  items, loading, onAdd, onRemove, onToggle,
}: {
  items: PoolItem[]
  loading: boolean
  onAdd: (code: string, name: string, note?: string, tags?: string) => void
  onRemove: (id: number) => void
  onToggle: (id: number, enabled: boolean) => void
}) {
  const [adding, setAdding] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<Record<string,string>[]>([])
  const [searching, setSearching] = useState(false)
  const [newCode, setNewCode] = useState('')
  const [newName, setNewName] = useState('')
  const [newNote, setNewNote] = useState('')
  const [newTags, setNewTags] = useState('')
  const [addingBusy, setAddingBusy] = useState(false)

  const searchStock = useCallback(async (q: string) => {
    if (!q.trim()) { setSearchResults([]); return }
    setSearching(true)
    try {
      const data = await fetchJson<{items: Array<{ts_code: string; name: string; industry?: string}>}>(
        `/api/stocks?query=${encodeURIComponent(q)}&page_size=20&exclude_delisted=true`
      )
      setSearchResults(data.items)
    } catch {
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }, [])

  const doAdd = useCallback(async () => {
    if (!newCode.trim()) return
    setAddingBusy(true)
    try {
      await onAdd(newCode.trim(), newName.trim() || newCode.trim(), newNote.trim() || undefined, newTags.trim() || undefined)
      setNewCode(''); setNewName(''); setNewNote(''); setNewTags(''); setAdding(false)
    } finally {
      setAddingBusy(false)
    }
  }, [newCode, newName, newNote, newTags, onAdd])

  return (
    <section className="rounded-lg border border-line bg-panel shadow-sm">
      <div className="flex items-center justify-between border-b border-line px-5 py-4">
        <h3 className="flex items-center gap-2 text-base font-semibold text-ink">
          <ShieldCheck className="h-5 w-5 text-accent" />避险股票池
        </h3>
        <button
          onClick={() => setAdding(!adding)}
          className="flex h-9 items-center gap-1.5 rounded-md border border-accent px-3 text-sm font-semibold text-accent hover:bg-accent/10"
        >
          <Plus className="h-4 w-4" />{adding ? '关闭' : '添加'}
        </button>
      </div>

      {adding && (
        <div className="border-b border-line bg-surface px-5 py-4">
          <div className="grid grid-cols-2 gap-3">
            <input placeholder="股票代码（如 601398.SH）" value={newCode} onChange={e => setNewCode(e.target.value)}
              className="h-10 rounded-md border border-line bg-panel px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent" />
            <input placeholder="名称（可留空自动填充）" value={newName} onChange={e => setNewName(e.target.value)}
              className="h-10 rounded-md border border-line bg-panel px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent" />
            <input placeholder="备注" value={newNote} onChange={e => setNewNote(e.target.value)}
              className="h-10 rounded-md border border-line bg-panel px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent" />
            <input placeholder="标签（逗号分隔，如 银行,低估值）" value={newTags} onChange={e => setNewTags(e.target.value)}
              className="h-10 rounded-md border border-line bg-panel px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent" />
          </div>
          <div className="mt-3 flex gap-2">
            <button onClick={doAdd} disabled={addingBusy || !newCode.trim()}
              className="rounded-md bg-accent px-4 py-1.5 text-sm font-semibold text-white hover:bg-accent/90 disabled:opacity-50">
              {addingBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : '确认添加'}
            </button>
            <button onClick={() => setAdding(false)} className="rounded-md border border-line px-4 py-1.5 text-sm text-muted hover:bg-rowHover">取消</button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="px-5 py-4"><Skeleton.Table rows={5} columns={6} /></div>
      ) : items.length === 0 ? (
        <div className="px-5 py-12 text-center text-sm text-muted">池内暂无股票，点击右上角「添加」加入</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-line text-left text-sm">
            <thead className="bg-tableHead text-xs font-semibold uppercase text-muted">
              <tr>
                <th className="px-4 py-3">代码</th>
                <th className="px-4 py-3">名称</th>
                <th className="px-4 py-3">标签</th>
                <th className="px-4 py-3">备注</th>
                <th className="px-4 py-3">状态</th>
                <th className="px-4 py-3 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {items.map((item) => (
                <tr key={item.id} className="hover:bg-rowHover">
                  <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-muted">{item.ts_code}</td>
                  <td className="px-4 py-3 font-semibold text-ink">{item.name}</td>
                  <td className="px-4 py-3">
                    {item.tags ? item.tags.split(',').map(t => (
                      <span key={t} className="mr-1 inline-block rounded-full border border-line bg-surface px-2 py-0.5 text-xs text-muted">{t.trim()}</span>
                    )) : <span className="text-muted">—</span>}
                  </td>
                  <td className="px-4 py-3 text-muted max-w-[200px] truncate">{item.note ?? '—'}</td>
                  <td className="px-4 py-3">
                    <button onClick={() => onToggle(item.id, !item.enabled)}
                      className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold transition ${item.enabled ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-line bg-surface text-muted'}`}>
                      {item.enabled ? '启用' : '禁用'}
                    </button>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button onClick={() => onRemove(item.id)} className="text-sm font-semibold text-red-600 hover:underline">移除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="border-t border-line px-5 py-3 text-sm text-muted">
        共 {items.length} 只 · 点击「启用/禁用」可快速切换
      </div>
    </section>
  )
}

function RecommendSection({ recs, state, onRefresh }: { recs: Recommendation[]; state: string; onRefresh: () => void }) {
  return (
    <section className="rounded-lg border border-line bg-panel shadow-sm">
      <div className="flex items-center justify-between border-b border-line px-5 py-4">
        <h3 className="flex items-center gap-2 text-base font-semibold text-ink">
          <ArrowDown className="h-5 w-5 text-red-500" />高切低推荐
        </h3>
        <span className="text-xs text-muted">{state === 'down' ? '大盘弱势，建议切换至防守仓位' : '大盘非弱势，暂无推荐'}</span>
      </div>
      {recs.length === 0 ? (
        <div className="px-5 py-12 text-center text-sm text-muted">
          {state !== 'down' ? '当前市场状态为非弱势，无需切换' : '避险库暂无启用标的，请先在上方添加并启用'}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-line text-left text-sm">
            <thead className="bg-tableHead text-xs font-semibold uppercase text-muted">
              <tr>
                <th className="px-4 py-3">代码</th>
                <th className="px-4 py-3">名称</th>
                <th className="px-4 py-3">配置方式</th>
                <th className="px-4 py-3">说明</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {recs.map((r) => (
                <tr key={r.ts_code} className="hover:bg-rowHover">
                  <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-muted">{r.ts_code}</td>
                  <td className="px-4 py-3 font-semibold text-ink">{r.name}</td>
                  <td className="px-4 py-3">
                    <span className="inline-block rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-700">等权·人工排序</span>
                  </td>
                  <td className="px-4 py-3 text-muted text-xs">{r.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function TriggerHistory({ triggers, loading, onRefresh }: { triggers: TriggerLog[]; loading: boolean; onRefresh: () => void }) {
  const [expandedId, setExpandedId] = useState<number | null>(null)
  return (
    <section className="rounded-lg border border-line bg-panel shadow-sm">
      <div className="flex items-center justify-between border-b border-line px-5 py-4">
        <h3 className="flex items-center gap-2 text-base font-semibold text-ink">
          <Clock className="h-5 w-5 text-accent" />触发历史
        </h3>
        <button onClick={onRefresh} className="rounded-md border border-line px-3 py-1.5 text-sm text-muted hover:bg-rowHover">刷新</button>
      </div>
      {loading ? (
        <div className="px-5 py-4"><Skeleton.Table rows={3} columns={3} /></div>
      ) : triggers.length === 0 ? (
        <div className="px-5 py-12 text-center text-sm text-muted">暂无触发记录</div>
      ) : (
        <div className="divide-y divide-line">
          {triggers.map((t) => (
            <div key={t.id}>
              <button onClick={() => setExpandedId(expandedId === t.id ? null : t.id)}
                className="flex w-full items-center gap-4 px-5 py-3 text-left hover:bg-rowHover">
                <span className="text-xs text-muted">{new Date(t.trigger_time).toLocaleString('zh-CN')}</span>
                {stateBadge(t.market_state)}
                <span className="text-xs text-muted">{t.index_code}</span>
                <span className="text-sm text-muted">{t.recommended_count} 只推荐</span>
                <ChevronRight className={`ml-auto h-4 w-4 text-muted transition-transform ${expandedId === t.id ? 'rotate-90' : ''}`} />
              </button>
              {expandedId === t.id && t.recommendations && (
                <div className="bg-surface px-5 py-3">
                  <table className="min-w-full text-left text-sm">
                    <thead className="text-xs font-semibold uppercase text-muted">
                      <tr>
                        <th className="px-3 py-2">代码</th>
                        <th className="px-3 py-2">名称</th>
                        <th className="px-3 py-2">原因</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {t.recommendations.map((r) => (
                        <tr key={r.ts_code}>
                          <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-muted">{r.ts_code}</td>
                          <td className="px-3 py-2 font-semibold">{r.name}</td>
                          <td className="px-3 py-2 text-muted text-xs">{r.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function SeesawPage() {
  const [poolItems, setPoolItems] = useState<PoolItem[]>([])
  const [poolLoading, setPoolLoading] = useState(true)
  const [showDisabled, setShowDisabled] = useState(true)
  const [marketState, setMarketState] = useState<MarketState | null>(null)
  const [marketLoading, setMarketLoading] = useState(true)
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [triggers, setTriggers] = useState<TriggerLog[]>([])
  const [triggersLoading, setTriggersLoading] = useState(true)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadPool = useCallback(async () => {
    setPoolLoading(true)
    try {
      const only = showDisabled ? 'false' : 'true'
      const data = await fetchJson<{items: PoolItem[]; total: number; page: number; page_size: number}>(`/api/seesaw/pool?enabled_only=${only}&page_size=200`)
      setPoolItems(data.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setPoolLoading(false)
    }
  }, [showDisabled])

  const loadMarket = useCallback(async () => {
    setMarketLoading(true)
    try {
      const data = await fetchJson<MarketState>('/api/seesaw/market-state')
      setMarketState(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setMarketLoading(false)
    }
  }, [])

  const loadRecommend = useCallback(async () => {
    try {
      const data = await fetchJson<{ market_state: string; recommendations: Recommendation[] }>('/api/seesaw/recommend')
      setRecommendations(data.recommendations)
    } catch { /* silent */ }
  }, [])

  const loadTriggers = useCallback(async () => {
    setTriggersLoading(true)
    try {
      const data = await fetchJson<{items: TriggerLog[]; total: number; page: number; page_size: number}>('/api/seesaw/triggers?page_size=20')
      setTriggers(data.items)
    } catch {
      setTriggers([])
    } finally {
      setTriggersLoading(false)
    }
  }, [])

  useEffect(() => { void Promise.all([loadPool(), loadMarket(), loadRecommend(), loadTriggers()]) }, [loadPool, loadMarket, loadRecommend, loadTriggers])

  const addPoolItem = useCallback(async (code: string, name: string, note?: string, tags?: string) => {
    await fetchJson('/api/seesaw/pool', { method: 'POST', body: JSON.stringify({ ts_code: code, name, note, tags, sort_order: 0 }) })
    setNotice(`已添加 ${code}`)
    await loadPool()
    await loadRecommend()
  }, [loadPool, loadRecommend])

  const removePoolItem = useCallback(async (id: number) => {
    await fetchJson(`/api/seesaw/pool/${id}`, { method: 'DELETE' })
    setNotice(`已移除`)
    await loadPool()
    await loadRecommend()
  }, [loadPool, loadRecommend])

  const togglePoolItem = useCallback(async (id: number, enabled: boolean) => {
    await fetchJson(`/api/seesaw/pool/${id}`, { method: 'PUT', body: JSON.stringify({ enabled }) })
    await loadPool()
    await loadRecommend()
  }, [loadPool, loadRecommend])

  return (
    <div className="space-y-5">
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900">
          <div className="flex items-center gap-2"><AlertTriangle className="h-4 w-4" /><span>{error}</span></div>
        </div>
      )}
      {notice && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">
          <div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4" /><span>{notice}</span></div>
        </div>
      )}

      <MarketStateCard state={marketState} loading={marketLoading} onRefresh={() => { void loadMarket(); void loadRecommend() }} />

      <div className="flex items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-sm font-medium text-ink">
          <input
            type="checkbox"
            checked={showDisabled}
            onChange={(e) => { setShowDisabled(e.target.checked); void loadPool() }}
            className="h-4 w-4 rounded border-line text-accent focus:ring-accent"
          />
          显示已禁用标的（便于恢复）
        </label>
      </div>

      <PoolSection items={poolItems} loading={poolLoading} onAdd={addPoolItem} onRemove={removePoolItem} onToggle={togglePoolItem} />

      <RecommendSection recs={recommendations} state={marketState?.state ?? 'neutral'} onRefresh={() => { void loadMarket(); void loadRecommend() }} />

      <TriggerHistory triggers={triggers} loading={triggersLoading} onRefresh={loadTriggers} />
    </div>
  )
}
