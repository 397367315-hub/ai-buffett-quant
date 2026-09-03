'use client';

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Activity, AlertTriangle, ArrowRightLeft, BarChart3, Database, Flame, Gauge, RefreshCw, ShieldAlert, TrendingDown, TrendingUp, Users } from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';

type AnyMap = Record<string, any>;
interface BreadthEntry { up: number; down: number; flat?: number; total: number; ratio: number; source?: string }
interface SentimentData {
  available: boolean; score: number | null; label: string; details: string[]; breadth: Record<string, BreadthEntry>; turnover: AnyMap;
  limit_counts: { up: number; down: number }; main_flow_trend: string | null; main_flow_amount: number | null; emotion: AnyMap;
  psychology_state: string; risk_level: string; history: AnyMap[]; source: string; data_date: string | null; updated_at: string; is_realtime: boolean;
}

function finite(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value); }
function pct(value: unknown, digits = 1): string { return finite(value) ? `${value.toFixed(digits)}%` : '--'; }
function money(value: unknown): string {
  if (!finite(value)) return '--'; const abs = Math.abs(value); const result = abs >= 1e8 ? `${(abs / 1e8).toFixed(2)}亿` : abs >= 1e4 ? `${(abs / 1e4).toFixed(1)}万` : abs.toFixed(0);
  return `${value > 0 ? '+' : value < 0 ? '-' : ''}${result}`;
}
function tone(value: unknown): string { return !finite(value) || value === 0 ? 'text-text-secondary' : value > 0 ? 'text-up' : 'text-down'; }
function scoreColor(score: number | null): string { return score == null ? '#667085' : score >= 65 ? '#EF5350' : score >= 43 ? '#D9A441' : '#26A69A'; }

function Panel({ title, icon: Icon, children, className = '' }: { title: string; icon: typeof Gauge; children: ReactNode; className?: string }) {
  return <section className={`min-w-0 overflow-hidden rounded-md border border-border bg-card ${className}`}><header className="flex items-center gap-2 border-b border-border px-4 py-3"><Icon size={15} className="text-accent" /><h2 className="text-sm font-semibold text-text">{title}</h2></header>{children}</section>;
}

function Metric({ label, value, hint, className = 'text-text', icon }: { label: string; value: string; hint?: string; className?: string; icon?: ReactNode }) {
  return <div className="min-w-0 bg-card px-3 py-3"><div className="flex items-center gap-1.5 text-[11px] text-text-secondary">{icon}{label}</div><div className={`mt-1.5 truncate font-mono text-xl font-semibold ${className}`} title={value}>{value}</div><div className="mt-1 min-h-4 truncate text-[10px] text-text-secondary" title={hint}>{hint || ' '}</div></div>;
}

function HistoryBars({ history }: { history: AnyMap[] }) {
  if (!history.length) return <div className="grid h-32 place-items-center text-xs text-text-secondary">暂无连续情绪历史</div>;
  const max = Math.max(...history.map((item) => Math.max(Number(item.up_count) || 0, Number(item.down_count) || 0)), 1);
  return <div className="flex h-36 items-end gap-2 px-1 pt-5">{history.map((item, index) => <div key={`${item.trade_date}-${index}`} className="flex min-w-0 flex-1 flex-col items-center gap-1"><div className="flex h-24 w-full items-end justify-center gap-1"><i className="block w-[38%] bg-up/80" style={{ height: `${Math.max(3, (Number(item.up_count) || 0) / max * 100)}%` }} title={`上涨 ${item.up_count ?? '--'}`} /><i className="block w-[38%] bg-down/80" style={{ height: `${Math.max(3, (Number(item.down_count) || 0) / max * 100)}%` }} title={`下跌 ${item.down_count ?? '--'}`} /></div><span className="max-w-full truncate font-mono text-[9px] text-text-secondary">{String(item.trade_date || '').slice(5)}</span></div>)}</div>;
}

