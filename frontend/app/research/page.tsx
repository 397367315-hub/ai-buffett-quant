'use client';

import {
  AlertTriangle,
  Archive,
  BarChart3,
  BookOpenCheck,
  BrainCircuit,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  FileSearch,
  FlaskConical,
  History,
  Loader2,
  Microscope,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  X,
} from 'lucide-react';
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import AddToPersonalPoolButton from '@/components/AddToPersonalPoolButton';
import StockKlineButton from '@/components/StockKlineButton';
import { apiFetch, friendlyApiError } from '@/lib/api';

type Tab = 'conclusion' | 'candidates' | 'evidence' | 'history';
type ReviewAction = 'APPROVE' | 'MODIFY' | 'REJECT';

interface ResearchSessionSummary {
  id: string;
  mode: string;
  topic: string | null;
  status: string;
  stage: string;
  progress: number;
  source_data_date: string | null;
  created_at: string | null;
  completed_at: string | null;
  summary?: {
    market_state?: string;
    principal_conflict?: string;
    action?: string;
    candidate_count?: number;
    sector_count?: number;
    data_completeness_pct?: number;
  };
}

interface MarketAnswer {
  question: string;
  answer: string;
  nature: string;
}

interface SectorResearch {
  rank?: number;
  name: string;
  classification?: string;
  lifecycle?: string;
  direction?: string;
  strength_score?: number | null;
  breadth?: number | null;
  change_pct?: number | null;
  main_net_inflow?: number | null;
  roles?: Array<{ role?: string; code?: string; name?: string; score?: number | null }>;
  evidence?: string[];
  risk_flags?: string[];
  why_core?: string;
}

interface StockCandidate {
  code: string;
  name: string;
  sector: string;
  research_class?: string;
  research_class_label?: string;
  research_status?: string;
  decision_label?: string;
  score?: number | null;
  company?: {
    main_business?: string | null;
    current_price?: number | null;
    total_market_cap?: number | null;
  };
  earnings?: {
    quality?: string | null;
    state?: string | null;
    sustainability?: string | null;
    metrics?: Record<string, number | null>;
  };
  valuation?: {
    state?: string | null;
    pe_ttm?: number | null;
    pe_percentile_3y?: number | null;
    cycle_phase?: string | null;
    pe_inversion_risk?: boolean;
  };
  alpha?: { score?: number | null; windows?: Array<{ days?: number; alpha_pct?: number | null }> };
  sector_role?: { role?: string | null };
  sector_dependency?: { dependency_level?: string | null; independence_level?: string | null };
  expectation_gap?: { state?: string | null; expectation_gap_proxy_pct?: number | null };
  emotion?: { level?: string | null; trend?: string | null };
  risk_reward?: {
    risk_reward_ratio?: number | null;
    potential_upside_pct?: number | null;
    potential_downside_pct?: number | null;
    valuation_risk?: string | null;
  };
  strategy_fit?: { long_term?: { fit?: string }; trend?: { fit?: string }; tail_1455?: { fit?: string } };
  why_research?: string[];
  main_advantage?: string;
  main_risk?: string;
  trigger_conditions?: string[];
  invalidation_conditions?: string[];
  evidence_chain?: Array<{ nature?: string; category?: string; statement?: string; source?: string; data_date?: string }>;
  data_completeness_pct?: number | null;
  confidence?: string;
  source_data_date?: string | null;
}

interface Scenario {
  key: string;
  name: string;
  support?: string;
  conditions?: string[];
  action?: string;
  invalidation?: string[];
  nature?: string;
}

interface ResearchReport {
  meta?: { mode?: string; topic?: string | null; generated_at?: string; source_data_date?: string; is_realtime?: boolean };
  conclusion?: {
    market_state?: string;
    principal_conflict?: string;
    dominant_aspect?: string;
    next_week_focus?: string;
    action?: string;
    statement?: string;
    nature?: string;
  };
  market_autopsy?: {
    market_state?: string;
    market_health?: number | null;
    attack_intensity?: number | null;
    risk_level?: number | null;
    one_line?: string;
    facts?: string[];
    answers?: MarketAnswer[];
  };
  conflicts?: {
    principal?: string;
    principal_evidence?: string[];
    dominant_aspect?: string;
    stage?: { code?: string; label?: string };
    confidence_pct?: number | null;
    validation?: { statement?: string; window?: string; falsification?: string[] };
  };
  sectors?: SectorResearch[];
  candidates?: StockCandidate[];
  exclusions?: Array<{ code?: string; name?: string; sector?: string; reason?: string }>;
  scenarios?: Scenario[];
  topic_research?: {
    question?: string;
    facts?: string[];
    inference?: string;
    counter_evidence?: string[];
    uncertainty?: string;
  } | null;
  data_quality?: {
    completeness_pct?: number | null;
    confidence?: string;
    missing_fields?: string[];
    stale_components?: string[];
    policy?: string;
  };
  agent_runs?: Array<{ agent?: string; status?: string; output?: string }>;
  ai_synthesis?: { available?: boolean; narrative?: string | null; nature?: string };
  guardrails?: string[];
}

interface Judgment {
  id: number;
  target_type: string;
  target_key: string;
  action: ReviewAction;
  user_judgment?: string | null;
  reason?: string | null;
  validation_status?: string;
  correct_party?: string | null;
}

interface Hypothesis {
  id: number;
  key: string;
  scope: string;
  target?: string | null;
  title: string;
  statement: string;
  horizon: string;
  evidence: string[];
  falsification: string[];
  due_date?: string | null;
  status: string;
  actual_result?: string | null;
  error_type?: string | null;
}

interface ResearchSession extends ResearchSessionSummary {
  report?: ResearchReport;
  judgments?: Judgment[];
  hypotheses?: Hypothesis[];
  versions?: Record<string, string>;
  error?: string | null;
}

interface MarketCase {
  id: number;
  case_type: string;
  title: string;
  summary: string;
  outcome: string;
  error_attribution?: string | null;
  lesson?: string | null;
  case_date?: string | null;
}

interface ResearchInsights {
  hypotheses?: { total?: number; pending?: number; validated?: number; accuracy_pct?: number | null };
  judgments?: { total?: number; actions?: Record<string, number>; correct_party?: Record<string, number> };
  errors?: Array<{ type: string; count: number }>;
  case_count?: number;
  knowledge_memory?: Array<{ pattern: string; observations: number; guidance: string }>;
}

