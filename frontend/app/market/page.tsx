'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertCircle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Bell,
  BrainCircuit,
  Check,
  ChevronRight,
  CircleDot,
  Database,
  Gauge,
  History,
  Layers3,
  LineChart,
  Loader2,
  RefreshCw,
  ScanSearch,
  ShieldAlert,
  SlidersHorizontal,
  Target,
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

interface SimilarHistoryResponse {
  version?: string;
  current_forecast_date?: string;
  analogs?: ForecastSnapshot['historical_analogs'];
  method?: string;
}

const FORECAST_CACHE_KEY = 'v5_forecast_dashboard_cache';
const SUPPLEMENT_CACHE_KEY = 'v5_market_supplement_cache';

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
  if (/防御|承压|退潮|恐慌|流出|弱化|下降|风险|负反馈|空/.test(text)) return 'down';
  if (/增强|修复|强化|走强|向上|流入|改善|低|进攻|多/.test(text)) return 'up';
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
  const barColors = { accent: '#4C8DFF', up: '#3FB950', down: '#F85149', warn: '#D9A441' };
  return <div className="v5-thin-bar"><span style={{ width: `${clamp(value)}%`, backgroundColor: barColors[color] }} /></div>;
}

function StatusPill({ children, value }: { children: React.ReactNode; value?: string | null }) {
  return <span className={`v5-pill v5-pill-${tone(value || String(children))}`}>{children}</span>;
}

function EmptyState({ text = '暂无可核验数据' }: { text?: string }) {
  return <div className="py-8 text-center text-xs text-text-secondary">{text}</div>;
}

function ForecastTimeline({ forecast }: { forecast: ForecastSnapshot }) {
  const timeline = (forecast.timeline || []).slice(0, 4);
  return (
    <section id="forecast" className="v5-panel v5-forecast-panel">
      <SectionHeader icon={BrainCircuit} title="AI市场前瞻预测引擎" subtitle="基于多因子共振与因果链推演，概率由模型计算，AI只负责解释与反证" action={<div className="flex items-center gap-2"><span className="hidden text-[10px] text-text-secondary sm:inline">更新 {localTime(forecast.generated_at)}</span><button className="v5-icon-button" type="button" title="刷新预测" aria-label="刷新预测" data-refresh-forecast><RefreshCw size={13} /></button></div>} />
      <div className="v5-timeline">
        {timeline.map((item) => (
          <article key={item.id} className="v5-horizon">
            <div className="flex items-center justify-between gap-2"><span className="text-[10px] text-text-secondary">{item.label}</span><span className={`font-mono text-[10px] ${toneClass(item.state)}`}>{percent(item.confidence_pct)} 置信</span></div>
            <div className={`mt-3 min-h-[42px] text-[15px] font-semibold ${toneClass(item.state)}`}>{item.state || '核验中'}</div>
            <div className="mt-3 grid grid-cols-3 gap-1 border-t border-border pt-2 text-center text-[10px]"><div><div className="text-text-secondary">向上</div><div className="mt-1 font-mono text-up">{percent(item.probabilities.upside)}</div></div><div className="border-x border-border"><div className="text-text-secondary">主路径</div><div className="mt-1 font-mono text-accent">{percent(item.probabilities.main)}</div></div><div><div className="text-text-secondary">向下</div><div className="mt-1 font-mono text-down">{percent(item.probabilities.downside)}</div></div></div>
            <div className="mt-3 text-[10px] leading-4 text-text-secondary">{(item.key_factors || []).slice(0, 2).join(' · ') || '因子仍在核验'}</div>
          </article>
        ))}
        <article className="v5-horizon v5-current-horizon"><div className="text-[10px] text-text-secondary">当前判断</div><div className={`mt-3 min-h-[42px] text-[15px] font-semibold ${toneClass(forecast.risk_preference.label)}`}>{forecast.risk_preference.label || '震荡分化'}</div><div className="mt-3 border-t border-border pt-2 text-[10px] text-text-secondary">市场阶段 · {stateLabel(forecast.phase)}</div><div className="mt-3 text-[10px] leading-4 text-text-secondary">{forecast.risk_preference.evidence.slice(0, 2).join(' · ') || '等待下一次数据验证'}</div></article>
      </div>
      <div className="v5-resonance-strip">
        <div className="v5-resonance-item"><div className="flex justify-between text-[10px]"><span className="text-text-secondary">风险偏好收缩形成度</span><b className="font-mono text-down">{percent(forecast.resonance.defensive_resonance_pct)}</b></div><ThinBar value={forecast.resonance.defensive_resonance_pct} color="down" /></div>
        <div className="v5-resonance-item"><div className="flex justify-between text-[10px]"><span className="text-text-secondary">进攻共振形成度</span><b className="font-mono text-up">{percent(forecast.resonance.offensive_resonance_pct)}</b></div><ThinBar value={forecast.resonance.offensive_resonance_pct} color="up" /></div>
        <div className="v5-resonance-summary"><span className="text-text-secondary">多周期共振</span><strong className="text-text">{forecast.resonance.risk_preference_label || '状态核验中'}</strong></div>
      </div>
    </section>
  );
}

