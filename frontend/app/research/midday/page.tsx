'use client';

import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BarChart3,
  BookOpenCheck,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Clock3,
  Database,
  FlaskConical,
  History,
  LineChart,
  Loader2,
  Microscope,
  RefreshCw,
  ScanSearch,
  ShieldAlert,
  Sparkles,
  Target,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import { ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import AddToPersonalPoolButton from '@/components/AddToPersonalPoolButton';
import StockKlineButton from '@/components/StockKlineButton';
import { apiFetch, friendlyApiError } from '@/lib/api';

type Tab = 'overview' | 'sectors' | 'stocks' | 'preview' | 'history';
type AnomalyKey = 'contrarian_strength' | 'alpha_strengthening' | 'beta_weak' | 'high_position_negative_feedback';

interface ResearchSessionSummary {
  id: string;
  mode: string;
  status: string;
  stage: string;
  progress: number;
  source_data_date: string | null;
  created_at: string | null;
  completed_at: string | null;
  error?: string | null;
  summary?: {
    market_state?: string;
    principal_conflict?: string;
    conflict_status?: string;
    candidate_count?: number;
    scenario?: string;
    validation_status?: string;
  };
}

interface ResearchChainItem {
  key: string;
  time: string;
  label: string;
  question: string;
  status: string;
  href: string;
}

interface SectorRole {
  role: string;
  code: string;
  name: string;
  change_pct: number | null;
}

interface SectorStructure {
  name: string;
  status: string;
  direction: string;
  structure_score: number | null;
  member_count: number;
  up_count: number;
  breadth_pct: number | null;
  average_change_pct: number | null;
  median_change_pct: number | null;
  leader_gap_pct: number | null;
  rear_average_pct: number | null;
  main_net_inflow: number | null;
  flow_rank: number | null;
  flow_source?: string;
  roles: SectorRole[];
  evidence: string[];
  risk_flags: string[];
}

interface FundBehaviour {
  state: string;
  label: string;
  interpretation: string;
}

interface StockRow {
  code: string;
  name: string;
  sector: string;
  price: number | null;
  change_pct: number | null;
  volume_ratio: number | null;
  turnover: number | null;
  market_cap_yi?: number | null;
  main_net_inflow: number | null;
  market_alpha_pct?: number | null;
  sector_alpha_pct?: number | null;
  position_20d_pct?: number | null;
  score?: number | null;
  reason?: string;
  fund_behaviour?: FundBehaviour;
}

interface PreviewStock extends StockRow {
  quality: string;
  passed_evidence: string[];
  failed: string[];
  unavailable: string[];
  pending_confirmation: string[];
}

interface Scenario {
  key: string;
  name: string;
  support_pct: number;
  nature: string;
  conditions: string[];
  watch: string[];
  action: string;
  falsification: string[];
}

interface Checkpoint {
  checkpoint: string;
  captured_at: string;
  is_realtime: boolean;
  strengthened_count: number;
  holding_count: number;
  weakened_count: number;
  stocks: Array<StockRow & { status: string; status_label: string; change_delta_pct: number | null }>;
}

interface MiddayReport {
  meta: {
    generated_at?: string;
    data_date?: string;
    phase?: string;
    phase_label?: string;
    is_realtime?: boolean;
    source?: string;
    stock_count?: number;
  };
  conclusion: {
    market_state?: string;
    market_health?: number | null;
    attack_intensity?: number | null;
    risk_level?: number | null;
    risk_label?: string;
    principal_conflict?: string;
    conflict_status?: string;
    action?: string;
    one_line?: string;
  };
  morning_autopsy: {
    truth_label?: string;
    one_line?: string;
    metrics?: Record<string, number | string | null>;
    answers?: Array<{ question: string; answer: string; nature: string }>;
  };
  principal_conflict: {
    current_statement?: string;
    previous_statement?: string | null;
    baseline_type?: string;
    status?: string;
    status_label?: string;
    dominant_aspect?: string;
    evidence?: string[];
    validation?: string;
  };
  sector_structures: SectorStructure[];
  stock_anomalies: Record<AnomalyKey, StockRow[]> & { counts: Record<AnomalyKey, number> };
  fund_behaviour: {
    patterns: Array<{ state: string; label: string; count: number }>;
    notable: StockRow[];
    method?: string;
  };
  afternoon_scenarios: Scenario[];
  tail_preview: {
    strategy_id?: string;
    strategy_name?: string;
    strategy_version?: string;
    preview_only?: boolean;
    scanned_count?: number;
    prefiltered_count?: number;
    candidate_count?: number;
    high_quality_count?: number;
    waiting_confirmation_count?: number;
    candidates?: PreviewStock[];
    boundary?: string;
    rules?: Record<string, unknown>;
  };
  tracking: { checkpoints?: Checkpoint[]; latest?: Checkpoint; policy?: string };
  validation: {
    completed?: boolean;
    status?: string;
    message?: string;
    validated_at?: string;
    predicted_scenario?: string;
    actual_scenario?: string;
    close_metrics?: Record<string, number | null>;
    preview_hit_rate_pct?: number | null;
    candidate_hits?: string[];
  };
  research_chain: ResearchChainItem[];
  data_quality: {
    completeness_pct?: number | null;
    complete_market_snapshot?: boolean;
    position_coverage_pct?: number | null;
    same_time_midday_baseline?: boolean;
    missing_fields?: string[];
    missing_policy?: string;
  };
  agent_runs?: Array<{ agent: string; status: string; output: string }>;
  ai_synthesis?: { available?: boolean; status?: string; narrative?: string | null; guard?: string };
}

interface MiddaySession extends ResearchSessionSummary {
  report?: MiddayReport;
  hypotheses?: Array<{
    id: number;
    title: string;
    statement: string;
    status: string;
    actual_result?: string | null;
  }>;
}

function isRecord(value: unknown): value is Record<string, any> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function arrayValue<T>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

function normalizeMiddayReport(value: unknown): MiddayReport {
  const source = isRecord(value) ? value : {};
  const anomalies = isRecord(source.stock_anomalies) ? source.stock_anomalies : {};
  const quality = isRecord(source.data_quality) ? source.data_quality : {};
  const validation = isRecord(source.validation) ? source.validation : {};
  const preview = isRecord(source.tail_preview) ? source.tail_preview : {};
  const tracking = isRecord(source.tracking) ? source.tracking : {};
  const fund = isRecord(source.fund_behaviour) ? source.fund_behaviour : {};
  const ai = isRecord(source.ai_synthesis) ? source.ai_synthesis : {};

  return {
    meta: isRecord(source.meta) ? source.meta as MiddayReport['meta'] : {},
    conclusion: isRecord(source.conclusion) ? source.conclusion as MiddayReport['conclusion'] : {},
    morning_autopsy: isRecord(source.morning_autopsy) ? source.morning_autopsy as MiddayReport['morning_autopsy'] : {},
    principal_conflict: isRecord(source.principal_conflict) ? source.principal_conflict as MiddayReport['principal_conflict'] : {},
    sector_structures: arrayValue<SectorStructure>(source.sector_structures),
    stock_anomalies: {
      contrarian_strength: arrayValue<StockRow>(anomalies.contrarian_strength),
      alpha_strengthening: arrayValue<StockRow>(anomalies.alpha_strengthening),
      beta_weak: arrayValue<StockRow>(anomalies.beta_weak),
      high_position_negative_feedback: arrayValue<StockRow>(anomalies.high_position_negative_feedback),
      counts: isRecord(anomalies.counts) ? anomalies.counts as Record<AnomalyKey, number> : {
        contrarian_strength: 0,
        alpha_strengthening: 0,
        beta_weak: 0,
        high_position_negative_feedback: 0,
      },
    },
    fund_behaviour: {
      patterns: arrayValue<FundBehaviour & { count: number }>(fund.patterns) as Array<{ state: string; label: string; count: number }>,
      notable: arrayValue<StockRow>(fund.notable),
      method: typeof fund.method === 'string' ? fund.method : undefined,
    },
    afternoon_scenarios: arrayValue<Scenario>(source.afternoon_scenarios),
    tail_preview: {
      ...preview,
      candidates: arrayValue<PreviewStock>(preview.candidates),
    } as MiddayReport['tail_preview'],
    tracking: {
      ...tracking,
      checkpoints: arrayValue<Checkpoint>(tracking.checkpoints),
    },
    validation: validation as MiddayReport['validation'],
    research_chain: arrayValue<ResearchChainItem>(source.research_chain),
    data_quality: {
      ...quality,
      missing_fields: arrayValue<string>(quality.missing_fields),
    },
    agent_runs: arrayValue<{ agent: string; status: string; output: string }>(source.agent_runs),
    ai_synthesis: {
      ...ai,
      narrative: typeof ai.narrative === 'string' ? ai.narrative : null,
    },
  };
}

function normalizeMiddaySession(value: unknown): MiddaySession | null {
  if (!isRecord(value) || typeof value.id !== 'string') return null;
  const report = isRecord(value.report) && Object.keys(value.report).length
    ? normalizeMiddayReport(value.report)
    : undefined;
  return {
    ...(value as MiddaySession),
    mode: typeof value.mode === 'string' ? value.mode : 'midday',
    status: typeof value.status === 'string' ? value.status : 'DRAFT',
    stage: typeof value.stage === 'string' ? value.stage : '等待午间研究任务',
    progress: finite(value.progress) ? value.progress : 0,
    source_data_date: typeof value.source_data_date === 'string' ? value.source_data_date : null,
    created_at: typeof value.created_at === 'string' ? value.created_at : null,
    completed_at: typeof value.completed_at === 'string' ? value.completed_at : null,
    error: typeof value.error === 'string' ? value.error : null,
    report,
    hypotheses: arrayValue<MiddaySession['hypotheses'] extends Array<infer T> ? T : never>(value.hypotheses),
  };
}

const STATUS_LABELS: Record<string, string> = {
  DRAFT: '等待运行', RUNNING: '研究中', COMPLETED: '研究完成', FAILED: '运行失败',
  CORRECT: '符合', PARTIAL: '部分符合', WRONG: '不符合', PENDING: '待盘后验证',
};

const ANOMALIES: Array<{ key: AnomalyKey; label: string; icon: typeof TrendingUp }> = [
  { key: 'contrarian_strength', label: '逆势强势', icon: TrendingUp },
  { key: 'alpha_strengthening', label: 'Alpha增强', icon: Sparkles },
  { key: 'beta_weak', label: '板块强个股弱', icon: TrendingDown },
  { key: 'high_position_negative_feedback', label: '高位负反馈', icon: AlertTriangle },
];

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function numberText(value: unknown, digits = 1): string {
  return finite(value) ? value.toFixed(digits) : '未观测';
}

function percent(value: unknown, digits = 1): string {
  return finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(digits)}%` : '未观测';
}

function plainPercent(value: unknown, digits = 1): string {
  return finite(value) ? `${value.toFixed(digits)}%` : '未观测';
}

function amount(value: unknown): string {
  if (!finite(value)) return '未观测';
  const yi = value / 100_000_000;
  return `${yi > 0 ? '+' : ''}${yi.toFixed(Math.abs(yi) >= 100 ? 0 : 1)}亿`;
}

function integer(value: unknown): string {
  return finite(value) ? Math.round(value).toLocaleString('zh-CN') : '未观测';
}

function dateTime(value: string | null | undefined): string {
  if (!value) return '未记录';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value.slice(0, 16).replace('T', ' ');
  return parsed.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function changeTone(value: unknown): string {
  if (!finite(value)) return 'text-text-secondary';
  return value > 0 ? 'text-up' : value < 0 ? 'text-down' : 'text-text-secondary';
}

function scoreTone(value: unknown): string {
  if (!finite(value)) return 'text-text-secondary';
  return value >= 70 ? 'text-up' : value >= 50 ? 'text-warn' : 'text-down';
}

function statusTone(status: string | undefined): string {
  if (status === 'FAILED' || status === 'WRONG' || status === 'WEAKENED') return 'border-down/50 text-down';
  if (status === 'CORRECT' || status === 'COMPLETED' || status === 'STRENGTHENED' || status === 'RESOLVED') return 'border-up/50 text-up';
  if (status === 'PARTIAL' || status === 'PENDING' || status === 'UNRESOLVED' || status === 'INTENSIFIED') return 'border-warn/50 text-warn';
  return 'border-border text-text-secondary';
}

function chainStatusLabel(status: string): string {
  return { available: '可用', completed: '已完成', pending: '待运行' }[status] || status;
}

export default function MiddayResearchPage() {
  const [session, setSession] = useState<MiddaySession | null>(null);
  const [sessions, setSessions] = useState<ResearchSessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [tracking, setTracking] = useState(false);
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<Tab>('overview');

  const loadSession = useCallback(async (id: string, showLoader = false) => {
    if (showLoader) setDetailLoading(true);
    try {
      const response = await apiFetch<{ data: MiddaySession }>(`/research/midday/${encodeURIComponent(id)}`);
      const normalized = normalizeMiddaySession(response.data);
      if (!normalized) throw new Error('午间研究记录为空或格式已失效');
      setSession(normalized);
      return normalized;
    } finally {
      if (showLoader) setDetailLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    const response = await apiFetch<{ data: { sessions: ResearchSessionSummary[] } }>('/research/midday?limit=40');
    setSessions(arrayValue<ResearchSessionSummary>(response.data?.sessions));
  }, []);

  const bootstrap = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [latest, history] = await Promise.all([
        apiFetch<{ data: MiddaySession | null }>('/research/midday/latest'),
        apiFetch<{ data: { sessions: ResearchSessionSummary[] } }>('/research/midday?limit=40'),
      ]);
      setSession(normalizeMiddaySession(latest.data));
      setSessions(arrayValue<ResearchSessionSummary>(history.data?.sessions));
    } catch (caught) {
      setError(friendlyApiError(caught, '午间研究台加载失败'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void bootstrap(); }, [bootstrap]);

  const running = Boolean(session && ['DRAFT', 'RUNNING'].includes(session.status));
  useEffect(() => {
    if (!session?.id || !running) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const next = await loadSession(session.id);
        if (!['DRAFT', 'RUNNING'].includes(next.status)) await loadHistory();
      } catch (caught) {
        setError(friendlyApiError(caught, '研究进度读取失败'));
      }
    }, 1600);
    return () => window.clearInterval(timer);
  }, [loadHistory, loadSession, running, session?.id]);

  const startResearch = async (force: boolean) => {
    setStarting(true);
    setError('');
    setTab('overview');
    try {
      const response = await apiFetch<{ data: MiddaySession }>('/research/midday/start', {
        method: 'POST',
        body: JSON.stringify({ force }),
        timeoutMs: 60000,
      });
      const normalized = normalizeMiddaySession(response.data);
      if (!normalized) throw new Error('午间研究任务返回为空');
      setSession(normalized);
      await loadHistory();
    } catch (caught) {
      setError(friendlyApiError(caught, '午间研究启动失败'));
    } finally {
      setStarting(false);
    }
  };

  const trackNow = async () => {
    if (!session?.id) return;
    setTracking(true);
    setError('');
    try {
      await apiFetch(`/research/midday/${encodeURIComponent(session.id)}/track`, {
        method: 'POST',
        body: JSON.stringify({ checkpoint: 'manual', force_quote: true }),
        timeoutMs: 60000,
      });
      await loadSession(session.id);
    } catch (caught) {
      setError(friendlyApiError(caught, '午后跟踪失败'));
    } finally {
      setTracking(false);
    }
  };

  const validateNow = async () => {
    setValidating(true);
    setError('');
    try {
      await apiFetch('/research/midday/validate', { method: 'POST', body: '{}' });
      if (session?.id) await loadSession(session.id);
      await loadHistory();
    } catch (caught) {
      setError(friendlyApiError(caught, '盘后验证失败'));
    } finally {
      setValidating(false);
    }
  };

  const report = session?.report;
  const anomalyCount = useMemo(() => Object.values(report?.stock_anomalies?.counts || {}).reduce((sum, value) => sum + Number(value || 0), 0), [report]);

  if (loading) {
    return <div className="grid min-h-[72vh] place-items-center"><div className="text-center"><Loader2 size={28} className="mx-auto animate-spin text-accent" /><div className="mt-3 text-xs text-text-secondary">正在读取午间研究档案</div></div></div>;
  }

  return (
    <main className="mx-auto w-full max-w-[1440px] px-3 py-4 sm:px-4 md:py-6">
      <header className="flex flex-col gap-4 border-b border-border pb-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-xl font-semibold text-text md:text-2xl"><ScanSearch size={23} className="text-accent" />午间 AI 研究台</h1>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-secondary">
            <span>数据日 {session?.source_data_date || report?.meta?.data_date || '未生成'}</span>
            <span>{report?.meta?.phase_label || '午间战术研究'}</span>
            <span>{report?.meta?.is_realtime ? '实时快照' : report ? '历史/缓存快照' : '等待首轮研究'}</span>
            {session && <span>{STATUS_LABELS[session.status] || session.status}</span>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link href="/research" className="command-button"><Microscope size={14} />周末战略研究</Link>
          <Link href="/quant" className="command-button"><LineChart size={14} />14:55执行筛选</Link>
          {report && !running && <button type="button" onClick={() => void startResearch(true)} disabled={starting} className="icon-button" title="重新生成午间研究" aria-label="重新生成午间研究"><RefreshCw size={15} className={starting ? 'animate-spin' : ''} /></button>}
          <button type="button" onClick={() => void startResearch(false)} disabled={starting || running} className="command-button-primary"><BrainCircuit size={14} />{starting ? '正在发起' : running ? '研究运行中' : report ? '读取今日研究' : '开始午间研究'}</button>
        </div>
      </header>

      {error && <div className="my-4 flex items-start gap-2 border border-down/40 bg-down/5 px-3 py-2.5 text-xs text-down"><AlertTriangle size={14} className="mt-0.5 shrink-0" /><span>{error}</span></div>}
      {running && session && <Progress session={session} />}
      {session?.status === 'FAILED' && <div className="my-4 border border-down/40 bg-down/5 p-4 text-xs text-down">{session.error || '午间研究运行失败，可重新发起。'}</div>}

      <div className="-mx-3 overflow-x-auto border-b border-border px-3 sm:mx-0 sm:px-0">
        <div className="flex min-w-max">
          <TabButton active={tab === 'overview'} onClick={() => setTab('overview')} icon={<BrainCircuit size={14} />} label="上午研判" />
          <TabButton active={tab === 'sectors'} onClick={() => setTab('sectors')} icon={<BarChart3 size={14} />} label={`板块结构 ${report?.sector_structures?.length || 0}`} />
          <TabButton active={tab === 'stocks'} onClick={() => setTab('stocks')} icon={<Activity size={14} />} label={`个股异常 ${anomalyCount}`} />
          <TabButton active={tab === 'preview'} onClick={() => setTab('preview')} icon={<Target size={14} />} label={`14:55预演 ${report?.tail_preview?.candidate_count || 0}`} />
          <TabButton active={tab === 'history'} onClick={() => setTab('history')} icon={<History size={14} />} label={`历史研究 ${sessions.length}`} />
        </div>
      </div>

      {detailLoading ? <div className="py-24 text-center"><Loader2 size={24} className="mx-auto animate-spin text-accent" /></div> : !report && !running && tab !== 'history' ? <EmptyState onStart={() => void startResearch(false)} /> : null}
      {report && tab === 'overview' && <Overview report={report} onTrack={trackNow} tracking={tracking} onValidate={validateNow} validating={validating} />}
      {report && tab === 'sectors' && <SectorView sectors={report.sector_structures || []} />}
      {report && tab === 'stocks' && <StockView anomalies={report.stock_anomalies} fund={report.fund_behaviour} />}
      {report && tab === 'preview' && <PreviewView preview={report.tail_preview} tracking={report.tracking} validation={report.validation} />}
      {tab === 'history' && <HistoryView sessions={sessions} activeId={session?.id} onSelect={async (id) => {
        setError('');
        try {
          await loadSession(id, true);
          setTab('overview');
        } catch (caught) {
          setError(friendlyApiError(caught, '历史午间研究读取失败'));
        }
      }} />}

      <style jsx global>{`
        .command-button,.command-button-primary{display:inline-flex;min-height:34px;align-items:center;justify-content:center;gap:6px;border-radius:5px;padding:7px 11px;font-size:12px;transition:color .15s,background .15s,border-color .15s}.command-button{border:1px solid #30363D;color:#C9D1D9;background:#161B22}.command-button:hover{border-color:#58A6FF;color:#58A6FF}.command-button-primary{border:1px solid #1F6FEB;color:#fff;background:#1F6FEB}.command-button:disabled,.command-button-primary:disabled{cursor:not-allowed;opacity:.5}.icon-button{display:grid;width:34px;height:34px;place-items:center;border:1px solid #30363D;border-radius:5px;color:#8B949E}.icon-button:hover{border-color:#58A6FF;color:#58A6FF}
        .midday-markdown p{margin-top:8px}.midday-markdown p:first-child{margin-top:0}.midday-markdown strong{color:#C9D1D9;font-weight:600}.midday-markdown ul{margin:6px 0 0 18px;list-style:disc}.midday-markdown li{margin-top:3px}
      `}</style>
    </main>
  );
}

function Progress({ session }: { session: MiddaySession }) {
  return <section className="my-4 border border-accent/40 bg-accent/5 p-4"><div className="flex items-center justify-between gap-3"><div className="flex min-w-0 items-center gap-2 text-sm text-text"><Loader2 size={15} className="shrink-0 animate-spin text-accent" /><span className="truncate">{session.stage}</span></div><span className="shrink-0 font-mono text-xs text-accent">{session.progress}%</span></div><div className="mt-3 h-1.5 overflow-hidden rounded bg-[#21262D]"><div className="h-full bg-accent transition-[width] duration-500" style={{ width: `${Math.max(2, session.progress || 0)}%` }} /></div></section>;
}

function EmptyState({ onStart }: { onStart: () => void }) {
  return <div className="grid min-h-[52vh] place-items-center border-b border-border"><div className="max-w-md px-4 text-center"><ScanSearch size={30} className="mx-auto text-text-secondary" /><h2 className="mt-4 text-base font-semibold text-text">尚无午间研究</h2><p className="mt-2 text-xs leading-5 text-text-secondary">交易日 11:42 自动生成；非交易时段可读取最近完整快照建立历史研究。</p><button type="button" onClick={onStart} className="command-button-primary mt-5"><BrainCircuit size={14} />开始研究</button></div></div>;
}

function TabButton({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: ReactNode; label: string }) {
  return <button type="button" onClick={onClick} className={`inline-flex h-11 items-center gap-1.5 border-b-2 px-4 text-xs ${active ? 'border-accent text-accent' : 'border-transparent text-text-secondary hover:text-text'}`}>{icon}{label}</button>;
}

function Overview({ report, onTrack, tracking, onValidate, validating }: { report: MiddayReport; onTrack: () => void; tracking: boolean; onValidate: () => void; validating: boolean }) {
  const conclusion = report.conclusion || {};
  const autopsy = report.morning_autopsy || {};
  const metrics = autopsy.metrics || {};
  const conflict = report.principal_conflict || {};
  const checkpoints = report.tracking?.checkpoints || [];
  return <div className="space-y-6 py-5">
    <ResearchChain items={report.research_chain || []} />

    <section className="overflow-hidden rounded-md border border-border bg-card">
      <div className="grid grid-cols-2 divide-x divide-y divide-border sm:grid-cols-4 sm:divide-y-0">
        <Metric label="市场状态" value={conclusion.market_state || '未观测'} detail={autopsy.truth_label} />
        <Metric label="结构健康度" value={numberText(conclusion.market_health, 0)} detail="0-100" tone={scoreTone(conclusion.market_health)} />
        <Metric label="进攻强度" value={numberText(conclusion.attack_intensity, 0)} detail="规则评分" tone={scoreTone(conclusion.attack_intensity)} />
        <Metric label="风险" value={conclusion.risk_label || '未观测'} detail={finite(conclusion.risk_level) ? `${conclusion.risk_level.toFixed(0)}/100` : undefined} tone={conclusion.risk_label === '高' ? 'text-down' : conclusion.risk_label === '中' ? 'text-warn' : 'text-up'} />
      </div>
      <div className="border-t border-border px-4 py-3 text-xs leading-5 text-text">{conclusion.one_line || autopsy.one_line || '上午结构尚未形成结论'}</div>
    </section>

    <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,.65fr)]">
      <div className="rounded-md border border-border bg-card">
        <SectionHeader icon={<FlaskConical size={15} />} title="上午市场尸检" meta={`${report.meta.stock_count || 0}只`} />
        <div className="grid grid-cols-2 border-b border-border sm:grid-cols-4">
          <SmallMetric label="成交额" value={amount(metrics.market_amount)} detail={finite(metrics.amount_change_pct) ? `同口径 ${percent(metrics.amount_change_pct)}` : String(metrics.comparison_baseline || '')} />
          <SmallMetric label="上涨 / 下跌" value={`${integer(metrics.up_count)} / ${integer(metrics.down_count)}`} detail={`宽度 ${plainPercent(metrics.breadth_pct)}`} />
          <SmallMetric label="涨停 / 跌停" value={`${integer(metrics.limit_up_count)} / ${integer(metrics.limit_down_count)}`} detail="按板块涨跌幅规则" />
          <SmallMetric label="市场中位数" value={percent(metrics.market_median_pct, 2)} detail={`上证 ${percent(metrics.index_change_pct, 2)}`} tone={changeTone(metrics.market_median_pct)} />
          <SmallMetric label="高位 / 低位" value={`${percent(metrics.high_position_avg_pct, 2)} / ${percent(metrics.low_position_avg_pct, 2)}`} detail={`${integer(metrics.high_position_count)} / ${integer(metrics.low_position_count)}只`} />
          <SmallMetric label="大盘 / 小盘" value={`${percent(metrics.large_cap_avg_pct, 2)} / ${percent(metrics.small_cap_avg_pct, 2)}`} detail="市值四分位" />
          <SmallMetric label="量能支持" value={String(metrics.volume_support || '未观测')} detail={String(metrics.comparison_baseline || '')} />
          <SmallMetric label="快照" value={report.meta.is_realtime ? '实时' : '历史/缓存'} detail={report.meta.data_date} />
        </div>
        <div className="divide-y divide-border/70 px-4">
          {(autopsy.answers || []).map((item) => <div key={item.question} className="grid gap-1 py-3 text-xs sm:grid-cols-[210px_1fr]"><div className="text-text-secondary">{item.question}</div><div className="text-text">{item.answer}</div></div>)}
        </div>
      </div>

      <div className="rounded-md border border-border bg-card">
        <SectionHeader icon={<Target size={15} />} title="今日主要矛盾" meta={conflict.status_label} />
        <div className="p-4">
          <p className="text-sm font-medium leading-6 text-text">{conflict.current_statement || '尚未形成稳定结论'}</p>
          <div className="mt-3 inline-flex rounded border border-border px-2 py-1 text-[10px] text-text-secondary">{conflict.baseline_type || '当前工作台'}：{conflict.previous_statement || '首轮基线'}</div>
          {conflict.dominant_aspect && <div className="mt-4 border-l-2 border-accent pl-3 text-xs leading-5 text-text-secondary"><span className="text-text">主要方面：</span>{conflict.dominant_aspect}</div>}
          <EvidenceList values={conflict.evidence || []} />
          {conflict.validation && <div className="mt-4 border-t border-border pt-3 text-[11px] leading-5 text-warn">{conflict.validation}</div>}
        </div>
      </div>
    </section>

    <section>
      <SectionTitle icon={<BrainCircuit size={16} />} title="下午情景" meta="规则支持度，不是统计胜率" />
      <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-3">
        {(report.afternoon_scenarios || []).map((scenario) => <ScenarioCard key={scenario.key} scenario={scenario} />)}
      </div>
    </section>

    {report.ai_synthesis?.available && report.ai_synthesis.narrative && <section className="rounded-md border border-accent/30 bg-accent/5 p-4"><div className="flex items-center gap-2 text-sm font-medium text-text"><Sparkles size={15} className="text-accent" />AI 战术解读</div><div className="midday-markdown mt-3 text-xs leading-6 text-text-secondary"><ReactMarkdown remarkPlugins={[remarkGfm]}>{report.ai_synthesis.narrative}</ReactMarkdown></div><div className="mt-3 text-[10px] text-text-secondary">{report.ai_synthesis.guard}</div></section>}

    <section className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <div className="rounded-md border border-border bg-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3"><div className="flex items-center gap-2 text-sm font-semibold text-text"><Clock3 size={15} className="text-accent" />午后固定样本跟踪</div><button type="button" onClick={onTrack} disabled={tracking} className="command-button"><RefreshCw size={13} className={tracking ? 'animate-spin' : ''} />{tracking ? '跟踪中' : '立即跟踪'}</button></div>
        {checkpoints.length ? <div className="divide-y divide-border">{checkpoints.map((item) => <div key={`${item.checkpoint}-${item.captured_at}`} className="grid grid-cols-[70px_1fr] gap-3 px-4 py-3 text-xs"><div className="font-mono text-text">{item.checkpoint}</div><div className="flex flex-wrap gap-x-4 gap-y-1 text-text-secondary"><span className="text-up">增强 {item.strengthened_count}</span><span>保持 {item.holding_count}</span><span className="text-down">失效 {item.weakened_count}</span><span>{item.is_realtime ? '实时' : '缓存'}</span></div></div>)}</div> : <div className="px-4 py-10 text-center text-xs text-text-secondary">尚无午后跟踪点</div>}
      </div>
      <ValidationPanel validation={report.validation} onValidate={onValidate} validating={validating} />
    </section>

    <DataAudit quality={report.data_quality} agents={report.agent_runs || []} />
  </div>;
}

function ResearchChain({ items }: { items: ResearchChainItem[] }) {
  return <section><SectionTitle icon={<BookOpenCheck size={16} />} title="四层研究链" meta="战略 → 战术 → 执行 → 验证" /><div className="mt-3 overflow-x-auto"><div className="grid min-w-[920px] grid-cols-5 overflow-hidden rounded-md border border-border bg-card">{items.map((item, index) => <Link key={item.key} href={item.href} className="group relative min-h-[118px] border-r border-border p-4 last:border-r-0 hover:bg-[#1F6FEB0A]"><div className="flex items-center justify-between gap-2"><span className="font-mono text-[10px] text-text-secondary">{item.time}</span><span className={`rounded border px-1.5 py-0.5 text-[10px] ${statusTone(item.status)}`}>{chainStatusLabel(item.status)}</span></div><div className="mt-3 text-sm font-semibold text-text">{item.label}</div><div className="mt-2 text-[11px] leading-5 text-text-secondary">{item.question}</div>{index < items.length - 1 && <ChevronRight size={14} className="absolute -right-2 top-1/2 z-10 -translate-y-1/2 rounded-full bg-card text-text-secondary" />}</Link>)}</div></div></section>;
}

function ScenarioCard({ scenario }: { scenario: Scenario }) {
  const bar = scenario.key === 'ATTACK' ? 'bg-up' : scenario.key === 'PULLBACK' ? 'bg-down' : 'bg-warn';
  return <article className="rounded-md border border-border bg-card p-4"><div className="flex items-start justify-between gap-3"><div><h3 className="text-sm font-semibold text-text">{scenario.name}</h3><div className="mt-1 text-[10px] text-text-secondary">条件支持度</div></div><div className="font-mono text-xl font-semibold text-text">{numberText(scenario.support_pct, 1)}%</div></div><div className="mt-3 h-1.5 overflow-hidden rounded bg-[#21262D]"><div className={`h-full ${bar}`} style={{ width: `${scenario.support_pct}%` }} /></div><div className="mt-4 text-[10px] text-text-secondary">触发条件</div><EvidenceList values={scenario.conditions || []} compact /><div className="mt-4 border-t border-border pt-3 text-xs leading-5 text-text">{scenario.action}</div><div className="mt-3 text-[10px] leading-5 text-warn">证伪：{(scenario.falsification || []).join('；')}</div></article>;
}

function ValidationPanel({ validation, onValidate, validating }: { validation: MiddayReport['validation']; onValidate: () => void; validating: boolean }) {
  return <div className="rounded-md border border-border bg-card"><div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3"><div className="flex items-center gap-2 text-sm font-semibold text-text"><BookOpenCheck size={15} className="text-accent" />盘后验证与学习</div><button type="button" onClick={onValidate} disabled={validating} className="command-button"><CheckCircle2 size={13} />{validating ? '验证中' : '执行验证'}</button></div><div className="p-4">{validation.completed ? <><div className="flex flex-wrap items-center gap-2"><span className={`rounded border px-2 py-1 text-xs ${statusTone(validation.status)}`}>{STATUS_LABELS[validation.status || ''] || validation.status}</span><span className="text-xs text-text-secondary">预判 {validation.predicted_scenario || '--'} / 实际 {validation.actual_scenario || '--'}</span></div><p className="mt-3 text-xs leading-5 text-text">{validation.message}</p><div className="mt-4 grid grid-cols-2 gap-3 text-xs"><SmallFact label="预演命中率" value={plainPercent(validation.preview_hit_rate_pct)} /><SmallFact label="验证时间" value={dateTime(validation.validated_at)} /></div></> : <div className="py-5 text-center"><Clock3 size={20} className="mx-auto text-text-secondary" /><div className="mt-3 text-xs text-text-secondary">{validation.message || '等待收盘真实结果'}</div></div>}</div></div>;
}

function DataAudit({ quality, agents }: { quality: MiddayReport['data_quality']; agents: NonNullable<MiddayReport['agent_runs']> }) {
  return <section className="rounded-md border border-border bg-card"><SectionHeader icon={<Database size={15} />} title="数据与 Agent 审计" meta={`完整度 ${numberText(quality.completeness_pct, 0)}%`} /><div className="grid grid-cols-1 divide-y divide-border lg:grid-cols-[1fr_1.2fr] lg:divide-x lg:divide-y-0"><div className="p-4"><div className="grid grid-cols-2 gap-3 text-xs"><SmallFact label="全市场快照" value={quality.complete_market_snapshot ? '完整' : '未完整'} /><SmallFact label="20日位置覆盖" value={plainPercent(quality.position_coverage_pct)} /><SmallFact label="同口径基线" value={quality.same_time_midday_baseline ? '已建立' : '本次建立'} /><SmallFact label="缺失项" value={String(quality.missing_fields?.length || 0)} /></div>{Boolean(quality.missing_fields?.length) && <div className="mt-4 border-l-2 border-warn pl-3 text-[11px] leading-5 text-warn">{quality.missing_fields?.join('；')}</div>}<p className="mt-4 text-[10px] leading-5 text-text-secondary">{quality.missing_policy}</p></div><div className="divide-y divide-border/70">{agents.map((item) => <div key={item.agent} className="grid grid-cols-[150px_82px_1fr] gap-2 px-4 py-2.5 text-[11px]"><span className="truncate font-mono text-text">{item.agent}</span><span className={item.status === 'completed' ? 'text-up' : 'text-warn'}>{item.status === 'completed' ? '已完成' : '待验证'}</span><span className="text-text-secondary">{item.output}</span></div>)}</div></div></section>;
}

function SectorView({ sectors }: { sectors: SectorStructure[] }) {
  if (!sectors.length) return <NoRows text="当前快照没有形成可核验板块结构" />;
  return <div className="py-5">
    <div className="mb-4"><SectionTitle icon={<BarChart3 size={16} />} title="板块内部结构" meta={`${sectors.length}个板块`} /></div>
    <div className="space-y-3 md:hidden">{sectors.map((sector) => <SectorCard key={sector.name} sector={sector} />)}</div>
    <div className="hidden overflow-x-auto rounded-md border border-border md:block">
      <table className="w-full min-w-[1120px] text-xs">
        <thead className="border-b border-border bg-card text-text-secondary"><tr><th className="px-4 py-3 text-left">板块</th><th className="px-3 text-right">结构分</th><th className="px-3 text-right">涨幅 / 中位数</th><th className="px-3 text-right">上涨宽度</th><th className="px-3 text-right">先锋差距</th><th className="px-3 text-right">资金</th><th className="px-3 text-left">内部角色</th><th className="px-4 text-left">风险</th></tr></thead>
        <tbody>{sectors.map((sector) => {
          const flowRank = sector.flow_rank && sector.flow_rank <= 10 ? `第${sector.flow_rank}` : '未进前十';
          return <tr key={sector.name} className="border-b border-border/70 align-top last:border-b-0"><td className="px-4 py-3"><div className="font-medium text-text">{sector.name}</div><div className="mt-1 text-[10px] text-text-secondary">{sector.status} · {sector.member_count}只</div></td><td className={`px-3 py-3 text-right font-mono ${scoreTone(sector.structure_score)}`}>{numberText(sector.structure_score, 0)}</td><td className="px-3 py-3 text-right font-mono"><div className={changeTone(sector.average_change_pct)}>{percent(sector.average_change_pct, 2)}</div><div className="mt-1 text-text-secondary">{percent(sector.median_change_pct, 2)}</div></td><td className="px-3 py-3 text-right font-mono text-text"><div>{plainPercent(sector.breadth_pct)}</div><div className="mt-1 text-text-secondary">{sector.up_count}/{sector.member_count}</div></td><td className="px-3 py-3 text-right font-mono"><div className={finite(sector.leader_gap_pct) && sector.leader_gap_pct >= 6 ? 'text-warn' : 'text-text'}>{percent(sector.leader_gap_pct, 2)}</div><div className="mt-1 text-text-secondary">后排 {percent(sector.rear_average_pct, 2)}</div></td><td className="px-3 py-3 text-right font-mono"><div className={changeTone(sector.main_net_inflow)}>{amount(sector.main_net_inflow)}</div><div className="mt-1 max-w-[150px] text-text-secondary">{sector.flow_source || '资金来源未标注'} · {flowRank}</div></td><td className="px-3 py-3"><RoleList roles={sector.roles} /></td><td className="max-w-[250px] px-4 py-3 text-[11px] leading-5 text-warn">{sector.risk_flags?.length ? sector.risk_flags.join('；') : '未发现集中性风险'}</td></tr>;
        })}</tbody>
      </table>
    </div>
  </div>;
}

function SectorCard({ sector }: { sector: SectorStructure }) {
  const flowRank = sector.flow_rank && sector.flow_rank <= 10 ? `第${sector.flow_rank}` : '未进前十';
  return <article className="rounded-md border border-border bg-card p-4"><div className="flex items-start justify-between gap-3"><div><h3 className="text-sm font-semibold text-text">{sector.name}</h3><div className="mt-1 text-[11px] text-text-secondary">{sector.status} · {sector.up_count}/{sector.member_count}上涨</div></div><span className={`font-mono text-lg ${scoreTone(sector.structure_score)}`}>{numberText(sector.structure_score, 0)}</span></div><div className="mt-4 grid grid-cols-3 overflow-hidden rounded border border-border"><SmallMetric label="均值" value={percent(sector.average_change_pct, 2)} tone={changeTone(sector.average_change_pct)} /><SmallMetric label="宽度" value={plainPercent(sector.breadth_pct)} /><SmallMetric label="资金" value={amount(sector.main_net_inflow)} tone={changeTone(sector.main_net_inflow)} /></div><div className="mt-2 text-[10px] text-text-secondary">{sector.flow_source || '资金来源未标注'} · {flowRank}</div><div className="mt-4"><RoleList roles={sector.roles} /></div>{Boolean(sector.risk_flags?.length) && <div className="mt-4 text-[11px] leading-5 text-warn">{sector.risk_flags.join('；')}</div>}</article>;
}

function RoleList({ roles }: { roles: SectorRole[] }) {
  return <div className="space-y-1.5">{(roles || []).map((role) => <div key={`${role.role}-${role.code}`} className="flex items-center justify-between gap-2"><span className="min-w-0 truncate text-text-secondary"><span className="mr-1 text-[10px] text-text-secondary">{role.role}</span><StockKlineButton code={role.code} name={role.name} className="text-xs text-text">{role.name}</StockKlineButton></span><span className={`shrink-0 font-mono text-[11px] ${changeTone(role.change_pct)}`}>{percent(role.change_pct, 2)}</span></div>)}</div>;
}

function StockView({ anomalies, fund }: { anomalies: MiddayReport['stock_anomalies']; fund: MiddayReport['fund_behaviour'] }) {
  const [active, setActive] = useState<AnomalyKey>('contrarian_strength');
  const rows = anomalies?.[active] || [];
  return <div className="space-y-7 py-5"><section><SectionTitle icon={<Activity size={16} />} title="个股强弱归因" meta="Alpha 与 Beta 分离" /><div className="mt-3 overflow-x-auto"><div className="inline-flex min-w-max overflow-hidden rounded-md border border-border bg-card">{ANOMALIES.map((item) => { const Icon = item.icon; return <button key={item.key} type="button" onClick={() => setActive(item.key)} className={`inline-flex h-10 items-center gap-1.5 border-r border-border px-3 text-xs last:border-r-0 ${active === item.key ? 'bg-accent/10 text-accent' : 'text-text-secondary hover:text-text'}`}><Icon size={13} />{item.label}<span className="font-mono">{anomalies?.counts?.[item.key] || 0}</span></button>; })}</div></div><div className="mt-3"><StockTable rows={rows} empty="该类别当前没有满足证据阈值的股票" source={`midday_${active}`} /></div></section><section><SectionTitle icon={<Activity size={16} />} title="上午资金行为" meta={fund?.method} /><div className="mt-3 flex flex-wrap gap-2">{(fund?.patterns || []).map((item) => <span key={item.state} className="rounded border border-border bg-card px-2.5 py-1.5 text-[11px] text-text-secondary">{item.label} <b className="ml-1 font-mono font-normal text-text">{item.count}</b></span>)}</div><div className="mt-3"><StockTable rows={fund?.notable || []} empty="暂无显著资金价格组合" source="midday_fund_behaviour" showBehaviour /></div></section></div>;
}

function StockTable({ rows, empty, source, showBehaviour = false }: { rows: StockRow[]; empty: string; source: string; showBehaviour?: boolean }) {
  if (!rows.length) return <NoRows text={empty} compact />;
  return <><div className="space-y-3 md:hidden">{rows.map((stock) => <StockCard key={`${source}-${stock.code}`} stock={stock} source={source} showBehaviour={showBehaviour} />)}</div><div className="hidden overflow-x-auto rounded-md border border-border md:block"><table className="w-full min-w-[1080px] text-xs"><thead className="border-b border-border bg-card text-text-secondary"><tr><th className="px-4 py-3 text-left">股票</th><th className="px-3 text-right">评分</th><th className="px-3 text-right">涨幅</th><th className="px-3 text-right">市场Alpha</th><th className="px-3 text-right">板块Alpha</th><th className="px-3 text-right">量比 / 换手</th><th className="px-3 text-right">资金</th><th className="px-3 text-left">判断</th><th className="px-4 text-right">个人池</th></tr></thead><tbody>{rows.map((stock) => <tr key={`${source}-${stock.code}`} className="border-b border-border/70 align-top last:border-b-0"><td className="px-4 py-3"><StockKlineButton code={stock.code} name={stock.name} className="font-medium text-text">{stock.name}</StockKlineButton><div className="mt-1 font-mono text-[10px] text-text-secondary">{stock.code} · {stock.sector}</div></td><td className={`px-3 py-3 text-right font-mono ${scoreTone(stock.score)}`}>{numberText(stock.score, 0)}</td><td className={`px-3 py-3 text-right font-mono ${changeTone(stock.change_pct)}`}>{percent(stock.change_pct, 2)}</td><td className={`px-3 py-3 text-right font-mono ${changeTone(stock.market_alpha_pct)}`}>{percent(stock.market_alpha_pct, 2)}</td><td className={`px-3 py-3 text-right font-mono ${changeTone(stock.sector_alpha_pct)}`}>{percent(stock.sector_alpha_pct, 2)}</td><td className="px-3 py-3 text-right font-mono text-text"><div>{numberText(stock.volume_ratio, 2)}</div><div className="mt-1 text-text-secondary">{plainPercent(stock.turnover, 2)}</div></td><td className={`px-3 py-3 text-right font-mono ${changeTone(stock.main_net_inflow)}`}>{amount(stock.main_net_inflow)}</td><td className="max-w-[300px] px-3 py-3 text-[11px] leading-5 text-text-secondary">{showBehaviour ? stock.fund_behaviour?.interpretation : stock.reason || stock.fund_behaviour?.interpretation || '结构异常已记录'}</td><td className="px-4 py-3 text-right"><AddToPersonalPoolButton code={stock.code} name={stock.name} industry={stock.sector} thesis={stock.reason || stock.fund_behaviour?.interpretation} source={source} compact /></td></tr>)}</tbody></table></div></>;
}

function StockCard({ stock, source, showBehaviour }: { stock: StockRow; source: string; showBehaviour: boolean }) {
  return <article className="rounded-md border border-border bg-card p-4"><div className="flex items-start justify-between gap-3"><div><StockKlineButton code={stock.code} name={stock.name} className="text-sm font-semibold text-text">{stock.name}</StockKlineButton><div className="mt-1 font-mono text-[10px] text-text-secondary">{stock.code} · {stock.sector}</div></div><div className={`font-mono text-sm ${changeTone(stock.change_pct)}`}>{percent(stock.change_pct, 2)}</div></div><div className="mt-4 grid grid-cols-3 overflow-hidden rounded border border-border"><SmallMetric label="评分" value={numberText(stock.score, 0)} tone={scoreTone(stock.score)} /><SmallMetric label="市场Alpha" value={percent(stock.market_alpha_pct, 2)} tone={changeTone(stock.market_alpha_pct)} /><SmallMetric label="量比" value={numberText(stock.volume_ratio, 2)} /></div><p className="mt-4 text-xs leading-5 text-text-secondary">{showBehaviour ? stock.fund_behaviour?.interpretation : stock.reason || stock.fund_behaviour?.interpretation}</p><div className="mt-4 flex justify-end"><AddToPersonalPoolButton code={stock.code} name={stock.name} industry={stock.sector} thesis={stock.reason || stock.fund_behaviour?.interpretation} source={source} compact /></div></article>;
}

function PreviewView({ preview, tracking, validation }: { preview: MiddayReport['tail_preview']; tracking: MiddayReport['tracking']; validation: MiddayReport['validation'] }) {
  const rows = preview.candidates || [];
  return <div className="space-y-6 py-5"><section className="rounded-md border border-border bg-card"><SectionHeader icon={<Target size={15} />} title="14:55策略预演" meta={preview.strategy_name} /><div className="grid grid-cols-2 divide-x divide-y divide-border sm:grid-cols-4 sm:divide-y-0"><Metric label="全市场扫描" value={integer(preview.scanned_count)} /><Metric label="静态预筛" value={integer(preview.prefiltered_count)} /><Metric label="高质量" value={integer(preview.high_quality_count)} tone="text-up" /><Metric label="等待确认" value={integer(preview.waiting_confirmation_count)} tone="text-warn" /></div>{preview.boundary && <div className="border-t border-border px-4 py-3 text-[11px] leading-5 text-warn">{preview.boundary}</div>}</section>{rows.length ? <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">{rows.map((stock) => <PreviewCard key={stock.code} stock={stock} />)}</div> : <NoRows text="本次午间快照没有形成14:55预演候选" />}{Boolean(tracking.checkpoints?.length) && <section><SectionTitle icon={<Clock3 size={16} />} title="候选强度轨迹" meta={`${tracking.checkpoints?.length || 0}个跟踪点`} /><div className="mt-3 overflow-x-auto rounded-md border border-border"><table className="w-full min-w-[820px] text-xs"><thead className="border-b border-border bg-card text-text-secondary"><tr><th className="px-4 py-3 text-left">时间</th><th className="px-3 text-right">增强</th><th className="px-3 text-right">保持</th><th className="px-3 text-right">失效</th><th className="px-4 text-left">状态</th></tr></thead><tbody>{tracking.checkpoints?.map((item) => <tr key={`${item.checkpoint}-${item.captured_at}`} className="border-b border-border/70 last:border-b-0"><td className="px-4 py-3 font-mono text-text">{item.checkpoint}</td><td className="px-3 text-right font-mono text-up">{item.strengthened_count}</td><td className="px-3 text-right font-mono text-text">{item.holding_count}</td><td className="px-3 text-right font-mono text-down">{item.weakened_count}</td><td className="px-4 text-text-secondary">{item.is_realtime ? '实时行情' : '缓存行情'} · {dateTime(item.captured_at)}</td></tr>)}</tbody></table></div></section>}<ValidationSummary validation={validation} /></div>;
}

function PreviewCard({ stock }: { stock: PreviewStock }) {
  return <article className="rounded-md border border-border bg-card p-4"><div className="flex items-start justify-between gap-3"><div><div className="flex flex-wrap items-center gap-2"><StockKlineButton code={stock.code} name={stock.name} className="text-sm font-semibold text-text">{stock.name}</StockKlineButton><span className="font-mono text-[10px] text-text-secondary">{stock.code}</span><span className={`rounded border px-1.5 py-0.5 text-[10px] ${stock.quality === '高质量' ? 'border-up/50 text-up' : 'border-warn/50 text-warn'}`}>{stock.quality}</span></div><div className="mt-1 text-[11px] text-text-secondary">{stock.sector} · {numberText(stock.score, 0)}分</div></div><div className={`font-mono text-sm ${changeTone(stock.change_pct)}`}>{percent(stock.change_pct, 2)}</div></div><div className="mt-4 grid grid-cols-3 overflow-hidden rounded border border-border"><SmallMetric label="量比" value={numberText(stock.volume_ratio, 2)} /><SmallMetric label="换手" value={plainPercent(stock.turnover, 2)} /><SmallMetric label="市值" value={finite(stock.market_cap_yi) ? `${stock.market_cap_yi.toFixed(1)}亿` : '未观测'} /></div><div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2"><ListBlock label="已通过" values={stock.passed_evidence || []} /><ListBlock label="14:55待确认" values={stock.pending_confirmation || []} warn /></div>{Boolean(stock.failed?.length || stock.unavailable?.length) && <div className="mt-4 text-[11px] leading-5 text-warn">未通过/未观测：{[...(stock.failed || []), ...(stock.unavailable || [])].join('、')}</div>}<div className="mt-4 flex justify-end"><AddToPersonalPoolButton code={stock.code} name={stock.name} industry={stock.sector} thesis={`午间14:55预演：${stock.passed_evidence?.join('、')}`} source="midday_tail_preview" compact /></div></article>;
}

function ValidationSummary({ validation }: { validation: MiddayReport['validation'] }) {
  if (!validation.completed) return null;
  return <section className="rounded-md border border-border bg-card p-4"><div className="flex flex-wrap items-center gap-2"><BookOpenCheck size={15} className="text-accent" /><h2 className="text-sm font-semibold text-text">盘后真实结果</h2><span className={`rounded border px-1.5 py-0.5 text-[10px] ${statusTone(validation.status)}`}>{STATUS_LABELS[validation.status || ''] || validation.status}</span></div><p className="mt-3 text-xs leading-5 text-text-secondary">{validation.message}</p></section>;
}

function HistoryView({ sessions, activeId, onSelect }: { sessions: ResearchSessionSummary[]; activeId?: string; onSelect: (id: string) => void }) {
  if (!sessions.length) return <NoRows text="尚无历史午间研究" />;
  return <div className="py-5"><SectionTitle icon={<History size={16} />} title="午间研究历史" meta={`${sessions.length}次`} /><div className="mt-3 divide-y divide-border border-y border-border">{sessions.map((item) => <button key={item.id} type="button" onClick={() => onSelect(item.id)} className={`grid w-full grid-cols-1 gap-2 px-2 py-4 text-left hover:bg-[#161B2255] sm:grid-cols-[140px_100px_1fr_120px_18px] sm:items-center sm:gap-3 ${activeId === item.id ? 'bg-accent/5' : ''}`}><div className="font-mono text-[11px] text-text-secondary">{dateTime(item.created_at)}</div><div className="text-xs text-text">{item.source_data_date || '未记录'}</div><div className="min-w-0"><div className="truncate text-xs text-text">{item.summary?.principal_conflict || '午间市场结构研究'}</div><div className="mt-1 text-[10px] text-text-secondary">{item.summary?.market_state || '--'} · 候选{item.summary?.candidate_count || 0}只 · {item.summary?.scenario || '情景未形成'}</div></div><div><span className={`rounded border px-1.5 py-0.5 text-[10px] ${statusTone(item.summary?.validation_status || item.status)}`}>{STATUS_LABELS[item.summary?.validation_status || item.status] || item.summary?.validation_status || item.status}</span></div><ChevronRight size={14} className="hidden text-text-secondary sm:block" /></button>)}</div></div>;
}

function SectionHeader({ icon, title, meta }: { icon: ReactNode; title: string; meta?: string | null }) {
  return <div className="flex min-h-11 items-center justify-between gap-3 border-b border-border px-4 py-3"><div className="flex items-center gap-2 text-sm font-semibold text-text"><span className="text-accent">{icon}</span>{title}</div>{meta && <span className="max-w-[58%] truncate text-right text-[10px] text-text-secondary">{meta}</span>}</div>;
}

function SectionTitle({ icon, title, meta }: { icon: ReactNode; title: string; meta?: string }) {
  return <div className="flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2 text-sm font-semibold text-text"><span className="text-accent">{icon}</span>{title}</div>{meta && <span className="text-[10px] text-text-secondary">{meta}</span>}</div>;
}

function Metric({ label, value, detail, tone = 'text-text' }: { label: string; value: string; detail?: string; tone?: string }) {
  return <div className="min-h-[94px] p-4"><div className="text-[10px] text-text-secondary">{label}</div><div className={`mt-2 break-words text-lg font-semibold ${tone}`}>{value}</div>{detail && <div className="mt-1 line-clamp-2 text-[10px] leading-4 text-text-secondary">{detail}</div>}</div>;
}

function SmallMetric({ label, value, detail, tone = 'text-text' }: { label: string; value: string; detail?: string; tone?: string }) {
  return <div className="min-h-[74px] border-b border-r border-border p-3 last:border-r-0"><div className="text-[10px] text-text-secondary">{label}</div><div className={`mt-1 break-words font-mono text-xs ${tone}`}>{value}</div>{detail && <div className="mt-1 line-clamp-2 text-[9px] leading-4 text-text-secondary">{detail}</div>}</div>;
}

function SmallFact({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0"><div className="text-[10px] text-text-secondary">{label}</div><div className="mt-1 break-words text-xs text-text">{value}</div></div>;
}

function EvidenceList({ values, compact = false }: { values: string[]; compact?: boolean }) {
  if (!values.length) return null;
  return <div className={compact ? 'mt-2 space-y-1.5' : 'mt-4 space-y-2'}>{values.map((value, index) => <div key={`${value}-${index}`} className="flex items-start gap-2 text-[11px] leading-5 text-text-secondary"><CircleDot size={9} className="mt-1.5 shrink-0 text-accent" /><span>{value}</span></div>)}</div>;
}

function ListBlock({ label, values, warn = false }: { label: string; values: string[]; warn?: boolean }) {
  return <div><div className="text-[10px] text-text-secondary">{label}</div><div className={`mt-2 space-y-1.5 text-[11px] leading-5 ${warn ? 'text-warn' : 'text-text-secondary'}`}>{values.length ? values.map((value, index) => <div key={`${value}-${index}`} className="flex items-start gap-2"><ArrowRight size={10} className="mt-1.5 shrink-0" /><span>{value}</span></div>) : <div>无</div>}</div></div>;
}

function NoRows({ text, compact = false }: { text: string; compact?: boolean }) {
  return <div className={`${compact ? 'py-12' : 'py-24'} text-center`}><ShieldAlert size={22} className="mx-auto text-text-secondary" /><div className="mt-3 text-xs text-text-secondary">{text}</div></div>;
}
