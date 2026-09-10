'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertCircle,
  Aperture,
  ArrowDown,
  ArrowDownRight,
  ArrowRight,
  ArrowUp,
  ArrowUpRight,
  BarChart3,
  Bell,
  BrainCircuit,
  Check,
  ChevronRight,
  ChevronDown,
  CircleDot,
  CircleGauge,
  Database,
  Gauge,
  History,
  Layers3,
  LineChart,
  Loader2,
  Menu,
  Radio,
  RefreshCw,
  ScanSearch,
  ShieldAlert,
  SlidersHorizontal,
  Target,
  UserCircle,
  Wallet,
  X,
} from 'lucide-react';
import AddToPersonalPoolButton from '@/components/AddToPersonalPoolButton';
import StockKlineButton from '@/components/StockKlineButton';
import { apiFetch, friendlyApiError } from '@/lib/api';

type NullableNumber = number | null | undefined;

interface ForecastHorizon {
  id: string;
  label: string;
  state: string;
  probabilities: { upside: NullableNumber; main: NullableNumber; downside: NullableNumber };
  confidence_pct: NullableNumber;
  data_coverage_pct?: NullableNumber;
  fresh_coverage_pct?: NullableNumber;
  probability_qualified?: boolean;
  forecast_mode?: string;
  tendency_strength?: string;
  dominant_path?: 'upside' | 'main' | 'downside' | string;
  key_factors?: string[];
  benefited_sectors?: string[];
  pressured_sectors?: string[];
}

interface ForecastFactor {
  id: string;
  name: string;
  layer: string;
  state: string;
  value: NullableNumber;
  delta: NullableNumber;
  acceleration: NullableNumber;
  freshness: NullableNumber;
  reliability: number;
  source: string;
  observed: boolean;
}

interface ForecastSector {
  name: string;
  state: string;
  latest_change_pct: NullableNumber;
  flow_persistence_pct: NullableNumber;
  reason: string;
}

interface AlphaSeed {
  code: string;
  name: string;
  sector: string;
  alpha_stage: string;
  behavior_state: string;
  crowding_state: string;
  fomo_risk: string;
  largest_risk: string;
  score?: NullableNumber;
}

interface BehaviorSnapshot {
  market_psychology_state: string;
  psychology_transition: string;
  behavior_imbalance_score: NullableNumber;
  behavior_imbalance_level: string;
  crowding_state: string;
  panic_state: string;
  fomo_state: string;
  false_breakout_risk: string;
  structure_quality: string;
  bias_signals: Array<{ id: string; label: string; state: string; score: NullableNumber; coverage_pct: NullableNumber }>;
}

interface ResonanceChain {
  id: string;
  name: string;
  direction: string;
  activation_pct: NullableNumber;
  status: string;
  evidence: string[];
}

interface ForecastSnapshot {
  version: string;
  model_version: string;
  generated_at: string;
  forecast_date: string;
  phase: string;
  data_cutoff_time: string | null;
  cache_used?: boolean;
  risk_preference: { state: string; label: string; evidence: string[] };
  data_health: {
    completeness_pct: NullableNumber;
    fresh_coverage_pct: NullableNumber;
    level: string;
    high_confidence_allowed: boolean;
    missing_factors: Array<{ factor_id: string; name: string; source: string; action: string }>;
    stale_factors: Array<{ factor_id: string; name: string }>;
    confidence_ceiling_pct: NullableNumber;
    data_high_confidence_allowed?: boolean;
    execution_allowed?: boolean;
    prediction_mode?: string;
    calibration_qualified?: boolean;
    truth_status?: string;
    truth_status_label?: string;
    trading_permission_label?: string;
    block_reasons?: string[];
    sources?: string[];
  };
  timeline: ForecastHorizon[];
  resonance: {
    defensive_resonance_pct: NullableNumber;
    offensive_resonance_pct: NullableNumber;
    resonance_formation_pct: NullableNumber;
    risk_preference_label: string;
    chains: ResonanceChain[];
  };
  factors: { all: ForecastFactor[]; leading: ForecastFactor[]; propagation: ForecastFactor[]; confirmation: ForecastFactor[] };
  sector_forecasts: ForecastSector[];
  alpha_seeds: AlphaSeed[];
  behavior: BehaviorSnapshot;
  workbench_summary?: MarketSupplement;
  today_action_center?: TodayActionCenterData;
  historical_analogs?: Array<{
    case_id: string;
    label: string;
    period: string;
    similarity_pct: NullableNumber;
    similar_factors: string[];
    different_factors: string[];
    historical_path: string;
    do_not_copy_reason: string;
  }>;
  turning_points: {
    increase_offensive_probability: string[];
    increase_defensive_probability: string[];
    falsify_current_path: string[];
  };
  trading_skills?: {
    action?: string;
    market_permission?: { code?: string; label?: string; reasons?: string[] };
    active_skills?: Array<{ skill_id: string; skill_name: string; lifecycle_state: string; validation_status: string; runtime: string; data_level?: string }>;
    candidates?: Array<{ code: string; name: string; sector?: string; market_segment?: string; best_skill?: string; best_stage?: string; best_stage_label?: string; candidate_type?: string; candidate_label?: string; diagnosis_level?: string; skill_score?: NullableNumber; action?: string }>;
    scanned_count?: number;
    data_cutoff_time?: string | null;
    cache_used?: boolean;
    filters?: {
      exclude_star_market?: boolean;
      exclude_gem?: boolean;
      excluded_counts?: Record<string, number>;
      description?: string;
    };
    reflexivity?: { candidate_count?: number; risk_count?: number; model_version?: string; short_cover_policy?: string; l2_policy?: string };
  };
}

interface SkillFilterState {
  exclude_star_market: boolean;
  exclude_gem: boolean;
}

const TRADING_STAGE_LABELS: Record<string, string> = {
  INSUFFICIENT_DATA: '数据不足，暂不能判断',
  EFFICIENT_UP: '量价效率改善',
  INEFFICIENT_UP: '上涨但量价效率下降',
  EFFICIENT_DOWN: '下跌中的量价效率',
  ABSORBED_DOWN: '下跌但承接较强',
  ABSORPTION: '承接增强',
  SELL_PRESSURE: '抛压增强',
  BALANCED: '承接与抛压平衡',
  VOLUME_SHOCK: '成交异常触发',
  NO_RECENT_EVENT: '暂无近期异常成交',
  DISTRIBUTION_RISK: '异常成交后失守风险',
  TREND_CONFIRMATION: '异常成交后趋势确认',
  PANIC_EXCHANGE: '恐慌放量换手',
  NOISE: '异常成交未形成明确结构',
  RECLAIM_PENDING_CONFIRM: '假跌破回收，待确认',
  NO_RECLAIM: '未形成回收结构',
  REACCEL_CONFIRMED: '二次启动确认',
  REACCEL_FORMING: '二次启动形成',
  BASE: '缩量整理',
  PULLBACK: '趋势回调',
  FAILED: '趋势回调失效',
  NO_PRIOR_TREND: '前期趋势不足',
  NONE: '未形成低位异动',
  FIRST_SHOCK: '首次成交异动',
  CONTRACTION: '异动后成交收敛',
  SECOND_LAUNCH_CONFIRMED: '二次启动确认',
  SECOND_LAUNCH_FORMING: '二次启动形成',
  HIGH_QUALITY: '高质量突破',
  MEDIUM: '中等质量突破',
  FALSE_BREAKOUT_RISK: '疑似假突破风险',
  LOW_QUALITY: '突破质量偏低',
  NO_BREAKOUT: '尚未形成突破',
  FOMO: '追涨行为风险',
  PANIC: '恐慌行为风险',
  FAKE_BREAKOUT: '假突破行为风险',
  BEHAVIORAL_OVERSHOOT: '行为过冲',
  WAIT: '等待竞价/分时数据',
  CONFIRM: '竞价与分时确认',
  WEAK_CONFIRM: '弱确认，仍需观察',
  REJECT: '竞价/分时确认未通过',
  PANIC_ABSORPTION_CANDIDATE: '恐慌吸收候选',
  ALPHA_SEED_REFLEXIVITY: 'Alpha萌芽反身性',
  POSITIVE_REFLEXIVITY_CANDIDATE: '正向反身候选',
  HIGH_LEVEL_REFLEXIVITY_DECAY: '高位反身性衰减',
  NEGATIVE_REFLEXIVITY_ACCELERATION: '负向反身性加速',
};

const STATUS_LABELS: Record<string, string> = {
  CURRENT: '当前阶段',
  PASSED: '已完成',
  PENDING: '待执行',
  ALLOW: '允许执行',
  BLOCK: '禁止执行',
  NO_TRADE: '不交易',
  RESEARCH_ONLY: '研究模式',
  UNKNOWN: '待核验',
  UNAVAILABLE: '暂不可用',
  DISCOVERED: '已发现',
  FADING: '走弱',
  ON: '运行中',
  OFF: '未运行',
  ACTIVE: '已启用',
  SHADOW: '观察运行',
  EXPERIMENTAL: '实验中',
  VALIDATED: '已验证',
  NOT_TESTED: '尚未验证',
  OBSERVED: '已观测',
  MIXED: '混合状态',
  HIGH: '高',
  MEDIUM: '中',
  LOW: '低',
  pre_market: '盘前许可核验',
  auction: '09:25竞价确认',
  midday: '午间战术研究',
  tail: '14:55执行筛选',
  post_close: '盘后验证复盘',
  mixed: '混合状态',
};

function readableStatus(value?: string | null): string {
  if (!value) return '待核验';
  return STATUS_LABELS[value] || STATUS_LABELS[value.toUpperCase()] || value;
}

function readableTradingStage(code?: string, label?: string): string {
  return label || TRADING_STAGE_LABELS[code || ''] || (code ? readableStatus(code) : '观察');
}

interface MarketSupplement {
  available?: boolean;
  meta?: { decision_date?: string | null; updated_at?: string | null; is_realtime?: boolean; coverage_pct?: number | null; decision_scope?: string };
  headline_metrics?: { sentiment_temperature: NullableNumber; market_amount: NullableNumber; up_count: NullableNumber; down_count: NullableNumber; limit_up: NullableNumber; limit_down: NullableNumber; failed_limit_rate: NullableNumber; main_line: string | null };
  main_lines?: Array<{ rank: number; name: string; strength_score: NullableNumber; change_pct: NullableNumber; main_net_inflow: NullableNumber; lifecycle: string; evidence: string; leader: { code: string; name: string } }>;
  market_state?: { state_label: string; score: NullableNumber; execution_level: string; confidence_pct: NullableNumber };
  strategy_selector?: { max_total_position_pct: number; conclusion: string; strategies: Array<{ id: string; name: string; status: string; reason: string; href: string }> };
  decision_2026?: { trading_permission?: { label: string; code: string; max_total_position_pct: number; reasons: string[] }; why_not_buy?: { reasons: string[] }; opportunity_density?: { score: NullableNumber; label: string; independent_alpha_count: number } };
  audit?: { stale_components: string[]; missing_fields: string[]; data_sources: string[]; score_version: string; same_day_rule: string };
  quick_links?: Array<{ label: string; href: string }>;
}

interface TodayActionStage {
  id: string;
  label: string;
  time?: string;
  window_status?: string;
  result?: string;
  href?: string;
  candidate_count?: number | null;
}

interface TodayActionCenterData {
  trade_date?: string;
  generated_at?: string;
  current_stage?: string;
  current_task?: string;
  permission?: {
    code?: string;
    label?: string;
    max_total_position_pct?: NullableNumber;
    reasons?: string[];
  };
  stages?: TodayActionStage[];
  rule?: string;
}

