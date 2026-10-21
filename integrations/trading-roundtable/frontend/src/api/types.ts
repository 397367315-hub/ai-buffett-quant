/** 契约模型（contracts/models.schema.json）在前端用到的子集：字段名与枚举值原样，不在前端新增。 */

export interface SourceRef {
  source_id: string; authority_type: string; locator: string | null; content_sha256: string | null;
  retrieved_at: string | null; published_at: string | null; citation: string | null;
}

export interface Evidence {
  evidence_id: string; kind: string; snapshot_id: string | null; symbol: string | null; observed_at: string | null;
  available_at: string | null; first_observed_at: string | null; time_precision: string; availability_basis: string;
  captured_at: string; source_ref: SourceRef; field_path: string | null; value: unknown; unit: string; quality: string;
  related_evidence_ids: string[];
}

export interface SnapshotMetadata {
  quote_date: string | null; session_date: string; captured_at: string; generated_at: string;
  latest_completed_session_date: string | null; timezone: string; feed_precision: string; source_mode: string;
  freshness: string; freshness_reason: string; quality: string; missing_fields: string[]; source_ids: string[];
  universe_policy_id: string; universe_fingerprint: string; price_basis: string; adjustment_policy_version: string | null;
  calendar_version: string; is_synthetic: boolean;
}

export interface InstrumentQuote {
  symbol: string; name: string; board: string; lot_size: number | null; is_st: boolean | null; listed_date: string | null;
  suspended: boolean | null; price_limit_rule_id: string | null; last_price_decimal: string | null;
  prev_close_decimal: string | null; upper_limit_decimal: string | null; lower_limit_decimal: string | null;
  quote_observed_at: string | null; evidence_ids: string[];
}

export interface DailyBar {
  symbol: string; session_date: string; open_decimal: string | null; high_decimal: string | null; low_decimal: string | null;
  close_decimal: string | null; volume_shares: number | null; amount_minor: string | null; bar_status: string;
  adjustment: string; adjustment_as_of: string | null; available_at: string | null; evidence_ids: string[];
}

export interface Metric {
  metric_id: string; symbol: string | null; value: string | null; unit: string; rule_version: string;
  available_at: string | null; evidence_ids: string[];
}

export interface MarketSnapshot {
  schema_version: string; snapshot_id: string; metadata: SnapshotMetadata; instruments: InstrumentQuote[];
  daily_bars: DailyBar[]; metrics: Metric[]; evidence: Evidence[]; conflicts: any[]; corporate_actions: any[];
  fingerprint: string;
}

export interface HealthData {
  status: 'ready' | 'degraded' | 'unavailable'; api_ready: boolean; database_ready: boolean;
  latest_snapshot_id: string | null; quality: string; freshness: string; quote_date: string | null;
  latest_completed_session_date: string | null; missing_inputs: string[]; checked_at: string; enabled_phases: string[];
}

export interface SeatDefinition {
  seat_id: string; legacy_seat_id: string; name: string; skill_path: string; skill_sha256: string; rule_version: string;
  role: string; horizons: string[]; capabilities: string[]; required_inputs: string[]; unavailable_behavior: string;
  enabled: boolean; registry_order: number;
}

export interface SeatRegistry { schema_version: string; registry_version: string; fingerprint: string; seats: SeatDefinition[] }

export interface ConditionResult {
  condition_id: string; description: string; result: 'met' | 'failed' | 'unknown' | 'inapplicable';
  evidence_ids: string[]; missing_inputs: string[];
}

export interface Candidate {
  symbol: string; seat_id: string; horizon: string; verdict: string; condition_ids: string[]; evidence_ids: string[];
  reason: string;
}

export interface SeatRun {
  seat_run_id: string; job_id: string; case_id: string | null; seat_id: string; snapshot_id: string; round: string;
  run_state: string; applicability: string; applicability_reason: string; verdict: string; candidate_count: number | null;
  candidates: Candidate[]; condition_results: ConditionResult[]; evidence_ids: string[]; missing_inputs: string[];
  explanation: string; counter_evidence: string[]; challenged_seat_run_ids: string[]; fingerprints: Record<string, unknown>;
  symbols?: string[]; horizon?: string | null;
  created_at: string; started_at: string | null; finished_at: string | null; error: any;
}

