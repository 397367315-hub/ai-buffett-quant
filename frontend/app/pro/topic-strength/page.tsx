'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Activity,
  BrainCircuit,
  CalendarDays,
  Database,
  Flame,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  TrendingDown,
  TrendingUp,
  Users,
} from 'lucide-react';
import AddToPersonalPoolButton from '@/components/AddToPersonalPoolButton';
import { apiFetch, formatYi } from '@/lib/api';

interface TopicStock {
  code: string;
  name: string;
  boards: number;
  boards_verified: boolean;
  price: number | null;
  pct: number | null;
  amount: number | null;
  turnover: number | null;
  industry: string;
  first_limit_time: string | number | null;
  main_net_inflow: number | null;
  return_5d_pct: number | null;
  heat_status: '过热' | '可观察' | '待核验';
  overheated: boolean | null;
  event_source: string;
  data_gaps: string[];
}

interface TopicGroup {
  name: string;
  rank: number;
  members: TopicStock[];
  leader: TopicStock;
  member_count: number;
  breadth: number | null;
  breadth_source: string | null;
  sector_flow_rank: number | null;
  sector_main_net_inflow: number | null;
  sector_change_pct: number | null;
  strength_score: number;
  status: '强' | '观察';
  novelty: '新出现' | '延续' | '待核验';
  evidence: string;
  audit: { facts: string[]; inferences: string[]; gaps: string[] };
}

interface AnalysisStep {
  step: number;
  title: string;
  classification: '事实' | '推断' | '数据缺口' | '边界' | '规则';
  result: string;
}

interface SectorFlow {
  rank: number;
  code: string;
  name: string;
  change_pct: number | null;
  main_net_inflow: number | null;
  up_count: number | null;
  down_count: number | null;
  source: string;
}

interface TopicStrengthData {
  available: boolean;
  updated: string;
  updated_at: string;
  data_date: string;
  is_realtime: boolean;
  source: string;
  cache_hit: boolean;
  market: {
    sentiment: { up: number; down: number; flat: number; total: number; up_ratio: number | null; breadth: string; source: string };
    emotion: { zt_count: number | null; dt_count: number | null; zb_count: number | null; break_rate: number | null; source: string };
    top_sectors: SectorFlow[];
    note: string;
  };
  topics: TopicGroup[];
  steps: AnalysisStep[];
  risk: string[];
  data_quality: {
    complete_market_snapshot: boolean;
    limit_pool: boolean;
    industry_flow: boolean;
    sentiment_cache: boolean;
    missing_fields: string[];
    missing_policy: string;
  };
  method: string;
}

interface DateOption {
  date: string;
  limit_up_count: number | null;
  failed_limit_count: number | null;
  stock_count: number | null;
}

interface TopicAnalysis {
  available: boolean;
  data_date: string;
  report: string;
  ai_generated: boolean;
  snapshot: TopicStrengthData;
}

function value(value: number | null | undefined, digits = 1): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '--';
}

function signed(valueToFormat: number | null | undefined): string {
  if (typeof valueToFormat !== 'number' || !Number.isFinite(valueToFormat)) return '--';
  return `${valueToFormat > 0 ? '+' : ''}${valueToFormat.toFixed(2)}%`;
}

function amount(valueToFormat: number | null | undefined): string {
  return typeof valueToFormat === 'number' && Number.isFinite(valueToFormat) ? formatYi(valueToFormat) : '--';
}

function classificationClass(kind: AnalysisStep['classification']): string {
  if (kind === '事实') return 'border-up/40 text-up';
  if (kind === '推断') return 'border-accent/40 text-accent';
  if (kind === '数据缺口') return 'border-warn/50 text-warn';
  return 'border-border text-text-secondary';
}

function LoadingState() {
  return (
    <div className="border-y border-border py-16">
      <div className="mx-auto max-w-sm text-center">
        <Activity size={24} className="mx-auto text-accent" />
        <div className="mt-3 text-sm text-text">正在核验市场环境与题材强度</div>
        <div className="mt-2 text-xs text-text-secondary">读取缓存、涨停池、行业资金与历史过热字段</div>
        <div className="mt-5 h-1 overflow-hidden bg-border"><div className="h-full w-full animate-pulse bg-accent" /></div>
      </div>
    </div>
  );
}

