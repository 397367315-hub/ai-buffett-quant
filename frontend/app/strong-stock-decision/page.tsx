'use client';

import dynamic from 'next/dynamic';
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  BarChart3,
  BookOpen,
  BrainCircuit,
  Clock3,
  Database,
  Flame,
  Gauge,
  Layers3,
  LineChart,
  Loader2,
  Network,
  RefreshCw,
  Search,
  ShieldAlert,
  Target,
  TrendingUp,
} from 'lucide-react';
import type { KlineRow } from '@/components/KlineChart';
import StrongStockV2Dashboard from '@/components/StrongStockV2Dashboard';
import { apiFetch, friendlyApiError } from '@/lib/api';

function V21BridgeWorkspace() {
  const [tab, setTab] = useState<'overview' | 'opportunities' | 'review'>('overview');
  const [payload, setPayload] = useState<AnyMap | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [excludeStarMarket, setExcludeStarMarket] = useState(true);
  const [excludeGem, setExcludeGem] = useState(true);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    if (refresh) setRefreshing(true);
    setError('');
    try {
      const params = new URLSearchParams({
        refresh: String(refresh),
        exclude_star_market: String(excludeStarMarket),
        exclude_gem: String(excludeGem),
      });
      const response = await apiFetch<{ data: AnyMap }>(`/strong-stock-decision/v21/overview?${params}`, { timeoutMs: 45000 });
      setPayload(response.data);
    } catch (caught) {
      setError(friendlyApiError(caught, 'V2.1桥接层暂时不可用'));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [excludeGem, excludeStarMarket]);

  useEffect(() => { void load(false); }, [load]);

  const regime = payload?.market?.regime || {};
  const opportunities = (payload?.opportunities || []).filter((item: AnyMap) => item.opportunity_pool !== 'RISK_EXCLUDE');
  const riskItems = (payload?.opportunities || []).filter((item: AnyMap) => item.opportunity_pool === 'RISK_EXCLUDE');
  const lifecycle = (payload?.lifecycle || []).slice(0, 12);
  const migration = (payload?.migration?.paths || []).slice(0, 6);
  const scan = payload?.candidate_scan || {};
  const renderRows = (rows: AnyMap[], empty: string) => rows.length ? rows.slice(0, 10).map((item, index) => {
    const key = `${item.symbol || item.sector_id || index}-${index}`;
    const content = <><div className="min-w-0"><div className="truncate text-xs text-text">{item.stock_name || item.sector_name || item.sector_id || '未命名'}</div><div className="mt-1 truncate text-[10px] text-text-secondary">{item.symbol ? `${item.selection_source_label || '候选'} · ${item.symbol}` : `${labelOf(item.state || item.sector_type)} · 置信度 ${item.confidence ?? '--'}`}{item.zone_stage ? ` · ${labelOf(item.zone_stage)}` : item.selection_source === 'system_scan' ? ' · 等待交易区确认' : ''}</div></div><span className={`self-start whitespace-nowrap text-[10px] ${item.priority === 'P1' || item.state === 'STARTING' ? 'text-up' : item.priority === 'EXCLUDE' ? 'text-down' : 'text-text-secondary'}`}>{labelOf(item.priority || item.state || 'WATCH')}</span></>;
    const className = "grid min-w-0 grid-cols-[1fr_auto] gap-3 border-b border-border py-2.5 no-underline last:border-0 hover:bg-white/[0.02]";
    return item.symbol ? <a key={key} href={`/strong-stock-decision?code=${encodeURIComponent(item.symbol)}`} className={className}>{content}</a> : <div key={key} className={className}>{content}</div>;
  }) : <div className="py-5 text-xs text-text-secondary">{empty}</div>;

  return <section className="mt-5 overflow-hidden rounded-md border border-border bg-card" aria-label="强势股交易决策系统 V2.1桥接层">
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
      <div className="min-w-0"><h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Network size={15} className="text-accent" />强势股交易决策 V2.1 · 板块轮动与机会雷达</h2><p className="mt-1 text-[10px] text-text-secondary">市场状态 → 板块生命周期 → 资金迁徙推断 → A/B区机会 → 盘后复盘 · Shadow</p></div>
      <div className="flex flex-wrap items-center justify-end gap-2">
        <label className="inline-flex items-center gap-1.5 text-[11px] text-text-secondary"><input type="checkbox" checked={excludeStarMarket} onChange={(event) => setExcludeStarMarket(event.target.checked)} />排除科创板</label>
        <label className="inline-flex items-center gap-1.5 text-[11px] text-text-secondary"><input type="checkbox" checked={excludeGem} onChange={(event) => setExcludeGem(event.target.checked)} />排除创业板</label>
        <button type="button" onClick={() => void load(true)} disabled={loading} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-xs text-text-secondary hover:border-accent hover:text-accent disabled:opacity-50" title="刷新全市场扫描与V2.1研究快照"><RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />刷新全市场</button>
      </div>
    </header>
    <div className="flex flex-wrap items-center gap-1 border-b border-border px-3 py-2">{([['overview', '市场与板块'], ['opportunities', 'A/B机会'], ['review', '盘后复盘']] as const).map(([key, label]) => <button key={key} type="button" onClick={() => setTab(key)} className={`rounded px-3 py-1.5 text-[11px] ${tab === key ? 'bg-accent/10 text-accent' : 'text-text-secondary hover:text-text'}`}>{label}</button>)}</div>
    {loading && !payload ? <div className="px-4 py-6 text-xs text-text-secondary"><div className="flex items-center gap-2"><Loader2 size={14} className="animate-spin text-accent" />正在读取市场状态、板块轨迹和V2.0快照</div><div className="mt-3 h-1 overflow-hidden rounded-full bg-border"><div className="h-full w-1/2 animate-pulse bg-accent" /></div></div> : error ? <div className="m-4 border-l-2 border-warn bg-warn/5 px-3 py-3 text-xs leading-5 text-warn">{error}<button type="button" className="ml-3 text-accent underline" onClick={() => void load(true)}>重试</button></div> : payload && <div className="grid gap-4 p-4 xl:grid-cols-[1.05fr_1fr_1fr]">
      <div className="min-w-0"><div className="mb-2 flex items-center justify-between"><span className="text-[11px] text-text-secondary">当前市场状态</span><span className="font-mono text-[10px] text-text-secondary">{payload.trade_date || '--'} · {payload.mode}</span></div><div className="border border-border bg-bg p-3"><div className="flex flex-wrap items-end justify-between gap-3"><strong className="text-lg text-text">{({ TREND_ATTACK: '进攻趋势市', ROTATION_RANGE: '高活跃震荡轮动市', DEFENSIVE_FADE: '防守退潮市', TRANSITION: '过渡/混沌市' } as Record<string, string>)[String(regime.regime)] || '过渡/混沌市'}</strong><span className="font-mono text-accent">{regime.confidence ?? '--'}%</span></div><div className="mt-2 text-xs leading-5 text-text-secondary">{regime.strategy_bias?.text || '等待足够市场证据'}</div><div className="mt-3 space-y-1 text-[10px] text-text-secondary">{(regime.evidence || []).slice(0, 3).map((item: AnyMap) => <div key={item.text}>事实 · {item.text}</div>)}{(regime.counter_evidence || []).slice(0, 2).map((item: AnyMap) => <div key={item.text} className="text-warn">反证 · {item.text}</div>)}</div></div><div className="mt-3 grid grid-cols-2 gap-2"><div className="border border-border p-2"><div className="text-[10px] text-text-secondary">板块数量</div><b className="font-mono text-sm text-text">{payload.sectors?.length || 0}</b></div><div className="border border-border p-2"><div className="text-[10px] text-text-secondary">迁徙推断</div><b className="font-mono text-sm text-text">{migration.length}</b></div></div></div>
      <div className="min-w-0"><div className="mb-2 flex items-center justify-between"><span className="text-[11px] font-semibold text-text">板块生命周期</span><span className="text-[10px] text-text-secondary">多日状态机</span></div><div className="max-h-64 overflow-auto pr-1">{renderRows(lifecycle, '暂无足够板块历史样本')}</div><div className="mt-3 border-t border-border pt-3">{migration.slice(0, 3).map((item: AnyMap) => <div key={`${item.source?.id}-${item.target?.id}`} className="flex min-w-0 items-center gap-2 py-1.5 text-[10px] text-text-secondary"><span className="truncate">{item.source?.name || '未命名板块'}</span><span className="text-accent">→</span><span className="truncate text-text">{item.target?.name || '未命名板块'}</span><span className="ml-auto shrink-0 font-mono">{item.confidence ?? '--'}%</span></div>)}</div></div>
      <div className="min-w-0">{tab === 'overview' && <><div className="mb-2 flex items-center justify-between"><span className="text-[11px] font-semibold text-text">机会与风险摘要</span><span className="text-[10px] text-text-secondary">A/B/C融合 + 全市场系统扫描</span></div><div className="mb-2 grid grid-cols-3 gap-1.5 text-[10px]"><div className="border border-border p-1.5 text-text-secondary">扫描 <b className="font-mono text-text">{scan.total_scanned ?? '--'}</b></div><div className="border border-border p-1.5 text-text-secondary">初筛 <b className="font-mono text-text">{scan.source_shortlisted ?? '--'}</b></div><div className="border border-border p-1.5 text-text-secondary">候选 <b className="font-mono text-text">{scan.shortlisted ?? '--'}</b></div></div>{renderRows([...opportunities, ...riskItems], '当前全市场扫描没有形成可展示候选')}<div className="mt-3 text-[10px] leading-4 text-text-secondary">资金迁徙是板块聚合数据的相对强弱推断，不是逐笔资金账户迁移。</div></>}{tab === 'opportunities' && <><div className="mb-2 text-[11px] font-semibold text-text">A区发现 / A区确认 / B区二攻 / 系统观察</div>{renderRows(opportunities, '暂无满足板块生命周期与A/B区条件的候选')}<div className="mt-3 border-t border-border pt-3 text-[10px] text-warn">风险淘汰：{riskItems.length} 条，C区优先级覆盖攻击信号。</div></>}{tab === 'review' && <><div className="mb-2 text-[11px] font-semibold text-text">今日盘后复盘入口</div><div className="space-y-2 text-xs leading-5 text-text-secondary"><p>状态：{regime.strategy_bias?.text || '数据不足，不能强行分类'}</p><p>明日验证：板块宽度、核心股跟随、资金连续性。</p><p>失效条件：市场转为防守退潮市，或核心股与板块宽度同步破坏。</p><p className="text-warn">结果需经过T+1/T+3/T+5验证后才进入经验层。</p></div></>}</div>
    </div>}
    {payload && <footer className="border-t border-border px-4 py-2 text-[10px] text-text-secondary">数据质量：{labelOf(payload.data_quality?.status || 'DATA_INCOMPLETE')} · 候选来源：{scan.source_label || '全市场系统扫描'} · 数据日：{scan.data_date || payload.trade_date || '--'} · {scan.is_realtime ? '实时' : scan.cache_used ? '缓存快照' : '最新可用'} · 运行模式：{labelOf(payload.mode || 'SHADOW')} · 不自动交易</footer>}
  </section>;
}

