'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Activity,
  BarChart3,
  BrainCircuit,
  BookOpen,
  CalendarDays,
  CalendarClock,
  Database,
  Flame,
  Layers3,
  Loader2,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
  TrendingDown,
  TrendingUp,
  Users,
  Wallet,
  type LucideIcon,
} from 'lucide-react';
import AddToPersonalPoolButton from '@/components/AddToPersonalPoolButton';
import StockKlineButton from '@/components/StockKlineButton';
import { apiFetch } from '@/lib/api';

type AnyMap = Record<string, any>;
type Period = 'week' | 'month' | 'quarter' | 'half_year';
type BoardType = 'all' | 'industry' | 'concept' | 'selected';

interface RankingRow {
  rank: number;
  code: string;
  name: string;
  board_type: string;
  strength_score: number | null;
  period_return_pct: number | null;
  main_net_inflow: number | null;
  positive_flow_ratio: number | null;
  breadth_pct: number | null;
  flow_sessions: number;
  session_count: number;
  primary_factors: string[];
  source: string;
}

interface BoardSelection {
  code: string;
  name: string;
  rank?: number;
  board_type?: string;
  strength_score?: number | null;
  period_return_pct?: number | null;
  main_net_inflow?: number | null;
  primary_factors?: string[];
  source?: string;
}

interface DataSection {
  available: boolean;
  rows: AnyMap[];
  count: number;
  source: string;
  data_date: string | null;
  updated_at: string | null;
  is_realtime: boolean;
  cache_hit: boolean;
  error?: string | null;
}

interface WorkspacePayload {
  available: boolean;
  period: Period;
  period_sessions: number;
  board_type: BoardType;
  updated_at: string;
  data_date: string | null;
  is_realtime: boolean;
  cache_hit: boolean;
  partial_cache_hit?: boolean;
  source: string;
  rankings: RankingRow[];
  sections: {
    selected_boards: DataSection;
    hot_search: DataSection;
    auction: DataSection;
    main_net: DataSection;
    strongest_fengkou: DataSection;
    theme_library: DataSection;
    theme_reasons: DataSection;
    market_stats: DataSection & { style: AnyMap[]; statistics: AnyMap[] };
  };
  quality: { provider_configured: boolean; coverage: number; errors: string[]; period_definition: string };
}

interface StockPayload {
  available: boolean;
  board_code: string;
  period: Period;
  rows: AnyMap[];
  count: number;
  source: string;
  data_date: string | null;
  is_realtime: boolean;
  cache_hit: boolean;
  errors: string[];
}

interface LegacyDateOption {
  date: string;
  limit_up_count: number | null;
  failed_limit_count: number | null;
  stock_count: number | null;
}

interface LegacyAnalysis {
  available: boolean;
  data_date: string;
  report: string;
  ai_generated: boolean;
}

interface LegacyDailyProps {
  snapshot: AnyMap | null;
  dates: LegacyDateOption[];
  selectedDate: string;
  setSelectedDate: (value: string) => void;
  loading: boolean;
  analyzing: boolean;
  analysis: LegacyAnalysis | null;
  onLoad: (date?: string) => Promise<void>;
  onAnalyze: () => Promise<void>;
  onShowPeriod: () => void;
}