export default function TopicStrengthPage() {
  const [data, setData] = useState<TopicStrengthData | null>(null);
  const [dates, setDates] = useState<DateOption[]>([]);
  const [selectedDate, setSelectedDate] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState<TopicAnalysis | null>(null);
  const [error, setError] = useState('');

  const orderedDates = useMemo(() => [...dates].reverse(), [dates]);

  const loadDates = useCallback(async () => {
    try {
      const response = await apiFetch<{ data: { dates: DateOption[] } }>('/topic-strength/dates?limit=180');
      setDates(response.data.dates || []);
    } catch {
      setDates([]);
    }
  }, []);

  const load = useCallback(async (targetDate = '', refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams();
      if (targetDate) params.set('date', targetDate);
      if (refresh) params.set('refresh', 'true');
      const suffix = params.size ? `?${params.toString()}` : '';
      const response = await apiFetch<{ data: TopicStrengthData }>(`/topic-strength${suffix}`);
      setData(response.data);
      setSelectedDate(response.data.data_date || targetDate);
      setAnalysis(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '题材强弱数据读取失败');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void Promise.all([load(), loadDates()]);
  }, [load, loadDates]);

  const runAnalysis = async () => {
    setAnalyzing(true);
    setError('');
    try {
      const response = await apiFetch<{ data: TopicAnalysis }>('/topic-strength/analysis', {
        method: 'POST',
        body: JSON.stringify({ date: selectedDate || undefined, use_ai: true }),
      });
      setAnalysis(response.data);
      if (response.data.snapshot) setData(response.data.snapshot);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'AI题材分析失败');
    } finally {
      setAnalyzing(false);
    }
  };

  return (
    <div className="mx-auto max-w-[1480px] px-4 py-5">
      <header className="mb-5 flex flex-col gap-4 border-b border-border pb-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold text-text"><Flame size={21} className="text-warn" />题材强弱</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-secondary">
            <span>数据日 {data?.data_date || '--'}</span>
            <span>{data?.is_realtime ? '实时核验' : data?.cache_hit ? '闭市缓存' : '盘后核验'}</span>
            <span>更新时间 {data?.updated || '--'}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex h-9 items-center gap-2 rounded-md border border-border bg-[#0D1117] px-2.5 text-xs text-text-secondary">
            <CalendarDays size={14} />
            <select
              value={selectedDate}
              onChange={(event) => void load(event.target.value)}
              className="min-w-[140px] bg-transparent text-text outline-none"
              aria-label="题材强弱交易日"
            >
              {!selectedDate && <option value="">最近有效日</option>}
              {selectedDate && !orderedDates.some((item) => item.date === selectedDate) && <option value={selectedDate}>{selectedDate}</option>}
              {orderedDates.map((item) => <option key={item.date} value={item.date}>{item.date} · 涨停{item.limit_up_count ?? '--'}</option>)}
            </select>
          </label>
          <button
            type="button"
            onClick={() => void load(selectedDate, true)}
            disabled={refreshing || loading}
            className="grid h-9 w-9 place-items-center rounded-md border border-border bg-card text-text-secondary hover:border-accent hover:text-text disabled:opacity-50"
            title="重新核验数据源"
            aria-label="重新核验数据源"
          >
            <RefreshCw size={15} className={refreshing ? 'animate-spin' : ''} />
          </button>
          <button
            type="button"
            onClick={() => void runAnalysis()}
            disabled={!data?.available || analyzing}
            className="inline-flex h-9 items-center gap-2 rounded-md bg-accent px-3 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            <BrainCircuit size={15} className={analyzing ? 'animate-pulse' : ''} />{analyzing ? '分析中' : 'AI分析'}
          </button>
        </div>
      </header>

      {error && <div className="mb-5 rounded-md border border-down/40 bg-down/10 px-4 py-3 text-sm text-down">{error}</div>}
      {loading ? <LoadingState /> : !data?.available ? (
        <div className="border-y border-border py-16 text-center text-sm text-text-secondary">当前交易日没有可核验的题材强弱数据。</div>
      ) : (
        <>
          <section className="mb-5 grid grid-cols-2 gap-px border border-border bg-border lg:grid-cols-6">
            <Metric label="上涨" value={`${data.market.sentiment.up}只`} icon={<TrendingUp size={15} />} tone="up" />
            <Metric label="下跌" value={`${data.market.sentiment.down}只`} icon={<TrendingDown size={15} />} tone="down" />
            <Metric label="市场宽度" value={data.market.sentiment.breadth} hint={data.market.sentiment.up_ratio == null ? '待核验' : `${value(data.market.sentiment.up_ratio)}%上涨`} icon={<Users size={15} />} />
            <Metric label="涨停" value={data.market.emotion.zt_count == null ? '--' : `${data.market.emotion.zt_count}只`} icon={<Flame size={15} />} tone="up" />
            <Metric label="炸板" value={data.market.emotion.zb_count == null ? '--' : `${data.market.emotion.zb_count}只`} hint={data.market.emotion.break_rate == null ? '炸板率待核验' : `炸板率 ${value(data.market.emotion.break_rate)}%`} icon={<ShieldAlert size={15} />} tone="warn" />
            <Metric label="强题材" value={`${data.topics.filter((item) => item.status === '强').length}个`} hint={`观察 ${data.topics.filter((item) => item.status !== '强').length}个`} icon={<Sparkles size={15} />} tone="accent" />
          </section>

          <section className="mb-5 flex flex-wrap items-center gap-x-4 gap-y-2 border-y border-border px-1 py-3 text-[11px] text-text-secondary">
            <span className="inline-flex items-center gap-1.5"><Database size={13} />{data.source}</span>
            <span>完整行情 {data.data_quality.complete_market_snapshot ? '有' : '无'}</span>
            <span>源生涨停池 {data.data_quality.limit_pool ? '有' : '无'}</span>
            <span>行业资金 {data.data_quality.industry_flow ? '有' : '无'}</span>
            <span className="lg:ml-auto">{data.data_quality.missing_policy}</span>
          </section>

          <div className="grid gap-6 xl:grid-cols-[minmax(0,1.65fr)_minmax(320px,0.85fr)]">
            <section>
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-semibold text-text">题材强度排序</h2>
                <span className="text-[11px] text-text-secondary">共 {data.topics.length} 组</span>
              </div>
              <div className="space-y-3">
                {data.topics.map((topic) => <TopicRow key={topic.name} topic={topic} />)}
              </div>
            </section>

            <aside className="xl:border-l xl:border-border xl:pl-6">
              <h2 className="mb-3 text-sm font-semibold text-text">八步分析链路</h2>
              <div className="border-y border-border">
                {data.steps.map((step) => (
                  <div key={step.step} className="grid grid-cols-[28px_minmax(0,1fr)] gap-3 border-b border-border/70 py-3 last:border-b-0">
                    <div className="grid h-7 w-7 place-items-center rounded-full border border-border font-mono text-[11px] text-text-secondary">{step.step}</div>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs font-medium text-text">{step.title}</span>
                        <span className={`border px-1.5 py-0.5 text-[10px] ${classificationClass(step.classification)}`}>{step.classification}</span>
                      </div>
                      <p className="mt-1.5 text-xs leading-5 text-text-secondary">{step.result}</p>
                    </div>
                  </div>
                ))}
              </div>

              <div className="mt-5">
                <h2 className="mb-3 text-sm font-semibold text-text">行业资金前十</h2>
                <div className="border-y border-border">
                  {data.market.top_sectors.slice(0, 10).map((sector) => (
                    <div key={sector.code || sector.name} className="flex items-center gap-3 border-b border-border/60 py-2 text-xs last:border-b-0">
                      <span className="w-5 font-mono text-text-secondary">{sector.rank}</span>
                      <span className="min-w-0 flex-1 truncate text-text">{sector.name}</span>
                      <span className={sector.change_pct != null && sector.change_pct >= 0 ? 'text-up' : 'text-down'}>{signed(sector.change_pct)}</span>
                      <span className="w-[78px] text-right font-mono text-text-secondary">{amount(sector.main_net_inflow)}</span>
                    </div>
                  ))}
                </div>
              </div>
            </aside>
          </div>

          <section className="mt-6 border-y border-warn/35 bg-warn/5 py-4">
            <div className="flex items-start gap-3 px-1">
              <ShieldAlert size={17} className="mt-0.5 shrink-0 text-warn" />
              <div>
                <h2 className="text-sm font-semibold text-text">风险与边界</h2>
                <div className="mt-2 grid gap-x-8 gap-y-1.5 text-xs leading-5 text-text-secondary lg:grid-cols-2">
                  {data.risk.map((item) => <div key={item}>• {item}</div>)}
                  {data.data_quality.missing_fields.map((item) => <div key={`gap-${item}`} className="text-warn">• 待补：{item}</div>)}
                </div>
              </div>
            </div>
          </section>

          {analysis && (
            <section className="mt-6 border-t border-border pt-5">
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <BrainCircuit size={17} className="text-accent" />
                <h2 className="text-sm font-semibold text-text">AI题材分析</h2>
                <span className="text-[10px] text-text-secondary">{analysis.ai_generated ? '模型生成' : '规则底稿'} · 数据日 {analysis.data_date}</span>
              </div>
              <div className="prose prose-invert max-w-none text-sm leading-7 text-text-secondary prose-headings:text-text prose-h2:mt-5 prose-h2:text-base prose-strong:text-text prose-li:my-1">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{analysis.report}</ReactMarkdown>
              </div>
            </section>
          )}

          <div className="mt-5 text-[10px] leading-5 text-text-secondary">{data.method}</div>
        </>
      )}
    </div>
  );
}

