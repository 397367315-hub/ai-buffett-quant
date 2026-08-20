'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CalendarClock, Compass, Database, Globe2, Landmark, Loader2, RefreshCw, Waves } from 'lucide-react';
import PersonalWorkspaceNav from '@/components/PersonalWorkspaceNav';
import { apiFetch } from '@/lib/api';

interface MacroData {
  updated_at: string;
  snapshot_updated_at?: string;
  cache_used: boolean;
  global_markets: Array<{ key: string; label: string; value: number | null; change_pct: number | null; currency: string; source_time: string | null; available: boolean; source: string; is_realtime?: boolean; cache_used?: boolean; data_age_minutes?: number | null }>;
  economic_calendar: Array<{ title: string; country: string; country_code: string; impact: string; event_at: string; forecast: string; previous: string; source: string }>;
  domestic_liquidity: { northbound: { available: boolean; date: string | null; net_inflow: number | null; consecutive_inflow_days: number; source: string }; turnover: { available: boolean; date: string | null; sh_amount: number | null; sh_index: number | null; sh_change_pct: number | null; source: string }; margin_balance: { available: boolean; value: number | null; message: string } };
  policy: { available: boolean; summary: string | null; international_items: Array<Record<string, any>>; policy_items: Array<Record<string, any>> };
  premarket_questions: Array<{ id: string; question: string; answer: string; status: string }>;
  a_share_outlook: {
    stance: 'bullish' | 'neutral' | 'cautious'; label: string; score: number; confidence: number;
    headline: string; summary: string; data_points: number; method: string;
    drivers: Array<{ factor: string; direction: string; explanation: string; affected: string; score: number }>;
    favored_sectors: string[]; pressured_sectors: string[];
  };
  source_status: Record<string, string>;
  disclaimer: string;
}

const number = (value: number | null | undefined, digits = 2) => value == null ? '--' : value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const signed = (value: number | null | undefined) => value == null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
const tone = (value: number | null | undefined) => value == null ? 'text-text-secondary' : value >= 0 ? 'text-up' : 'text-down';
const time = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '--';