interface ReviewForm {
  targetType: 'market' | 'sector' | 'stock' | 'scenario';
  targetKey: string;
  title: string;
  aiSummary: string;
  action: ReviewAction;
  userJudgment: string;
  reason: string;
}

interface ValidationForm {
  hypothesis: Hypothesis;
  result: 'CORRECT' | 'PARTIAL' | 'WRONG' | 'UNVERIFIABLE';
  actualResult: string;
  errorType: string;
  lesson: string;
  correctParty: '' | 'AI' | 'USER' | 'BOTH' | 'NEITHER';
}

const STATUS_LABELS: Record<string, string> = {
  DRAFT: '等待开始', RUNNING: '研究中', COMPLETED: '待审阅', REVIEWING: '审阅中',
  VALIDATING: '验证中', ARCHIVED: '已归档', FAILED: '失败',
};

const MODE_LABELS: Record<string, string> = { quick: '快速研究', deep: '深度研究', topic: '专题研究' };
const ACTION_LABELS: Record<ReviewAction, string> = { APPROVE: '认可', MODIFY: '修改', REJECT: '否决' };
const RESULT_LABELS: Record<string, string> = { PENDING: '待验证', CORRECT: '正确', PARTIAL: '部分正确', WRONG: '错误', UNVERIFIABLE: '无法验证' };

function scoreText(value?: number | null): string {
  return value == null ? '--' : value.toFixed(1);
}

