import React from 'react'
import { Loader2 } from 'lucide-react'
import {
  BACKTEST_MARKETS,
  type BacktestMarket,
  type BacktestRunParams,
  type BacktestTargetType,
  type WatchlistGroupOption,
  defaultFiltersForTarget,
  normalizeMarketTarget,
} from '../lib/backtest-run'

interface BacktestRunModalProps {
  title: string
  submitLabel: string
  initialParams: BacktestRunParams
  watchlistGroups: WatchlistGroupOption[]
  watchlistGroupsLoading?: boolean
  submitting: boolean
  submitError?: string | null
  onCancel: () => void
  onSubmit: (params: BacktestRunParams) => void | Promise<void>
}

const inputClass = 'h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:ring-2 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60'
const choiceClass = 'flex min-h-10 items-center gap-2 rounded-md border border-line bg-surface px-3 text-sm font-medium text-ink'

function validate(params: BacktestRunParams, watchlistGroups: WatchlistGroupOption[], watchlistGroupsLoading: boolean): string | null {
  if (!params.start_date || !params.end_date) return '请选择完整的回测日期'
  if (params.start_date > params.end_date) return '开始日期不能晚于结束日期'
  const initialCash = Number(params.initial_cash)
  if (!params.initial_cash.trim() || !Number.isFinite(initialCash) || initialCash <= 0) return '初始资金必须是大于 0 的有限数值'
  if (params.benchmark_code.trim().length > 16) return '基准代码不能超过 16 个字符'
  for (const [label, value] of [['止损', params.stop_loss_pct], ['止盈', params.take_profit_pct], ['移动止损', params.trailing_stop_pct]] as const) {
    if (value.trim() && (!Number.isFinite(Number(value)) || Number(value) < 0)) return `${label}比例必须是非负有限数值`
  }
  if (params.time_stop_days.trim() && (!Number.isInteger(Number(params.time_stop_days)) || Number(params.time_stop_days) <= 0)) {
    return '最大持仓天数必须是正整数'
  }
  if (params.rebalance_mode !== 'disabled' && params.rebalance_mode !== 'ranked') return '请选择有效的调仓模式'
  if (params.rebalance_mode === 'ranked' && params.rebalance_version !== 1 && params.rebalance_version !== 2) return '请选择有效的调仓版本'
  if (!params.max_positions.trim() || !Number.isInteger(Number(params.max_positions)) || Number(params.max_positions) < 0) {
    return '最大持仓数必须是非负整数'
  }
  if (params.rebalance_mode === 'ranked' && params.rebalance_version === 2 && Number(params.max_positions) < 1) {
    return 'v2 调仓时最大持仓数至少为 1'
  }
  if (params.target_type === 'market' && normalizeMarketTarget(params.target_value).length === 0) return '请至少选择一个市场板块'
  if (params.target_type === 'watchlist_group') {
    if (watchlistGroupsLoading) return '正在加载自选股分组'
    const group = typeof params.target_value === 'string' ? params.target_value.trim() : ''
    if (!group || !watchlistGroups.some((option) => option.group_name === group)) return '请选择可用的自选股分组'
  }
  return null
}