export default function MacroPage() {
  const [data, setData] = useState<MacroData | null>(null);
  const [loading, setLoading] = useState(true);
  const [progress, setProgress] = useState(6);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => { setLoading(true); setProgress(8); setError(null); try { const response = await apiFetch<{ data: MacroData }>('/personal/macro'); setData(response.data); setProgress(100); } catch (caught) { setError(caught instanceof Error ? caught.message : '宏观数据加载失败'); } finally { window.setTimeout(() => setLoading(false), 120); } }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (!loading) return; const timer = window.setInterval(() => setProgress((value) => Math.min(88, value + 4)), 500); return () => window.clearInterval(timer); }, [loading]);

  return <div className="max-w-7xl mx-auto px-4 py-5 md:py-6">
    <PersonalWorkspaceNav />
    <header className="flex flex-wrap items-start justify-between gap-4 mb-5"><div><h1 className="text-xl md:text-2xl font-bold text-text flex items-center gap-2"><Globe2 size={22} className="text-accent" />宏观数据</h1><p className="text-xs text-text-secondary mt-1">全球市场、经济日历、国内资金与政策</p></div><button type="button" onClick={load} disabled={loading} className="inline-flex items-center gap-1.5 px-3 py-2 border border-border rounded-md text-xs text-text-secondary hover:text-accent"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} />刷新</button></header>
    {error && <div className="mb-4 border border-up/50 bg-[#EF535014] rounded-md p-3 text-xs text-up flex gap-2"><AlertTriangle size={15} />{error}</div>}
    {loading && !data ? <div className="py-24 text-center"><Loader2 size={28} className="animate-spin text-accent mx-auto" /><div className="text-sm text-text mt-4">正在汇总国际市场与国内政策</div><div className="max-w-sm mx-auto h-1.5 bg-[#21262D] mt-5 rounded overflow-hidden"><div className="h-full bg-accent transition-all" style={{ width: `${progress}%` }} /></div><div className="text-xs font-mono text-text-secondary mt-2">{progress}%</div></div> : data && <>
      <section className="border border-border rounded-md px-3 py-2.5 mb-5 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-text-secondary"><span className={data.cache_used ? 'text-warn' : 'text-down'}>{data.cache_used ? '部分来源使用最近快照' : '本轮来源已返回'}</span><span>刷新：{time(data.updated_at)}</span>{data.snapshot_updated_at && <span>快照：{time(data.snapshot_updated_at)}</span>}<span className="sm:ml-auto flex items-center gap-1"><Database size={13} />不同市场按各自源时间展示</span></section>

      <section className="border-y border-border py-4 mb-5">
        <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><h2 className="text-sm font-semibold text-text flex items-center gap-2"><Compass size={16} className="text-warn" />{data.a_share_outlook.headline}</h2><p className="text-xs text-text-secondary mt-1.5 leading-5">{data.a_share_outlook.summary}</p></div><div className="text-right shrink-0"><span className={`inline-block border rounded px-2 py-1 text-xs ${data.a_share_outlook.stance === 'bullish' ? 'border-up/50 text-up' : data.a_share_outlook.stance === 'cautious' ? 'border-down/50 text-down' : 'border-warn/50 text-warn'}`}>{data.a_share_outlook.label} · {data.a_share_outlook.score >= 0 ? '+' : ''}{data.a_share_outlook.score}</span><div className="text-[10px] text-text-secondary mt-1">置信度 {data.a_share_outlook.confidence.toFixed(0)}%</div></div></div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-3">{data.a_share_outlook.drivers.slice(0, 3).map((driver) => <div key={driver.factor} className="border-l-2 border-border pl-3"><div className={`text-xs ${driver.direction === 'positive' ? 'text-up' : driver.direction === 'negative' ? 'text-down' : 'text-text'}`}>{driver.factor}</div><p className="text-[11px] text-text-secondary leading-5 mt-1">{driver.explanation}</p><div className="text-[10px] text-text-secondary mt-1">影响：{driver.affected}</div></div>)}</div>
        {(data.a_share_outlook.favored_sectors.length > 0 || data.a_share_outlook.pressured_sectors.length > 0) && <div className="flex flex-wrap gap-x-5 gap-y-1 mt-3 pt-3 border-t border-border text-[11px]"><span className="text-up">相对受益：{data.a_share_outlook.favored_sectors.join('、') || '--'}</span><span className="text-down">相对承压：{data.a_share_outlook.pressured_sectors.join('、') || '--'}</span></div>}
      </section>

      <section className="mb-5"><h2 className="text-sm font-semibold text-text flex items-center gap-2 mb-3"><Globe2 size={16} className="text-accent" />全球市场</h2><div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 border border-border rounded-md divide-x divide-y lg:divide-y-0 divide-border">{data.global_markets.map((item) => <div key={item.key} className="p-3 min-w-0"><div className="flex items-center justify-between gap-2"><div className="text-[11px] text-text-secondary truncate">{item.label}</div><span className={`shrink-0 text-[9px] ${item.is_realtime ? 'text-down' : item.cache_used ? 'text-warn' : 'text-text-secondary'}`}>{item.is_realtime ? '实时源' : item.cache_used ? '缓存' : '最新报价'}</span></div><div className="font-mono text-base text-text mt-1 truncate">{number(item.value, item.value && item.value < 1000 ? 2 : 1)}</div><div className={`font-mono text-xs mt-1 ${tone(item.change_pct)}`}>{signed(item.change_pct)}</div><div className="text-[10px] text-text-secondary mt-1 truncate" title={item.source_time || undefined}>{item.source_time || '源时间未返回'}</div><div className="text-[9px] text-text-secondary mt-1 truncate">{item.source}{item.data_age_minutes != null ? ` · ${item.data_age_minutes.toFixed(0)}分钟前` : ''}</div></div>)}</div></section>

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] gap-5 mb-5">
        <section className="border border-border rounded-md overflow-hidden"><div className="px-4 py-3 border-b border-border"><h2 className="text-sm font-semibold text-text flex items-center gap-2"><CalendarClock size={15} className="text-warn" />未来两周经济日历</h2></div>{data.economic_calendar.length ? <div className="divide-y divide-border max-h-[440px] overflow-y-auto">{data.economic_calendar.map((item, index) => <div key={`${item.event_at}-${item.title}-${index}`} className="px-4 py-3 flex items-start gap-3"><div className="w-24 shrink-0"><div className="text-xs font-mono text-text">{new Date(item.event_at).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}</div><div className="text-[10px] text-text-secondary mt-1">{new Date(item.event_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })}</div></div><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="text-xs text-text">{item.title}</span><span className={`text-[10px] border rounded px-1 ${item.impact === '高' ? 'border-up/50 text-up' : 'border-warn/50 text-warn'}`}>{item.impact}</span></div><div className="text-[11px] text-text-secondary mt-1">{item.country} · 预测 {item.forecast || '--'} · 前值 {item.previous || '--'}</div></div></div>)}</div> : <Empty text="未来两周没有返回中高影响事件" />}</section>
        <section className="border border-border rounded-md p-4"><h2 className="text-sm font-semibold text-text flex items-center gap-2"><Waves size={15} className="text-accent" />国内资金面</h2><div className="mt-4 space-y-4"><Liquidity label="北向净流入" value={data.domestic_liquidity.northbound.net_inflow == null ? '--' : `${data.domestic_liquidity.northbound.net_inflow >= 0 ? '+' : ''}${number(data.domestic_liquidity.northbound.net_inflow / 1e8)}亿元`} sub={`${data.domestic_liquidity.northbound.date || '--'} · 连续流入 ${data.domestic_liquidity.northbound.consecutive_inflow_days || 0}日`} valueClass={data.domestic_liquidity.northbound.net_inflow == null ? '' : data.domestic_liquidity.northbound.net_inflow >= 0 ? 'text-up' : 'text-down'} /><Liquidity label="上证指数" value={number(data.domestic_liquidity.turnover.sh_index)} sub={`${data.domestic_liquidity.turnover.date || '--'} · ${signed(data.domestic_liquidity.turnover.sh_change_pct)}`} valueClass={tone(data.domestic_liquidity.turnover.sh_change_pct)} /><Liquidity label="沪市成交额" value={data.domestic_liquidity.turnover.sh_amount == null ? '--' : `${number(data.domestic_liquidity.turnover.sh_amount / 1e8)}亿元`} sub="东方财富市场快照" /><Liquidity label="融资余额" value="--" sub={data.domestic_liquidity.margin_balance.message} /></div></section>
      </div>

      <section className="border border-border rounded-md p-4 mb-5"><h2 className="text-sm font-semibold text-text">盘前五问</h2><div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-3 mt-3">{data.premarket_questions.map((item, index) => <div key={item.id} className="border-l-2 border-accent pl-3 min-w-0"><div className="text-[10px] text-text-secondary">0{index + 1}</div><div className="text-xs text-text mt-1 leading-5">{item.question}</div><div className={`text-[11px] mt-1 leading-5 ${item.status === 'positive' ? 'text-up' : item.status === 'negative' ? 'text-down' : item.status === 'warning' ? 'text-warn' : 'text-text-secondary'}`}>{item.answer}</div></div>)}</div></section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
        <PolicySection title="国际经济" items={data.policy.international_items} />
        <PolicySection title="国内发展政策" items={data.policy.policy_items} />
      </div>

      <section className="border-t border-border pt-4 text-[11px] text-text-secondary"><div className="flex flex-wrap gap-x-4 gap-y-1 mb-2">{Object.entries(data.source_status).map(([source, status]) => <span key={source} className="flex items-center gap-1"><i className={`w-1.5 h-1.5 rounded-full ${status === 'available' ? 'bg-down' : status === 'cache' ? 'bg-warn' : 'bg-border'}`} />{source}</span>)}</div>{data.disclaimer}</section>
    </>}
  </div>;
}