function pctText(value?: number | null): string {
  return value == null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function yiText(value?: number | null): string {
  if (value == null) return '--';
  return `${(value / 1e8).toFixed(1)}亿`;
}

function dateTimeText(value?: string | null): string {
  if (!value) return '--';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value.slice(0, 16) : date.toLocaleString('zh-CN', { hour12: false });
}

function toneForScore(value?: number | null, inverse = false): string {
  if (value == null) return 'text-text-secondary';
  const positive = inverse ? value <= 40 : value >= 65;
  const negative = inverse ? value >= 70 : value < 45;
  return positive ? 'text-down' : negative ? 'text-up' : 'text-warn';
}

export default function ResearchCenterPage() {
  const [tab, setTab] = useState<Tab>('conclusion');
  const [session, setSession] = useState<ResearchSession | null>(null);
  const [sessions, setSessions] = useState<ResearchSessionSummary[]>([]);
  const [cases, setCases] = useState<MarketCase[]>([]);
  const [insights, setInsights] = useState<ResearchInsights>({});
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [topic, setTopic] = useState('');
  const [review, setReview] = useState<ReviewForm | null>(null);
  const [validation, setValidation] = useState<ValidationForm | null>(null);
  const [saving, setSaving] = useState(false);

  const loadSupporting = useCallback(async () => {
    const [historyResponse, casesResponse, insightsResponse] = await Promise.all([
      apiFetch<{ data: { sessions: ResearchSessionSummary[] } }>('/research/weekly?limit=30'),
      apiFetch<{ data: { cases: MarketCase[] } }>('/research/cases?limit=30'),
      apiFetch<{ data: ResearchInsights }>('/research/insights'),
    ]);
    setSessions(historyResponse.data.sessions || []);
    setCases(casesResponse.data.cases || []);
    setInsights(insightsResponse.data || {});
  }, []);

  const loadSession = useCallback(async (id: string, quiet = false) => {
    if (!quiet) setDetailLoading(true);
    try {
      const response = await apiFetch<{ data: ResearchSession }>(`/research/weekly/${encodeURIComponent(id)}`);
      setSession(response.data);
      return response.data;
    } finally {
      if (!quiet) setDetailLoading(false);
    }
  }, []);

  const loadInitial = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [latestResponse] = await Promise.all([
        apiFetch<{ data: ResearchSession | null }>('/research/weekly/latest'),
        loadSupporting(),
      ]);
      setSession(latestResponse.data || null);
    } catch (caught) {
      setError(friendlyApiError(caught, '研究中心加载失败'));
    } finally {
      setLoading(false);
    }
  }, [loadSupporting]);

  useEffect(() => { void loadInitial(); }, [loadInitial]);

  useEffect(() => {
    if (!session || !['DRAFT', 'RUNNING'].includes(session.status)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const updated = await loadSession(session.id, true);
        if (!['DRAFT', 'RUNNING'].includes(updated.status)) await loadSupporting();
      } catch (caught) {
        setError(friendlyApiError(caught, '研究进度读取失败'));
      }
    }, 1800);
    return () => window.clearInterval(timer);
  }, [loadSession, loadSupporting, session]);

  const startResearch = async (mode: 'quick' | 'deep' | 'topic') => {
    if (mode === 'topic' && topic.trim().length < 2) {
      setError('请填写至少两个字的专题研究问题');
      return;
    }
    setStarting(mode);
    setError('');
    setMessage('');
    try {
      const response = mode === 'topic'
        ? await apiFetch<{ data: ResearchSession }>('/research/topic', { method: 'POST', body: JSON.stringify({ question: topic.trim() }) })
        : await apiFetch<{ data: ResearchSession }>('/research/weekly/start', { method: 'POST', body: JSON.stringify({ mode }) });
      setSession(response.data);
      setTab('conclusion');
      setMessage(`${MODE_LABELS[mode]}已开始`);
      await loadSupporting();
    } catch (caught) {
      setError(friendlyApiError(caught, '研究任务启动失败'));
    } finally {
      setStarting(null);
    }
  };

  const openReview = (
    targetType: ReviewForm['targetType'],
    targetKey: string,
    title: string,
    aiSummary: string,
    action: ReviewAction,
  ) => setReview({ targetType, targetKey, title, aiSummary, action, userJudgment: '', reason: '' });

  const saveReview = async (event: FormEvent) => {
    event.preventDefault();
    if (!review || !session) return;
    if (review.action === 'MODIFY' && !review.userJudgment.trim()) {
      setError('修改判断时需要填写你的判断');
      return;
    }
    if (review.action === 'REJECT' && !review.reason.trim()) {
      setError('否决判断时需要填写理由');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await apiFetch(`/research/${session.id}/judgment`, {
        method: 'POST',
        body: JSON.stringify({
          target_type: review.targetType,
          target_key: review.targetKey,
          action: review.action,
          user_judgment: review.userJudgment,
          reason: review.reason,
        }),
      });
      setReview(null);
      setMessage('你的判断已独立保存');
      await loadSession(session.id, true);
      await loadSupporting();
    } catch (caught) {
      setError(friendlyApiError(caught, '判断保存失败'));
    } finally {
      setSaving(false);
    }
  };

  const saveValidation = async (event: FormEvent) => {
    event.preventDefault();
    if (!validation || !session) return;
    if (!validation.actualResult.trim()) {
      setError('请填写真实市场结果');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await apiFetch(`/research/hypothesis/${validation.hypothesis.id}/validate`, {
        method: 'POST',
        body: JSON.stringify({
          result: validation.result,
          actual_result: validation.actualResult,
          error_type: validation.errorType,
          lesson: validation.lesson,
          correct_party: validation.correctParty || null,
        }),
      });
      setValidation(null);
      setMessage('市场验证结果已写入案例库');
      await loadSession(session.id, true);
      await loadSupporting();
    } catch (caught) {
      setError(friendlyApiError(caught, '验证结果保存失败'));
    } finally {
      setSaving(false);
    }
  };

  const archiveSession = async () => {
    if (!session || !window.confirm('确定归档这次研究吗？')) return;
    try {
      const response = await apiFetch<{ data: ResearchSession }>(`/research/${session.id}/archive`, { method: 'POST' });
      setSession(response.data);
      await loadSupporting();
    } catch (caught) {
      setError(friendlyApiError(caught, '归档失败'));
    }
  };

  const judgmentMap = useMemo(() => new Map(
    (session?.judgments || []).map((item) => [`${item.target_type}:${item.target_key}`, item]),
  ), [session?.judgments]);

  if (loading) {
    return <div className="min-h-[70vh] grid place-items-center"><div className="text-center"><Loader2 size={28} className="animate-spin text-accent mx-auto" /><p className="mt-3 text-xs text-text-secondary">正在读取研究档案</p></div></div>;
  }

  const report = session?.report;
  const running = Boolean(session && ['DRAFT', 'RUNNING'].includes(session.status));

  return (
    <div className="mx-auto w-full max-w-7xl px-3 py-4 sm:px-4 md:py-6">
      <header className="flex flex-col gap-4 border-b border-border pb-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-xl font-bold text-text md:text-2xl"><Microscope size={23} className="text-accent" />AI研究中心</h1>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-secondary">
            <span>数据日 {session?.source_data_date || '--'}</span>
            <span>{session ? MODE_LABELS[session.mode] || session.mode : '尚无研究'}</span>
            <span>{session ? STATUS_LABELS[session.status] || session.status : '--'}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => void startResearch('quick')} disabled={Boolean(starting || running)} className="command-button"><Search size={14} />{starting === 'quick' ? '启动中' : '快速研究'}</button>
          <button type="button" onClick={() => void startResearch('deep')} disabled={Boolean(starting || running)} className="command-button-primary"><Sparkles size={14} />{starting === 'deep' ? '启动中' : '深度研究'}</button>
          {session && !running && session.status !== 'ARCHIVED' && <button type="button" onClick={() => void archiveSession()} className="icon-button" title="归档研究"><Archive size={16} /></button>}
        </div>
      </header>

      <section className="border-b border-border py-4">
        <form onSubmit={(event) => { event.preventDefault(); void startResearch('topic'); }} className="flex flex-col gap-2 sm:flex-row">
          <div className="relative min-w-0 flex-1"><FlaskConical size={15} className="absolute left-3 top-2.5 text-text-secondary" /><input value={topic} onChange={(event) => setTopic(event.target.value)} placeholder="输入专题，例如：机器人板块最近一个月为何持续走强" className="w-full rounded-md border border-border bg-card py-2 pl-9 pr-3 text-xs text-text outline-none focus:border-accent" /></div>
          <button type="submit" disabled={Boolean(starting || running)} className="command-button"><FileSearch size={14} />专题研究</button>
        </form>
      </section>

      {error && <Notice tone="error" onClose={() => setError('')}>{error}</Notice>}
      {message && <Notice tone="success" onClose={() => setMessage('')}>{message}</Notice>}

      {running && session && <ResearchProgress session={session} />}
      {session?.status === 'FAILED' && <section className="my-4 border border-up/50 bg-[#EF535010] p-4 text-xs text-up"><div className="flex items-center gap-2 font-medium"><AlertTriangle size={15} />研究任务失败</div><p className="mt-2 text-text-secondary">{session.error || '数据源暂时不可用'}</p></section>}

      <div className="-mx-3 overflow-x-auto border-b border-border px-3 sm:mx-0 sm:px-0">
        <div className="flex min-w-max">
          <TabButton active={tab === 'conclusion'} onClick={() => setTab('conclusion')} icon={<BrainCircuit size={14} />} label="结论" />
          <TabButton active={tab === 'candidates'} onClick={() => setTab('candidates')} icon={<BarChart3 size={14} />} label={`候选池 ${report?.candidates?.length || 0}`} />
          <TabButton active={tab === 'evidence'} onClick={() => setTab('evidence')} icon={<FlaskConical size={14} />} label={`证据与验证 ${session?.hypotheses?.length || 0}`} />
          <TabButton active={tab === 'history'} onClick={() => setTab('history')} icon={<History size={14} />} label={`历史研究 ${sessions.length}`} />
        </div>
      </div>

      {detailLoading ? <div className="py-24 text-center"><Loader2 size={24} className="animate-spin text-accent mx-auto" /></div> : !report && !running ? <EmptyState onStart={() => void startResearch('quick')} /> : report ? <>
        {tab === 'conclusion' && <ConclusionView report={report} session={session} judgmentMap={judgmentMap} openReview={openReview} />}
        {tab === 'candidates' && <CandidatesView report={report} session={session} judgmentMap={judgmentMap} openReview={openReview} />}
        {tab === 'evidence' && <EvidenceView report={report} hypotheses={session?.hypotheses || []} topic={session?.topic} onValidate={(item) => setValidation({ hypothesis: item, result: 'CORRECT', actualResult: '', errorType: '', lesson: '', correctParty: '' })} />}
      </> : null}

      {tab === 'history' && <HistoryView sessions={sessions} activeId={session?.id} cases={cases} insights={insights} onSelect={async (id) => { setError(''); await loadSession(id); setTab('conclusion'); }} />}

      {review && <ReviewModal form={review} saving={saving} onChange={setReview} onSubmit={saveReview} onClose={() => setReview(null)} />}
      {validation && <ValidationModal form={validation} saving={saving} onChange={setValidation} onSubmit={saveValidation} onClose={() => setValidation(null)} />}

      <style jsx global>{`
        .command-button, .command-button-primary { display:inline-flex; min-height:34px; align-items:center; justify-content:center; gap:6px; border-radius:5px; padding:7px 12px; font-size:12px; transition:color .15s,background .15s,border-color .15s; }
        .command-button { border:1px solid #30363D; color:#C9D1D9; background:#161B22; }
        .command-button:hover { border-color:#58A6FF; color:#58A6FF; }
        .command-button-primary { border:1px solid #1F6FEB; color:#fff; background:#1F6FEB; }
        .command-button:disabled, .command-button-primary:disabled { cursor:not-allowed; opacity:.5; }
        .icon-button { display:grid; width:34px; height:34px; place-items:center; border:1px solid #30363D; border-radius:5px; color:#8B949E; }
        .icon-button:hover { border-color:#58A6FF; color:#58A6FF; }
        .research-input { width:100%; border:1px solid #30363D; background:#0D1117; color:#E6EDF3; border-radius:4px; padding:8px 10px; font-size:12px; outline:none; }
        .research-input:focus { border-color:#58A6FF; }
      `}</style>
    </div>
  );
}

