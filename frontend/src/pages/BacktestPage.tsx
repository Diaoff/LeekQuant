import React from 'react'
import { AlertTriangle, BarChart3, ArrowLeft, TrendingUp, TrendingDown, Loader2, Target, Percent, DollarSign, Activity, Trash2, GitCompare, ZoomIn, ZoomOut, RotateCcw, ShieldAlert } from 'lucide-react'
import { createChart, createSeriesMarkers, ColorType, LineSeries, CandlestickSeries } from 'lightweight-charts'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { fetchJson, formatNumber, formatDateTime } from '../lib/utils'
import Skeleton from '../components/Skeleton'

const COMPARE_COLORS = ['#10b981', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899']
const MAX_COMPARE_COUNT = 6
const ZOOM_STEP = 0.8
const MIN_VISIBLE_BARS = 8

type ChartApi = ReturnType<typeof createChart>

function createChartControls(chart: ChartApi | null, direction: 'in' | 'out' | 'reset') {
  if (!chart) return
  const timeScale = chart.timeScale()
  if (direction === 'reset') {
    timeScale.fitContent()
    return
  }

  const range = timeScale.getVisibleLogicalRange()
  if (!range) {
    timeScale.fitContent()
    return
  }

  const span = range.to - range.from
  const center = (range.from + range.to) / 2
  const nextSpan = direction === 'in' ? Math.max(span * ZOOM_STEP, MIN_VISIBLE_BARS) : span / ZOOM_STEP
  const half = nextSpan / 2
  timeScale.setVisibleLogicalRange({ from: center - half, to: center + half })
}

function ChartToolbar({
  onZoomIn,
  onZoomOut,
  onReset,
}: {
  onZoomIn: () => void
  onZoomOut: () => void
  onReset: () => void
}) {
  const buttonClass = 'inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-muted transition-colors hover:bg-rowHover hover:text-ink'

  return (
    <div className="flex items-center gap-1">
      <button type="button" onClick={onZoomIn} className={buttonClass} title="放大" aria-label="放大">
        <ZoomIn className="h-4 w-4" />
      </button>
      <button type="button" onClick={onZoomOut} className={buttonClass} title="缩小" aria-label="缩小">
        <ZoomOut className="h-4 w-4" />
      </button>
      <button type="button" onClick={onReset} className={buttonClass} title="重置缩放" aria-label="重置缩放">
        <RotateCcw className="h-4 w-4" />
      </button>
    </div>
  )
}

async function deleteBacktest(id: number) {
  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'}/api/backtests/${id}`, { method: 'DELETE' })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail ?? `${response.status} ${response.statusText}`)
  }
}

async function fetchBacktestKlines(backtestId: number, tsCode: string) {
  return fetchJson<KlineBar[]>(`/api/backtests/${backtestId}/klines?ts_code=${encodeURIComponent(tsCode)}`)
}

interface KlineBar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

interface BacktestResult {
  id: number
  strategy_id: number
  strategy_name: string | null
  target_label?: string | null
  start_date: string
  end_date: string
  initial_cash: string
  status: string
  total_return: string | null
  annual_return: string | null
  sharpe_ratio: string | null
  max_drawdown: string | null
  annual_vol: string | null
  win_rate: string | null
  trade_count: number | null
  performance: Record<string, unknown> | null
  trade_records: unknown[] | null
  equity_curve: { date: string; total_asset: number; cash: number }[] | null
  kline_data: Record<string, KlineBar[]> | null
  stock_names: Record<string, string> | null
  error_message: string | null
  created_at: string
  finished_at: string | null
}

interface EquityPoint {
  date: string
  value: number
}

interface TradeRecord {
  ts_code: string
  trade_date: string
  direction: string
  price: number
  volume: number
  amount: number
  commission: number
  stamp_tax: number
  transfer_fee: number
  total_fee: number
  action?: string
  signal_reason?: string
  target_position?: number
  position_before?: number
  position_after?: number
  pnl?: number
  balance_before?: number
  balance_after?: number
  holding_days?: number
  exit_reason?: string
}