export default function BacktestRunModal({ title, submitLabel, initialParams, watchlistGroups, watchlistGroupsLoading = false, submitting, submitError, onCancel, onSubmit }: BacktestRunModalProps) {
  const [params, setParams] = React.useState<BacktestRunParams>(() => ({ ...initialParams, preservedConfig: { ...initialParams.preservedConfig } }))
  const titleId = React.useId()
  const errorId = React.useId()
  const firstInputRef = React.useRef<HTMLInputElement>(null)
  const previousFocusRef = React.useRef<HTMLElement | null>(null)
  const dialogRef = React.useRef<HTMLDivElement>(null)
  const error = validate(params, watchlistGroups, watchlistGroupsLoading)
  const selectedGroup = typeof params.target_value === 'string' ? params.target_value : ''
  const historicalGroupMissing = params.target_type === 'watchlist_group'
    && Boolean(selectedGroup)
    && !watchlistGroups.some((group) => group.group_name === selectedGroup)

  React.useEffect(() => {
    setParams({ ...initialParams, preservedConfig: { ...initialParams.preservedConfig } })
  }, [initialParams])

  React.useEffect(() => {
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const timer = window.setTimeout(() => firstInputRef.current?.focus(), 0)
    return () => {
      window.clearTimeout(timer)
      previousFocusRef.current?.focus()
    }
  }, [])

  React.useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !submitting) onCancel()
      if (event.key !== 'Tab') return
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) ?? [])
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (!focusable.includes(document.activeElement as HTMLElement)) {
        event.preventDefault()
        ;(event.shiftKey ? last : first).focus()
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onCancel, submitting])

  const selectTargetType = (type: BacktestTargetType) => {
    setParams((current) => ({
      ...current,
      target_type: type,
      target_value: type === 'market' ? [] : '',
      ...defaultFiltersForTarget(type),
    }))
  }

  const toggleMarket = (market: BacktestMarket) => {
    setParams((current) => {
      const selected = normalizeMarketTarget(current.target_value)
      const next = selected.includes(market)
        ? selected.filter((value) => value !== market)
        : BACKTEST_MARKETS.filter((value) => value === market || selected.includes(value))
      return { ...current, target_value: next }
    })
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-3 sm:p-6"
      onMouseDown={(event) => { if (event.target === event.currentTarget && !submitting) onCancel() }}
    >
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={error || submitError ? errorId : undefined} className="flex max-h-[calc(100vh-1.5rem)] w-full max-w-xl flex-col overflow-hidden rounded-lg border border-line bg-panel text-ink shadow-xl sm:max-h-[calc(100vh-3rem)]">
        <h2 id={titleId} className="border-b border-line px-5 py-4 text-lg font-semibold">{title}</h2>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <div>
            <span className="mb-1 block text-sm font-medium text-muted">标的范围</span>
            <div className="grid grid-cols-3 gap-2">
              {([['all', '全市场'], ['market', '市场板块'], ['watchlist_group', '自选分组']] as const).map(([type, label]) => (
                <button key={type} type="button" onClick={() => selectTargetType(type)} aria-pressed={params.target_type === type} className={`min-h-10 rounded-md border px-2 text-sm font-medium ${params.target_type === type ? 'border-accent bg-accent text-white' : 'border-line bg-surface text-ink hover:bg-rowHover'}`}>{label}</button>
              ))}
            </div>
          </div>

          {params.target_type === 'market' && (
            <fieldset>
              <legend className="mb-1 text-sm font-medium text-muted">市场板块</legend>
              <div className="grid grid-cols-2 gap-2">
                {BACKTEST_MARKETS.map((market) => (
                  <label key={market} className={choiceClass}>
                    <input type="checkbox" checked={normalizeMarketTarget(params.target_value).includes(market)} onChange={() => toggleMarket(market)} className="h-4 w-4 rounded border-line text-accent focus:ring-accent" />
                    {market}
                  </label>
                ))}
              </div>
            </fieldset>
          )}

          {params.target_type === 'watchlist_group' && (
            <label className="block text-sm font-medium text-muted">
              自选股分组
              <select disabled={watchlistGroupsLoading} value={selectedGroup} onChange={(event) => setParams({ ...params, target_value: event.target.value })} className={`mt-1 ${inputClass}`}>
                <option value="">{watchlistGroupsLoading ? '正在加载分组' : watchlistGroups.length === 0 ? '暂无可用分组' : '请选择'}</option>
                {historicalGroupMissing && <option value={selectedGroup}>{selectedGroup}（已不可用）</option>}
                {watchlistGroups.map((group) => <option key={group.group_name} value={group.group_name}>{group.group_name} ({group.item_count} 只)</option>)}
              </select>
            </label>
          )}

          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <label className={choiceClass}><input type="checkbox" checked={params.exclude_st} onChange={(event) => setParams({ ...params, exclude_st: event.target.checked })} className="h-4 w-4 rounded border-line text-accent focus:ring-accent" />排除 ST</label>
            <label className={choiceClass}><input type="checkbox" checked={params.exclude_loss_pe} onChange={(event) => setParams({ ...params, exclude_loss_pe: event.target.checked })} className="h-4 w-4 rounded border-line text-accent focus:ring-accent" />排除亏损市盈率 PE&lt;=0</label>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="text-sm font-medium text-muted">开始日期<input ref={firstInputRef} type="date" value={params.start_date} onChange={(event) => setParams({ ...params, start_date: event.target.value })} className={`mt-1 ${inputClass}`} /></label>
            <label className="text-sm font-medium text-muted">结束日期<input type="date" value={params.end_date} onChange={(event) => setParams({ ...params, end_date: event.target.value })} className={`mt-1 ${inputClass}`} /></label>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="text-sm font-medium text-muted">初始资金<input type="number" value={params.initial_cash} onChange={(event) => setParams({ ...params, initial_cash: event.target.value })} className={`mt-1 ${inputClass}`} /></label>
            <label className="text-sm font-medium text-muted">基准代码（可选）<input type="text" maxLength={16} value={params.benchmark_code} onChange={(event) => setParams({ ...params, benchmark_code: event.target.value })} placeholder="例如 000300.SH" className={`mt-1 ${inputClass}`} /></label>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="text-sm font-medium text-muted">调仓模式<select value={params.rebalance_mode} onChange={(event) => setParams({ ...params, rebalance_mode: event.target.value as BacktestRunParams['rebalance_mode'], rebalance_version: 1, max_positions: event.target.value === 'disabled' ? '0' : params.max_positions })} className={`mt-1 ${inputClass}`}><option value="disabled">不调仓</option><option value="ranked">按评分调仓</option></select></label>
            <label className="text-sm font-medium text-muted">最大持仓数<input type="number" min={params.rebalance_mode === 'ranked' && params.rebalance_version === 2 ? 1 : 0} step="1" value={params.max_positions} onChange={(event) => setParams({ ...params, max_positions: event.target.value })} placeholder={params.rebalance_mode === 'ranked' && params.rebalance_version === 2 ? '1-100' : '0=不限'} disabled={params.rebalance_mode === 'disabled'} className={`mt-1 ${inputClass}`} /></label>
          </div>

          {params.rebalance_mode === 'ranked' && (
            <div>
              <span className="mb-1 block text-sm font-medium text-muted">调仓版本</span>
              <div className="grid grid-cols-2 gap-2">
                <button type="button" onClick={() => setParams({ ...params, rebalance_version: 1 })} aria-pressed={params.rebalance_version === 1} className={`min-h-10 rounded-md border px-2 text-sm font-medium ${params.rebalance_version === 1 ? 'border-accent bg-accent text-white' : 'border-line bg-surface text-ink hover:bg-rowHover'}`}>v1（旧版）</button>
                <button type="button" onClick={() => setParams({ ...params, rebalance_version: 2, max_positions: params.max_positions === '0' ? '10' : params.max_positions })} aria-pressed={params.rebalance_version === 2} className={`min-h-10 rounded-md border px-2 text-sm font-medium ${params.rebalance_version === 2 ? 'border-accent bg-accent text-white' : 'border-line bg-surface text-ink hover:bg-rowHover'}`}>v2（每周组合）</button>
              </div>
            </div>
          )}

          {params.rebalance_mode === 'ranked' && params.rebalance_version === 2 && (
            <div className="rounded-md border border-line bg-surface p-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="text-sm">
                  <span className="font-medium text-muted">调仓频率</span>
                  <p className="mt-0.5 text-ink">每周</p>
                </div>
                <div className="text-sm">
                  <span className="font-medium text-muted">权重方式</span>
                  <p className="mt-0.5 text-ink">等权</p>
                </div>
                <div className="text-sm">
                  <span className="font-medium text-muted">排名缓冲</span>
                  <p className="mt-0.5 text-ink">20%</p>
                </div>
                <div className="text-sm">
                  <span className="font-medium text-muted">评分有效期</span>
                  <p className="mt-0.5 text-ink">5个交易日</p>
                </div>
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="text-sm font-medium text-muted">止损 %（可选）<input type="number" min="0" step="0.1" value={params.stop_loss_pct} onChange={(event) => setParams({ ...params, stop_loss_pct: event.target.value })} className={`mt-1 ${inputClass}`} /></label>
            <label className="text-sm font-medium text-muted">止盈 %（可选）<input type="number" min="0" step="0.1" value={params.take_profit_pct} onChange={(event) => setParams({ ...params, take_profit_pct: event.target.value })} className={`mt-1 ${inputClass}`} /></label>
            <label className="text-sm font-medium text-muted">移动止损 %（可选）<input type="number" min="0" step="0.1" value={params.trailing_stop_pct} onChange={(event) => setParams({ ...params, trailing_stop_pct: event.target.value })} className={`mt-1 ${inputClass}`} /></label>
            <label className="text-sm font-medium text-muted">最大持仓天数（可选）<input type="number" min="1" step="1" value={params.time_stop_days} onChange={(event) => setParams({ ...params, time_stop_days: event.target.value })} className={`mt-1 ${inputClass}`} /></label>
          </div>
          {(error || submitError) && <p id={errorId} role="alert" className="text-sm text-red-600">{error ?? submitError}</p>}
        </div>
        <div className="flex gap-3 border-t border-line px-5 py-4">
          <button type="button" onClick={onCancel} disabled={submitting} className="h-10 flex-1 rounded-md border border-line px-4 text-sm font-semibold text-ink hover:bg-rowHover disabled:cursor-not-allowed disabled:opacity-60">取消</button>
          <button type="button" onClick={() => void onSubmit(params)} disabled={submitting || Boolean(error)} className="inline-flex h-10 flex-1 items-center justify-center gap-2 rounded-md bg-accent px-4 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60">
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}{submitting ? '提交中' : submitLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
