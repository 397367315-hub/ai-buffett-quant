'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, BookOpen, Bot, BrainCircuit, CalendarDays, ChevronDown, Clock3, Loader2, MoonStar, RefreshCw, WalletCards } from 'lucide-react';
import AddToPersonalPoolButton from '@/components/AddToPersonalPoolButton';
import PersonalWorkspaceNav from '@/components/PersonalWorkspaceNav';
import StockKlineButton from '@/components/StockKlineButton';
import { apiFetch } from '@/lib/api';

type PoolType = 'short' | 'long';

interface RobotRun {
  id: number;
  pool_type: PoolType;
  status: string;
  progress: number;
  message: string;
  source_data_date: string | null;
  is_realtime: boolean;
  created_at: string | null;
  finished_at: string | null;
  summary: Record<string, any>;
}

interface RobotPick {
  id: number;
  pool_type: PoolType;
  code: string;
  name: string;
  sector_label: string;
  selected_price: number | null;
  selected_on: string | null;
  simulated_shares: number;
  cost_value: number | null;
  latest_price: number | null;
  market_value: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  price_status: 'waiting' | 'quote_unavailable' | 'available';
  change_pct: number | null;
  score: number | null;
  confidence: number | null;
  verdict: string;
  state: 'new' | 'retained';
  basis: string;
  holding_label: string;
  evidence: Array<{ agent: string; summary: string; evidence?: string[]; risks?: string[] }>;
}

interface PoolView {
  config: { label: string; holding_period: string; refresh_rule: string; criteria: string[] };
  run: RobotRun | null;
  sectors: Array<{ key: string; label: string; count: number; picks: RobotPick[] }>;
  picks: RobotPick[];
  performance: Performance;
  journal: RobotJournal | null;
  next_update: string;
}

interface RobotJournal {
  id: number;
  run_id: number | null;
  pool_type: PoolType;
  journal_date: string;
  source_data_date: string | null;
  is_realtime: boolean;
  action_summary: string;
  decision_reason: string;
  pnl_reflection: string;
  lessons: string;
  metrics: Record<string, any>;
  picks_snapshot: Array<{
    code?: string;
    name?: string;
    sector?: string;
    state?: string;
    score?: number | null;
    confidence?: number | null;
    selected_price?: number | null;
    latest_price?: number | null;
    pnl?: number | null;
    pnl_pct?: number | null;
    explanation?: {
      plain_reason?: string;
      key_facts?: string[];
      positive_evidence?: string[];
      risks?: string[];
      validation_conditions?: string[];
      invalidation_conditions?: string[];
      data_quality?: string;
    };
    [key: string]: any;
  }>;
}

interface Performance {
  positions: number;
  priced_positions: number;
  waiting_positions: number;
  quote_unavailable_positions: number;
  simulated_shares: number;
  cost_value: number | null;
  market_value: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  winners: number;
  losers: number;
}

interface Dashboard {
  updated_at: string;
  pools: Record<PoolType, PoolView>;
  active_runs: Record<PoolType, RobotRun | null>;
  combined_performance: Performance;
  quote: { available: boolean; source: string; data_date: string | null; is_realtime: boolean; complete: boolean; cache_used: boolean; stale: boolean };
  overnight?: {
    tag: string;
    schedule: string;
    run: { id: number; stage: string; status: string; message: string; data_date: string | null } | null;
    positions: OvernightPosition[];
    recent_closed: OvernightPosition[];
    performance: Performance & { open?: number; closed?: number; win_rate?: number | null };
    data_quality: Record<string, any>;
  };
  simulation_rule: string;
  disclaimer: string;
}

interface CalendarDay {
  date: string;
  pnl: number;
  pnl_pct: number | null;
  cost_value: number;
  market_value: number;
  status: 'profit' | 'loss' | 'flat';
  pools: Record<string, { pnl: number | null; pnl_pct: number | null; source_data_date: string | null }>;
  source_data_dates: string[];
  is_realtime: boolean;
}

interface PerformanceCalendar {
  from: string;
  to: string;
  pool_type: string;
  days: CalendarDay[];
  summary: {
    recorded_days: number;
    profit_days: number;
    loss_days: number;
    flat_days: number;
    total_pnl: number;
    total_pnl_pct: number | null;
    current_loss_streak: number;
    max_loss_streak: number;
  };
  methodology: string;
}