const PERIODS: Array<{ key: Period; label: string; sessions: string }> = [
  { key: 'week', label: '周', sessions: '5日' },
  { key: 'month', label: '月', sessions: '20日' },
  { key: 'quarter', label: '季度', sessions: '60日' },
  { key: 'half_year', label: '半年', sessions: '120日' },
];
const BOARD_TYPES: Array<{ key: BoardType; label: string }> = [
  { key: 'all', label: '全部板块' },
  { key: 'industry', label: '行业' },
  { key: 'concept', label: '概念' },
  { key: 'selected', label: '精选板块' },
];

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function pct(value: unknown, digits = 2): string {
  return finite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%` : '--';
}

function money(value: unknown): string {
  if (!finite(value)) return '--';
  const absolute = Math.abs(value);
  const text = absolute >= 1e8 ? `${(value / 1e8).toFixed(2)}亿` : absolute >= 1e4 ? `${(value / 1e4).toFixed(1)}万` : `${value.toFixed(0)}`;
  return value > 0 ? `+${text}` : text;
}

function tone(value: unknown): string {
  if (!finite(value) || value === 0) return 'text-text-secondary';
  return value > 0 ? 'text-up' : 'text-down';
}

function value(valueToFormat: unknown, digits = 1): string {
  return finite(valueToFormat) ? valueToFormat.toFixed(digits) : '--';
}

function sourceLabel(source: string | undefined, realtime?: boolean, cache?: boolean): string {
  const sourceText = source?.includes('numcat') && source?.includes('database') ? '猫爪+数据库' : source?.includes('numcat') ? '猫爪' : source?.includes('database') ? '数据库缓存' : source || '暂无来源';
  return `${sourceText} · ${realtime ? '实时' : cache ? '缓存' : '收盘/历史'}`;
}

function SectionMeta({ section }: { section: DataSection | undefined }) {
  if (!section) return null;
  return <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-text-secondary"><span>{sourceLabel(section.source, section.is_realtime, section.cache_hit)}</span><span>数据日 {section.data_date || '--'}</span>{section.error && <span className="text-warn" title={section.error}>部分接口不可用，已保留可用数据</span>}</div>;
}

function Panel({ title, icon: Icon, section, children, className = '' }: { title: string; icon: LucideIcon; section?: DataSection; children: ReactNode; className?: string }) {
  return <section className={`min-w-0 border border-border bg-card ${className}`}>
    <header className="flex min-w-0 items-start justify-between gap-3 border-b border-border px-3 py-3 sm:px-4">
      <div className="min-w-0"><h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Icon size={15} className="shrink-0 text-accent" />{title}</h2><SectionMeta section={section} /></div>
      {section && <span className="shrink-0 font-mono text-[11px] text-text-secondary">{section.count ?? 0}</span>}
    </header>
    <div className="min-w-0">{children}</div>
  </section>;
}

function EmptySection({ section }: { section?: DataSection }) {
  return <div className="px-4 py-8 text-center text-xs text-text-secondary">{section?.error ? '当前接口未返回有效数据，页面不会用零值替代。' : '当前暂无可核验数据。'}</div>;
}

function CompactTable<T>({ columns, rows, renderRow }: { columns: string[]; rows: T[]; renderRow: (row: T, index: number) => ReactNode }) {
  if (!rows.length) return <EmptySection />;
  return <div className="overflow-x-auto"><table className="w-full min-w-[560px] border-collapse text-xs"><thead><tr className="border-b border-border text-left text-[10px] text-text-secondary">{columns.map((column) => <th key={column} className="whitespace-nowrap px-3 py-2 font-normal">{column}</th>)}</tr></thead><tbody>{rows.map((row, index) => renderRow(row, index))}</tbody></table></div>;
}

function LegacyDailyWorkspace({ snapshot, dates, selectedDate, setSelectedDate, loading, analyzing, analysis, onLoad, onAnalyze, onShowPeriod }: LegacyDailyProps) {
  const topics: AnyMap[] = snapshot?.topics || [];
  const market = snapshot?.market || {};
  const emotion = market.emotion || {};
  const sentiment = market.sentiment || {};
  return <main className="mx-auto w-full min-w-0 max-w-[1540px] px-3 py-4 sm:px-4 sm:py-6">
    <header className="mb-4 flex flex-col gap-4 border-b border-border pb-4 lg:flex-row lg:items-end lg:justify-between">
      <div><h1 className="flex items-center gap-2 text-xl font-semibold text-text"><Flame size={21} className="text-accent" />题材强弱研究工作台</h1><p className="mt-1 text-xs text-text-secondary">今日题材 · 龙头成员 · 连板联动 · 过热审计 · 八步分析</p><div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-text-secondary"><span>数据日 {snapshot?.data_date || '--'}</span><span>{sourceLabel(snapshot?.source, snapshot?.is_realtime, snapshot?.cache_hit)}</span><span>更新时间 {snapshot?.updated || snapshot?.updated_at?.slice(0, 16).replace('T', ' ') || '--'}</span></div></div>
      <div className="flex w-full flex-wrap items-center gap-2 lg:w-auto"><div className="flex rounded-md border border-border bg-card p-1"><button type="button" className="h-8 rounded bg-accent/15 px-3 text-xs text-accent">今日题材</button><button type="button" onClick={onShowPeriod} className="h-8 rounded px-3 text-xs text-text-secondary hover:text-text">周期强度与资金</button></div><label className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-md border border-border bg-bg px-2.5 text-xs text-text-secondary sm:flex-none"><CalendarDays size={14} className="shrink-0" /><select value={selectedDate} onChange={(event) => { setSelectedDate(event.target.value); void onLoad(event.target.value); }} className="w-full min-w-0 bg-transparent text-text outline-none sm:min-w-[145px]" aria-label="题材强弱交易日"><option value="">最近有效日</option>{dates.map((item) => <option key={item.date} value={item.date}>{item.date} · 涨停{item.limit_up_count ?? '--'}</option>)}</select></label><button type="button" onClick={() => void onLoad(selectedDate)} disabled={loading} className="grid h-9 w-9 place-items-center rounded-md border border-border text-text-secondary hover:border-accent hover:text-accent" title="刷新今日题材"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} /></button><button type="button" onClick={() => void onAnalyze()} disabled={!snapshot?.available || analyzing} className="inline-flex h-9 items-center gap-1.5 rounded-md border border-accent/50 px-3 text-xs text-accent disabled:opacity-50"><BrainCircuit size={14} className={analyzing ? 'animate-pulse' : ''} />{analyzing ? '分析中' : 'AI分析'}</button></div>
    </header>
    {loading && !snapshot ? <div className="grid min-h-[55vh] place-items-center text-xs text-text-secondary"><span className="inline-flex items-center gap-2"><Loader2 size={16} className="animate-spin text-accent" />正在恢复日级题材、成员和审计数据</span></div> : !snapshot?.available ? <div className="grid min-h-64 place-items-center border border-border text-sm text-text-secondary">当前交易日没有可核验的题材数据</div> : <>
      <section className="mb-4 grid grid-cols-2 gap-px border border-border bg-border md:grid-cols-3 xl:grid-cols-6">
        {[['上涨', sentiment.up == null ? '--' : `${sentiment.up}只`, 'text-up', TrendingUp], ['下跌', sentiment.down == null ? '--' : `${sentiment.down}只`, 'text-down', TrendingDown], ['市场宽度', sentiment.breadth || '--', 'text-text', Users], ['涨停', emotion.zt_count == null ? '--' : `${emotion.zt_count}只`, 'text-up', Flame], ['炸板', emotion.zb_count == null ? '--' : `${emotion.zb_count}只`, 'text-warn', ShieldAlert], ['强题材', `${topics.filter((item) => item.status === '强').length}个`, 'text-accent', Sparkles]].map(([label, metric, className, Icon]: any) => <div key={label} className="min-w-0 bg-card px-3 py-3"><div className="flex items-center gap-1.5 text-[11px] text-text-secondary"><Icon size={13} />{label}</div><div className={`mt-1.5 font-mono text-lg font-semibold ${className}`}>{metric}</div>{label === '炸板' && <div className="mt-1 text-[10px] text-text-secondary">炸板率 {pct(emotion.break_rate)}</div>}</div>)}
      </section>
      <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(310px,.75fr)]"><section className="min-w-0"><div className="mb-2 flex items-center justify-between"><h2 className="text-sm font-semibold text-text">日级题材强度排序</h2><span className="text-[10px] text-text-secondary">{topics.length} 组 · 点击股票可看 K 线</span></div><div className="space-y-3">{topics.map((topic: AnyMap, topicIndex: number) => <article key={`${topic.name}-${topicIndex}`} className="min-w-0 overflow-hidden rounded-md border border-border bg-card"><header className="flex flex-col gap-3 border-b border-border px-3 py-3 md:flex-row md:items-center"><div className="flex min-w-0 items-center gap-3"><span className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-border font-mono text-xs text-text-secondary">{topic.rank || topicIndex + 1}</span><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-text">{topic.name}</h3><span className={`border px-1.5 py-0.5 text-[10px] ${topic.status === '强' ? 'border-up/40 text-up' : 'border-warn/40 text-warn'}`}>{topic.status || '观察'}</span><span className="border border-border px-1.5 py-0.5 text-[10px] text-text-secondary">{topic.novelty || '待核验'}</span></div><p className="mt-1 text-[11px] leading-5 text-text-secondary">{topic.evidence || '等待更多板块成员与资金证据'}</p></div></div><div className="grid grid-cols-4 gap-px border border-border bg-border md:ml-auto md:min-w-[350px]">{[['强度', value(topic.strength_score)], ['涨停联动', `${topic.member_count ?? 0}只`], ['上涨宽度', pct(topic.breadth)], ['资金排名', topic.sector_flow_rank == null ? '--' : `#${topic.sector_flow_rank}`]].map(([label, metric]) => <div key={label} className="bg-bg px-2 py-2 text-center"><div className="text-[9px] text-text-secondary">{label}</div><div className="mt-1 font-mono text-xs text-text">{metric}</div></div>)}</div></header><div className="overflow-x-auto touch-pan-x" tabIndex={0}><table className="w-full min-w-[800px] text-xs"><thead className="bg-bg text-text-secondary"><tr><th className="px-3 py-2 text-left font-normal">观察标的</th><th className="px-3 py-2 text-right font-normal">连板</th><th className="px-3 py-2 text-right font-normal">涨幅</th><th className="px-3 py-2 text-right font-normal">成交额</th><th className="px-3 py-2 text-right font-normal">5日涨幅</th><th className="px-3 py-2 text-left font-normal">过热审计</th><th className="px-3 py-2 text-right font-normal">操作</th></tr></thead><tbody>{(topic.members || []).map((stock: AnyMap, index: number) => <tr key={`${stock.code}-${index}`} className="border-t border-border/60"><td className="px-3 py-2.5"><StockKlineButton code={stock.code} name={stock.name} className="font-medium text-text hover:text-accent">{stock.name}<span className="ml-2 font-mono text-[10px] text-text-secondary">{stock.code}</span></StockKlineButton>{index === 0 && <span className="ml-2 text-[10px] text-accent">核心</span>}</td><td className={`px-3 py-2.5 text-right font-mono ${stock.boards_verified ? 'text-up' : 'text-warn'}`}>{stock.boards_verified ? stock.boards : '待核验'}</td><td className={`px-3 py-2.5 text-right font-mono ${tone(stock.pct)}`}>{pct(stock.pct)}</td><td className="px-3 py-2.5 text-right font-mono text-text-secondary">{money(stock.amount)}</td><td className={`px-3 py-2.5 text-right font-mono ${tone(stock.return_5d_pct)}`}>{pct(stock.return_5d_pct)}</td><td className="px-3 py-2.5"><span className={stock.heat_status === '过热' ? 'text-down' : stock.heat_status === '待核验' ? 'text-warn' : 'text-up'}>{stock.heat_status || '待核验'}</span>{stock.data_gaps?.length > 0 && <div className="mt-0.5 text-[9px] text-text-secondary">缺 {stock.data_gaps.join('、')}</div>}</td><td className="px-3 py-2.5 text-right"><AddToPersonalPoolButton code={stock.code} name={stock.name} industry={stock.industry} thesis={`题材强弱：${topic.evidence || topic.name}`} source="topic_strength" compact /></td></tr>)}</tbody></table></div></article>)}</div></section>
        <aside className="min-w-0 xl:border-l xl:border-border xl:pl-4"><h2 className="mb-2 text-sm font-semibold text-text">八步分析链路</h2><div className="border-y border-border">{(snapshot.steps || []).map((step: AnyMap) => <div key={step.step} className="grid grid-cols-[26px_minmax(0,1fr)] gap-2 border-b border-border/60 py-2.5 last:border-b-0"><span className="grid h-6 w-6 place-items-center border border-border font-mono text-[10px] text-text-secondary">{step.step}</span><div><div className="flex flex-wrap items-center gap-2 text-xs text-text"><span>{step.title}</span>{step.classification && <span className="border border-border px-1 py-0.5 text-[9px] text-text-secondary">{step.classification}</span>}</div><p className="mt-1 text-[11px] leading-5 text-text-secondary">{step.result}</p></div></div>)}</div><h2 className="mb-2 mt-5 text-sm font-semibold text-text">行业资金前十</h2><div className="border-y border-border">{(market.top_sectors || []).slice(0, 10).map((sector: AnyMap, index: number) => <div key={sector.code || index} className="grid grid-cols-[22px_minmax(0,1fr)_60px_78px] gap-2 border-b border-border/60 py-2 text-xs last:border-0"><span className="font-mono text-text-secondary">{sector.rank || index + 1}</span><span className="truncate text-text">{sector.name}</span><span className={`text-right font-mono ${tone(sector.change_pct)}`}>{pct(sector.change_pct)}</span><span className={`text-right font-mono ${tone(sector.main_net_inflow)}`}>{money(sector.main_net_inflow)}</span></div>)}</div></aside></div>
      {(snapshot.risk?.length || snapshot.data_quality?.missing_fields?.length) > 0 && <section className="mt-4 border-y border-warn/35 bg-warn/5 px-3 py-3"><h2 className="flex items-center gap-2 text-xs font-semibold text-text"><ShieldAlert size={14} className="text-warn" />风险与数据边界</h2><div className="mt-2 grid gap-1 text-[11px] leading-5 text-text-secondary md:grid-cols-2">{(snapshot.risk || []).map((item: string) => <span key={item}>• {item}</span>)}{(snapshot.data_quality?.missing_fields || []).map((item: string) => <span key={item} className="text-warn">• 待核验：{item}</span>)}</div></section>}
      {analysis && <section className="mt-4 rounded-md border border-border bg-card px-4 py-4"><div className="mb-3 flex flex-wrap items-center gap-2 text-sm font-semibold text-text"><BrainCircuit size={15} className="text-accent" />AI题材分析<span className="text-[10px] font-normal text-text-secondary">{analysis.ai_generated ? '模型生成' : '规则底稿'} · {analysis.data_date}</span></div><div className="max-w-full overflow-x-auto text-xs leading-6 text-text-secondary prose prose-invert prose-headings:text-text prose-strong:text-text [&_table]:min-w-[620px]"><ReactMarkdown remarkPlugins={[remarkGfm]}>{analysis.report}</ReactMarkdown></div></section>}
    </>}
  </main>;
}