function ResearchProgress({ session }: { session: ResearchSession }) {
  return <section className="my-4 border border-accent/40 bg-[#1F6FEB0D] p-4"><div className="flex items-center justify-between gap-4"><div className="flex min-w-0 items-center gap-2 text-sm text-text"><Loader2 size={15} className="shrink-0 animate-spin text-accent" /><span className="truncate">{session.stage}</span></div><span className="shrink-0 font-mono text-xs text-accent">{session.progress}%</span></div><div className="mt-3 h-1.5 overflow-hidden rounded bg-[#21262D]"><div className="h-full bg-accent transition-[width] duration-500" style={{ width: `${Math.max(2, session.progress)}%` }} /></div></section>;
}

function ConclusionView({ report, session, judgmentMap, openReview }: { report: ResearchReport; session: ResearchSession | null; judgmentMap: Map<string, Judgment>; openReview: (type: ReviewForm['targetType'], key: string, title: string, summary: string, action: ReviewAction) => void }) {
  const autopsy = report.market_autopsy || {};
  const conclusion = report.conclusion || {};
  const conflicts = report.conflicts || {};
  const marketReview = judgmentMap.get('market:market');
  return <div className="py-5 space-y-6">
    <section className="overflow-hidden rounded-md border border-border"><div className="grid grid-cols-2 lg:grid-cols-4"><Metric label="市场状态" value={autopsy.market_state || '--'} /><Metric label="市场健康度" value={scoreText(autopsy.market_health)} tone={toneForScore(autopsy.market_health)} /><Metric label="进攻强度" value={scoreText(autopsy.attack_intensity)} tone={toneForScore(autopsy.attack_intensity)} /><Metric label="风险等级" value={scoreText(autopsy.risk_level)} tone={toneForScore(autopsy.risk_level, true)} /></div></section>

    <section className="border-b border-border pb-6"><div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between"><div className="max-w-4xl"><NatureTag value="INFERENCE" /><h2 className="mt-2 text-base font-semibold text-text">{conclusion.statement || autopsy.one_line}</h2><dl className="mt-4 grid grid-cols-1 gap-3 text-xs md:grid-cols-3"><Definition label="主要矛盾" value={conflicts.principal || conclusion.principal_conflict} /><Definition label="主导方面" value={conflicts.dominant_aspect || conclusion.dominant_aspect} /><Definition label="下周验证" value={conclusion.next_week_focus} /></dl>{report.ai_synthesis?.available && report.ai_synthesis.narrative && <div className="mt-4 border-l-2 border-accent pl-3"><div className="flex items-center gap-2 text-[10px] text-accent"><BrainCircuit size={12} />ReportAgent综合解读</div><p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-text-secondary">{report.ai_synthesis.narrative}</p></div>}</div>{session && <ReviewButtons targetType="market" targetKey="market" title="市场总判断" summary={conclusion.statement || ''} existing={marketReview} onOpen={openReview} />}</div></section>

    <section><SectionTitle icon={<FileSearch size={16} />} title="市场尸检" meta={`置信度 ${report.data_quality?.confidence || '--'}`} /><div className="mt-3 overflow-hidden rounded-md border border-border"><div className="divide-y divide-border">{(autopsy.answers || []).map((item) => <div key={item.question} className="grid grid-cols-1 gap-1 px-3 py-3 text-xs md:grid-cols-[220px_1fr] md:gap-5 md:px-4"><div className="flex items-start gap-2 font-medium text-text"><NatureTag value={item.nature} compact />{item.question}</div><p className="leading-5 text-text-secondary">{item.answer}</p></div>)}</div></div></section>

    <section><SectionTitle icon={<CircleDot size={16} />} title="重点板块生命周期" meta={`${report.sectors?.length || 0}个板块`} /><div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">{(report.sectors || []).map((sector) => <SectorCard key={sector.name} sector={sector} existing={judgmentMap.get(`sector:${sector.name}`)} onOpen={openReview} />)}</div></section>

    <section><SectionTitle icon={<FlaskConical size={16} />} title="下周情景" meta="条件系统" /><div className="mt-3 divide-y divide-border border-y border-border">{(report.scenarios || []).map((scenario) => <div key={scenario.key} className="grid grid-cols-1 gap-3 py-4 lg:grid-cols-[160px_1fr_1fr]"><div><div className="text-sm font-semibold text-text">{scenario.name}</div><div className="mt-1 text-[11px] text-text-secondary">支持度 {scenario.support || '--'}</div></div><Definition label="触发条件" value={(scenario.conditions || []).join('；')} /><Definition label="对应行动" value={scenario.action} /></div>)}</div></section>
  </div>;
}

