'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  AlertCircle,
  ArrowRight,
  BarChart3,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clock3,
  Database,
  Gauge,
  History,
  Layers3,
  ListChecks,
  Loader2,
  RefreshCw,
  ShieldCheck,
  ShieldAlert,
  Sparkles,
  Target,
  Workflow,
  XCircle,
} from 'lucide-react';
import AddToPersonalPoolButton from '@/components/AddToPersonalPoolButton';
import StockKlineButton from '@/components/StockKlineButton';
import { apiFetch, friendlyApiError } from '@/lib/api';

interface WorkbenchMeta {
  contract_version?: string;
  decision_date: string | null;
  calculated_at: string;
  updated_at: string;
  is_realtime: boolean;
  cache_used: boolean;
  source: string;
  coverage_pct: number;
  confidence_pct: number;
  decision_scope: string;
}

interface ScoreDimension {
  id: string;
  label: string;
  weight: number;
  score: number | null;
  observed: boolean;
  contribution: number | null;
  metrics: Record<string, unknown>;
  evidence: string[];
  method: string;
}

interface MarketState {
  state_code: string;
  state_label: string;
  score: number | null;
  execution_level: string;
  coverage_pct: number;
  confidence_pct: number;
  dimensions: ScoreDimension[];
  version: string;
  missing_policy: string;
}

interface StructureHealth {
  score: number | null;
  status: string;
  coverage_pct: number;
  components: Record<string, number | null>;
  evidence: string[];
  missing: string[];
  method: string;
}

interface VolumePriceAlignment {
  score: number | null;
  status: string;
  coverage_pct: number;
  metrics: Record<string, number | null>;
  evidence: string[];
  missing: string[];
  method: string;
}

interface CrowdingRisk {
  score: number | null;
  status: string;
  feedback: string;
  coverage_pct: number;
  components: Record<string, number | null>;
  evidence: string[];
  missing: string[];
  method: string;
}

interface MarketCognition {
  facts: string[];
  principal_contradiction: { statement: string; evidence: string[]; confidence_pct: number | null };
  dominant_aspect: { statement: string; direction: string; evidence: string[] };
  stage: { code: string; label: string };
  quantitative_changes: Array<{ id: string; label: string; streak: number; status: string; evidence: string }>;
  qualitative_shift: { status: string; evidence: string[] };
  practice_hypothesis: { statement: string; validation_window: string; falsification: string[] };
  final_action: 'execute' | 'caution' | 'observe' | 'no_trade';
  action_label: string;
  method: string;
}

interface StrategyHealth {
  id: string;
  name: string;
  state: string;
  health_score: number | null;
  metrics: {
    sample_count: number;
    run_count: number;
    win_rate_pct: number | null;
    expectancy: number | null;
    profit_factor: number | null;
    max_drawdown_amount: number | null;
    max_loss_streak: number;
    out_of_sample: number | null;
  };
  reason: string;
  evidence: string[];
  missing: string[];
}

interface HeadlineMetrics {
  sentiment_temperature: number | null;
  market_amount: number | null;
  up_count: number | null;
  down_count: number | null;
  up_down_ratio: number | null;
  limit_up: number | null;
  limit_down: number | null;
  failed_limit_rate: number | null;
  main_line: string | null;
}

interface AIJudgement {
  market_summary: string;
  key_evidence: string[];
  dominant_sectors: string[];
  preferred_strategies: string[];
  avoid_conditions: string[];
  conclusion: string;
  confidence_pct: number;
  note: string;
}

type StrategyStatus = 'allowed' | 'limited' | 'forbidden';

interface StrategyItem {
  id: string;
  name: string;
  status: StrategyStatus;
  priority: number;
  max_position_pct: number;
  reason: string;
  href: string;
}

interface StrategySelector {
  conclusion: string;
  max_total_position_pct: number;
  strategies: StrategyItem[];
  allowed: string[];
  limited: string[];
  forbidden: string[];
  loss_alert: {
    warning?: boolean;
    consecutive_losses?: number;
    reason?: string;
  };
  policy: string;
}

interface MainLine {
  rank: number;
  name: string;
  classification: string;
  lifecycle: string;
  strength_score: number | null;
  breadth: number | null;
  change_pct: number | null;
  main_net_inflow: number | null;
  member_count: number | null;
  evidence: string;
  leader: {
    code: string;
    name: string;
    price: number | null;
    change_pct: number | null;
    boards: number | null;
    heat_status: string;
  };
  risk_flags: string[];
}

interface Candidate {
  code: string;
  name: string;
  sector: string;
  price: number | null;
  change_pct: number | null;
  score: number | null;
  confidence_pct: number | null;
  score_breakdown: {
    market_fit?: number | null;
    sector_strength?: number | null;
    trend?: number | null;
    volume_price?: number | null;
    relative_strength?: number | null;
    capital?: number | null;
    risk_penalty?: number | null;
  };
  score_method: string;
  strategy: string;
  pool: string;
  status: string;
  execution_eligible: boolean;
  stale: boolean;
  data_date: string | null;
  why_selected: string[];
  why_not_full: string[];
  abandon_conditions: string[];
  source: string;
}

interface DailyShortTermRecommendation {
  rank: number;
  code: string;
  name: string;
  sector: string;
  price: number | null;
  change_pct: number | null;
  volume_ratio: number | null;
  turnover: number | null;
  main_net_inflow: number | null;
  market_cap: number | null;
  score: number | null;
  confidence_pct: number | null;
  score_breakdown: {
    market_sentiment?: number | null;
    market_fit?: number | null;
    sector_strength?: number | null;
    capital?: number | null;
    profitability?: number | null;
    risk_safety?: number | null;
    volume_ratio?: number | null;
    trend?: number | null;
  };
  profitability: {
    status?: string;
    roe?: number | null;
    pe?: number | null;
    revenue_growth?: number | null;
    deducted_profit_growth?: number | null;
    disclosed_at?: string | null;
  };
  risk: string;
  reasons: string[];
  invalidation_conditions: string[];
  status: string;
  data_date: string | null;
  source: string;
  is_realtime: boolean;
}

interface DailyShortTermRecommendations {
  available: boolean;
  horizon: string;
  data_date: string | null;
  market_action: string;
  candidates: DailyShortTermRecommendation[];
  universe_count: number;
  eligible_count: number;
  financial_cache_count: number;
  warnings: string[];
  method: string;
  rule: string;
  source: string;
}

interface ExecutionPhase {
  id: string;
  label: string;
  scheduled_at: string;
  status: string;
  display_status: string;
  data_date: string | null;
  candidate_count: number;
  message: string;
  run_id: number | null;
}

interface DecisionSnapshot {
  id: number;
  decision_date: string;
  phase: string;
  phase_label: string;
  snapshot_hash: string;
  is_realtime: boolean;
  validation_status: string;
  captured_at: string | null;
  evidence: string[];
}

interface DecisionCandidate2026 {
  code: string;
  name: string;
  sector: string;
  price: number | null;
  change_pct: number | null;
  score: number | null;
  state_label: string;
  data_coverage_pct: number;
  beta_alpha: {
    market_beta_pct: number | null;
    sector_beta_pct: number | null;
    individual_alpha_pct: number | null;
    alpha_score: number | null;
    detachment: string;
    method: string;
  };
  fundamental: Record<string, unknown> & { score?: number | null; roe?: number | null; pe?: number | null };
  valuation: { pe: number | null; score: number | null; note: string };
  fund_behaviour: { code: string; label: string; supports_price: boolean | null };
  emotion: {
    score: number | null;
    label: string;
    coverage_pct: number;
    boundary: string;
  };
  trade_structure: { label: string; technical_score: number | null };
  execution: {
    level: 'ALERT' | 'PREPARE' | 'EXECUTE' | 'EXCLUDE';
    label: string;
    passed_count: number;
    observed_count: number;
    conditions: Array<{ key: string; label: string; passed: boolean; observed: boolean }>;
  };
  why_strong: string[];
  why_not_buy: string[];
  trigger_conditions: string[];
  invalidation_conditions: string[];
  detail_href: string;
  source: string;
}

interface Decision2026 {
  version: string;
  positioning: string;
  market_regime: {
    code: string;
    label: string;
    score: number | null;
    structure_score: number | null;
    crowding_score: number | null;
    evidence: string[];
  };
  trading_permission: {
    code: 'ALLOW' | 'CAUTION' | 'OBSERVE' | 'BLOCK';
    label: string;
    allows_new_position: boolean;
    max_total_position_pct: number;
    reasons: string[];
    rule: string;
  };
  opportunity_density: {
    score: number | null;
    label: string;
    coverage_pct: number;
    candidate_count: number;
    independent_alpha_count: number;
    factors: Array<{ id: string; label: string; score: number | null; weight: number; observed: boolean }>;
    method: string;
  };
  sector_map: Array<MainLine & { permission: string; internal_structure: string }>;
  dynamic_weights: { regime: string; weights: Record<string, number>; version: string };
  candidate_decisions: DecisionCandidate2026[];
  decision_windows: Array<{ id: string; time: string; label: string; status: string; immutable_after_capture: boolean }>;
  conditional_orders: {
    alert: DecisionCandidate2026[];
    prepare: DecisionCandidate2026[];
    execute: DecisionCandidate2026[];
    rule: string;
    real_broker_order: boolean;
  };
  why_not_buy: { reasons: string[]; candidate_count: number; principle: string };
  exit_engine: {
    logic_failure: string[];
    market_deterioration: string[];
    overheating: string[];
    fixed_stop_is_only_backstop: boolean;
  };
  strategy_lifecycle: Array<{
    id: string;
    name: string;
    stage: string;
    health_state: string;
    health_score: number | null;
    sample_count: number;
    win_rate_pct: number | null;
    profit_factor: number | null;
    weight_pct: number | null;
    degradation_detected: boolean;
    missing: string[];
  }>;
  final_questions: Array<{ question: string; answer: string }>;
  snapshot_registry: { latest: DecisionSnapshot[]; count: number; immutable_windows: boolean };
  boundaries: string[];
}

interface TruthLayerV4 {
  research_trade_date: string | null;
  data_cutoff_time: string | null;
  generated_at: string | null;
  status: 'PASS' | 'LIMITED' | 'FAIL' | string;
  status_label: string;
  completeness_pct: number;
  confidence_pct: number;
  high_confidence_allowed: boolean;
  source_grade_summary: Record<string, number>;
  pit_guard: { passed: boolean; accepted_count: number; rejected_count: number; rule: string };
  conflicts: Array<{ fact_key: string; source_keys: string[]; values: unknown[]; resolution: string }>;
  warnings: string[];
  records: Array<{ id: string; label: string; source_name: string; source_grade: string; tag: string; available_time: string; data_cutoff_time: string; status: string }>;
}

interface DirectionV4 {
  id: string;
  name: string;
  marginal_state: string;
  policy_count_30d: number;
  max_verified_level: string;
  transmission_state: string;
  industry_validation: {
    status: string;
    label: string;
    sample_count: number;
    universe_count?: number;
    coverage_pct?: number | null;
    source_data_date?: string | null;
    latest_disclosure_date?: string | null;
    metrics?: Record<string, number | null>;
    boundary: string;
  };
  market_validation: {
    status: string;
    strength_score: number | null;
    change_pct: number | null;
    breadth_pct: number | null;
    sectors: string[];
    source?: string[];
    data_date?: string | null;
  };
  gap: { state: string; label: string; causal_verified: boolean };
  score: number | null;
  confidence_pct: number;
  evidence: string[];
  policies: Array<{ title: string; source: string; source_grade: string; published_at: string; url: string; level: string; tag: string }>;
  stages: Array<{ level: string; label: string; verified: boolean; proxy_observed?: boolean; evidence: string[] }>;
}

interface BackfillJob {
  run_id?: number;
  status?: string;
  dataset?: string;
  requested_days?: number;
  total_tasks?: number;
  completed_tasks?: number;
  records_written?: number;
  progress?: number;
  error?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  already_running?: boolean;
  cooldown?: boolean;
}

interface MarketWayV4 {
  version: string;
  contract_version: string;
  generated_at: string;
  phase: string;
  truth: TruthLayerV4;
  national_direction_radar: { directions: DirectionV4[]; observed_count: number; policy_source_available: boolean; summary: string; boundary: string };
  momentum: { state: string; direction: string; strength: number | null; persistence_sessions: number; breadth: number | null; marginal_change: number | null; order_score: number | null; order_state: string; order_change: number | null; evidence: string[]; method: string };
  capital_migration: { from: Array<{ sector: string; flow: number | null; lifecycle?: string }>; to: Array<{ sector: string; flow: number | null; lifecycle?: string }>; risk_appetite: string; stage: string; rotation_type: string; rotation_label: string; evidence: string[]; boundary: string };
  market_force: { type: string; label: string; confidence_pct: number; scores: Array<{ type: string; score: number }>; evidence: string[]; boundary: string };
  chain: Array<{ key: string; glyph: string; label: string; status: string; tag: string; summary: string; evidence: string[] }>;
  principal_contradiction: { statement: string; evidence?: string[]; confidence_pct?: number | null };
  final_decision: { code: string; label: string; confidence_pct: number; permission?: string; why_not_buy: string[]; evidence: string[]; counter_evidence: string[]; next_validation: string[]; real_broker_order: boolean };
  boundaries: string[];
  data_pipeline: {
    policy: { status: string; updated_at?: string | null; source: string };
    industry_financial: { status: string; direction_count: number; verified_count: number; source_data_date?: string | null; source: string };
    market: { status: string; data_date?: string | null; is_realtime: boolean; source?: string };
    market_history?: {
      status: string;
      data_date?: string | null;
      history_count: number;
      amount_history_count: number;
      turnover_history_count: number;
      history_coverage_pct?: number | null;
      amount_coverage_pct?: number | null;
      turnover_coverage_pct?: number | null;
      trading_day_age?: number | null;
      amount_comparable?: boolean;
      source: string;
      sources?: string[];
      source_chain?: string[];
      message: string;
      action: string;
      backfill_job?: BackfillJob | null;
    };
    industry_flow?: {
      status: string;
      board_count: number;
      data_date?: string | null;
      coverage_pct?: number | null;
      cache_used?: boolean;
      sources?: string[];
      source_chain?: string[];
      source: string;
    };
    refresh_job: {
      status: string;
      stage: string;
      progress: number;
      message: string;
      warnings?: string[];
      updated_at?: string | null;
    };
    rule: string;
  };
}