const KlineChart = dynamic(() => import('@/components/KlineChart'), {
  ssr: false,
  loading: () => <div className="grid h-[280px] place-items-center text-xs text-text-secondary"><Loader2 size={18} className="animate-spin text-accent" /></div>,
});

type AnyMap = Record<string, any>;

const ACTION_LABELS: Record<string, string> = {
  NO_TRADE: '暂不参与',
  WATCH: '观察等待',
  READY: '准备攻击',
  CONFIRMING: '等待确认',
  HOLD: '持有观察',
  RISK: '风险优先',
  EXIT: '退出观察',
};

const SIGNAL_LABELS: Record<string, string> = {
  NOT_FOUND: '未形成',
  POSSIBLE: '疑似',
  FORMING: '构建中',
  CONFIRMED: '已确认',
  WEAKENING: '减弱',
  INVALID: '失效',
};

const V21_LABELS: Record<string, string> = {
  HIDDEN: '潜伏', PREHEAT: '预热', STARTING: '启动', ACCELERATING: '加速',
  CLIMAX: '高潮', FIRST_DIVERGENCE: '首次分歧', RETURNING: '回流',
  SECOND_STRENGTH: '二次强化', FADING: '退潮', INVALID: '数据不足',
  A_PREPARE: 'A区准备', A_FORMING: 'A区形成', A_ACTIVE: 'A区活跃',
  A_LATE: 'A区后期', A_INVALID: 'A区无效', B_FORMING: 'B区形成',
  B_ACTIVE: 'B区活跃', B_SMALL_A_FORMING: 'B区小A形成', B_REATTACK: 'B区二攻',
  B_INVALID: 'B区无效', C_WARNING: 'C区预警', C_FORMED: 'C区形成',
  C_DEEPENING: 'C区加深', C_EXIT: 'C区退出', A_DISCOVERY: 'A区发现池',
  A_CONFIRM: 'A区确认池', RISK_EXCLUDE: '风险淘汰', WATCH: '观察',
  P1: '一级', P2: '二级', EXCLUDE: '淘汰', SHADOW: '影子研究',
  DATA_INCOMPLETE: '数据待确认', COMPLETE: '完整', PARTIAL: '部分可用',
  UNKNOWN: '未知', TRANSITION: '过渡/混沌', TREND_ATTACK: '进攻趋势',
  ROTATION_RANGE: '活跃震荡轮动', DEFENSIVE_FADE: '防守退潮',
};