function SectorCard({ sector, existing, onOpen }: { sector: SectorResearch; existing?: Judgment; onOpen: (type: ReviewForm['targetType'], key: string, title: string, summary: string, action: ReviewAction) => void }) {
  return <article className="rounded-md border border-border p-4"><div className="flex items-start justify-between gap-3"><div><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold text-text">{sector.name}</h3><span className="rounded border border-accent/40 px-1.5 py-0.5 text-[10px] text-accent">{sector.lifecycle || '观察'}</span><span className="text-[10px] text-text-secondary">{sector.classification}</span></div><div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-secondary"><span>强度 <b className="font-mono font-normal text-text">{scoreText(sector.strength_score)}</b></span><span>宽度 <b className="font-mono font-normal text-text">{scoreText(sector.breadth)}</b></span><span>涨跌 <b className={Number(sector.change_pct) >= 0 ? 'font-mono font-normal text-up' : 'font-mono font-normal text-down'}>{pctText(sector.change_pct)}</b></span><span>主力 <b className="font-mono font-normal text-text">{yiText(sector.main_net_inflow)}</b></span></div></div><ReviewStatus judgment={existing} /></div><p className="mt-3 text-xs leading-5 text-text-secondary">{sector.why_core}</p>{(sector.roles || []).length > 0 && <div className="mt-3 flex flex-wrap gap-2">{(sector.roles || []).map((role, index) => <span key={`${role.code}-${index}`} className="rounded border border-border px-2 py-1 text-[10px] text-text-secondary"><b className="font-normal text-text">{role.role}</b> {role.name}</span>)}</div>}{(sector.risk_flags || []).length > 0 && <p className="mt-3 flex items-start gap-1.5 text-[11px] text-warn"><AlertTriangle size={12} className="mt-0.5 shrink-0" />{sector.risk_flags?.join('；')}</p>}<div className="mt-4"><ReviewButtons targetType="sector" targetKey={sector.name} title={`${sector.name}生命周期`} summary={`${sector.name}处于${sector.lifecycle}，方向${sector.direction}`} existing={existing} onOpen={onOpen} compact /></div></article>;
}

function CandidatesView({ report, session, judgmentMap, openReview }: { report: ResearchReport; session: ResearchSession | null; judgmentMap: Map<string, Judgment>; openReview: (type: ReviewForm['targetType'], key: string, title: string, summary: string, action: ReviewAction) => void }) {
  return <div className="py-5"><div className="mb-4 flex flex-wrap items-center justify-between gap-2"><SectionTitle icon={<Search size={16} />} title="研究候选池" meta={`${report.candidates?.length || 0}只`} /><span className="text-[11px] text-text-secondary">数据日 {session?.source_data_date || '--'}</span></div><div className="grid grid-cols-1 gap-4 xl:grid-cols-2">{(report.candidates || []).map((stock) => <CandidateCard key={stock.code} stock={stock} existing={judgmentMap.get(`stock:${stock.code}`)} onOpen={openReview} />)}</div>{!(report.candidates || []).length && <div className="py-20 text-center text-xs text-text-secondary">本轮没有形成可核验研究候选</div>}{(report.exclusions || []).length > 0 && <section className="mt-7"><SectionTitle icon={<ThumbsDown size={16} />} title="为什么不是它" meta={`${report.exclusions?.length}只`} /><div className="mt-3 overflow-x-auto rounded-md border border-border"><table className="w-full min-w-[680px] text-xs"><thead className="border-b border-border text-text-secondary"><tr><th className="px-4 py-2.5 text-left">股票</th><th className="px-3 text-left">板块</th><th className="px-4 text-left">淘汰依据</th></tr></thead><tbody>{report.exclusions?.map((item) => <tr key={item.code} className="border-b border-border/60 last:border-b-0"><td className="px-4 py-3 text-text">{item.name} <span className="font-mono text-text-secondary">{item.code}</span></td><td className="px-3 py-3 text-text-secondary">{item.sector}</td><td className="px-4 py-3 leading-5 text-text-secondary">{item.reason}</td></tr>)}</tbody></table></div></section>}</div>;
}

function CandidateCard({ stock, existing, onOpen }: { stock: StockCandidate; existing?: Judgment; onOpen: (type: ReviewForm['targetType'], key: string, title: string, summary: string, action: ReviewAction) => void }) {
  const rr = stock.risk_reward || {};
  const alpha = stock.alpha || {};
  return <article className="min-w-0 rounded-md border border-border bg-card p-4"><header className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><StockKlineButton code={stock.code} name={stock.name} className="text-sm font-semibold text-text">{stock.name}</StockKlineButton><span className="font-mono text-[11px] text-text-secondary">{stock.code}</span><span className="rounded border border-border px-1.5 py-0.5 text-[10px] text-text-secondary">{stock.research_class} · {stock.research_class_label}</span></div><div className="mt-1 text-[11px] text-text-secondary">{stock.sector} · {stock.research_status} · {stock.decision_label}</div></div><ReviewStatus judgment={existing} /></header><div className="mt-4 grid grid-cols-2 overflow-hidden rounded border border-border sm:grid-cols-4"><SmallMetric label="Alpha" value={scoreText(alpha.score)} /><SmallMetric label="风险收益比" value={rr.risk_reward_ratio == null ? '--' : rr.risk_reward_ratio.toFixed(2)} /><SmallMetric label="盈利质量" value={stock.earnings?.quality || '--'} /><SmallMetric label="数据完整度" value={`${scoreText(stock.data_completeness_pct)}%`} /></div><p className="mt-4 line-clamp-3 text-xs leading-5 text-text-secondary">{stock.company?.main_business || '主营业务公开数据本次未完整返回'}</p><dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2"><Definition label="最大优势" value={stock.main_advantage} /><Definition label="最大风险" value={stock.main_risk} warn /><Definition label="估值" value={`${stock.valuation?.state || '--'}${stock.valuation?.cycle_phase ? ` · ${stock.valuation.cycle_phase}` : ''}`} /><Definition label="板块依赖" value={`${stock.sector_dependency?.dependency_level || '--'}依赖 · 独立性${stock.sector_dependency?.independence_level || '--'}`} /><Definition label="预期差" value={stock.expectation_gap?.state || '--'} /><Definition label="情绪" value={`${stock.emotion?.level || '--'} · ${stock.emotion?.trend || '--'}`} /></dl><div className="mt-4 border-t border-border pt-3"><ListBlock label="触发条件" values={stock.trigger_conditions || []} /><div className="mt-3"><ListBlock label="失效条件" values={stock.invalidation_conditions || []} warn /></div></div><footer className="mt-4 flex flex-wrap items-center justify-between gap-3"><AddToPersonalPoolButton code={stock.code} name={stock.name} industry={stock.sector} thesis={(stock.why_research || []).join('；')} source="weekend_research_v3" compact /><ReviewButtons targetType="stock" targetKey={stock.code} title={`${stock.name}研究判断`} summary={`${stock.research_class_label}；${stock.main_advantage}；风险：${stock.main_risk}`} existing={existing} onOpen={onOpen} compact /></footer></article>;
}

