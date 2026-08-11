'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, ChartNoAxesCombined, Database, Loader2, RefreshCw } from 'lucide-react';
import PersonalWorkspaceNav from '@/components/PersonalWorkspaceNav';
import StockKlineButton from '@/components/StockKlineButton';
import { apiFetch } from '@/lib/api';

type Period = 'year' | '3m' | '12m';
interface AttributionData {
  period: { id: Period; label: string; start: string; end: string };
  summary: { estimated_return_pct: number; benchmark_return_pct: number | null; alpha_pct: number | null; realized_pnl: number; realized_return_pct: number | null; open_contribution_pct: number; win_count: number; loss_count: number; closed_trade_count: number; win_rate_pct: number | null; average_win_pct: number | null; average_loss_pct: number | null; payoff_ratio: number | null; data_points: number; max_drawdown_pct: number | null; volatility_pct: number | null; sharpe: number | null };
  by_stock: Array<{ code: string; name: string; industry: string; weight_pct: number; return_pct: number | null; contribution_pct: number | null; source: string }>;
  by_industry: Array<{ industry: string; contribution_pct: number }>;
  by_month: Array<{ month: string; realized_pnl: number }>;
  closed_trades: Array<{ code: string; name: string; entry_date: string; exit_date: string; shares: number; entry_price: number; exit_price: number; pnl: number; return_pct: number | null }>;
  warnings: string[];
  data_quality: { total_assets_configured: boolean; complete_trade_logs: boolean; excluded_log_count: number; benchmark_points: number; method: string; limitation: string };
}

