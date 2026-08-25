export const BACKTEST_MARKETS = ['主板', '创业板', '科创板', '北交所'] as const

export type BacktestMarket = typeof BACKTEST_MARKETS[number]

export const INDEX_OPTIONS = [
  { code: '', label: '不使用' },
  { code: '000300.SH', label: '沪深300' },
  { code: '000001.SH', label: '上证指数' },
  { code: '000016.SH', label: '上证50' },
  { code: '000688.SH', label: '科创50' },
  { code: '000905.SH', label: '中证500' },
  { code: '000852.SH', label: '中证1000' },
  { code: '399001.SZ', label: '深证成指' },
  { code: '399005.SZ', label: '中小100' },
  { code: '399006.SZ', label: '创业板指' },
  { code: '399330.SZ', label: '深证100' },
] as const
export type BacktestTargetType = 'all' | 'market' | 'watchlist_group'
export type RebalanceMode = 'disabled' | 'ranked'
export type StrategyMode = 'signal' | 'bull_bear'

export interface BacktestRunParams {
  start_date: string
  end_date: string
  initial_cash: string
  benchmark_code: string
  target_type: BacktestTargetType
  target_value: string | BacktestMarket[]
  exclude_st: boolean
  exclude_loss_pe: boolean
  stop_loss_pct: string
  take_profit_pct: string
  trailing_stop_pct: string
  time_stop_days: string
  rebalance_mode: RebalanceMode
  rebalance_version: number
  rebalance_frequency: string
  weighting_method: string
  rank_buffer_pct: string
  score_max_age_sessions: string
  max_positions: string
  max_daily_buys: string
  defensive_enabled: boolean
  dynamic_tp_sl_enabled: boolean
  tp_sl_benchmark_code: string
  up_stop_loss_pct: string
  up_take_profit_pct: string
  up_trailing_stop_pct: string
  up_trailing_activation_pct: string
  up_time_stop_days: string
  down_stop_loss_pct: string
  down_take_profit_pct: string
  down_trailing_stop_pct: string
  down_trailing_activation_pct: string
  down_time_stop_days: string
  neutral_stop_loss_pct: string
  neutral_take_profit_pct: string
  neutral_trailing_stop_pct: string
  neutral_trailing_activation_pct: string
  neutral_time_stop_days: string
  strategy_mode: StrategyMode
  bull_pool_group_name: string
  bull_confirm_days: string
  bull_smooth_days: string
  preservedConfig: Record<string, unknown>
}

export interface WatchlistGroupOption {
  group_name: string
  item_count: number
}

export interface HistoricalBacktestResult {
  start_date?: unknown
  end_date?: unknown
  initial_cash?: unknown
  benchmark_code?: unknown
  target_type?: unknown
  target_value?: unknown
  params_snapshot?: unknown
  performance?: unknown
}

export interface BacktestCreateRequest {
  strategy_id: number
  start_date: string
  end_date: string
  initial_cash: number
  benchmark_code: string | null
  config?: Record<string, unknown>
  target_type: BacktestTargetType
  target_value: string | BacktestMarket[] | null
  exclude_st: boolean
  exclude_loss_pe: boolean
}

export interface BatchBacktestCreateRequest extends Omit<BacktestCreateRequest, 'strategy_id'> {
  strategy_ids: number[]
}

const VISIBLE_CONFIG_KEYS = [
  'stop_loss_pct',
  'take_profit_pct',
  'trailing_stop_pct',
  'time_stop_days',
  'rebalance_mode',
  'max_positions',
  'max_daily_buys',
  'rebalance_version',
  'rebalance_frequency',
  'weighting_method',
  'rank_buffer_pct',
  'score_max_age_sessions',
] as const

function record(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>
  if (typeof value === 'string') {
    try {
      const parsed: unknown = JSON.parse(value)
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {}
    } catch {
      return {}
    }
  }
  return {}
}

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function displayNumber(value: unknown): string {
  const parsed = finiteNumber(value)
  return parsed === null ? '' : String(parsed)
}

function displayRatio(value: unknown): string {
  const parsed = finiteNumber(value)
  if (parsed === null) return ''
  return Number((parsed * 100).toPrecision(12)).toString()
}

function firstDefined(...values: unknown[]): unknown {
  return values.find((value) => value !== undefined && value !== null)
}

