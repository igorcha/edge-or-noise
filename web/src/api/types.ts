// Hand-written types for the API payloads (api/backtests/views.py, services.py).

export type Spec = Record<string, unknown> & {
  instrument: string
  timeframe: string
  direction: 'long' | 'short'
}

export type Project = {
  id: number
  name: string
  instrument: string
  timeframe: string
  holdout_boundary: string | null
  trial_count: number
  exam_used: boolean
  locked_spec_hash: string | null
  is_demo: boolean
  created_at: string
}

export type VerdictLabel = 'probably noise' | "can't tell yet" | 'promising'

export type TrialSummary = {
  id: number
  project: number
  number: number
  stage: 'development' | 'holdout'
  spec_hash: string
  summary: string
  created_at: string
  n: number | null
  expectancy_R: number | null
  verdict: VerdictLabel | null
  baseline_beaten: number | null
}

export type Metrics = {
  n: number
  expectancy_R: number | null
  sd_R: number | null
  se_R: number | null
  win_rate: number | null
  profit_factor: number | null
  max_drawdown_R: number | null
  total_R: number
  cost_share: number | null
  avg_bars_held: number | null
  exit_mix: Record<string, number>
  mae_R_median: number | null
  mfe_R_median: number | null
}

export type Verdict = {
  label: VerdictLabel
  stage: 'development' | 'holdout'
  n: number
  n_trials: number
  expectancy_R: number | null
  expectancy_ci: [number, number] | null
  se_R: number | null
  baseline_beaten: number | null
  edge_over_baseline_R: number | null
  sharpe_per_trade: number | null
  expected_max_sharpe: number | null
  deflated_sharpe: number | null
  trades_needed: number | null
  reasons: string[]
}

export type Baseline = {
  n_runs: number
  n_signals: number
  beaten: number | null
  expectancy_pct: { p5: number | null; p50: number | null; p95: number | null }
  trade_count_median: number
  curves: { p5: number[]; p50: number[]; p95: number[] }
}

export type CostStress = {
  factor: number
  base_expectancy_R: number | null
  stressed_expectancy_R: number | null
  survives: boolean | null
  metrics: Metrics
}

export type Trade = {
  direction: 1 | -1
  signal_time: string
  entry_time: string
  exit_time: string
  entry: number
  exit: number
  stop: number
  target: number
  exit_reason: 'stop' | 'target' | 'time' | 'end'
  R: number
  cost_R: number
  mae_R: number
  mfe_R: number
  bars_held: number
  signal_bar: number
  entry_bar: number
  exit_bar: number
  [geometry: string]: number | string | null  // setup geometry, e.g. "gap.top"
}

export type Trial = TrialSummary & {
  spec: Spec
  card: string
  metrics: Metrics
  verdict: Verdict
  baseline: Baseline
  stress: CostStress | null
  skipped: Record<string, number>
  trades: Trade[]
}

export type RunResult = Trial & { is_new: boolean }

export type Card = { sections: Record<string, string>; text: string }

export type Preview = {
  spec: Spec
  spec_hash: string
  card: Card
  trial: { existing: number | null; next: number }
}

export type ParamSchema = {
  type?: string
  enum?: string[]
  const?: string
  anyOf?: ParamSchema[]
  items?: ParamSchema
  default?: unknown
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
}

export type BlockInfo = {
  name: string
  description: string
  aliases: string[]
  params: { properties: Record<string, ParamSchema> }
  inputs: Record<string, string>
  outputs: Record<string, string>
}

export type Catalog = {
  catalog: { blocks: BlockInfo[]; unsupported: Record<string, string> }
  spec_schema: unknown
  examples: Record<string, Spec>
  instruments: string[]
  timeframes: string[]
}

export type ParseStatus = 'ready' | 'questions' | 'unsupported' | 'invalid'

export type ParseResult = {
  status: ParseStatus
  spec: Spec | null
  draft: Record<string, unknown> | null
  assumptions: string[]
  defaults: string[]
  questions: string[]
  unsupported: { concept: string; reason: string }[]
  error: string | null
  card: Card | null
  attempts: { error: string | null; seconds: number; tokens: number | null }[]
  spec_hash?: string
  trial?: Preview['trial']
}

export type BarsWindow = {
  first_bar: number
  bars: { bar: number; time: number; open: number; high: number; low: number; close: number }[]
  trade: Trade
  timeframe: string
}