function EvidenceView({ report, hypotheses, topic, onValidate }: { report: ResearchReport; hypotheses: Hypothesis[]; topic?: string | null; onValidate: (value: Hypothesis) => void }) {
  return <div className="py-5 space-y-7">{topic && report.topic_research && <section><SectionTitle icon={<FlaskConical size={16} />} title="专题研究" meta="证据约束" /><div className="mt-3 border-y border-border py-4"><h3 className="text-sm font-semibold text-text">{report.topic_research.question}</h3><p className="mt-3 text-xs leading-5 text-text-secondary">{report.topic_research.inference}</p><div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2"><ListBlock label="事实" values={report.topic_research.facts || []} /><ListBlock label="反证" values={report.topic_research.counter_evidence || []} warn /></div><p className="mt-4 text-[11px] text-warn">{report.topic_research.uncertainty}</p></div></section>}
    <section><SectionTitle icon={<ShieldAlert size={16} />} title="数据完整度" meta={`${scoreText(report.data_quality?.completeness_pct)}% · ${report.data_quality?.confidence || '低'}置信度`} /><div className="mt-3 grid grid-cols-1 gap-4 border-y border-border py-4 lg:grid-cols-2"><ListBlock label="缺失字段" values={report.data_quality?.missing_fields || []} empty="无已知缺失字段" /><ListBlock label="跨日组件" values={report.data_quality?.stale_components || []} empty="无跨日组件" /></div></section>
    <section><SectionTitle icon={<BrainCircuit size={16} />} title="Agent执行链" meta={`${report.agent_runs?.length || 0}个`} /><div className="mt-3 overflow-x-auto rounded-md border border-border"><table className="w-full min-w-[620px] text-xs"><thead className="border-b border-border text-text-secondary"><tr><th className="px-4 py-2.5 text-left">Agent</th><th className="px-3 text-left">状态</th><th className="px-4 text-left">输出</th></tr></thead><tbody>{report.agent_runs?.map((item) => <tr key={item.agent} className="border-b border-border/60 last:border-b-0"><td className="px-4 py-3 font-mono text-text">{item.agent}</td><td className="px-3 py-3 text-down"><span className="inline-flex items-center gap-1"><Check size={12} />完成</span></td><td className="px-4 py-3 text-text-secondary">{item.output}</td></tr>)}</tbody></table></div></section>
    <section><SectionTitle icon={<FlaskConical size={16} />} title="研究假设与市场验证" meta={`${hypotheses.filter((item) => item.status === 'PENDING').length}条待验证`} /><div className="mt-3 space-y-3">{hypotheses.map((item) => <article key={item.id} className="rounded-md border border-border p-4"><div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold text-text">{item.title}</h3><span className={`rounded border px-1.5 py-0.5 text-[10px] ${item.status === 'PENDING' ? 'border-warn/50 text-warn' : item.status === 'WRONG' ? 'border-up/50 text-up' : 'border-down/50 text-down'}`}>{RESULT_LABELS[item.status] || item.status}</span><NatureTag value="FORECAST" compact /></div><p className="mt-2 max-w-4xl text-xs leading-5 text-text-secondary">{item.statement}</p><div className="mt-2 text-[10px] text-text-secondary">{item.horizon} · 验证日 {item.due_date || '--'}</div></div>{item.status === 'PENDING' && <button type="button" onClick={() => onValidate(item)} className="command-button shrink-0"><BookOpenCheck size={14} />记录结果</button>}</div>{item.actual_result && <div className="mt-3 border-t border-border pt-3 text-xs text-text-secondary"><span className="text-text">真实结果：</span>{item.actual_result}</div>}<div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2"><ListBlock label="证据" values={item.evidence} /><ListBlock label="证伪条件" values={item.falsification} warn /></div></article>)}</div></section>
  </div>;
}

function HistoryView({ sessions, activeId, cases, insights, onSelect }: { sessions: ResearchSessionSummary[]; activeId?: string; cases: MarketCase[]; insights: ResearchInsights; onSelect: (id: string) => Promise<void> }) {
  return <div className="py-5 space-y-7"><section><div className="grid grid-cols-2 overflow-hidden rounded-md border border-border lg:grid-cols-4"><Metric label="研究假设" value={String(insights.hypotheses?.total || 0)} /><Metric label="待验证" value={String(insights.hypotheses?.pending || 0)} /><Metric label="已验证" value={String(insights.hypotheses?.validated || 0)} /><Metric label="验证准确率" value={insights.hypotheses?.accuracy_pct == null ? '--' : `${insights.hypotheses.accuracy_pct}%`} tone={toneForScore(insights.hypotheses?.accuracy_pct)} /></div></section><section><SectionTitle icon={<History size={16} />} title="历史研究" meta={`${sessions.length}次`} /><div className="mt-3 divide-y divide-border border-y border-border">{sessions.map((item) => <button key={item.id} type="button" onClick={() => void onSelect(item.id)} className={`grid w-full grid-cols-1 gap-2 px-1 py-4 text-left transition-colors hover:bg-[#161B2255] sm:grid-cols-[150px_110px_1fr_90px_20px] sm:items-center sm:gap-3 ${activeId === item.id ? 'bg-[#1F6FEB10]' : ''}`}><div className="font-mono text-[11px] text-text-secondary">{dateTimeText(item.created_at)}</div><div className="text-xs text-text">{MODE_LABELS[item.mode] || item.mode}</div><div className="min-w-0"><div className="truncate text-xs text-text">{item.topic || item.summary?.principal_conflict || '本周市场研究'}</div><div className="mt-1 text-[10px] text-text-secondary">{item.summary?.market_state || '--'} · 候选{item.summary?.candidate_count || 0}只</div></div><div className="text-[11px] text-text-secondary">{STATUS_LABELS[item.status] || item.status}</div><ChevronRight size={14} className="hidden text-text-secondary sm:block" /></button>)}</div></section><section><SectionTitle icon={<BookOpenCheck size={16} />} title="市场案例库" meta={`${cases.length}条`} /><div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">{cases.map((item) => <article key={item.id} className="rounded-md border border-border p-4"><div className="flex items-start justify-between gap-3"><h3 className="text-sm font-semibold text-text">{item.title}</h3><span className={`shrink-0 text-[10px] ${item.outcome === 'WRONG' ? 'text-up' : 'text-down'}`}>{RESULT_LABELS[item.outcome] || item.outcome}</span></div><p className="mt-2 text-xs leading-5 text-text-secondary">{item.summary}</p>{item.error_attribution && <p className="mt-3 text-[11px] text-warn">错误归因：{item.error_attribution}</p>}{item.lesson && <p className="mt-2 text-[11px] text-text-secondary">复盘：{item.lesson}</p>}</article>)}</div>{!cases.length && <div className="py-12 text-center text-xs text-text-secondary">真实验证后，案例会沉淀在这里</div>}</section>{(insights.knowledge_memory || []).length > 0 && <section><SectionTitle icon={<BrainCircuit size={16} />} title="AI认知库" meta="验证后更新" /><div className="mt-3 divide-y divide-border border-y border-border">{insights.knowledge_memory?.map((item) => <div key={item.pattern} className="grid grid-cols-1 gap-1 py-3 text-xs sm:grid-cols-[180px_80px_1fr]"><span className="text-text">{item.pattern}</span><span className="font-mono text-text-secondary">{item.observations}次</span><span className="text-text-secondary">{item.guidance}</span></div>)}</div></section>}</div>;
}

function ReviewButtons({ targetType, targetKey, title, summary, existing, onOpen, compact = false }: { targetType: ReviewForm['targetType']; targetKey: string; title: string; summary: string; existing?: Judgment; onOpen: (type: ReviewForm['targetType'], key: string, title: string, summary: string, action: ReviewAction) => void; compact?: boolean }) {
  if (existing) return <button type="button" onClick={() => onOpen(targetType, targetKey, title, summary, existing.action)} title="更新我的判断"><ReviewStatus judgment={existing} /></button>;
  return <div className="inline-flex overflow-hidden rounded-md border border-border"><button type="button" onClick={() => onOpen(targetType, targetKey, title, summary, 'APPROVE')} className="review-button text-down" title="认可"><ThumbsUp size={12} />{!compact && '认可'}</button><button type="button" onClick={() => onOpen(targetType, targetKey, title, summary, 'MODIFY')} className="review-button text-warn" title="修改"><RefreshCw size={12} />{!compact && '修改'}</button><button type="button" onClick={() => onOpen(targetType, targetKey, title, summary, 'REJECT')} className="review-button text-up" title="否决"><ThumbsDown size={12} />{!compact && '否决'}</button><style jsx>{`.review-button{display:inline-flex;min-width:${compact ? '34px' : '58px'};height:30px;align-items:center;justify-content:center;gap:4px;border-right:1px solid #30363D;padding:0 8px;font-size:11px}.review-button:last-child{border-right:0}.review-button:hover{background:#21262D}`}</style></div>;
}

function ReviewStatus({ judgment }: { judgment?: Judgment }) {
  if (!judgment) return null;
  const tone = judgment.action === 'APPROVE' ? 'text-down border-down/40' : judgment.action === 'REJECT' ? 'text-up border-up/40' : 'text-warn border-warn/40';
  return <span className={`inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-1 text-[10px] ${tone}`}><Check size={10} />我的判断：{ACTION_LABELS[judgment.action]}</span>;
}

function ReviewModal({ form, saving, onChange, onSubmit, onClose }: { form: ReviewForm; saving: boolean; onChange: (value: ReviewForm) => void; onSubmit: (event: FormEvent) => void; onClose: () => void }) {
  return <Modal title={form.title} onClose={onClose}><form onSubmit={onSubmit} className="space-y-4"><div className="rounded border border-border bg-bg p-3"><div className="text-[10px] text-text-secondary">AI原判断</div><p className="mt-1 text-xs leading-5 text-text">{form.aiSummary}</p></div><div className="grid grid-cols-3 overflow-hidden rounded-md border border-border">{(['APPROVE', 'MODIFY', 'REJECT'] as ReviewAction[]).map((action) => <button key={action} type="button" onClick={() => onChange({ ...form, action })} className={`h-9 border-r border-border text-xs last:border-r-0 ${form.action === action ? action === 'APPROVE' ? 'bg-[#26A69A22] text-down' : action === 'REJECT' ? 'bg-[#EF535022] text-up' : 'bg-[#D2992222] text-warn' : 'text-text-secondary'}`}>{ACTION_LABELS[action]}</button>)}</div>{form.action === 'MODIFY' && <Field label="我的判断"><textarea value={form.userJudgment} onChange={(event) => onChange({ ...form, userJudgment: event.target.value })} className="research-input min-h-24 resize-y" /></Field>}<Field label={form.action === 'REJECT' ? '否决理由' : '判断依据（可选）'}><textarea value={form.reason} onChange={(event) => onChange({ ...form, reason: event.target.value })} className="research-input min-h-24 resize-y" /></Field><ModalActions saving={saving} onCancel={onClose} /></form></Modal>;
}

function ValidationModal({ form, saving, onChange, onSubmit, onClose }: { form: ValidationForm; saving: boolean; onChange: (value: ValidationForm) => void; onSubmit: (event: FormEvent) => void; onClose: () => void }) {
  const results = ['CORRECT', 'PARTIAL', 'WRONG', 'UNVERIFIABLE'] as const;
  return <Modal title="市场验证" onClose={onClose}><form onSubmit={onSubmit} className="space-y-4"><div className="rounded border border-border bg-bg p-3"><div className="text-[10px] text-text-secondary">原研究假设</div><p className="mt-1 text-xs leading-5 text-text">{form.hypothesis.statement}</p></div><div className="grid grid-cols-2 overflow-hidden rounded-md border border-border sm:grid-cols-4">{results.map((result) => <button key={result} type="button" onClick={() => onChange({ ...form, result })} className={`h-9 border-b border-r border-border text-xs last:border-r-0 sm:border-b-0 ${form.result === result ? 'bg-[#1F6FEB22] text-accent' : 'text-text-secondary'}`}>{RESULT_LABELS[result]}</button>)}</div><Field label="真实市场结果"><textarea value={form.actualResult} onChange={(event) => onChange({ ...form, actualResult: event.target.value })} className="research-input min-h-24 resize-y" /></Field><Field label="AI与我的判断"><select value={form.correctParty} onChange={(event) => onChange({ ...form, correctParty: event.target.value as ValidationForm['correctParty'] })} className="research-input"><option value="">尚不比较</option><option value="AI">AI更接近结果</option><option value="USER">我的判断更接近结果</option><option value="BOTH">双方都正确</option><option value="NEITHER">双方都不正确</option></select></Field>{form.result === 'WRONG' && <Field label="错误归因"><select value={form.errorType} onChange={(event) => onChange({ ...form, errorType: event.target.value })} className="research-input"><option value="">请选择</option>{['数据问题', '逻辑问题', '权重问题', '阶段判断错误', '因果判断错误', '异常事件', '信息滞后', '市场结构变化'].map((item) => <option key={item}>{item}</option>)}</select></Field>}<Field label="复盘结论（可选）"><textarea value={form.lesson} onChange={(event) => onChange({ ...form, lesson: event.target.value })} className="research-input min-h-20 resize-y" /></Field><ModalActions saving={saving} onCancel={onClose} /></form></Modal>;
}

function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return <div className="fixed inset-0 z-[100] flex items-end justify-center bg-black/75 sm:items-center sm:p-4" role="dialog" aria-modal="true"><section className="max-h-[94dvh] w-full overflow-y-auto rounded-t-md border border-border bg-card sm:max-w-2xl sm:rounded-md"><header className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-card px-4 py-3"><h2 className="text-sm font-semibold text-text">{title}</h2><button type="button" onClick={onClose} className="icon-button" title="关闭"><X size={16} /></button></header><div className="p-4">{children}</div></section></div>;
}