function targetType(value: unknown): BacktestTargetType | null {
  return value === 'all' || value === 'market' || value === 'watchlist_group' ? value : null
}

export function defaultFiltersForTarget(type: BacktestTargetType) {
  const enabled = type === 'all' || type === 'market'
  return { exclude_st: enabled, exclude_loss_pe: enabled }
}

export function normalizeMarketTarget(value: unknown): BacktestMarket[] {
  const values = Array.isArray(value) ? value : typeof value === 'string' ? [value] : []
  return BACKTEST_MARKETS.filter((market) => values.includes(market))
}

export function createDefaultBacktestRunParams(now = new Date()): BacktestRunParams {
  const end = new Date(now)
  const start = new Date(now)
  start.setFullYear(start.getFullYear() - 1)
  return {
    start_date: start.toISOString().split('T')[0],
    end_date: end.toISOString().split('T')[0],
    initial_cash: '100000',
    benchmark_code: '000300.SH',
    target_type: 'all',
    target_value: '',
    ...defaultFiltersForTarget('all'),
    stop_loss_pct: '',
    take_profit_pct: '',
    trailing_stop_pct: '',
    time_stop_days: '',
    rebalance_mode: 'disabled',
    rebalance_version: 1,
    rebalance_frequency: 'weekly',
    weighting_method: 'equal',
    rank_buffer_pct: '0.20',
    score_max_age_sessions: '5',
    max_positions: '0',
    max_daily_buys: '0',
    defensive_enabled: false,
    dynamic_tp_sl_enabled: true,
    tp_sl_benchmark_code: '',
    up_stop_loss_pct: '8',
    up_take_profit_pct: '25',
    up_trailing_stop_pct: '10',
    up_trailing_activation_pct: '10',
    up_time_stop_days: '30',
    down_stop_loss_pct: '5',
    down_take_profit_pct: '10',
    down_trailing_stop_pct: '6',
    down_trailing_activation_pct: '6',
    down_time_stop_days: '15',
    neutral_stop_loss_pct: '6',
    neutral_take_profit_pct: '15',
    neutral_trailing_stop_pct: '8',
    neutral_trailing_activation_pct: '8',
    neutral_time_stop_days: '20',
    strategy_mode: 'signal',
    bull_pool_group_name: '沪深300联动',
    bull_confirm_days: '3',
    bull_smooth_days: '2',
    preservedConfig: {},
  }
}

export function applyLastBacktestRiskParams(
  defaults: BacktestRunParams,
  lastBacktest: HistoricalBacktestResult | null,
): BacktestRunParams {
  if (!lastBacktest) return defaults
  const mapped = mapHistoricalBacktestRun(lastBacktest)
  return {
    ...defaults,
    stop_loss_pct: mapped.stop_loss_pct,
    take_profit_pct: mapped.take_profit_pct,
    trailing_stop_pct: mapped.trailing_stop_pct,
    time_stop_days: mapped.time_stop_days,
  }
}