function labelOf(value: unknown): string {
  const key = String(value || '').trim();
  return V21_LABELS[key] || key || '未知';
}

const DETAIL_TABS = [
  { id: 'overview', label: '决策总览' },
  { id: 'hunter', label: '猎取强势股' },
  { id: 'big-pattern', label: '暴涨大形态' },
  { id: 'star', label: '暴涨之星' },
  { id: 'main-force', label: '主力' },
  { id: 'stacking', label: '量能体叠加' },
  { id: 'cases', label: '历史案例' },
  { id: 'intraday', label: '盘中验证' },
  { id: 'explanation', label: 'AI解释' },
] as const;

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function numberText(value: unknown, digits = 2, fallback = '有效样本不足'): string {
  return finite(value) ? value.toFixed(digits) : fallback;
}

function percentText(value: unknown, digits = 2): string {
  return finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(digits)}%` : '有效样本不足';
}

function compactNumber(value: unknown): string {
  if (!finite(value)) return '有效样本不足';
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(1)}万`;
  return value.toFixed(0);
}

function actionLabel(action: unknown): string {
  return ACTION_LABELS[String(action || '')] || '暂不参与';
}

function signalLabel(status: unknown): string {
  return SIGNAL_LABELS[String(status || '')] || '未形成';
}

function aShareTone(value: unknown): string {
  if (!finite(value) || value === 0) return 'text-text-secondary';
  return value > 0 ? 'text-up' : 'text-down';
}

function statusTone(status: unknown): string {
  const value = String(status || '');
  if (value === 'CONFIRMED') return 'strong-status strong-status-up';
  if (value === 'POSSIBLE' || value === 'FORMING' || value === 'WEAKENING') return 'strong-status strong-status-warn';
  if (value === 'INVALID') return 'strong-status strong-status-down';
  return 'strong-status';
}

function actionTone(action: unknown): string {
  const value = String(action || '');
  if (value === 'EXIT' || value === 'RISK') return 'strong-action strong-action-risk';
  if (value === 'READY' || value === 'CONFIRMING') return 'strong-action strong-action-ready';
  if (value === 'HOLD') return 'strong-action strong-action-hold';
  return 'strong-action';
}

function safeText(value: unknown, fallback = '有效样本不足'): string {
  const text = String(value ?? '').trim();
  return text || fallback;
}

function latestSignal(items: AnyMap[] | undefined, preferredStatuses = ['CONFIRMED', 'FORMING', 'POSSIBLE']): AnyMap | null {
  for (const status of preferredStatuses) {
    const found = (items || []).find((item) => item?.status === status);
    if (found) return found;
  }
  return (items || [])[0] || null;
}

function Panel({ title, icon: Icon = Activity, meta, children, className = '', risk = false }: { title: string; icon?: any; meta?: ReactNode; children: ReactNode; className?: string; risk?: boolean }) {
  return (
    <section className={`strong-panel ${risk ? 'strong-panel-risk' : ''} ${className}`}>
      <header className="strong-panel-header">
        <h2><Icon size={15} />{title}</h2>
        {meta && <span>{meta}</span>}
      </header>
      <div className="strong-panel-body">{children}</div>
    </section>
  );
}

function MetricLine({ label, value, valueClass = 'text-text', detail }: { label: string; value: ReactNode; valueClass?: string; detail?: ReactNode }) {
  return (
    <div className="strong-metric-line">
      <span>{label}</span>
      <b className={valueClass}>{value}</b>
      {detail && <small>{detail}</small>}
    </div>
  );
}

function SignalRow({ signal, compact = false }: { signal: AnyMap; compact?: boolean }) {
  return (
    <div className={`strong-signal-row ${compact ? 'strong-signal-row-compact' : ''}`}>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-text">{safeText(signal?.name)}</span>
          {signal?.skill_id && <span className="shrink-0 font-mono text-[9px] text-text-secondary">{signal.skill_id}</span>}
        </div>
        {!compact && <div className="mt-1 line-clamp-2 text-[10px] leading-4 text-text-secondary">{safeText(signal?.evidence?.[0]?.text, '当前没有形成可核验的附加证据')}</div>}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {finite(signal?.confidence) && <span className="font-mono text-[10px] text-text-secondary">{signal.confidence.toFixed(0)}</span>}
        <span className={statusTone(signal?.status)}>{signalLabel(signal?.status)}</span>
      </div>
    </div>
  );
}

function ProgressGauge({ value }: { value: number | null }) {
  const safe = finite(value) ? Math.max(0, Math.min(100, value)) : 0;
  return (
    <div className="strong-gauge-wrap">
      <div className="strong-gauge" style={{ background: `conic-gradient(from 225deg, #3FB950 0deg, #3FB950 ${safe * 2.7}deg, #F85149 ${safe * 2.7}deg, #F85149 270deg, transparent 270deg)` }}>
        <div className="strong-gauge-hole">
          <strong className={safe >= 65 ? 'text-up' : safe >= 45 ? 'text-warn' : 'text-down'}>{finite(value) ? Math.round(value) : '--'}</strong>
          <span>结构评分</span>
        </div>
      </div>
      <div className="strong-gauge-scale"><span>0</span><span>100</span></div>
    </div>
  );
}