function MarketStatePanel({ supplement }: { supplement: MarketSupplement | null }) {
  const metrics = supplement?.headline_metrics;
  const marketState = supplement?.market_state;
  const breadth = finite(metrics?.up_count) && finite(metrics?.down_count) ? metrics.up_count / Math.max(1, metrics.up_count + metrics.down_count) * 100 : null;
  return (
    <section className="v5-panel">
      <SectionHeader icon={Activity} title="当前市场状态" subtitle={supplement?.meta?.is_realtime ? '交易时段 · 实时快照' : `最近完整交易日 ${supplement?.meta?.decision_date || '--'}`} />
      <div className="v5-market-state">
        <div className="v5-state-score"><span className={`font-mono text-4xl font-semibold ${toneClass(marketState?.state_label)}`}>{numberText(marketState?.score)}</span><div className="mt-2 flex items-center gap-2"><StatusPill value={marketState?.state_label}>{marketState?.state_label || '核验中'}</StatusPill><span className="text-[10px] text-text-secondary">{marketState?.execution_level || '等待许可'}</span></div></div>
        <div className="v5-state-metrics"><div><span>市场温度</span><b>{numberText(metrics?.sentiment_temperature)}°</b></div><div><span>成交额</span><b>{compactAmount(metrics?.market_amount)}</b></div><div><span>涨 / 跌</span><b>{numberText(metrics?.up_count)} / {numberText(metrics?.down_count)}</b></div><div><span>涨停 / 跌停</span><b><i className="text-up">{numberText(metrics?.limit_up)}</i> / <i className="text-down">{numberText(metrics?.limit_down)}</i></b></div></div>
      </div>
      <div className="border-t border-border px-4 py-3"><div className="mb-1 flex justify-between text-[10px]"><span className="text-text-secondary">市场宽度</span><span className="font-mono text-text">{percent(breadth)}</span></div><ThinBar value={breadth} color={tone(marketState?.state_label) === 'down' ? 'down' : tone(marketState?.state_label) === 'up' ? 'up' : 'warn'} /></div>
      <div className="grid grid-cols-2 border-t border-border"><div className="p-3"><div className="text-[10px] text-text-secondary">第一主线</div><div className="mt-1 truncate text-xs font-medium text-accent">{metrics?.main_line || '--'}</div></div><div className="border-l border-border p-3"><div className="text-[10px] text-text-secondary">炸板率</div><div className={`mt-1 font-mono text-xs ${toneClass(metrics?.failed_limit_rate && metrics.failed_limit_rate > 25 ? '风险' : '稳定')}`}>{percent(metrics?.failed_limit_rate, 1)}</div></div></div>
    </section>
  );
}

function EventMonitor({ forecast, supplement }: { forecast: ForecastSnapshot; supplement: MarketSupplement | null }) {
  const events = [...(forecast.risk_preference.evidence || []), ...(forecast.turning_points.increase_defensive_probability || [])].slice(0, 5);
  const sourceCount = supplement?.audit?.data_sources?.length || forecast.data_health.sources?.length || 0;
  return (
    <section className="v5-panel">
      <SectionHeader icon={Bell} title="重要事件监控" subtitle={`${sourceCount || '--'} 个已登记数据源`} />
      {events.length ? <div className="divide-y divide-border">{events.map((event, index) => <div key={`${event}-${index}`} className="v5-event-row"><span className="v5-event-tag">{index % 2 === 0 ? '市场' : '风险'}</span><span className="min-w-0 flex-1 truncate text-xs text-text">{event}</span><span className="shrink-0 text-[10px] text-text-secondary">{index === 0 ? '当前' : '监控'}</span></div>)}</div> : <EmptyState text="当前没有新增事件摘要" />}
      <div className="border-t border-border px-4 py-2.5 text-[10px] leading-4 text-text-secondary">事件只作为因果链证据，不能单独生成买卖结论。</div>
    </section>
  );
}

