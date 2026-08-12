export type RuleLogic = 'AND' | 'OR';
export type RuleOperator = 'gt' | 'gte' | 'lt' | 'lte' | 'eq' | 'ne' | 'in' | 'not_in' | 'between';

export interface StrategyRule {
  type: string;
  operator: RuleOperator;
  value: number | string | boolean | string[] | number[];
}

export interface RuleGroup {
  logic: RuleLogic;
  rules: StrategyRule[];
}

export interface ExitConfig {
  stop_loss_pct: number;
  take_profit_pct: number;
  max_holding_days: number;
  rules: StrategyRule[];
}

export interface PositionConfig {
  method: 'equal_weight' | 'kelly' | 'fixed_amount';
  max_holdings: number;
  max_position_pct: number;
  fixed_amount?: number | null;
}

export interface Strategy {
  id: string;
  name: string;
  active: boolean;
  scan_schedule: 'daily' | 'manual';
  created_at: string;
  updated_at: string;
  filter: RuleGroup;
  entry: RuleGroup;
  exit: ExitConfig;
  position: PositionConfig;
  description?: string;
  builtin?: boolean;
  horizon?: 'short' | 'long';
  target_win_rate?: [number, number];
  validation_note?: string;
}

export type StrategyDraft = Omit<
  Strategy,
  'id' | 'created_at' | 'updated_at' | 'description' | 'builtin' | 'horizon' | 'target_win_rate' | 'validation_note'
>;

export interface RuleMeta {
  type: string;
  label: string;
  value_type: 'number' | 'select' | 'multi-select' | 'boolean';
  unit: string;
  operators: RuleOperator[];
  default: number | string | boolean | string[];
  options?: Array<number | string>;
  category?: string;
  source?: string;
  historical_support?: string;
  note?: string;
}

export interface SectorOption {
  code: string;
  name: string;
  type: string;
}

export interface SignalStrategyMatch {
  strategy_id: string;
  strategy_name: string;
  match_score: number;
  matched_rules: string[];
}

export interface TradeSignal {
  signal_id: string;
  strategy_id: string;
  strategy_name: string;
  strategy_ids?: string[];
  strategy_matches?: SignalStrategyMatch[];
  type: 'buy' | 'sell';
  stock_code: string;
  stock_name: string;
  match_score: number;
  price: number;
  change_pct: number;
  turnover: number;
  pe_ttm: number | null;
  sector?: string;
  matched_rules: string[];
  unmatched_rules: string[];
  unavailable_rules?: string[];
  rule_audit?: {
    counts: Record<string, number>;
    complete: boolean;
  };
  risk_flags?: {
    level: string;
    hard_blocks: string[];
    warnings: string[];
    missing: string[];
  };
  generated_at: string;
}

export interface SignalSnapshot {
  generated_at: string | null;
  data_date: string | null;
  source: string;
  is_realtime: boolean;
  stale: boolean;
  warning: string | null;
  scanned_stocks: number;
  technical_candidate_count?: number;
  technical_evaluated_count?: number;
  technical_history_coverage?: number;
  technical_truncated?: boolean;
  strategy_count?: number;
  feature_updated_at?: string | null;
  feature_coverage?: {
    requested_fields: string[];
    datasets: Record<string, {
      label: string;
      covered?: number;
      total?: number;
      coverage_pct?: number;
      available?: boolean;
    }>;
    warnings: string[];
    missing_policy: string;
  };
  signals: TradeSignal[];
}

export interface BackgroundJob {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  progress: number;
  phase: string;
  message: string;
  error?: string | null;
  result?: Record<string, unknown> | null;
  already_running?: boolean;
}

export interface FQEHolding {
  code: string;
  name: string;
  industry: string;
  score?: number;
  peg?: number;
  roe_ttm?: number;
  ocf_to_profit_ttm?: number;
  debt_ratio?: number;
  market_cap_yi?: number;
  pe_ttm?: number;
  alpha_raw?: number;
  alpha_neutral?: number;
  weight: number;
  weight_pct: number;
  engine_type: 'Retail_Light' | 'Institutional_Heavy';
  financial_disclosed_at?: string | null;
  data_warnings?: string[];
}