function DataBadge({ data, source }: { data: AnyMap; source?: string }) {
  const realtime = Boolean(data?.is_realtime);
  const date = data?.trade_date || data?.data_cutoff_time?.slice?.(0, 10);
  return <span className={`strong-data-badge ${realtime ? 'is-live' : ''}`}><i />{realtime ? '实时' : '历史/缓存'} · {safeText(date, '日期未标注')}{source ? ` · ${source}` : ''}</span>;
}

function emptyMessage(value: unknown): string {
  return value ? safeText(value) : '暂无可核验记录';
}

const SCORE_WEIGHTS = [0.22, 0.28, 0.16, 0.12, 0.12, 0.1];

function fallbackScoreComponents(qts: AnyMap, mainForce: AnyMap, volumeMa: AnyMap, zone: AnyMap): AnyMap[] {
  const eventScores: Record<string, number> = {
    '量价同步异动': 82,
    '量先异动': 68,
    '价先异动': 60,
    '无明显异动': 48,
  };
  const maScores: Record<string, number> = {
    '均线展开': 85,
    '均线归位中': 72,
    '均线聚合': 58,
    '均线未归位': 35,
  };
  const zoneScores: Record<string, number> = {
    '强势A区': 88,
    '强势B区': 68,
    '风险C区': 25,
  };
  return [
    { key: 'risk_control', label: '风险控制', value: finite(qts.risk) ? Math.max(0, Math.min(100, 100 - qts.risk)) : null, weight: SCORE_WEIGHTS[0], available: finite(qts.risk) },
    { key: 'quantity_time_space', label: '量时空', value: finite(qts.opportunity) ? qts.opportunity : null, weight: SCORE_WEIGHTS[1], available: finite(qts.opportunity) },
    { key: 'main_force', label: '主力证据', value: finite(mainForce.confidence) ? mainForce.confidence : null, weight: SCORE_WEIGHTS[2], available: finite(mainForce.confidence) },
    { key: 'volume_price', label: '量价异动', value: eventScores[volumeMa.event] ?? null, weight: SCORE_WEIGHTS[3], available: eventScores[volumeMa.event] !== undefined },
    { key: 'moving_average', label: '均线归位', value: maScores[volumeMa.ma_state] ?? null, weight: SCORE_WEIGHTS[4], available: maScores[volumeMa.ma_state] !== undefined },
    { key: 'trading_zone', label: 'A/B/C区', value: zoneScores[zone.zone] ?? null, weight: SCORE_WEIGHTS[5], available: zoneScores[zone.zone] !== undefined },
  ];
}

function weightedScore(components: AnyMap[]): number | null {
  const available = components.filter((item) => finite(item.value) && finite(item.weight));
  const weight = available.reduce((total, item) => total + item.weight, 0);
  return weight > 0 ? available.reduce((total, item) => total + item.value * item.weight, 0) / weight : null;
}

function scoreText(value: unknown): string {
  return finite(value) ? `${Math.round(value)}分` : '--';
}

function scoreTone(value: unknown): string {
  if (!finite(value)) return 'text-text-secondary';
  return value >= 70 ? 'text-up' : value >= 50 ? 'text-warn' : 'text-down';
}