function SectorResonance({ sectors, supplement }: { sectors: ForecastSector[]; supplement: MarketSupplement | null }) {
  const rows = (sectors || []).slice(0, 6);
  return (
    <section id="sectors" className="v5-panel min-w-0">
      <SectionHeader icon={Layers3} title="板块共振强度排名" subtitle="趋势、资金持续性与传播结构的合成观察" action={<Link href="/pro/rotation" className="v5-text-button">查看板块 <ArrowRight size={12} /></Link>} />
      <div className="hidden overflow-x-auto md:block"><table className="v5-table w-full"><thead><tr><th>排名</th><th>板块</th><th>共振</th><th>状态</th><th>资金/变化</th></tr></thead><tbody>{rows.map((item, index) => <tr key={`${item.name}-${index}`}><td className="font-mono text-text-secondary">{String(index + 1).padStart(2, '0')}</td><td><div className="font-medium text-text">{item.name}</div><div className="mt-1 max-w-[250px] truncate text-[10px] text-text-secondary">{item.reason || '结构观察'}</div></td><td className="w-32"><div className="flex items-center gap-2"><span className={`w-8 font-mono ${toneClass(item.state)}`}>{numberText(item.flow_persistence_pct)}</span><ThinBar value={item.flow_persistence_pct} color={tone(item.state) === 'up' ? 'up' : tone(item.state) === 'down' ? 'down' : 'warn'} /></div></td><td><StatusPill value={item.state}>{item.state || '核验中'}</StatusPill></td><td className={`font-mono ${toneClass(item.latest_change_pct && item.latest_change_pct < 0 ? '下跌' : '上涨')}`}>{signedPercent(item.latest_change_pct)}<div className="mt-1 text-[10px] text-text-secondary">持续 {percent(item.flow_persistence_pct)}</div></td></tr>)}</tbody></table></div>
      <div className="divide-y divide-border md:hidden">{rows.map((item, index) => <div key={`${item.name}-mobile-${index}`} className="p-4"><div className="flex items-center justify-between gap-3"><div><span className="mr-2 font-mono text-[10px] text-text-secondary">{String(index + 1).padStart(2, '0')}</span><span className="text-sm font-medium text-text">{item.name}</span></div><StatusPill value={item.state}>{item.state || '核验中'}</StatusPill></div><div className="mt-3 flex items-center gap-2"><ThinBar value={item.flow_persistence_pct} color="accent" /><span className="font-mono text-[10px] text-text-secondary">{percent(item.flow_persistence_pct)}</span></div><div className="mt-2 text-[10px] text-text-secondary">{signedPercent(item.latest_change_pct)} · {item.reason || '结构观察'}</div></div>)}</div>
      {!rows.length && <EmptyState text={supplement?.main_lines?.length ? 'V5 板块因子正在对齐' : '暂无可核验的板块共振数据'} />}
    </section>
  );
}

function AlphaRadar({ seeds }: { seeds: AlphaSeed[] }) {
  return (
    <section id="alpha" className="v5-panel min-w-0">
      <SectionHeader icon={ScanSearch} title="Alpha萌芽雷达" subtitle="A0-A6阶段，仅展示研究苗头，不直接下单" action={<Link href="/pro/stock-picker" className="v5-text-button">个股雷达 <ArrowRight size={12} /></Link>} />
      {seeds.length ? <div className="hidden overflow-x-auto md:block"><table className="v5-table w-full"><thead><tr><th>代码 / 名称</th><th>板块</th><th>阶段</th><th>确认度</th><th>行为状态</th><th></th></tr></thead><tbody>{seeds.slice(0, 6).map((item) => <tr key={item.code}><td><StockKlineButton code={item.code} name={item.name} className="text-text hover:text-accent"><span className="font-medium">{item.name}</span><span className="ml-2 font-mono text-[10px] text-text-secondary">{item.code}</span></StockKlineButton></td><td className="text-text-secondary">{item.sector}</td><td><StatusPill value={item.alpha_stage}>{item.alpha_stage}</StatusPill></td><td><div className="flex items-center gap-2"><span className="font-mono text-accent">{numberText(item.score)}</span><span className="text-[10px] text-text-secondary">/100</span></div></td><td className="max-w-[130px] truncate text-[10px] text-text-secondary">{item.behavior_state || '状态核验中'} · {item.crowding_state || '拥挤核验中'}</td><td><AddToPersonalPoolButton code={item.code} name={item.name} industry={item.sector} thesis={`V5 Alpha苗头：${item.alpha_stage}；${item.behavior_state}`} source="v5_alpha_radar" compact /></td></tr>)}</tbody></table></div> : <EmptyState text="暂无满足数据完整度门槛的 Alpha 苗头" />}
      <div className="border-t border-border px-4 py-2.5 text-[10px] text-text-secondary">Alpha阶段不是收益承诺；需等待市场、板块、资金和个股确认条件共同出现。</div>
    </section>
  );
}