export default function SentimentPage() {
  const [data, setData] = useState<SentimentData | null>(null); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const load = useCallback(async (refresh = false) => { setLoading(true); setError(''); try { const response = await apiFetch<{ data: SentimentData }>(`/market/sentiment${refresh ? '?refresh=true' : ''}`, { timeoutMs: 35000 }); setData(response.data); } catch (caught) { setError(friendlyApiError(caught, '市场情绪数据暂时无法读取')); } finally { setLoading(false); } }, []);
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 60000); return () => window.clearInterval(timer); }, [load]);
  const breadthEntries = useMemo(() => Object.entries(data?.breadth || {}), [data]); const fullBreadth = breadthEntries.find(([name]) => name === '全市场')?.[1] || breadthEntries[0]?.[1];
  const emotion = data?.emotion || {}; const score = data?.score ?? null; const color = scoreColor(score);
  const action = score == null ? '等待可核验数据' : score >= 75 ? '情绪过热，控制追高' : score >= 60 ? '偏强，关注分化' : score >= 43 ? '中性，按结构选择' : score >= 30 ? '偏弱，降低进攻仓位' : '恐慌区，等待止跌确认';

  if (loading && !data) return <main className="grid min-h-[70vh] place-items-center"><div className="text-center text-sm text-text-secondary"><RefreshCw size={22} className="mx-auto mb-3 animate-spin text-accent" />正在汇总市场宽度、涨跌停与猫爪情绪数据</div></main>;
  return <main className="mx-auto w-full max-w-[1540px] px-3 py-5 sm:px-5">
    <header className="flex flex-col gap-4 border-b border-border pb-4 lg:flex-row lg:items-end lg:justify-between"><div><h1 className="flex items-center gap-2 text-xl font-semibold text-text sm:text-2xl"><Gauge size={22} className="text-accent" />市场情绪仪表盘</h1><p className="mt-1 text-xs text-text-secondary">市场宽度、涨跌停质量、连板晋级、主力资金与成交活跃度联合判断</p><div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-text-secondary"><span className="inline-flex items-center gap-1"><Database size={12} />{data?.source?.includes('numcat') ? '猫爪 + 行情聚合' : data?.source || '数据源未标注'}</span><span>数据日 {data?.data_date || '--'}</span><span>{data?.is_realtime ? '盘中实时' : '最近收盘/缓存'}</span><span>更新 {data?.updated_at?.slice(0, 16).replace('T', ' ') || '--'}</span></div></div><button type="button" onClick={() => void load(true)} disabled={loading} className="inline-flex h-9 items-center justify-center gap-1.5 rounded-md border border-border px-3 text-xs text-text-secondary hover:border-accent hover:text-text disabled:opacity-50"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} />刷新全部</button></header>
    {error && <div className="my-4 flex items-start gap-2 border border-warn/40 bg-warn/5 px-3 py-3 text-xs text-warn"><AlertTriangle size={14} />{error}</div>}
    {!data?.available ? <div className="mt-4 grid min-h-64 place-items-center border border-border text-sm text-text-secondary">当前没有可核验的情绪数据</div> : <>
      <div className="mt-4 grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        <Panel title="综合情绪温度" icon={Gauge}><div className="grid min-h-[286px] place-items-center px-4 py-5"><div className="text-center"><div className="relative mx-auto h-36 w-36 rounded-full p-3" style={{ background: `conic-gradient(${color} ${Math.max(0, Number(score) || 0) * 3.6}deg, #252C36 0deg)` }}><div className="grid h-full w-full place-items-center rounded-full bg-card"><div><strong className="block font-mono text-4xl" style={{ color }}>{score ?? '--'}</strong><span className="text-[10px] text-text-secondary">/ 100</span></div></div></div><div className="mt-4 text-lg font-semibold" style={{ color }}>{data.label}</div><div className="mt-1 text-xs text-text-secondary">心理阶段：{data.psychology_state}</div><div className="mt-4 border-l-2 border-accent bg-bg px-3 py-2 text-left text-xs leading-5 text-text">{action}</div></div></div></Panel>
        <div className="grid grid-cols-2 gap-px border border-border bg-border md:grid-cols-4">
          <Metric label="上涨家数" value={fullBreadth ? `${fullBreadth.up}只` : '--'} hint={fullBreadth ? `占比 ${pct(fullBreadth.ratio)}` : '宽度未返回'} className="text-up" icon={<TrendingUp size={13} />} />
          <Metric label="下跌家数" value={fullBreadth ? `${fullBreadth.down}只` : '--'} hint={fullBreadth ? `平盘 ${fullBreadth.flat ?? '--'}只` : '宽度未返回'} className="text-down" icon={<TrendingDown size={13} />} />
          <Metric label="涨停 / 跌停" value={`${data.limit_counts.up} / ${data.limit_counts.down}`} hint={`最高连板 ${emotion.max_streak_height ?? '--'}板`} className="text-text" icon={<Flame size={13} />} />
          <Metric label="炸板率" value={pct(emotion.failed_limit_rate)} hint={`炸板 ${emotion.failed_limit_count ?? '--'}只`} className={finite(emotion.failed_limit_rate) && emotion.failed_limit_rate >= 35 ? 'text-down' : 'text-warn'} icon={<ShieldAlert size={13} />} />
          <Metric label="连板晋级率" value={pct(emotion.promotion_rate)} hint={`1进2 ${pct(emotion.promotion_rate_1_to_2)}`} className="text-warn" icon={<BarChart3 size={13} />} />
          <Metric label="二板及以上" value={emotion.second_board_or_higher_count == null ? '--' : `${emotion.second_board_or_higher_count}只`} hint={`三板及以上 ${emotion.third_board_or_higher_count ?? '--'}只`} icon={<Activity size={13} />} />
          <Metric label="主力资金" value={data.main_flow_trend || '--'} hint={money(data.main_flow_amount)} className={tone(data.main_flow_amount)} icon={<ArrowRightLeft size={13} />} />
          <Metric label="风险等级" value={data.risk_level || '--'} hint={`高位负反馈 ${emotion.high_negative_feedback_count ?? '--'}只`} className={data.risk_level === '高' ? 'text-down' : data.risk_level === '中' ? 'text-warn' : 'text-up'} icon={<ShieldAlert size={13} />} />
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1.25fr_.75fr]">
        <Panel title="近十日赚钱效应" icon={Users}><div className="p-4"><div className="flex items-center gap-4 text-[10px] text-text-secondary"><span className="inline-flex items-center gap-1"><i className="h-2 w-2 bg-up" />上涨家数</span><span className="inline-flex items-center gap-1"><i className="h-2 w-2 bg-down" />下跌家数</span></div><HistoryBars history={data.history || []} /></div></Panel>
        <Panel title="成交与资金温度" icon={Activity}><div className="grid grid-cols-2 gap-px bg-border"><Metric label="全市场成交额" value={money(emotion.market_amount)} hint={finite(emotion.market_amount_change) ? `较前日 ${money(emotion.market_amount_change)}` : '较前日变化未返回'} /><Metric label="预测成交额" value={money(emotion.market_amount_forecast)} hint={finite(emotion.market_amount_forecast_change_pct) ? `预计变化 ${pct(emotion.market_amount_forecast_change_pct)}` : '盘中预测口径'} /><Metric label="大涨超7%" value={emotion.up_7pct_count == null ? '--' : `${emotion.up_7pct_count}只`} className="text-up" /><Metric label="大跌超7%" value={emotion.down_7pct_count == null ? '--' : `${emotion.down_7pct_count}只`} className="text-down" /></div></Panel>
      </div>

      <Panel title="当前情绪判断" icon={Activity} className="mt-4"><div className="grid gap-0 md:grid-cols-2"><div className="border-b border-border p-4 md:border-b-0 md:border-r"><h3 className="text-xs font-semibold text-text">数据事实</h3><div className="mt-2 space-y-2">{data.details?.map((detail) => <div key={detail} className="border-l-2 border-accent/60 pl-3 text-xs leading-5 text-text-secondary">{detail}</div>)}</div></div><div className="p-4"><h3 className="text-xs font-semibold text-text">使用边界</h3><div className="mt-2 space-y-2 text-xs leading-5 text-text-secondary"><p>情绪指数用于判断交易环境，不单独产生买卖结论。</p><p>情绪过热时关注炸板率和高位负反馈；情绪恐慌时仍需等待价格、成交与核心股止跌确认。</p><p>非交易时段展示最近有效交易日数据，页面会明确标记为收盘或缓存口径。</p></div></div></div></Panel>
    </>}
  </main>;
}