function Liquidity({ label, value, sub, valueClass = '' }: { label: string; value: string; sub: string; valueClass?: string }) { return <div className="border-b border-border pb-3 last:border-b-0"><div className="text-[11px] text-text-secondary">{label}</div><div className={`font-mono text-lg mt-1 ${valueClass || 'text-text'}`}>{value}</div><div className="text-[10px] text-text-secondary mt-1 leading-4">{sub}</div></div>; }
function Empty({ text }: { text: string }) { return <div className="py-16 text-center text-xs text-text-secondary">{text}</div>; }
function PolicySection({ title, items }: { title: string; items: Array<Record<string, any>> }) { return <section className="border border-border rounded-md overflow-hidden"><div className="px-4 py-3 border-b border-border"><h2 className="text-sm font-semibold text-text flex items-center gap-2"><Landmark size={15} className="text-accent" />{title}</h2></div>{items.length ? <div className="divide-y divide-border max-h-96 overflow-y-auto">{items.slice(0, 12).map((item, index) => <article key={`${item.url || item.title}-${index}`} className="px-4 py-3"><div className="text-xs text-text leading-5">{item.title || item.name || '未命名事项'}</div><div className="text-[10px] text-text-secondary mt-1">{item.source || '--'} · {item.published_at || item.date || '--'}</div>{item.summary && <p className="text-[11px] text-text-secondary mt-1 leading-5 line-clamp-2">{item.summary}</p>}</article>)}</div> : <Empty text={`${title}来源当前未返回条目`} />}</section>; }