function CapitalFlow({ forecast, supplement }: { forecast: ForecastSnapshot; supplement: MarketSupplement | null }) {
  const factors = (forecast.factors?.propagation || []).filter((item) => item.observed).slice(0, 5);
  return (
    <section id="capital" className="v5-panel">
      <SectionHeader icon={Wallet} title="资金动向" subtitle="价格、成交与资金行为的联合观察" action={<div className="v5-tab-strip"><span className="active">主力</span><span>ETF</span><span>融资</span></div>} />
      {factors.length ? <div className="space-y-4 p-4">{factors.map((item) => <div key={item.id}><div className="flex items-center justify-between gap-3 text-xs"><span className="min-w-0 truncate text-text">{item.name}</span><span className={`shrink-0 font-mono ${toneClass(item.state)}`}>{numberText(item.value)}</span></div><div className="mt-1 flex items-center gap-2"><ThinBar value={item.value} color={tone(item.state) === 'down' ? 'down' : tone(item.state) === 'up' ? 'up' : 'accent'} /><span className={`w-12 text-right font-mono text-[10px] ${toneClass(item.delta)}`}>{signedPercent(item.delta)}</span></div><div className="mt-1 truncate text-[10px] text-text-secondary">来源 {item.source}</div></div>)}</div> : <EmptyState text={supplement?.headline_metrics ? '资金因子正在刷新' : '暂无可核验的资金因子'} />}
    </section>
  );
}

function BehaviorPanel({ behavior }: { behavior: BehaviorSnapshot }) {
  const signals = behavior?.bias_signals || [];
  return (
    <section id="behavior" className="v5-panel">
      <SectionHeader icon={CircleDot} title="行为博弈与人性偏差" subtitle="内部分析层：识别人群行为，不推断不可验证的操控者意图" />
      <div className="grid grid-cols-2 border-b border-border"><div className="p-4"><div className="text-[10px] text-text-secondary">市场心理阶段</div><div className={`mt-2 text-sm font-semibold ${toneClass(behavior.market_psychology_state)}`}>{behavior.market_psychology_state || '核验中'}</div><div className="mt-1 text-[10px] text-text-secondary">迁移：{behavior.psychology_transition || '状态待核验'}</div></div><div className="border-l border-border p-4"><div className="text-[10px] text-text-secondary">行为失衡度</div><div className="mt-2 font-mono text-2xl text-text">{numberText(behavior.behavior_imbalance_score)}<span className="ml-1 text-xs text-text-secondary">/100</span></div><div className="mt-1 text-[10px] text-text-secondary">{behavior.behavior_imbalance_level || '核验中'}</div></div></div>
      <div className="grid grid-cols-2 divide-x divide-y divide-border">{[['追涨行为', behavior.fomo_state], ['恐慌踩踏', behavior.panic_state], ['高位一致性', behavior.crowding_state], ['假突破风险', behavior.false_breakout_risk]].map(([label, value]) => <div key={label} className="p-3"><div className="text-[10px] text-text-secondary">{label}</div><div className={`mt-1 text-xs font-medium ${toneClass(String(value))}`}>{value || '核验中'}</div></div>)}</div>
      {signals.length > 0 && <div className="border-t border-border px-4 py-3"><div className="mb-2 text-[10px] text-text-secondary">主要偏差信号</div><div className="flex flex-wrap gap-2">{signals.slice(0, 4).map((item) => <StatusPill key={item.id} value={item.state}>{item.label} · {item.state}</StatusPill>)}</div></div>}
    </section>
  );
}