const number = (value: number | null | undefined, digits = 2) => value == null ? '--' : value.toFixed(digits);
const signed = (value: number | null | undefined) => value == null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
const money = (value: number | null | undefined) => value == null ? '--' : `¥${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const tone = (value: number | null | undefined) => value == null ? 'text-text-secondary' : value >= 0 ? 'text-up' : 'text-down';

export default function AttributionPage() {
  const [period, setPeriod] = useState<Period>('year');
  const [data, setData] = useState<AttributionData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => { setLoading(true); setError(null); try { const response = await apiFetch<{ data: AttributionData }>(`/personal/attribution?period=${period}`); setData(response.data); } catch (caught) { setError(caught instanceof Error ? caught.message : '归因数据加载失败'); } finally { setLoading(false); } }, [period]);
  useEffect(() => { load(); }, [load]);

  return <div className="max-w-7xl mx-auto px-4 py-5 md:py-6">
    <PersonalWorkspaceNav />
    <header className="flex flex-wrap items-start justify-between gap-4 mb-5"><div><h1 className="text-xl md:text-2xl font-bold text-text flex items-center gap-2"><ChartNoAxesCombined size={22} className="text-accent" />业绩归因</h1><p className="text-xs text-text-secondary mt-1">收益来源、交易质量与基准比较</p></div><div className="flex items-center gap-2"><div className="flex border border-border rounded-md overflow-hidden">{([['year', '今年'], ['3m', '近3月'], ['12m', '近12月']] as Array<[Period, string]>).map(([key, label]) => <button key={key} type="button" onClick={() => setPeriod(key)} className={`px-3 py-2 text-xs ${period === key ? 'bg-accent text-white' : 'text-text-secondary hover:text-text'}`}>{label}</button>)}</div><button type="button" onClick={load} disabled={loading} className="p-2 border border-border rounded-md text-text-secondary" title="刷新"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} /></button></div></header>
    {error && <div className="mb-4 border border-up/50 bg-[#EF535014] rounded-md p-3 text-xs text-up flex gap-2"><AlertTriangle size={15} />{error}</div>}
    {loading && !data ? <div className="py-24 text-center"><Loader2 size={28} className="animate-spin text-accent mx-auto" /><div className="text-xs text-text-secondary mt-3">正在配对交易日志与历史行情</div></div> : data && <>
      {data.warnings.map((warning) => <div key={warning} className="mb-3 border border-warn/50 bg-[#D2992210] rounded-md p-3 text-xs text-warn flex gap-2"><AlertTriangle size={14} className="shrink-0" />{warning}</div>)}
      <section className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 border border-border rounded-md divide-x divide-y lg:divide-y-0 divide-border mb-5">
        <Metric label="组合估算收益" value={signed(data.summary.estimated_return_pct)} className={tone(data.summary.estimated_return_pct)} />
        <Metric label="上证基准" value={signed(data.summary.benchmark_return_pct)} className={tone(data.summary.benchmark_return_pct)} />
        <Metric label="超额 Alpha" value={signed(data.summary.alpha_pct)} className={tone(data.summary.alpha_pct)} />
        <Metric label="已实现盈亏" value={money(data.summary.realized_pnl)} className={tone(data.summary.realized_pnl)} />
        <Metric label="胜率" value={data.summary.win_rate_pct == null ? '--' : `${number(data.summary.win_rate_pct, 1)}%`} />
        <Metric label="盈亏比" value={number(data.summary.payoff_ratio)} />
        <Metric label="最大回撤" value={signed(data.summary.max_drawdown_pct)} className="text-down" />
        <Metric label="夏普" value={number(data.summary.sharpe)} />
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
        <section className="border border-border rounded-md p-4"><div className="flex items-center justify-between"><h2 className="text-sm font-semibold text-text">股票贡献</h2><span className="text-[11px] text-text-secondary">权重 × 区间收益</span></div>{data.by_stock.length ? <div className="mt-3 space-y-3">{data.by_stock.slice(0, 12).map((item) => { const width = Math.min(Math.abs(item.contribution_pct || 0) * 12, 100); return <div key={item.code}><div className="flex items-center justify-between gap-3 text-xs"><StockKlineButton code={item.code} name={item.name} className="min-w-0 truncate text-text">{item.name}<span className="font-mono text-text-secondary ml-2">{item.code}</span></StockKlineButton><span className={`font-mono ${tone(item.contribution_pct)}`}>{signed(item.contribution_pct)}</span></div><div className="relative h-1.5 bg-[#21262D] mt-1.5 rounded overflow-hidden"><div className={`absolute h-full ${Number(item.contribution_pct) >= 0 ? 'bg-up' : 'bg-down'}`} style={{ width: `${width}%`, left: Number(item.contribution_pct) >= 0 ? 0 : undefined, right: Number(item.contribution_pct) < 0 ? 0 : undefined }} /></div><div className="text-[10px] text-text-secondary mt-1">仓位 {number(item.weight_pct, 1)}% · 区间 {signed(item.return_pct)} · {item.source}</div></div>; })}</div> : <Empty text="持仓或历史行情不足" />}</section>
        <section className="border border-border rounded-md p-4"><h2 className="text-sm font-semibold text-text">行业贡献</h2>{data.by_industry.length ? <div className="mt-3 space-y-2">{data.by_industry.map((item) => <div key={item.industry} className="flex items-center gap-3 text-xs"><span className="w-28 truncate text-text-secondary">{item.industry}</span><div className="flex-1 h-2 bg-[#21262D] rounded overflow-hidden"><div className={item.contribution_pct >= 0 ? 'h-full bg-up' : 'h-full bg-down'} style={{ width: `${Math.min(Math.abs(item.contribution_pct) * 14, 100)}%` }} /></div><span className={`w-16 text-right font-mono ${tone(item.contribution_pct)}`}>{signed(item.contribution_pct)}</span></div>)}</div> : <Empty text="暂无行业贡献数据" />}<div className="border-t border-border mt-5 pt-4"><h3 className="text-sm font-semibold text-text">月度已实现盈亏</h3>{data.by_month.length ? <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-2">{data.by_month.map((item) => <div key={item.month} className="border border-border rounded-md px-3 py-2"><div className="text-[11px] text-text-secondary">{item.month}</div><div className={`font-mono text-xs mt-1 ${tone(item.realized_pnl)}`}>{money(item.realized_pnl)}</div></div>)}</div> : <p className="text-xs text-text-secondary mt-3">区间内没有完整卖出交易。</p>}</div></section>
      </div>

      <section className="border border-border rounded-md overflow-hidden mb-5"><div className="px-4 py-3 border-b border-border flex justify-between"><h2 className="text-sm font-semibold text-text">已平仓交易</h2><span className="text-xs text-text-secondary">{data.summary.win_count}胜 / {data.summary.loss_count}负 / {data.summary.closed_trade_count}笔</span></div>{data.closed_trades.length ? <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-xs"><thead className="text-text-secondary border-b border-border"><tr><th className="text-left px-4 py-2.5">股票</th><th className="text-left px-3">买入</th><th className="text-left px-3">卖出</th><th className="text-right px-3">股数</th><th className="text-right px-3">买入价</th><th className="text-right px-3">卖出价</th><th className="text-right px-4">盈亏</th></tr></thead><tbody>{data.closed_trades.map((trade, index) => <tr key={`${trade.code}-${trade.exit_date}-${index}`} className="border-b border-border/60"><td className="px-4 py-3 text-text"><StockKlineButton code={trade.code} name={trade.name} className="text-text">{trade.name}<span className="font-mono text-text-secondary ml-2">{trade.code}</span></StockKlineButton></td><td className="px-3 text-text-secondary">{trade.entry_date}</td><td className="px-3 text-text-secondary">{trade.exit_date}</td><td className="px-3 text-right font-mono">{trade.shares}</td><td className="px-3 text-right font-mono">{number(trade.entry_price)}</td><td className="px-3 text-right font-mono">{number(trade.exit_price)}</td><td className={`px-4 text-right font-mono ${tone(trade.pnl)}`}>{money(trade.pnl)} · {signed(trade.return_pct)}</td></tr>)}</tbody></table></div> : <Empty text="没有可由 FIFO 完整配对的平仓交易" />}</section>

      <section className="border-t border-border pt-4 flex items-start gap-2 text-[11px] text-text-secondary leading-5"><Database size={14} className="shrink-0 mt-0.5" /><div><div>{data.data_quality.method}</div><div>{data.data_quality.limitation}</div><div className="mt-1">日线样本 {data.summary.data_points} · 基准样本 {data.data_quality.benchmark_points} · 排除日志 {data.data_quality.excluded_log_count}</div></div></section>
    </>}
  </div>;
}

function Metric({ label, value, className = '' }: { label: string; value: string; className?: string }) { return <div className="p-3 min-w-0"><div className="text-[11px] text-text-secondary">{label}</div><div className={`font-mono text-sm mt-1 truncate ${className || 'text-text'}`}>{value}</div></div>; }
function Empty({ text }: { text: string }) { return <div className="py-12 text-center text-xs text-text-secondary">{text}</div>; }