export interface RoleAssignment { seat_id: string; role: 'primary' | 'validator' | 'discipline' | 'context' }

export interface CaseFinal {
  summary: string; agreements: string[]; disagreements: string[]; confirmation_conditions: ConditionResult[];
  invalidation_conditions: ConditionResult[]; missing_inputs: string[]; evidence_ids: string[];
  executed_return_ratio: string | null;
}

export interface RoundtableCase {
  case_id: string; supersedes_case_id: string | null; job_id: string; cycle_id: string; snapshot_id: string; title: string;
  question: string; symbols: string[]; horizon: string; seat_ids: string[]; primary_strategy_id: string;
  role_assignments: RoleAssignment[]; llm_mode: string; provider_id: string | null; model_id: string | null; state: string;
  seat_run_ids: string[]; final_result: CaseFinal | null; created_at: string; finished_at: string | null;
  replay_id: string | null;
}

export interface PlanRule {
  rule_id: string; description: string; rule_origin: string; origin_ref: string; source_version: string;
  rule_version: string; kind: string; symbol: string | null; price_decimal: string | null; threshold_ratio: string | null;
  deadline_session_date: string | null; evidence_ids: string[];
}

export interface DecisionPlan {
  plan_id: string; version: number; previous_version: number | null; change_reason: string; effective_from: string;
  portfolio_id: string | null; basis_portfolio_revision: number | null; case_id: string; snapshot_id: string;
  status: 'draft' | 'frozen'; primary_strategy_id: string; purpose: string; execution_eligibility: string;
  execution_eligibility_reasons: string[]; planned_shares: number | null; title: string; hypothesis: string;
  symbols: string[]; horizon: string; entry_rules: PlanRule[]; exit_rules: PlanRule[]; cancel_rules: PlanRule[];
  time_exit_rule: PlanRule | null; exit_deadline: string | null; invalidation_rules: PlanRule[];
  risk_budget_minor: string | null; max_position_ratio: string | null; benchmark_id: string | null;
  evaluation_window: { start_session_date: string; end_session_date: string }; fee_model_version: string | null;
  created_at: string; frozen_at: string | null; frozen_hash: string | null; replay_id: string | null;
}

export interface PlanLifecycleEvent {
  event_id: string; plan_id: string; plan_version: number; frozen_hash: string; event_type: string; effective_at: string;
  recorded_at: string; snapshot_id: string | null; condition_ids: string[]; journal_event_id: string | null;
  previous_event_id: string | null; reason: string; replay_id: string | null;
}

export interface ReviewResult {
  review_id: string; job_id: string; plan_id: string; plan_version: number; frozen_hash: string | null; review_kind: string;
  preflight_eligibility: string | null; as_of_snapshot_id: string; run_state: string;
  directional_price_return_ratio: string | null; total_return_ratio: string | null; executed_return_ratio: string | null;
  benchmark_return_ratio: string | null; max_drawdown_ratio: string | null; rule_adherence: ConditionResult[];
  corporate_action_evidence_ids: string[]; trade_event_ids: string[]; missing_inputs: string[]; conclusions: string[];
  created_at: string; replay_id: string | null;
}

export interface Position {
  symbol: string; shares: number; available_shares: number; cost_basis_minor: string; last_price_decimal: string | null;
  market_value_minor: string | null; unrealized_pnl_minor: string | null; unrealized_return_ratio: string | null;
  valuation_snapshot_id: string | null;
}

export interface Portfolio {
  portfolio_id: string; mode: 'paper' | 'user_reported'; revision: number; cash_minor: string; positions: Position[];
  market_value_minor: string | null; total_equity_minor: string | null; quality: string; missing_inputs: string[];
  last_event_sequence: number; replay_id: string | null;
}