interface WorkbenchData {
  available: boolean;
  meta: WorkbenchMeta;
  market_state: MarketState;
  structure_health: StructureHealth;
  volume_price_alignment: VolumePriceAlignment;
  crowding_risk: CrowdingRisk;
  market_cognition: MarketCognition;
  contradiction_evolution: {
    quantitative_changes: MarketCognition['quantitative_changes'];
    accumulating_count: number;
    qualitative_shift: string;
    evidence: string[];
    method: string;
    data_coverage: Record<string, number>;
  };
  strategy_health: StrategyHealth[];
  adaptive_strategy_weights: {
    weights: Array<{ strategy_id: string; name?: string; weight_pct: number }>;
    health_adjustments: Record<string, string>;
    final_action: string;
    rule: string;
  };
  headline_metrics: HeadlineMetrics;
  ai_judgement: AIJudgement;
  strategy_selector: StrategySelector;
  main_lines: MainLine[];
  daily_short_term_recommendations: DailyShortTermRecommendations;
  candidates: Candidate[];
  candidate_summary: {
    total: number;
    execution_ready: number;
    same_day_observation: number;
    historical_observation: number;
    rule: string;
  };
  execution_queue: {
    phases: ExecutionPhase[];
    schedule: string;
    execution_mode: string;
  };
  risk: {
    market: string[];
    strategy: string[];
    stock: string[];
    reminder_only: boolean;
    disclaimer: string;
  };
  audit: {
    component_dates: Record<string, string | null>;
    stale_components: string[];
    missing_fields: string[];
    score_version: string;
    candidate_score_version: string;
    data_sources: string[];
    same_day_rule: string;
    no_future_data: boolean;
    missing_policy: string;
    refresh_warning?: string;
  };
  quick_links: Array<{ label: string; href: string }>;
  decision_2026: Decision2026;
  market_way_v4: MarketWayV4;
}

const WORKBENCH_CONTRACT_VERSION = 'market-workbench-v4.0.0';
const LOCAL_CACHE_KEY = 'market_decision_workbench_v4_0_0';

function finite(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function value(valueToFormat: number | null | undefined, digits = 1): string {
  return finite(valueToFormat) ? valueToFormat.toFixed(digits) : '--';
}

function integer(valueToFormat: number | null | undefined): string {
  return finite(valueToFormat) ? Math.round(valueToFormat).toLocaleString('zh-CN') : '--';
}

function signed(valueToFormat: number | null | undefined): string {
  if (!finite(valueToFormat)) return '--';
  return `${valueToFormat > 0 ? '+' : ''}${valueToFormat.toFixed(2)}%`;
}

function amount(valueToFormat: number | null | undefined): string {
  if (!finite(valueToFormat)) return '--';
  return `${(valueToFormat / 1e8).toFixed(valueToFormat >= 1e12 ? 0 : 1)}亿`;
}

function localTime(raw: string | null | undefined): string {
  if (!raw) return '--';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw.slice(0, 16).replace('T', ' ');
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed);
}

function stateTone(code: string): string {
  if (code === 'S1' || code === 'S2') return 'text-up border-up/40 bg-up/10';
  if (code === 'S3') return 'text-warn border-warn/40 bg-warn/10';
  if (code === 'S4' || code === 'S5') return 'text-down border-down/40 bg-down/10';
  return 'text-text-secondary border-border bg-[#21262D]';
}

function strategyPresentation(status: StrategyStatus) {
  if (status === 'allowed') {
    return { label: '允许', icon: CheckCircle2, className: 'text-up border-up/40 bg-up/10' };
  }
  if (status === 'limited') {
    return { label: '限制', icon: CircleAlert, className: 'text-warn border-warn/40 bg-warn/10' };
  }
  return { label: '禁止', icon: XCircle, className: 'text-down border-down/40 bg-down/10' };
}

function phaseTone(status: string): string {
  if (status === '有候选' || status === '买入/持有') return 'border-up/40 text-up';
  if (status === '失败') return 'border-down/40 text-down';
  if (status === '运行中') return 'border-accent/40 text-accent';
  if (status === '无信号') return 'border-border text-text-secondary';
  return 'border-warn/40 text-warn';
}

function actionTone(action: string): string {
  if (action === 'execute') return 'text-up border-up/40 bg-up/10';
  if (action === 'caution') return 'text-warn border-warn/40 bg-warn/10';
  if (action === 'observe') return 'text-accent border-accent/40 bg-accent/10';
  return 'text-down border-down/40 bg-down/10';
}

function permissionTone(code: string): string {
  if (code === 'ALLOW') return 'text-up border-up/40 bg-up/10';
  if (code === 'CAUTION') return 'text-warn border-warn/40 bg-warn/10';
  if (code === 'OBSERVE') return 'text-accent border-accent/40 bg-accent/10';
  return 'text-down border-down/40 bg-down/10';
}

function executionTone(level: string): string {
  if (level === 'EXECUTE') return 'text-up border-up/40 bg-up/10';
  if (level === 'PREPARE') return 'text-warn border-warn/40 bg-warn/10';
  if (level === 'ALERT') return 'text-accent border-accent/40 bg-accent/10';
  return 'text-text-secondary border-border bg-[#21262D]';
}

function recommendationStatusTone(status: string): string {
  if (status.includes('仅观察')) return 'border-accent/40 bg-accent/10 text-accent';
  if (status.includes('研究候选')) return 'border-up/40 bg-up/10 text-up';
  return 'border-warn/40 bg-warn/10 text-warn';
}

function healthTone(state: string): string {
  if (state === 'ACTIVE') return 'text-up';
  if (state === 'CAUTION' || state === 'REDUCE') return 'text-warn';
  if (state === 'SUSPENDED') return 'text-down';
  return 'text-accent';
}

const COMPONENT_LABELS: Record<string, string> = {
  sector_diffusion: '板块扩散度',
  market_breadth: '市场宽度',
  volume_price: '量价匹配',
  mainline_stability: '主线稳定性',
  sector_synchronization: '板块同步性',
  leader_follower: '龙头跟风关系',
  high_level_negative_feedback: '高位负反馈',
  high_level_crowding: '高位拥挤',
  high_level_pullback: '高位回撤',
  leader_negative_feedback: '龙头负反馈',
  follower_weakening: '跟风弱化',
  failed_limit_rate: '炸板率',
  promotion_rate_decline: '晋级率弱化',
  capital_concentration: '资金集中度',
};

function componentLabel(key: string): string {
  return COMPONENT_LABELS[key] || key;
}

const PIPELINE_STATUS_LABELS: Record<string, string> = {
  available: '已核验',
  PASS: '真值通过',
  LIMITED: '证据受限',
  FAIL: '真值阻断',
  running: '更新中',
  completed: '更新完成',
  completed_with_gaps: '已更新，源重试中',
  cache: '缓存可用',
  cache_incomplete: '历史补齐中',
  refresh_pending: '已排入采集',
  unavailable: '已排入采集',
  idle: '按计划更新',
  failed: '自动重试中',
  partial: '部分已核验',
  queued: '等待补采',
  backfill_running: '历史补采中',
  not_started: '等待补采',
};

function pipelineStatusLabel(status: string): string {
  return PIPELINE_STATUS_LABELS[status] || status || '状态核验中';
}

function pipelineStatusTone(status: string): string {
  if (['available', 'PASS', 'completed', 'cache'].includes(status)) return 'border-up/40 text-up';
  if (['FAIL', 'failed'].includes(status)) return 'border-down/40 text-down';
  if (['running', 'queued', 'backfill_running'].includes(status)) return 'border-accent/40 text-accent';
  return 'border-warn/40 text-warn';
}

function backfillSummary(job: BackfillJob | null | undefined, marketHistory?: MarketWayV4['data_pipeline']['market_history']): string {
  if (marketHistory?.status === 'available') {
    return `${marketHistory.history_count || 0}日基线已核验 · 成交额与换手齐全`;
  }
  if (!job) return '历史基线等待采集';
  const days = job.requested_days ? `${job.requested_days}日` : '历史';
  if (job.status === 'running' || job.status === 'queued') {
    return `${days}补采 ${value(job.progress, 0)}% · ${job.completed_tasks || 0}/${job.total_tasks || 0} 项`;
  }
  if (job.status === 'completed') return `${days}补采完成 · 写入 ${integer(job.records_written)}`;
  if (job.status === 'partial') return `${days}已部分补采 · 等待重试`;
  if (job.status === 'failed') return `${days}补采失败 · 自动重试`;
  return `${days}补采 ${pipelineStatusLabel(job.status || 'not_started')}`;
}

const V4_STATUS_LABELS: Record<string, string> = {
  ACCELERATING: '边际加速',
  STABLE: '政策稳定',
  DECELERATING: '边际减弱',
  REVERSING: '方向收紧',
  UNOBSERVED: '采集验证中',
  CONTIGUOUS: '连续传导',
  PARTIAL: '部分传导',
  PRICED_UP: '市场已定价',
  NOT_STARTED: '市场未启动',
  MIXED: '市场分化',
  OBSERVED: '已有证据',
  ACTIVE: '规则生效',
  UP: '向上',
  DOWN: '向下',
  UNKNOWN: '核验中',
};

function v4StatusLabel(status: string): string {
  return V4_STATUS_LABELS[status] || pipelineStatusLabel(status);
}