function ModalActions({ saving, onCancel }: { saving: boolean; onCancel: () => void }) {
  return <div className="flex justify-end gap-2 pt-2"><button type="button" onClick={onCancel} className="command-button">取消</button><button type="submit" disabled={saving} className="command-button-primary">{saving && <Loader2 size={13} className="animate-spin" />}保存</button></div>;
}

function EmptyState({ onStart }: { onStart: () => void }) {
  return <div className="py-24 text-center"><Microscope size={34} className="mx-auto text-border" /><h2 className="mt-4 text-sm font-semibold text-text">尚无周末研究</h2><button type="button" onClick={onStart} className="command-button-primary mt-5"><Search size={14} />开始本周市场研究</button></div>;
}

function Notice({ tone, children, onClose }: { tone: 'error' | 'success'; children: ReactNode; onClose: () => void }) {
  return <div className={`my-4 flex items-start justify-between gap-3 border p-3 text-xs ${tone === 'error' ? 'border-up/50 bg-[#EF535010] text-up' : 'border-down/50 bg-[#26A69A10] text-down'}`}><span className="flex items-start gap-2">{tone === 'error' ? <AlertTriangle size={14} className="shrink-0" /> : <Check size={14} className="shrink-0" />}{children}</span><button type="button" onClick={onClose} title="关闭"><X size={13} /></button></div>;
}