export default function BacktestPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const [results, setResults] = React.useState<BacktestResult[]>([])
  const [selected, setSelected] = React.useState<BacktestResult | null>(null)
  const [detailLoading, setDetailLoading] = React.useState(false)
  const [loading, setLoading] = React.useState(true)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [deleting, setDeleting] = React.useState<number | null>(null)
  const [selectedIds, setSelectedIds] = React.useState<Set<number>>(new Set())
  const [compareBacktests, setCompareBacktests] = React.useState<BacktestResult[]>([])
  const [compareLoading, setCompareLoading] = React.useState(false)

  const isCompareMode = searchParams.get('ids') !== null
  const compareIds = React.useMemo(() => {
    const idsParam = searchParams.get('ids')
    if (!idsParam) return []
    return idsParam.split(',').map(id => parseInt(id, 10)).filter(id => !isNaN(id))
  }, [searchParams])

  const loadResults = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchJson<BacktestResult[]>('/api/backtests?limit=50')
      setResults(data)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadCompareBacktests = React.useCallback(async (ids: number[]) => {
    setCompareLoading(true)
    setError(null)
    try {
      const promises = ids.map(id => fetchJson<BacktestResult>(`/api/backtests/${id}?include_kline=false`))
      const results = await Promise.all(promises)
      setCompareBacktests(results)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setCompareLoading(false)
    }
  }, [])

  React.useEffect(() => {
    if (isCompareMode && compareIds.length > 0) {
      void loadCompareBacktests(compareIds)
    }
  }, [isCompareMode, compareIds, loadCompareBacktests])

  const handleDelete = async (r: BacktestResult) => {
    if (!confirm('确认删除该回测结果？')) return
    setDeleting(r.id)
    try {
      await deleteBacktest(r.id)
      if (selected?.id === r.id) setSelected(null)
      setSelectedIds(prev => {
        const next = new Set(prev)
        next.delete(r.id)
        return next
      })
      void loadResults()
      setNotice('回测结果已删除')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setDeleting(null)
    }
  }

  const openResult = async (r: BacktestResult) => {
    setDetailLoading(true)
    try {
      const detail = await fetchJson<BacktestResult>(`/api/backtests/${r.id}`)
      setSelected(detail)
    } catch {
      setSelected(r)
    } finally {
      setDetailLoading(false)
    }
  }

  const toggleSelect = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        if (next.size >= MAX_COMPARE_COUNT) {
          setNotice(`最多只能对比 ${MAX_COMPARE_COUNT} 个回测结果`)
          return prev
        }
        next.add(id)
      }
      return next
    })
  }

  const toggleSelectAll = () => {
    const successIds = results.filter(r => r.status === 'success').map(r => r.id)
    if (selectedIds.size === successIds.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(successIds.slice(0, MAX_COMPARE_COUNT)))
    }
  }

  const handleBatchDelete = async () => {
    if (selectedIds.size === 0) return
    if (!confirm(`确认删除选中的 ${selectedIds.size} 个回测结果？`)) return
    let deleted = 0
    for (const id of selectedIds) {
      try {
        await deleteBacktest(id)
        deleted++
      } catch {
        // continue
      }
    }
    setSelectedIds(new Set())
    if (selected?.id && selectedIds.has(selected.id)) setSelected(null)
    void loadResults()
    setNotice(`已删除 ${deleted} 个回测结果`)
  }

  const handleCompare = () => {
    if (selectedIds.size < 2) {
      setNotice('请至少选择2个回测结果进行对比')
      return
    }
    const ids = Array.from(selectedIds).join(',')
    navigate(`/backtests/compare?ids=${ids}`)
  }

  const handleBackFromCompare = () => {
    setSelectedIds(new Set())
    setCompareBacktests([])
    setSearchParams({})
  }

  React.useEffect(() => { void loadResults() }, [loadResults])

  if (isCompareMode) {
    return (
      <>
        {(notice || error) && (
          <section className={`mb-4 rounded-lg border p-4 text-sm ${error ? 'border-red-200 bg-red-50 text-red-900' : 'border-emerald-200 bg-emerald-50 text-emerald-900'}`} role="status">
            <div className="flex items-start gap-2">
              {error ? <AlertTriangle className="mt-0.5 h-4 w-4" /> : null}
              <span className="break-words">{error ?? notice}</span>
            </div>
          </section>
        )}
        {compareLoading ? (
          <section className="flex items-center justify-center rounded-lg border border-line bg-panel p-12 shadow-sm">
            <Loader2 className="mr-2 h-5 w-5 animate-spin text-accent" />
            <span className="text-sm text-muted">加载对比数据…</span>
          </section>
        ) : (
          <CompareView backtests={compareBacktests} onBack={handleBackFromCompare} />
        )}
      </>
    )
  }

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

      {!selected && !isCompareMode && (
        <section className="overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
          <div className="flex items-center gap-3 border-b border-line px-4 py-3">
            <h2 className="text-base font-semibold text-ink">回测结果</h2>
            <button onClick={() => void loadResults()} className="ml-auto text-sm font-medium text-accent hover:underline">刷新</button>
          </div>
          {selectedIds.size >= 2 && (
            <div className="flex items-center gap-3 border-b border-line px-4 py-3 bg-tableHead">
              <span className="text-sm text-muted">已选择 {selectedIds.size} 个回测</span>
              <button onClick={handleCompare} className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent/90 transition-colors">
                <GitCompare className="h-4 w-4" />
                对比
              </button>
              <button onClick={() => void handleBatchDelete()} className="ml-auto inline-flex items-center gap-2 rounded-md border border-red-300 px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-50 transition-colors">
                <Trash2 className="h-4 w-4" />
                批量删除
              </button>
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-line text-left text-sm">
              <thead className="bg-tableHead text-xs font-semibold uppercase text-muted">
                <tr><th className="w-10 px-2 py-3">
                  <input type="checkbox" checked={results.length > 0 && selectedIds.size === results.filter(r => r.status === 'success').length} onChange={toggleSelectAll} className="h-4 w-4 rounded border-slate-300 text-accent focus:ring-accent" />
                </th><th className="px-4 py-3">策略</th><th className="px-4 py-3">区间</th><th className="px-4 py-3">初始资金</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">收益率</th><th className="px-4 py-3">操作</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {loading ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-4">
                      <Skeleton.Table rows={5} columns={6} />
                    </td>
                  </tr>
                ) : results.length === 0 ? <tr><td colSpan={7} className="px-4 py-8 text-center text-muted">暂无回测结果，请先在策略中心运行回测</td></tr> : results.map((r) => {
                  const totalReturn = r.total_return !== null ? (Number(r.total_return) * 100).toFixed(2) : null
                  return (
                    <tr key={r.id} className="hover:bg-rowHover">
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          checked={selectedIds.has(r.id)}
                          onChange={() => r.status === 'success' && toggleSelect(r.id)}
                          disabled={r.status !== 'success'}
                          className="h-4 w-4 rounded border-slate-300 text-accent focus:ring-accent disabled:cursor-not-allowed disabled:opacity-40"
                        />
                      </td>
                      <td className="px-4 py-3 font-medium">{r.strategy_name ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-muted">{r.start_date} ~ {r.end_date}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums">{formatNumber(Number(r.initial_cash), 2)}</td>
                      <td className="px-4 py-3">
                        <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${r.status === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : r.status === 'running' ? 'border-amber-200 bg-amber-50 text-amber-800' : r.status === 'failed' ? 'border-red-200 bg-red-50 text-red-800' : 'border-line bg-tableHead text-muted'}`}>{r.status}</span>
                      </td>
                      <td className={`whitespace-nowrap px-4 py-3 tabular-nums font-medium ${totalReturn !== null && Number(totalReturn) >= 0 ? 'text-red-600' : totalReturn !== null ? 'text-emerald-600' : ''}`}>{totalReturn !== null ? `${totalReturn}%` : '—'}</td>
                       <td className="px-4 py-3 flex items-center gap-2">
                         {r.status === 'success' && <button onClick={() => openResult(r)} className="text-sm font-medium text-accent hover:underline">查看</button>}
                         {r.status === 'failed' && r.error_message && <span className="text-xs text-red-600" title={r.error_message}>失败</span>}
                         <button
                           onClick={() => void handleDelete(r)}
                           disabled={deleting === r.id}
                           className="text-sm font-medium text-red-500 hover:underline disabled:opacity-40 disabled:cursor-not-allowed"
                           title="删除回测结果"
                         >
                           {deleting === r.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                         </button>
                        </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {selected && <BacktestDetail result={selected} onBack={() => setSelected(null)} />}
      {detailLoading && <BacktestDetailSkeleton />}
    </>
  )
}

function CompareView({ backtests, onBack }: { backtests: BacktestResult[]; onBack: () => void }) {
  const chartRef = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    if (!chartRef.current || backtests.length === 0) return
    const container = chartRef.current
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark'
    const chart = createChart(container, {
      width: container.clientWidth,
      height: 400,
      layout: { background: { type: ColorType.Solid, color: isDark ? '#172033' : '#ffffff' }, textColor: isDark ? '#e8eaed' : '#334155' },
      grid: { vertLines: { color: isDark ? '#2a3a52' : '#f1f5f9' }, horzLines: { color: isDark ? '#2a3a52' : '#f1f5f9' } },
      timeScale: { borderColor: isDark ? '#2a3a52' : '#e2e8f0' },
      rightPriceScale: { borderColor: isDark ? '#2a3a52' : '#e2e8f0' },
    })

    backtests.forEach((bt, index) => {
      const color = COMPARE_COLORS[index % COMPARE_COLORS.length]
      if (bt.equity_curve && bt.equity_curve.length > 0) {
        const firstValue = bt.equity_curve[0].total_asset
        const lineSeries = chart.addSeries(LineSeries, {
          color,
          lineWidth: 2,
          crosshairMarkerRadius: 4,
          title: bt.strategy_name ?? `回测 ${bt.id}`,
        })
        const normalizedData = bt.equity_curve.map(d => ({
          time: d.date,
          value: (d.total_asset / firstValue) * 100000,
        }))
        lineSeries.setData(normalizedData)
      }
    })

    chart.timeScale().fitContent()

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth })
    }
    window.addEventListener('resize', handleResize)
    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
    }
  }, [backtests])

  if (backtests.length === 0) {
    return (
    <section className="rounded-lg border border-line bg-panel p-8 text-center text-muted shadow-sm">
        <p>没有可对比的回测数据</p>
        <button onClick={onBack} className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-accent hover:underline">
          <ArrowLeft className="h-4 w-4" />
          返回列表
        </button>
      </section>
    )
  }

  return (
    <section className="rounded-lg border border-line bg-panel shadow-sm">
      <div className="flex items-center gap-3 border-b border-line px-4 py-3">
        <button onClick={onBack} className="inline-flex items-center gap-1 text-sm font-medium text-accent hover:underline">
          <ArrowLeft className="h-4 w-4" />
          返回
        </button>
        <h2 className="text-base font-semibold text-ink">回测对比 ({backtests.length} 个)</h2>
      </div>

      <div className="px-4 pt-4">
        <div className="flex flex-wrap gap-4">
          {backtests.map((bt, index) => {
            const color = COMPARE_COLORS[index % COMPARE_COLORS.length]
            const totalReturn = bt.total_return !== null ? (Number(bt.total_return) * 100).toFixed(2) : '—'
            return (
              <div key={bt.id} className="flex items-center gap-2 text-sm">
                <span className="inline-block h-3 w-3 rounded-full" style={{ backgroundColor: color }}></span>
                <span className="font-medium text-ink">{bt.strategy_name ?? `回测 ${bt.id}`}</span>
                <span className="text-muted">总收益率: </span>
                <span className={`font-semibold ${Number(totalReturn) >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                  {totalReturn !== '—' ? `${totalReturn}%` : '—'}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      <div className="px-4 pt-4 pb-2">
        <div className="overflow-hidden rounded-md border border-line" ref={chartRef} style={{ height: 400 }} />
      </div>

      <div className="px-4 pb-4">
        <h3 className="mb-3 text-sm font-semibold text-ink">关键指标对比</h3>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-line text-left text-sm">
            <thead className="bg-tableHead text-xs font-semibold uppercase text-muted">
              <tr>
                <th className="px-4 py-3">策略名</th>
                <th className="px-4 py-3">回测区间</th>
                <th className="px-4 py-3">总收益率</th>
                <th className="px-4 py-3">年化收益</th>
                <th className="px-4 py-3">夏普比率</th>
                <th className="px-4 py-3">最大回撤</th>
                <th className="px-4 py-3">交易次数</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {backtests.map((bt, index) => {
                const color = COMPARE_COLORS[index % COMPARE_COLORS.length]
                return (
                  <tr key={bt.id} className="hover:bg-rowHover">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="inline-block h-3 w-3 rounded-full" style={{ backgroundColor: color }}></span>
                        <span className="font-medium">{bt.strategy_name ?? '—'}</span>
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-muted">{bt.start_date} ~ {bt.end_date}</td>
                    <td className="whitespace-nowrap px-4 py-3 tabular-nums">
                      {bt.total_return !== null ? (
                        <span className={`font-medium ${Number(bt.total_return) >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                          {(Number(bt.total_return) * 100).toFixed(2)}%
                        </span>
                      ) : '—'}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 tabular-nums">
                      {bt.annual_return !== null ? `${(Number(bt.annual_return) * 100).toFixed(2)}%` : '—'}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 tabular-nums">
                      {bt.sharpe_ratio !== null ? formatNumber(Number(bt.sharpe_ratio), 2) : '—'}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 tabular-nums">
                      {bt.max_drawdown !== null ? `${(Number(bt.max_drawdown) * 100).toFixed(2)}%` : '—'}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 tabular-nums">
                      {formatNumber(bt.trade_count ?? 0)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}

function BacktestDetailSkeleton() {
  return (
    <section className="rounded-lg border border-line bg-panel shadow-sm">
      <div className="border-b border-line px-4 py-3">
        <Skeleton.Line className="w-32" />
      </div>
      <div className="grid gap-4 p-4 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton.Card key={i} lines={2} />
        ))}
      </div>
      <div className="px-4 pb-4">
        <div className="h-[400px] overflow-hidden rounded-md border border-line">
          <Skeleton.Table rows={8} columns={4} />
        </div>
      </div>
      <div className="px-4 pb-4">
        <div className="h-[300px] overflow-hidden rounded-md border border-line">
          <Skeleton.Table rows={5} columns={4} />
        </div>
      </div>
      <div className="border-t border-line px-4 py-3">
        <Skeleton.Table rows={5} columns={10} />
      </div>
    </section>
  )
}

function BacktestDetail({ result, onBack }: { result: BacktestResult; onBack: () => void }) {
  const perf = result.performance as Record<string, string | number> | null
  const trades = (result.trade_records ?? []) as TradeRecord[]
  const [selectedTradeIndex, setSelectedTradeIndex] = React.useState<number | null>(null)
  const [klineCache, setKlineCache] = React.useState<Record<string, KlineBar[]>>(() => result.kline_data ?? {})
  const [klineLoadingTsCode, setKlineLoadingTsCode] = React.useState<string | null>(null)
  const [klineError, setKlineError] = React.useState<string | null>(null)
  const chartRef = React.useRef<HTMLDivElement>(null)
  const klineChartRef = React.useRef<HTMLDivElement>(null)
  const equityChartRef = React.useRef<ChartApi | null>(null)
  const klineChartApiRef = React.useRef<ChartApi | null>(null)
  const selectedTrade = selectedTradeIndex === null ? null : trades[selectedTradeIndex] ?? null
  const selectedTsCode = selectedTrade?.ts_code ?? null
  const selectedKlines = selectedTsCode ? klineCache[selectedTsCode] ?? null : null
  const isKlineLoading = selectedTsCode !== null && klineLoadingTsCode === selectedTsCode

  React.useEffect(() => {
    setSelectedTradeIndex(null)
    setKlineCache(result.kline_data ?? {})
    setKlineLoadingTsCode(null)
    setKlineError(null)
  }, [result.id, result.kline_data])

  React.useEffect(() => {
    if (!selectedTsCode) {
      setKlineLoadingTsCode(null)
      return
    }
    if (Object.prototype.hasOwnProperty.call(klineCache, selectedTsCode)) {
      setKlineError(null)
      setKlineLoadingTsCode(null)
      return
    }

    let cancelled = false
    setKlineLoadingTsCode(selectedTsCode)
    setKlineError(null)
    fetchBacktestKlines(result.id, selectedTsCode)
      .then((klines) => {
        if (cancelled) return
        setKlineCache((cache) => ({ ...cache, [selectedTsCode]: klines }))
      })
      .catch((caught) => {
        if (cancelled) return
        setKlineError(caught instanceof Error ? caught.message : String(caught))
      })
      .finally(() => {
        if (!cancelled) setKlineLoadingTsCode(null)
      })

    return () => {
      cancelled = true
    }
  }, [klineCache, result.id, selectedTsCode])

  React.useEffect(() => {
    if (!chartRef.current) return
    const container = chartRef.current
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark'
    const chart = createChart(container, {
      width: container.clientWidth,
      height: 300,
      layout: { background: { type: ColorType.Solid, color: isDark ? '#172033' : '#ffffff' }, textColor: isDark ? '#e8eaed' : '#334155' },
      grid: { vertLines: { color: isDark ? '#2a3a52' : '#f1f5f9' }, horzLines: { color: isDark ? '#2a3a52' : '#f1f5f9' } },
      timeScale: { borderColor: isDark ? '#2a3a52' : '#e2e8f0' },
      rightPriceScale: { borderColor: isDark ? '#2a3a52' : '#e2e8f0' },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: false, axisPressedMouseMove: true, axisDoubleClickReset: false },
    })
    equityChartRef.current = chart

    const equityCurve = result.equity_curve
    if (equityCurve && equityCurve.length > 0) {
      const lineSeries = chart.addSeries(LineSeries, {
        color: '#10b981',
        lineWidth: 2,
        crosshairMarkerRadius: 4,
      })
      lineSeries.setData(equityCurve.map((d) => ({ time: d.date, value: d.total_asset })))
      chart.timeScale().fitContent()
    }

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth })
    }
    window.addEventListener('resize', handleResize)
    return () => {
      window.removeEventListener('resize', handleResize)
      equityChartRef.current = null
      chart.remove()
    }
  }, [result.equity_curve])

  React.useEffect(() => {
    if (!klineChartRef.current || !selectedTsCode || !selectedKlines || selectedKlines.length === 0) return
    const container = klineChartRef.current
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark'
    const chart = createChart(container, {
      width: container.clientWidth,
      height: 400,
      layout: { background: { type: ColorType.Solid, color: isDark ? '#172033' : '#ffffff' }, textColor: isDark ? '#e8eaed' : '#334155' },
      grid: { vertLines: { color: isDark ? '#2a3a52' : '#f1f5f9' }, horzLines: { color: isDark ? '#2a3a52' : '#f1f5f9' } },
      timeScale: { borderColor: isDark ? '#2a3a52' : '#e2e8f0' },
      rightPriceScale: { borderColor: isDark ? '#2a3a52' : '#e2e8f0' },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: false, axisPressedMouseMove: true, axisDoubleClickReset: false },
    })
    klineChartApiRef.current = chart

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#ef4444',
      downColor: '#22c55e',
      borderUpColor: '#ef4444',
      borderDownColor: '#22c55e',
      wickUpColor: '#ef4444',
      wickDownColor: '#22c55e',
    })
    candleSeries.setData(selectedKlines.map((k) => ({
      time: k.date,
      open: k.open,
      high: k.high,
      low: k.low,
      close: k.close,
    })))

    const markers = trades
      .filter((t) => t.ts_code === selectedTsCode)
      .map((t) => {
        const isBuy = t.direction === '买入' || t.action?.startsWith('BUY')
        return {
          time: t.trade_date,
          position: isBuy ? 'belowBar' as const : 'aboveBar' as const,
          color: isBuy ? '#ef4444' : '#22c55e',
          shape: isBuy ? 'arrowUp' as const : 'arrowDown' as const,
          text: isBuy ? '买入' : '卖出',
        }
      })
    createSeriesMarkers(candleSeries, markers)
    chart.timeScale().fitContent()

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth })
    }
    window.addEventListener('resize', handleResize)
    return () => {
      window.removeEventListener('resize', handleResize)
      klineChartApiRef.current = null
      chart.remove()
    }
  }, [selectedKlines, selectedTsCode, trades])

  const riskCfg = result.performance?.risk_config as Record<string, number> | undefined
  const riskLabel = !riskCfg
    ? '—'
    : [riskCfg.stop_loss_pct, riskCfg.take_profit_pct, riskCfg.trailing_stop_pct, riskCfg.time_stop_days].every(v => !v || Number(v) === 0)
      ? '未设置'
      : [
          riskCfg.stop_loss_pct ? `${(Number(riskCfg.stop_loss_pct) * 100).toFixed(0)}%` : '',
          riskCfg.take_profit_pct ? `${(Number(riskCfg.take_profit_pct) * 100).toFixed(0)}%` : '',
          riskCfg.trailing_stop_pct ? `${(Number(riskCfg.trailing_stop_pct) * 100).toFixed(0)}%` : '',
          riskCfg.time_stop_days ? `${riskCfg.time_stop_days}天` : '',
        ].filter(Boolean).join(' / ')

  const metrics = [
    { icon: <DollarSign className="h-4 w-4 text-accent" />, label: '初始资金', value: formatNumber(Number(result.initial_cash), 2) },
    { icon: <TrendingUp className="h-4 w-4 text-red-600" />, label: '总收益率', value: result.total_return !== null ? `${(Number(result.total_return) * 100).toFixed(2)}%` : '—' },
    { icon: <Target className="h-4 w-4 text-mint" />, label: '年化收益', value: result.annual_return !== null ? `${(Number(result.annual_return) * 100).toFixed(2)}%` : '—' },
    { icon: <Activity className="h-4 w-4 text-warn" />, label: '最大回撤', value: result.max_drawdown !== null ? `${(Number(result.max_drawdown) * 100).toFixed(2)}%` : '—' },
    { icon: <Percent className="h-4 w-4 text-slate-600" />, label: '夏普比率', value: result.sharpe_ratio !== null ? formatNumber(Number(result.sharpe_ratio), 2) : '—' },
    { icon: <BarChart3 className="h-4 w-4 text-slate-600" />, label: '交易次数', value: formatNumber(result.trade_count ?? trades.length) },
    { icon: <ShieldAlert className="h-4 w-4 text-amber-600" />, label: '止损 / 止盈 / 移动 / 时间', value: riskLabel },
  ]

  const totalPnl = trades.reduce((sum, t) => sum + (t.pnl || 0), 0)
  const avgHoldingDays = (() => { const d = trades.filter(t => (t.holding_days ?? 0) > 0); return d.length > 0 ? d.reduce((s, t) => s + (t.holding_days || 0), 0) / d.length : 0 })()
  const totalFees = trades.reduce((sum, t) => sum + (t.total_fee || 0), 0)

  return (
    <section className="rounded-lg border border-line bg-panel shadow-sm">
      <div className="flex items-center gap-3 border-b border-line px-4 py-3">
        <button onClick={onBack} className="inline-flex items-center gap-1 text-sm font-medium text-accent hover:underline">
          <ArrowLeft className="h-4 w-4" />
          返回
        </button>
        <h2 className="text-base font-semibold text-ink">{result.strategy_name ?? '回测详情'}</h2>
        <span className="rounded-full border border-line bg-tableHead px-2.5 py-1 text-xs text-muted">{result.target_label ?? '全市场'}</span>
        <span className="text-sm text-muted">{result.start_date} ~ {result.end_date}</span>
      </div>

      <div className="grid gap-4 p-4 md:grid-cols-3 xl:grid-cols-6">
        {metrics.map((m) => (
          <div key={m.label} className="rounded-lg border border-line p-3">
            <div className="flex items-center gap-2 text-sm text-muted">{m.icon}<span>{m.label}</span></div>
            <p className="mt-2 text-xl font-semibold tabular-nums text-ink">{m.value}</p>
          </div>
        ))}
      </div>

      {trades.length > 0 && (
        <>
          <div className="mx-4 mt-2 grid grid-cols-4 gap-2 rounded-lg bg-tableHead p-3">
            <div className="text-center">
              <div className="text-[10px] uppercase tracking-wider text-muted">总盈亏</div>
              <div className={`text-sm font-bold ${totalPnl >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>{totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)}</div>
            </div>
            <div className="text-center">
              <div className="text-[10px] uppercase tracking-wider text-muted">持仓天</div>
              <div className="text-sm font-bold text-ink">{avgHoldingDays.toFixed(1)}天</div>
            </div>
            <div className="text-center">
              <div className="text-[10px] uppercase tracking-wider text-muted">手续费</div>
              <div className="text-sm font-bold text-warn">{totalFees.toFixed(2)}</div>
            </div>
            <div className="text-center">
              <div className="text-[10px] uppercase tracking-wider text-muted">交易笔数</div>
              <div className="text-sm font-bold text-ink">{trades.length}</div>
            </div>
          </div>

          <div className="px-4 pb-4">
            <div className="mb-2 flex items-center justify-end">
              <ChartToolbar
                onZoomIn={() => createChartControls(equityChartRef.current, 'in')}
                onZoomOut={() => createChartControls(equityChartRef.current, 'out')}
                onReset={() => createChartControls(equityChartRef.current, 'reset')}
              />
            </div>
            <div className="h-[300px] overflow-hidden rounded-md border border-line" ref={chartRef} />
          </div>

          <div className="px-4 pb-4">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-base font-semibold text-ink">交易 K 线</h3>
              <div className="flex flex-wrap items-center gap-3">
                {selectedTrade && (
                  <span className="text-sm text-muted">
                    {selectedTrade.ts_code} · {selectedTrade.trade_date} · {selectedTrade.direction} · ¥{formatNumber(selectedTrade.price, 2)}
                  </span>
                )}
                <ChartToolbar
                  onZoomIn={() => createChartControls(klineChartApiRef.current, 'in')}
                  onZoomOut={() => createChartControls(klineChartApiRef.current, 'out')}
                  onReset={() => createChartControls(klineChartApiRef.current, 'reset')}
                />
              </div>
            </div>
            {selectedTrade && selectedKlines && selectedKlines.length > 0 ? (
              <div className="h-[400px] overflow-hidden rounded-md border border-line" ref={klineChartRef} />
            ) : (
              <div className="flex h-[240px] items-center justify-center rounded-md border border-dashed border-line bg-surface px-4 text-center text-sm text-muted">
                {isKlineLoading ? (
                  <span className="inline-flex items-center gap-2">
                    <Loader2 className="h-4 w-4 animate-spin text-accent" />
                    正在加载 {selectedTrade?.ts_code} 的 K 线…
                  </span>
                ) : klineError ? (
                  <span className="text-red-700">{selectedTrade?.ts_code} 的 K 线加载失败：{klineError}</span>
                ) : selectedTrade ? (
                  `${selectedTrade.ts_code} 在回测区间内暂无 K 线数据。`
                ) : (
                  '点击下方任意一条买卖记录，查看该股票的 K 线和交易标记。'
                )}
              </div>
            )}
          </div>

          <div className="border-t border-line">
            <div className="px-4 py-3 flex items-center justify-between">
              <h3 className="text-base font-semibold text-ink">交易明细 ({trades.length}笔)</h3>
            </div>
            <TradeRecordsTable
              trades={trades}
              selectedTradeIndex={selectedTradeIndex}
              onSelectTrade={setSelectedTradeIndex}
              stockNames={result.stock_names ?? {}}
            />
          </div>
        </>
      )}

      {result.error_message && (
        <div className="border-t border-line p-4">
          <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-900">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4" />
              <div>
                <p className="font-medium">回测失败</p>
                <p className="mt-1 break-words">{result.error_message}</p>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

type SortKey = keyof TradeRecord | 'index'
type SortDir = 'asc' | 'desc'

function TradeRecordsTable({
  trades,
  selectedTradeIndex,
  onSelectTrade,
  stockNames,
}: {
  trades: TradeRecord[]
  selectedTradeIndex: number | null
  onSelectTrade: (index: number) => void
  stockNames: Record<string, string>
}) {
  const [sortKey, setSortKey] = React.useState<SortKey>('index')
  const [sortDir, setSortDir] = React.useState<SortDir>('asc')

  const totalPnl = React.useMemo(() => trades.reduce((sum, t) => sum + (t.pnl || 0), 0), [trades])
  const avgHoldingDays = React.useMemo(() => {
    const withDays = trades.filter((t) => (t.holding_days ?? 0) > 0)
    return withDays.length > 0 ? trades.reduce((sum, t) => sum + (t.holding_days || 0), 0) / withDays.length : 0
  }, [trades])
  const totalFees = React.useMemo(() => trades.reduce((sum, t) => sum + (t.total_fee || 0), 0), [trades])

  const handleSort = React.useCallback((key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }, [sortKey])

  const sortedTrades = React.useMemo(() => {
    const indexed = trades.map((t, i) => ({ ...t, _idx: i }))
    return [...indexed].sort((a, b) => {
      let va: number | string, vb: number | string
      if (sortKey === 'index') { va = a._idx; vb = b._idx } else {
        va = a[sortKey] ?? 0; vb = b[sortKey] ?? 0
      }
      if (typeof va === 'string') return sortDir === 'asc' ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va))
      return sortDir === 'asc' ? (va as number) - (vb as number) : (vb as number) - (va as number)
    })
  }, [trades, sortKey, sortDir])

  const sortIndicator = (key: SortKey) => {
    if (sortKey !== key) return <span className="ml-1 text-muted">↕</span>
    return <span className="ml-1 text-accent">{sortDir === 'asc' ? '↑' : '↓'}</span>
  }

  return (
    <div className="px-4 pb-4">
      <div className="overflow-x-auto rounded-md border border-line">
        <table className="min-w-full divide-y divide-line text-left text-xs">
          <thead className="sticky top-0 z-10 bg-tableHead text-xs font-semibold uppercase text-muted">
            <tr>
              <th className="cursor-pointer select-none whitespace-nowrap px-2 py-2.5" style={{ width: 50 }} onClick={() => handleSort('index')}>序号{sortIndicator('index')}</th>
              <th className="cursor-pointer select-none whitespace-nowrap px-2 py-2.5" style={{ width: 100 }} onClick={() => handleSort('trade_date')}>日期{sortIndicator('trade_date')}</th>
              <th className="cursor-pointer select-none whitespace-nowrap px-2 py-2.5" style={{ width: 100 }} onClick={() => handleSort('ts_code')}>股票代码{sortIndicator('ts_code')}</th>
              <th className="whitespace-nowrap px-2 py-2.5" style={{ width: 120 }}>股票名称</th>
              <th className="whitespace-nowrap px-2 py-2.5" style={{ width: 80 }}>方向</th>
              <th className="whitespace-nowrap px-2 py-2.5" style={{ width: 90 }}>动作</th>
              <th className="hidden whitespace-nowrap px-2 py-2.5 sm:table-cell" style={{ width: 80 }}>卖出原因</th>
              <th className="hidden whitespace-nowrap px-2 py-2.5 lg:table-cell" style={{ width: 150 }}>信号原因</th>
              <th className="cursor-pointer select-none whitespace-nowrap px-2 py-2.5" style={{ width: 80 }} onClick={() => handleSort('price')}>价格{sortIndicator('price')}</th>
              <th className="cursor-pointer select-none whitespace-nowrap px-2 py-2.5" style={{ width: 80 }} onClick={() => handleSort('volume')}>数量{sortIndicator('volume')}</th>
              <th className="cursor-pointer select-none whitespace-nowrap px-2 py-2.5" style={{ width: 100 }} onClick={() => handleSort('amount')}>金额{sortIndicator('amount')}</th>
              <th className="hidden whitespace-nowrap px-2 py-2.5 md:table-cell" style={{ width: 120 }}>仓位变化</th>
              <th className="cursor-pointer select-none whitespace-nowrap px-2 py-2.5" style={{ width: 100 }} onClick={() => handleSort('pnl')}>盈亏{sortIndicator('pnl')}</th>
              <th className="hidden whitespace-nowrap px-2 py-2.5 sm:table-cell" style={{ width: 80 }} onClick={() => handleSort('holding_days')}>持仓天数{sortIndicator('holding_days')}</th>
              <th className="hidden cursor-pointer select-none whitespace-nowrap px-2 py-2.5 sm:table-cell" style={{ width: 80 }} onClick={() => handleSort('total_fee')}>总费用{sortIndicator('total_fee')}</th>
              <th className="hidden cursor-pointer select-none whitespace-nowrap px-2 py-2.5 md:table-cell" style={{ width: 110 }} onClick={() => handleSort('balance_after')}>交易后资产{sortIndicator('balance_after')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {sortedTrades.map((t, i) => {
              const isSelected = selectedTradeIndex === t._idx
              return (
              <tr
                key={t._idx}
                tabIndex={0}
                onClick={() => onSelectTrade(t._idx)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onSelectTrade(t._idx)
                  }
                }}
                className={`cursor-pointer outline-none transition hover:bg-rowHover focus:bg-rowHover focus:ring-2 focus:ring-inset focus:ring-accent ${isSelected ? 'bg-accent/10 ring-2 ring-inset ring-accent' : i % 2 === 1 ? 'bg-rowAlt' : ''}`}
              >
                <td className="whitespace-nowrap px-2 py-2 tabular-nums text-muted">{t._idx + 1}</td>
                <td className="whitespace-nowrap px-2 py-2 text-muted">{t.trade_date}</td>
                <td className="whitespace-nowrap px-2 py-2 font-mono font-medium">{t.ts_code}</td>
                <td className="whitespace-nowrap px-2 py-2 text-muted">{stockNames[t.ts_code] ?? '—'}</td>
                <td className="px-2 py-2">
                  <span className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${t.direction === '买入' ? 'border-red-200 bg-red-50 text-red-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>{t.direction}</span>
                </td>
                <td className="px-2 py-2">
                  {t.action && (
                    <span className={`text-xs font-medium ${t.action.includes('BUY') ? 'text-red-600' : t.action.includes('SELL') ? 'text-emerald-600' : 'text-muted'}`}>
                      {t.action.replace('BUY', '买入').replace('SELL_PARTIAL', '部分卖出').replace('SELL_ALL', '全部卖出')}
                    </span>
                  )}
                </td>
                <td className="hidden whitespace-nowrap px-2 py-2 sm:table-cell">
                  {t.exit_reason ? (
                    <span className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${
                      t.exit_reason === '止损' ? 'border-red-200 bg-red-50 text-red-700'
                      : t.exit_reason === '止盈' ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                      : t.exit_reason === '移动止盈' ? 'border-amber-200 bg-amber-50 text-amber-700'
                      : t.exit_reason === '时间止损' ? 'border-slate-200 bg-slate-50 text-slate-600'
                      : 'border-gray-200 bg-gray-50 text-gray-600'
                    }`}>{t.exit_reason}</span>
                  ) : '—'}
                </td>
                <td className="hidden truncate px-2 py-2 text-muted lg:table-cell" title={t.signal_reason}>{t.signal_reason ?? '—'}</td>
                <td className="whitespace-nowrap px-2 py-2 tabular-nums">¥{formatNumber(t.price, 2)}</td>
                <td className="whitespace-nowrap px-2 py-2 tabular-nums">{formatNumber(t.volume, 0)}股</td>
                <td className="whitespace-nowrap px-2 py-2 tabular-nums">¥{formatNumber(t.amount, 2)}</td>
                <td className="hidden whitespace-nowrap px-2 py-2 md:table-cell">
                  {(t.position_before != null || t.position_after != null) ? (
                    <span className="tabular-nums text-xs">
                      {((t.position_before ?? 0) * 100).toFixed(1)}%
                      {t.position_after != null && (
                        <>
                          {' → '}
                          <span className={t.position_after > (t.position_before ?? 0) ? 'text-red-600' : t.position_after < (t.position_before ?? 0) ? 'text-emerald-600' : ''}>
                            {(t.position_after * 100).toFixed(1)}%
                            {t.position_after > (t.position_before ?? 0) ? ' ↑' : t.position_after < (t.position_before ?? 0) ? ' ↓' : ''}
                          </span>
                        </>
                      )}
                    </span>
                  ) : '—'}
                </td>
                <td className="whitespace-nowrap px-2 py-2 tabular-nums">
                  {Math.abs(t.pnl ?? 0) > 0 ? (
                    <span className={`font-semibold ${(t.pnl ?? 0) > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                      {(t.pnl ?? 0) > 0 ? '+' : ''}¥{formatNumber(Math.abs(t.pnl ?? 0), 2)}
                    </span>
                  ) : '—'}
                </td>
                <td className="hidden whitespace-nowrap px-2 py-2 tabular-nums sm:table-cell">{(t.holding_days ?? 0) > 0 ? `${t.holding_days}天` : '—'}</td>
                <td className="hidden whitespace-nowrap px-2 py-2 tabular-nums sm:table-cell">¥{formatNumber(t.total_fee, 2)}</td>
                <td className="hidden whitespace-nowrap px-2 py-2 tabular-nums md:table-cell">{t.balance_after != null ? `¥${formatNumber(t.balance_after, 2)}` : '—'}</td>
              </tr>
            )})}
          </tbody>
        </table>
      </div>
    </div>
  )
}