function Metric({ label, value: metricValue, hint, icon, tone }: { label: string; value: string; hint?: string; icon: React.ReactNode; tone?: 'up' | 'down' | 'warn' | 'accent' }) {
  const toneClass = tone === 'up' ? 'text-up' : tone === 'down' ? 'text-down' : tone === 'warn' ? 'text-warn' : tone === 'accent' ? 'text-accent' : 'text-text';
  return (
    <div className="min-h-[92px] bg-card px-3 py-3">
      <div className="flex items-center gap-1.5 text-[11px] text-text-secondary">{icon}{label}</div>
      <div className={`mt-2 font-mono text-lg font-semibold ${toneClass}`}>{metricValue}</div>
      <div className="mt-1 min-h-[16px] text-[10px] text-text-secondary">{hint || ' '}</div>
    </div>
  );
}

function TopicRow({ topic }: { topic: TopicGroup }) {
  return (
    <article className="overflow-hidden rounded-md border border-border bg-card">
      <div className="flex flex-col gap-3 border-b border-border px-4 py-3 md:flex-row md:items-center">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-border font-mono text-xs text-text-secondary">{topic.rank}</span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="font-semibold text-text">{topic.name}</h3>
              <span className={`border px-1.5 py-0.5 text-[10px] ${topic.status === '强' ? 'border-up/50 text-up' : 'border-warn/50 text-warn'}`}>{topic.status}</span>
              <span className="border border-border px-1.5 py-0.5 text-[10px] text-text-secondary">{topic.novelty}</span>
            </div>
            <p className="mt-1 text-[11px] leading-5 text-text-secondary">{topic.evidence}</p>
          </div>
        </div>
        <div className="grid grid-cols-4 gap-px border border-border bg-border md:ml-auto md:min-w-[360px]">
          <TopicMetric label="强度" value={value(topic.strength_score)} />
          <TopicMetric label="涨停联动" value={`${topic.member_count}只`} />
          <TopicMetric label="上涨宽度" value={topic.breadth == null ? '--' : `${value(topic.breadth)}%`} />
          <TopicMetric label="资金排名" value={topic.sector_flow_rank == null ? '--' : `#${topic.sector_flow_rank}`} />
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-xs">
          <thead className="bg-[#0D1117] text-text-secondary">
            <tr><th className="px-4 py-2 text-left font-medium">观察标的</th><th className="px-3 py-2 text-right font-medium">连板</th><th className="px-3 py-2 text-right font-medium">涨幅</th><th className="px-3 py-2 text-right font-medium">成交额</th><th className="px-3 py-2 text-right font-medium">5日涨幅</th><th className="px-3 py-2 text-left font-medium">过热审计</th><th className="px-4 py-2 text-right font-medium">个人池</th></tr>
          </thead>
          <tbody>
            {topic.members.map((stock, index) => (
              <tr key={stock.code} className="border-t border-border/60">
                <td className="px-4 py-2.5">
                  <Link href={`/pro/stock?code=${stock.code}`} className="font-medium text-text hover:text-accent">{stock.name}<span className="ml-2 font-mono text-[10px] text-text-secondary">{stock.code}</span></Link>
                  {index === 0 && <span className="ml-2 text-[10px] text-accent">核心</span>}
                </td>
                <td className={`px-3 py-2.5 text-right font-mono ${stock.boards_verified ? 'text-up' : 'text-warn'}`}>{stock.boards_verified ? stock.boards : '待核验'}</td>
                <td className={`px-3 py-2.5 text-right font-mono ${stock.pct != null && stock.pct >= 0 ? 'text-up' : 'text-down'}`}>{signed(stock.pct)}</td>
                <td className="px-3 py-2.5 text-right font-mono text-text-secondary">{amount(stock.amount)}</td>
                <td className={`px-3 py-2.5 text-right font-mono ${stock.return_5d_pct != null && stock.return_5d_pct >= 0 ? 'text-up' : 'text-down'}`}>{signed(stock.return_5d_pct)}</td>
                <td className="px-3 py-2.5">
                  <span className={stock.heat_status === '过热' ? 'text-down' : stock.heat_status === '待核验' ? 'text-warn' : 'text-up'}>{stock.heat_status}</span>
                  {stock.data_gaps.length > 0 && <div className="mt-0.5 text-[9px] text-text-secondary">缺 {stock.data_gaps.join('、')}</div>}
                </td>
                <td className="px-4 py-2.5 text-right"><AddToPersonalPoolButton code={stock.code} name={stock.name} industry={stock.industry} thesis={`题材强弱：${topic.evidence}`} source="topic_strength" compact /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}

function TopicMetric({ label, value: metricValue }: { label: string; value: string }) {
  return <div className="min-h-[52px] bg-[#0D1117] px-2 py-2 text-center"><div className="text-[9px] text-text-secondary">{label}</div><div className="mt-1 font-mono text-xs text-text">{metricValue}</div></div>;
}
