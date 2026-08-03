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
}

export type StrategyDraft = Omit<Strategy, 'id' | 'created_at' | 'updated_at'>;

export interface RuleMeta {
  type: string;
  label: string;
  value_type: 'number' | 'select' | 'multi-select' | 'boolean';
  unit: string;
  operators: RuleOperator[];
  default: number | string | boolean | string[];
  options?: Array<number | string>;
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