function TurningPoints({ forecast }: { forecast: ForecastSnapshot }) {
  return (
    <section className="v5-panel"><SectionHeader icon={Target} title="关键转折条件监控" subtitle="条件满足后才提高对应路径概率" /><div className="grid gap-4 p-4 sm:grid-cols-2"><div><h3 className="flex items-center gap-2 text-xs font-semibold text-up"><ArrowUpRight size={14} />提高进攻概率需满足</h3><ul className="mt-3 space-y-2 text-[11px] leading-4 text-text-secondary">{(forecast.turning_points.increase_offensive_probability || []).slice(0, 4).map((item) => <li key={item} className="flex gap-2"><Check size={12} className="mt-0.5 shrink-0 text-up" />{item}</li>)}</ul></div><div><h3 className="flex items-center gap-2 text-xs font-semibold text-down"><ArrowDownRight size={14} />提高防御概率需满足</h3><ul className="mt-3 space-y-2 text-[11px] leading-4 text-text-secondary">{(forecast.turning_points.increase_defensive_probability || []).slice(0, 4).map((item) => <li key={item} className="flex gap-2"><ShieldAlert size={12} className="mt-0.5 shrink-0 text-down" />{item}</li>)}</ul></div></div><div className="border-t border-border px-4 py-2.5 text-[10px] leading-4 text-warn">失效边界：{(forecast.turning_points.falsify_current_path || []).slice(0, 2).join('；') || '等待模型生成可验证反证'}</div></section>
  );
}

function HistoricalAnalogs({ forecast, onLoad, loading }: { forecast: ForecastSnapshot; onLoad: () => void; loading: boolean }) {
  const analogs = forecast.historical_analogs || [];
  return (
    <section id="history" className="v5-panel"><SectionHeader icon={History} title="历史相似情景" subtitle="仅作结构参考，不代表未来必然重复" action={!analogs.length ? <button type="button" onClick={onLoad} disabled={loading} className="v5-text-button">{loading ? <Loader2 size={12} className="animate-spin" /> : <History size={12} />}加载回溯</button> : undefined} />{analogs.length ? <div className="divide-y divide-border">{analogs.slice(0, 3).map((item) => <details key={item.case_id} className="group px-4 py-3"><summary className="flex cursor-pointer list-none items-center gap-3 [&::-webkit-details-marker]:hidden"><span className="w-12 shrink-0 font-mono text-xs text-accent">{percent(item.similarity_pct)}</span><span className="min-w-0 flex-1"><b className="text-xs text-text">{item.period} · {item.label}</b><span className="ml-2 text-[10px] text-text-secondary">相似度</span></span><ChevronRight size={14} className="text-text-secondary transition-transform group-open:rotate-90" /></summary><div className="mt-3 grid gap-3 border-t border-border pt-3 text-[10px] leading-4 text-text-secondary sm:grid-cols-2"><div><span className="text-text-secondary">相似点：</span>{item.similar_factors.join('、') || '--'}</div><div><span className="text-text-secondary">差异点：</span>{item.different_factors.join('、') || '--'}</div><div className="sm:col-span-2 text-warn">边界：{item.do_not_copy_reason || '历史路径不作交易承诺'}</div></div></details>)}</div> : <EmptyState text="点击加载多维因子历史相似情景" />}<div className="border-t border-border px-4 py-2.5 text-[10px] text-text-secondary">历史案例采用点时数据规则，禁止用结果反推当时可见信息。</div></section>
  );
}

function StrategyAdvice({ forecast, supplement }: { forecast: ForecastSnapshot; supplement: MarketSupplement | null }) {
  const permission = supplement?.decision_2026?.trading_permission;
  const position = permission?.max_total_position_pct ?? supplement?.strategy_selector?.max_total_position_pct;
  const action = permission?.label || forecast.risk_preference.label || '等待验证';
  const sectors = (forecast.sector_forecasts || []).filter((item) => tone(item.state) === 'up').slice(0, 3).map((item) => item.name);
  return <section className="v5-panel v5-advice-panel"><SectionHeader icon={SlidersHorizontal} title="今日策略建议" subtitle="14:55执行参考 · 最终决策由用户掌握" /><div className="grid gap-4 p-4 sm:grid-cols-3"><div><div className="text-[10px] text-text-secondary">仓位建议</div><div className={`mt-2 text-xl font-semibold ${toneClass(action)}`}>{finite(position) ? `控制在 ${position}% 内` : '等待数据确认'}</div></div><div><div className="text-[10px] text-text-secondary">风格建议</div><div className="mt-2 text-sm font-medium text-text">{action}</div><div className="mt-1 text-[10px] text-text-secondary">{forecast.risk_preference.evidence.slice(0, 1).join('') || '不追逐未经确认的价格强度'}</div></div><div><div className="text-[10px] text-text-secondary">重点观察</div><div className="mt-2 flex flex-wrap gap-2">{sectors.length ? sectors.map((item) => <span key={item} className="v5-tag">{item}</span>) : <span className="text-xs text-text-secondary">等待板块确认</span>}</div></div></div><div className="border-t border-border px-4 py-2.5 text-[10px] leading-4 text-warn">不输出“必涨、稳赚、强烈买入”。当前置信上限 {percent(forecast.data_health.confidence_ceiling_pct)}，数据完整度 {percent(forecast.data_health.completeness_pct)}。</div></section>;
}