export default function StrongStockDecisionPage() {
  const [symbol, setSymbol] = useState('002123');
  const [input, setInput] = useState('002123');
  const [data, setData] = useState<AnyMap | null>(null);
  const [detailTab, setDetailTab] = useState('overview');
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [intraday, setIntraday] = useState<AnyMap | null>(null);
  const [cases, setCases] = useState<AnyMap | null>(null);
  const [refreshingDetail, setRefreshingDetail] = useState(false);

  const loadOverview = useCallback(async (code: string, refresh = false) => {
    const normalized = code.trim().toUpperCase().replace(/\.(SH|SZ|BJ)$/i, '');
    if (!/^\d{6}$/.test(normalized)) {
      setError('请输入六位有效股票代码');
      return;
    }
    setLoading(true);
    setProgress(10);
    setError('');
    setIntraday(null);
    setCases(null);
    const timer = window.setInterval(() => setProgress((current) => Math.min(91, current + Math.max(1, Math.round((91 - current) / 8)))), 260);
    try {
      const response = await apiFetch<{ data: AnyMap }>(`/strong-stock-decision/${normalized}/overview?refresh=${refresh ? 'true' : 'false'}`, { timeoutMs: 35000 });
      setData(response.data);
      setSymbol(normalized);
      setInput(normalized);
      setProgress(100);
    } catch (caught) {
      setData(null);
      setError(friendlyApiError(caught, '强势股交易决策加载失败'));
    } finally {
      window.clearInterval(timer);
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const queryCode = typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('code') : null;
    void loadOverview(queryCode || '002123');
  }, [loadOverview]);

  const loadIntraday = useCallback(async () => {
    if (!symbol || intraday) return;
    setRefreshingDetail(true);
    try {
      const response = await apiFetch<{ data: AnyMap }>(`/strong-stock-decision/${symbol}/intraday`);
      setIntraday(response.data);
    } catch (caught) {
      setError(friendlyApiError(caught, '盘中验证加载失败'));
    } finally {
      setRefreshingDetail(false);
    }
  }, [intraday, symbol]);

  const loadCases = useCallback(async () => {
    if (!symbol || cases) return;
    setRefreshingDetail(true);
    try {
      const response = await apiFetch<{ data: AnyMap }>(`/strong-stock-decision/${symbol}/cases`);
      setCases(response.data);
    } catch (caught) {
      setError(friendlyApiError(caught, '历史案例加载失败'));
    } finally {
      setRefreshingDetail(false);
    }
  }, [cases, symbol]);

  useEffect(() => {
    if (detailTab === 'intraday') void loadIntraday();
    if (detailTab === 'cases') void loadCases();
  }, [detailTab, loadCases, loadIntraday]);

  const quote = data?.quote || {};
  const bars: AnyMap[] = data?.bars || [];
  const klineRows: KlineRow[] = useMemo(() => bars.map((row) => ({
    date: safeText(row.trade_date, ''),
    open: finite(row.open) ? row.open : null,
    close: finite(row.close) ? row.close : null,
    high: finite(row.high) ? row.high : null,
    low: finite(row.low) ? row.low : null,
    volume: finite(row.volume) ? row.volume : null,
    amount: finite(row.amount) ? row.amount : null,
    change_pct: finite(row.change_pct) ? row.change_pct : null,
  })), [bars]);
  const lastBar = bars[bars.length - 1] || {};
  const close = finite(quote.price) ? quote.price : lastBar.close;
  const changePct = finite(quote.change_pct) ? quote.change_pct : lastBar.change_pct;
  const priorClose = finite(quote.previous_close) ? quote.previous_close : lastBar.close && finite(lastBar.change_pct) ? lastBar.close / (1 + lastBar.change_pct / 100) : null;
  const qts = data?.quantity_time_space || {};
  const mainForce = data?.main_force || {};
  const volumeMa = data?.volume_price_ma || {};
  const zone = data?.best_trading_zone || {};
  const decision = data?.decision || {};
  const bigPatterns: AnyMap[] = data?.big_patterns || [];
  const stars: AnyMap[] = data?.rising_stars || [];
  const profitPatterns: AnyMap[] = data?.profit_patterns || [];
  const sellSignals: AnyMap[] = data?.sell_signals || [];
  const stacking = data?.volume_energy_stacking || {};
  const topic = data?.topic_confirmation || {};
  const sourceStatus = data?.source_status || {};
  const strongestBig = latestSignal(bigPatterns);
  const strongestStar = latestSignal(stars);
  const strongestProfit = latestSignal(profitPatterns);
  const riskSignal = latestSignal(sellSignals, ['CONFIRMED', 'POSSIBLE', 'FORMING']);
  const sellTop = sellSignals.find((item) => item.skill_id === 'HQS_015') || null;
  const sellResistance = sellSignals.find((item) => item.skill_id === 'HQS_016') || null;
  const sellZone = sellSignals.find((item) => item.skill_id === 'HQS_017') || null;
  const riskCount = (data?.signals || []).filter((item: AnyMap) => item.status === 'CONFIRMED' && /风险|卖出|现顶/.test(item.name || '')).length;
  const backendScore = data?.composite_score || {};
  const scoreComponents: AnyMap[] = Array.isArray(backendScore.components) && backendScore.components.length
    ? backendScore.components
    : fallbackScoreComponents(qts, mainForce, volumeMa, zone);
  const score = finite(backendScore.value) ? backendScore.value : weightedScore(scoreComponents);
  const scoreCoverage = finite(backendScore.coverage_pct)
    ? backendScore.coverage_pct
    : scoreComponents.reduce((total, item) => total + (item.available !== false && finite(item.value) ? Number(item.weight || 0) * 100 : 0), 0);
  const sourceName = String(sourceStatus.daily_bars_source || '').includes('tencent') ? '腾讯行情' : sourceStatus.daily_bars_source ? '系统日线缓存' : undefined;
  const v2 = data?.v2 || null;

  const renderDetail = () => {
    if (!data) return null;
    if (detailTab === 'hunter') {
      const hunterSignals = (data.signals || []).filter((item: AnyMap) => String(item.skill_id || '').startsWith('HQS_'));
      return <DetailList title="《猎取强势股》技能核验" subtitle="风险优先，量时空 → 主力 → 均线 → 最佳交易区 → 卖出策略" items={hunterSignals} />;
    }
    if (detailTab === 'big-pattern') return <DetailList title="《暴涨大形态》结构核验" subtitle="高级形态只作为候选观察，未经回测不进入 Active" items={bigPatterns} />;
    if (detailTab === 'star') return <DetailList title="《暴涨之星》时机核验" subtitle="攻击信号必须等待量、主力、交易区和后续价格确认" items={stars} />;
    if (detailTab === 'main-force') return <EvidencePanel title="主力证据时间线" value={mainForce} signals={(data.signals || []).filter((item: AnyMap) => ['HQS_003', 'HQS_004'].includes(item.skill_id))} />;
    if (detailTab === 'stacking') return <DetailList title="量能体叠加与冲突" subtitle="风险 C 区和卖出信号优先于攻击信号" items={data.signals || []} />;
    if (detailTab === 'cases') return <CasePanel value={cases} loading={refreshingDetail} />;
    if (detailTab === 'intraday') return <IntradayPanel value={intraday} loading={refreshingDetail} />;
    if (detailTab === 'explanation') return <ExplanationPanel value={data.explanation || {}} />;
    return <DetailList title="决策总览：全部可核验技能" subtitle="书内术语与工程特征分层显示" items={data.signals || []} />;
  };

  return (
    <div className="strong-terminal min-h-screen">
      <header className="strong-terminal-header">
        <div className="strong-title-block">
          <div className="strong-title-row"><h1>强势股交易决策系统 V2.0</h1><span className="strong-shadow-badge">V2 Shadow · V1兼容</span></div>
          <p>基于《猎取强势股》《暴涨大形态》《暴涨之星》三书体系</p>
        </div>
      </header>

      <main className="strong-terminal-main">
          <section className="strong-stock-strip">
            <div className="strong-search-cell"><label>股票代码</label><div className="strong-code-search"><input value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void loadOverview(input); }} /><button type="button" onClick={() => void loadOverview(input)} aria-label="查询股票"><Search size={15} /></button></div></div>
            <div className="strong-stock-name"><span>股票名称</span><b>{safeText(data?.name, '等待查询')}</b></div>
            <div className="strong-price"><b>{finite(close) ? close.toFixed(2) : '--'}</b><span className={aShareTone(changePct)}>{percentText(changePct)}</span><small>{finite(priorClose) ? `昨收 ${priorClose.toFixed(2)}` : '有效样本不足'}</small></div>
            <div className="strong-strip-item"><span>行业</span><b>{safeText(data?.sector?.name, '行业未标注')}</b></div>
            <div className="strong-strip-item"><span>数据来源</span><b>{sourceName || '系统缓存'}</b></div>
            <div className="strong-strip-item"><span>分析周期</span><b>日线</b></div>
            <div className="strong-strip-status"><DataBadge data={data || {}} source={sourceName} /></div>
            <button type="button" className="strong-refresh-button" onClick={() => void loadOverview(symbol, true)} disabled={loading} title="刷新当前股票"><RefreshCw size={15} className={loading ? 'animate-spin' : ''} /></button>
          </section>

          {error && <div className="strong-error"><AlertTriangle size={15} />{error}<button type="button" onClick={() => void loadOverview(symbol, true)}>重试</button></div>}
          {loading && <div className="strong-loading"><Loader2 size={17} className="animate-spin" /><span>正在核验日线、量价、主力与三书技能…</span><div><i style={{ width: `${progress}%` }} /></div><b>{progress}%</b></div>}

          {!data && !loading ? <div className="strong-empty-page"><Search size={28} /><p>输入六位股票代码开始分析</p></div> : data && <>
            <div className="strong-primary-grid">
              <Panel title="实时K线图（日线）" icon={LineChart} meta={<span>{data?.trade_date || '--'} · {bars.length}根日线</span>} className="strong-kline-panel">
                <div className="strong-ma-legend"><span>MA5:{numberText(data?.engine_features?.ma5)}</span><span>MA10:{numberText(data?.engine_features?.ma10)}</span><span>MA20:{numberText(data?.engine_features?.ma20)}</span><span>MA30:{numberText(data?.v2?.moving_average?.values?.ma30)}</span><span>MA60:{numberText(data?.engine_features?.ma60)}</span></div>
                <KlineChart rows={klineRows.slice(-150)} height={280} showMovingAverages annotations={data?.v2?.annotations || []} showAnnotations />
                <div className="strong-chart-annotations">{[zone.zone, strongestBig?.name, strongestStar?.name].filter(Boolean).map((item) => <span key={item}>{item}</span>)}</div>
              </Panel>
              <Panel title="决策总览（综合评分）" icon={Gauge} className="strong-score-panel">
                <div className="strong-score-content"><ProgressGauge value={score} /><div className="strong-score-list">{scoreComponents.map((item) => <MetricLine key={item.key} label={safeText(item.label, item.key)} value={scoreText(item.value)} valueClass={scoreTone(item.value)} />)}</div></div>
                <div className="strong-score-note">{safeText(backendScore.method, '可用组件按权重归一化')} · 数据覆盖 {finite(scoreCoverage) ? `${Math.round(scoreCoverage)}%` : '--'} · 评分仅用于结构排序参考。</div>
              </Panel>
              <Panel title="ACTION 建议" icon={Target} className="strong-action-panel" risk={decision.action === 'RISK' || decision.action === 'EXIT'}>
                <div className={`strong-action-heading ${decision.action === 'RISK' || decision.action === 'EXIT' ? 'is-risk' : ''}`}>{safeText(decision.primary_skill, '等待结构')}<span>→</span>{actionLabel(decision.action)}</div>
                <p className="strong-action-summary">{decision.action === 'RISK' || decision.action === 'EXIT' ? '风险信号优先，暂停攻击类解释，等待结构修复或明确失效。' : '满足当前阶段的部分条件，仍需等待后续价格、成交和板块互证。'}</p>
                <div className="strong-action-subtitle">下一步确认：</div>
                <ol>{(decision.next_confirmation || []).slice(0, 3).map((item: string) => <li key={item}>{item}</li>)}</ol>
                <div className="strong-action-subtitle is-risk-text">失效条件：</div>
                <ol className="is-risk-list">{(decision.invalidation || []).slice(0, 3).map((item: string) => <li key={item}>{item}</li>)}</ol>
                <div className={actionTone(decision.action)}>{safeText(decision.action, 'NO_TRADE')} · {actionLabel(decision.action)}</div>
              </Panel>
              <Panel title="风险监控（实时）" icon={ShieldAlert} className="strong-risk-panel" risk>
                <MetricLine label="大盘/结构风险" value={zone.zone === '风险C区' ? '高' : zone.zone === '强势B区' ? '中' : '低'} valueClass={zone.zone === '风险C区' ? 'text-down' : 'text-up'} />
                <MetricLine label="最佳交易区" value={safeText(zone.zone)} valueClass={zone.zone === '风险C区' ? 'text-down' : 'text-up'} />
                <MetricLine label="量时空压力" value={finite(qts.risk) ? `${Math.round(qts.risk)}/100` : '有效样本不足'} valueClass={finite(qts.risk) && qts.risk >= 70 ? 'text-down' : 'text-warn'} />
                <MetricLine label="卖出信号" value={riskSignal ? signalLabel(riskSignal.status) : '未触发'} valueClass={riskSignal?.status === 'CONFIRMED' ? 'text-down' : 'text-warn'} />
                <MetricLine label="风险状态" value={riskCount > 0 || zone.zone === '风险C区' ? '需优先处理' : '可控'} valueClass={riskCount > 0 || zone.zone === '风险C区' ? 'text-down' : 'text-up'} />
                <div className="strong-risk-foot">风险C区与卖出类信号优先于攻击信号。</div>
              </Panel>
            </div>

            <div className="strong-card-grid strong-card-grid-four">
              <Panel title="主力状态" icon={TrendingUp}><MetricLine label="主力状态" value={safeText(mainForce.state)} valueClass={mainForce.direction === '偏多' ? 'text-up' : mainForce.direction === '偏空' ? 'text-down' : 'text-warn'} /><MetricLine label="主力方向" value={safeText(mainForce.direction)} valueClass={mainForce.direction === '偏多' ? 'text-up' : 'text-text'} /><MetricLine label="持续性" value={safeText(mainForce.persistence)} valueClass="text-text" /><MetricLine label="证据置信度" value={finite(mainForce.confidence) ? `${Math.round(mainForce.confidence)}%` : '有效样本不足'} valueClass="text-warn" /><div className="strong-mini-note">{safeText(mainForce.evidence?.[0]?.text, '日线样本不足，不能还原主力身影')}</div></Panel>
              <Panel title="量价异动" icon={BarChart3}><MetricLine label="量价事件" value={safeText(volumeMa.event)} valueClass={volumeMa.event === '量价同步异动' ? 'text-up' : 'text-warn'} /><MetricLine label="均线状态" value={safeText(volumeMa.ma_state)} /><MetricLine label="推动链" value={safeText(volumeMa['推动'])} valueClass={volumeMa['推动']?.includes('推动') ? 'text-up' : 'text-warn'} /><MetricLine label="状态" value={volumeMa.status === 'AVAILABLE' ? '可核验' : '数据不足'} valueClass={volumeMa.status === 'AVAILABLE' ? 'text-up' : 'text-warn'} /></Panel>
              <Panel title="均线归位" icon={LineChart}><MetricLine label="均线状态" value={safeText(volumeMa.ma_state)} valueClass="text-up" /><MetricLine label="MA5" value={numberText(data.engine_features?.ma5)} /><MetricLine label="MA20" value={numberText(data.engine_features?.ma20)} /><MetricLine label="MA60" value={numberText(data.engine_features?.ma60)} /><MetricLine label="成交量比" value={finite(data.engine_features?.volume_ratio) ? data.engine_features.volume_ratio.toFixed(2) : '有效样本不足'} valueClass="text-warn" /></Panel>
              <Panel title="强势A/B/C区" icon={Target} risk={zone.zone === '风险C区'}><MetricLine label="当前区域" value={safeText(zone.zone)} valueClass={zone.zone === '风险C区' ? 'text-down' : 'text-up'} /><div className="strong-zone-list"><div className={zone.zone === '强势A区' ? 'is-current' : ''}>A区 · 趋势与量价共振</div><div className={zone.zone === '强势B区' ? 'is-current' : ''}>B区 · 调整/重新转强</div><div className={zone.zone === '风险C区' ? 'is-current is-risk' : ''}>C区 · 风险优先</div></div><div className="strong-mini-note">{(zone.reasons || []).slice(0, 2).join('；') || '尚未形成明确交易区'}</div></Panel>
            </div>

            <div className="strong-card-grid strong-card-grid-four">
              <Panel title="暴涨大形态" icon={Layers3}><MetricLine label="当前形态" value={safeText(strongestBig?.name, '未形成')} valueClass={strongestBig?.status === 'CONFIRMED' ? 'text-up' : 'text-warn'} /><MetricLine label="阶段" value={signalLabel(strongestBig?.status)} /><MetricLine label="置信度" value={finite(strongestBig?.confidence) ? `${Math.round(strongestBig.confidence)}%` : '有效样本不足'} /><div className="strong-pattern-line">{bigPatterns.filter((item) => item.status !== 'NOT_FOUND').slice(0, 3).map((item) => <span key={item.skill_id}>{item.name}</span>)}</div></Panel>
              <Panel title="暴涨之星" icon={Flame}><MetricLine label="当前星级" value={safeText(strongestStar?.name, '未形成')} valueClass={strongestStar?.name?.includes('现顶') ? 'text-down' : 'text-up'} /><MetricLine label="星线阶段" value={signalLabel(strongestStar?.status)} /><MetricLine label="关键位置" value={safeText(strongestStar?.chart_annotations?.[0]?.key_price ? numberText(strongestStar.chart_annotations[0].key_price) : null)} /><div className="strong-star-stars">{stars.filter((item) => item.status !== 'NOT_FOUND').slice(0, 5).map((item) => <span key={item.skill_id} className={item.name?.includes('现顶') ? 'text-down' : 'text-up'}>★</span>)}</div></Panel>
              <Panel title="经典盈利模式" icon={BookOpen}><MetricLine label="匹配模式" value={safeText(strongestProfit?.name, '未形成')} valueClass="text-up" /><MetricLine label="模式阶段" value={signalLabel(strongestProfit?.status)} /><MetricLine label="条件状态" value={strongestProfit ? '候选观察' : '暂无'} /><div className="strong-mini-note">模式识别不等于交易指令，需结合卖出风险和后续确认。</div></Panel>
              <Panel title="量能体叠加术" icon={Database}><MetricLine label="叠加信号" value={safeText(stacking.level, '有效样本不足')} valueClass={stacking.level === '很强' || stacking.level === '强' ? 'text-up' : 'text-warn'} /><MetricLine label="已确认" value={stacking.confirmed?.length ?? '有效样本不足'} /><MetricLine label="疑似构建" value={stacking.possible?.length ?? '有效样本不足'} /><MetricLine label="风险冲突" value={stacking.risks?.length ?? '有效样本不足'} valueClass={stacking.risks?.length ? 'text-down' : 'text-up'} /></Panel>
            </div>

            <div className="strong-card-grid strong-card-grid-four">
              <Panel title="题材互证" icon={BrainCircuit}><MetricLine label="互证状态" value={safeText(topic.status)} valueClass={topic.status === '成立' ? 'text-up' : topic.status === '不足' ? 'text-warn' : 'text-text-secondary'} /><MetricLine label="量能体" value={safeText(topic.volume_energy)} /><MetricLine label="题材强度" value={safeText(topic.theme)} /><div className="strong-mini-note">{(topic.reasons || []).join('；') || '没有可核验板块或题材数据'}</div></Panel>
              <Panel title="卖出风险" icon={AlertTriangle} risk={Boolean(riskSignal && riskSignal.status !== 'NOT_FOUND')}><MetricLine label="明显见顶" value={sellTop ? signalLabel(sellTop.status) : '未触发'} valueClass="text-down" /><MetricLine label="明显遇顶" value={sellResistance ? signalLabel(sellResistance.status) : '未触发'} valueClass="text-warn" /><MetricLine label="C区卖出" value={sellZone ? signalLabel(sellZone.status) : '未触发'} valueClass={zone.zone === '风险C区' ? 'text-down' : 'text-up'} /><div className="strong-mini-note is-risk-text">{safeText(riskSignal?.evidence?.[0]?.text, '当前没有形成卖出类核验信号')}</div></Panel>
              <Panel title="决策链路（因果链）" icon={ArrowDownRight} className="strong-chain-panel"><div className="strong-chain">{['风险监控', '量时空', '主力状态', '量价异动', '均线归位', 'A/B/C区', '大形态', '暴涨之星', '量能叠加', '题材互证', '卖出风险'].map((item, index) => <span key={item} className={index < 3 && decision.action !== 'NO_TRADE' ? 'is-passed' : ''}>{item}{index < 10 && <ArrowRight size={12} />}</span>)}</div></Panel>
              <Panel title="数据审计" icon={Clock3}><MetricLine label="日线" value={sourceStatus.daily_bars === 'available' ? '可用' : '不可用'} valueClass={sourceStatus.daily_bars === 'available' ? 'text-up' : 'text-down'} detail={safeText(sourceStatus.daily_bars_source, '来源未标注')} /><MetricLine label="个股资金" value={sourceStatus.stock_flow === 'available' ? '可用' : '未覆盖'} valueClass={sourceStatus.stock_flow === 'available' ? 'text-up' : 'text-warn'} /><MetricLine label="板块资金" value={sourceStatus.sector_flow === 'available' ? '可用' : '未覆盖'} valueClass={sourceStatus.sector_flow === 'available' ? 'text-up' : 'text-warn'} /><MetricLine label="数据完整度" value={finite(data.data_completeness_pct) ? `${data.data_completeness_pct.toFixed(0)}%` : '有效样本不足'} valueClass="text-warn" /><DataBadge data={data} source={sourceName} /></Panel>
            </div>

            <section className="strong-detail-shell">
              <div className="strong-detail-tabs">{DETAIL_TABS.map((tab) => <button type="button" key={tab.id} onClick={() => setDetailTab(tab.id)} className={detailTab === tab.id ? 'is-active' : ''}>{tab.label}</button>)}</div>
              <div className="strong-detail-content">{renderDetail()}</div>
            </section>

            {v2 && <StrongStockV2Dashboard v2={v2} symbol={symbol} onRefresh={() => void loadOverview(symbol, true)} />}
            <V21BridgeWorkspace />

            <footer className="strong-terminal-footer"><span>免责声明：本系统基于三本书理论与可核验数据开发，仅供学习和研究参考，不构成投资建议。</span><span><b>ⓘ</b> Shadow模式：仅分析不执行交易</span></footer>
          </>}
      </main>
    </div>
  );
}