export interface FeeBreakdown {
  commission_minor: string; stamp_minor: string; transfer_minor: string; total_minor: string; fee_model_version: string;
}

export interface JournalEvent {
  event_id: string; portfolio_id: string; sequence: number; event_type: string; mode: string; effective_at: string;
  recorded_at: string; session_date: string; symbol: string | null; side: 'buy' | 'sell' | null; shares: number | null;
  price_decimal: string | null; gross_amount_minor: string | null; net_cash_delta_minor: string | null;
  fees: FeeBreakdown | null; snapshot_id: string | null; plan_id: string | null; plan_version: number | null;
  corrects_event_id: string | null; correction_action: string | null; corrected_event_type: string | null;
  legacy_trade_id: string | null; note: string; prev_event_hash: string | null; event_hash: string;
  replay_id: string | null; evidence_ids: string[];
}

export interface PortfolioControlEvent {
  event_id: string; portfolio_id: string; control_type: 'cooldown'; event_type: 'enabled' | 'released' | 'expired';
  policy_id: string; policy_version: string; effective_at: string; scheduled_end_at: string | null; recorded_at: string;
  related_control_event_id: string | null; previous_event_id: string | null; reason: string; replay_id: string | null;
}

export interface ReplaySession {
  replay_id: string; job_id: string; state: string; symbols: string[]; start_session_date: string; end_session_date: string;
  cursor_session_date: string; knowledge_cutoff_at: string; strict_as_of: true; revealed: boolean;
  visible_snapshot_id: string | null; calendar_version: string; data_archive_fingerprint: string;
  isolated_portfolio_id: string; created_at: string; revealed_at: string | null;
}

export interface ValidationRun {
  validation_id: string; job_id: string; state: string; target_type: string; target_id: string; target_version: string;
  dataset_fingerprint: string; rule_version: string; calendar_version: string; fee_model_version: string;
  execution_model_version: string; seed: number; window: { start_session_date: string; end_session_date: string };
  split_policy: string;
  metrics: { sample_count: number; executed_trade_count: number; win_rate_ratio: string | null; expectancy_r: string | null;
             max_drawdown_ratio: string | null; excess_return_ratio: string | null;
             directional_price_return_ratio: string | null; execution_return_ratio: string | null };
  limit_notes: string[]; evidence_ids: string[]; created_at: string; finished_at: string | null;
  evaluation_manifest: { manifest_id: string; version: number; content: any; created_at: string; frozen_at: string;
                         frozen_hash: string };
}

export interface CardDeck {
  deck_id: string; version: number; title: string; source_type: 'case' | 'plan' | 'review'; source_id: string;
  source_version: string | null; cards: { card_id: string; front: string; back: string; evidence_ids: string[] }[];
  created_at: string;
}

export interface WorkspaceIndex {
  replay_id: string | null;
  cases: { case_id: string; title: string; state: string; snapshot_id: string; primary_strategy_id: string; symbols: string[];
           horizon: string; supersedes_case_id: string | null; created_at: string; finished_at: string | null }[];
  plans: { plan_id: string; version: number; status: string; title: string; purpose: string; case_id: string;
           symbols: string[]; execution_eligibility: string; primary_strategy_id: string; created_at: string;
           frozen_at: string | null }[];
  portfolios: { portfolio_id: string; mode: string; revision: number; name: string; created_at: string }[];
  replays: { replay_id: string; state: string; start_session_date: string; end_session_date: string;
             cursor_session_date: string; revealed: boolean; created_at: string }[];
  validations: { validation_id: string; state: string; target_type: string; target_id: string; created_at: string;
                 finished_at: string | null }[];
  decks: { deck_id: string; version: number; title: string; source_type: string; source_id: string; created_at: string }[];
  reviews: { review_id: string; plan_id: string; plan_version: number; review_kind: string; run_state: string; created_at: string }[];
  archive: { fingerprint: string; coverage_start: string; coverage_end: string | null; session_count: number | null;
             calendar_version: string; note?: string } | null;
}

export interface ListPage<T> { items: T[]; next_cursor: string | null }