interface MarketOverview {
  available?: boolean;
  data_date?: string | null;
  source?: string;
  is_realtime?: boolean;
  market_index?: {
    sh_index?: NullableNumber;
    sh_change?: NullableNumber;
    sh_change_pct?: NullableNumber;
    sh_amount?: NullableNumber;
    sh_volume?: NullableNumber;
    data_date?: string | null;
    source_updated_at?: string | null;
    is_realtime?: boolean;
    source?: string;
    indices?: Record<string, {
      value?: NullableNumber;
      price?: NullableNumber;
      change?: NullableNumber;
      change_pct?: NullableNumber;
      amount?: NullableNumber;
      data_date?: string | null;
      source?: string;
    }>;
    index_series?: Record<string, NullableNumber[]>;
  };
  market_breadth?: Record<string, { up?: NullableNumber; down?: NullableNumber; flat?: NullableNumber; total?: NullableNumber; ratio?: NullableNumber; data_date?: string | null; source?: string }>;
  limit_board?: { limit_up?: NullableNumber; limit_down?: NullableNumber };
  fund_flow?: { top_inflow?: Array<{ name: string; inflow?: NullableNumber }>; top_outflow?: Array<{ name: string; outflow?: NullableNumber }> };
  hot_sectors?: Array<{ name: string; change_pct?: NullableNumber; main_net_inflow?: NullableNumber; up_count?: NullableNumber; down_count?: NullableNumber }>;
  updated_at?: string;
  update_time?: string;
}

interface EventInterpretation {
  interpretation: string;
  generated_at?: string | null;
  data_cutoff_time?: string | null;
  snapshot_updated_at?: string | null;
  cache_used?: boolean;
  sources?: string[];
}

interface RadarEventItem {
  event_id: string;
  canonical_title: string;
  summary?: string;
  event_type?: string;
  source?: string;
  source_level?: string;
  event_score?: NullableNumber;
  alert_level?: string;
  direction?: string;
  status?: string;
  market_confirmation_score?: NullableNumber;
  topics?: Array<{ name: string; relevance_score?: NullableNumber }>;
  published_at?: string | null;
  last_updated_at?: string | null;
  cached?: boolean;
}

interface RadarEventDetail extends RadarEventItem {
  stocks?: Array<{ code: string; name: string; relation_type?: string; total_score?: NullableNumber; evidence_tag?: string; business_evidence?: string }>;
  quality?: { ai_explanation_policy?: string };
}

interface V51Summary {
  auction?: { coverage_pct?: NullableNumber; timeline_coverage_pct?: NullableNumber; observed_stocks?: number; time_series_snapshots?: number; quality?: { status?: string; warning?: string | null } };
  reward_punishment?: { state?: string; breadth_pct?: NullableNumber; rewarded_structures?: string[]; punished_structures?: string[] };
  leadership?: { sectors?: Array<{ name: string; leadership_score?: NullableNumber; change_pct?: NullableNumber }>; quality?: { status?: string } };
}

interface AiInterpretationSection {
  title: string;
  body: string;
}

type BreadthSnapshot = {
  up?: NullableNumber;
  down?: NullableNumber;
  flat?: NullableNumber;
  total?: NullableNumber;
  ratio?: NullableNumber;
  data_date?: string | null;
};

interface SimilarHistoryResponse {
  version?: string;
  current_forecast_date?: string;
  analogs?: ForecastSnapshot['historical_analogs'];
  method?: string;
}

const FORECAST_CACHE_KEY = 'v5_forecast_dashboard_cache';
const SUPPLEMENT_CACHE_KEY = 'v5_market_supplement_cache';
const OVERVIEW_CACHE_KEY = 'v5_market_overview_cache';

const FACTOR_LABELS: Record<string, string> = {
  fomo_behavior: '疑似追涨行为形成',
  panic_behavior: '恐慌行为扩散',
  false_breakout_risk: '假突破风险',
  behavior_imbalance: '行为失衡度',
  market_breadth: '市场宽度变化',
  sector_breadth: '板块扩散宽度',
  limit_up_down_balance: '涨跌停结构',
  failed_limit_rate: '炸板率变化',
  market_amount_vs_ma20: '成交额相对均值',
  sector_flow_persistence: '板块资金持续性',
  alpha_density: 'Alpha候选密度',
  crowding_risk: '高位拥挤风险',
  structure_health: '市场结构健康度',
  market_amount_percentile: '成交额历史分位',
  market_structure_transition: '市场结构转折',
  risk_preference_contraction: '风险偏好收缩',
  risk_preference_enhancement: '风险偏好增强',
  risk_preference_neutral: '风险偏好中性',
  financial_pit_validation: '盈利验证',
  policy_support: '政策边际',
  sp500_change: '标普500隔夜变化',
  nasdaq_change: '纳斯达克隔夜变化',
  us10y_change: '美国10年期利率',
  gold_change: '黄金价格变化',
};

function finite(value: NullableNumber): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function numberText(value: NullableNumber, digits = 0): string {
  return finite(value) ? value.toFixed(digits) : '--';
}

function percent(value: NullableNumber, digits = 0): string {
  return finite(value) ? `${value.toFixed(digits)}%` : '--';
}

function signedPercent(value: NullableNumber, digits = 2): string {
  if (!finite(value)) return '--';
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`;
}

function compactAmount(value: NullableNumber): string {
  if (!finite(value)) return '--';
  if (Math.abs(value) >= 1e12) return `${(value / 1e12).toFixed(1)}万亿`;
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(1)}亿`;
  return `${(value / 1e4).toFixed(0)}万`;
}

function localTime(raw: string | null | undefined): string {
  if (!raw) return '--';
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw.slice(0, 16).replace('T', ' ');
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
}

type Tone = 'up' | 'down' | 'warn' | 'accent';

function tone(value: string | number | null | undefined): Tone {
  if (typeof value === 'number') return value > 0 ? 'up' : value < 0 ? 'down' : 'accent';
  const text = String(value || '');
  if (/风险偏好.*增强|增强.*风险偏好/.test(text)) return 'up';
  if (/风险偏好.*收缩|收缩.*风险偏好/.test(text)) return 'warn';
  if (/风险|恐慌|禁止|阻断|极端|危险|负反馈|防御|退潮|承压/.test(text)) return 'warn';
  if (/流出|下跌|弱化|下降|减弱|偏空|空仓|做空/.test(text)) return 'down';
  if (/上涨|上升|增加|增强|修复|强化|走强|向上|流入|改善|低位|进攻|偏多|多头/.test(text)) return 'up';
  if (/分歧|震荡|中性|观察|中|试探|怀疑/.test(text)) return 'warn';
  return 'accent';
}

function toneClass(value: string | number | null | undefined): string {
  const colors = { up: 'text-up', down: 'text-down', warn: 'text-warn', accent: 'text-accent' };
  return colors[tone(value)];
}

function clamp(value: NullableNumber): number {
  return finite(value) ? Math.max(0, Math.min(100, value)) : 0;
}

function factorLabel(value: string): string {
  if (FACTOR_LABELS[value]) return FACTOR_LABELS[value];
  if (/^[a-z0-9_]+$/i.test(value)) return value.replace(/_/g, ' ') || '因子待核验';
  return value || '因子待核验';
}

function cleanAiText(value: string): string {
  return String(value || '')
    .replace(/```[a-zA-Z0-9_+-]*\s*/g, '')
    .replace(/```/g, '')
    .replace(/\*\*/g, '')
    .replace(/__/g, '')
    .replace(/^\s{0,3}#{1,6}\s*/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function splitAiInterpretation(value: string): AiInterpretationSection[] {
  const headings = new Set(['当前事实', '因子共振链', '主要矛盾', '发展方向与风险', '下一观察条件']);
  const sections: AiInterpretationSection[] = [];
  let current: AiInterpretationSection | null = null;
  for (const rawLine of cleanAiText(value).split('\n')) {
    const line = rawLine.trim();
    if (!line) continue;
    const match = line.match(/^(?:\d+[.)、]\s*)?(当前事实|因子共振链|主要矛盾|发展方向与风险|下一观察条件)\s*[:：]?\s*(.*)$/);
    if (match && headings.has(match[1])) {
      current = { title: match[1], body: match[2] || '' };
      sections.push(current);
    } else if (current) {
      current.body = current.body ? `${current.body}\n${line}` : line;
    } else {
      current = { title: '综合判断', body: line };
      sections.push(current);
    }
  }
  return sections.length ? sections : [{ title: '综合判断', body: '当前没有返回可读解读。' }];
}

function stateLabel(value: string | null | undefined): string {
  const map: Record<string, string> = {
    risk_preference_contraction: '风险偏好收缩',
    risk_preference_enhancement: '风险偏好增强',
    risk_preference_neutral: '风险偏好中性',
    improving: '改善',
    weakening: '走弱',
    stable: '稳定',
    pre_market: '盘前研究',
    intraday: '盘中研究',
    post_market: '盘后复盘',
  };
  return map[value || ''] || value || '核验中';
}

function LoadingScreen({ progress }: { progress: number }) {
  const stage = progress < 30 ? '读取最近完整交易日' : progress < 65 ? '对齐因子、行为与市场状态' : '生成多周期前瞻路径';
  return (
    <main className="v5-page v5-loading" role="status">
      <div className="v5-loading-box">
        <Loader2 size={24} className="animate-spin text-accent" />
        <div className="mt-4 text-sm text-text">{stage}</div>
        <div className="mt-2 text-xs text-text-secondary">数据沿用最近可核验快照，缺失字段不以默认值代替</div>
        <div className="v5-progress mt-5"><span style={{ width: `${progress}%` }} /></div>
        <div className="mt-2 font-mono text-[10px] text-text-secondary">{progress}%</div>
      </div>
    </main>
  );
}

function SectionHeader({ icon: Icon, title, subtitle, action }: { icon: typeof Activity; title: string; subtitle?: string; action?: React.ReactNode }) {
  return (
    <div className="v5-section-header">
      <div className="flex min-w-0 items-center gap-2">
        <Icon size={15} className="shrink-0 text-accent" />
        <div className="min-w-0"><h2 className="truncate text-sm font-semibold text-text">{title}</h2>{subtitle && <p className="mt-0.5 truncate text-[10px] text-text-secondary">{subtitle}</p>}</div>
      </div>
      {action}
    </div>
  );
}

function ThinBar({ value, color = 'accent' }: { value: NullableNumber; color?: 'accent' | 'up' | 'down' | 'warn' }) {
  const barColors = { accent: '#4C8DFF', up: '#EF5350', down: '#26A69A', warn: '#D9A441' };
  return <div className="v5-thin-bar"><span style={{ width: `${clamp(value)}%`, backgroundColor: barColors[color] }} /></div>;
}