function DetailList({ title, subtitle, items }: { title: string; subtitle: string; items: AnyMap[] }) {
  return <div><div className="strong-detail-heading"><div><h2>{title}</h2><p>{subtitle}</p></div><span>{items.filter((item) => item.status !== 'NOT_FOUND').length} 个有效信号</span></div><div className="strong-signal-list">{items.length ? items.map((item) => <SignalRow key={`${item.skill_id}-${item.name}`} signal={item} />) : <div className="strong-empty-inline">暂无可核验技能结果</div>}</div></div>;
}

function EvidencePanel({ title, value, signals }: { title: string; value: AnyMap; signals: AnyMap[] }) {
  return <div><div className="strong-detail-heading"><div><h2>{title}</h2><p>只描述可观察的成交、价格和承接证据，不推断参与者意图。</p></div><span className="text-up">{safeText(value.direction)}</span></div><div className="strong-evidence-grid"><div className="strong-evidence-summary"><MetricLine label="主力身影" value={safeText(value.state)} valueClass="text-up" /><MetricLine label="方向" value={safeText(value.direction)} /><MetricLine label="持续性" value={safeText(value.persistence)} /><MetricLine label="置信度" value={finite(value.confidence) ? `${Math.round(value.confidence)}%` : '有效样本不足'} /><p>{safeText(value.evidence?.map((item: AnyMap) => item.text).join('；'), '日线样本不足')}</p></div><div className="strong-signal-list">{signals.map((item) => <SignalRow key={item.skill_id} signal={item} />)}</div></div></div>;
}