export default function MarketDecisionWorkbenchPage() {
  const [forecast, setForecast] = useState<ForecastSnapshot | null>(null);
  const [supplement, setSupplement] = useState<MarketSupplement | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [progress, setProgress] = useState(8);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [judgmentAction, setJudgmentAction] = useState('WAIT');
  const [judgmentNote, setJudgmentNote] = useState('');
  const [judgmentBusy, setJudgmentBusy] = useState(false);

  const load = useCallback(async (force = false) => {
    if (force) setRefreshing(true); else setLoading(true);
    setError('');
    setNotice('');
    setProgress(8);
    const forecastPath = `/forecast/dashboard${force ? '?refresh=true' : ''}`;
    const supplementPath = `/market/workbench${force ? '?refresh=true' : ''}`;
    try {
      const [forecastResult, supplementResult] = await Promise.allSettled([
        apiFetch<{ code: number; data: ForecastSnapshot }>(forecastPath, { cache: 'no-store', timeoutMs: 45000 }),
        apiFetch<{ code: number; data: MarketSupplement }>(supplementPath, { cache: 'no-store', timeoutMs: 30000 }),
      ]);
      if (forecastResult.status === 'fulfilled' && forecastResult.value.code === 0 && forecastResult.value.data) {
        setForecast(forecastResult.value.data);
        window.localStorage.setItem(FORECAST_CACHE_KEY, JSON.stringify(forecastResult.value.data));
      } else {
        throw forecastResult.status === 'rejected' ? forecastResult.reason : new Error('前瞻预测返回无效数据');
      }
      if (supplementResult.status === 'fulfilled' && supplementResult.value.code === 0 && supplementResult.value.data) {
        setSupplement(supplementResult.value.data);
        window.localStorage.setItem(SUPPLEMENT_CACHE_KEY, JSON.stringify(supplementResult.value.data));
      } else {
        try { setSupplement(JSON.parse(window.localStorage.getItem(SUPPLEMENT_CACHE_KEY) || 'null') as MarketSupplement | null); } catch { setSupplement(null); }
      }
      setProgress(100);
      if (force) setNotice('V5 预测与可用市场快照已重新核验');
    } catch (caught) {
      try {
        const cached = JSON.parse(window.localStorage.getItem(FORECAST_CACHE_KEY) || 'null') as ForecastSnapshot | null;
        const cachedSupplement = JSON.parse(window.localStorage.getItem(SUPPLEMENT_CACHE_KEY) || 'null') as MarketSupplement | null;
        if (cached) {
          setForecast(cached);
          setSupplement(cachedSupplement);
          setNotice('后端连接暂时中断，当前显示浏览器最近一次成功的 V5 快照。');
        } else throw caught;
      } catch (cacheError) {
        setError(friendlyApiError(cacheError, 'V5 预测中枢加载失败'));
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void load(false); const timer = window.setInterval(() => void load(false), 60_000); return () => window.clearInterval(timer); }, [load]);
  useEffect(() => { if (!loading && !refreshing) return undefined; const timer = window.setInterval(() => setProgress((current) => Math.min(92, current + Math.max(1, Math.ceil((92 - current) / 8)))), 350); return () => window.clearInterval(timer); }, [loading, refreshing]);

  useEffect(() => {
    const button = document.querySelector('[data-refresh-forecast]');
    const handler = () => void load(true);
    button?.addEventListener('click', handler);
    return () => button?.removeEventListener('click', handler);
  }, [load, forecast]);

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
    ['总览驾驶舱', '#top', Gauge], ['市场全景', '#market', Activity], ['资金流向', '#capital', Wallet], ['因子监控', '#factors', LineChart], ['板块分析', '#sectors', Layers3], ['个股雷达', '#alpha', ScanSearch], ['事件驱动', '#events', Bell], ['行为博弈', '#behavior', CircleDot], ['多周期预测', '#forecast', BrainCircuit], ['策略信号', '#strategy', Target], ['复盘回溯', '#history', History],
  ] as const, []);

  if (loading && !forecast) return <LoadingScreen progress={progress} />;
  if (error && !forecast) return <main className="v5-page v5-error"><AlertCircle size={32} className="text-down" /><h1 className="mt-4 text-base font-semibold text-text">V5 预测中枢加载失败</h1><p className="mt-2 max-w-md text-sm text-text-secondary">{error}</p><button type="button" onClick={() => void load(false)} className="v5-button mt-5"><RefreshCw size={13} />重新加载</button></main>;
  if (!forecast) return null;

  const health = forecast.data_health;
  const factors = (forecast.factors?.all || []).filter((item) => item.observed).slice(0, 8);
  const permission = supplement?.decision_2026?.trading_permission;
  const confidence = health.confidence_ceiling_pct ?? health.completeness_pct ?? 0;
  return (
    <main id="top" className="v5-page">
      <div className="v5-layout">
        <aside className="v5-sidebar">
          <div className="v5-side-brand"><span className="v5-brand-mark">V5</span><div><div className="text-xs font-semibold text-text">预测中枢</div><div className="mt-0.5 text-[9px] text-text-secondary">AI MULTI-FACTOR</div></div></div>
          <nav className="v5-side-nav" aria-label="V5工作台导航">{navItems.map(([label, href, Icon]) => <a key={href} href={href} className={`v5-side-link ${href === '#forecast' ? 'active' : ''}`}><Icon size={14} /><span>{label}</span></a>)}</nav>
          <div className="v5-side-risk"><div className="text-[10px] text-text-secondary">风险偏好</div><div className={`mt-2 text-xs font-semibold ${toneClass(forecast.risk_preference.label)}`}>{forecast.risk_preference.label || '核验中'}</div><div className="mt-2 flex items-center justify-between text-[10px]"><span className="font-mono text-text">{percent(confidence)}</span><span className="text-text-secondary">置信上限</span></div><ThinBar value={confidence} color={tone(forecast.risk_preference.label) === 'down' ? 'down' : 'accent'} /></div>
        </aside>

        <div className="v5-main">
          <header className="v5-topbar"><div className="min-w-0"><h1 className="truncate text-base font-semibold text-text">AI多因子共振预测中枢 <span className="ml-1 font-mono text-[10px] font-normal text-accent">V5.0</span></h1><p className="mt-1 truncate text-[10px] text-text-secondary">察变 · 建因 · 共振 · 推演 · 验证</p></div><div className="v5-top-meta"><span className="hidden sm:inline">{forecast.forecast_date}</span><span className="v5-live-dot">●</span><span>{supplement?.meta?.is_realtime ? '交易中' : '缓存快照'}</span><button type="button" className="v5-icon-button" title="刷新工作台" aria-label="刷新工作台" onClick={() => void load(true)} disabled={refreshing}>{refreshing ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}</button></div></header>
          <div className="v5-feature-nav"><a className="active" href="#forecast">预测引擎</a><a href="#factors">因子监控</a><a href="#sectors">板块轮动</a><a href="#alpha">Alpha雷达</a><a href="#history">历史回溯</a><a href="#strategy">策略执行</a><span className="ml-auto hidden items-center gap-2 text-[10px] text-text-secondary lg:flex"><Database size={12} />模型 {forecast.model_version} · 截止 {localTime(forecast.data_cutoff_time)}</span></div>
          {(notice || error) && <div className={`v5-notice ${error ? 'error' : ''}`}><span>{error || notice}</span><button type="button" onClick={() => { setNotice(''); setError(''); }} aria-label="关闭提示"><X size={13} /></button></div>}

          <div className="v5-content">
            <div className="v5-hero-grid"><div className="min-w-0"><ForecastTimeline forecast={forecast} /></div><div id="market" className="v5-right-stack"><MarketStatePanel supplement={supplement} /><div id="events"><EventMonitor forecast={forecast} supplement={supplement} /></div></div></div>

            <div className="v5-section-grid"><div id="sectors"><SectorResonance sectors={forecast.sector_forecasts || []} supplement={supplement} /></div><div id="alpha"><AlphaRadar seeds={forecast.alpha_seeds || []} /></div><div><CapitalFlow forecast={forecast} supplement={supplement} /></div></div>

            <div id="factors" className="v5-panel"><SectionHeader icon={LineChart} title="因子监控与共振变化" subtitle="领先因子 → 传播因子 → 确认因子，按新鲜度和可靠度进入预测" action={<span className="text-[10px] text-text-secondary">观测 {factors.length} / {forecast.factors?.all?.length || 0}</span>} /><div className="v5-factor-grid">{factors.map((item) => <div key={item.id} className="v5-factor"><div className="flex items-start justify-between gap-2"><span className="line-clamp-2 text-xs text-text">{item.name}</span><span className={`shrink-0 text-[10px] ${item.observed ? 'text-up' : 'text-warn'}`}>{item.observed ? '已观测' : '核验中'}</span></div><div className="mt-3 flex items-end justify-between"><span className="font-mono text-lg text-text">{numberText(item.value)}</span><span className={`font-mono text-[10px] ${toneClass(item.delta)}`}>{signedPercent(item.delta)}</span></div><ThinBar value={item.value} color={tone(item.state) === 'down' ? 'down' : tone(item.state) === 'up' ? 'up' : 'accent'} /><div className="mt-2 truncate text-[10px] text-text-secondary">{item.layer} · {item.source}</div></div>)}{!factors.length && <EmptyState text="暂无满足新鲜度门槛的因子" />}</div></div>

            <div className="v5-section-grid v5-behavior-grid"><BehaviorPanel behavior={forecast.behavior} /><div><TurningPoints forecast={forecast} /></div><div id="strategy"><StrategyAdvice forecast={forecast} supplement={supplement} /></div></div>

            <div className="v5-section-grid"><HistoricalAnalogs forecast={forecast} onLoad={() => void loadHistory()} loading={historyLoading} /><section className="v5-panel"><SectionHeader icon={Gauge} title="预测置信度与风险提示" subtitle="置信度受数据完整度上限约束" /><div className="p-4"><div className="flex items-end justify-between"><span className="text-3xl font-semibold text-text">{percent(confidence)}</span><StatusPill value={health.level}>{health.level || '核验中'}</StatusPill></div><ThinBar value={confidence} color={confidence >= 70 ? 'up' : 'warn'} /><div className="mt-4 space-y-2 text-[10px] leading-4 text-text-secondary">{(health.stale_factors || []).slice(0, 3).map((item) => <div key={item.factor_id} className="flex gap-2"><span className="text-warn">·</span>{item.name}沿用缓存，边际信息可能滞后</div>)}{(health.missing_factors || []).slice(0, 3).map((item) => <div key={item.factor_id} className="flex gap-2"><span className="text-warn">·</span>{item.name}当前缺少可核验来源：{item.source}</div>)}{!health.stale_factors?.length && !health.missing_factors?.length && <div>当前没有新增数据边界提示。</div>}</div></div></section></div>

            <section className="v5-panel"><SectionHeader icon={Wallet} title="AI与用户双轨判断" subtitle="保存你的判断，盘后与实际市场状态对照" /><div className="grid gap-3 p-4 sm:grid-cols-[160px_minmax(0,1fr)_auto] sm:items-end"><label className="text-[10px] text-text-secondary">我的判断<select value={judgmentAction} onChange={(event) => setJudgmentAction(event.target.value)} className="v5-select mt-1"><option value="BULLISH">偏多</option><option value="NEUTRAL">中性</option><option value="BEARISH">偏空</option><option value="WAIT">等待</option><option value="NO_TRADE">不交易</option></select></label><label className="text-[10px] text-text-secondary">依据与反证<textarea value={judgmentNote} onChange={(event) => setJudgmentNote(event.target.value)} rows={2} placeholder="记录与你的判断有关的事实、反证或观察条件" className="v5-textarea mt-1" /></label><button type="button" onClick={() => void saveJudgment()} disabled={judgmentBusy} className="v5-button">{judgmentBusy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}保存判断</button></div></section>

            {health.missing_factors.length > 0 && <section className="v5-data-boundary"><div className="flex items-center gap-2 font-medium text-warn"><Database size={13} />数据边界与补采队列</div><div className="mt-2 text-[10px] leading-4 text-text-secondary">{health.missing_factors.slice(0, 5).map((item) => `${item.name}：${item.source}`).join(' · ')}。缺口不会填充默认值，预测置信度已按规则封顶。</div></section>}
            <footer className="v5-footer"><span>V5.0 · {forecast.version} · {forecast.data_health.high_confidence_allowed ? '高置信度通道可用' : '高置信度通道受限'}</span><span>实时行情仅在交易时段更新，非交易时段使用最近完整快照</span><div className="flex gap-3"><Link href="/pro/research">研究中心</Link><Link href="/pro/personal">个人股票池</Link><Link href="/quant">量化策略</Link></div></footer>
          </div>
        </div>
      </div>
    </main>
  );
}