function Sparkline({ values, color = '#4C8DFF', label = '数据轨迹' }: { values: NullableNumber[]; color?: string; label?: string }) {
  const points = values.filter(finite);
  if (points.length < 2) return <div className="v5-sparkline v5-sparkline-empty" aria-label={`${label}暂无序列`} />;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const spread = max - min || 1;
  const path = points.map((point, index) => {
    const x = points.length === 1 ? 0 : (index / (points.length - 1)) * 100;
    const y = 24 - ((point - min) / spread) * 18;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg className="v5-sparkline" viewBox="0 0 100 26" role="img" aria-label={label} preserveAspectRatio="none">
      <path d="M0 23H100" className="v5-sparkline-grid" />
      <polyline points={path} fill="none" stroke={color} strokeWidth="1.35" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function ProbabilityDonut({ item }: { item: ForecastHorizon }) {
  if (!item.probability_qualified) {
    return (
      <div className="v5-probability-chart" role="img" aria-label={`${shortHorizonLabel(item)}尚未完成概率校准`}>
        <div className="v5-probability-donut">
          <svg viewBox="0 0 44 44" aria-hidden="true"><circle className="v5-probability-track" cx="22" cy="22" r="15.5" /></svg>
          <strong className="text-[8px]">待校准</strong>
        </div>
        <div className="v5-probability-legend"><span><i style={{ backgroundColor: '#667085' }} />当前只输出方向研究，不作为成功概率</span></div>
      </div>
    );
  }
  const parts = [
    { key: 'upside', label: '上行', value: item.probabilities?.upside, color: '#EF5350' },
    { key: 'main', label: '主情景', value: item.probabilities?.main, color: '#5A9BFF' },
    { key: 'downside', label: '下行', value: item.probabilities?.downside, color: '#26A69A' },
  ];
  const radius = 15.5;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  const total = parts.reduce((sum, part) => sum + (finite(part.value) ? Math.max(0, part.value) : 0), 0);
  const main = parts.find((part) => part.key === 'main')?.value;

  return (
    <div className="v5-probability-chart" role="img" aria-label={`${shortHorizonLabel(item)}概率：上行${percent(item.probabilities?.upside)}，主情景${percent(item.probabilities?.main)}，下行${percent(item.probabilities?.downside)}`}>
      <div className="v5-probability-donut">
        <svg viewBox="0 0 44 44" aria-hidden="true">
          <circle className="v5-probability-track" cx="22" cy="22" r={radius} />
          {total > 0 && parts.map((part) => {
            const value = finite(part.value) ? Math.max(0, part.value) : 0;
            const length = (value / Math.max(100, total)) * circumference;
            const currentOffset = offset;
            offset += length;
            if (length <= 0) return null;
            return <circle key={part.key} className="v5-probability-segment" cx="22" cy="22" r={radius} stroke={part.color} strokeDasharray={`${length} ${circumference - length}`} strokeDashoffset={-currentOffset} />;
          })}
        </svg>
        <strong>{percent(main)}</strong>
      </div>
      <div className="v5-probability-legend">
        {parts.map((part) => <span key={part.key}><i style={{ backgroundColor: part.color }} />{part.label} {percent(part.value)}</span>)}
      </div>
    </div>
  );
}

function ArcGauge({ value, color = '#EF5350', label }: { value: NullableNumber; color?: string; label: string }) {
  const safe = clamp(value);
  const arcLength = 157;
  return (
    <div className="v5-arc-gauge" aria-label={`${label}${finite(value) ? `${safe.toFixed(0)}%` : '暂无数据'}`}>
      <svg viewBox="0 0 120 72" aria-hidden="true">
        <path d="M10 64 A50 50 0 0 1 110 64" className="v5-arc-track" />
        <path d="M10 64 A50 50 0 0 1 110 64" className="v5-arc-value" style={{ stroke: color, strokeDasharray: `${(safe / 100) * arcLength} ${arcLength}` }} />
      </svg>
      <strong>{finite(value) ? Math.round(value) : '--'}</strong>
      <span>{label}</span>
    </div>
  );
}

function shortHorizonLabel(item: ForecastHorizon): string {
  if (item.id === 'short_1_3d') return '1-3天';
  if (item.id === 'week_1w') return '1周';
  if (item.id === 'month_1m') return '1个月';
  if (item.id === 'quarter_1q') return '1季度';
  return item.label.replace(/^未来(?:约)?/, '').replace('个交易日', '天');
}

function directionFor(item: ForecastHorizon): { glyph: string; color: Tone } {
  const upside = item.probabilities?.upside;
  const downside = item.probabilities?.downside;
  if (finite(downside) && finite(upside) && downside > upside + 5) return { glyph: '↓', color: 'down' };
  if (finite(upside) && finite(downside) && upside > downside + 5) return { glyph: '↗', color: 'up' };
  if (item.dominant_path === 'downside') return { glyph: '↓', color: 'down' };
  if (item.dominant_path === 'upside') return { glyph: '↗', color: 'up' };
  return { glyph: '→', color: 'warn' };
}

function horizonStateLabel(value: string | null | undefined): string {
  const text = String(value || '核验中');
  if (/防御|承压|退潮/.test(text)) return '防御偏强';
  if (/修复|回暖/.test(text)) return '震荡修复';
  if (/转强|增强|向上/.test(text)) return '结构转强';
  if (/分歧|震荡|分化/.test(text)) return '震荡分化';
  return text.length > 8 ? `${text.slice(0, 8)}…` : text;
}

function IndexCard({ label, value, changePct, trace, source }: { label: string; value: NullableNumber; changePct: NullableNumber; trace: NullableNumber[]; source?: string }) {
  const changeColor = finite(changePct) ? (changePct >= 0 ? '#EF5350' : '#26A69A') : '#667085';
  return (
    <article className="v5-index-card">
      <div className="text-[10px] text-text-secondary">{label}</div>
      <div className="mt-1 font-mono text-[17px] text-text">{finite(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '--'}</div>
      <div className="mt-0.5 font-mono text-[10px]" style={{ color: changeColor }}>{signedPercent(changePct, 2)}</div>
      <Sparkline values={trace} color={changeColor} label={`${label}走势`} />
      {source && <div className="mt-1 truncate text-[8px] text-text-muted">{source}</div>}
    </article>
  );
}

function allMarketBreadth(overview: MarketOverview | null): BreadthSnapshot | null {
  if (!overview?.market_breadth) return null;
  const all = overview.market_breadth['全市场'];
  if (all) return all;
  const first = Object.values(overview.market_breadth)[0];
  return first || null;
}

function StatusPill({ children, value }: { children: React.ReactNode; value?: string | null }) {
  return <span className={`v5-pill v5-pill-${tone(value || String(children))}`}>{children}</span>;
}

function EmptyState({ text = '暂无可核验数据' }: { text?: string }) {
  return <div className="py-8 text-center text-xs text-text-secondary">{text}</div>;
}

const FALLBACK_ACTION_STAGES: TodayActionStage[] = [
  { id: 'pre_market', label: '盘前许可核验', time: '开盘前', window_status: 'CURRENT', result: '核验真值、新鲜度、隔夜事件和持仓风险', href: '/market/v4' },
  { id: 'auction', label: '09:25竞价确认', time: '09:15-09:25', window_status: 'PENDING', result: '承接前一交易日14:55候选，等待真实竞价序列', href: '/pro/auction' },
  { id: 'midday', label: '午间战术研究', time: '11:42', window_status: 'PENDING', result: '检查上午强弱、主要矛盾与板块内部结构', href: '/research/midday' },
  { id: 'tail', label: '14:55执行筛选', time: '14:55', window_status: 'PENDING', result: '只跟踪满足规则的候选，条件不完整就放弃', href: '/quant' },
  { id: 'post_close', label: '盘后验证复盘', time: '收盘后', window_status: 'PENDING', result: '核对判断、模拟执行、盈亏与放弃原因', href: '/pro/research' },
];

function fallbackActionCenter(forecast: ForecastSnapshot, supplement: MarketSupplement | null): TodayActionCenterData {
  const permission = supplement?.decision_2026?.trading_permission;
  const health = forecast.data_health;
  const permissionCode = String(permission?.code || 'UNKNOWN').toUpperCase();
  const blocked = health.truth_status === 'FAIL' || health.execution_allowed === false || permissionCode === 'BLOCK' || permissionCode === 'NO_TRADE';
  const researchOnly = !blocked && health.calibration_qualified === false;
  const code = blocked ? 'BLOCK' : researchOnly ? 'RESEARCH_ONLY' : permissionCode || 'UNKNOWN';
  const phase = forecast.phase === 'auction_0925' ? 'auction' : forecast.phase === 'midday' || forecast.phase === 'morning_1040' || forecast.phase === 'midday_1130' ? 'midday' : forecast.phase === 'afternoon_1330' || forecast.phase === 'close_1500' ? 'tail' : forecast.phase === 'post_close' ? 'post_close' : 'pre_market';
  const order = ['pre_market', 'auction', 'midday', 'tail', 'post_close'];
  const currentIndex = order.indexOf(phase);
  const stages = FALLBACK_ACTION_STAGES.map((stage) => ({ ...stage, window_status: order.indexOf(stage.id) === currentIndex ? 'CURRENT' : order.indexOf(stage.id) < currentIndex ? 'PASSED' : 'PENDING' }));
  const reasons = Array.from(new Set([...(health.block_reasons || []), ...(permission?.reasons || [])])).slice(0, 5);
  return {
    trade_date: forecast.forecast_date,
    generated_at: forecast.generated_at,
    current_stage: phase,
    current_task: phase === 'pre_market' ? '先核验真值、新鲜度、隔夜事件和持仓风险，不急于找股票。' : phase === 'auction' ? '竞价序列或覆盖不足时只观察，不确认隔夜策略。' : phase === 'midday' ? '完成上午市场尸检，判断主要矛盾是否发生变化。' : phase === 'tail' ? '只跟踪满足规则的候选；条件不完整就放弃，不临时放宽。' : '复盘系统判断与实际结果，记录执行偏差和下一次改进。',
    permission: {
      code,
      label: blocked ? '数据资格不足，禁止执行' : researchOnly ? '可研究，预测概率尚未校准' : permission?.label || '许可通过',
      max_total_position_pct: blocked || researchOnly ? 0 : permission?.max_total_position_pct,
      reasons,
    },
    stages,
    rule: '按当日时序执行；09:25竞价承接前一交易日14:55候选，任何候选都不能绕过数据质量闸门。',
  };
}

function TodayActionCenter({ forecast, supplement }: { forecast: ForecastSnapshot; supplement: MarketSupplement | null }) {
  const action = forecast.today_action_center || fallbackActionCenter(forecast, supplement);
  const permission = action.permission || {};
  const code = String(permission.code || 'UNKNOWN').toUpperCase();
  const permissionTone = code === 'ALLOW' ? 'text-up' : code === 'BLOCK' || code === 'NO_TRADE' ? 'text-down' : 'text-warn';
  const reasons = permission.reasons || [];
  const stages = action.stages?.length ? action.stages : FALLBACK_ACTION_STAGES;
  return (
    <section id="action-center" className="v5-panel mb-2 min-w-0">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0"><div className="flex items-center gap-2"><Target size={15} className="shrink-0 text-accent" /><h2 className="text-sm font-semibold text-text">今日行动中心</h2></div><p className="mt-1 text-[10px] leading-4 text-text-secondary">先统一许可，再按当前阶段执行；系统只提供研究与风控边界，不替代你的下单决定。</p></div>
        <div className="flex max-w-full flex-wrap items-center gap-2 text-[10px]"><span className="text-text-secondary">交易许可</span><span className={`rounded border border-border px-2 py-1 font-medium ${permissionTone}`}>{permission.label || readableStatus(code)}</span>{finite(permission.max_total_position_pct) && <span className="font-mono text-text-secondary">仓位上限 {permission.max_total_position_pct}%</span>}</div>
      </div>
      <div className="grid min-w-0 gap-0 md:grid-cols-[minmax(0,1.05fr)_minmax(0,1.25fr)]">
        <div className="min-w-0 border-b border-border p-4 md:border-b-0 md:border-r">
          <div className="grid min-w-0 gap-3 sm:grid-cols-2 md:grid-cols-1 lg:grid-cols-2">
            <div className="min-w-0"><span className="text-[10px] text-text-secondary">当前阶段</span><strong className="mt-1 block truncate text-sm text-text" title={readableStatus(action.current_stage)}>{readableStatus(action.current_stage)}</strong></div>
            <div className="min-w-0"><span className="text-[10px] text-text-secondary">当前任务</span><p className="mt-1 break-words text-[11px] leading-5 text-text">{action.current_task || '等待系统生成当前任务'}</p></div>
          </div>
          <div className="mt-4 border-t border-border pt-3"><div className="text-[10px] text-text-secondary">为什么现在{code === 'ALLOW' ? '可做' : '不可直接做'}</div>{reasons.length ? <ul className="mt-2 space-y-1.5 text-[10px] leading-4 text-text-secondary">{reasons.slice(0, 4).map((reason) => <li key={reason} className="flex gap-2"><span className={permissionTone}>·</span><span className="break-words">{reason}</span></li>)}</ul> : <p className="mt-2 text-[10px] leading-4 text-text-secondary">当前没有新增阻断理由，仍需按阶段条件完成确认。</p>}</div>
        </div>
        <div className="min-w-0 p-4"><div className="mb-2 flex items-center justify-between gap-2"><span className="text-[10px] text-text-secondary">五阶段流程</span><span className="truncate text-[9px] text-text-muted">{action.rule || '阶段状态以当前数据快照为准'}</span></div><div className="grid min-w-0 gap-2 sm:grid-cols-5">{stages.map((stage) => { const status = String(stage.window_status || 'UNKNOWN').toUpperCase(); const content = <><span className="font-mono text-[9px] text-text-muted">{stage.time || '--'}</span><strong className="mt-1 block break-words text-[10px] leading-4 text-text">{stage.label || readableStatus(stage.id)}</strong><span className={`mt-1 block text-[9px] ${status === 'CURRENT' ? 'text-accent' : status === 'PASSED' ? 'text-up' : 'text-text-secondary'}`}>{readableStatus(status)}</span></>; return stage.href ? <Link key={stage.id} href={stage.href} className="min-w-0 rounded border border-border bg-black/10 p-2 transition hover:border-accent/50">{content}</Link> : <div key={stage.id} className="min-w-0 rounded border border-border bg-black/10 p-2">{content}</div>; })}</div></div>
      </div>
    </section>
  );
}

function ForecastTimeline({
  forecast,
  onRefresh,
  skillFilters,
  onSkillFilterChange,
  skillFilterBusy,
  skillsLoading,
}: {
  forecast: ForecastSnapshot;
  onRefresh: () => void;
  skillFilters: SkillFilterState;
  onSkillFilterChange: (next: SkillFilterState) => void;
  skillFilterBusy: boolean;
  skillsLoading: boolean;
}) {
  const timeline = (forecast.timeline || []).slice(0, 4);
  return (
    <section id="forecast" className="v5-panel v5-forecast-panel">
      <SectionHeader icon={BrainCircuit} title="AI市场前瞻预测引擎" subtitle="基于多因子共振与因果链推演" action={<div className="v5-panel-header-meta"><span>预测更新时间：{localTime(forecast.generated_at)}</span><button type="button" className="v5-mini-refresh" onClick={onRefresh} title="刷新预测" aria-label="刷新预测"><RefreshCw size={12} />刷新预测</button></div>} />
      <div className="v5-forecast-caption">未来市场路径概率 <span>（多周期时间线预测）</span></div>
      <div className="v5-timeline">
        {timeline.map((item) => (
          <article key={item.id} className="v5-horizon">
            {(() => {
              const direction = directionFor(item);
              return <>
                <div className="flex items-center justify-between gap-2"><span className="text-[11px] text-text-secondary">{shortHorizonLabel(item)}</span><span className={`font-mono text-[10px] ${toneClass(item.state)}`}>{item.probability_qualified ? `${percent(item.confidence_pct)} 置信` : '研究级 · 待校准'}</span></div>
                <div className={`mt-2 min-h-[34px] text-[15px] font-semibold ${toneClass(item.state)}`} title={item.state || '核验中'}><span>{horizonStateLabel(item.state)}</span></div>
                <div className={`v5-horizon-signal v5-horizon-signal-${direction.color}`}><strong>{item.probability_qualified ? percent(finite(item.probabilities.main) ? item.probabilities.main : item.confidence_pct) : (item.tendency_strength || '研究倾向')}</strong><span className={`v5-direction v5-direction-${direction.color}`} aria-hidden="true">{direction.glyph}</span></div>
                <ProbabilityDonut item={item} />
                <div className="v5-horizon-reasons">{(item.key_factors || []).slice(0, 3).map((factor) => <div key={factor} className="truncate">{factorLabel(factor)}</div>)}</div>
              </>;
            })()}
          </article>
        ))}
        <article className="v5-horizon v5-current-horizon"><div className="text-[11px] text-text-secondary">当前判断</div><div className={`mt-2 min-h-[40px] line-clamp-2 text-[13px] leading-5 font-semibold ${toneClass(forecast.risk_preference.label)}`} title={forecast.risk_preference.label || '震荡分化'}>{forecast.risk_preference.label || '震荡分化'}</div><div className="v5-current-orbit"><CircleGauge size={38} strokeWidth={1} /></div><div className="mt-2 border-t border-border pt-2 text-[10px] text-text-secondary">建议策略</div><div className="mt-1 text-[10px] leading-4 text-text-secondary">控制仓位 · 精选结构性机会</div></article>
      </div>
      <div className="v5-resonance-strip">
        <div className="v5-resonance-item"><div className="flex justify-between text-[10px]"><span className="text-text-secondary">风险偏好收缩形成度</span><b className="font-mono text-warn">{percent(forecast.resonance.defensive_resonance_pct)}</b></div><ThinBar value={forecast.resonance.defensive_resonance_pct} color="warn" /></div>
        <div className="v5-resonance-item"><div className="flex justify-between text-[10px]"><span className="text-text-secondary">进攻共振形成度</span><b className="font-mono text-up">{percent(forecast.resonance.offensive_resonance_pct)}</b></div><ThinBar value={forecast.resonance.offensive_resonance_pct} color="up" /></div>
        <div className="v5-resonance-summary"><span className="text-text-secondary">多周期共振</span><strong className="text-text">{forecast.resonance.risk_preference_label || '状态核验中'}</strong></div>
      </div>
      <SkillRuntimeStrip
        skills={forecast.trading_skills}
        loading={skillsLoading}
        filters={skillFilters}
        onFilterChange={onSkillFilterChange}
        busy={skillFilterBusy}
      />
    </section>
  );
}

function SkillRuntimeStrip({
  skills,
  loading,
  filters,
  onFilterChange,
  busy,
}: {
  skills?: ForecastSnapshot['trading_skills'];
  loading: boolean;
  filters: SkillFilterState;
  onFilterChange: (next: SkillFilterState) => void;
  busy: boolean;
}) {
  if (!skills) return loading ? <div className="border-t border-border px-4 py-3 text-[10px] text-text-secondary"><Loader2 size={12} className="mr-1 inline animate-spin text-accent" />交易技能异步扫描中，主预测内容已可用。</div> : null;
  const permission = skills.market_permission;
  const candidates = skills.candidates || [];
  const update = (key: keyof SkillFilterState) => onFilterChange({ ...filters, [key]: !filters[key] });
  return <div className="v5-skill-runtime">
    <div className="v5-skill-runtime-head">
      <div><span className="v5-skill-kicker">交易技能漏斗</span><strong>当前运行技能与可观测苗头</strong></div>
      <span className={`v5-skill-permission ${permission?.code === 'ALLOW' ? 'is-up' : permission?.code === 'BLOCK' ? 'is-down' : 'is-warn'}`}>{permission?.label || skills.action || '核验中'}</span>
    </div>
    <div className="v5-skill-filter-row" aria-label="可交易板块筛选">
      <span className="v5-skill-filter-title">可交易范围</span>
      <label><input type="checkbox" checked={filters.exclude_star_market} disabled={busy} onChange={() => update('exclude_star_market')} />排除科创板</label>
      <label><input type="checkbox" checked={filters.exclude_gem} disabled={busy} onChange={() => update('exclude_gem')} />排除创业板（300/301/302）</label>
      <span className="v5-skill-filter-hint">默认按主板权限扫描；取消勾选后纳入对应板块研究</span>
      {busy && <Loader2 size={12} className="animate-spin text-accent" aria-label="正在重新扫描" />}
    </div>
    <div className="v5-skill-runtime-body">
      <div className="v5-skill-list">{(skills.active_skills || []).slice(0, 10).map((item) => <span key={item.skill_id} className={`v5-skill-chip ${item.runtime === 'ON' ? 'is-on' : 'is-off'}`} title={`${item.skill_name} · ${readableStatus(item.lifecycle_state)} · ${readableStatus(item.validation_status)}`}><i />{item.skill_name}</span>)}{!(skills.active_skills || []).length && <span className="text-[10px] text-text-secondary">暂无满足许可的运行技能</span>}</div>
      <div className="v5-skill-candidates">{candidates.slice(0, 3).map((item) => { const stage = item.candidate_label || readableTradingStage(item.best_stage, item.best_stage_label); return <div key={item.code} className="v5-skill-candidate"><span className="font-mono text-[10px] text-text">{item.code}</span><span className="min-w-0 truncate text-[10px] text-text-secondary">{item.name}</span><span className="min-w-0 truncate text-[9px] text-accent" title={`${stage}${item.diagnosis_level ? ` · ${item.diagnosis_level}` : ''}`}>{stage}</span></div>; })}{!candidates.length && <span className="text-[10px] text-text-secondary">当前没有通过市场/板块许可的候选，系统保持观察。</span>}</div>
    </div>
    <div className="v5-skill-runtime-foot">已扫描 {skills.scanned_count ?? '--'} 只 · 反身性候选 {skills.reflexivity?.candidate_count ?? '--'} / 风险 {skills.reflexivity?.risk_count ?? '--'} · 数据截止 {skills.data_cutoff_time ? localTime(skills.data_cutoff_time) : '--'} · 技能只提供研究候选，不连接下单</div>
  </div>;
}

function MarketStatePanel({ supplement, overview }: { supplement: MarketSupplement | null; overview: MarketOverview | null }) {
  const metrics = supplement?.headline_metrics;
  const marketState = supplement?.market_state;
  const index = overview?.market_index;
  const breadthSnapshot = allMarketBreadth(overview);
  const up = finite(breadthSnapshot?.up) ? breadthSnapshot.up : metrics?.up_count;
  const down = finite(breadthSnapshot?.down) ? breadthSnapshot.down : metrics?.down_count;
  const flat = finite(breadthSnapshot?.flat) ? breadthSnapshot.flat : null;
  const breadth = finite(breadthSnapshot?.ratio) ? breadthSnapshot.ratio : finite(up) && finite(down) ? up / Math.max(1, up + down) * 100 : null;
  const limitUp = overview?.limit_board?.limit_up ?? metrics?.limit_up;
  const limitDown = overview?.limit_board?.limit_down ?? metrics?.limit_down;
  // During call auction an upstream may legitimately publish a zero amount.
  // Keep the last complete market amount instead of presenting zero as the
  // full-session turnover.
  const amount = finite(index?.sh_amount) && index.sh_amount > 0
    ? index.sh_amount
    : finite(metrics?.market_amount) && metrics.market_amount > 0
      ? metrics.market_amount
      : null;
  const sourceDate = overview?.data_date || supplement?.meta?.decision_date;
  const marketSentiment = metrics?.sentiment_temperature;
  const indices = overview?.market_index?.indices || {};
  const shQuote = indices.shanghai || indices.sh || {};
  const chinextQuote = indices.chinext || indices.cyb || {};
  const hs300Quote = indices.hs300 || indices['沪深300'] || {};
  const indexDate = index?.data_date || shQuote.data_date;
  const snapshotLabel = index?.is_realtime
    ? `指数实时 ${indexDate || sourceDate || '--'}`
    : sourceDate
      ? `最近完整快照 ${sourceDate}`
      : '等待市场快照';
  const indexSeries = overview?.market_index?.index_series || {};
  return (
    <section id="market" className="v5-panel v5-market-panel">
      <SectionHeader icon={Activity} title="大盘状态" subtitle={snapshotLabel} />
      <div className="v5-index-grid">
        <IndexCard label="上证指数" value={shQuote.value ?? shQuote.price ?? index?.sh_index} changePct={shQuote.change_pct ?? index?.sh_change_pct} trace={indexSeries.shanghai || indexSeries.sh || []} source={shQuote.source || (overview?.source === 'cache' ? '缓存' : overview?.source)} />
        <IndexCard label="创业板指" value={chinextQuote.value ?? chinextQuote.price} changePct={chinextQuote.change_pct} trace={indexSeries.chinext || indexSeries.cyb || []} source={chinextQuote.source} />
        <IndexCard label="沪深300" value={hs300Quote.value ?? hs300Quote.price} changePct={hs300Quote.change_pct} trace={indexSeries.hs300 || []} source={hs300Quote.source} />
      </div>
      <div className="v5-market-overview-grid">
        <div className="v5-breadth-box">
          <div className="v5-mini-title">市场宽度</div>
          <div className="v5-breadth-body">
            <div className="v5-breadth-numbers"><span>上涨家数<strong className="text-up">{numberText(up)}</strong></span><span>下跌家数<strong className="text-down">{numberText(down)}</strong></span><span>平盘<strong>{numberText(flat)}</strong></span></div>
            <ArcGauge value={breadth} color={breadth !== null && breadth >= 50 ? '#EF5350' : '#26A69A'} label="市场宽度" />
            <div className="v5-breadth-bottom"><span>涨停 <b className="text-up">{numberText(limitUp)}</b></span><span>跌停 <b className="text-down">{numberText(limitDown)}</b></span></div>
          </div>
        </div>
        <div className="v5-market-mini-grid">
          <div className="v5-market-mini"><span>成交额</span><strong>{compactAmount(amount)}</strong><em className={toneClass(index?.sh_change_pct)}>{amount === null ? '集合竞价待形成' : signedPercent(index?.sh_change_pct)}</em><Sparkline values={[]} color="#4C8DFF" label="成交额趋势" /></div>
          <div className="v5-market-mini"><span>市场情绪</span><ArcGauge value={marketSentiment} color="#D9A441" label="情绪温度" /></div>
        </div>
      </div>
      <div className="v5-market-state-footer"><span>当前状态 <b className={toneClass(marketState?.state_label)}>{marketState?.state_label || '核验中'}</b></span><span>炸板率 <b className={toneClass(metrics?.failed_limit_rate && metrics.failed_limit_rate > 25 ? '风险' : '稳定')}>{percent(metrics?.failed_limit_rate, 1)}</b></span></div>
    </section>
  );
}

function EventMonitor({ forecast, supplement }: { forecast: ForecastSnapshot; supplement: MarketSupplement | null }) {
  const [interpretation, setInterpretation] = useState<EventInterpretation | null>(null);
  const [interpretationLoading, setInterpretationLoading] = useState(false);
  const [interpretationError, setInterpretationError] = useState('');
  const events = [...(forecast.risk_preference.evidence || []), ...(forecast.turning_points.increase_defensive_probability || [])].slice(0, 5);
  const sourceCount = supplement?.audit?.data_sources?.length || forecast.data_health.sources?.length || 0;
  const explain = async () => {
    if (interpretationLoading) return;
    setInterpretationLoading(true);
    setInterpretationError('');
    try {
      const response = await apiFetch<{ code: number; data: EventInterpretation }>('/forecast/event-interpretation', { method: 'POST', timeoutMs: 60000, cache: 'no-store' });
      setInterpretation(response.data);
    } catch (error) {
      setInterpretationError(friendlyApiError(error, '事件解读暂时不可用'));
    } finally {
      setInterpretationLoading(false);
    }
  };
  return (
    <section className="v5-panel">
      <SectionHeader icon={Bell} title="重要事件监控" subtitle={`${sourceCount || '--'} 个已登记数据源`} action={<button type="button" onClick={() => void explain()} disabled={interpretationLoading} className="v5-mini-refresh" title="调用AI解读因子共振链">{interpretationLoading ? <Loader2 size={12} className="animate-spin" /> : <BrainCircuit size={12} />}AI解读</button>} />
      {events.length ? <div className="divide-y divide-border">{events.map((event, index) => <div key={`${event}-${index}`} className="v5-event-row"><span className="v5-event-tag">{index % 2 === 0 ? '市场' : '风险'}</span><span className="min-w-0 flex-1 truncate text-xs text-text" title={event}>{factorLabel(event)}</span><span className="shrink-0 text-[10px] text-text-secondary">{index === 0 ? '当前' : '监控'}</span></div>)}</div> : <EmptyState text="当前没有新增事件摘要" />}
      {interpretation && <div className="v5-event-ai v5-event-monitor-expanded"><div className="v5-event-ai-title"><BrainCircuit size={14} />资深交易员解读 <span>已按事实、矛盾和观察条件整理</span></div><div className="v5-event-ai-grid">{splitAiInterpretation(interpretation.interpretation).map((section) => <article key={section.title} className="v5-event-ai-section"><h3>{section.title}</h3><p>{section.body}</p></article>)}</div><div className="v5-event-ai-meta">{interpretation.cache_used ? '部分沿用缓存' : '本轮数据'} · 因子截止 {localTime(interpretation.data_cutoff_time)} · 快照 {localTime(interpretation.snapshot_updated_at)}</div></div>}
      {interpretationError && <div className="v5-event-ai-error">{interpretationError}</div>}
      <div className="border-t border-border px-4 py-2.5 text-[10px] leading-4 text-text-secondary">事件只作为因果链证据，不能单独生成买卖结论。</div>
    </section>
  );
}

function RadarEventPanel() {
  const [events, setEvents] = useState<RadarEventItem[]>([]);
  const [selected, setSelected] = useState<RadarEventDetail | null>(null);
  const [interpretation, setInterpretation] = useState('');
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams({ limit: '8' });
      if (refresh) params.set('refresh', 'true');
      const response = await apiFetch<{ code: number; data: { events: RadarEventItem[] } }>(`/radar/events?${params.toString()}`, { cache: 'no-store', timeoutMs: 15000 });
      setEvents(response.data?.events || []);
    } catch (caught) {
      setError(friendlyApiError(caught, '事件雷达暂时不可用'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(false); const timer = window.setInterval(() => void load(false), 60_000); return () => window.clearInterval(timer); }, [load]);

  const open = async (event: RadarEventItem) => {
    setDetailLoading(true);
    setInterpretation('');
    try {
      const response = await apiFetch<{ code: number; data: RadarEventDetail }>(`/radar/events/${encodeURIComponent(event.event_id)}`, { cache: 'no-store', timeoutMs: 12000 });
      setSelected(response.data);
    } catch (caught) {
      setError(friendlyApiError(caught, '事件详情暂时不可用'));
    } finally {
      setDetailLoading(false);
    }
  };

  const explain = async () => {
    if (!selected) return;
    setDetailLoading(true);
    try {
      const response = await apiFetch<{ code: number; data: { interpretation: string } }>(`/radar/events/${encodeURIComponent(selected.event_id)}/interpretation`, { method: 'POST', timeoutMs: 45000, cache: 'no-store' });
      setInterpretation(cleanAiText(response.data?.interpretation || '暂无可读解读'));
    } catch (caught) {
      setError(friendlyApiError(caught, '事件AI解读暂时不可用'));
    } finally {
      setDetailLoading(false);
    }
  };

  return <section className="v5-panel v5-radar-panel">
    <SectionHeader icon={Radio} title="AI实时事件雷达" subtitle="发现、验源、题材映射与市场确认" action={<button type="button" className="v5-mini-refresh" onClick={() => void load(true)} disabled={loading} title="刷新事件雷达">{loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}刷新</button>} />
    <div className="v5-radar-summary"><span><i className="v5-radar-live-dot" />免费源优先</span><span>未证实传闻最高 C 级</span><span>事件不等于买入信号</span></div>
    {error && <div className="px-4 py-2 text-[10px] text-warn">{error}</div>}
    {events.length ? <div className="divide-y divide-border">{events.slice(0, 6).map((event) => <button type="button" key={event.event_id} className="v5-radar-event" onClick={() => void open(event)}><span className={`v5-radar-level level-${String(event.alert_level || 'C').toLowerCase()}`}>{event.alert_level || 'C'}</span><span className="min-w-0 flex-1 text-left"><strong className="block truncate text-[11px] text-text">{event.canonical_title}</strong><span className="mt-1 block truncate text-[9px] text-text-secondary">{event.source || '公开源'} · 市场确认 {numberText(event.market_confirmation_score)} · {readableStatus(event.status)}</span></span><span className="shrink-0 font-mono text-[10px] text-accent">{numberText(event.event_score)}</span></button>)}</div> : <EmptyState text={loading ? '正在汇总公开事件源' : '当前没有可核验事件'} />}
    {selected && <div className="v5-radar-detail"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="text-xs font-medium text-text">{selected.canonical_title}</div><div className="mt-1 text-[10px] text-text-secondary">{selected.source || '--'} · {selected.event_type || '--'} · {readableStatus(selected.status)}</div></div><button type="button" className="v5-icon-button" onClick={() => { setSelected(null); setInterpretation(''); }} aria-label="关闭事件详情" title="关闭"><X size={13} /></button></div><div className="mt-3 flex flex-wrap gap-1.5">{(selected.topics || []).slice(0, 4).map((topic) => <span className="v5-tag" key={topic.name}>{topic.name}</span>)}{(selected.stocks || []).slice(0, 4).map((stock) => <span className="v5-tag" key={stock.code}>{stock.name || stock.code} · {stock.evidence_tag === 'FACT' ? '事实关联' : '推断关联'}</span>)}</div><p className="mt-3 text-[11px] leading-5 text-text-secondary">{selected.summary || '暂无摘要'}</p><button type="button" className="v5-text-button mt-2" onClick={() => void explain()} disabled={detailLoading}>{detailLoading ? <Loader2 size={12} className="animate-spin" /> : <BrainCircuit size={12} />}AI解读</button>{interpretation && <p className="v5-radar-interpretation">{interpretation}</p>}<div className="mt-3 text-[9px] leading-4 text-warn">关联股仅按已登记事实或行业字段标注；缺少主营证据时不会升级为核心受益。</div></div>}
  </section>;
}

function V51EvidencePanel() {
  const [data, setData] = useState<V51Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    try {
      const response = await apiFetch<{ code: number; data: V51Summary }>('/v51/dashboard', { cache: 'no-store', timeoutMs: 15000 });
      setData(response.data || null);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 120_000); return () => window.clearInterval(timer); }, [load]);
  const auction = data?.auction;
  const reward = data?.reward_punishment;
  const sectors = data?.leadership?.sectors || [];
  return <section className="v5-panel v5-v51-panel"><SectionHeader icon={Layers3} title="V5.1证据层" subtitle="竞价、预期差、供给测试与市场奖惩" />{loading && !data ? <EmptyState text="正在读取V5.1证据" /> : <><div className="v5-v51-metrics"><div><span>竞价覆盖</span><strong>{percent(auction?.coverage_pct)}</strong><small>{readableStatus(auction?.quality?.status)}</small></div><div><span>竞价序列</span><strong>{percent(auction?.timeline_coverage_pct)}</strong><small>{auction?.time_series_snapshots ?? '--'} 条时间点</small></div><div><span>奖惩状态</span><strong className={toneClass(reward?.state)}>{readableStatus(reward?.state)}</strong><small>宽度 {percent(reward?.breadth_pct)}</small></div></div><div className="border-t border-border px-4 py-3"><div className="text-[10px] text-text-secondary">板块领导力观察</div><div className="mt-2 flex flex-wrap gap-2">{sectors.slice(0, 5).map((item) => <span className="v5-tag" key={item.name}>{item.name} {numberText(item.leadership_score)}</span>)}{!sectors.length && <span className="text-[10px] text-text-secondary">暂无板块成分证据</span>}</div></div>{auction?.quality?.warning && <div className="border-t border-border px-4 py-2.5 text-[9px] leading-4 text-warn">{auction.quality.warning}</div>}</>}</section>;
}

function SectorResonance({ sectors, supplement }: { sectors: ForecastSector[]; supplement: MarketSupplement | null }) {
  const rows = (sectors || []).slice(0, 5);
  return (
    <section className="v5-panel min-w-0">
      <SectionHeader icon={Layers3} title="板块共振强度排名" subtitle="趋势、资金持续性与传播结构的合成观察" action={<Link href="/pro/rotation" className="v5-text-button">查看板块 <ArrowRight size={12} /></Link>} />
      <div className="hidden overflow-x-auto md:block"><table className="v5-table w-full"><thead><tr><th>排名</th><th>板块</th><th>共振</th><th>状态</th><th>资金/变化</th></tr></thead><tbody>{rows.map((item, index) => <tr key={`${item.name}-${index}`} title={item.reason || '结构观察'}><td className="font-mono text-text-secondary">{String(index + 1).padStart(2, '0')}</td><td><div className="font-medium text-text">{item.name}</div></td><td className="w-24"><div className="flex items-center gap-2"><span className={`w-8 font-mono ${toneClass(item.state)}`}>{numberText(item.flow_persistence_pct)}</span><ThinBar value={item.flow_persistence_pct} color={tone(item.state) === 'up' ? 'up' : tone(item.state) === 'down' ? 'down' : 'warn'} /></div></td><td><StatusPill value={item.state}>{item.state || '核验中'}</StatusPill></td><td className={`font-mono ${toneClass(item.latest_change_pct && item.latest_change_pct < 0 ? '下跌' : '上涨')}`}>{signedPercent(item.latest_change_pct)}</td></tr>)}</tbody></table></div>
      <div className="divide-y divide-border md:hidden">{rows.map((item, index) => <div key={`${item.name}-mobile-${index}`} className="p-4"><div className="flex items-center justify-between gap-3"><div><span className="mr-2 font-mono text-[10px] text-text-secondary">{String(index + 1).padStart(2, '0')}</span><span className="text-sm font-medium text-text">{item.name}</span></div><StatusPill value={item.state}>{item.state || '核验中'}</StatusPill></div><div className="mt-3 flex items-center gap-2"><ThinBar value={item.flow_persistence_pct} color="accent" /><span className="font-mono text-[10px] text-text-secondary">{percent(item.flow_persistence_pct)}</span></div><div className="mt-2 text-[10px] text-text-secondary">{signedPercent(item.latest_change_pct)} · {item.reason || '结构观察'}</div></div>)}</div>
      {!rows.length && <EmptyState text={supplement?.main_lines?.length ? 'V5 板块因子正在对齐' : '暂无可核验的板块共振数据'} />}
    </section>
  );
}

function AlphaRadar({ seeds }: { seeds: AlphaSeed[] }) {
  return (
    <section className="v5-panel min-w-0">
      <SectionHeader icon={ScanSearch} title="Alpha萌芽雷达" subtitle="A0-A6阶段，仅展示研究苗头，不直接下单" action={<Link href="/pro/stock-picker" className="v5-text-button">个股雷达 <ArrowRight size={12} /></Link>} />
      {seeds.length ? <div className="hidden overflow-x-auto md:block"><table className="v5-table w-full"><thead><tr><th>代码 / 名称</th><th>板块</th><th>阶段</th><th>确认度</th><th>行为状态</th><th></th></tr></thead><tbody>{seeds.slice(0, 5).map((item) => <tr key={item.code}><td><StockKlineButton code={item.code} name={item.name} className="text-text hover:text-accent"><span className="font-medium">{item.name}</span><span className="ml-2 font-mono text-[10px] text-text-secondary">{item.code}</span></StockKlineButton></td><td className="text-text-secondary">{item.sector}</td><td><StatusPill value={item.alpha_stage}>{item.alpha_stage}</StatusPill></td><td><div className="flex items-center gap-2"><span className="font-mono text-accent">{numberText(item.score)}</span><span className="text-[10px] text-text-secondary">/100</span></div></td><td className="max-w-[130px] truncate text-[10px] text-text-secondary">{item.behavior_state || '状态核验中'} · {item.crowding_state || '拥挤核验中'}</td><td><AddToPersonalPoolButton code={item.code} name={item.name} industry={item.sector} thesis={`V5 Alpha苗头：${item.alpha_stage}；${item.behavior_state}`} source="v5_alpha_radar" compact /></td></tr>)}</tbody></table></div> : <EmptyState text="暂无满足数据完整度门槛的 Alpha 苗头" />}
      <div className="border-t border-border px-4 py-2.5 text-[10px] text-text-secondary">Alpha阶段不是收益承诺；需等待市场、板块、资金和个股确认条件共同出现。</div>
    </section>
  );
}

function CapitalFlow({ forecast, supplement, overview }: { forecast: ForecastSnapshot; supplement: MarketSupplement | null; overview: MarketOverview | null }) {
  const inflow = overview?.fund_flow?.top_inflow || [];
  const outflow = overview?.fund_flow?.top_outflow || [];
  const overviewRows = [...inflow.map((item) => ({ name: item.name, amount: item.inflow, direction: 'up' as const })), ...outflow.map((item) => ({ name: item.name, amount: item.outflow, direction: 'down' as const }))];
  const hasObservedFlow = overviewRows.some((item) => finite(item.amount) && item.amount !== 0);
  const rows = hasObservedFlow
    ? overviewRows.slice(0, 8)
    : (supplement?.main_lines || []).slice(0, 8).map((item) => ({ name: item.name, amount: item.main_net_inflow, direction: finite(item.main_net_inflow) && item.main_net_inflow >= 0 ? 'up' as const : 'down' as const }));
  const maxAmount = Math.max(1, ...rows.map((item) => Math.abs(item.amount || 0)));
  return (
    <section id="capital" className="v5-panel">
      <SectionHeader icon={Wallet} title="资金动向" subtitle="主力资金净流入" action={<Link href="/pro/flow-observer" className="v5-text-button">更多 <ArrowRight size={11} /></Link>} />
      <div className="v5-capital-tabs"><span className="active">行业</span><span>净流入(亿)</span><span>5日趋势</span></div>
      {rows.length ? <div className="v5-capital-list">{rows.map((item, index) => <div className="v5-capital-row" key={`${item.name}-${index}`}><span className="v5-capital-name">{item.name}</span><span className={`v5-capital-bar ${item.direction}`}><i style={{ width: `${Math.max(8, Math.abs(item.amount || 0) / maxAmount * 100)}%` }} /></span><b className={item.direction === 'up' ? 'text-up' : 'text-down'}>{item.amount === null || item.amount === undefined ? '--' : `${item.amount >= 0 ? '+' : ''}${(item.amount / 1e8).toFixed(1)}`}</b><Sparkline values={[]} color={item.direction === 'up' ? '#EF5350' : '#26A69A'} label={`${item.name}趋势`} /></div>)}</div> : <EmptyState text="暂无可核验的资金因子" />}
    </section>
  );
}

function BehaviorPanel({ behavior }: { behavior: BehaviorSnapshot }) {
  const signals = behavior?.bias_signals || [];
  return (
    <section id="behavior" className="v5-panel">
      <SectionHeader icon={CircleDot} title="行为博弈与人性偏差" subtitle="内部分析层：识别人群行为，不推断不可验证的操控者意图" />
      <div className="grid grid-cols-2 border-b border-border"><div className="p-4"><div className="text-[10px] text-text-secondary">市场心理阶段</div><div className={`mt-2 text-sm font-semibold ${toneClass(behavior.market_psychology_state)}`}>{behavior.market_psychology_state || '核验中'}</div><div className="mt-1 text-[10px] text-text-secondary">迁移：{behavior.psychology_transition || '状态待核验'}</div></div><div className="border-l border-border p-4"><div className="text-[10px] text-text-secondary">行为失衡度</div><div className="mt-2 font-mono text-2xl text-text">{numberText(behavior.behavior_imbalance_score)}<span className="ml-1 text-xs text-text-secondary">/100</span></div><div className="mt-1 text-[10px] text-text-secondary">{behavior.behavior_imbalance_level || '核验中'}</div></div></div>
      <div className="grid grid-cols-2 divide-x divide-y divide-border">{[['追涨行为', behavior.fomo_state], ['恐慌踩踏', behavior.panic_state], ['高位一致性', behavior.crowding_state], ['假突破风险', behavior.false_breakout_risk]].map(([label, value]) => <div key={label} className="min-w-0 p-3"><div className="text-[10px] text-text-secondary">{label}</div><div className={`mt-1 break-words text-xs font-medium ${toneClass(String(value))}`}>{readableStatus(String(value || ''))}</div></div>)}</div>
      {signals.length > 0 && <div className="border-t border-border px-4 py-3"><div className="mb-2 text-[10px] text-text-secondary">主要偏差信号</div><div className="flex flex-wrap gap-2">{signals.slice(0, 4).map((item) => <StatusPill key={item.id} value={item.state}>{item.label} · {readableStatus(item.state)}</StatusPill>)}</div></div>}
    </section>
  );
}

function TurningPoints({ forecast }: { forecast: ForecastSnapshot }) {
  return (
    <section className="v5-panel v5-turning-panel"><SectionHeader icon={Target} title="关键转折条件监控" subtitle="条件满足后才提高对应路径概率" /><div className="grid gap-4 p-4 sm:grid-cols-2"><div><h3 className="flex items-center gap-2 text-xs font-semibold text-up"><ArrowUpRight size={14} />提高进攻概率需满足</h3><ul className="mt-3 space-y-2 text-[11px] leading-4 text-text-secondary">{(forecast.turning_points.increase_offensive_probability || []).slice(0, 4).map((item) => <li key={item} className="flex gap-2"><Check size={12} className="mt-0.5 shrink-0 text-up" />{item}</li>)}</ul></div><div><h3 className="flex items-center gap-2 text-xs font-semibold text-warn"><ArrowDownRight size={14} />提高防御概率需满足</h3><ul className="mt-3 space-y-2 text-[11px] leading-4 text-text-secondary">{(forecast.turning_points.increase_defensive_probability || []).slice(0, 4).map((item) => <li key={item} className="flex gap-2"><ShieldAlert size={12} className="mt-0.5 shrink-0 text-warn" />{item}</li>)}</ul></div></div><div className="border-t border-border px-4 py-2.5 text-[10px] leading-4 text-warn">失效边界：{(forecast.turning_points.falsify_current_path || []).slice(0, 2).join('；') || '等待模型生成可验证反证'}</div></section>
  );
}

function HistoricalAnalogs({ forecast, onLoad, loading }: { forecast: ForecastSnapshot; onLoad: () => void; loading: boolean }) {
  const analogs = forecast.historical_analogs || [];
  return (
    <section id="history" className="v5-panel"><SectionHeader icon={History} title="历史相似情景" subtitle="仅作结构参考，不代表未来必然重复" action={!analogs.length ? <button type="button" onClick={onLoad} disabled={loading} className="v5-text-button">{loading ? <Loader2 size={12} className="animate-spin" /> : <History size={12} />}加载回溯</button> : undefined} />{analogs.length ? <div className="divide-y divide-border">{analogs.slice(0, 3).map((item) => <details key={item.case_id} className="group px-4 py-3"><summary className="flex cursor-pointer list-none items-center gap-3 [&::-webkit-details-marker]:hidden"><span className="w-12 shrink-0 font-mono text-xs text-accent">{percent(item.similarity_pct)}</span><span className="min-w-0 flex-1"><b className="text-xs text-text">{item.period} · {item.label}</b><span className="ml-2 text-[10px] text-text-secondary">相似度</span></span><ChevronRight size={14} className="text-text-secondary transition-transform group-open:rotate-90" /></summary><div className="mt-3 grid gap-3 border-t border-border pt-3 text-[10px] leading-4 text-text-secondary sm:grid-cols-2"><div><span className="text-text-secondary">相似点：</span>{item.similar_factors.join('、') || '--'}</div><div><span className="text-text-secondary">差异点：</span>{item.different_factors.join('、') || '--'}</div><div className="sm:col-span-2 text-warn">边界：{item.do_not_copy_reason || '历史路径不作交易承诺'}</div></div></details>)}</div> : <EmptyState text="点击加载多维因子历史相似情景" />}<div className="border-t border-border px-4 py-2.5 text-[10px] text-text-secondary">历史案例采用点时数据规则，禁止用结果反推当时可见信息。</div></section>
  );
}

function StrategyAdvice({ forecast, supplement }: { forecast: ForecastSnapshot; supplement: MarketSupplement | null }) {
  const actionCenter = forecast.today_action_center || fallbackActionCenter(forecast, supplement);
  const permission = actionCenter.permission || {};
  const permissionCode = String(permission.code || 'UNKNOWN').toUpperCase();
  const nonExecutable = permissionCode === 'BLOCK' || permissionCode === 'NO_TRADE' || permissionCode === 'RESEARCH_ONLY';
  const position = nonExecutable ? 0 : permission.max_total_position_pct;
  const action = permission.label || forecast.risk_preference.label || '等待验证';
  const sectors = (forecast.sector_forecasts || []).filter((item) => tone(item.state) === 'up').slice(0, 3).map((item) => item.name);
  return <section className="v5-panel v5-advice-panel"><SectionHeader icon={SlidersHorizontal} title="今日策略建议" subtitle="服从今日行动中心的统一交易许可" /><div className="v5-advice-content"><div><span>仓位建议</span><strong className={toneClass(action)}>{finite(position) ? `控制在 ${position}% 内` : '等待数据确认'}</strong></div><div><span>风格建议</span><strong className={permissionCode === 'BLOCK' || permissionCode === 'NO_TRADE' ? 'text-down' : nonExecutable ? 'text-warn' : undefined}>{action}</strong></div><div><span>重点观察</span><div className="flex flex-wrap gap-1.5">{sectors.length ? sectors.map((item) => <span key={item} className="v5-tag">{item}</span>) : <span className="text-xs text-text-secondary">等待板块确认</span>}</div></div></div><div className="border-t border-border px-3 py-2 text-[9px] leading-4 text-warn">不输出“必涨、稳赚、强烈买入”；当前置信上限 {percent(forecast.data_health.confidence_ceiling_pct)}。</div></section>;
}

export default function MarketDecisionWorkbenchPage() {
  const [forecast, setForecast] = useState<ForecastSnapshot | null>(null);
  const [supplement, setSupplement] = useState<MarketSupplement | null>(null);
  const [overview, setOverview] = useState<MarketOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [progress, setProgress] = useState(8);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [judgmentAction, setJudgmentAction] = useState('WAIT');
  const [judgmentNote, setJudgmentNote] = useState('');
  const [judgmentBusy, setJudgmentBusy] = useState(false);
  const [skillFilters, setSkillFilters] = useState<SkillFilterState>({ exclude_star_market: true, exclude_gem: true });
  const [skillFilterBusy, setSkillFilterBusy] = useState(false);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const skillFiltersRef = useRef<SkillFilterState>({ exclude_star_market: true, exclude_gem: true });
  const skillsRequestRef = useRef(false);

  const loadSkills = useCallback(async (force = false, next = skillFiltersRef.current): Promise<boolean> => {
    if (skillsRequestRef.current) return false;
    skillsRequestRef.current = true;
    setSkillsLoading(true);
    try {
      const params = new URLSearchParams({
        exclude_star_market: String(next.exclude_star_market),
        exclude_gem: String(next.exclude_gem),
      });
      if (force) params.set('refresh', 'true');
      const response = await apiFetch<{ code: number; data: ForecastSnapshot['trading_skills'] }>(`/trading-skills/dashboard?${params.toString()}`, { cache: 'no-store', timeoutMs: 45000 });
      if (response.code !== 0 || !response.data) throw new Error('交易技能扫描返回无效数据');
      setForecast((current) => current ? { ...current, trading_skills: response.data } : current);
      return true;
    } catch (caught) {
      setNotice(friendlyApiError(caught, '交易技能异步扫描失败，主预测仍可使用'));
      return false;
    } finally {
      skillsRequestRef.current = false;
      setSkillsLoading(false);
    }
  }, []);

  const load = useCallback(async (force = false) => {
    if (force) setRefreshing(true); else setLoading(true);
    setError('');
    setNotice('');
    setProgress(8);
    const forecastParams = new URLSearchParams();
    if (force) forecastParams.set('refresh', 'true');
    forecastParams.set('exclude_star_market', String(skillFiltersRef.current.exclude_star_market));
    forecastParams.set('exclude_gem', String(skillFiltersRef.current.exclude_gem));
    forecastParams.set('include_skills', 'false');
    const forecastPath = `/forecast/dashboard?${forecastParams.toString()}`;
    const overviewPath = `/market/overview${force ? '?refresh=true' : ''}`;
    let mainForecastLoaded = false;
    try {
      const [forecastResult, overviewResult] = await Promise.allSettled([
        apiFetch<{ code: number; data: ForecastSnapshot }>(forecastPath, { cache: 'no-store', timeoutMs: 45000 }),
        apiFetch<{ code: number; data: MarketOverview }>(overviewPath, { cache: 'no-store', timeoutMs: 25000 }),
      ]);
      if (forecastResult.status === 'fulfilled' && forecastResult.value.code === 0 && forecastResult.value.data) {
        const forecastData = forecastResult.value.data;
        mainForecastLoaded = true;
        setForecast(forecastData);
        if (forecastData.workbench_summary) {
          setSupplement(forecastData.workbench_summary);
          window.localStorage.setItem(SUPPLEMENT_CACHE_KEY, JSON.stringify(forecastData.workbench_summary));
        } else {
          try { setSupplement(JSON.parse(window.localStorage.getItem(SUPPLEMENT_CACHE_KEY) || 'null') as MarketSupplement | null); } catch { setSupplement(null); }
        }
        window.localStorage.setItem(FORECAST_CACHE_KEY, JSON.stringify(forecastData));
      } else {
        throw forecastResult.status === 'rejected' ? forecastResult.reason : new Error('前瞻预测返回无效数据');
      }
      if (overviewResult.status === 'fulfilled' && overviewResult.value.code === 0 && overviewResult.value.data) {
        setOverview(overviewResult.value.data);
        window.localStorage.setItem(OVERVIEW_CACHE_KEY, JSON.stringify(overviewResult.value.data));
      } else {
        try { setOverview(JSON.parse(window.localStorage.getItem(OVERVIEW_CACHE_KEY) || 'null') as MarketOverview | null); } catch { setOverview(null); }
      }
      setProgress(100);
      if (force) setNotice('V5 预测与可用市场快照已重新核验');
    } catch (caught) {
      try {
        const cached = JSON.parse(window.localStorage.getItem(FORECAST_CACHE_KEY) || 'null') as ForecastSnapshot | null;
        const cachedSupplement = JSON.parse(window.localStorage.getItem(SUPPLEMENT_CACHE_KEY) || 'null') as MarketSupplement | null;
        const cachedOverview = JSON.parse(window.localStorage.getItem(OVERVIEW_CACHE_KEY) || 'null') as MarketOverview | null;
        if (cached) {
          setForecast(cached);
          setSupplement(cached.workbench_summary || cachedSupplement);
          setOverview(cachedOverview);
          mainForecastLoaded = true;
          setNotice('后端连接暂时中断，当前显示浏览器最近一次成功的 V5 快照。');
        } else throw caught;
      } catch (cacheError) {
        setError(friendlyApiError(cacheError, 'V5 预测中枢加载失败'));
      }
    } finally {
      if (mainForecastLoaded) void loadSkills(force, skillFiltersRef.current);
      setLoading(false);
      setRefreshing(false);
    }
  }, [loadSkills]);

  const refreshSkillScope = useCallback(async (next: SkillFilterState) => {
    if (skillFilterBusy || skillsRequestRef.current) return;
    skillFiltersRef.current = next;
    setSkillFilters(next);
    setSkillFilterBusy(true);
    setError('');
    try {
      const ok = await loadSkills(true, next);
      if (ok) setNotice(`已按当前可交易范围重新扫描：${next.exclude_star_market ? '排除科创板' : '包含科创板'}，${next.exclude_gem ? '排除创业板' : '包含创业板'}`);
    } catch (caught) {
      setNotice(friendlyApiError(caught, '板块筛选扫描失败，已保留上一份结果'));
    } finally {
      setSkillFilterBusy(false);
    }
  }, [loadSkills, skillFilterBusy]);

  useEffect(() => { void load(false); const timer = window.setInterval(() => void load(false), 60_000); return () => window.clearInterval(timer); }, [load]);
  useEffect(() => { if (!loading && !refreshing) return undefined; const timer = window.setInterval(() => setProgress((current) => Math.min(92, current + Math.max(1, Math.ceil((92 - current) / 8)))), 350); return () => window.clearInterval(timer); }, [loading, refreshing]);

  const loadHistory = useCallback(async () => {
    if (!forecast || forecast.historical_analogs?.length) return;
    setHistoryLoading(true);
    try {
      const response = await apiFetch<{ code: number; data: SimilarHistoryResponse }>('/history/similar', { cache: 'no-store', timeoutMs: 30000 });
      if (response.code !== 0) throw new Error('历史相似情景返回无效数据');
      setForecast((current) => current ? { ...current, historical_analogs: response.data.analogs || [] } : current);
    } catch (caught) { setNotice(friendlyApiError(caught, '历史相似情景加载失败')); } finally { setHistoryLoading(false); }
  }, [forecast]);

  const saveJudgment = useCallback(async () => {
    setJudgmentBusy(true);
    try {
      await apiFetch('/decisions/judgments', { method: 'POST', body: JSON.stringify({ user_action: judgmentAction, user_judgment: judgmentNote, user_evidence: judgmentNote ? [judgmentNote] : [], phase: forecast?.phase || 'current' }) });
      setNotice('个人判断已单独保存，盘后可用于实践验证。');
    } catch (caught) { setNotice(friendlyApiError(caught, '个人判断保存失败')); } finally { setJudgmentBusy(false); }
  }, [forecast, judgmentAction, judgmentNote]);

  const navItems = useMemo(() => [
    ['总览驾驶舱', '#top', Gauge], ['市场全景', '#market', Activity], ['资金流向', '#capital', Wallet], ['因子监控', '#factors', LineChart], ['板块分析', '#sectors', Layers3], ['个股雷达', '#alpha', ScanSearch], ['事件驱动', '#events', Bell], ['行为博弈', '#behavior', CircleDot], ['多周期预测', '#forecast', BrainCircuit], ['策略信号', '#strategy', Target], ['复盘回溯', '#history', History], ['系统日志', '#system-status', Database],
  ] as const, []);

  if (loading && !forecast) return <LoadingScreen progress={progress} />;
  if (error && !forecast) return <main className="v5-page v5-error"><AlertCircle size={32} className="text-[#F85149]" /><h1 className="mt-4 text-base font-semibold text-text">V5 预测中枢加载失败</h1><p className="mt-2 max-w-md text-sm text-text-secondary">{error}</p><button type="button" onClick={() => void load(false)} className="v5-button mt-5"><RefreshCw size={13} />重新加载</button></main>;
  if (!forecast) return null;

  const health = forecast.data_health;
  const allFactors = forecast.factors?.all || [];
  const observedFactorCount = allFactors.filter((item) => item.observed).length;
  const factors = allFactors.filter((item) => item.observed).slice(0, 8);
  const confidence = health.confidence_ceiling_pct ?? health.completeness_pct ?? 0;
  return (
    <main id="top" className="v5-page">
      <header className="v5-global-header">
        <Link href="#top" className="v5-global-brand" aria-label="AI多因子共振预测中枢总览">
          <span className="v5-aperture-mark"><Aperture size={25} strokeWidth={1.25} /></span>
          <span className="min-w-0"><strong>AI多因子共振预测中枢 <b>V5.1</b></strong><small>进因势位时止 · 洞察先机 · 驭势而行</small></span>
        </Link>
            <nav className="v5-global-nav max-[820px]:!hidden" aria-label="预测工作台功能导航">
              <a className="active" href="#forecast">预测引擎</a><a href="#factors">因子监控</a><a href="#sectors">板块轮动</a><a href="#alpha">Alpha雷达</a><a href="#history">历史回溯</a><a href="#strategy">策略执行</a><a href="#system-status">系统状态</a>
              <span className="v5-nav-divider" aria-hidden="true" />
              <Link className="v5-global-utility" href="/market/v4">V4工作台</Link><Link className="v5-global-utility" href="/pro/research">研究中心</Link><Link className="v5-global-utility" href="/pro/event-radar">事件雷达</Link><Link className="v5-global-utility" href="/pro/auction">竞价监控</Link><Link className="v5-global-utility" href="/numcat">数据中枢</Link><Link className="v5-global-utility" href="/quant">量化策略</Link>
            </nav>
        <div className="v5-global-meta"><span className="v5-header-time">{localTime(forecast.generated_at)}</span><span className={`v5-trading-state ${supplement?.meta?.is_realtime ? 'live' : ''}`}><i />{supplement?.meta?.is_realtime ? '交易中' : '缓存快照'}</span><button type="button" className="v5-header-icon max-[820px]:!inline-grid min-[821px]:!hidden" title="打开功能导航" aria-label="打开功能导航" onClick={() => setMobileNavOpen((open) => !open)}><Menu size={15} /></button><button type="button" className="v5-header-icon" title="刷新工作台" aria-label="刷新工作台" onClick={() => void load(true)} disabled={refreshing}>{refreshing ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}</button><UserCircle size={22} className="v5-user-mark" aria-hidden="true" /></div>
        {mobileNavOpen && <div className="absolute left-2 right-2 top-[56px] z-50 max-h-[calc(100dvh-64px)] overflow-y-auto rounded-md border border-border bg-[#101821] p-2 shadow-xl max-[820px]:block min-[821px]:hidden"><div className="grid gap-1 sm:grid-cols-2">{navItems.map(([label, href, Icon]) => <a key={href} href={href} onClick={() => setMobileNavOpen(false)} className="flex min-w-0 items-center gap-2 rounded px-3 py-2 text-[11px] text-text-secondary hover:bg-[#172535] hover:text-text"><Icon size={13} className="shrink-0 text-accent" /><span className="truncate">{label}</span></a>)}<span className="my-1 border-t border-border sm:col-span-2" />{[['V4工作台', '/market/v4'], ['研究中心', '/pro/research'], ['事件雷达', '/pro/event-radar'], ['竞价监控', '/pro/auction'], ['数据中枢', '/numcat'], ['量化策略', '/quant']].map(([label, href]) => <Link key={href} href={href} onClick={() => setMobileNavOpen(false)} className="rounded px-3 py-2 text-[11px] text-text-secondary hover:bg-[#172535] hover:text-text">{label}</Link>)}</div></div>}
        {refreshing && <div className="v5-refresh-progress" role="progressbar" aria-label="刷新工作台进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><span style={{ width: `${progress}%` }} /></div>}
      </header>
      <div className="v5-layout">
        <aside className="v5-sidebar">
          <nav className="v5-side-nav" aria-label="V5工作台导航">{navItems.map(([label, href, Icon]) => <a key={href} href={href} className={`v5-side-link ${href === '#top' ? 'active' : ''}`}><Icon size={14} /><span>{label}</span></a>)}</nav>
          <div className="v5-side-risk"><div className="v5-side-risk-title">风险偏好状态</div><div className={`v5-side-risk-label ${toneClass(forecast.risk_preference.label)}`} title={forecast.risk_preference.label || '核验中'}>{forecast.risk_preference.label || '核验中'}</div><div className="v5-side-risk-sub">{stateLabel(forecast.risk_preference.state)}</div><div className="v5-side-gauge"><ArcGauge value={confidence} color={tone(forecast.risk_preference.label) === 'down' ? '#26A69A' : tone(forecast.risk_preference.label) === 'warn' ? '#D9A441' : '#EF5350'} label="置信上限" /></div><div className="v5-side-updated">更新时间 <b>{localTime(forecast.generated_at).split(' ')[1] || '--'}</b></div><Link href="/market/v4" className="v5-v4-link">打开 V4 独立工作台 <ArrowRight size={11} /></Link></div>
        </aside>

        <div className="v5-main">
          {(notice || error) && <div className={`v5-notice ${error ? 'error' : ''}`}><span>{error || notice}</span><button type="button" onClick={() => { setNotice(''); setError(''); }} aria-label="关闭提示"><X size={13} /></button></div>}

          <div className="v5-content">
            <TodayActionCenter forecast={forecast} supplement={supplement} />
            <div className="v5-dashboard-grid"><div className="min-w-0"><ForecastTimeline forecast={forecast} onRefresh={() => void load(true)} skillFilters={skillFilters} onSkillFilterChange={(next) => void refreshSkillScope(next)} skillFilterBusy={skillFilterBusy} skillsLoading={skillsLoading} /></div><MarketStatePanel supplement={supplement} overview={overview} /><div id="events" className="min-w-0"><EventMonitor forecast={forecast} supplement={supplement} /></div></div>

            <div className="v5-research-grid"><div id="sectors"><SectorResonance sectors={forecast.sector_forecasts || []} supplement={supplement} /></div><div id="alpha"><AlphaRadar seeds={forecast.alpha_seeds || []} /></div><div><CapitalFlow forecast={forecast} supplement={supplement} overview={overview} /></div><BehaviorPanel behavior={forecast.behavior} /></div>

            <div id="radar" className="v5-evidence-grid"><V51EvidencePanel /><RadarEventPanel /></div>

            <div className="v5-bottom-grid"><div><TurningPoints forecast={forecast} /></div><HistoricalAnalogs forecast={forecast} onLoad={() => void loadHistory()} loading={historyLoading} /><section className="v5-panel"><SectionHeader icon={Gauge} title="数据与模型资格" subtitle="真值、新鲜度与校准共同决定是否可执行" /><div className="p-4"><div className="flex items-end justify-between gap-3"><div><span className="block text-[10px] text-text-secondary">数据可信上限</span><span className="text-3xl font-semibold text-text">{percent(confidence)}</span></div><StatusPill value={health.level}>{health.level || '核验中'}</StatusPill></div><ThinBar value={confidence} color={health.execution_allowed ? 'up' : 'warn'} /><div className="mt-4 space-y-2 text-[10px] leading-4 text-text-secondary"><div><span className="text-text-secondary">真值：</span>{health.truth_status_label || '状态不可用'} · <span className="text-text-secondary">模型：</span>{health.calibration_qualified ? '概率校准通过' : '样本外校准未通过，只输出研究倾向'}</div>{(health.block_reasons || []).slice(0, 3).map((item) => <div key={item} className="flex gap-2"><span className="text-warn">·</span>{item}</div>)}{!(health.block_reasons || []).length && <div>当前没有新增数据边界提示。</div>}</div></div></section><div id="strategy"><StrategyAdvice forecast={forecast} supplement={supplement} /></div></div>

            <div id="factors" className="v5-panel"><SectionHeader icon={LineChart} title="因子监控与共振变化" subtitle="领先因子 → 传播因子 → 确认因子，按新鲜度和可靠度进入预测" action={<span className="shrink-0 text-[10px] text-text-secondary">观测 {observedFactorCount} / {allFactors.length} <span className="ml-1 text-text-muted">仅展示前8项</span></span>} /><div className="v5-factor-grid">{factors.map((item) => <div key={item.id} className="v5-factor"><div className="flex items-start justify-between gap-2"><span className="line-clamp-2 text-xs text-text">{item.name}</span><span className={`shrink-0 text-[10px] ${item.observed ? 'text-up' : 'text-warn'}`}>{item.observed ? '已观测' : '核验中'}</span></div><div className="mt-3 flex items-end justify-between"><span className="font-mono text-lg text-text">{numberText(item.value)}</span><span className={`font-mono text-[10px] ${toneClass(item.delta)}`}>{signedPercent(item.delta)}</span></div><ThinBar value={item.value} color={tone(item.state) === 'down' ? 'down' : tone(item.state) === 'up' ? 'up' : 'accent'} /><div className="mt-2 truncate text-[10px] text-text-secondary">{item.layer} · {item.source}</div></div>)}{!factors.length && <EmptyState text="暂无满足新鲜度门槛的因子" />}</div></div>

            <section className="v5-panel"><SectionHeader icon={Wallet} title="AI与用户双轨判断" subtitle="保存你的判断，盘后与实际市场状态对照" /><div className="grid gap-3 p-4 sm:grid-cols-[160px_minmax(0,1fr)_auto] sm:items-end"><label className="text-[10px] text-text-secondary">我的判断<select value={judgmentAction} onChange={(event) => setJudgmentAction(event.target.value)} className="v5-select mt-1"><option value="BULLISH">偏多</option><option value="NEUTRAL">中性</option><option value="BEARISH">偏空</option><option value="WAIT">等待</option><option value="NO_TRADE">不交易</option></select></label><label className="text-[10px] text-text-secondary">依据与反证<textarea value={judgmentNote} onChange={(event) => setJudgmentNote(event.target.value)} rows={2} placeholder="记录与你的判断有关的事实、反证或观察条件" className="v5-textarea mt-1" /></label><button type="button" onClick={() => void saveJudgment()} disabled={judgmentBusy} className="v5-button">{judgmentBusy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}保存判断</button></div></section>

            {health.missing_factors.length > 0 && <section className="v5-data-boundary"><div className="flex items-center gap-2 font-medium text-warn"><Database size={13} />数据边界与补采队列</div><div className="mt-2 text-[10px] leading-4 text-text-secondary">{health.missing_factors.slice(0, 5).map((item) => `${item.name}：${item.source}`).join(' · ')}。缺口不会填充默认值，预测置信度已按规则封顶。</div></section>}
            <footer id="system-status" className="v5-footer"><span>V5.1 · {forecast.version} · {forecast.data_health.high_confidence_allowed ? '高置信度通道可用' : `${forecast.data_health.truth_status_label || '证据受限'} · 研究模式`}</span><span>实时行情仅在交易时段更新；未校准模型不显示成功概率</span><div className="flex gap-3"><Link href="/pro/research">研究中心</Link><Link href="/pro/personal">个人股票池</Link><Link href="/quant">量化策略</Link></div></footer>
          </div>
        </div>
      </div>
    </main>
  );
}