export function mapHistoricalBacktestRun(result: HistoricalBacktestResult): BacktestRunParams {
  const defaults = createDefaultBacktestRunParams()
  const snapshot = record(result.params_snapshot)
  const snapshotTarget = record(snapshot.target)
  const performance = record(result.performance)
  const config = record(snapshot.config)
  const nestedRisk = record(config.risk_config)
  const performanceRisk = record(performance.risk_config)

  const mappedTargetType = targetType(snapshotTarget.type)
    ?? targetType(snapshot.target_type)
    ?? targetType(result.target_type)
    ?? 'all'
  const rawTargetValue = firstDefined(
    snapshotTarget.value,
    snapshot.target_value,
    result.target_value,
  )
  const mappedTargetValue = mappedTargetType === 'market'
    ? normalizeMarketTarget(rawTargetValue)
    : mappedTargetType === 'watchlist_group' && typeof rawTargetValue === 'string'
      ? rawTargetValue
      : ''
  const filterDefaults = defaultFiltersForTarget(mappedTargetType)
  const filters = record(snapshot.filters)

  const riskValue = (key: 'stop_loss_pct' | 'take_profit_pct' | 'trailing_stop_pct') =>
    firstDefined(config[key], nestedRisk[key], performanceRisk[key])
  const configValue = (key: string) =>
    firstDefined(config[key], nestedRisk[key], performanceRisk[key])
  const mappedRebalance = configValue('rebalance_mode') === 'ranked' ? 'ranked' : 'disabled'
  const rawVersion = configValue('rebalance_version')
  const mappedRebalanceVersion = rawVersion === 2 || rawVersion === '2' ? 2 : 1
  const startDate = firstDefined(result.start_date, snapshot.start_date)
  const endDate = firstDefined(result.end_date, snapshot.end_date)
  const initialCash = firstDefined(result.initial_cash, snapshot.initial_cash)
  const benchmarkCode = firstDefined(result.benchmark_code, snapshot.benchmark_code)

  return {
    ...defaults,
    start_date: typeof startDate === 'string' && startDate ? startDate : defaults.start_date,
    end_date: typeof endDate === 'string' && endDate ? endDate : defaults.end_date,
    initial_cash: displayNumber(initialCash) || defaults.initial_cash,
    benchmark_code: typeof benchmarkCode === 'string' ? benchmarkCode : '',
    target_type: mappedTargetType,
    target_value: mappedTargetValue,
    exclude_st: typeof filters.exclude_st === 'boolean'
      ? filters.exclude_st
      : typeof snapshot.exclude_st === 'boolean' ? snapshot.exclude_st : filterDefaults.exclude_st,
    exclude_loss_pe: typeof filters.exclude_loss_pe === 'boolean'
      ? filters.exclude_loss_pe
      : typeof snapshot.exclude_loss_pe === 'boolean' ? snapshot.exclude_loss_pe : filterDefaults.exclude_loss_pe,
    stop_loss_pct: displayRatio(riskValue('stop_loss_pct')),
    take_profit_pct: displayRatio(riskValue('take_profit_pct')),
    trailing_stop_pct: displayRatio(riskValue('trailing_stop_pct')),
    time_stop_days: displayNumber(configValue('time_stop_days')),
    rebalance_mode: mappedRebalance,
    rebalance_version: mappedRebalanceVersion,
    rebalance_frequency: 'weekly',
    weighting_method: 'equal',
    rank_buffer_pct: '0.20',
    score_max_age_sessions: '5',
    max_positions: displayNumber(configValue('max_positions')) || '0',
    max_daily_buys: displayNumber(configValue('max_daily_buys')) || '0',
    defensive_enabled: Boolean(record(config.defensive_switch).enabled),
    strategy_mode: (config.strategy_mode === 'bull_bear' ? 'bull_bear' : 'signal') as StrategyMode,
    bull_pool_group_name: typeof config.bull_pool_group_name === 'string' ? config.bull_pool_group_name : '沪深300联动',
    bull_confirm_days: displayNumber(configValue('bull_confirm_days')) || '3',
    bull_smooth_days: displayNumber(configValue('bull_smooth_days')) || '2',
    preservedConfig: { ...config },
  }
}

