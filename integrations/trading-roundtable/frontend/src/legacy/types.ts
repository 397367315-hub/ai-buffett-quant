/** 与业务 API 之间的唯一数据契约。禁止在视图组件中自行生成交易信号。 */
export interface FunnelStage {
  id: string;
  title: string;
  shortTitle: string;
  count: number;
  note: string;
  conditions: string[];
  logic: string;
}

export type TradeGate = 'OPEN' | 'CLOSED' | 'MIXED' | 'WEAK' | 'NO_SIGNAL' | 'UNCONFIGURED';
export type SeatRunState = 'IDLE' | 'RUNNING' | 'SUCCESS' | 'ERROR';

export interface StrategySeat {
  id: string;
  name: string;
  subtitle: string;
  version: string;
  gate: TradeGate;
  runState: SeatRunState;
  count: number | null;
  keywords: string[];
  reasoning: string;
  portrait?: string;
  configured: boolean;
  updatedAt?: string;
  /** 这个席位自己筛出来的票；审查席为空数组。 */
  picks?: SeatPick[];
}

export interface MarketGate {
  date: string;
  primary: {
    source: string; open: boolean; state: string; reason: string;
    metrics: Record<string, number | null>;
  };
  secondary: { source: string; state: string; note: string };
  backtest: { excess: number; t: number; days: number; window: string };
  note?: string;
}

export interface DataStatus {
  quote_date: string | null;
  session_date: string | null;
  generated_at: string | null;
  latest_completed_session_date: string | null;
  timezone: string;
  feed_precision: string;
  source_mode: string;
  freshness: 'fresh' | 'stale' | 'unknown';
  freshness_reason: string;
  quality: 'complete' | 'partial' | 'unavailable';
  quality_reason?: string;
}

export interface UniverseInfo { id: string; label: string; counts: Record<string, number> }

export interface QuantPageData {
  universe?: UniverseInfo;
  /** 最终候选池中去重后的股票数。*/
  stages: FunnelStage[];
  seats: StrategySeat[];
  /** 是否为真实后端返回的数据。 */
  source: 'demo' | 'live' | 'unavailable';
  asOf?: string;
  candidates?: Candidate[];
  poolCounts?: Record<PoolKey, number>;
  backtest?: Backtest;
  /** 全市场报价快照：代码 → [名称, 最新价, 昨收]。个人池估值用它。 */
  marketGate?: MarketGate;
  dataStatus?: DataStatus;
  quotes?: Record<string, [string, number, number]>;
  quoteDate?: string;
}

/* ── 以下为本仓新增：候选池与回测（与后端 panel/refresh.py 对齐） ── */

export interface KBar { d: string; o: number; h: number; l: number; c: number; }

export interface TradeRules {
  entry_mode?: 'pivot' | 'valuation' | 'structure' | 'board' | null;
  entry_pull: number | null;
  entry_break: number | null;
  pivot?: number | null;
  chase_pct?: number | null;
  add_rule?: string | null;
  stop: number | null;
  risk_pct: number | null;
  risk_flag: string | null;
  tgt1: number | null;
  tgt2: number | null;
  target_note?: string | null;
  time_exit: string | null;
  atr: number | null;
  atr_mult: number | null;
}

export interface CandidateStats {
  ret5: number | null; ret20: number | null; dev20: number;
  vr: number | null; amp20: number; flow5: number; flow20: number;
  dist_hh: number; ma_align: string; up_days10: number;
}

export type PoolKey = 'short' | 'swing' | 'value';

export interface Candidate {
  code: string;
  name: string;
  pool: PoolKey;
  poolLabel: string;
  seat: string;
  /** 同时命中这只票的全部席位。 */
  seats?: string[];
  role: string;
  tags?: string[];
  risks?: string[];
  stage: string | null;
  state10: string[];
  kline: KBar[];
  flow: (number | null)[];
  stats: CandidateStats | null;
  rules: TradeRules | null;
}

/** 席位抽屉里的单只票，规则按该席位自己的口径。 */
export interface SeatPick {
  code: string;
  name: string;
  pool: PoolKey;
  poolLabel: string;
  role: string;
  close: number | null;
  stop: number | null;
  risk_pct: number | null;
  risk_flag: string;
  stage: string | null;
  rules?: TradeRules | null;
}

export interface BacktestRow {
  seat: string; days: number; avgCand: number; excess: number; win: number; t: number;
  horizon?: number;   // 5 日或 60 日
  note?: string;
}

export interface BacktestGateRow { seat: string; days: number; excess: number; t: number; }

export interface Backtest {
  status?: 'legacy_unverified' | 'validated';
  status_note?: string;
  window: string; baseline: number; note: string;
  rows: BacktestRow[];
  gate?: BacktestGateRow[];
  years: { year: string; values: number[] }[];
  yearCols: string[];
  verdict: string;
}