function TabButton({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: ReactNode; label: string }) {
  return <button type="button" onClick={onClick} className={`inline-flex h-11 items-center gap-1.5 border-b-2 px-4 text-xs ${active ? 'border-accent text-accent' : 'border-transparent text-text-secondary hover:text-text'}`}>{icon}{label}</button>;
}

function SectionTitle({ icon, title, meta }: { icon: ReactNode; title: string; meta?: string }) {
  return <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="flex items-center gap-2 text-sm font-semibold text-text"><span className="text-accent">{icon}</span>{title}</h2>{meta && <span className="text-[11px] text-text-secondary">{meta}</span>}</div>;
}

function Metric({ label, value, tone = 'text-text' }: { label: string; value: string; tone?: string }) {
  return <div className="min-w-0 border-b border-r border-border p-3 last:border-r-0 lg:border-b-0 lg:p-4"><div className="text-[10px] text-text-secondary">{label}</div><div className={`mt-1 truncate text-base font-semibold ${tone}`}>{value}</div></div>;
}

function SmallMetric({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0 border-b border-r border-border p-2.5 last:border-r-0 sm:border-b-0"><div className="text-[9px] text-text-secondary">{label}</div><div className="mt-1 truncate font-mono text-xs text-text">{value}</div></div>;
}

function Definition({ label, value, warn = false }: { label: string; value?: string | null; warn?: boolean }) {
  return <div><dt className="text-[10px] text-text-secondary">{label}</dt><dd className={`mt-1 text-xs leading-5 ${warn ? 'text-warn' : 'text-text-secondary'}`}>{value || '--'}</dd></div>;
}

function ListBlock({ label, values, warn = false, empty = '暂无可核验证据' }: { label: string; values: string[]; warn?: boolean; empty?: string }) {
  return <div><div className="text-[10px] text-text-secondary">{label}</div><ul className={`mt-1 space-y-1 text-xs leading-5 ${warn ? 'text-warn' : 'text-text-secondary'}`}>{values.length ? values.map((item, index) => <li key={`${item}-${index}`} className="flex items-start gap-1.5"><span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-current" />{item}</li>) : <li>{empty}</li>}</ul></div>;
}

function NatureTag({ value, compact = false }: { value: string; compact?: boolean }) {
  const label = value === 'FACT' ? '事实' : value === 'FORECAST' ? '预测' : '推断';
  const tone = value === 'FACT' ? 'border-down/40 text-down' : value === 'FORECAST' ? 'border-warn/40 text-warn' : 'border-accent/40 text-accent';
  return <span className={`inline-flex shrink-0 rounded border px-1.5 py-0.5 ${compact ? 'text-[9px]' : 'text-[10px]'} ${tone}`}>{label}</span>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block"><span className="mb-1.5 block text-[11px] text-text-secondary">{label}</span>{children}</label>;
}