function buildConfig(params: BacktestRunParams): Record<string, unknown> {
  const config: Record<string, unknown> = { ...params.preservedConfig }
  const nestedRisk = record(config.risk_config)
  const nextNestedRisk = { ...nestedRisk }

  for (const key of VISIBLE_CONFIG_KEYS) {
    delete config[key]
    delete nextNestedRisk[key]
  }
  if (Object.keys(nestedRisk).length > 0) {
    if (Object.keys(nextNestedRisk).length > 0) config.risk_config = nextNestedRisk
    else delete config.risk_config
  }

  for (const key of ['stop_loss_pct', 'take_profit_pct', 'trailing_stop_pct'] as const) {
    const value = finiteNumber(params[key])
    if (params[key].trim() && value !== null) config[key] = value / 100
  }
  const timeStopDays = finiteNumber(params.time_stop_days)
  if (params.time_stop_days.trim() && timeStopDays !== null) config.time_stop_days = timeStopDays
  if (params.rebalance_mode !== 'disabled') {
    config.rebalance_mode = params.rebalance_mode
    config.rebalance_version = params.rebalance_version
    if (params.rebalance_version === 2) {
      config.rebalance_frequency = params.rebalance_frequency
      config.weighting_method = params.weighting_method
      config.rank_buffer_pct = params.rank_buffer_pct
      config.score_max_age_sessions = params.score_max_age_sessions
    }
  }
  const maxPositions = finiteNumber(params.max_positions)
  if (params.rebalance_mode !== 'disabled' && maxPositions !== null && maxPositions > 0) {
    config.max_positions = maxPositions
  }
  const maxDailyBuys = finiteNumber(params.max_daily_buys)
  if (maxDailyBuys !== null && maxDailyBuys > 0) {
    config.max_daily_buys = maxDailyBuys
  }
  // 动态止盈止损：按市场状态分别配置
  if (params.dynamic_tp_sl_enabled) {
    const dynamic: Record<string, Record<string, number>> = {}
    const stateKeys = ['up', 'down', 'neutral'] as const
    const fieldMap: Array<[string, string]> = [
      ['stop_loss_pct', 'stop_loss_pct'],
      ['take_profit_pct', 'take_profit_pct'],
      ['trailing_stop_pct', 'trailing_stop_pct'],
      ['trailing_activation_pct', 'trailing_activation_pct'],
      ['time_stop_days', 'time_stop_days'],
    ]
    for (const state of stateKeys) {
      const overrides: Record<string, number> = {}
      for (const [paramKey, suffix] of fieldMap) {
        const val = params[`${state}_${suffix}` as keyof BacktestRunParams] as string | undefined
        if (val !== undefined && val.trim()) {
          const num = finiteNumber(val)
          if (num !== null) {
            overrides[paramKey] = suffix === 'time_stop_days' ? num : num / 100
          }
        }
      }
      if (Object.keys(overrides).length > 0) dynamic[state] = overrides
    }
    if (Object.keys(dynamic).length > 0) config.dynamic_tp_sl = dynamic
    // 动态止盈止损独立基准
    if (params.tp_sl_benchmark_code.trim()) {
      config.tp_sl_benchmark_code = params.tp_sl_benchmark_code.trim()
    }
  } else {
    delete config.dynamic_tp_sl
    delete config.tp_sl_benchmark_code
  }

  // 避险切换（跷跷板）：启用时把 defensive_switch 写入 params_snapshot，
  // 后端据此在回测时序中监测基准、切换避险资产。
  // 避险库由人工维护、全部启用、等权买入（不计算质量分、不限制只数）；
  // benchmark_code：避险判定所用基准（缺省回落到上方“基准代码”）。
  if (params.defensive_enabled) {
    config.defensive_switch = {
      enabled: true,
      benchmark_code: params.benchmark_code?.trim() || undefined,
    }
  } else {
    // 关键：取消勾选必须显式移除 defensive_switch。
    // config 来自 {...params.preservedConfig}（上一次运行/历史回测的完整 config），
    // 若不删除，旧 {enabled:true} 会原样带到后端，导致“取消避险仍自动启用”。
    delete config.defensive_switch
  }

  // 跷跷板择时模式
  if (params.strategy_mode === 'bull_bear') {
    config.strategy_mode = 'bull_bear'
    if (params.bull_pool_group_name.trim()) {
      config.bull_pool_group_name = params.bull_pool_group_name.trim()
    }
    const confirmDays = finiteNumber(params.bull_confirm_days)
    if (confirmDays !== null) config.bull_confirm_days = confirmDays
    const smoothDays = finiteNumber(params.bull_smooth_days)
    if (smoothDays !== null) config.bull_smooth_days = smoothDays
  } else {
    // signal 模式：显式删除，防止 preservedConfig 残留 strategy_mode
    delete config.strategy_mode
    delete config.bull_pool_group_name
    delete config.bull_confirm_days
    delete config.bull_smooth_days
  }

  return config
}

function buildBaseRequest(params: BacktestRunParams): Omit<BacktestCreateRequest, 'strategy_id'> {
  const config = buildConfig(params)
  return {
    start_date: params.start_date,
    end_date: params.end_date,
    initial_cash: Number(params.initial_cash),
    benchmark_code: params.benchmark_code.trim() || null,
    config: Object.keys(config).length > 0 ? config : undefined,
    target_type: params.target_type,
    target_value: params.target_type === 'all' ? null : params.target_value,
    exclude_st: params.exclude_st,
    exclude_loss_pe: params.exclude_loss_pe,
  }
}

export function buildSingleBacktestRequest(strategyId: number, params: BacktestRunParams): BacktestCreateRequest {
  return { strategy_id: strategyId, ...buildBaseRequest(params) }
}

export function buildBatchBacktestRequest(strategyIds: number[], params: BacktestRunParams): BatchBacktestCreateRequest {
  return { strategy_ids: strategyIds, ...buildBaseRequest(params) }
}
