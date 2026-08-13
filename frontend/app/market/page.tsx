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
  Layers3,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Target,
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
}

const WORKBENCH_CONTRACT_VERSION = 'market-workbench-v2.0.1';
const LOCAL_CACHE_KEY = 'market_decision_workbench_v2_0_1';

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

export default function MarketDecisionWorkbenchPage() {
  const [data, setData] = useState<WorkbenchData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [progress, setProgress] = useState(8);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

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

  const { meta, market_state: marketState, headline_metrics: metrics } = data;
  const upDownText = finite(metrics.up_down_ratio) ? `${metrics.up_down_ratio.toFixed(2)} : 1` : '--';
  const staleCount = data.audit.stale_components.length;

  return (
    <main className="mx-auto w-full max-w-[1500px] px-3 py-4 sm:px-5 sm:py-6">
      <header className="mb-4 flex flex-col gap-3 border-b border-border pb-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <BrainCircuit size={21} className="shrink-0 text-accent" />
            <h1 className="text-xl font-semibold text-text sm:text-2xl">A股 AI 自适应决策工作台</h1>
          </div>
          <p className="mt-1.5 text-xs text-text-secondary">客观事实 → 主要矛盾 → 阶段判断 → 策略许可 → 实践验证</p>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-text-secondary">
            <span className={meta.is_realtime ? 'text-up' : 'text-warn'}>{meta.is_realtime ? '盘中实时决策' : meta.decision_scope}</span>
            <span>决策日 <b className="font-mono font-normal text-text">{meta.decision_date || '--'}</b></span>
            <span>更新 {localTime(meta.updated_at)}</span>
            <span>覆盖率 {value(meta.coverage_pct, 0)}%</span>
            {staleCount > 0 && <span className="text-warn">{staleCount} 个跨日组件已降级</span>}
          </div>
        </div>
        <div className="flex items-center gap-2 self-start lg:self-auto">
          <button
            type="button"
            onClick={() => void load(true)}
            disabled={refreshing}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-accent/50 px-3 text-xs text-accent hover:bg-accent/10 disabled:opacity-50"
          >
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
            {refreshing ? `${progress}%` : '重新核验'}
          </button>
        </div>
      </header>

      {notice && (
        <div className={`mb-4 border-l-2 px-3 py-2 text-xs ${notice.startsWith('已重新') ? 'border-up bg-up/5 text-up' : 'border-warn bg-warn/5 text-warn'}`}>
          {notice}
        </div>
      )}

      {data.audit.refresh_warning && (
        <div className="mb-4 border-l-2 border-warn bg-warn/5 px-3 py-2 text-xs text-warn">
          {data.audit.refresh_warning}
        </div>
      )}

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

      <section className="mb-4 grid grid-cols-2 overflow-hidden rounded-md border border-border bg-card sm:grid-cols-3 lg:grid-cols-5" aria-label="V2核心状态">
        <MetricCell label="市场状态" primary={`${marketState.state_code} ${marketState.state_label}`} secondary={`覆盖 ${value(marketState.coverage_pct, 0)}%`} tone={stateTone(marketState.state_code).split(' ')[0]} />
        <MetricCell label="结构健康" primary={value(data.structure_health.score)} secondary={data.structure_health.status} tone={data.structure_health.score != null && data.structure_health.score >= 65 ? 'text-up' : 'text-warn'} />
        <MetricCell label="量价匹配" primary={value(data.volume_price_alignment.score)} secondary={data.volume_price_alignment.status === 'supportive' ? '承接支持' : data.volume_price_alignment.status === 'divergent' ? '冲高缺承接' : '混合'} tone={data.volume_price_alignment.status === 'divergent' ? 'text-down' : 'text-text'} />
        <MetricCell label="抱团风险" primary={value(data.crowding_risk.score)} secondary={data.crowding_risk.status} tone={data.crowding_risk.score != null && data.crowding_risk.score >= 71 ? 'text-down' : data.crowding_risk.score != null && data.crowding_risk.score >= 51 ? 'text-warn' : 'text-up'} />
        <MetricCell label="今日最终行动" primary={data.market_cognition.action_label} secondary={`阶段：${data.market_cognition.stage.label}`} tone={actionTone(data.market_cognition.final_action).split(' ')[0]} />
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
                  <span className={dimension.observed ? 'font-mono text-text' : 'text-warn'}>{dimension.observed ? value(dimension.score) : '待采集'}</span>
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

      <section className="mb-4 overflow-hidden rounded-md border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border px-4 py-3"><h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Target size={15} className="text-accent" />策略有效性</h2><span className="text-[10px] text-text-secondary">只统计真实前向模拟样本</span></div>
        <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-xs"><thead className="border-b border-border bg-[#0D1117] text-[10px] text-text-secondary"><tr><th className="px-4 py-2.5 text-left">策略</th><th className="px-3 text-left">状态</th><th className="px-3 text-right">样本</th><th className="px-3 text-right">胜率</th><th className="px-3 text-right">期望值</th><th className="px-3 text-right">盈亏比</th><th className="px-3 text-right">最大回撤</th><th className="px-4 text-left">结论</th></tr></thead><tbody className="divide-y divide-border/70">{data.strategy_health.length ? data.strategy_health.map((item) => <tr key={item.id}><td className="px-4 py-3 text-text">{item.name}<div className="mt-1 font-mono text-[10px] text-text-secondary">{item.id}</div></td><td className={`px-3 py-3 font-mono ${healthTone(item.state)}`}>{item.state}</td><td className="px-3 py-3 text-right font-mono text-text">{item.metrics.sample_count}</td><td className="px-3 py-3 text-right font-mono text-text">{value(item.metrics.win_rate_pct)}%</td><td className="px-3 py-3 text-right font-mono text-text">{value(item.metrics.expectancy, 2)}</td><td className="px-3 py-3 text-right font-mono text-text">{value(item.metrics.profit_factor, 2)}</td><td className="px-3 py-3 text-right font-mono text-text">{amount(item.metrics.max_drawdown_amount)}</td><td className="max-w-[300px] px-4 py-3 text-[10px] leading-4 text-text-secondary">{item.reason}</td></tr>) : <tr><td colSpan={8} className="px-4 py-8 text-center text-xs text-text-secondary">暂无策略前向样本，不能判定有效性</td></tr>}</tbody></table></div>
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
            <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><BarChart3 size={15} className="text-accent" />候选股票池</h2>
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
        {data.main_lines.length === 0 ? (
          <div className="px-4 py-10 text-center text-xs text-text-secondary">主线板块数据待采集</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-xs">
              <thead className="border-b border-border bg-[#0D1117] text-[10px] text-text-secondary">
                <tr>
                  <th className="px-4 py-2.5 text-left font-medium">排名 / 板块</th>
                  <th className="px-3 text-left font-medium">级别</th>
                  <th className="px-3 text-left font-medium">生命周期</th>
                  <th className="px-3 text-right font-medium">强度</th>
                  <th className="px-3 text-right font-medium">宽度</th>
                  <th className="px-3 text-right font-medium">资金</th>
                  <th className="px-3 text-left font-medium">龙头</th>
                  <th className="px-4 text-left font-medium">证据 / 风险</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/70">
                {data.main_lines.slice(0, 8).map((line) => (
                  <tr key={`${line.rank}-${line.name}`} className="hover:bg-[#21262D]/40">
                    <td className="px-4 py-3"><span className="mr-2 font-mono text-text-secondary">{line.rank}</span><span className="font-medium text-text">{line.name}</span></td>
                    <td className="px-3 py-3 text-accent">{line.classification}</td>
                    <td className={`px-3 py-3 ${line.lifecycle === '退潮' || line.lifecycle === '分化预警' ? 'text-warn' : 'text-text'}`}>{line.lifecycle}</td>
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
        )}
      </section>

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
                {data.audit.stale_components.length > 0 && <div>跨日降级：{data.audit.stale_components.join('、')}</div>}
                {data.audit.missing_fields.length > 0 && <div>待采集：{data.audit.missing_fields.slice(0, 6).join('、')}</div>}
              </div>
            )}
            <div className="mt-3 text-[10px] leading-5 text-text-secondary">{data.audit.same_day_rule}</div>
          </div>
        </section>
      </div>

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
