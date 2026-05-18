import React from 'react'
import { AlertTriangle, BarChart3, ArrowLeft, TrendingUp, TrendingDown, Loader2, Target, Percent, DollarSign, Activity } from 'lucide-react'
import { createChart, ColorType, LineSeries, CandlestickSeries } from 'lightweight-charts'
import { fetchJson, formatNumber, formatDateTime } from '../App'

interface BacktestResult {
  id: number
  strategy_id: number
  strategy_name: string | null
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
}

export default function BacktestPage() {
  const [results, setResults] = React.useState<BacktestResult[]>([])
  const [selected, setSelected] = React.useState<BacktestResult | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)

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

  const openResult = (r: BacktestResult) => {
    setSelected(r)
  }

  React.useEffect(() => { void loadResults() }, [loadResults])

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

      {!selected && (
        <section className="overflow-hidden rounded-lg border border-line bg-white shadow-sm">
          <div className="flex items-center gap-3 border-b border-line px-4 py-3">
            <h2 className="text-base font-semibold text-ink">回测结果</h2>
            <button onClick={() => void loadResults()} className="ml-auto text-sm font-medium text-accent hover:underline">刷新</button>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-line text-left text-sm">
              <thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-600">
                <tr><th className="px-4 py-3">策略</th><th className="px-4 py-3">区间</th><th className="px-4 py-3">初始资金</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">收益率</th><th className="px-4 py-3">操作</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {loading ? <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">加载中</td></tr> : results.length === 0 ? <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">暂无回测结果，请先在策略中心运行回测</td></tr> : results.map((r) => {
                  const perf = r.performance as Record<string, string> | null
                  const totalReturn = perf?.total_return ?? '—'
                  return (
                    <tr key={r.id} className="hover:bg-slate-50/60">
                      <td className="px-4 py-3 font-medium">{r.strategy_name ?? '—'}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-700">{r.start_date} ~ {r.end_date}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums">{formatNumber(Number(r.initial_cash), 2)}</td>
                      <td className="px-4 py-3">
                        <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${r.status === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : r.status === 'running' ? 'border-amber-200 bg-amber-50 text-amber-800' : r.status === 'failed' ? 'border-red-200 bg-red-50 text-red-800' : 'border-slate-200 bg-slate-50 text-slate-700'}`}>{r.status}</span>
                      </td>
                      <td className={`whitespace-nowrap px-4 py-3 tabular-nums font-medium ${String(totalReturn).startsWith('-') ? 'text-emerald-600' : totalReturn !== '—' ? 'text-red-600' : ''}`}>{totalReturn !== '—' ? `${totalReturn}%` : '—'}</td>
                      <td className="px-4 py-3">
                        {r.status === 'success' && <button onClick={() => openResult(r)} className="text-sm font-medium text-accent hover:underline">查看</button>}
                        {r.status === 'failed' && r.error_message && <span className="text-xs text-red-600" title={r.error_message}>失败</span>}
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
    </>
  )
}

function BacktestDetail({ result, onBack }: { result: BacktestResult; onBack: () => void }) {
  const perf = result.performance as Record<string, string | number> | null
  const trades = (result.trade_records ?? []) as TradeRecord[]
  const chartRef = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    if (!chartRef.current) return
    const container = chartRef.current
    const chart = createChart(container, {
      width: container.clientWidth,
      height: 300,
      layout: { background: { type: ColorType.Solid, color: '#ffffff' }, textColor: '#334155' },
      grid: { vertLines: { color: '#f1f5f9' }, horzLines: { color: '#f1f5f9' } },
      timeScale: { borderColor: '#e2e8f0' },
      rightPriceScale: { borderColor: '#e2e8f0' },
    })

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
      chart.remove()
    }
  }, [result.equity_curve])

  const metrics = [
    { icon: <DollarSign className="h-4 w-4 text-accent" />, label: '初始资金', value: formatNumber(Number(result.initial_cash), 2) },
    { icon: <TrendingUp className="h-4 w-4 text-red-600" />, label: '总收益率', value: result.total_return !== null ? `${(Number(result.total_return) * 100).toFixed(2)}%` : '—' },
    { icon: <Target className="h-4 w-4 text-mint" />, label: '年化收益', value: result.annual_return !== null ? `${(Number(result.annual_return) * 100).toFixed(2)}%` : '—' },
    { icon: <Activity className="h-4 w-4 text-warn" />, label: '最大回撤', value: result.max_drawdown !== null ? `${(Number(result.max_drawdown) * 100).toFixed(2)}%` : '—' },
    { icon: <Percent className="h-4 w-4 text-slate-600" />, label: '夏普比率', value: result.sharpe_ratio !== null ? formatNumber(Number(result.sharpe_ratio), 2) : '—' },
    { icon: <BarChart3 className="h-4 w-4 text-slate-600" />, label: '交易次数', value: formatNumber(result.trade_count ?? trades.length) },
  ]

  const totalPnl = trades.reduce((sum, t) => sum + (t.pnl || 0), 0)
  const avgHoldingDays = (() => { const d = trades.filter(t => (t.holding_days ?? 0) > 0); return d.length > 0 ? d.reduce((s, t) => s + (t.holding_days || 0), 0) / d.length : 0 })()
  const totalFees = trades.reduce((sum, t) => sum + (t.total_fee || 0), 0)

  return (
    <section className="rounded-lg border border-line bg-white shadow-sm">
      <div className="flex items-center gap-3 border-b border-line px-4 py-3">
        <button onClick={onBack} className="inline-flex items-center gap-1 text-sm font-medium text-accent hover:underline">
          <ArrowLeft className="h-4 w-4" />
          返回
        </button>
        <h2 className="text-base font-semibold text-ink">{result.strategy_name ?? '回测详情'}</h2>
        <span className="text-sm text-slate-500">{result.start_date} ~ {result.end_date}</span>
      </div>

      <div className="grid gap-4 p-4 md:grid-cols-3 xl:grid-cols-6">
        {metrics.map((m) => (
          <div key={m.label} className="rounded-lg border border-line p-3">
            <div className="flex items-center gap-2 text-sm text-slate-600">{m.icon}<span>{m.label}</span></div>
            <p className="mt-2 text-xl font-semibold tabular-nums text-ink">{m.value}</p>
          </div>
        ))}
      </div>

      {trades.length > 0 && (
        <>
          <div className="mx-4 mt-2 grid grid-cols-4 gap-2 rounded-lg bg-slate-50 p-3">
            <div className="text-center">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">总盈亏</div>
              <div className={`text-sm font-bold ${totalPnl >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>{totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)}</div>
            </div>
            <div className="text-center">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">持仓天</div>
              <div className="text-sm font-bold text-ink">{avgHoldingDays.toFixed(1)}天</div>
            </div>
            <div className="text-center">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">手续费</div>
              <div className="text-sm font-bold text-warn">{totalFees.toFixed(2)}</div>
            </div>
            <div className="text-center">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">交易笔数</div>
              <div className="text-sm font-bold text-ink">{trades.length}</div>
            </div>
          </div>

          <div className="px-4 pb-4">
            <div className="h-[300px] overflow-hidden rounded-md border border-line" ref={chartRef} />
          </div>

          <div className="border-t border-line">
            <div className="px-4 py-3 flex items-center justify-between">
              <h3 className="text-base font-semibold text-ink">交易明细 ({trades.length}笔)</h3>
            </div>
            <TradeRecordsTable trades={trades} />
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

function TradeRecordsTable({ trades }: { trades: TradeRecord[] }) {
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
    if (sortKey !== key) return <span className="ml-1 text-slate-300">↕</span>
    return <span className="ml-1 text-accent">{sortDir === 'asc' ? '↑' : '↓'}</span>
  }

  return (
    <div className="px-4 pb-4">
      <div className="overflow-x-auto rounded-md border border-line">
        <table className="min-w-full divide-y divide-line text-left text-xs">
          <thead className="sticky top-0 z-10 bg-slate-50 text-xs font-semibold uppercase text-slate-600">
            <tr>
              <th className="cursor-pointer select-none whitespace-nowrap px-2 py-2.5" style={{ width: 50 }} onClick={() => handleSort('index')}>序号{sortIndicator('index')}</th>
              <th className="cursor-pointer select-none whitespace-nowrap px-2 py-2.5" style={{ width: 100 }} onClick={() => handleSort('trade_date')}>日期{sortIndicator('trade_date')}</th>
              <th className="cursor-pointer select-none whitespace-nowrap px-2 py-2.5" style={{ width: 100 }} onClick={() => handleSort('ts_code')}>股票代码{sortIndicator('ts_code')}</th>
              <th className="whitespace-nowrap px-2 py-2.5" style={{ width: 80 }}>方向</th>
              <th className="whitespace-nowrap px-2 py-2.5" style={{ width: 90 }}>动作</th>
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
            {sortedTrades.map((t, i) => (
              <tr key={i} className={`hover:bg-slate-50/60 ${i % 2 === 1 ? 'bg-slate-50/30' : ''}`}>
                <td className="whitespace-nowrap px-2 py-2 tabular-nums text-slate-500">{t._idx + 1}</td>
                <td className="whitespace-nowrap px-2 py-2 text-slate-700">{t.trade_date}</td>
                <td className="whitespace-nowrap px-2 py-2 font-mono font-medium">{t.ts_code}</td>
                <td className="px-2 py-2">
                  <span className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${t.direction === '买入' ? 'border-red-200 bg-red-50 text-red-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>{t.direction}</span>
                </td>
                <td className="px-2 py-2">
                  {t.action && (
                    <span className={`text-xs font-medium ${t.action.includes('BUY') ? 'text-red-600' : t.action.includes('SELL') ? 'text-emerald-600' : 'text-slate-600'}`}>
                      {t.action.replace('BUY', '买入').replace('SELL_PARTIAL', '部分卖出').replace('SELL_ALL', '全部卖出')}
                    </span>
                  )}
                </td>
                <td className="hidden truncate px-2 py-2 text-slate-600 lg:table-cell" title={t.signal_reason}>{t.signal_reason ?? '—'}</td>
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
                  {t.pnl != null ? (
                    <span className={`font-semibold ${t.pnl > 0 ? 'text-red-600' : t.pnl < 0 ? 'text-emerald-600' : 'text-slate-500'}`}>
                      {t.pnl > 0 ? '+' : ''}¥{formatNumber(Math.abs(t.pnl), 2)}
                    </span>
                  ) : '—'}
                </td>
                <td className="hidden whitespace-nowrap px-2 py-2 tabular-nums sm:table-cell">{t.holding_days != null ? `${t.holding_days}天` : '—'}</td>
                <td className="hidden whitespace-nowrap px-2 py-2 tabular-nums sm:table-cell">¥{formatNumber(t.total_fee, 2)}</td>
                <td className="hidden whitespace-nowrap px-2 py-2 tabular-nums md:table-cell">{t.balance_after != null ? `¥${formatNumber(t.balance_after, 2)}` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