export interface FQEPortfolio {
  engine_type: 'Retail_Light' | 'Institutional_Heavy';
  label: string;
  count: number;
  holdings: FQEHolding[];
  eligible_count: number;
  candidate_pool_count?: number;
  rejection_counts?: Array<[string, number]>;
  excluded_examples?: Array<{ code: string; name: string; reasons: string[] }>;
  warnings: string[];
  method: string;
  data_quality: {
    status: 'ready' | 'research_only' | 'insufficient' | string;
    auditable: boolean;
    mode?: string;
    notes?: string[];
  };
  optimizer?: {
    gamma: number;
    lower_weight: number;
    upper_weight: number;
    industry_cap: number;
    constraint_audit: {
      weight_sum: number;
      min_weight: number;
      max_weight: number;
      max_industry_weight: number;
      industry_weights: Record<string, number>;
      violations: string[];
    };
  };
  covariance?: {
    available: boolean;
    usable_days?: number;
    stock_count?: number;
    warning?: string | null;
  };
}

export interface FQEResult {
  version: number;
  engine_mode: 'COMPARE_DUAL_ENGINE' | string;
  generated_at: string;
  as_of_date: string;
  data_date?: string | null;
  source: string;
  is_realtime: boolean;
  cache_used: boolean;
  retail_portfolio: FQEPortfolio;
  institutional_portfolio: FQEPortfolio;
  data_contract: Record<string, {
    status: string;
    covered?: number;
    total?: number;
    note?: string;
    formula?: string;
  }>;
  feature_coverage?: Record<string, unknown>;
  reference_coverage?: Record<string, number | string | null>;
  warnings: string[];
  disclaimer: string;
}

export interface FQEDataSyncRun {
  id: number;
  run_id: number;
  sync_mode: 'full' | 'incremental' | string;
  requested_years: number;
  status: 'queued' | 'running' | 'completed' | 'partial' | 'failed' | string;
  stage: string;
  message?: string | null;
  progress: number;
  total_securities: number;
  completed_securities: number;
  master_count: number;
  inactive_count: number;
  valuation_count: number;
  failed_count: number;
  failed_codes: string[];
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
  error?: string | null;
  already_running?: boolean;
}

export interface FQEDataSyncStatus {
  run: FQEDataSyncRun | null;
  coverage: {
    security_total: number;
    currently_listed: number;
    listing_dated: number;
    inactive_total: number;
    inactive_dated: number;
    status_events: number;
    valuation_series: number;
    valuation_percentiles: number;
    current_valuation_series: number;
    current_valuation_percentiles: number;
    valuation_date?: string | null;
  };
}

export interface ResearchFactor {
  id: string;
  name: string;
  category: string;
  version: string;
  formula: string;
  direction: string;
  frequency: string;
  required_fields: string[];
  available_at: string;
  source: string;
  economic_logic: string;
  status: string;
  status_label: string;
  blocker?: string | null;
  registered: boolean;
}

export interface ResearchExperiment {
  id: string;
  name: string;
  family: string;
  cadence: string;
  status: string;
  supported: boolean;
  factor_ids: string[];
  factor_names: string[];
  description: string;
  blockers: string[];
}

export interface ResearchDataset {
  available: boolean;
  dataset_id: string;
  source?: string[];
  date_range?: [string | null, string | null];
  record_count?: number;
  stock_count?: number;
  manifest_hash?: string;
  error?: string;
  warnings?: string[];
  cache_used?: boolean;
  manifest_cache_used?: boolean;
  cache_generated_at?: string | null;
  data_inventory?: Array<{
    key: string;
    label: string;
    status: string;
    record_count: number;
    stock_count: number;
    session_count: number;
    target_sessions: number;
    coverage_pct: number;
    date_range?: [string | null, string | null];
    note?: string;
    exact_sessions?: number;
    derived_sessions?: number;
    complete_sessions?: number;
    observed_sessions?: number;
  }>;
  universe?: {
    status: string;
    historical_membership: boolean;
    note?: string;
    observed_daily_sessions?: number;
    observed_from_daily_bars?: boolean;
  };
  point_in_time?: {
    status: string;
    observation_time?: string;
    available_time_field?: string | null;
    note?: string;
  };
  researchability?: Record<string, string>;
}

export interface ResearchPartition {
  trading_periods: number;
  from: string | null;
  to: string | null;
  total_return: number;
  win_rate: number;
  profit_factor: number;
  max_drawdown: number;
  data_sufficient: boolean;
}

export interface ResearchResult {
  available?: boolean;
  error?: string;
  trading_periods?: number;
  trading_days?: number;
  total_return?: number;
  benchmark_return?: number;
  win_rate?: number;
  max_drawdown?: number;
  sharpe_ratio?: number;
  information_coefficient?: number;
  data_quality?: {
    grade?: string;
    bar_count?: number;
    stock_count?: number;
    candidate_observations?: number;
    warnings?: string[];
  };
  parameter_sensitivity?: Array<{
    lookback_days: number;
    trading_periods: number;
    total_return: number;
    information_coefficient: number;
  }>;
  daily_details?: Array<Record<string, unknown>>;
}