function PipelineCell({ label, status, primary, secondary, source }: {
  label: string;
  status: string;
  primary: string;
  secondary: string;
  source: string;
}) {
  return (
    <div className="min-h-[116px] border-b border-r border-border p-3 last:border-r-0 sm:p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] text-text-secondary">{label}</span>
        <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[9px] ${pipelineStatusTone(status)}`}>{pipelineStatusLabel(status)}</span>
      </div>
      <div className="mt-2 text-xs font-medium text-text">{primary}</div>
      <div className="mt-1 text-[10px] leading-4 text-text-secondary">{secondary}</div>
      <div className="mt-2 line-clamp-2 text-[9px] leading-4 text-text-secondary" title={source}>来源：{source}</div>
    </div>
  );
}

function MetricCell({ label, primary, secondary, tone = 'text-text' }: {
  label: string;
  primary: string;
  secondary?: string;
  tone?: string;
}) {
  return (
    <div className="min-h-[78px] border-b border-r border-border px-3 py-3 last:border-r-0 sm:px-4">
      <div className="text-[10px] text-text-secondary">{label}</div>
      <div className={`mt-1 truncate text-base font-semibold ${tone}`}>{primary}</div>
      {secondary && <div className="mt-1 truncate text-[10px] text-text-secondary">{secondary}</div>}
    </div>
  );
}

function LoadingWorkbench({ progress }: { progress: number }) {
  const status = progress < 35
    ? '读取最近完整交易日'
    : progress < 68
      ? '对齐行情、题材与策略快照'
      : '计算市场状态与执行许可';
  return (
    <div className="mx-auto flex min-h-[65vh] max-w-sm items-center px-5">
      <div className="w-full text-center" role="status">
        <Loader2 size={26} className="mx-auto animate-spin text-accent" />
        <div className="mt-4 text-sm text-text">{status}</div>
        <div className="mt-2 text-xs text-text-secondary">缺失字段保持为空，不以默认分代替</div>
        <div className="mt-5 h-1.5 overflow-hidden bg-[#21262D]">
          <div className="h-full bg-accent transition-[width] duration-300" style={{ width: `${progress}%` }} />
        </div>
        <div className="mt-2 font-mono text-[10px] text-text-secondary">{progress}%</div>
      </div>
    </div>
  );
}

function CandidateActions({ candidate }: { candidate: Candidate }) {
  return (
    <AddToPersonalPoolButton
      code={candidate.code}
      name={candidate.name}
      industry={candidate.sector}
      thesis={`${candidate.strategy}：综合分${value(candidate.score)}；${candidate.why_selected[0] || '结构候选'}`}
      source="ai_decision_workbench"
      compact
    />
  );
}

function DailyRecommendationActions({ item }: { item: DailyShortTermRecommendation }) {
  return (
    <AddToPersonalPoolButton
      code={item.code}
      name={item.name}
      industry={item.sector}
      thesis={`每日短期研究候选：综合分${value(item.score)}；${item.reasons[0] || '多因子覆盖'}`}
      source="daily_short_term_workbench"
      compact
    />
  );
}

function DecisionCandidateActions({ item }: { item: DecisionCandidate2026 }) {
  return (
    <div className="flex items-center justify-end gap-2">
      <Link href={item.detail_href} className="inline-flex h-8 items-center gap-1 rounded-md border border-border px-2 text-[10px] text-accent hover:border-accent hover:text-text">
        决策画像<ArrowRight size={11} />
      </Link>
      <AddToPersonalPoolButton
        code={item.code}
        name={item.name}
        industry={item.sector}
        thesis={`2026决策工作台：${item.state_label}；${item.beta_alpha.detachment}；${item.execution.label}`}
        source="decision_workbench_2026"
        compact
      />
    </div>
  );
}

export default function MarketDecisionWorkbenchPage() {
  const [data, setData] = useState<WorkbenchData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [progress, setProgress] = useState(8);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [snapshotBusy, setSnapshotBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [sourceRefreshBusy, setSourceRefreshBusy] = useState(false);
  const [judgmentAction, setJudgmentAction] = useState('WAIT');
  const [judgmentNote, setJudgmentNote] = useState('');
  const [judgmentBusy, setJudgmentBusy] = useState(false);

  const load = useCallback(async (force = false) => {
    if (force) setRefreshing(true);
    else setLoading(true);
    setError('');
    setNotice('');
    setProgress(8);
    try {
      const response = await apiFetch<{ code: number; data: WorkbenchData }>(
        `/market/workbench${force ? '?refresh=true' : ''}`,
        { cache: 'no-store' },
      );
      if (response.code !== 0 || !response.data) throw new Error('工作台返回无效数据');
      if (response.data.meta?.contract_version !== WORKBENCH_CONTRACT_VERSION) {
        throw new Error('工作台正在更新，请稍后重试');
      }
      setData(response.data);
      setProgress(100);
      if (typeof window !== 'undefined') {
        window.localStorage.setItem(LOCAL_CACHE_KEY, JSON.stringify(response.data));
      }
      if (force) setNotice(`已重新核验至 ${response.data.meta.decision_date || '--'}`);
    } catch (caught) {
      let cached: WorkbenchData | null = null;
      if (typeof window !== 'undefined') {
        try {
          cached = JSON.parse(window.localStorage.getItem(LOCAL_CACHE_KEY) || 'null') as WorkbenchData | null;
        } catch {
          cached = null;
        }
      }
      if (cached?.available && cached.meta?.contract_version === WORKBENCH_CONTRACT_VERSION) {
        setData(cached);
        setNotice('后端连接暂时中断，当前显示本浏览器最近一次成功快照。');
      } else {
        setError(friendlyApiError(caught, '决策工作台加载失败'));
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
    const interval = window.setInterval(() => void load(false), 60_000);
    return () => window.clearInterval(interval);
  }, [load]);

  useEffect(() => {
    if (!loading && !refreshing) return undefined;
    const timer = window.setInterval(() => {
      setProgress((current) => Math.min(92, current + Math.max(1, Math.round((92 - current) / 7))));
    }, 350);
    return () => window.clearInterval(timer);
  }, [loading, refreshing]);

  const sourceJobStatus = data?.market_way_v4?.data_pipeline?.refresh_job?.status || 'idle';
  const historyBackfillStatus = data?.market_way_v4?.data_pipeline?.market_history?.backfill_job?.status || 'idle';
  const acquisitionBusy = ['queued', 'running'].includes(sourceJobStatus) || ['queued', 'running'].includes(historyBackfillStatus);

  useEffect(() => {
    if (!acquisitionBusy) return undefined;
    let disposed = false;
    let finished = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const response = await apiFetch<{
          code: number;
          data: {
            pipeline?: Partial<MarketWayV4['data_pipeline']>;
            refresh_job: MarketWayV4['data_pipeline']['refresh_job'];
          };
        }>('/way/data/status', { cache: 'no-store' });
        if (disposed || response.code !== 0 || !response.data?.refresh_job) return;
        const job = response.data.refresh_job;
        const pipeline = response.data.pipeline || {};
        const backfill = pipeline.market_history?.backfill_job;
        setData((current) => current ? {
          ...current,
          market_way_v4: {
            ...current.market_way_v4,
            data_pipeline: {
              ...current.market_way_v4.data_pipeline,
              ...pipeline,
              refresh_job: job,
            },
          },
        } : current);
        const refreshFinished = !['queued', 'running'].includes(job.status);
        const backfillFinished = !backfill || !['queued', 'running'].includes(backfill.status || '');
        if (refreshFinished && backfillFinished && !finished) {
          finished = true;
          if (timer) window.clearInterval(timer);
          setSourceRefreshBusy(false);
          setNotice(job.message || '数据闭环与历史基线更新完成');
          window.setTimeout(() => void load(false), 150);
        }
      } catch {
        // Keep polling: the acquisition task can continue through a transient API timeout.
      }
    };

    void poll();
    timer = window.setInterval(() => void poll(), 3_000);
    return () => {
      disposed = true;
      if (timer) window.clearInterval(timer);
    };
  }, [acquisitionBusy, load]);

  const captureSnapshot = useCallback(async () => {
    setSnapshotBusy(true);
    setError('');
    try {
      const response = await apiFetch<{ code: number; data: { id: number; created: boolean } }>('/market/workbench/snapshots', {
        method: 'POST',
        body: JSON.stringify({ phase: 'manual', force: false }),
      });
      const message = response.data.created ? `研究快照 #${response.data.id} 已保存` : `今日研究快照 #${response.data.id} 已存在`;
      await load(false);
      setNotice(message);
    } catch (caught) {
      setError(friendlyApiError(caught, '研究快照保存失败'));
    } finally {
      setSnapshotBusy(false);
    }
  }, [load]);

  const validateSnapshots = useCallback(async () => {
    setSnapshotBusy(true);
    setError('');
    try {
      const response = await apiFetch<{ code: number; data: { message: string } }>('/market/workbench/validate', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      const message = response.data.message || '决策验证已完成';
      await load(false);
      setNotice(message);
    } catch (caught) {
      setError(friendlyApiError(caught, '决策验证失败'));
    } finally {
      setSnapshotBusy(false);
    }
  }, [load]);

  const refreshV4Sources = useCallback(async () => {
    setSourceRefreshBusy(true);
    setError('');
    try {
      const response = await apiFetch<{ code: number; data: { message?: string; status?: string; progress?: number } }>('/way/data/refresh', {
        method: 'POST',
        body: JSON.stringify({ background: true }),
      });
      setNotice(response.data.status === 'running' ? '数据闭环更新已启动：政策、全市场行情、市场情绪、行业资金流与财务PIT正在逐项核验。' : '数据闭环更新请求已提交。');
      await load(false);
    } catch (caught) {
      setError(friendlyApiError(caught, '数据闭环更新失败'));
    } finally {
      setSourceRefreshBusy(false);
    }
  }, [load]);

  const saveJudgment = useCallback(async () => {
    setJudgmentBusy(true);
    setError('');
    try {
      await apiFetch('/decisions/judgments', {
        method: 'POST',
        body: JSON.stringify({
          user_action: judgmentAction,
          user_judgment: judgmentNote,
          user_evidence: judgmentNote ? [judgmentNote] : [],
          phase: data?.market_way_v4.phase || 'current',
        }),
      });
      setNotice('你的判断已与AI结论分开保存，盘后可验证双方谁更接近实际状态。');
    } catch (caught) {
      setError(friendlyApiError(caught, '用户判断保存失败'));
    } finally {
      setJudgmentBusy(false);
    }
  }, [data, judgmentAction, judgmentNote]);

  if (loading && !data) return <LoadingWorkbench progress={progress} />;

  if (error && !data) {
    return (
      <div className="mx-auto max-w-lg px-4 py-16 text-center">
        <AlertCircle size={34} className="mx-auto text-down" />
        <h1 className="mt-4 text-base font-semibold text-text">决策工作台加载失败</h1>
        <p className="mt-2 text-sm text-text-secondary">{error}</p>
        <button
          type="button"
          onClick={() => void load(false)}
          className="mt-5 inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-xs text-text-secondary hover:border-accent hover:text-text"
        >
          <RefreshCw size={14} />重新加载
        </button>
      </div>
    );
  }

  if (!data?.available) {
    return (
      <div className="mx-auto max-w-lg px-4 py-16 text-center">
        <Database size={34} className="mx-auto text-text-secondary" />
        <h1 className="mt-4 text-base font-semibold text-text">尚无可计算的完整交易日</h1>
        <p className="mt-2 text-sm text-text-secondary">完成全市场快照与题材缓存后，工作台会自动生成决策状态。</p>
      </div>
    );
  }

  const { meta, market_state: marketState, headline_metrics: metrics, decision_2026: decision, market_way_v4: marketWay } = data;
  const pipeline = marketWay.data_pipeline;
  const marketHistory = pipeline.market_history;
  const industryFlow = pipeline.industry_flow;
  const historyBackfill = marketHistory?.backfill_job;
  const refreshJob = pipeline.refresh_job;
  const refreshProgress = Math.max(0, Math.min(100, refreshJob.progress || 0));
  const upDownText = finite(metrics.up_down_ratio) ? `${metrics.up_down_ratio.toFixed(2)} : 1` : '--';
  const staleCount = data.audit.stale_components.length;

  return (
    <main className="mx-auto w-full max-w-[1500px] px-3 py-4 sm:px-5 sm:py-6">
      <header className="mb-4 flex flex-col gap-3 border-b border-border pb-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <BrainCircuit size={21} className="shrink-0 text-accent" />
            <h1 className="text-xl font-semibold text-text sm:text-2xl">A股研究与交易决策工作台 2026</h1>
          </div>
          <p className="mt-1.5 text-xs text-text-secondary">客观事实 → 主要矛盾 → 阶段判断 → 策略许可 → 实践验证</p>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-text-secondary">
            <span className={meta.is_realtime ? 'text-up' : 'text-warn'}>{meta.is_realtime ? '盘中实时决策' : meta.decision_scope}</span>
            <span>决策日 <b className="font-mono font-normal text-text">{meta.decision_date || '--'}</b></span>
            <span>更新 {localTime(meta.updated_at)}</span>
            <span>覆盖率 {value(meta.coverage_pct, 0)}%</span>
            {staleCount > 0 && <span className="text-warn">{staleCount} 个组件沿用最近可用数据</span>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 self-start lg:self-auto">
          <button
            type="button"
            onClick={() => void captureSnapshot()}
            disabled={snapshotBusy}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-border px-3 text-xs text-text-secondary hover:border-accent hover:text-text disabled:opacity-50"
          >
            <ShieldCheck size={14} />{snapshotBusy ? '处理中' : '保存快照'}
          </button>
          <button
            type="button"
            onClick={() => setHistoryOpen((current) => !current)}
            className="grid h-9 w-9 place-items-center rounded-md border border-border text-text-secondary hover:border-accent hover:text-text"
            title="历史决策快照"
            aria-label="历史决策快照"
          ><History size={14} /></button>
          <button
            type="button"
            onClick={() => void load(true)}
            disabled={refreshing}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-accent/50 px-3 text-xs text-accent hover:bg-accent/10 disabled:opacity-50"
          >
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
            {refreshing ? `${progress}%` : '重新核验'}
          </button>
          <button
            type="button"
            onClick={() => void refreshV4Sources()}
            disabled={sourceRefreshBusy}
            className="grid h-9 w-9 place-items-center rounded-md border border-border text-text-secondary hover:border-accent hover:text-text disabled:opacity-50"
            title="刷新V4数据源与缓存"
            aria-label="刷新V4数据源与缓存"
          ><Database size={14} className={sourceRefreshBusy ? 'animate-pulse' : ''} /></button>
        </div>
      </header>

      {notice && (
        <div className={`mb-4 border-l-2 px-3 py-2 text-xs ${notice.startsWith('已重新') ? 'border-up bg-up/5 text-up' : 'border-warn bg-warn/5 text-warn'}`}>
          {notice}
        </div>
      )}

      {error && data && (
        <div className="mb-4 border-l-2 border-down bg-down/5 px-3 py-2 text-xs text-down">{error}</div>
      )}

      {data.audit.refresh_warning && (
        <div className="mb-4 border-l-2 border-warn bg-warn/5 px-3 py-2 text-xs text-warn">
          {data.audit.refresh_warning}
        </div>
      )}

      <section className="mb-4 overflow-hidden rounded-md border border-border bg-card" aria-label="今日市场之道">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Workflow size={16} className="text-accent" />今日市场之道</h2>
            <p className="mt-1 text-[10px] text-text-secondary">先看真值，再看道、策、业、势、力，最后决定时与止</p>
          </div>
          <div className="flex items-center gap-2 text-[10px]">
            <span className={`rounded border px-2 py-1 ${marketWay.truth.status === 'PASS' ? 'border-up/40 text-up' : marketWay.truth.status === 'LIMITED' ? 'border-warn/40 text-warn' : 'border-down/40 text-down'}`}>{marketWay.truth.status_label}</span>
            <span className={`rounded border px-2 py-1 ${marketWay.final_decision.code === 'NO_TRADE' ? 'border-warn/40 text-warn' : 'border-accent/40 text-accent'}`}>{marketWay.final_decision.label}</span>
          </div>
        </div>
        <div className="grid grid-cols-2 divide-x divide-y divide-border sm:grid-cols-5 xl:grid-cols-10 xl:divide-y-0">
          {marketWay.chain.map((item) => (
            <div key={item.key} className="min-h-[112px] px-3 py-3 sm:min-h-[120px]">
              <div className="flex items-center justify-between gap-2">
                <span className="font-serif text-lg text-accent">{item.glyph}</span>
                <span className={`truncate text-[9px] ${item.status === 'PASS' || item.status === 'UP' || item.status === 'OBSERVED' || item.status === 'ACTIVE' ? 'text-up' : item.status === 'FAIL' || item.status === 'DOWN' ? 'text-down' : 'text-warn'}`}>{v4StatusLabel(item.status)}</span>
              </div>
              <div className="mt-2 truncate text-[10px] font-medium text-text">{item.label}</div>
              <div className="mt-1 line-clamp-3 text-[10px] leading-4 text-text-secondary">{item.summary}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="mb-4 grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><BrainCircuit size={15} className="text-accent" />当前结论与主要矛盾</h2>
            <span className="font-mono text-[10px] text-text-secondary">置信 {value(marketWay.final_decision.confidence_pct, 0)}%</span>
          </div>
          <div className="grid gap-4 p-4 sm:grid-cols-2">
            <div>
              <div className="text-[10px] text-text-secondary">当前行动</div>
              <div className="mt-1 text-xl font-semibold text-text">{marketWay.final_decision.label}</div>
              <div className="mt-2 text-xs leading-5 text-text-secondary">市场许可：{marketWay.final_decision.permission || decision.trading_permission.label}</div>
              <div className="mt-3 text-[10px] text-text-secondary">证据</div>
              <ul className="mt-1 space-y-1 text-[10px] leading-4 text-text">{marketWay.final_decision.evidence.slice(0, 3).map((item) => <li key={item}>· {item}</li>)}</ul>
            </div>
            <div>
              <div className="text-[10px] text-text-secondary">主要矛盾</div>
              <p className="mt-1 text-sm leading-6 text-text">{marketWay.principal_contradiction.statement}</p>
              <div className="mt-3 text-[10px] text-warn">反证与不买条件</div>
              <ul className="mt-1 space-y-1 text-[10px] leading-4 text-warn">{marketWay.final_decision.counter_evidence.slice(0, 3).map((item) => <li key={item}>· {item}</li>)}</ul>
            </div>
          </div>
          <div className="border-t border-border px-4 py-2.5 text-[10px] leading-4 text-text-secondary">下一步验证：{marketWay.final_decision.next_validation.slice(0, 3).join('；')}</div>
        </section>
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3"><h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Database size={15} className="text-accent" />数据闭环</h2><span className="font-mono text-[10px] text-text-secondary">截止 {marketWay.truth.data_cutoff_time ? localTime(marketWay.truth.data_cutoff_time) : '--'}</span></div>
          <div className="grid grid-cols-2 divide-x divide-y divide-border">
            <PipelineCell label="真值审计" status={marketWay.truth.status} primary={`${value(marketWay.truth.completeness_pct, 0)}% 完整度`} secondary={`${marketWay.truth.pit_guard.accepted_count} 条可用证据 · PIT ${marketWay.truth.pit_guard.passed ? '通过' : '受限'}`} source="四时间真值层" />
            <PipelineCell label="官方政策" status={pipeline.policy.status} primary={pipelineStatusLabel(pipeline.policy.status)} secondary={pipeline.policy.updated_at ? `更新 ${localTime(pipeline.policy.updated_at)}` : '等待官方源'} source={pipeline.policy.source} />
            <PipelineCell label="产业财务 PIT" status={pipeline.industry_financial.status} primary={`${pipeline.industry_financial.verified_count}/${pipeline.industry_financial.direction_count} 个方向`} secondary={`数据日 ${pipeline.industry_financial.source_data_date || '--'}`} source={pipeline.industry_financial.source} />
            <PipelineCell label="全行业资金流" status={industryFlow?.status || 'refresh_pending'} primary={`${industryFlow?.board_count || 0} 个板块`} secondary={`数据日 ${industryFlow?.data_date || '--'}${industryFlow?.cache_used ? ' · 同口径缓存' : ' · 已核验快照'}`} source={(industryFlow?.source_chain || []).join(' → ') || industryFlow?.source || '东方财富 / 腾讯 / FTShare / 系统缓存'} />
            <PipelineCell label="市场情绪历史" status={marketHistory?.status || 'unavailable'} primary={`${marketHistory?.history_count || 0}/30 个交易日`} secondary={`成交额 ${marketHistory?.amount_history_count || 0} 日 · 换手 ${marketHistory?.turnover_history_count || 0} 日`} source={(marketHistory?.source_chain || []).join(' → ') || marketHistory?.source || 'StockDailyBar → MarketSentimentDaily'} />
            <PipelineCell label="行情快照" status={pipeline.market.status} primary={`${pipeline.market.data_date || '--'} ${pipeline.market.is_realtime ? '盘中' : '缓存'}`} secondary={pipeline.market.is_realtime ? '当前交易时段实时' : '最近成功交易日缓存'} source={pipeline.market.source || '系统行情缓存'} />
          </div>
          <div className="border-t border-border px-4 py-3">
            <div className="flex items-center justify-between gap-3 text-[10px]"><span className="text-text-secondary">数据补采进度</span><span className={`rounded border px-1.5 py-0.5 ${pipelineStatusTone(refreshJob.status)}`}>{pipelineStatusLabel(refreshJob.status)} · {refreshProgress}%</span></div>
            <div className="mt-2 h-1.5 overflow-hidden bg-[#21262D]"><div className={`h-full transition-[width] duration-500 ${refreshJob.status === 'failed' ? 'bg-down' : 'bg-accent'}`} style={{ width: `${refreshProgress}%` }} /></div>
            <div className="mt-2 text-[10px] leading-4 text-text-secondary">{refreshJob.message}{refreshJob.warnings?.length ? ` · ${refreshJob.warnings.slice(0, 2).join('；')}` : ''}</div>
            <div className="mt-3 border-t border-border/70 pt-2 text-[10px] leading-4">
              <div className="flex items-center justify-between gap-3"><span className="text-text-secondary">历史基线</span><span className={marketHistory?.status === 'available' || historyBackfill?.status === 'completed' ? 'text-up' : 'text-accent'}>{backfillSummary(historyBackfill, marketHistory)}</span></div>
              {historyBackfill && ['queued', 'running'].includes(historyBackfill.status || '') && <div className="mt-1.5 h-1 overflow-hidden bg-[#21262D]"><div className="h-full bg-accent transition-[width] duration-500" style={{ width: `${Math.max(2, Math.min(100, historyBackfill.progress || 0))}%` }} /></div>}
            </div>
          </div>
          {(marketWay.truth.conflicts.length > 0 || marketWay.truth.warnings.length > 0) && <div className="border-t border-warn/30 bg-warn/5 px-4 py-2.5 text-[10px] leading-4 text-warn">{[...marketWay.truth.warnings, ...marketWay.truth.conflicts.map((item) => `${item.fact_key}存在来源冲突`)].slice(0, 3).join('；')}</div>}
          <div className="border-t border-border px-4 py-2.5 text-[10px] leading-4 text-text-secondary">{pipeline.rule}</div>
        </section>
      </section>

      <section className="mb-4 grid gap-4 lg:grid-cols-3">
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3"><h2 className="text-sm font-semibold text-text">势 · 市场有序度</h2><span className="font-mono text-accent">{marketWay.momentum.state}</span></div>
          <div className="grid grid-cols-3 border-b border-border text-center text-[10px]"><div className="p-3"><div className="text-text-secondary">强度</div><div className="mt-1 font-mono text-text">{value(marketWay.momentum.strength)}</div></div><div className="border-l border-border p-3"><div className="text-text-secondary">有序度</div><div className="mt-1 text-text">{marketWay.momentum.order_state}</div></div><div className="border-l border-border p-3"><div className="text-text-secondary">边际</div><div className={`mt-1 font-mono ${(marketWay.momentum.marginal_change || 0) >= 0 ? 'text-up' : 'text-down'}`}>{signed(marketWay.momentum.marginal_change)}</div></div></div>
          <div className="space-y-1.5 p-4 text-[10px] leading-4 text-text-secondary">{marketWay.momentum.evidence.slice(0, 3).map((item) => <div key={item}>· {item}</div>)}</div>
        </section>
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3"><h2 className="text-sm font-semibold text-text">力 · 资金迁徙</h2><span className={`font-mono ${marketWay.capital_migration.risk_appetite === 'RISK_ON' ? 'text-up' : marketWay.capital_migration.risk_appetite === 'RISK_OFF' ? 'text-down' : 'text-warn'}`}>{marketWay.capital_migration.risk_appetite}</span></div>
          <div className="grid grid-cols-2 gap-3 p-4 text-[10px]"><div><div className="text-text-secondary">流入</div><div className="mt-1 space-y-1 text-up">{marketWay.capital_migration.to.slice(0, 3).map((item) => <div key={item.sector}>{item.sector} {amount(item.flow)}</div>)}{marketWay.capital_migration.to.length === 0 && <div className="text-text-secondary">当前无可核验流入记录</div>}</div></div><div><div className="text-text-secondary">流出</div><div className="mt-1 space-y-1 text-down">{marketWay.capital_migration.from.slice(0, 3).map((item) => <div key={item.sector}>{item.sector} {amount(item.flow)}</div>)}{marketWay.capital_migration.from.length === 0 && <div className="text-text-secondary">当前无可核验流出记录</div>}</div></div></div>
          <div className="border-t border-border px-4 py-2.5 text-[10px] text-text-secondary">{marketWay.capital_migration.rotation_label} · 阶段 {marketWay.capital_migration.stage}</div>
        </section>
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3"><h2 className="text-sm font-semibold text-text">力 · 主要定价力量</h2><span className="font-mono text-accent">{marketWay.market_force.type}</span></div>
          <div className="p-4"><div className="text-xs text-text">{marketWay.market_force.label}</div><div className="mt-1 text-[10px] text-text-secondary">推断置信 {value(marketWay.market_force.confidence_pct, 0)}%，不是资金身份确认</div><div className="mt-3 space-y-1.5 text-[10px] leading-4 text-text-secondary">{marketWay.market_force.evidence.slice(0, 3).map((item) => <div key={item}>· {item}</div>)}</div></div>
        </section>
      </section>

      <section className="mb-4 overflow-hidden rounded-md border border-border bg-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3"><div><h2 className="text-sm font-semibold text-text">道 · 国家方向与政策传导</h2><p className="mt-1 text-[10px] text-text-secondary">只展示已采集的官方政策、行业财务PIT和市场验证；L5/L6缺口不会被政策标题掩盖</p></div><span className="text-[10px] text-text-secondary">{marketWay.national_direction_radar.observed_count} 个方向有证据</span></div>
        <div className="hidden overflow-x-auto md:block"><table className="w-full min-w-[1050px] text-xs"><thead className="border-b border-border bg-[#0D1117] text-[10px] text-text-secondary"><tr><th className="px-4 py-2.5 text-left">方向</th><th className="px-3 text-left">政策边际</th><th className="px-3 text-left">传导</th><th className="px-3 text-left">产业/盈利</th><th className="px-3 text-left">市场</th><th className="px-3 text-left">错位</th><th className="px-3 text-right">覆盖</th><th className="px-4 text-left">证据</th></tr></thead><tbody className="divide-y divide-border/70">{marketWay.national_direction_radar.directions.slice(0, 10).map((item) => <tr key={item.id}><td className="px-4 py-3"><span className="font-medium text-text">{item.name}</span><div className="mt-1 text-[10px] text-text-secondary">{item.policy_count_30d}条近30日政策 · {item.max_verified_level}</div></td><td className={`px-3 py-3 text-[10px] ${item.marginal_state === 'ACCELERATING' ? 'text-up' : item.marginal_state === 'REVERSING' ? 'text-down' : 'text-text-secondary'}`}>{v4StatusLabel(item.marginal_state)}</td><td className="px-3 py-3 text-[10px] text-text-secondary">{v4StatusLabel(item.transmission_state)}</td><td className="px-3 py-3 text-[10px]">{item.industry_validation.label}<div className="mt-1 font-mono text-text-secondary">{item.industry_validation.sample_count}样本 · {value(item.industry_validation.coverage_pct, 0)}%</div></td><td className="px-3 py-3 text-[10px] text-text-secondary">{v4StatusLabel(item.market_validation.status)}<div className="mt-1 font-mono">{value(item.market_validation.strength_score)} · {item.market_validation.data_date || '--'}</div></td><td className="px-3 py-3 text-[10px] text-warn">{item.gap.state} · {item.gap.label}</td><td className="px-3 py-3 text-right font-mono text-text">{value(item.score)}</td><td className="max-w-[310px] px-4 py-3 text-[10px] leading-4 text-text-secondary">{item.evidence.slice(0, 2).join('；')}</td></tr>)}</tbody></table></div>
        <div className="divide-y divide-border md:hidden">{marketWay.national_direction_radar.directions.slice(0, 10).map((item) => <article key={item.id} className="p-4"><div className="flex items-start justify-between gap-3"><div><div className="text-sm font-medium text-text">{item.name}</div><div className="mt-1 text-[10px] text-text-secondary">{v4StatusLabel(item.marginal_state)} · {v4StatusLabel(item.transmission_state)} · {item.max_verified_level}</div></div><span className="font-mono text-accent">{value(item.score)}</span></div><div className="mt-3 grid grid-cols-3 border-y border-border py-2 text-center text-[10px]"><div><div className="text-text-secondary">产业样本</div><div className="mt-1 text-text">{item.industry_validation.sample_count}</div></div><div className="border-l border-border"><div className="text-text-secondary">覆盖</div><div className="mt-1 text-text">{value(item.industry_validation.coverage_pct, 0)}%</div></div><div className="border-l border-border"><div className="text-text-secondary">市场</div><div className="mt-1 text-warn">{v4StatusLabel(item.market_validation.status)}</div></div></div><div className="mt-3 text-[10px] leading-4 text-text-secondary">{item.gap.label}；市场数据日 {item.market_validation.data_date || '--'}；{item.evidence.slice(0, 2).join('；')}</div></article>)}</div>
        <div className="border-t border-border px-4 py-2.5 text-[10px] leading-4 text-text-secondary">{marketWay.national_direction_radar.boundary}</div>
      </section>

      <section className="mb-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(320px,0.7fr)]">
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="border-b border-border px-4 py-3"><h2 className="text-sm font-semibold text-text">实践 · AI与用户双轨判断</h2><p className="mt-1 text-[10px] text-text-secondary">用户判断独立保存，下一交易日按市场状态验证，不回写原始AI结论</p></div>
          <div className="grid gap-3 p-4 sm:grid-cols-[150px_minmax(0,1fr)_auto] sm:items-end"><label className="text-[10px] text-text-secondary">我的判断<select value={judgmentAction} onChange={(event) => setJudgmentAction(event.target.value)} className="mt-1 h-9 w-full border border-border bg-bg px-2 text-xs text-text"><option value="BULLISH">偏多</option><option value="NEUTRAL">中性</option><option value="BEARISH">偏空</option><option value="WAIT">等待</option><option value="NO_TRADE">不交易</option></select></label><label className="text-[10px] text-text-secondary">依据与反证<textarea value={judgmentNote} onChange={(event) => setJudgmentNote(event.target.value)} rows={2} placeholder="记录你与AI不同或相同的判断依据" className="mt-1 w-full resize-y border border-border bg-bg px-2 py-2 text-xs text-text placeholder:text-text-secondary" /></label><button type="button" onClick={() => void saveJudgment()} disabled={judgmentBusy} className="inline-flex h-9 items-center justify-center gap-1.5 border border-accent/50 px-3 text-xs text-accent hover:bg-accent/10 disabled:opacity-50"><CheckCircle2 size={13} />{judgmentBusy ? '保存中' : '保存判断'}</button></div>
        </section>
        <section className="overflow-hidden rounded-md border border-border bg-card"><div className="border-b border-border px-4 py-3"><h2 className="text-sm font-semibold text-text">知止 · 失效边界</h2></div><ul className="space-y-2 p-4 text-[10px] leading-4 text-warn">{marketWay.boundaries.map((item) => <li key={item}>· {item}</li>)}</ul></section>
      </section>

      <section className="mb-4 overflow-hidden rounded-md border border-border bg-card" aria-label="市场核心指标">
        <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 [&>*:nth-child(2n)]:border-r-0 sm:[&>*:nth-child(2n)]:border-r xl:[&>*:nth-child(8n)]:border-r-0">
          <MetricCell
            label="市场状态"
            primary={`${marketState.state_code} ${marketState.state_label}`}
            secondary={`${marketState.execution_level} · ${value(marketState.score)}分`}
            tone={marketState.state_code === 'S1' || marketState.state_code === 'S2' ? 'text-up' : marketState.state_code === 'S3' ? 'text-warn' : 'text-down'}
          />
          <MetricCell label="情绪温度" primary={`${value(metrics.sentiment_temperature, 0)}°`} secondary={`置信 ${value(marketState.confidence_pct, 0)}%`} />
          <MetricCell label="两市成交额" primary={amount(metrics.market_amount)} secondary="完整市场快照" />
          <MetricCell label="涨跌比" primary={upDownText} secondary={`${integer(metrics.up_count)} 涨 / ${integer(metrics.down_count)} 跌`} />
          <MetricCell label="涨停" primary={integer(metrics.limit_up)} secondary="只" tone="text-up" />
          <MetricCell label="跌停" primary={integer(metrics.limit_down)} secondary="只" tone="text-down" />
          <MetricCell label="炸板率" primary={`${value(metrics.failed_limit_rate)}%`} secondary="越低越稳定" tone={finite(metrics.failed_limit_rate) && metrics.failed_limit_rate >= 25 ? 'text-down' : 'text-text'} />
          <MetricCell label="第一主线" primary={metrics.main_line || '--'} secondary={data.main_lines[0] ? `${data.main_lines[0].lifecycle} · ${value(data.main_lines[0].strength_score)}分` : '待识别'} tone="text-accent" />
        </div>
      </section>

      <section className="mb-4 grid grid-cols-2 overflow-hidden rounded-md border border-border bg-card sm:grid-cols-3 lg:grid-cols-5" aria-label="2026核心决策">
        <MetricCell label="今日交易许可" primary={decision.trading_permission.label} secondary={decision.trading_permission.allows_new_position ? '可筛选高质量机会' : '不生成主动执行建议'} tone={permissionTone(decision.trading_permission.code).split(' ')[0]} />
        <MetricCell label="市场阶段" primary={decision.market_regime.label} secondary={`${decision.market_regime.code} · ${value(decision.market_regime.score)}分`} tone={stateTone(decision.market_regime.code).split(' ')[0]} />
        <MetricCell label="机会密度" primary={value(decision.opportunity_density.score)} secondary={`${decision.opportunity_density.label} · Alpha ${decision.opportunity_density.independent_alpha_count}`} tone={decision.opportunity_density.score != null && decision.opportunity_density.score >= 70 ? 'text-up' : decision.opportunity_density.score != null && decision.opportunity_density.score >= 45 ? 'text-warn' : 'text-down'} />
        <MetricCell label="建议总仓上限" primary={`${decision.trading_permission.max_total_position_pct}%`} secondary="最终决策由用户掌握" tone={decision.trading_permission.max_total_position_pct > 35 ? 'text-up' : decision.trading_permission.max_total_position_pct > 0 ? 'text-warn' : 'text-down'} />
        <MetricCell label="条件单" primary={`${decision.conditional_orders.execute.length} / ${decision.conditional_orders.prepare.length} / ${decision.conditional_orders.alert.length}`} secondary="执行 / 准备 / 预警" tone="text-accent" />
      </section>

      <section className="mb-4 overflow-x-auto rounded-md border border-border bg-card" aria-label="研究与执行窗口">
        <div className="flex min-w-max divide-x divide-border">
          {decision.decision_windows.map((window) => (
            <div key={window.id} className="w-[165px] px-3 py-3">
              <div className="font-mono text-[10px] text-accent">{window.time}</div>
              <div className="mt-1 text-xs font-medium text-text">{window.label}</div>
              <div className={`mt-1 text-[10px] ${window.status === '进行中' ? 'text-up' : window.status === '窗口已到' ? 'text-warn' : 'text-text-secondary'}`}>{window.status}{window.immutable_after_capture ? ' · 可冻结' : ''}</div>
            </div>
          ))}
        </div>
      </section>

      {historyOpen && (
        <section className="mb-4 overflow-hidden rounded-md border border-border bg-card">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
            <div>
              <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><History size={15} className="text-accent" />决策快照</h2>
              <p className="mt-1 text-[10px] text-text-secondary">窗口数据冻结后只追加验证结果，不用下午数据改写上午判断</p>
            </div>
            <button type="button" onClick={() => void validateSnapshots()} disabled={snapshotBusy} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-2.5 text-[10px] text-text-secondary hover:border-accent hover:text-text disabled:opacity-50"><ListChecks size={12} />盘后验证</button>
          </div>
          {decision.snapshot_registry.latest.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-xs">
                <thead className="border-b border-border bg-[#0D1117] text-[10px] text-text-secondary"><tr><th className="px-4 py-2.5 text-left">日期</th><th className="px-3 text-left">窗口</th><th className="px-3 text-left">证据</th><th className="px-3 text-left">验证</th><th className="px-4 text-right">哈希</th></tr></thead>
                <tbody className="divide-y divide-border/70">{decision.snapshot_registry.latest.map((item) => <tr key={item.id}><td className="px-4 py-3 font-mono text-text">{item.decision_date}</td><td className="px-3 py-3 text-text">{item.phase_label}<div className="mt-1 font-mono text-[10px] text-text-secondary">{localTime(item.captured_at)}</div></td><td className="max-w-[360px] px-3 py-3 text-[10px] leading-4 text-text-secondary">{item.evidence.join('；')}</td><td className={`px-3 py-3 font-mono text-[10px] ${item.validation_status === 'ERROR' ? 'text-down' : item.validation_status === 'CONFIRMED' ? 'text-up' : 'text-warn'}`}>{item.validation_status}</td><td className="px-4 py-3 text-right font-mono text-[10px] text-text-secondary">{item.snapshot_hash.slice(0, 10)}</td></tr>)}</tbody>
              </table>
            </div>
          ) : <div className="px-4 py-8 text-center text-xs text-text-secondary">尚无窗口快照，定时任务会在交易窗口自动冻结</div>}
        </section>
      )}

      <details open className="group mb-4">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 border-b border-border py-3 text-sm font-semibold text-text [&::-webkit-details-marker]:hidden">
          <span className="flex items-center gap-2"><BrainCircuit size={15} className="text-accent" />分析 · 市场认知与结构</span>
          <span className="flex items-center gap-2 text-[10px] font-normal text-text-secondary">事实 → 矛盾 → 阶段 → 权重<ChevronRight size={14} className="transition-transform group-open:rotate-90" /></span>
        </summary>
        <div className="pt-4">
      <section className="mb-4 grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3"><h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Gauge size={15} className="text-accent" />机会密度</h2><span className="font-mono text-sm text-text">{value(decision.opportunity_density.score)}</span></div>
          <div className="grid grid-cols-2 gap-x-5 gap-y-3 p-4 sm:grid-cols-3">
            {decision.opportunity_density.factors.map((factor) => <div key={factor.id}><div className="flex items-center justify-between text-[10px]"><span className="text-text-secondary">{factor.label} <span className="font-mono">{factor.weight}%</span></span><span className={factor.observed ? 'font-mono text-text' : 'text-warn'}>{factor.observed ? value(factor.score, 0) : '补采中'}</span></div><div className="mt-1 h-1 bg-[#21262D]"><div className={factor.observed ? 'h-full bg-accent' : 'h-full bg-warn/30'} style={{ width: `${factor.observed ? Math.max(2, factor.score || 0) : 0}%` }} /></div></div>)}
          </div>
          <div className="border-t border-border px-4 py-2.5 text-[10px] leading-4 text-text-secondary">{decision.opportunity_density.method}</div>
        </section>
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3"><h2 className="flex items-center gap-2 text-sm font-semibold text-text"><ShieldAlert size={15} className="text-warn" />AI为什么不买</h2><span className={`rounded border px-2 py-1 text-[10px] ${permissionTone(decision.trading_permission.code)}`}>{decision.trading_permission.label}</span></div>
          <ul className="space-y-2 p-4 text-xs leading-5 text-text-secondary">{decision.why_not_buy.reasons.slice(0, 6).map((item) => <li key={item} className="flex gap-2"><span className="text-warn">·</span><span>{item}</span></li>)}</ul>
          <div className="border-t border-border px-4 py-2.5 text-[10px] text-text-secondary">{decision.why_not_buy.principle}</div>
        </section>
      </section>

      <section className="mb-4 grid overflow-hidden rounded-md border border-border bg-card lg:grid-cols-[360px_minmax(0,1fr)]">
        <div className="border-b border-border p-4 lg:border-b-0 lg:border-r">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-[10px] text-text-secondary">市场状态引擎</div>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="font-mono text-4xl font-semibold text-text">{value(marketState.score)}</span>
                <span className={`rounded border px-2 py-0.5 text-xs ${stateTone(marketState.state_code)}`}>{marketState.execution_level}</span>
              </div>
            </div>
            <Gauge size={22} className="text-accent" />
          </div>
          <div className="mt-4 space-y-2.5">
            {marketState.dimensions.map((dimension) => (
              <div key={dimension.id}>
                <div className="mb-1 flex items-center justify-between text-[10px]">
                  <span className="text-text-secondary">{dimension.label} <span className="font-mono">{dimension.weight}%</span></span>
                  <span className={dimension.observed ? 'font-mono text-text' : 'text-warn'}>{dimension.observed ? value(dimension.score) : '补采中'}</span>
                </div>
                <div className="h-1 overflow-hidden bg-[#21262D]">
                  <div className={`h-full ${dimension.observed ? 'bg-accent' : 'bg-warn/40'}`} style={{ width: `${dimension.observed ? Math.max(2, dimension.score || 0) : 0}%` }} />
                </div>
              </div>
            ))}
          </div>
          <div className="mt-4 border-t border-border pt-3 text-[10px] leading-5 text-text-secondary">
            评分覆盖 {value(marketState.coverage_pct, 0)}% · {marketState.version}
          </div>
        </div>

        <div className="p-4 sm:p-5">
          <div className="flex items-center justify-between gap-3">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><BrainCircuit size={16} className="text-accent" />AI 今日市场判断</h2>
            <span className={`rounded border px-2 py-1 text-[10px] ${stateTone(marketState.state_code)}`}>结论：{data.ai_judgement.conclusion}</span>
          </div>
          <p className="mt-3 text-sm leading-6 text-text">{data.ai_judgement.market_summary}</p>
          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            <div>
              <div className="text-[10px] font-medium text-text-secondary">关键依据</div>
              <ul className="mt-2 space-y-1.5 text-xs leading-5 text-text">
                {data.ai_judgement.key_evidence.slice(0, 3).map((item) => <li key={item}>· {item}</li>)}
              </ul>
            </div>
            <div>
              <div className="text-[10px] font-medium text-text-secondary">今日主线与策略</div>
              <ul className="mt-2 space-y-1.5 text-xs leading-5 text-text">
                <li>· 主线：{data.ai_judgement.dominant_sectors.join('、') || '待识别'}</li>
                <li>· 优先：{data.ai_judgement.preferred_strategies.join('、') || '暂无允许策略'}</li>
                <li>· 总仓上限：{data.strategy_selector.max_total_position_pct}%</li>
              </ul>
            </div>
            <div>
              <div className="text-[10px] font-medium text-text-secondary">风险与失效</div>
              <ul className="mt-2 space-y-1.5 text-xs leading-5 text-warn">
                {data.ai_judgement.avoid_conditions.slice(0, 3).map((item) => <li key={item}>· {item}</li>)}
              </ul>
            </div>
          </div>
          <div className="mt-4 border-t border-border pt-3 text-[10px] text-text-secondary">
            解释置信度 {value(data.ai_judgement.confidence_pct, 0)}% · {data.ai_judgement.note}
          </div>
        </div>
      </section>

      <section className="mb-4 grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><BrainCircuit size={15} className="text-accent" />市场矛盾分析</h2>
            <span className={`rounded border px-2 py-1 text-[10px] ${actionTone(data.market_cognition.final_action)}`}>{data.market_cognition.action_label}</span>
          </div>
          <div className="grid gap-4 p-4 sm:grid-cols-2">
            <div>
              <div className="text-[10px] text-text-secondary">客观事实</div>
              <ul className="mt-2 space-y-1.5 text-xs leading-5 text-text">
                {data.market_cognition.facts.slice(0, 4).map((item) => <li key={item}>· {item}</li>)}
              </ul>
            </div>
            <div>
              <div className="text-[10px] text-text-secondary">当前主要矛盾</div>
              <p className="mt-2 text-sm leading-6 text-text">{data.market_cognition.principal_contradiction.statement}</p>
              <div className="mt-2 text-[10px] leading-4 text-text-secondary">{data.market_cognition.principal_contradiction.evidence.slice(0, 2).join('；')}</div>
            </div>
            <div>
              <div className="text-[10px] text-text-secondary">矛盾主要方面</div>
              <p className="mt-2 text-sm leading-6 text-text">{data.market_cognition.dominant_aspect.statement}</p>
              <div className="mt-2 text-[10px] text-text-secondary">阶段：{data.market_cognition.stage.label} · 质变：{data.market_cognition.qualitative_shift.status}</div>
            </div>
            <div>
              <div className="text-[10px] text-text-secondary">实践假设</div>
              <p className="mt-2 text-xs leading-5 text-text-secondary">{data.market_cognition.practice_hypothesis.statement}</p>
              <div className="mt-2 text-[10px] text-accent">验证窗口：{data.market_cognition.practice_hypothesis.validation_window}</div>
            </div>
          </div>
          <div className="border-t border-border px-4 py-2.5 text-[10px] leading-5 text-text-secondary">{data.market_cognition.method}</div>
        </section>

        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Activity size={15} className="text-accent" />量变 → 质变监测</h2>
            <span className={`text-[10px] ${data.contradiction_evolution.qualitative_shift === 'confirmed' ? 'text-down' : data.contradiction_evolution.qualitative_shift === 'warning' ? 'text-warn' : 'text-up'}`}>{data.contradiction_evolution.qualitative_shift}</span>
          </div>
          <div className="divide-y divide-border">
            {data.contradiction_evolution.quantitative_changes.length ? data.contradiction_evolution.quantitative_changes.map((item) => (
              <div key={item.id} className="px-4 py-3">
                <div className="flex items-center justify-between gap-3 text-xs"><span className="text-text">{item.label}</span><span className={item.status === 'accumulating' ? 'text-warn' : 'text-text-secondary'}>{item.streak} 次</span></div>
                <div className="mt-1 text-[10px] leading-4 text-text-secondary">{item.evidence}</div>
              </div>
            )) : <div className="px-4 py-8 text-center text-xs text-text-secondary">暂无连续异常证据</div>}
          </div>
          <div className="border-t border-border px-4 py-2.5 text-[10px] text-text-secondary">{data.contradiction_evolution.method}</div>
        </section>
      </section>

      <section className="mb-4 grid gap-4 lg:grid-cols-3">
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3"><h2 className="text-sm font-semibold text-text">结构健康分解</h2><span className="font-mono text-xs text-accent">{value(data.structure_health.score)}</span></div>
          <div className="space-y-2 p-4">
            {Object.entries(data.structure_health.components).map(([key, item]) => <div key={key}><div className="flex justify-between text-[10px] text-text-secondary"><span>{componentLabel(key)}</span><span className="font-mono text-text">{value(item)}</span></div><div className="mt-1 h-1 bg-[#21262D]"><div className="h-full bg-accent" style={{ width: `${finite(item) ? Math.max(2, item) : 0}%` }} /></div></div>)}
          </div>
        </section>
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3"><h2 className="text-sm font-semibold text-text">抱团风险分解</h2><span className={`font-mono text-xs ${data.crowding_risk.score != null && data.crowding_risk.score >= 71 ? 'text-down' : 'text-warn'}`}>{value(data.crowding_risk.score)}</span></div>
          <div className="space-y-2 p-4">
            {Object.entries(data.crowding_risk.components).map(([key, item]) => <div key={key}><div className="flex justify-between text-[10px] text-text-secondary"><span>{componentLabel(key)}</span><span className="font-mono text-text">{value(item)}</span></div><div className="mt-1 h-1 bg-[#21262D]"><div className={`h-full ${data.crowding_risk.score != null && data.crowding_risk.score >= 71 ? 'bg-down' : 'bg-warn'}`} style={{ width: `${finite(item) ? Math.max(2, item) : 0}%` }} /></div></div>)}
          </div>
        </section>
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3"><h2 className="text-sm font-semibold text-text">自适应策略权重</h2><span className={`rounded border px-2 py-1 text-[10px] ${actionTone(data.adaptive_strategy_weights.final_action)}`}>{data.market_cognition.action_label}</span></div>
          <div className="space-y-3 p-4">
            {data.adaptive_strategy_weights.weights.map((item) => <div key={item.strategy_id}><div className="flex justify-between text-[10px] text-text-secondary"><span>{item.name || item.strategy_id}</span><span className="font-mono text-text">{item.weight_pct.toFixed(0)}%</span></div><div className="mt-1 h-1 bg-[#21262D]"><div className="h-full bg-accent" style={{ width: `${item.weight_pct}%` }} /></div></div>)}
          </div>
          <div className="border-t border-border px-4 py-2.5 text-[10px] leading-4 text-text-secondary">{data.adaptive_strategy_weights.rule}</div>
        </section>
      </section>
        </div>
      </details>

      <details className="group mb-4">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 border-b border-border py-3 text-sm font-semibold text-text [&::-webkit-details-marker]:hidden">
          <span className="flex items-center gap-2"><Target size={15} className="text-accent" />选股 · 研究与板块</span>
          <span className="flex items-center gap-2 text-[10px] font-normal text-text-secondary">候选、策略、主线<ChevronRight size={14} className="transition-transform group-open:rotate-90" /></span>
        </summary>
        <div className="pt-4">
      <section className="mb-4 overflow-hidden rounded-md border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border px-4 py-3"><h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Target size={15} className="text-accent" />策略有效性</h2><span className="text-[10px] text-text-secondary">只统计真实前向模拟样本</span></div>
        <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-xs"><thead className="border-b border-border bg-[#0D1117] text-[10px] text-text-secondary"><tr><th className="px-4 py-2.5 text-left">策略</th><th className="px-3 text-left">状态</th><th className="px-3 text-right">样本</th><th className="px-3 text-right">胜率</th><th className="px-3 text-right">期望值</th><th className="px-3 text-right">盈亏比</th><th className="px-3 text-right">最大回撤</th><th className="px-4 text-left">结论</th></tr></thead><tbody className="divide-y divide-border/70">{data.strategy_health.length ? data.strategy_health.map((item) => <tr key={item.id}><td className="px-4 py-3 text-text">{item.name}<div className="mt-1 font-mono text-[10px] text-text-secondary">{item.id}</div></td><td className={`px-3 py-3 font-mono ${healthTone(item.state)}`}>{item.state}</td><td className="px-3 py-3 text-right font-mono text-text">{item.metrics.sample_count}</td><td className="px-3 py-3 text-right font-mono text-text">{value(item.metrics.win_rate_pct)}%</td><td className="px-3 py-3 text-right font-mono text-text">{value(item.metrics.expectancy, 2)}</td><td className="px-3 py-3 text-right font-mono text-text">{value(item.metrics.profit_factor, 2)}</td><td className="px-3 py-3 text-right font-mono text-text">{amount(item.metrics.max_drawdown_amount)}</td><td className="max-w-[300px] px-4 py-3 text-[10px] leading-4 text-text-secondary">{item.reason}</td></tr>) : <tr><td colSpan={8} className="px-4 py-8 text-center text-xs text-text-secondary">暂无策略前向样本，不能判定有效性</td></tr>}</tbody></table></div>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(300px,0.65fr)]">
        <section className="min-w-0 overflow-hidden rounded-md border border-border bg-card">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Activity size={15} className="text-accent" />策略生命周期与衰减</h2>
            <span className="text-[10px] text-text-secondary">异常自动降权，不因一次成功上线</span>
          </div>
          {decision.strategy_lifecycle.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-xs">
                <thead className="border-b border-border bg-[#0D1117] text-[10px] text-text-secondary"><tr><th className="px-4 py-2.5 text-left">策略</th><th className="px-3 text-left">阶段</th><th className="px-3 text-left">健康</th><th className="px-3 text-right">样本</th><th className="px-3 text-right">胜率</th><th className="px-3 text-right">盈亏比</th><th className="px-3 text-right">权重</th><th className="px-4 text-left">待验证</th></tr></thead>
                <tbody className="divide-y divide-border/70">{decision.strategy_lifecycle.map((item) => <tr key={item.id} className={item.degradation_detected ? 'bg-down/5' : ''}><td className="px-4 py-3 text-text">{item.name}<div className="mt-1 font-mono text-[10px] text-text-secondary">{item.id}</div></td><td className="px-3 py-3 font-mono text-[10px] text-accent">{item.stage}</td><td className={`px-3 py-3 font-mono text-[10px] ${item.degradation_detected ? 'text-down' : healthTone(item.health_state)}`}>{item.health_state}</td><td className="px-3 py-3 text-right font-mono text-text">{item.sample_count}</td><td className="px-3 py-3 text-right font-mono text-text">{value(item.win_rate_pct)}%</td><td className="px-3 py-3 text-right font-mono text-text">{value(item.profit_factor, 2)}</td><td className="px-3 py-3 text-right font-mono text-text">{finite(item.weight_pct) ? `${value(item.weight_pct, 0)}%` : '--'}</td><td className="max-w-[260px] px-4 py-3 text-[10px] leading-4 text-text-secondary">{item.missing.join('、') || '当前证据完整'}</td></tr>)}</tbody>
              </table>
            </div>
          ) : <div className="px-4 py-8 text-center text-xs text-text-secondary">暂无可审计的策略样本</div>}
        </section>
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3"><h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Gauge size={15} className="text-accent" />动态因子权重</h2><span className="text-[10px] text-text-secondary">{decision.dynamic_weights.regime}</span></div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-3 p-4">
            {Object.entries(decision.dynamic_weights.weights).map(([key, weight]) => <div key={key}><div className="flex items-center justify-between text-[10px]"><span className="text-text-secondary">{componentLabel(key)}</span><span className="font-mono text-text">{weight}%</span></div><div className="mt-1 h-1 bg-[#21262D]"><div className="h-full bg-accent" style={{ width: `${Math.min(100, weight * 4)}%` }} /></div></div>)}
          </div>
          <div className="border-t border-border px-4 py-2.5 font-mono text-[10px] text-text-secondary">{decision.dynamic_weights.version}</div>
        </section>
      </section>

      <section className="mb-4 overflow-hidden rounded-md border border-border bg-card">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Workflow size={15} className="text-accent" />Alpha归因与三级条件单</h2>
            <p className="mt-1 text-[10px] text-text-secondary">个股状态、板块Beta、独立Alpha、资金行为与风险条件统一判断</p>
          </div>
          <div className="flex items-center gap-2 text-[10px]">
            <span className="rounded border border-up/40 px-2 py-1 text-up">执行 {decision.conditional_orders.execute.length}</span>
            <span className="rounded border border-warn/40 px-2 py-1 text-warn">准备 {decision.conditional_orders.prepare.length}</span>
            <span className="rounded border border-accent/40 px-2 py-1 text-accent">预警 {decision.conditional_orders.alert.length}</span>
          </div>
        </div>
        {decision.candidate_decisions.length ? (
          <>
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full min-w-[1120px] text-xs">
                <thead className="border-b border-border bg-[#0D1117] text-[10px] text-text-secondary"><tr><th className="px-4 py-2.5 text-left">股票 / 状态</th><th className="px-3 text-right">综合</th><th className="px-3 text-right">板块Beta</th><th className="px-3 text-right">个股Alpha</th><th className="px-3 text-left">资金 / 情绪</th><th className="px-3 text-left">交易结构</th><th className="px-3 text-left">条件单</th><th className="px-3 text-left">为什么不买</th><th className="px-4 text-right">操作</th></tr></thead>
                <tbody className="divide-y divide-border/70">{decision.candidate_decisions.map((item) => <tr key={item.code} className="align-top hover:bg-[#21262D]/40"><td className="px-4 py-3"><StockKlineButton code={item.code} name={item.name} className="font-medium text-text">{item.name}<span className="ml-2 font-mono text-[10px] text-text-secondary">{item.code}</span></StockKlineButton><div className="mt-1 text-[10px] text-text-secondary">{item.sector} · {item.state_label} · 覆盖{value(item.data_coverage_pct, 0)}%</div></td><td className="px-3 py-3 text-right font-mono text-base font-semibold text-text">{value(item.score)}</td><td className="px-3 py-3 text-right font-mono text-text-secondary">{signed(item.beta_alpha.sector_beta_pct)}</td><td className={`px-3 py-3 text-right font-mono ${finite(item.beta_alpha.individual_alpha_pct) && item.beta_alpha.individual_alpha_pct > 0 ? 'text-up' : 'text-down'}`}>{signed(item.beta_alpha.individual_alpha_pct)}<div className="mt-1 text-[10px] text-text-secondary">{item.beta_alpha.detachment}</div></td><td className="px-3 py-3"><div className={item.fund_behaviour.supports_price === true ? 'text-up' : item.fund_behaviour.supports_price === false ? 'text-warn' : 'text-text-secondary'}>{item.fund_behaviour.label}</div><div className="mt-1 text-[10px] text-text-secondary">情绪 {item.emotion.label} · {value(item.emotion.score)}</div></td><td className="px-3 py-3 text-text">{item.trade_structure.label}<div className="mt-1 text-[10px] text-text-secondary">技术 {value(item.trade_structure.technical_score)}</div></td><td className="px-3 py-3"><span className={`inline-block rounded border px-1.5 py-0.5 text-[10px] ${executionTone(item.execution.level)}`}>{item.execution.label}</span><div className="mt-1 text-[10px] text-text-secondary">{item.execution.passed_count}/{item.execution.observed_count} 已观测条件通过</div></td><td className="max-w-[270px] px-3 py-3"><details><summary className="cursor-pointer text-[10px] text-warn">查看阻断与失效证据</summary><div className="mt-2 space-y-1 text-[10px] leading-4 text-text-secondary"><div>{item.why_not_buy.slice(0, 4).join('；')}</div><div className="text-down">失效：{item.invalidation_conditions.slice(0, 3).join('；')}</div></div></details></td><td className="px-4 py-3"><DecisionCandidateActions item={item} /></td></tr>)}</tbody>
              </table>
            </div>
            <div className="grid gap-3 p-3 md:hidden">{decision.candidate_decisions.map((item) => <article key={item.code} className="rounded-md border border-border bg-bg p-3"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><StockKlineButton code={item.code} name={item.name} className="font-medium text-text">{item.name}<span className="ml-1 font-mono text-[10px] text-text-secondary">{item.code}</span></StockKlineButton><div className="mt-1 text-[10px] text-text-secondary">{item.sector} · {item.state_label}</div></div><span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${executionTone(item.execution.level)}`}>{item.execution.label}</span></div><div className="mt-3 grid grid-cols-4 overflow-hidden rounded border border-border"><div className="p-2 text-center"><div className="text-[10px] text-text-secondary">综合</div><div className="mt-1 font-mono text-text">{value(item.score)}</div></div><div className="border-l border-border p-2 text-center"><div className="text-[10px] text-text-secondary">Alpha</div><div className="mt-1 font-mono text-up">{signed(item.beta_alpha.individual_alpha_pct)}</div></div><div className="border-l border-border p-2 text-center"><div className="text-[10px] text-text-secondary">情绪</div><div className="mt-1 text-text">{item.emotion.label}</div></div><div className="border-l border-border p-2 text-center"><div className="text-[10px] text-text-secondary">条件</div><div className="mt-1 font-mono text-text">{item.execution.passed_count}/{item.execution.observed_count}</div></div></div><div className="mt-3 space-y-1.5 text-[10px] leading-4"><div><span className="text-up">强因：</span><span className="text-text-secondary">{item.why_strong.slice(0, 2).join('；')}</span></div><div><span className="text-warn">不买：</span><span className="text-text-secondary">{item.why_not_buy.slice(0, 2).join('；')}</span></div><div><span className="text-down">失效：</span><span className="text-text-secondary">{item.invalidation_conditions.slice(0, 2).join('；')}</span></div></div><div className="mt-3"><DecisionCandidateActions item={item} /></div></article>)}</div>
          </>
        ) : <div className="px-4 py-10 text-center text-xs text-text-secondary">当前没有可形成完整归因的候选，系统不会为了交易而强行推荐</div>}
        <div className="border-t border-border px-4 py-2.5 text-[10px] leading-4 text-text-secondary">{decision.conditional_orders.rule}</div>
      </section>

      <section className="mb-4 overflow-hidden rounded-md border border-border bg-card">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Sparkles size={15} className="text-accent" />每日短期推荐</h2>
            <p className="mt-1 text-[10px] leading-4 text-text-secondary">{data.daily_short_term_recommendations.horizon}</p>
          </div>
          <div className="text-right text-[10px] leading-4 text-text-secondary">
            <div>数据日 <span className="font-mono text-text">{data.daily_short_term_recommendations.data_date || '--'}</span> · {data.daily_short_term_recommendations.source}</div>
            <div>候选池 {data.daily_short_term_recommendations.universe_count} 只 · 通过 {data.daily_short_term_recommendations.eligible_count} 只 · PIT财务 {data.daily_short_term_recommendations.financial_cache_count} 只</div>
          </div>
        </div>

        <div className="border-b border-border px-4 py-3 text-[10px] leading-5 text-text-secondary">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className={`rounded border px-1.5 py-0.5 ${actionTone(data.daily_short_term_recommendations.market_action)}`}>市场闸门：{data.daily_short_term_recommendations.market_action}</span>
            <span>{data.daily_short_term_recommendations.method}</span>
          </div>
          {data.daily_short_term_recommendations.warnings.length > 0 && <div className="mt-1 text-warn">{data.daily_short_term_recommendations.warnings.join('；')}</div>}
        </div>

        {!data.daily_short_term_recommendations.available ? (
          <div className="px-4 py-10 text-center text-xs text-text-secondary">当前没有同时满足数据时效、板块强度、资金、盈利和量比规则的短期候选，不伪造推荐。</div>
        ) : (
          <div className="grid gap-3 p-3 sm:grid-cols-2 xl:grid-cols-4">
            {data.daily_short_term_recommendations.candidates.map((item) => (
              <article key={item.code} className="min-w-0 border border-border bg-bg p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <StockKlineButton code={item.code} name={item.name} className="max-w-full text-left font-medium text-text">
                      <span className="truncate">{item.name}</span><span className="ml-1 font-mono text-[10px] text-text-secondary">{item.code}</span>
                    </StockKlineButton>
                    <div className="mt-1 truncate text-[10px] text-text-secondary">{item.sector} · 数据日 {item.data_date || '--'}</div>
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="font-mono text-xl font-semibold text-text">{value(item.score)}</div>
                    <div className="text-[10px] text-text-secondary">置信 {value(item.confidence_pct, 0)}%</div>
                  </div>
                </div>
                <div className="mt-3 flex items-center justify-between gap-2 border-y border-border py-2">
                  <span className="font-mono text-sm text-text">{value(item.price, 2)}</span>
                  <span className={`font-mono text-xs ${finite(item.change_pct) && item.change_pct >= 0 ? 'text-up' : 'text-down'}`}>{signed(item.change_pct)}</span>
                  <span className={`rounded border px-1.5 py-0.5 text-[10px] ${recommendationStatusTone(item.status)}`}>{item.status}</span>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-[10px]">
                  <div><span className="text-text-secondary">板块</span><div className="mt-0.5 font-mono text-text">{value(item.score_breakdown.sector_strength)}</div></div>
                  <div><span className="text-text-secondary">资金</span><div className="mt-0.5 font-mono text-text">{value(item.score_breakdown.capital)}</div></div>
                  <div><span className="text-text-secondary">盈利</span><div className="mt-0.5 font-mono text-text">{value(item.score_breakdown.profitability)}</div></div>
                  <div><span className="text-text-secondary">风险安全</span><div className="mt-0.5 font-mono text-text">{value(item.score_breakdown.risk_safety)}</div></div>
                  <div><span className="text-text-secondary">量比</span><div className="mt-0.5 font-mono text-text">{value(item.volume_ratio, 2)}</div></div>
                  <div><span className="text-text-secondary">市场情绪</span><div className="mt-0.5 font-mono text-text">{value(item.score_breakdown.market_sentiment)}</div></div>
                </div>
                <div className="mt-3 space-y-1 text-[10px] leading-4">
                  {item.reasons.slice(0, 3).map((reason) => <div key={reason} className="text-text-secondary"><span className="text-up">·</span> {reason}</div>)}
                  <div className="text-warn">风险：{item.risk}</div>
                </div>
                <details className="mt-3 border-t border-border pt-2 text-[10px]">
                  <summary className="cursor-pointer text-accent">失效条件与数据来源</summary>
                  <div className="mt-2 space-y-1 leading-4 text-text-secondary">
                    <div>失效：{item.invalidation_conditions.join('；')}</div>
                    <div>盈利数据：{item.profitability.status === 'financial_pit_cache' ? `公告日 ${item.profitability.disclosed_at || '--'} PIT缓存` : '行情端ROE/PE代理，需个股页复核'}</div>
                    <div>来源：{item.source} · {item.is_realtime ? '实时' : '历史/缓存'}</div>
                  </div>
                </details>
                <div className="mt-3 flex items-center justify-between gap-2">
                  <button type="button" onClick={() => { window.location.href = `/pro/stock?code=${encodeURIComponent(item.code)}`; }} className="inline-flex min-w-0 items-center gap-1 text-[10px] text-accent hover:text-text" title="打开个股决策画像">
                    查看个股画像<ArrowRight size={12} />
                  </button>
                  <DailyRecommendationActions item={item} />
                </div>
              </article>
            ))}
          </div>
        )}
        <div className="border-t border-border px-4 py-2.5 text-[10px] leading-5 text-text-secondary">{data.daily_short_term_recommendations.rule}</div>
      </section>

      <div className="mb-4 grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Target size={15} className="text-accent" />今日策略许可</h2>
            <span className="text-[10px] text-text-secondary">总仓 ≤ {data.strategy_selector.max_total_position_pct}%</span>
          </div>
          <div className="divide-y divide-border">
            {data.strategy_selector.strategies.map((strategy) => {
              const presentation = strategyPresentation(strategy.status);
              const Icon = presentation.icon;
              return (
                <Link key={strategy.id} href={strategy.href} className="block px-4 py-3 hover:bg-[#21262D]/60">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium text-text">{strategy.name}</div>
                      <div className="mt-1 line-clamp-2 text-[10px] leading-4 text-text-secondary">{strategy.reason}</div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] ${presentation.className}`}><Icon size={11} />{presentation.label}</span>
                      <ChevronRight size={13} className="text-text-secondary" />
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
          {data.strategy_selector.loss_alert?.warning && (
            <div className="border-t border-warn/30 bg-warn/5 px-4 py-3 text-[10px] leading-5 text-warn">
              {data.strategy_selector.loss_alert.reason}
            </div>
          )}
        </section>

        <section className="min-w-0 overflow-hidden rounded-md border border-border bg-card">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
            <div>
              <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><BarChart3 size={15} className="text-accent" />原始策略证据池</h2>
              <p className="mt-1 text-[10px] text-text-secondary">保留策略原始输出供复核；上方 2026 决策层负责 Alpha 归因和执行许可</p>
            </div>
            <div className="flex flex-wrap gap-x-3 text-[10px] text-text-secondary">
              <span>执行 {data.candidate_summary.execution_ready}</span>
              <span>同日观察 {data.candidate_summary.same_day_observation}</span>
              <span>历史观察 {data.candidate_summary.historical_observation}</span>
            </div>
          </div>

          {data.candidates.length === 0 ? (
            <div className="px-4 py-12 text-center text-xs text-text-secondary">当前没有符合数据时效与结构规则的候选</div>
          ) : (
            <>
              <div className="hidden overflow-x-auto md:block">
                <table className="w-full min-w-[980px] text-xs">
                  <thead className="border-b border-border bg-[#0D1117] text-[10px] text-text-secondary">
                    <tr>
                      <th className="px-4 py-2.5 text-left font-medium">股票 / 来源</th>
                      <th className="px-3 text-left font-medium">状态</th>
                      <th className="px-3 text-right font-medium">综合</th>
                      <th className="px-3 text-right font-medium">市场</th>
                      <th className="px-3 text-right font-medium">板块</th>
                      <th className="px-3 text-right font-medium">趋势</th>
                      <th className="px-3 text-right font-medium">量价</th>
                      <th className="px-3 text-right font-medium">资金</th>
                      <th className="px-3 text-left font-medium">决策逻辑</th>
                      <th className="px-4 text-right font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/70">
                    {data.candidates.map((candidate) => (
                      <tr key={candidate.code} className="align-top hover:bg-[#21262D]/40">
                        <td className="px-4 py-3">
                          <StockKlineButton code={candidate.code} name={candidate.name} className="font-medium text-text">
                            {candidate.name}<span className="ml-2 font-mono text-[10px] text-text-secondary">{candidate.code}</span>
                          </StockKlineButton>
                          <div className="mt-1 text-[10px] text-text-secondary">{candidate.sector} · {candidate.strategy}</div>
                          <div className={`mt-1 text-[10px] ${candidate.stale ? 'text-warn' : 'text-text-secondary'}`}>数据日 {candidate.data_date || '--'}</div>
                        </td>
                        <td className="px-3 py-3">
                          <span className={`inline-block rounded border px-1.5 py-0.5 text-[10px] ${candidate.execution_eligible ? 'border-up/40 text-up' : candidate.stale ? 'border-warn/40 text-warn' : 'border-accent/40 text-accent'}`}>{candidate.status}</span>
                          <div className="mt-1.5 text-[10px] text-text-secondary">{candidate.pool}</div>
                        </td>
                        <td className="px-3 py-3 text-right font-mono text-base font-semibold text-text">{value(candidate.score)}</td>
                        <td className="px-3 py-3 text-right font-mono text-text-secondary">{value(candidate.score_breakdown.market_fit)}</td>
                        <td className="px-3 py-3 text-right font-mono text-text-secondary">{value(candidate.score_breakdown.sector_strength)}</td>
                        <td className="px-3 py-3 text-right font-mono text-text-secondary">{value(candidate.score_breakdown.trend)}</td>
                        <td className="px-3 py-3 text-right font-mono text-text-secondary">{value(candidate.score_breakdown.volume_price)}</td>
                        <td className="px-3 py-3 text-right font-mono text-text-secondary">{value(candidate.score_breakdown.capital)}</td>
                        <td className="max-w-[320px] px-3 py-3">
                          <details>
                            <summary className="cursor-pointer text-[11px] text-accent">依据、扣分与放弃条件</summary>
                            <div className="mt-2 grid gap-2 text-[10px] leading-4">
                              <div><span className="text-up">入选：</span><span className="text-text-secondary">{candidate.why_selected.join('；')}</span></div>
                              <div><span className="text-warn">扣分：</span><span className="text-text-secondary">{candidate.why_not_full.join('；')}</span></div>
                              <div><span className="text-down">放弃：</span><span className="text-text-secondary">{candidate.abandon_conditions.join('；')}</span></div>
                            </div>
                          </details>
                        </td>
                        <td className="px-4 py-3 text-right"><CandidateActions candidate={candidate} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="divide-y divide-border md:hidden">
                {data.candidates.map((candidate) => (
                  <article key={candidate.code} className="p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <StockKlineButton code={candidate.code} name={candidate.name} className="font-medium text-text">
                          {candidate.name}<span className="ml-2 font-mono text-[10px] text-text-secondary">{candidate.code}</span>
                        </StockKlineButton>
                        <div className="mt-1 text-[10px] text-text-secondary">{candidate.sector} · {candidate.strategy}</div>
                      </div>
                      <div className="shrink-0 text-right">
                        <div className="font-mono text-xl font-semibold text-text">{value(candidate.score)}</div>
                        <div className={`text-[10px] ${candidate.stale ? 'text-warn' : candidate.execution_eligible ? 'text-up' : 'text-accent'}`}>{candidate.status}</div>
                      </div>
                    </div>
                    <div className="mt-3 grid grid-cols-5 border-y border-border py-2 text-center text-[10px]">
                      <div><div className="text-text-secondary">市场</div><div className="mt-1 font-mono text-text">{value(candidate.score_breakdown.market_fit, 0)}</div></div>
                      <div><div className="text-text-secondary">板块</div><div className="mt-1 font-mono text-text">{value(candidate.score_breakdown.sector_strength, 0)}</div></div>
                      <div><div className="text-text-secondary">趋势</div><div className="mt-1 font-mono text-text">{value(candidate.score_breakdown.trend, 0)}</div></div>
                      <div><div className="text-text-secondary">量价</div><div className="mt-1 font-mono text-text">{value(candidate.score_breakdown.volume_price, 0)}</div></div>
                      <div><div className="text-text-secondary">资金</div><div className="mt-1 font-mono text-text">{value(candidate.score_breakdown.capital, 0)}</div></div>
                    </div>
                    <div className="mt-3 space-y-1.5 text-[10px] leading-4">
                      <div><span className="text-up">入选：</span><span className="text-text-secondary">{candidate.why_selected.slice(0, 2).join('；')}</span></div>
                      <div><span className="text-warn">扣分：</span><span className="text-text-secondary">{candidate.why_not_full.slice(0, 2).join('；')}</span></div>
                      <div><span className="text-down">放弃：</span><span className="text-text-secondary">{candidate.abandon_conditions.slice(0, 2).join('；')}</span></div>
                    </div>
                    <div className="mt-3 flex items-center justify-between gap-3">
                      <span className="text-[10px] text-text-secondary">{candidate.pool} · {candidate.data_date || '--'}</span>
                      <CandidateActions candidate={candidate} />
                    </div>
                  </article>
                ))}
              </div>
            </>
          )}
          <div className="border-t border-border px-4 py-2.5 text-[10px] leading-5 text-text-secondary">{data.candidate_summary.rule}</div>
        </section>
      </div>

      <section className="mb-4 overflow-hidden rounded-md border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Layers3 size={15} className="text-accent" />主线板块与龙头结构</h2>
          <Link href="/pro/topic-strength" className="inline-flex items-center gap-1 text-[10px] text-accent hover:text-text">完整题材雷达<ArrowRight size={12} /></Link>
        </div>
        {decision.sector_map.length === 0 ? (
          <div className="px-4 py-10 text-center text-xs text-text-secondary">主线板块正在从全行业资金流与题材快照重建</div>
        ) : (
          <>
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full min-w-[1080px] text-xs">
              <thead className="border-b border-border bg-[#0D1117] text-[10px] text-text-secondary">
                <tr>
                  <th className="px-4 py-2.5 text-left font-medium">排名 / 板块</th>
                  <th className="px-3 text-left font-medium">许可</th>
                  <th className="px-3 text-left font-medium">级别</th>
                  <th className="px-3 text-left font-medium">生命周期</th>
                  <th className="px-3 text-left font-medium">内部结构</th>
                  <th className="px-3 text-right font-medium">强度</th>
                  <th className="px-3 text-right font-medium">宽度</th>
                  <th className="px-3 text-right font-medium">资金</th>
                  <th className="px-3 text-left font-medium">龙头</th>
                  <th className="px-4 text-left font-medium">证据 / 风险</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/70">
                {decision.sector_map.slice(0, 8).map((line) => (
                  <tr key={`${line.rank}-${line.name}`} className="hover:bg-[#21262D]/40">
                    <td className="px-4 py-3"><span className="mr-2 font-mono text-text-secondary">{line.rank}</span><span className="font-medium text-text">{line.name}</span></td>
                    <td className={`px-3 py-3 ${line.permission === '允许研究' ? 'text-up' : line.permission === '排除' ? 'text-down' : 'text-warn'}`}>{line.permission}</td>
                    <td className="px-3 py-3 text-accent">{line.classification}</td>
                    <td className={`px-3 py-3 ${line.lifecycle === '退潮' || line.lifecycle === '分化预警' ? 'text-warn' : 'text-text'}`}>{line.lifecycle}</td>
                    <td className="px-3 py-3 text-[10px] text-text-secondary">{line.internal_structure}</td>
                    <td className="px-3 py-3 text-right font-mono text-text">{value(line.strength_score)}</td>
                    <td className="px-3 py-3 text-right font-mono text-text-secondary">{value(line.breadth)}%</td>
                    <td className={`px-3 py-3 text-right font-mono ${finite(line.main_net_inflow) && line.main_net_inflow >= 0 ? 'text-up' : 'text-down'}`}>{amount(line.main_net_inflow)}</td>
                    <td className="px-3 py-3">
                      {line.leader.code ? (
                        <StockKlineButton code={line.leader.code} name={line.leader.name} className="text-text">
                          {line.leader.name}<span className="ml-1 text-[10px] text-text-secondary">{line.leader.boards == null ? '' : `${line.leader.boards}板`}</span>
                        </StockKlineButton>
                      ) : '--'}
                    </td>
                    <td className="max-w-[360px] px-4 py-3 text-[10px] leading-4 text-text-secondary">
                      <div>{line.evidence}</div>
                      {line.risk_flags.length > 0 && <div className="mt-1 text-warn">{line.risk_flags.join('；')}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="divide-y divide-border md:hidden">
            {decision.sector_map.slice(0, 8).map((line) => (
              <article key={`${line.rank}-${line.name}`} className="p-4">
                <div className="flex items-start justify-between gap-3">
                  <div><div className="text-sm font-medium text-text"><span className="mr-2 font-mono text-[10px] text-text-secondary">{line.rank}</span>{line.name}</div><div className="mt-1 text-[10px] text-text-secondary">{line.classification} · {line.lifecycle}</div></div>
                  <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${line.permission === '允许研究' ? 'border-up/40 text-up' : line.permission === '排除' ? 'border-down/40 text-down' : 'border-warn/40 text-warn'}`}>{line.permission}</span>
                </div>
                <div className="mt-3 grid grid-cols-3 border-y border-border py-2 text-center text-[10px]"><div><div className="text-text-secondary">强度</div><div className="mt-1 font-mono text-text">{value(line.strength_score)}</div></div><div><div className="text-text-secondary">宽度</div><div className="mt-1 font-mono text-text">{value(line.breadth)}%</div></div><div><div className="text-text-secondary">资金</div><div className={`mt-1 font-mono ${finite(line.main_net_inflow) && line.main_net_inflow >= 0 ? 'text-up' : 'text-down'}`}>{amount(line.main_net_inflow)}</div></div></div>
                <div className="mt-3 text-[10px] leading-4 text-text-secondary">{line.internal_structure}；{line.evidence}</div>
                {line.risk_flags.length > 0 && <div className="mt-2 text-[10px] leading-4 text-warn">{line.risk_flags.join('；')}</div>}
              </article>
            ))}
          </div>
          </>
        )}
      </section>

        </div>
      </details>

      <details className="group mb-4">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 border-b border-border py-3 text-sm font-semibold text-text [&::-webkit-details-marker]:hidden">
          <span className="flex items-center gap-2"><Clock3 size={15} className="text-accent" />执行 · 模拟与验证</span>
          <span className="flex items-center gap-2 text-[10px] font-normal text-text-secondary">队列、退出、六问<ChevronRight size={14} className="transition-transform group-open:rotate-90" /></span>
        </summary>
        <div className="pt-4">
      <section className="mb-4 overflow-hidden rounded-md border border-border bg-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Clock3 size={15} className="text-accent" />今日执行队列</h2>
          <span className="text-[10px] text-text-secondary">{data.execution_queue.execution_mode}</span>
        </div>
        <div className="grid sm:grid-cols-2 xl:grid-cols-4">
          {data.execution_queue.phases.map((phase, index) => (
            <div key={phase.id} className="relative border-b border-border p-4 last:border-b-0 sm:border-r sm:[&:nth-child(2n)]:border-r-0 xl:border-b-0 xl:[&:nth-child(2n)]:border-r xl:last:border-r-0">
              <div className="flex items-center justify-between gap-3">
                <span className="font-mono text-[10px] text-accent">0{index + 1} · {phase.scheduled_at}</span>
                <span className={`rounded border px-1.5 py-0.5 text-[10px] ${phaseTone(phase.display_status)}`}>{phase.display_status}</span>
              </div>
              <div className="mt-2 text-sm font-medium text-text">{phase.label}</div>
              <div className="mt-1 text-[10px] leading-4 text-text-secondary">{phase.message}</div>
              <div className="mt-3 flex items-center justify-between text-[10px] text-text-secondary">
                <span>数据日 {phase.data_date || '--'}</span>
                <span>{phase.candidate_count} 个候选</span>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="mb-4 grid gap-4 lg:grid-cols-[minmax(300px,0.8fr)_minmax(0,1.2fr)]">
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center gap-2 border-b border-border px-4 py-3"><ShieldAlert size={15} className="text-warn" /><h2 className="text-sm font-semibold text-text">动态退出引擎</h2></div>
          <div className="grid gap-4 p-4 sm:grid-cols-3 lg:grid-cols-1 xl:grid-cols-3">
            <div><div className="text-[10px] font-medium text-text-secondary">原始逻辑失效</div><ul className="mt-2 space-y-1.5 text-[10px] leading-4 text-down">{decision.exit_engine.logic_failure.map((item) => <li key={item}>· {item}</li>)}</ul></div>
            <div><div className="text-[10px] font-medium text-text-secondary">市场环境恶化</div><ul className="mt-2 space-y-1.5 text-[10px] leading-4 text-warn">{decision.exit_engine.market_deterioration.map((item) => <li key={item}>· {item}</li>)}</ul></div>
            <div><div className="text-[10px] font-medium text-text-secondary">个股过热</div><ul className="mt-2 space-y-1.5 text-[10px] leading-4 text-warn">{decision.exit_engine.overheating.map((item) => <li key={item}>· {item}</li>)}</ul></div>
          </div>
          <div className="border-t border-border px-4 py-2.5 text-[10px] text-text-secondary">固定止盈止损只作最后保护，退出首先依据原研究逻辑是否失效。</div>
        </section>
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3"><h2 className="flex items-center gap-2 text-sm font-semibold text-text"><ListChecks size={15} className="text-accent" />每日决策六问</h2><span className="text-[10px] text-text-secondary">先回答，再执行</span></div>
          <div className="grid sm:grid-cols-2">
            {decision.final_questions.map((item, index) => <div key={item.question} className="border-b border-border p-4 sm:odd:border-r sm:[&:nth-last-child(-n+2)]:border-b-0"><div className="text-[10px] text-accent">0{index + 1}</div><div className="mt-1 text-xs font-medium text-text">{item.question}</div><div className="mt-2 text-[10px] leading-5 text-text-secondary">{item.answer}</div></div>)}
          </div>
        </section>
      </section>
        </div>
      </details>

      <details className="group mb-4">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 border-b border-border py-3 text-sm font-semibold text-text [&::-webkit-details-marker]:hidden">
          <span className="flex items-center gap-2"><Database size={15} className="text-accent" />审计 · 风险与复盘</span>
          <span className="flex items-center gap-2 text-[10px] font-normal text-text-secondary">风险红线、数据审计<ChevronRight size={14} className="transition-transform group-open:rotate-90" /></span>
        </summary>
        <div className="pt-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center gap-2 border-b border-border px-4 py-3"><ShieldAlert size={15} className="text-warn" /><h2 className="text-sm font-semibold text-text">风险红线</h2></div>
          <div className="grid gap-4 p-4 sm:grid-cols-3">
            <div><div className="text-[10px] text-text-secondary">市场级</div><ul className="mt-2 space-y-1.5 text-[10px] leading-4 text-warn">{data.risk.market.map((item) => <li key={item}>· {item}</li>)}</ul></div>
            <div><div className="text-[10px] text-text-secondary">策略级</div><ul className="mt-2 space-y-1.5 text-[10px] leading-4 text-warn">{(data.risk.strategy.length ? data.risk.strategy : ['未触发策略级额外提醒']).map((item) => <li key={item}>· {item}</li>)}</ul></div>
            <div><div className="text-[10px] text-text-secondary">个股级</div><ul className="mt-2 space-y-1.5 text-[10px] leading-4 text-warn">{(data.risk.stock.length ? data.risk.stock : ['未触发已观测个股风险']).map((item) => <li key={item}>· {item}</li>)}</ul></div>
          </div>
          <div className="border-t border-border px-4 py-2.5 text-[10px] text-text-secondary">{data.risk.disclaimer}</div>
        </section>

        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3"><div className="flex items-center gap-2"><Database size={15} className="text-accent" /><h2 className="text-sm font-semibold text-text">数据审计</h2></div><span className="font-mono text-[10px] text-text-secondary">{data.audit.score_version}</span></div>
          <div className="p-4">
            <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-[10px] sm:grid-cols-3">
              {Object.entries(data.audit.component_dates).map(([key, dateValue]) => (
                <div key={key} className="flex items-center justify-between gap-2 border-b border-border/70 pb-1.5">
                  <span className="truncate text-text-secondary">{key}</span>
                  <span className={`shrink-0 font-mono ${dateValue && dateValue !== meta.decision_date && key !== 'auction' ? 'text-warn' : 'text-text'}`}>{dateValue || '--'}</span>
                </div>
              ))}
            </div>
            {(data.audit.missing_fields.length > 0 || data.audit.stale_components.length > 0) && (
              <div className="mt-3 border-l-2 border-warn bg-warn/5 px-3 py-2 text-[10px] leading-5 text-warn">
                {data.audit.stale_components.length > 0 && <div>沿用最近可用数据：{data.audit.stale_components.join('、')}</div>}
                {data.audit.missing_fields.length > 0 && <div>补采队列：{data.audit.missing_fields.slice(0, 6).join('、')}</div>}
              </div>
            )}
            <div className="mt-3 text-[10px] leading-5 text-text-secondary">{data.audit.same_day_rule}</div>
          </div>
        </section>
      </div>
        </div>
      </details>

      <nav className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-4" aria-label="工作台快捷入口">
        <Activity size={14} className="mr-1 text-text-secondary" />
        {data.quick_links.map((item) => (
          <Link key={item.href} href={item.href} className="inline-flex h-8 items-center gap-1 rounded-md border border-border px-2.5 text-[10px] text-text-secondary hover:border-accent hover:text-text">
            {item.label}<ChevronRight size={11} />
          </Link>
        ))}
      </nav>
    </main>
  );
}