export default function TopicStrengthPage() {
  const [view, setView] = useState<'daily' | 'period'>('daily');
  const [period, setPeriod] = useState<Period>('week');
  const [boardType, setBoardType] = useState<BoardType>('all');
  const [data, setData] = useState<WorkspacePayload | null>(null);
  const [stocks, setStocks] = useState<StockPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [progress, setProgress] = useState(8);
  const [refreshing, setRefreshing] = useState(false);
  const [stockLoading, setStockLoading] = useState(false);
  const [error, setError] = useState('');
  const [selectedBoard, setSelectedBoard] = useState<BoardSelection | null>(null);
  const [statsTab, setStatsTab] = useState<'style' | 'statistics'>('style');
  const requestId = useRef(0);
  const stockRequestId = useRef(0);
  const autoSelectedBoard = useRef('');
  const [legacyDates, setLegacyDates] = useState<LegacyDateOption[]>([]);
  const [selectedDate, setSelectedDate] = useState('');
  const [legacySnapshot, setLegacySnapshot] = useState<AnyMap | null>(null);
  const [legacyAnalysis, setLegacyAnalysis] = useState<LegacyAnalysis | null>(null);
  const [legacyLoading, setLegacyLoading] = useState(false);
  const [legacyAnalyzing, setLegacyAnalyzing] = useState(false);

  const load = useCallback(async (nextPeriod: Period, nextBoardType: BoardType, refresh = false) => {
    const currentRequest = ++requestId.current;
    setLoading(!refresh);
    setRefreshing(refresh);
    setProgress(8);
    setError('');
    setStocks(null);
    if (refresh === false) setSelectedBoard(null);
    let timer: number | undefined;
    try {
      timer = window.setInterval(() => setProgress((current) => Math.min(current + 7, 88)), 260);
      const query = new URLSearchParams({ period: nextPeriod, board_type: nextBoardType, limit: '30' });
      if (refresh) query.set('refresh', 'true');
      const response = await apiFetch<{ data: WorkspacePayload }>(`/topic-workspace?${query.toString()}`, { timeoutMs: 50000 });
      if (currentRequest !== requestId.current) return;
      setProgress(100);
      setData(response.data);
    } catch (caught) {
      if (currentRequest === requestId.current) {
        setError(caught instanceof Error ? caught.message : '统一题材工作台读取失败');
      }
    } finally {
      if (timer !== undefined) window.clearInterval(timer);
      if (currentRequest === requestId.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  const loadLegacyDates = useCallback(async () => {
    try {
      const response = await apiFetch<{ data: { dates: LegacyDateOption[] } }>('/topic-strength/dates?limit=180');
      setLegacyDates(response.data.dates || []);
    } catch {
      setLegacyDates([]);
    }
  }, []);

  const loadLegacySnapshot = async (targetDate = '') => {
    setLegacyLoading(true);
    setLegacyAnalysis(null);
    setError('');
    try {
      const query = targetDate ? `?date=${encodeURIComponent(targetDate)}` : '';
      const response = await apiFetch<{ data: AnyMap }>(`/topic-strength${query}`, { timeoutMs: 50000 });
      setLegacySnapshot(response.data);
      setSelectedDate(String(response.data?.data_date || targetDate || ''));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '日级题材核验失败');
    } finally {
      setLegacyLoading(false);
    }
  };

  const runLegacyAnalysis = async () => {
    if (!legacySnapshot?.available) return;
    setLegacyAnalyzing(true);
    setError('');
    try {
      const response = await apiFetch<{ data: LegacyAnalysis }>('/topic-strength/analysis', {
        method: 'POST',
        body: JSON.stringify({ date: selectedDate || undefined, use_ai: true }),
        timeoutMs: 50000,
      });
      setLegacyAnalysis(response.data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'AI题材分析失败');
    } finally {
      setLegacyAnalyzing(false);
    }
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedPeriod = params.get('period');
    const requestedBoardType = params.get('board_type');
    const initialPeriod = PERIODS.some((item) => item.key === requestedPeriod) ? requestedPeriod as Period : period;
    const initialBoardType = BOARD_TYPES.some((item) => item.key === requestedBoardType) ? requestedBoardType as BoardType : boardType;
    if (params.get('view') === 'period' || params.get('board')) setView('period');
    setPeriod(initialPeriod);
    setBoardType(initialBoardType);
    void Promise.all([load(initialPeriod, initialBoardType), loadLegacyDates(), loadLegacySnapshot()]);
  }, [load, loadLegacyDates]);

  const selectBoard = async (row: BoardSelection) => {
    const currentRequest = ++stockRequestId.current;
    setSelectedBoard(row);
    setStockLoading(true);
    setError('');
    try {
      const response = await apiFetch<{ data: StockPayload }>(`/topic-workspace/boards/${encodeURIComponent(row.code)}/stocks?period=${period}&limit=80`, { timeoutMs: 50000 });
      if (currentRequest !== stockRequestId.current) return;
      setStocks(response.data);
    } catch (caught) {
      if (currentRequest !== stockRequestId.current) return;
      setError(caught instanceof Error ? caught.message : '板块成分读取失败');
      setStocks(null);
    } finally {
      if (currentRequest === stockRequestId.current) setStockLoading(false);
    }
  };

  useEffect(() => {
    const requestedCode = new URLSearchParams(window.location.search).get('board') || '';
    if (!requestedCode || !data || autoSelectedBoard.current === requestedCode) return;
    const matched = (data.rankings || []).find((item) => item.code === requestedCode);
    if (!matched) return;
    autoSelectedBoard.current = requestedCode;
    void selectBoard(matched);
  }, [data]);

  const rankings = useMemo(() => data?.rankings || [], [data]);
  const sections = data?.sections;
  const chosenPeriod = PERIODS.find((item) => item.key === period);

  if (view === 'daily') {
    return <LegacyDailyWorkspace
      snapshot={legacySnapshot}
      dates={legacyDates}
      selectedDate={selectedDate}
      setSelectedDate={setSelectedDate}
      loading={legacyLoading}
      analyzing={legacyAnalyzing}
      analysis={legacyAnalysis}
      onLoad={loadLegacySnapshot}
      onAnalyze={runLegacyAnalysis}
      onShowPeriod={() => setView('period')}
    />;
  }

  if (loading && !data) {
    return <main className="mx-auto grid min-h-[70vh] max-w-[1480px] place-items-center px-4"><div className="w-full max-w-md text-center"><Loader2 size={27} className="mx-auto animate-spin text-accent" /><div className="mt-3 text-sm text-text">正在汇总题材研究数据</div><div className="mt-1 text-xs text-text-secondary">周期排名、精选板块、竞价、资金、热搜与市场统计</div><div className="mt-5 h-1.5 overflow-hidden rounded-full bg-border"><div className="h-full bg-accent transition-[width] duration-200" style={{ width: `${progress}%` }} /></div><div className="mt-2 font-mono text-xs text-accent">{progress}%</div></div></main>;
  }

  return <main className="mx-auto w-full min-w-0 max-w-[1540px] px-3 py-4 sm:px-4 sm:py-6">
    <header className="mb-4 flex flex-col gap-4 border-b border-border pb-4 lg:flex-row lg:items-end lg:justify-between">
      <div className="min-w-0"><h1 className="flex items-center gap-2 text-xl font-semibold text-text"><Flame size={21} className="text-accent" />题材强弱研究工作台</h1><p className="mt-1 text-xs text-text-secondary">周期强度 · 板块竞价 · 资金迁徙 · 热点题材 · 个股排名</p><div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-text-secondary"><span>数据日 {data?.data_date || '--'}</span><span>{sourceLabel(data?.source, data?.is_realtime, data?.cache_hit)}</span><span>覆盖 {value(data?.quality?.coverage, 0)}%</span></div></div>
      <div className="flex flex-wrap items-center gap-2"><button type="button" onClick={() => setView('daily')} className="h-9 rounded-md border border-border px-3 text-xs text-text-secondary hover:border-accent hover:text-accent">今日题材</button><div className="flex max-w-full overflow-x-auto rounded-md border border-border bg-bg p-1">{PERIODS.map((item) => <button key={item.key} type="button" onClick={() => { setPeriod(item.key); void load(item.key, boardType); }} className={`h-8 shrink-0 rounded px-3 text-xs ${period === item.key ? 'bg-[#1F6FEB33] text-accent' : 'text-text-secondary hover:text-text'}`}>{item.label}<span className="ml-1 text-[10px] opacity-70">{item.sessions}</span></button>)}</div><button type="button" onClick={() => void load(period, boardType, true)} disabled={refreshing} className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border px-3 text-xs text-text-secondary hover:border-accent hover:text-text disabled:opacity-50"><RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />刷新</button></div>
    </header>

    {error && <div className="mb-4 flex items-start gap-2 border border-up/40 bg-[#EF535010] p-3 text-xs text-up"><Activity size={14} className="mt-0.5 shrink-0" />{error}</div>}
    {data?.partial_cache_hit && <div className="mb-4 flex items-center gap-2 border border-warn/40 bg-[#D9A44110] p-3 text-xs text-warn"><Database size={14} className="shrink-0" />部分区块暂用最近成功缓存，页面未用零值替代；可点击刷新重新核验。</div>}
    {loading && data && <div className="mb-4 border-y border-border py-2.5"><div className="flex items-center justify-between text-[11px] text-text-secondary"><span>正在切换研究范围，旧数据仍保留</span><span className="font-mono text-accent">{progress}%</span></div><div className="mt-1.5 h-1 overflow-hidden bg-border"><div className="h-full bg-accent transition-[width]" style={{ width: `${progress}%` }} /></div></div>}
    {refreshing && <div className="mb-4 border-y border-border py-2.5"><div className="flex items-center justify-between text-[11px] text-text-secondary"><span>正在更新猫爪数据并保留可用缓存</span><span className="font-mono text-accent">{progress}%</span></div><div className="mt-1.5 h-1 overflow-hidden bg-border"><div className="h-full bg-accent transition-[width]" style={{ width: `${progress}%` }} /></div></div>}

    <section className="mb-4 grid min-w-0 gap-px border border-border bg-border sm:grid-cols-2 xl:grid-cols-4">
      {[['周期窗口', chosenPeriod ? `${chosenPeriod.label} / ${chosenPeriod.sessions}` : '--', data?.quality?.period_definition || '有效交易日'], ['板块样本', `${rankings.length}组`, `点击查看对应个股排名`], ['精选数据', `${sections?.selected_boards?.count || 0}组`, sourceLabel(sections?.selected_boards?.source, sections?.selected_boards?.is_realtime, sections?.selected_boards?.cache_hit)], ['数据状态', data?.is_realtime ? '盘中实时' : data?.cache_hit ? '最近缓存' : '收盘/历史', `更新 ${data?.updated_at?.slice(0, 16).replace('T', ' ') || '--'}`]].map(([label, main, sub]) => <div key={label} className="min-w-0 bg-card px-4 py-3"><div className="text-[10px] text-text-secondary">{label}</div><div className="mt-1 truncate font-mono text-lg font-semibold text-text">{main}</div><div className="mt-1 truncate text-[10px] text-text-secondary" title={sub}>{sub}</div></div>)}
    </section>

    <div className="mb-4 flex max-w-full overflow-x-auto rounded-md border border-border bg-card p-1">{BOARD_TYPES.map((item) => <button key={item.key} type="button" onClick={() => { setBoardType(item.key); void load(period, item.key); }} className={`h-8 shrink-0 rounded px-3 text-xs ${boardType === item.key ? 'bg-[#1F6FEB33] text-accent' : 'text-text-secondary hover:text-text'}`}>{item.label}</button>)}</div>

    <Panel title={`${chosenPeriod?.label || ''}期板块强度排名`} icon={BarChart3} className="mb-4">
      <CompactTable columns={['排名', '板块', '强度', '周期涨幅', '主力净额', '资金持续性', '上涨宽度', '主要因子']} rows={rankings} renderRow={(row: RankingRow, index) => <tr key={`${row.code}-${index}`} className={`cursor-pointer border-b border-border/60 hover:bg-[#1F6FEB12] ${selectedBoard?.code === row.code ? 'bg-[#1F6FEB18]' : ''}`} onClick={() => void selectBoard(row)}><td className="px-3 py-3 font-mono text-text-secondary">{row.rank || index + 1}</td><td className="whitespace-nowrap px-3 py-3"><div className="font-medium text-text">{row.name}</div><div className="mt-0.5 font-mono text-[10px] text-text-secondary">{row.code} · {row.board_type === 'industry' ? '行业' : row.board_type === 'concept' ? '概念' : '精选'}</div></td><td className="px-3 py-3 font-mono font-semibold text-accent">{value(row.strength_score)}</td><td className={`px-3 py-3 font-mono ${tone(row.period_return_pct)}`}>{pct(row.period_return_pct)}</td><td className={`px-3 py-3 font-mono ${tone(row.main_net_inflow)}`}>{money(row.main_net_inflow)}</td><td className="px-3 py-3 font-mono text-text-secondary">{pct(row.positive_flow_ratio, 1)}</td><td className="px-3 py-3 font-mono text-text-secondary">{pct(row.breadth_pct, 1)}</td><td className="max-w-[220px] px-3 py-3 text-text-secondary">{row.primary_factors?.join(' · ') || '--'}</td></tr>} />
      {data && <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-3 py-2 text-[10px] text-text-secondary"><span>强度算法：周期涨幅35% · 资金30% · 持续性15% · 上涨宽度20%</span><span>数据源：{sourceLabel(data.source, data.is_realtime, data.cache_hit)}</span></div>}
    </Panel>

    {selectedBoard && <Panel title={`${selectedBoard.name} · 个股排名`} icon={Layers3} className="mb-4"><div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2.5 text-[11px] text-text-secondary"><span>板块编码 {selectedBoard.code} · {chosenPeriod?.label}期</span>{stockLoading && <span className="inline-flex items-center gap-1 text-accent"><Loader2 size={12} className="animate-spin" />正在读取个股</span>}{stocks && <span>{sourceLabel(stocks.source, stocks.is_realtime, stocks.cache_hit)} · 数据日 {stocks.data_date || '--'}</span>}</div>{stocks ? <CompactTable columns={['排名', '股票', '涨跌幅', '主力净额', '量比', '换手', '标签', '操作']} rows={stocks.rows} renderRow={(row: AnyMap, index) => <tr key={`${row.code}-${index}`} className="border-b border-border/60"><td className="px-3 py-3 font-mono text-text-secondary">{row.rank || index + 1}</td><td className="whitespace-nowrap px-3 py-3"><div className="font-medium text-text">{row.name}</div><div className="font-mono text-[10px] text-text-secondary">{row.code}</div></td><td className={`px-3 py-3 font-mono ${tone(row.change_pct)}`}>{pct(row.change_pct)}</td><td className={`px-3 py-3 font-mono ${tone(row.main_net_amount)}`}>{money(row.main_net_amount)}</td><td className="px-3 py-3 font-mono text-text-secondary">{value(row.volume_ratio, 2)}</td><td className="px-3 py-3 font-mono text-text-secondary">{pct(row.turnover, 1)}</td><td className="whitespace-nowrap px-3 py-3 text-[10px]">{row.is_authentic && <span className="mr-1 border border-accent/40 px-1.5 py-0.5 text-accent">最正宗</span>}{row.is_dragon_ranked && <span className="border border-warn/40 px-1.5 py-0.5 text-warn">龙排名</span>}</td><td className="whitespace-nowrap px-3 py-3"><div className="flex items-center gap-2"><AddToPersonalPoolButton code={row.code} name={row.name} industry={selectedBoard.name} thesis={`${selectedBoard.name} ${chosenPeriod?.label || ''}期板块强度排名`} source="topic_workspace" compact /><StockKlineButton code={row.code} name={row.name} /></div></td></tr>} /> : <div className="px-4 py-8 text-center text-xs text-text-secondary">点击板块排名查看个股。</div>}</Panel>}

    <div className="grid min-w-0 gap-4 xl:grid-cols-2">
      <Panel title="精选板块" icon={Layers3} section={sections?.selected_boards}><CompactTable columns={['板块', '涨跌幅', '强度', '成员', '精选标签']} rows={(sections?.selected_boards?.rows || []).slice(0, 12)} renderRow={(row: AnyMap, index) => <tr key={`${row.code}-${index}`} className="cursor-pointer border-b border-border/60 hover:bg-[#1F6FEB12]" onClick={() => void selectBoard({ code: String(row.code || ''), name: String(row.name || row.code || ''), rank: index + 1, strength_score: row.strength, period_return_pct: row.change_pct, main_net_inflow: row.main_net_inflow, board_type: 'selected', primary_factors: [], source: row.source })}><td className="px-3 py-2.5"><div className="font-medium text-text">{row.name}</div><div className="font-mono text-[10px] text-text-secondary">{row.code}</div></td><td className={`px-3 py-2.5 font-mono ${tone(row.change_pct)}`}>{pct(row.change_pct)}</td><td className="px-3 py-2.5 font-mono text-accent">{value(row.strength)}</td><td className="px-3 py-2.5 font-mono text-text-secondary">{row.member_count ?? '--'}</td><td className="px-3 py-2.5 text-[10px] text-text-secondary">{row.authentic_codes?.length ? '最正宗' : ''}{row.long_codes?.length ? ' · 龙排名' : ''}</td></tr>} /></Panel>
      <Panel title="实时热搜" icon={Search} section={sections?.hot_search}><CompactTable columns={['排名', '股票', '涨跌幅', '榜单']} rows={(sections?.hot_search?.rows || []).slice(0, 12)} renderRow={(row: AnyMap, index) => <tr key={`${row.code}-${index}`} className="border-b border-border/60"><td className="px-3 py-2.5 font-mono text-text-secondary">{row.rank || index + 1}</td><td className="px-3 py-2.5"><span className="text-text">{row.name || row.code}</span><span className="ml-2 font-mono text-[10px] text-text-secondary">{row.code}</span></td><td className={`px-3 py-2.5 font-mono ${tone(row.change_pct)}`}>{pct(row.change_pct)}</td><td className="px-3 py-2.5 text-[10px] text-text-secondary">{row.type || '热搜'}</td></tr>} /></Panel>
      <Panel title="板块竞价" icon={CalendarClock} section={sections?.auction}><CompactTable columns={['分组', '板块', '竞价放量', '异常金额', '竞价额', '主力净额']} rows={(sections?.auction?.rows || []).slice(0, 12)} renderRow={(row: AnyMap, index) => <tr key={`${row.theme_symbol}-${index}`} className="border-b border-border/60"><td className="px-3 py-2.5 text-[10px] text-accent">{row.group || '--'}</td><td className="px-3 py-2.5 text-text">{row.theme_name || row.theme_symbol}</td><td className="px-3 py-2.5 font-mono text-text-secondary">{value(row.bid_volume_burst, 2)}</td><td className="px-3 py-2.5 font-mono text-text-secondary">{money(row.abnormal_amount)}</td><td className="px-3 py-2.5 font-mono text-text-secondary">{money(row.bid_volume)}</td><td className={`px-3 py-2.5 font-mono ${tone(row.main_net_amount)}`}>{money(row.main_net_amount)}</td></tr>} /></Panel>
      <Panel title="主力净额" icon={Wallet} section={sections?.main_net}><CompactTable columns={['板块', '主力净额', '主力买入', '主力卖出', '时间']} rows={(sections?.main_net?.rows || []).slice(0, 12)} renderRow={(row: AnyMap, index) => <tr key={`${row.theme_symbol || row.symbol}-${index}`} className="border-b border-border/60"><td className="px-3 py-2.5 text-text">{row.theme_name || row.theme_symbol || row.name || '--'}</td><td className={`px-3 py-2.5 font-mono ${tone(row.main_net_amount)}`}>{money(row.main_net_amount)}</td><td className="px-3 py-2.5 font-mono text-text-secondary">{money(row.main_buy_amount)}</td><td className="px-3 py-2.5 font-mono text-text-secondary">{money(row.main_sell_amount)}</td><td className="px-3 py-2.5 font-mono text-[10px] text-text-secondary">{row.trademin || row.trade_date || '--'}</td></tr>} /></Panel>
      <Panel title="最强风口" icon={TrendingUp} section={sections?.strongest_fengkou}><CompactTable columns={['排名', '股票', '强度', '涨跌幅', '主力净额', '关联题材']} rows={(sections?.strongest_fengkou?.rows || []).slice(0, 12)} renderRow={(row: AnyMap, index) => <tr key={`${row.code}-${index}`} className="border-b border-border/60"><td className="px-3 py-2.5 font-mono text-text-secondary">{row.rank || index + 1}</td><td className="px-3 py-2.5"><span className="text-text">{row.name}</span><span className="ml-2 font-mono text-[10px] text-text-secondary">{row.code}</span></td><td className="px-3 py-2.5 font-mono text-accent">{value(row.strength)}</td><td className={`px-3 py-2.5 font-mono ${tone(row.change_pct)}`}>{pct(row.change_pct)}</td><td className={`px-3 py-2.5 font-mono ${tone(row.main_net_amount)}`}>{money(row.main_net_amount)}</td><td className="max-w-[180px] truncate px-3 py-2.5 text-[10px] text-text-secondary" title={String(row.selected_themes || '')}>{row.selected_themes || '--'}</td></tr>} /></Panel>
      <Panel title="板块原因" icon={BookOpen} section={sections?.theme_reasons}><CompactTable columns={['日期', '板块', '来源', '原因']} rows={(sections?.theme_reasons?.rows || []).slice(0, 12)} renderRow={(row: AnyMap, index) => <tr key={`${row.theme_symbol}-${index}`} className="border-b border-border/60 align-top"><td className="whitespace-nowrap px-3 py-2.5 font-mono text-[10px] text-text-secondary">{row.trade_date || '--'}</td><td className="whitespace-nowrap px-3 py-2.5 text-text">{row.name || row.theme_symbol || '--'}</td><td className="whitespace-nowrap px-3 py-2.5 text-[10px] text-accent">{row.reason_source || '--'}</td><td className="max-w-[360px] px-3 py-2.5 leading-5 text-text-secondary">{row.reason || '--'}</td></tr>} /></Panel>
    </div>

    <div className="mt-4 grid min-w-0 gap-4 xl:grid-cols-[1fr_1.4fr]">
      <Panel title="题材库" icon={BookOpen} section={sections?.theme_library}><CompactTable columns={['题材ID', '题材名称']} rows={(sections?.theme_library?.rows || []).slice(0, 18)} renderRow={(row: AnyMap, index) => <tr key={`${row.theme_id}-${index}`} className="border-b border-border/60"><td className="px-3 py-2.5 font-mono text-text-secondary">{row.theme_id}</td><td className="px-3 py-2.5 text-text">{row.name}</td></tr>} /></Panel>
      <Panel title="市场统计" icon={Activity} section={sections?.market_stats}><div className="flex gap-1 border-b border-border px-3 py-2"><button type="button" onClick={() => setStatsTab('style')} className={`rounded px-2.5 py-1.5 text-[11px] ${statsTab === 'style' ? 'bg-[#1F6FEB33] text-accent' : 'text-text-secondary'}`}>风格板块</button><button type="button" onClick={() => setStatsTab('statistics')} className={`rounded px-2.5 py-1.5 text-[11px] ${statsTab === 'statistics' ? 'bg-[#1F6FEB33] text-accent' : 'text-text-secondary'}`}>统计指数</button></div><CompactTable columns={['名称', '涨跌幅', '成交额', '快照时间']} rows={(statsTab === 'style' ? sections?.market_stats?.style : sections?.market_stats?.statistics) || []} renderRow={(row: AnyMap, index) => <tr key={`${row.symbol}-${index}`} className="border-b border-border/60"><td className="px-3 py-2.5"><span className="text-text">{row.name || '--'}</span><span className="ml-2 font-mono text-[10px] text-text-secondary">{row.symbol || '--'}</span></td><td className={`px-3 py-2.5 font-mono ${tone(row.pct_chg)}`}>{pct(row.pct_chg)}</td><td className="px-3 py-2.5 font-mono text-text-secondary">{money(row.amount)}</td><td className="px-3 py-2.5 font-mono text-[10px] text-text-secondary">{row.servertime || '--'}</td></tr>} /></Panel>
    </div>

    <footer className="mt-4 flex flex-wrap items-start gap-x-5 gap-y-2 border-t border-border pt-3 text-[10px] leading-5 text-text-secondary"><span className="inline-flex items-center gap-1"><Database size={12} />缓存策略：闭市优先读取最近成功快照，缓存不冒充实时</span><span>周期使用有效交易日：5 / 20 / 60 / 120</span><span>强度是研究排序，不构成买卖指令；单区块失败不会影响其他数据。</span></footer>
  </main>;
}