export interface ResearchGate {
  id: string;
  label: string;
  threshold: string;
  actual?: number | string | null;
  passed: boolean;
  reason?: string;
}

export interface ResearchReport {
  report_version: string;
  experiment_id: string;
  experiment: ResearchExperiment;
  status: string;
  promotion_stage: string;
  strategy_lock_hash: string;
  dataset: ResearchDataset;
  parameters: {
    days: number;
    top_n: number;
    lookback_days: number;
    holding_days: number;
    capital: number;
  };
  result: ResearchResult;
  partitions: Record<string, ResearchPartition>;
  stress_tests: Record<string, {
    available: boolean;
    total_return?: number;
    max_drawdown?: number;
    trading_periods?: number;
    note: string;
  }>;
  gates: ResearchGate[];
  audit_log: string[];
  next_actions: string[];
  result_hash: string;
  persistence_warning?: string;
}

export interface ResearchWorkspace {
  version: string;
  generated_at: string;
  factor_catalog: ResearchFactor[];
  factor_summary: {
    total: number;
    by_status: Record<string, number>;
    by_category: Record<string, number>;
  };
  experiments: ResearchExperiment[];
  lifecycle: Array<{ id: string; label: string; description: string }>;
  hard_gates: Array<{ id: string; label: string; threshold: string }>;
  dataset: ResearchDataset;
  latest_report?: ResearchReport | null;
  active_job?: BackgroundJob | null;
  research_contract: Record<string, string>;
}

export interface BacktestTrade {
  date: string;
  signal_date: string;
  action: 'buy' | 'sell';
  stock_code: string;
  stock_name: string;
  price: number;
  shares: number;
  amount: number;
  commission: number;
  tax: number;
  reason: string;
  execution: string;
  profit_pct?: number | null;
}

export interface BacktestResult {
  job_id: string;
  strategy_id: string;
  strategy_name: string;
  available: boolean;
  total_return: number;
  annual_return: number;
  win_rate: number;
  profit_loss_ratio: number;
  max_drawdown: number;
  sharpe_ratio: number;
  trade_count: number;
  completed_trade_count: number;
  passed: boolean;
  candidate_count: number;
  stock_count: number;
  period: { from: string; to: string };
  params: { initial_capital: number; final_value: number; trading_days: number };
  daily_values: Array<{ date: string; value: number; cash: number; holding_count: number }>;
  trades: BacktestTrade[];
  data_quality: { grade: string; audit_eligible: boolean; warnings: string[] };
  execution_rule: string;
}

export interface PaperHolding {
  stock_code: string;
  stock_name: string;
  shares: number;
  cost: number;
  cost_per_share: number;
  buy_date: string;
  current_price: number;
  market_value: number;
  profit_pct: number;
  strategy_ids?: string[];
}

export interface PaperPortfolio {
  account: {
    initial_capital: number;
    available_cash: number;
    total_value: number;
    total_return_pct: number;
  };
  price_source?: string;
  price_updated_at?: string | null;
  price_is_realtime?: boolean;
  price_warning?: string | null;
  holdings: PaperHolding[];
  history: Array<{
    id: string;
    date: string;
    action: 'buy' | 'sell';
    stock_code: string;
    stock_name: string;
    price: number;
    shares: number;
    amount: number;
    commission: number;
    tax: number;
    realized_pnl?: number;
    reason: string;
  }>;
}

export interface ZhabanFactor {
  key: string;
  label: string;
  type: 'number' | 'boolean' | string;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  options?: Array<{ value: string; label: string }>;
}

export interface ZhabanResearch {
  generated_at: string;
  data_date: string | null;
  status: string;
  source: string;
  is_realtime: boolean;
  cache_used: boolean;
  strategy: Record<string, any>;
  market_environment?: Record<string, any>;
  summary?: Record<string, any>;
  candidates: Array<Record<string, any>>;
  events?: Array<Record<string, any>>;
  warnings: string[];
  data_quality: Record<string, any>;
  disclaimer: string;
}

export interface ZhabanBacktest {
  generated_at: string;
  status: string;
  source: string;
  is_realtime: boolean;
  cache_used: boolean;
  strategy: Record<string, any>;
  period: Record<string, any>;
  summary: Record<string, any>;
  annual: Array<Record<string, any>>;
  monthly: Array<Record<string, any>>;
  board_performance: Array<Record<string, any>>;
  cost_sensitivity: Array<Record<string, any>>;
  validation?: Record<string, any>;
  trades: Array<Record<string, any>>;
  daily_values: Array<Record<string, any>>;
  candidate_count: number;
  event_count: number;
  data_quality: Record<string, any>;
  warnings: string[];
  disclaimer: string;
}