function CasePanel({ value, loading }: { value: AnyMap | null; loading: boolean }) {
  if (loading) return <div className="strong-detail-loading"><Loader2 size={18} className="animate-spin" />正在读取历史案例</div>;
  const rows = value?.cases || [];
  return <div><div className="strong-detail-heading"><div><h2>望星空案例对照</h2><p>{safeText(value?.note, '不把未经标注的历史形态伪装成正例。')}</p></div><span>{rows.length} 条记录</span></div>{rows.length ? <div className="strong-case-grid">{rows.map((row: AnyMap) => <article key={row.id}><div className="flex items-center justify-between gap-2"><b>{safeText(row.book)}</b><span className="strong-status">{safeText(row.case_type)}</span></div><div className="mt-2 text-xs text-text">{safeText(row.skill_id)}</div><div className="mt-2 text-[10px] text-text-secondary">{safeText(row.start_date)} → {safeText(row.end_date)}</div><p>{safeText(row.notes, '暂无案例说明')}</p></article>)}</div> : <div className="strong-empty-inline">当前股票还没有已标注正例、反例或形似失败案例。</div>}</div>;
}

function IntradayPanel({ value, loading }: { value: AnyMap | null; loading: boolean }) {
  if (loading) return <div className="strong-detail-loading"><Loader2 size={18} className="animate-spin" />正在读取盘中验证</div>;
  const events = value?.events || [];
  return <div><div className="strong-detail-heading"><div><h2>盘中验证</h2><p>日线负责结构，盘中只验证突破、攻击、风险恶化与失效，不随意推翻日线大形态。</p></div><DataBadge data={{ ...value, trade_date: value?.latest_bar_at?.slice?.(0, 10) }} source={value?.source} /></div><div className="strong-intraday-grid"><div><MetricLine label="数据状态" value={safeText(value?.data_status, '有效样本不足')} valueClass={value?.data_status === 'AVAILABLE' ? 'text-up' : 'text-warn'} /><MetricLine label="最新分钟" value={safeText(value?.latest_bar_at)} /><MetricLine label="分钟数量" value={value?.bar_count ?? '有效样本不足'} /></div><div className="strong-event-list">{events.length ? events.map((item: AnyMap) => <div key={item.event} className={item.status === 'OBSERVED' ? 'is-observed' : 'is-watch'}><b>{safeText(item.event)}</b><span>{safeText(item.text)}</span></div>) : <div className="strong-empty-inline">暂无盘中事件，可能处于非交易时段或上游分钟源未返回。</div>}</div></div></div>;
}

function ExplanationPanel({ value }: { value: AnyMap }) {
  const sections = [['当前判断', value['当前判断']], ['因', value['因']], ['书中技能互证', value['书中技能互证']], ['果', value['果']], ['下一步', value['下一步']], ['失效', value['失效']], ['规则边界', value['规则边界']]];
  return <div><div className="strong-detail-heading"><div><h2>AI解释</h2><p>当前为规则引擎解释层；只基于已计算证据，不编造主力意图。</p></div><span className="text-accent">纯文本审计</span></div><div className="strong-explanation-grid">{sections.map(([label, content]) => <article key={label}><h3>{label}</h3>{Array.isArray(content) ? <ul>{content.map((item: any) => <li key={String(item)}>{typeof item === 'object' ? JSON.stringify(item) : String(item)}</li>)}</ul> : <p>{safeText(content, '有效样本不足')}</p>}</article>)}</div></div>;
}
