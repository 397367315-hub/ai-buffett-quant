'use client';

import { useEffect, useMemo, useState } from 'react';
import dynamic from 'next/dynamic';
import { AlertTriangle, BarChart3, CheckCircle2, ChevronDown, CircleX, Clock3, Loader2, Play, RefreshCw, Scale } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import type { BackgroundJob, BacktestResult, Strategy } from '../types';

const ReactECharts = dynamic(() => import('echarts-for-react'), { ssr: false });

function inputDate(offsetDays: number) {
  const value = new Date();
  value.setDate(value.getDate() + offsetDays);
  return value.toISOString().slice(0, 10);
}

function signed(value: number, digits = 2) { return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`; }

function Progress({ job }: { job: BackgroundJob }) {
  return <div className="border border-accent/50 bg-[#1F6FEB22] rounded-md p-3"><div className="flex justify-between gap-3 text-xs"><span className="flex items-center gap-2 text-text"><Loader2 size={14} className="animate-spin text-accent" />{job.message}</span><span className="font-mono text-accent">{Math.round(job.progress)}%</span></div><div className="mt-2 bg-bg h-1.5 rounded-full overflow-hidden"><div className="h-full bg-accent transition-all" style={{ width: `${Math.max(2, job.progress)}%` }} /></div></div>;
}

function EquityBars({ values }: { values: BacktestResult['daily_values'] }) {
  const sampled = values.length > 64 ? values.filter((_, index) => index % Math.ceil(values.length / 64) === 0) : values;
  const base = values[0]?.value || 1;
  const returns = sampled.map((item) => (item.value / base - 1) * 100);
  const max = Math.max(...returns.map(Math.abs), 1);
  return <div className="h-28 flex items-end gap-px border-b border-l border-border px-1 pt-2 overflow-hidden" aria-label="策略净值走势">{returns.map((value, index) => <div key={`${sampled[index].date}-${index}`} className={`min-w-[2px] flex-1 ${value >= 0 ? 'bg-up/80' : 'bg-down/80'}`} style={{ height: `${Math.max(4, Math.abs(value) / max * 100)}%` }} title={`${sampled[index].date}: ${signed(value)}`} />)}</div>;
}

function PerformanceRadar({ result }: { result: BacktestResult }) {
  const clamp = (value: number) => Math.max(0, Math.min(100, value));
  const option = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item' },
    radar: {
      radius: '66%', center: ['50%', '52%'],
      indicator: [
        { name: '收益', max: 100 }, { name: '胜率', max: 100 }, { name: '盈亏比', max: 100 },
        { name: '回撤控制', max: 100 }, { name: '夏普', max: 100 }, { name: '样本量', max: 100 },
      ],
      splitNumber: 4,
      axisName: { color: '#8B949E', fontSize: 10 },
      axisLine: { lineStyle: { color: '#30363D' } },
      splitLine: { lineStyle: { color: '#30363D' } },
      splitArea: { areaStyle: { color: ['rgba(22,27,34,.25)', 'rgba(13,17,23,.25)'] } },
    },
    series: [{
      type: 'radar', symbolSize: 4,
      data: [{
        value: [
          clamp((result.total_return + 20) / 0.7), clamp(result.win_rate),
          clamp(result.profit_loss_ratio / 3 * 100), clamp(100 - result.max_drawdown * 4),
          clamp((result.sharpe_ratio + 1) / 4 * 100), clamp(result.completed_trade_count / 50 * 100),
        ],
        areaStyle: { color: 'rgba(88,166,255,.24)' },
        lineStyle: { color: '#58A6FF', width: 1.5 }, itemStyle: { color: '#EF5350' },
      }],
    }],
  };
  return <ReactECharts option={option} style={{ height: 180, width: '100%' }} opts={{ renderer: 'svg' }} />;
}

export default function BacktestPanel({ strategies, initialStrategyId, onResult }: {
  strategies: Strategy[];
  initialStrategyId?: string | null;
  onResult?: (result: BacktestResult) => void;
}) {
  const [strategyId, setStrategyId] = useState('');
  const [startDate, setStartDate] = useState(() => inputDate(-365));
  const [endDate, setEndDate] = useState(() => inputDate(0));
  const [capital, setCapital] = useState(100000);
  const [job, setJob] = useState<BackgroundJob | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [comparison, setComparison] = useState<Array<{ strategy_id: string; strategy_name: string; available: boolean; result?: BacktestResult | null }> | null>(null);
  const [comparing, setComparing] = useState(false);

  useEffect(() => {
    if (initialStrategyId) setStrategyId(initialStrategyId);
  }, [initialStrategyId]);
  useEffect(() => {
    if (!strategyId && strategies[0]) setStrategyId(strategies[0].id);
  }, [strategies, strategyId]);
  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return;
    const poll = async () => {
      try {
        const response = await apiFetch<{ data: BackgroundJob }>(`/quant/backtest/${job.job_id}/status`);
        const next = response.data;
        setJob(next);
        if (next.status === 'completed' && next.result) {
          const backtest = next.result as unknown as BacktestResult;
          setResult(backtest); onResult?.(backtest);
        }
      } catch (caught) { setError(caught instanceof Error ? caught.message : '回测状态读取失败'); }
    };
    poll();
    const timer = window.setInterval(poll, 1500);
    return () => window.clearInterval(timer);
  }, [job?.job_id, job?.status, onResult]);

  const selected = useMemo(() => strategies.find((item) => item.id === strategyId), [strategies, strategyId]);
  const running = Boolean(job && ['queued', 'running'].includes(job.status));
  const start = async () => {
    if (!strategyId) return;
    setError(null); setResult(null);
    try {
      const response = await apiFetch<{ data: BackgroundJob }>(`/quant/backtest/${strategyId}`, { method: 'POST', body: JSON.stringify({ start_date: startDate, end_date: endDate, initial_capital: capital }) });
      setJob(response.data);
    } catch (caught) { setError(caught instanceof Error ? caught.message : '回测启动失败'); }
  };
  const toggleCompare = (id: string) => setCompareIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id].slice(-5));
  const runCompare = async () => {
    if (compareIds.length < 2) return;
    setComparing(true);
    try {
      const response = await apiFetch<{ data: Array<{ strategy_id: string; strategy_name: string; available: boolean; result?: BacktestResult | null }> }>('/quant/compare', { method: 'POST', body: JSON.stringify({ strategy_ids: compareIds }) });
      setComparison(response.data);
    } catch (caught) { setError(caught instanceof Error ? caught.message : '策略比较失败'); }
    setComparing(false);
  };

  if (!strategies.length) return <div className="border border-border rounded-md py-14 text-center text-sm text-text-secondary">先在“策略管理”创建或应用一个策略模板，再运行回测。</div>;
  return <div className="space-y-4">
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-3"><div><h2 className="text-base font-bold text-text flex items-center gap-2"><BarChart3 size={17} className="text-accent" />回测中心</h2><p className="text-xs text-text-secondary mt-1">T 日收盘产生信号，T+1 开盘成交；结果不构成收益承诺。</p></div></div>
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_250px] gap-4">
      <section className="border border-border rounded-md p-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          <label className="text-xs text-text-secondary">策略<select value={strategyId} onChange={(event) => setStrategyId(event.target.value)} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-2 text-text"><option value="">选择策略</option>{strategies.map((strategy) => <option key={strategy.id} value={strategy.id}>{strategy.name}</option>)}</select></label>
          <label className="text-xs text-text-secondary">开始日期<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-2 text-text" /></label>
          <label className="text-xs text-text-secondary">结束日期<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-2 text-text" /></label>
          <label className="text-xs text-text-secondary">初始资金<input type="number" min="10000" step="10000" value={capital} onChange={(event) => setCapital(Number(event.target.value))} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-2 text-text" /></label>
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2"><div className="text-xs text-text-secondary">{selected?.name || '未选择策略'} · 佣金 0.025% · 卖出印花税 0.1% · 单边滑点 0.2%</div><button type="button" onClick={start} disabled={!strategyId || running} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs bg-accent text-white rounded-md hover:brightness-110 disabled:opacity-50"><Play size={14} />{running ? '回测运行中' : '开始回测'}</button></div>
      </section>
      <section className="border border-border rounded-md p-3 text-xs"><div className="text-text-secondary">通过标准</div><div className="mt-2 space-y-1.5"><div className="flex justify-between"><span>总收益率</span><span className="text-up">&gt; 0%</span></div><div className="flex justify-between"><span>已完成交易胜率</span><span className="text-up">&ge; 40%</span></div><div className="flex justify-between"><span>完成交易</span><span className="text-up">至少 1 笔</span></div></div></section>
    </div>
    {running && job && <Progress job={job} />}
    {job?.status === 'failed' && <div className="border border-down/50 bg-[#EF535022] rounded-md p-3 text-xs text-down flex gap-2"><CircleX size={15} />{job.error || '回测失败'}</div>}
    {error && <div className="border border-down/50 bg-[#EF535022] rounded-md p-3 text-xs text-down">{error}</div>}

    {result && <section className="space-y-4">
      <div className={`border rounded-md p-3 flex flex-wrap items-center justify-between gap-2 ${result.passed ? 'border-up/50 bg-[#EF535012]' : 'border-warn/50 bg-[#D2992212]'}`}><div className="flex items-center gap-2 text-sm font-semibold text-text">{result.passed ? <CheckCircle2 size={17} className="text-up" /> : <AlertTriangle size={17} className="text-warn" />}{result.passed ? '满足绩效通过标准' : '未满足绩效通过标准'}</div><div className="text-xs text-text-secondary">{result.period.from} 至 {result.period.to} · {result.params.trading_days} 个交易日</div></div>
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 border border-border rounded-md divide-x divide-y md:divide-y-0 divide-border">{[
        ['总收益', signed(result.total_return), result.total_return >= 0 ? 'text-up' : 'text-down'], ['年化收益', signed(result.annual_return), result.annual_return >= 0 ? 'text-up' : 'text-down'], ['胜率', `${result.win_rate.toFixed(1)}%`, result.win_rate >= 40 ? 'text-up' : 'text-down'], ['盈亏比', result.profit_loss_ratio.toFixed(2), 'text-text'], ['最大回撤', `${result.max_drawdown.toFixed(2)}%`, 'text-down'], ['夏普', result.sharpe_ratio.toFixed(2), result.sharpe_ratio >= 1 ? 'text-up' : 'text-text'],
      ].map(([label, value, color]) => <div key={label as string} className="p-3"><div className="text-xs text-text-secondary">{label}</div><div className={`font-mono mt-1 text-base ${color}`}>{value}</div></div>)}</div>
      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_280px_320px] gap-4"><section className="border border-border rounded-md p-3"><div className="flex justify-between text-sm font-semibold text-text"><span>净值走势</span><span className="font-mono text-text-secondary">期末 {result.params.final_value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}</span></div><EquityBars values={result.daily_values} /></section><section className="border border-border rounded-md p-3"><div className="text-sm font-semibold text-text">绩效雷达</div><PerformanceRadar result={result} /></section><section className="border border-border rounded-md p-3"><div className="text-sm font-semibold text-text mb-2">数据审计</div><div className="text-xs text-text-secondary mb-2">数据等级：<span className={result.data_quality.audit_eligible ? 'text-up' : 'text-warn'}>{result.data_quality.grade}</span></div><div className="space-y-1.5 max-h-36 overflow-y-auto">{result.data_quality.warnings.map((warning, index) => <div key={index} className="text-xs text-warn flex gap-1.5"><AlertTriangle size={12} className="mt-0.5 shrink-0" />{warning}</div>)}</div></section></div>
      <section className="border border-border rounded-md overflow-hidden"><div className="px-3 py-2 text-sm font-semibold text-text border-b border-border flex items-center justify-between"><span>交易明细</span><span className="text-xs font-normal text-text-secondary">{result.trade_count} 笔委托记录，{result.completed_trade_count} 笔已平仓</span></div><div className="overflow-x-auto"><table className="w-full min-w-[800px] text-xs"><thead className="bg-[#161B22] text-text-secondary"><tr><th className="text-left px-3 py-2">成交日</th><th className="text-left px-3 py-2">动作</th><th className="text-left px-3 py-2">股票</th><th className="text-right px-3 py-2">价格</th><th className="text-right px-3 py-2">数量</th><th className="text-right px-3 py-2">盈亏</th><th className="text-left px-3 py-2">原因</th></tr></thead><tbody>{result.trades.slice().reverse().slice(0, 100).map((trade, index) => <tr key={`${trade.date}-${trade.stock_code}-${index}`} className="border-t border-border/70"><td className="px-3 py-2 text-text-secondary">{trade.date}</td><td className={`px-3 py-2 font-semibold ${trade.action === 'buy' ? 'text-up' : 'text-down'}`}>{trade.action === 'buy' ? '买入' : '卖出'}</td><td className="px-3 py-2"><span className="text-text">{trade.stock_name}</span><span className="text-text-secondary ml-1 font-mono">{trade.stock_code}</span></td><td className="px-3 py-2 text-right font-mono">{trade.price.toFixed(2)}</td><td className="px-3 py-2 text-right font-mono">{trade.shares}</td><td className={`px-3 py-2 text-right font-mono ${(trade.profit_pct || 0) >= 0 ? 'text-up' : 'text-down'}`}>{trade.profit_pct == null ? '-' : signed(trade.profit_pct)}</td><td className="px-3 py-2 text-text-secondary max-w-[280px] truncate">{trade.reason}</td></tr>)}</tbody></table></div></section>
    </section>}

    <section className="border-t border-border pt-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="text-sm font-semibold text-text flex items-center gap-2"><Scale size={15} className="text-accent" />策略比较</h3><p className="text-xs text-text-secondary mt-1">选择 2 至 5 个已有回测结果的策略。</p></div><button type="button" onClick={runCompare} disabled={compareIds.length < 2 || comparing} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs border border-border rounded-md text-text-secondary hover:border-accent hover:text-text disabled:opacity-50"><RefreshCw size={13} className={comparing ? 'animate-spin' : ''} />比较</button></div><div className="mt-3 flex flex-wrap gap-2">{strategies.map((strategy) => <label key={strategy.id} className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs border rounded-md cursor-pointer ${compareIds.includes(strategy.id) ? 'border-accent bg-[#1F6FEB22] text-text' : 'border-border text-text-secondary'}`}><input type="checkbox" checked={compareIds.includes(strategy.id)} onChange={() => toggleCompare(strategy.id)} className="accent-[#58A6FF]" />{strategy.name}</label>)}</div>{comparison && <div className="mt-3 overflow-x-auto border border-border rounded-md"><table className="w-full min-w-[620px] text-xs"><thead className="bg-[#161B22] text-text-secondary"><tr><th className="text-left px-3 py-2">策略</th><th className="text-right px-3 py-2">总收益</th><th className="text-right px-3 py-2">胜率</th><th className="text-right px-3 py-2">最大回撤</th><th className="text-right px-3 py-2">夏普</th></tr></thead><tbody>{comparison.map((item) => <tr key={item.strategy_id} className="border-t border-border"><td className="px-3 py-2 text-text">{item.strategy_name}</td>{item.available && item.result ? <><td className={`px-3 py-2 text-right font-mono ${item.result.total_return >= 0 ? 'text-up' : 'text-down'}`}>{signed(item.result.total_return)}</td><td className="px-3 py-2 text-right font-mono">{item.result.win_rate.toFixed(1)}%</td><td className="px-3 py-2 text-right font-mono text-down">{item.result.max_drawdown.toFixed(2)}%</td><td className="px-3 py-2 text-right font-mono">{item.result.sharpe_ratio.toFixed(2)}</td></> : <td colSpan={4} className="px-3 py-2 text-text-secondary">暂无回测结果</td>}</tr>)}</tbody></table></div>}</section>
  </div>;
}
