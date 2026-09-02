'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  Clock3,
  Database,
  Gauge,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
  TrendingUp,
} from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';

type AnyMap = Record<string, any>;

interface AuctionObservation {
  code: string;
  name: string;
  high_open_pct?: number | null;
  source?: string;
  is_realtime?: boolean;
}

interface AuctionDashboard {
  status?: string;
  trade_date?: string | null;
  model_version?: string;
  observed_stocks?: number;
  time_series_snapshots?: number;
  universe_count?: number;
  coverage_pct?: number;
  timeline_coverage_pct?: number;
  latest_observations?: AuctionObservation[];
  sample_features?: AnyMap[];
  quality?: { status?: string; warning?: string | null; no_fake_backtest?: boolean };
}

interface AuctionTimeline {
  symbol?: string;
  timeline?: AnyMap[];
  transition?: string;
  quality?: AnyMap;
  model_version?: string;
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function numberText(value: unknown, digits = 2): string {
  return finite(value) ? value.toFixed(digits) : '--';
}

function percentText(value: unknown): string {
  return finite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%` : '--';
}

function sourceLabel(value: unknown): string {
  const source = String(value || '');
  if (source.includes('numcat')) return '猫爪';
  if (source.includes('eastmoney')) return '东方财富';
  if (source.includes('cache')) return '数据库缓存';
  return source || '缓存/未知';
}

function Panel({ title, icon: Icon, action, children }: { title: string; icon: typeof Activity; action?: React.ReactNode; children: React.ReactNode }) {
  return <section className="min-w-0 overflow-hidden rounded-md border border-border bg-card"><header className="flex min-w-0 items-center justify-between gap-3 border-b border-border px-4 py-3"><h2 className="flex min-w-0 items-center gap-2 text-sm font-semibold text-text"><Icon size={15} className="shrink-0 text-accent" />{title}</h2>{action}</header>{children}</section>;
}

function Metric({ label, value, detail, tone = 'text-text' }: { label: string; value: string; detail: string; tone?: string }) {
  return <div className="min-w-0 border-l-2 border-accent/60 bg-[#151D27] px-3 py-3"><div className="truncate text-[10px] text-text-secondary">{label}</div><div className={`mt-1 truncate font-mono text-lg font-semibold ${tone}`}>{value}</div><div className="mt-1 truncate text-[10px] text-text-secondary" title={detail}>{detail}</div></div>;
}

export default function AuctionPage() {
  const [dashboard, setDashboard] = useState<AuctionDashboard | null>(null);
  const [symbol, setSymbol] = useState('600519');
  const [timeline, setTimeline] = useState<AuctionTimeline | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [error, setError] = useState('');
  const [timelineError, setTimelineError] = useState('');

  const load = useCallback(async (refresh = false) => {
    setError('');
    if (refresh) setRefreshing(true); else setLoading(true);
    try {
      const response = await apiFetch<{ data: AuctionDashboard }>(`/auction/dashboard${refresh ? '?refresh=true' : ''}`, { timeoutMs: 30000 });
      setDashboard(response.data);
    } catch (caught) {
      setError(friendlyApiError(caught, '竞价监控暂时无法读取'));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(true), 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const loadTimeline = async () => {
    const code = symbol.trim();
    if (!code) {
      setTimelineError('请输入股票代码');
      return;
    }
    setTimelineLoading(true);
    setTimelineError('');
    try {
      const response = await apiFetch<{ data: AuctionTimeline }>(`/auction/${encodeURIComponent(code)}/timeline`, { timeoutMs: 30000 });
      setTimeline(response.data);
    } catch (caught) {
      setTimeline(null);
      setTimelineError(friendlyApiError(caught, '该标的竞价时间线暂时不可用'));
    } finally {
      setTimelineLoading(false);
    }
  };

  const observations = dashboard?.latest_observations || [];
  const sortedObservations = useMemo(() => [...observations].sort((a, b) => (b.high_open_pct || -Infinity) - (a.high_open_pct || -Infinity)), [observations]);
  const quality = dashboard?.quality;

  return <div className="min-h-screen bg-bg text-text"><div className="mx-auto max-w-[1500px] space-y-4 p-3 sm:p-5">
    <header className="flex min-w-0 flex-wrap items-start justify-between gap-4 border-b border-border pb-4"><div className="min-w-0"><div className="flex items-center gap-2 text-[11px] font-mono text-accent"><Activity size={14} />OPENING AUCTION / V5.1</div><h1 className="mt-2 text-xl font-semibold sm:text-2xl">早盘竞价监控</h1><p className="mt-1 max-w-4xl text-xs leading-5 text-text-secondary">竞价监控独立于一夜持股策略，可单独查看09:15-09:25附近的价格、成交量和时间序列证据。这里只展示观察结果，不把竞价强度直接等同于买入信号。</p></div><div className="flex shrink-0 items-center gap-2"><Link href="/quant" className="command-button"><TrendingUp size={13} />量化策略</Link><button type="button" onClick={() => void load(true)} disabled={refreshing} className="command-button command-button-primary"><RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />刷新竞价</button></div></header>

    {error && <div className="flex items-start gap-2 rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-xs text-warn"><AlertTriangle size={14} className="mt-0.5 shrink-0" />{error}</div>}
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4"><Metric label="交易日" value={dashboard?.trade_date || '--'} detail={dashboard?.model_version || '模型版本'} /><Metric label="观测股票" value={String(dashboard?.observed_stocks ?? '--')} detail={`样本池 ${dashboard?.universe_count ?? '--'} 只`} /><Metric label="单点覆盖" value={finite(dashboard?.coverage_pct) ? `${dashboard?.coverage_pct.toFixed(1)}%` : '--'} detail="竞价快照覆盖率" tone="text-up" /><Metric label="序列覆盖" value={finite(dashboard?.timeline_coverage_pct) ? `${dashboard?.timeline_coverage_pct.toFixed(1)}%` : '--'} detail={`${dashboard?.time_series_snapshots ?? 0} 条时间序列`} tone={dashboard?.timeline_coverage_pct ? 'text-accent' : 'text-warn'} /></div>

    <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]"><div className="min-w-0 space-y-4"><Panel title="竞价观测排名" icon={BarChart3} action={<span className="text-[10px] text-text-secondary">按高开幅度排序</span>}>{loading && !dashboard ? <div className="flex items-center justify-center gap-2 py-14 text-xs text-text-secondary"><Loader2 size={15} className="animate-spin text-accent" />读取竞价缓存</div> : sortedObservations.length ? <div className="overflow-x-auto"><table className="w-full min-w-[650px] text-[11px]"><thead className="border-b border-border bg-[#151D27] text-left text-[10px] text-text-secondary"><tr><th className="px-3 py-2 font-normal">排名</th><th className="px-3 py-2 font-normal">标的</th><th className="px-3 py-2 text-right font-normal">高开幅度</th><th className="px-3 py-2 font-normal">来源</th><th className="px-3 py-2 font-normal">时效</th><th className="px-3 py-2 text-right font-normal">操作</th></tr></thead><tbody>{sortedObservations.map((item, index) => <tr key={`${item.code}-${index}`} className="border-b border-border/70 last:border-0 hover:bg-[#18212C]"><td className="px-3 py-2.5 font-mono text-text-secondary">{index + 1}</td><td className="px-3 py-2.5"><span className="font-medium text-text">{item.name || '--'}</span><span className="ml-2 font-mono text-[10px] text-text-secondary">{item.code}</span></td><td className={`px-3 py-2.5 text-right font-mono ${item.high_open_pct != null && item.high_open_pct >= 0 ? 'text-up' : 'text-down'}`}>{percentText(item.high_open_pct)}</td><td className="px-3 py-2.5 text-text-secondary">{sourceLabel(item.source)}</td><td className="px-3 py-2.5 text-text-secondary">{item.is_realtime ? '盘中/实时' : '收盘快照'}</td><td className="px-3 py-2.5 text-right"><button type="button" onClick={() => { setSymbol(item.code); void (async () => { const code = item.code; setTimelineLoading(true); setTimelineError(''); try { const response = await apiFetch<{ data: AuctionTimeline }>(`/auction/${encodeURIComponent(code)}/timeline`, { timeoutMs: 30000 }); setTimeline(response.data); } catch (caught) { setTimelineError(friendlyApiError(caught, '该标的竞价时间线暂时不可用')); } finally { setTimelineLoading(false); } })(); }} className="inline-flex items-center gap-1 text-accent hover:text-text">时间线 <ArrowRight size={12} /></button></td></tr>)}</tbody></table></div> : <div className="py-14 text-center text-xs text-text-secondary">当前没有竞价观测。系统会在有数据时更新，缺失历史不会用日K反推。</div>}</Panel>

      <Panel title="模型边界" icon={ShieldCheck}><div className="grid gap-3 p-4 sm:grid-cols-3"><div className="border-l-2 border-accent/60 bg-[#151D27] px-3 py-3"><div className="text-[10px] text-text-secondary">当前状态</div><div className="mt-1 text-sm text-text">{quality?.status || dashboard?.status || '核验中'}</div></div><div className="border-l-2 border-warn/60 bg-[#151D27] px-3 py-3"><div className="text-[10px] text-text-secondary">历史序列</div><div className="mt-1 text-sm text-text">{quality?.warning ? '逐步积累' : '已观测'}</div></div><div className="border-l-2 border-up/60 bg-[#151D27] px-3 py-3"><div className="text-[10px] text-text-secondary">伪回测</div><div className="mt-1 text-sm text-up">{quality?.no_fake_backtest ? '已禁止' : '未说明'}</div></div></div>{quality?.warning && <div className="border-t border-border px-4 py-3 text-[10px] leading-5 text-warn">{quality.warning}</div>}<div className="border-t border-border px-4 py-3 text-[10px] leading-5 text-text-secondary">竞价未满足条件时只是观察结果，是否执行仍需结合市场状态、板块宽度、资金和个人交易权限确认。</div></Panel></div>

    <aside className="min-w-0 space-y-4"><Panel title="单股竞价时间线" icon={Gauge}><div className="p-4"><div className="flex min-w-0 gap-2"><label className="min-w-0 flex-1"><span className="sr-only">股票代码</span><input value={symbol} onChange={(event) => setSymbol(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void loadTimeline(); }} className="input w-full font-mono text-xs" placeholder="输入代码，如600519" /></label><button type="button" onClick={() => void loadTimeline()} disabled={timelineLoading} className="command-button command-button-primary shrink-0"><Search size={13} />查询</button></div><div className="mt-2 text-[10px] text-text-secondary">独立查询，不依赖一夜持股扫描结果。</div>{timelineError && <div className="mt-3 flex items-start gap-2 text-[10px] leading-5 text-warn"><AlertTriangle size={13} className="mt-0.5 shrink-0" />{timelineError}</div>}{timelineLoading && <div className="mt-4 flex items-center gap-2 text-xs text-text-secondary"><Loader2 size={14} className="animate-spin" />读取竞价时间线</div>}{timeline && !timelineLoading && <div className="mt-4 space-y-3"><div className="border-l-2 border-accent bg-[#151D27] px-3 py-3 text-xs text-text"><div className="font-mono">{timeline.symbol || symbol}</div><div className="mt-1 text-[10px] text-text-secondary">状态 {timeline.transition || '暂无转折标签'} · {timeline.model_version || 'V5.1'}</div></div>{timeline.timeline?.length ? <div className="space-y-2">{timeline.timeline.slice(0, 18).map((item, index) => <div key={`${String(item.time || item.snapshot_time || index)}-${index}`} className="grid grid-cols-[72px_minmax(0,1fr)_auto] items-center gap-2 border-b border-border/70 pb-2 text-[10px]"><span className="font-mono text-text-secondary">{String(item.time || item.snapshot_time || '--')}</span><span className="min-w-0 truncate text-text">{String(item.label || item.feature || item.name || '竞价观测')}</span><span className="font-mono text-text-secondary">{String(item.value ?? item.score ?? '--')}</span></div>)}</div> : <div className="text-xs text-text-secondary">该代码当前没有足够的竞价时间序列。</div>}</div>}</div></Panel>

      <Panel title="接入方式" icon={Database}><div className="space-y-3 p-4 text-[10px] leading-5 text-text-secondary"><div className="flex items-start gap-2"><CheckCircle2 size={13} className="mt-0.5 shrink-0 text-up" />优先读取已保存的竞价快照和时间序列。</div><div className="flex items-start gap-2"><CheckCircle2 size={13} className="mt-0.5 shrink-0 text-up" />实时交易时段可接入猫爪/行情源，非交易时段展示最近完整快照。</div><div className="flex items-start gap-2"><Clock3 size={13} className="mt-0.5 shrink-0 text-accent" />只保存业务快照，不在前端或本页新增原始大数据仓库。</div></div></Panel>

      <Panel title="下一步动作" icon={TrendingUp}><div className="p-4 text-[10px] leading-5 text-text-secondary">竞价观察后，进入量化策略查看一夜持股和其他策略的独立扫描结果。两个模块互不阻塞，便于人工比较判断。<Link href="/quant" className="mt-3 inline-flex items-center gap-1 text-accent hover:text-text">打开量化策略 <ArrowRight size={12} /></Link></div></Panel></aside></div>

    <footer className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-border pt-3 text-[10px] text-text-secondary"><span>数据日 {dashboard?.trade_date || '--'}</span><span>实时行情仅在交易时段更新</span><span>无数据时沿用最近完整缓存，并明确标注来源</span></footer>
  </div></div>;
}
