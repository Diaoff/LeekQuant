export const BACKTEST_MARKETS = ['主板', '创业板', '科创板', '北交所'] as const

export type BacktestMarket = typeof BACKTEST_MARKETS[number]
export type BacktestTargetType = 'all' | 'market' | 'watchlist_group'
export type RebalanceMode = 'disabled' | 'ranked'

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
    benchmark_code: '',
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