interface CalendarAnalysis {
  analysis: string;
  source: string;
  generated_at: string;
  calendar: PerformanceCalendar;
}

interface OvernightPosition {
  id: number;
  code: string;
  name: string;
  sector: string;
  status: 'open' | 'closed';
  shares: number;
  entry_at: string;
  entry_price: number;
  current_price: number | null;
  cost_value: number;
  pnl: number | null;
  pnl_pct: number | null;
  exit_reason: string | null;
}

const money = (value: number | null | undefined) => value == null ? '--' : `¥${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const number = (value: number | null | undefined, digits = 2) => value == null ? '--' : value.toFixed(digits);
const signed = (value: number | null | undefined) => value == null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
const pnlClass = (value: number | null | undefined) => value == null ? 'text-text-secondary' : value >= 0 ? 'text-up' : 'text-down';
const time = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '--';

export default function RobotPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [history, setHistory] = useState<RobotRun[]>([]);
  const [journals, setJournals] = useState<RobotJournal[]>([]);
  const [calendar, setCalendar] = useState<PerformanceCalendar | null>(null);
  const [calendarDay, setCalendarDay] = useState<{ date: string; journals: RobotJournal[]; available: boolean } | null>(null);
  const [calendarAnalysis, setCalendarAnalysis] = useState<CalendarAnalysis | null>(null);
  const [selectedJournalId, setSelectedJournalId] = useState<number | null>(null);
  const [poolType, setPoolType] = useState<PoolType>('short');
  const [loading, setLoading] = useState(true);
  const [loadProgress, setLoadProgress] = useState(8);
  const [error, setError] = useState<string | null>(null);
  const [triggering, setTriggering] = useState(false);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    setError(null);
    try {
      const [dashboard, runs, journalResponse, calendarResponse] = await Promise.allSettled([
        apiFetch<{ data: Dashboard }>('/personal/robot'),
        apiFetch<{ data: { runs: RobotRun[] } }>('/personal/robot/history?limit=12'),
        apiFetch<{ data: { journals: RobotJournal[] } }>('/personal/robot/journals?limit=30'),
        apiFetch<{ data: PerformanceCalendar }>('/personal/robot/calendar?days=180'),
      ]);
      if (dashboard.status === 'fulfilled') setData(dashboard.value.data);
      if (runs.status === 'fulfilled') setHistory(runs.value.data.runs || []);
      if (journalResponse.status === 'fulfilled') setJournals(journalResponse.value.data.journals || []);
      if (calendarResponse.status === 'fulfilled') setCalendar(calendarResponse.value.data);
      if (dashboard.status === 'rejected') throw dashboard.reason;
      setLoadProgress(100);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'AI机器人池加载失败');
    } finally {
      if (!quiet) window.setTimeout(() => setLoading(false), 120);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!loading) return;
    const timer = window.setInterval(() => setLoadProgress((value) => Math.min(92, value + 5)), 450);
    return () => window.clearInterval(timer);
  }, [loading]);

  const activeRun = data?.active_runs?.[poolType];
  useEffect(() => {
    if (!activeRun || !['queued', 'running'].includes(activeRun.status)) return;
    const timer = window.setInterval(() => load(true), 4000);
    return () => window.clearInterval(timer);
  }, [activeRun?.id, activeRun?.status, load]);

  const trigger = async () => {
    setTriggering(true);
    setError(null);
    try {
      await apiFetch('/personal/robot/runs', { method: 'POST', body: JSON.stringify({ pool_type: poolType }) });
      await load(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '刷新任务提交失败');
    } finally {
      setTriggering(false);
    }
  };

  const selectCalendarDay = async (day: CalendarDay) => {
    setCalendarDay(null);
    try {
      const response = await apiFetch<{ data: { date: string; journals: RobotJournal[]; available: boolean } }>(`/personal/robot/calendar/${day.date}`);
      setCalendarDay(response.data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '收益日详情读取失败');
    }
  };

  const analyzeCalendar = async () => {
    setCalendarAnalysis(null);
    try {
      const response = await apiFetch<{ data: CalendarAnalysis }>('/personal/robot/calendar/analyze', {
        method: 'POST', body: JSON.stringify({ pool_type: poolType, days: 180, use_ai: true }),
      });
      setCalendarAnalysis(response.data);
      setCalendar(response.data.calendar);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '收益日历分析失败');
    }
  };

  const pool = data?.pools?.[poolType];
  const combined = data?.combined_performance;
  const latestHistory = useMemo(() => history.filter((run) => run.status !== 'queued' && run.status !== 'running').slice(0, 6), [history]);
  const poolJournals = useMemo(() => journals.filter((journal) => journal.pool_type === poolType), [journals, poolType]);
  const selectedJournal = useMemo(
    () => poolJournals.find((journal) => journal.id === selectedJournalId) || poolJournals[0] || pool?.journal || null,
    [poolJournals, selectedJournalId, pool?.journal],
  );

  if (loading && !data) {
    return <div className="max-w-5xl mx-auto px-4 py-20 text-center"><Loader2 size={30} className="animate-spin text-accent mx-auto" /><div className="text-sm text-text mt-4">正在读取机器人快照与模拟持仓</div><div className="h-1.5 max-w-sm mx-auto bg-[#21262D] mt-5 overflow-hidden rounded"><div className="h-full bg-accent transition-all" style={{ width: `${loadProgress}%` }} /></div><div className="text-xs text-text-secondary font-mono mt-2">{loadProgress}%</div></div>;
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-5 md:py-6">
      <PersonalWorkspaceNav />
      <header className="flex flex-wrap items-start justify-between gap-4 mb-5">
        <div><h1 className="text-xl md:text-2xl font-bold text-text flex items-center gap-2"><Bot size={23} className="text-accent" />AI选股机器人</h1><p className="text-xs text-text-secondary mt-1">独立短期池与长期池 · 真实行情模拟100股</p></div>
        <button type="button" onClick={trigger} disabled={triggering || Boolean(activeRun)} className="inline-flex items-center gap-1.5 px-3 py-2 bg-accent text-white rounded-md text-xs disabled:opacity-50"><RefreshCw size={14} className={triggering || activeRun ? 'animate-spin' : ''} />{activeRun ? '分析进行中' : `刷新${poolType === 'short' ? '短期池' : '长期池'}`}</button>
      </header>

      {error && <div className="mb-4 border border-up/50 bg-[#EF535014] rounded-md p-3 text-xs text-up flex gap-2"><AlertTriangle size={15} />{error}</div>}

      {combined && <section className="grid grid-cols-2 md:grid-cols-5 border border-border rounded-md divide-x divide-y md:divide-y-0 divide-border mb-4">
        <Metric label="组合标的" value={`${combined.positions}只`} />
        <Metric label="模拟总股数" value={`${combined.simulated_shares}股`} />
        <Metric label="已核算成本" value={money(combined.cost_value)} />
        <Metric label="最新市值" value={money(combined.market_value)} />
        <Metric label="实际浮盈亏" value={`${money(combined.pnl)} · ${signed(combined.pnl_pct)}`} className={pnlClass(combined.pnl)} wrapperClassName="col-span-2 md:col-span-1" />
      </section>}

      {data && <section className="border border-border rounded-md px-3 py-2.5 mb-5 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-text-secondary">
        <span className={data.quote.is_realtime ? 'text-up' : data.quote.available ? 'text-warn' : 'text-text-secondary'}>{!data.quote.available ? '暂无组合行情' : data.quote.is_realtime ? '盘中实时行情' : data.quote.cache_used ? '最近交易日缓存' : '最近可验证行情（非实时）'}</span>
        <span>数据日期：<b className="font-mono font-normal text-text">{data.quote.data_date || '--'}</b></span>
        <span>来源：{data.quote.source}</span>
        <span className={data.quote.complete ? 'text-text-secondary' : 'text-warn'}>{!data.quote.available ? '等待机器人池建立' : data.quote.complete ? '组合行情完整' : '部分行情待恢复'}</span>
        <span className="sm:ml-auto">{data.simulation_rule}</span>
      </section>}

      <div className="flex border-b border-border mb-4">
        {(['short', 'long'] as PoolType[]).map((key) => <button key={key} type="button" onClick={() => setPoolType(key)} className={`px-5 py-2.5 text-sm border-b-2 ${poolType === key ? 'border-accent text-accent' : 'border-transparent text-text-secondary'}`}>{key === 'short' ? '短期池' : '长期池'}<span className="font-mono ml-2 text-xs">{data?.pools[key].performance.positions || 0}</span></button>)}
      </div>

      {activeRun && <section className="border border-accent/50 rounded-md p-4 mb-4"><div className="flex items-center justify-between gap-3 text-xs"><span className="text-text flex items-center gap-2"><Loader2 size={14} className="animate-spin text-accent" />{activeRun.message}</span><span className="font-mono text-accent">{activeRun.progress}%</span></div><div className="h-1.5 bg-[#21262D] mt-3 overflow-hidden rounded"><div className="h-full bg-accent transition-all" style={{ width: `${activeRun.progress}%` }} /></div></section>}

      {calendar && <RobotPerformanceCalendar calendar={calendar} selectedDay={calendarDay} analysis={calendarAnalysis} onSelect={selectCalendarDay} onAnalyze={analyzeCalendar} />}

      {pool && <>
        <section className="grid grid-cols-2 lg:grid-cols-6 border border-border rounded-md divide-x divide-y lg:divide-y-0 divide-border mb-5">
          <Metric label="池内股票" value={`${pool.performance.positions}只`} />
          <Metric label="模拟成本" value={money(pool.performance.cost_value)} />
          <Metric label="当前市值" value={money(pool.performance.market_value)} />
          <Metric label="浮盈亏" value={`${money(pool.performance.pnl)} · ${signed(pool.performance.pnl_pct)}`} className={pnlClass(pool.performance.pnl)} />
          <Metric label="盈利 / 亏损" value={`${pool.performance.winners} / ${pool.performance.losers}`} />
          <Metric label="待有效价格" value={`${pool.performance.waiting_positions + pool.performance.quote_unavailable_positions}只`} className={pool.performance.waiting_positions ? 'text-warn' : ''} />
        </section>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-text-secondary mb-4"><span>持有周期：{pool.config.holding_period}</span><span>自动更新：{pool.config.refresh_rule}</span><span>最近运行：{time(pool.run?.finished_at)}</span><span>下次：{time(pool.next_update)}</span>{pool.run?.summary && <span>调入 {pool.run.summary.new || 0} · 保留 {pool.run.summary.retained || 0} · 淘汰 {pool.run.summary.removed || 0}</span>}</div>

        {selectedJournal && <RobotJournalSection journals={poolJournals.length ? poolJournals : [selectedJournal]} selected={selectedJournal} onSelect={setSelectedJournalId} />}

        {poolType === 'short' && data?.overnight && <OvernightRobotSection data={data.overnight} />}

        {pool.sectors.length === 0 ? <section className="border border-border rounded-md py-16 text-center"><Bot size={28} className="text-border mx-auto" /><div className="text-sm text-text mt-3">当前还没有完成的{pool.config.label}快照</div><div className="text-xs text-text-secondary mt-2">数据源明确返回空时不会使用旧名单冒充本轮结果。</div></section> : <div className="space-y-4">
          {pool.sectors.map((sector) => <section key={sector.key} className="border border-border rounded-md overflow-hidden"><div className="px-4 py-3 bg-card border-b border-border flex items-center"><h2 className="text-sm font-semibold text-text">{sector.label}</h2><span className="ml-2 text-xs font-mono text-text-secondary">{sector.count}只</span></div><div className="overflow-x-auto"><table className="w-full min-w-[980px] text-xs"><thead className="text-text-secondary border-b border-border"><tr><th className="text-left px-4 py-2.5">股票</th><th className="text-right px-3">评分</th><th className="text-right px-3">模拟买入</th><th className="text-right px-3">最新价</th><th className="text-right px-3">100股成本</th><th className="text-right px-3">市值</th><th className="text-right px-3">实际盈亏</th><th className="text-left px-3">判断依据</th><th className="text-right px-4">个人池</th></tr></thead><tbody>{sector.picks.map((pick) => <RobotRow key={pick.id} pick={pick} />)}</tbody></table></div></section>)}
        </div>}
      </>}

      {latestHistory.length > 0 && <section className="mt-6 border-t border-border pt-4"><h2 className="text-sm font-semibold text-text flex items-center gap-2"><Clock3 size={15} className="text-text-secondary" />最近运行</h2><div className="mt-3 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">{latestHistory.map((run) => <div key={run.id} className="border border-border rounded-md px-3 py-2.5 text-xs"><div className="flex justify-between"><span className="text-text">{run.pool_type === 'short' ? '短期池' : '长期池'} #{run.id}</span><span className={run.status === 'failed' ? 'text-up' : run.status === 'partial' ? 'text-warn' : 'text-down'}>{run.status === 'completed' ? '完成' : run.status === 'partial' ? '部分完成' : '失败'}</span></div><div className="text-text-secondary mt-1">{run.message}</div><div className="text-text-secondary font-mono mt-1">{run.source_data_date || '--'}</div></div>)}</div></section>}
      {data && <p className="text-[11px] text-text-secondary mt-5">{data.disclaimer}</p>}
    </div>
  );
}

function Metric({ label, value, className = '', wrapperClassName = '' }: { label: string; value: string; className?: string; wrapperClassName?: string }) {
  return <div className={`p-3 min-w-0 ${wrapperClassName}`}><div className="text-[11px] text-text-secondary">{label}</div><div className={`font-mono text-sm md:text-base mt-1 truncate ${className || 'text-text'}`}>{value}</div></div>;
}

function RobotPerformanceCalendar({
  calendar,
  selectedDay,
  analysis,
  onSelect,
  onAnalyze,
}: {
  calendar: PerformanceCalendar;
  selectedDay: { date: string; journals: RobotJournal[]; available: boolean } | null;
  analysis: CalendarAnalysis | null;
  onSelect: (day: CalendarDay) => void;
  onAnalyze: () => void;
}) {
  const summary = calendar.summary;
  const currentAlert = summary.current_loss_streak >= 3;
  const dayMap = new Map(calendar.days.map((day) => [day.date, day]));
  const calendarEnd = new Date(`${calendar.to}T12:00:00`);
  const calendarStart = new Date(calendarEnd);
  calendarStart.setDate(calendarEnd.getDate() - ((calendarEnd.getDay() + 6) % 7) - 35);
  const calendarCells = Array.from({ length: 42 }, (_, index) => {
    const current = new Date(calendarStart);
    current.setDate(calendarStart.getDate() + index);
    const key = current.toISOString().slice(0, 10);
    return { key, day: dayMap.get(key) || null };
  });
  return <section className="mb-4 border border-border rounded-md overflow-hidden">
    <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3"><h2 className="flex items-center gap-2 text-sm font-semibold text-text"><CalendarDays size={15} className="text-accent" />AI选股收益日历</h2><span className="text-[11px] text-text-secondary">已记录{summary.recorded_days}日 · {calendar.from} 至 {calendar.to}</span><button type="button" onClick={onAnalyze} className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-text-secondary hover:border-accent hover:text-text"><BrainCircuit size={13} />AI解析</button></div>
    <div className="grid grid-cols-2 md:grid-cols-5 border-b border-border divide-x divide-border"><Metric label="累计盈亏" value={money(summary.total_pnl)} className={pnlClass(summary.total_pnl)} /><Metric label="盈利日" value={`${summary.profit_days}日`} className="text-up" /><Metric label="亏损日" value={`${summary.loss_days}日`} className="text-down" /><Metric label="当前连续亏损" value={`${summary.current_loss_streak}日`} className={currentAlert ? 'text-warn' : ''} /><Metric label="最长连续亏损" value={`${summary.max_loss_streak}日`} className="text-text" /></div>
    {currentAlert && <div className="flex gap-2 border-b border-warn/50 bg-[#D2992218] px-4 py-2.5 text-xs text-warn"><AlertTriangle size={14} className="shrink-0" />连续亏损达到3日：仅提醒并保留行情扫描，建议人工复核，不自动停止选股。</div>}
    <div className="grid grid-cols-7 border-b border-border text-[10px] text-text-secondary">{['一', '二', '三', '四', '五', '六', '日'].map((day) => <div key={day} className="px-2 py-2 text-center">周{day}</div>)}</div>
    <div className="grid grid-cols-7 gap-px bg-border p-px">{calendarCells.map(({ key, day }) => day ? <button key={key} type="button" title={`${day.date} ${money(day.pnl)}`} onClick={() => onSelect(day)} className={`min-h-[54px] bg-bg px-1 py-1.5 text-center hover:bg-card ${day.status === 'profit' ? 'text-up' : day.status === 'loss' ? 'text-down' : 'text-text-secondary'}`}><div className="font-mono text-[10px] text-text-secondary">{day.date.slice(5)}</div><div className="mt-1 font-mono text-xs">{day.pnl >= 0 ? '+' : ''}{day.pnl.toFixed(0)}</div><div className="mt-0.5 text-[9px]">{day.status === 'profit' ? '盈' : day.status === 'loss' ? '亏' : '平'}</div></button> : <div key={key} className="min-h-[54px] bg-bg px-1 py-1.5 text-center"><div className="font-mono text-[10px] text-text-secondary/60">{key.slice(5)}</div><div className="mt-3 text-[9px] text-text-secondary/40">--</div></div>)}</div>
    {selectedDay?.available && <div className="border-b border-border bg-[#0D1117] px-4 py-3"><div className="text-xs font-semibold text-text">{selectedDay.date} · 当日复盘</div><div className="mt-2 grid gap-2 md:grid-cols-2">{selectedDay.journals.map((journal) => <div key={journal.id} className="rounded-md border border-border p-2.5 text-xs"><div className="flex justify-between gap-2"><span className="text-text">{journal.pool_type === 'short' ? '短期池' : '长期池'}</span><span className={pnlClass((journal.metrics?.performance as Performance | undefined)?.pnl)}>{money((journal.metrics?.performance as Performance | undefined)?.pnl)}</span></div><div className="mt-1.5 text-text-secondary leading-5">{journal.pnl_reflection || journal.action_summary}</div></div>)}</div></div>}
    {analysis && <div className="border-b border-border px-4 py-3"><div className="text-xs font-semibold text-text">AI解析 · {analysis.source === 'deepseek' ? 'DeepSeek' : '规则审计'}</div><div className="mt-2 whitespace-pre-line text-xs leading-6 text-text-secondary">{analysis.analysis}</div></div>}
    <div className="px-4 py-2 text-[10px] text-text-secondary">{calendar.methodology}</div>
  </section>;
}

function RobotRow({ pick }: { pick: RobotPick }) {
  return <tr className="border-b border-border/60 last:border-b-0 align-top"><td className="px-4 py-3"><StockKlineButton code={pick.code} name={pick.name} className="text-text font-medium">{pick.name}<span className="font-mono text-text-secondary ml-2">{pick.code}</span></StockKlineButton><div className="mt-1 flex gap-1.5"><span className={`border rounded px-1.5 py-0.5 ${pick.state === 'new' ? 'border-accent/50 text-accent' : 'border-border text-text-secondary'}`}>{pick.state === 'new' ? '新调入' : '保留'}</span><span className="text-text-secondary py-0.5">{pick.holding_label}</span></div></td><td className="px-3 py-3 text-right font-mono text-text">{number(pick.score, 1)}</td><td className="px-3 py-3 text-right font-mono"><div className="text-text">{pick.selected_price == null ? '等待有效行情' : `¥${number(pick.selected_price)}`}</div><div className="text-text-secondary mt-1">{pick.selected_on || '--'} · {pick.simulated_shares}股</div></td><td className="px-3 py-3 text-right font-mono"><div className="text-text">{pick.latest_price == null ? '--' : `¥${number(pick.latest_price)}`}</div><div className={pnlClass(pick.change_pct)}>{signed(pick.change_pct)}</div></td><td className="px-3 py-3 text-right font-mono text-text">{money(pick.cost_value)}</td><td className="px-3 py-3 text-right font-mono text-text">{money(pick.market_value)}</td><td className={`px-3 py-3 text-right font-mono ${pnlClass(pick.pnl)}`}><div>{money(pick.pnl)}</div><div>{signed(pick.pnl_pct)}</div></td><td className="px-3 py-3 max-w-[260px]"><p className="text-text-secondary leading-5 line-clamp-2">{pick.basis || pick.verdict || 'Agent证据已随快照保存'}</p>{pick.evidence.length > 0 && <details className="mt-1"><summary className="text-accent cursor-pointer inline-flex items-center gap-1">Agent证据<ChevronDown size={12} /></summary><div className="mt-2 space-y-1.5">{pick.evidence.slice(0, 4).map((item, index) => <div key={`${item.agent}-${index}`} className="border-l border-border pl-2"><div className="text-text">{item.agent}</div><div className="text-text-secondary leading-5">{item.summary}</div></div>)}</div></details>}</td><td className="px-4 py-3 text-right"><AddToPersonalPoolButton code={pick.code} name={pick.name} industry={pick.sector_label} thesis={`${pick.holding_label}：${pick.basis || pick.verdict}`} source={`ai_robot_${pick.pool_type}`} compact /></td></tr>;
}

function RobotJournalSection({ journals, selected, onSelect }: { journals: RobotJournal[]; selected: RobotJournal; onSelect: (id: number) => void }) {
  const performance = selected.metrics?.performance as Performance | undefined;
  return <section className="border border-border rounded-md overflow-hidden mb-4"><div className="px-4 py-3 border-b border-border flex flex-wrap items-center gap-2"><h2 className="text-sm font-semibold text-text flex items-center gap-2"><BookOpen size={15} className="text-accent" />机器人复盘日记</h2><span className="text-[11px] text-text-secondary">记录日 {selected.journal_date} · 行情日 {selected.source_data_date || '--'}</span><span className="sm:ml-auto text-[11px] text-text-secondary">{selected.is_realtime ? '盘中数据' : '收盘/缓存数据'}</span></div><div className="grid lg:grid-cols-[180px_1fr]"><div className="border-b lg:border-b-0 lg:border-r border-border bg-[#0D1117] p-2 flex lg:block gap-1 overflow-x-auto">{journals.slice(0, 20).map((journal) => <button key={journal.id} type="button" onClick={() => onSelect(journal.id)} className={`shrink-0 w-[132px] lg:w-full text-left px-2.5 py-2 rounded text-xs mb-0 lg:mb-1 ${journal.id === selected.id ? 'bg-accent/15 text-accent' : 'text-text-secondary hover:bg-card hover:text-text'}`}><div className="font-mono">{journal.journal_date}</div><div className="mt-0.5 truncate">{journal.metrics?.new || 0}调入 · {journal.metrics?.removed || 0}移出</div></button>)}</div><div className="p-4 space-y-4"><div className="grid gap-4 md:grid-cols-2"><JournalBlock title="今天做了什么" text={selected.action_summary} /><JournalBlock title="盈亏思考" text={selected.pnl_reflection || (performance ? `组合盈亏 ${money(performance.pnl)} · ${signed(performance.pnl_pct)}` : '等待盘后盈亏快照')} /><JournalBlock title="今天的收获" text={selected.lessons} /><JournalBlock title="总体判断" text={selected.decision_reason} /></div><div><div className="text-[11px] text-text-secondary mb-2">逐股说明 · 每只股票单独依据</div><div className="grid gap-2">{selected.picks_snapshot?.map((pick, index) => <RobotJournalPick key={`${pick.code || index}-${index}`} pick={pick} />)}</div></div></div></div></section>;
}

function RobotJournalPick({ pick }: { pick: RobotJournal['picks_snapshot'][number] }) {
  const explanation = pick.explanation || {};
  const positive = explanation.positive_evidence || [];
  const risks = explanation.risks || [];
  return <article className="border border-border rounded-md p-3 bg-[#0D1117] min-w-0"><div className="flex flex-wrap items-start justify-between gap-2"><div><StockKlineButton code={pick.code || ''} name={pick.name} className="text-sm font-semibold text-text">{pick.name || '--'} <span className="font-mono text-text-secondary ml-1">{pick.code || '--'}</span></StockKlineButton><div className="text-[11px] text-text-secondary mt-1">{pick.sector || '板块未返回'} · {pick.state === 'new' ? '新调入' : '保留'} · 评分 {pick.score == null ? '--' : pick.score.toFixed(1)}</div></div><span className="text-[11px] text-text-secondary">数据质量：{explanation.data_quality || '未标注'}</span></div><p className="mt-2 text-xs leading-5 text-text">{explanation.plain_reason || pick.basis || '暂无逐股解释'}</p><div className="mt-2 grid gap-2 md:grid-cols-3 text-[11px]"><div><div className="text-text-secondary mb-1">关键事实</div><div className="space-y-1 text-text">{(explanation.key_facts || []).slice(0, 4).map((item) => <div key={item}>{item}</div>)}</div></div><div><div className="text-text-secondary mb-1">支持证据</div><div className="space-y-1 text-up">{positive.slice(0, 3).map((item) => <div key={item}>{item}</div>)}</div></div><div><div className="text-text-secondary mb-1">风险与失效</div><div className="space-y-1 text-warn">{[...risks, ...(explanation.invalidation_conditions || [])].slice(0, 3).map((item) => <div key={item}>{item}</div>)}</div></div></div></article>;
}

function JournalBlock({ title, text }: { title: string; text: string }) {
  return <div className="min-w-0"><div className="text-[11px] text-text-secondary mb-1.5">{title}</div><div className="text-xs text-text leading-6 whitespace-pre-line break-words">{text || '--'}</div></div>;
}

function OvernightRobotSection({ data }: { data: NonNullable<Dashboard['overnight']> }) {
  return <section className="border border-accent/40 rounded-md overflow-hidden mb-4"><div className="px-4 py-3 border-b border-border flex flex-wrap items-center gap-2"><h2 className="text-sm font-semibold text-text flex items-center gap-2"><MoonStar size={15} className="text-accent" />{data.tag}</h2><span className="text-[11px] text-text-secondary">{data.schedule}</span><span className="sm:ml-auto text-[11px] text-text-secondary">{data.run?.data_date || '等待交易时段首轮扫描'}</span></div>{data.positions.length ? <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-xs"><thead className="text-text-secondary bg-[#161B22]"><tr><th className="text-left px-4 py-2">股票</th><th className="text-right px-3">尾盘模拟买入</th><th className="text-right px-3">当前价</th><th className="text-right px-3">100股成本</th><th className="text-right px-3">净盈亏</th><th className="text-left px-3">退出纪律</th><th className="text-right px-4">个人池</th></tr></thead><tbody>{data.positions.map((position) => <tr key={position.id} className="border-t border-border/70"><td className="px-4 py-3"><StockKlineButton code={position.code} name={position.name} className="text-text font-medium">{position.name}<span className="font-mono text-text-secondary ml-2">{position.code}</span></StockKlineButton><span className="inline-block mt-1 border border-accent/50 text-accent rounded px-1.5 py-0.5">一夜持股</span></td><td className="px-3 py-3 text-right font-mono"><div className="text-text">¥{number(position.entry_price, 4)}</div><div className="text-text-secondary mt-1">{time(position.entry_at)} · {position.shares}股</div></td><td className="px-3 py-3 text-right font-mono text-text">{position.current_price == null ? '--' : `¥${number(position.current_price, 4)}`}</td><td className="px-3 py-3 text-right font-mono text-text">{money(position.cost_value)}</td><td className={`px-3 py-3 text-right font-mono ${pnlClass(position.pnl)}`}><div>{money(position.pnl)}</div><div>{signed(position.pnl_pct)}</div></td><td className="px-3 py-3 text-text-secondary">{position.exit_reason || '次日09:30-10:00强制退出'}</td><td className="px-4 py-3 text-right"><AddToPersonalPoolButton code={position.code} name={position.name} industry={position.sector} thesis={`一夜持股：次日10:00前退出`} source="ai_robot_overnight" compact /></td></tr>)}</tbody></table></div> : <div className="px-4 py-8 text-center text-xs text-text-secondary">仅在尾盘分钟条件和所有排雷数据完整通过后建立100股模拟仓位</div>}</section>;
}
