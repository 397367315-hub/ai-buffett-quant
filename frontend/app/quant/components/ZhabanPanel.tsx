'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Database,
  FlaskConical,
  Loader2,
  Play,
  RefreshCw,
  ShieldAlert,
  SlidersHorizontal,
  WalletCards,
  XCircle,
} from 'lucide-react';
import AddToPersonalPoolButton from '@/components/AddToPersonalPoolButton';
import StockKlineButton from '@/components/StockKlineButton';
import { apiFetch } from '@/lib/api';
import type { BackgroundJob, ZhabanBacktest, ZhabanFactor, ZhabanResearch } from '../types';

type Config = Record<string, any>;

const FALLBACK_CONFIG: Config = {
  id: 'zhaban_resilience_research_v1', name: '炸板韧性研究策略', version: '1.0', board_scope: 'main',
  depth_pct_max: 2, recovery_rate_min: 0.7, absorption_strength_min: 0.4, close_position_min: 0.6,
  failed_limit_rate_max_pct: 30, prior_5d_return_max_pct: 25, turnover_3d_avg_max_pct: 25,
  limit_touch_count_10d_max: 2, require_market_ma20: true, require_sector_linkage: true,
  sector_limit_touch_min: 2, exclude_tail_touch: true, allow_daily_approximation: true,
  allow_unknown_market: false, exclude_st: true, max_candidates: 20, max_positions: 5,
  max_position_pct: 20, holding_days: 3, stop_loss_pct: 5, take_profit_pct: 8,
  take_profit_partial_pct: 50, require_auction_confirmation: false, auction_volume_ratio_min: 2,
  auction_high_open_pct_min: 0, auction_high_open_pct_max: 3, commission_rate: 0.0003,
  stamp_tax_rate: 0.0005, slippage_rate: 0.001,
};

const FALLBACK_FACTORS: ZhabanFactor[] = [
  { key: 'board_scope', label: '独立板块样本', type: 'select', options: [
    { value: 'main', label: '主板 10%' }, { value: 'chinext', label: '创业板 20%' },
    { value: 'star', label: '科创板 20%' }, { value: 'beijing', label: '北交所 30%' },
    { value: 'all', label: '全部（仅分组对比）' },
  ] },
  { key: 'depth_pct_max', label: '炸板深度上限', type: 'number', min: 0.1, max: 10, step: 0.1, unit: '%' },
  { key: 'recovery_rate_min', label: '收复率下限', type: 'number', min: 0, max: 1, step: 0.05 },
  { key: 'absorption_strength_min', label: '吸收强度下限', type: 'number', min: 0, max: 1, step: 0.05 },
  { key: 'close_position_min', label: '收盘位置下限', type: 'number', min: 0, max: 1, step: 0.05 },
  { key: 'failed_limit_rate_max_pct', label: '全市场炸板率上限', type: 'number', min: 0, max: 100, step: 1, unit: '%' },
  { key: 'prior_5d_return_max_pct', label: '前5日累计涨幅上限', type: 'number', min: 0, max: 100, step: 1, unit: '%' },
  { key: 'turnover_3d_avg_max_pct', label: '近3日平均换手上限', type: 'number', min: 0, max: 100, step: 1, unit: '%' },
  { key: 'require_market_ma20', label: '要求上证站上MA20', type: 'boolean' },
  { key: 'require_sector_linkage', label: '要求板块联动', type: 'boolean' },
  { key: 'exclude_tail_touch', label: '排除14:30后首次触板', type: 'boolean' },
  { key: 'allow_daily_approximation', label: '允许日线近似进入研究池', type: 'boolean' },
  { key: 'require_auction_confirmation', label: '要求历史竞价确认', type: 'boolean' },
  { key: 'holding_days', label: '回测最长持有天数', type: 'number', min: 1, max: 10, step: 1, unit: '日' },
];

function readableError(caught: unknown, fallback: string): string {
  const message = caught instanceof Error ? caught.message : '';
  if (['Load failed', 'Failed to fetch', 'NetworkError when attempting to fetch resource.'].includes(message)) {
    return '后端连接暂时中断，已载入内置默认参数；恢复连接后可运行扫描和回测。';
  }
  return message || fallback;
}

const dateInput = (value: Date) => {
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
};

function valueText(value: unknown, digits = 2): string {
  if (value == null || value === '') return '--';
  if (typeof value === 'number' && Number.isFinite(value)) return value.toFixed(digits);
  return String(value);
}

function pct(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function numberClass(value: unknown): string {
  if (typeof value !== 'number') return 'text-text-secondary';
  return value > 0 ? 'text-up' : value < 0 ? 'text-down' : 'text-text-secondary';
}

function StatusBadge({ status }: { status: string }) {
  const passed = status === 'passed' || status === 'research_only';
  const failed = status === 'failed' || status === 'insufficient_data';
  return <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] ${passed ? 'border-up/50 bg-[#26A69A18] text-up' : failed ? 'border-down/50 bg-[#EF535018] text-down' : 'border-warn/50 bg-[#D2992218] text-warn'}`}>
    {passed ? <CheckCircle2 size={11} /> : failed ? <XCircle size={11} /> : <ShieldAlert size={11} />}
    {status === 'research_only' ? '研究模式' : status === 'insufficient_data' ? '数据不足' : status === 'passed' ? '通过' : status === 'failed' ? '未通过' : '待核验'}
  </span>;
}

function ProgressBar({ job, label }: { job: BackgroundJob | null; label: string }) {
  if (!job || !['queued', 'running'].includes(job.status)) return null;
  return <section className="border border-accent/50 bg-[#1F6FEB14] rounded-md p-3">
    <div className="flex items-center justify-between gap-3 text-xs"><span className="inline-flex items-center gap-1.5 text-text"><Loader2 size={14} className="animate-spin text-accent" />{job.message || label}</span><span className="font-mono text-accent">{job.progress}%</span></div>
    <div className="mt-2 h-1.5 overflow-hidden rounded bg-[#21262D]"><div className="h-full bg-accent transition-[width] duration-300" style={{ width: `${Math.max(3, job.progress)}%` }} /></div>
  </section>;
}

function FactorEditor({ factors, config, onChange }: { factors: ZhabanFactor[]; config: Config; onChange: (key: string, value: any) => void }) {
  return <section className="border border-border rounded-md p-3">
    <div className="flex flex-wrap items-center justify-between gap-2 mb-3"><div><h3 className="text-sm font-semibold text-text flex items-center gap-2"><SlidersHorizontal size={15} className="text-accent" />炸板因子微调</h3><p className="mt-1 text-[11px] text-text-secondary">参数只影响研究扫描和回测，不会连接券商下单。</p></div><span className="text-[11px] text-text-secondary">默认来自后端策略版本</span></div>
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-2.5">
      {factors.map((factor) => factor.type === 'boolean' ? <label key={factor.key} className="flex min-h-10 items-center justify-between gap-3 rounded-md border border-border px-2.5 py-2 text-xs text-text-secondary"><span>{factor.label}</span><input type="checkbox" checked={Boolean(config[factor.key])} onChange={(event) => onChange(factor.key, event.target.checked)} className="accent-[#2F81F7]" /></label> : factor.type === 'select' ? <label key={factor.key} className="rounded-md border border-border px-2.5 py-2 text-xs text-text-secondary"><span>{factor.label}</span><select value={String(config[factor.key] ?? '')} onChange={(event) => onChange(factor.key, event.target.value)} className="mt-1.5 w-full rounded border border-border bg-[#0D1117] px-2 py-1.5 text-text focus:border-accent focus:outline-none">{(factor.options || []).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label> : <label key={factor.key} className="rounded-md border border-border px-2.5 py-2 text-xs text-text-secondary"><span>{factor.label}</span><div className="mt-1.5 flex items-center gap-1.5"><input type="number" min={factor.min} max={factor.max} step={factor.step || 0.1} value={config[factor.key] ?? ''} onChange={(event) => onChange(factor.key, Number(event.target.value))} className="min-w-0 flex-1 rounded border border-border bg-[#0D1117] px-2 py-1.5 font-mono text-text focus:border-accent focus:outline-none" /><span>{factor.unit || ''}</span></div></label>)}
    </div>
  </section>;
}

function CandidateTable({ candidates }: { candidates: Array<Record<string, any>> }) {
  return <section className="border border-border rounded-md overflow-hidden">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2.5"><h3 className="text-sm font-semibold text-text flex items-center gap-2"><BarChart3 size={15} className="text-accent" />真炸板研究候选</h3><span className="text-[11px] text-text-secondary">按评分排序 · 日线近似</span></div>
    {candidates.length === 0 ? <div className="py-12 text-center text-xs text-text-secondary">当前没有满足研究条件的真炸板事件</div> : <div className="overflow-x-auto"><table className="w-full min-w-[1120px] text-xs"><thead className="bg-[#161B22] text-text-secondary"><tr><th className="px-3 py-2 text-left">股票</th><th className="px-3 py-2 text-left">事件</th><th className="px-3 py-2 text-right">评分</th><th className="px-3 py-2 text-right">深度</th><th className="px-3 py-2 text-right">收复率</th><th className="px-3 py-2 text-right">吸收强度</th><th className="px-3 py-2 text-right">收盘位置</th><th className="px-3 py-2 text-left">依据</th><th className="px-3 py-2 text-right">个人池</th></tr></thead><tbody>{candidates.map((item) => <tr key={`${item.code}-${item.trade_date}`} className="border-t border-border/70 align-top"><td className="px-3 py-3"><StockKlineButton code={item.code} name={item.name} className="font-medium text-text">{item.name}<span className="ml-2 font-mono text-text-secondary">{item.code}</span></StockKlineButton><div className="mt-1 text-[10px] text-text-secondary">{item.sector || '未分类'} · {item.board} · {item.trade_date}</div></td><td className="px-3 py-3"><StatusBadge status={item.qualification_label === '研究候选' ? 'research_only' : 'unavailable'} /><div className="mt-1 text-[10px] text-text-secondary">{item.event_source === 'daily_bar_approximation' ? '日线近似事件' : '事件池+日线'}</div></td><td className="px-3 py-3 text-right font-mono text-text">{valueText(item.score, 1)}</td><td className="px-3 py-3 text-right font-mono text-text">{valueText(item.depth_pct)}%</td><td className="px-3 py-3 text-right font-mono text-text">{valueText(item.recovery_rate)}</td><td className="px-3 py-3 text-right font-mono text-text">{valueText(item.absorption_strength)}</td><td className="px-3 py-3 text-right font-mono text-text">{valueText(item.close_position_ratio)}</td><td className="max-w-[300px] px-3 py-3 text-text-secondary"><div className="leading-5">{item.basis || '--'}</div>{(item.failed_reasons || []).length > 0 && <div className="mt-1 text-down">未通过：{item.failed_reasons.join('、')}</div>}<details className="mt-1"><summary className="cursor-pointer text-accent">查看规则审计</summary><div className="mt-2 space-y-1.5">{(item.conditions || []).map((condition: Record<string, any>) => <div key={condition.key} className="flex gap-1.5"><span className={condition.status === 'passed' ? 'text-up' : condition.status === 'failed' ? 'text-down' : 'text-warn'}>{condition.status === 'passed' ? '✓' : condition.status === 'failed' ? '×' : '?'}</span><span>{condition.label}：{valueText(condition.actual)}，要求{condition.expected} · {condition.source}</span></div>)}</div></details></td><td className="px-3 py-3 text-right"><AddToPersonalPoolButton code={item.code} name={item.name} industry={item.sector} thesis={`炸板研究：${item.basis || '日线韧性事件候选'}`} source="quant_zhaban" compact /></td></tr>)}</tbody></table></div>}
  </section>;
}

function SummaryMetrics({ summary }: { summary: Record<string, any> }) {
  const items = [
    ['执行交易', `${summary.trade_count || 0}笔`, 'text-text'],
    ['总收益', pct(summary.total_return_pct), numberClass(summary.total_return_pct)],
    ['胜率', summary.win_rate_pct == null ? '--' : `${valueText(summary.win_rate_pct, 1)}%`, 'text-text'],
    ['盈亏比', valueText(summary.profit_loss_ratio, 2), 'text-text'],
    ['最大回撤', pct(-Math.abs(summary.max_drawdown_pct || 0)), 'text-warn'],
    ['连续亏损', `${summary.max_consecutive_losses || 0}笔`, summary.max_consecutive_losses >= 3 ? 'text-warn' : 'text-text'],
  ];
  return <section className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 border border-border rounded-md divide-x divide-y xl:divide-y-0 divide-border">{items.map(([label, value, color]) => <div key={label} className="min-w-0 p-3"><div className="text-[11px] text-text-secondary">{label}</div><div className={`mt-1 truncate font-mono text-base ${color}`}>{value}</div></div>)}</section>;
}

function ReportTable({ title, rows, columns }: { title: string; rows: Array<Record<string, any>>; columns: Array<[string, string]> }) {
  return <section className="border border-border rounded-md overflow-hidden"><div className="border-b border-border px-3 py-2.5 text-sm font-semibold text-text">{title}</div>{rows.length === 0 ? <div className="px-3 py-6 text-xs text-text-secondary">暂无足够数据</div> : <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-xs"><thead className="bg-[#161B22] text-text-secondary"><tr>{columns.map(([key, label]) => <th key={key} className={`px-3 py-2 ${key === 'period' || key === 'label' ? 'text-left' : 'text-right'}`}>{label}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.period || row.key || index}`} className="border-t border-border/70">{columns.map(([key]) => <td key={key} className={`px-3 py-2 font-mono ${key === 'period' || key === 'label' ? 'text-left text-text' : `text-right ${numberClass(row[key])}`}`}>{key.includes('return') ? pct(row[key]) : key === 'win_rate_pct' ? row[key] == null ? '--' : `${valueText(row[key], 1)}%` : valueText(row[key], key === 'total_pnl' ? 2 : 1)}</td>)}</tr>)}</tbody></table></div>}</section>;
}

function ValidationPanel({ validation }: { validation?: Record<string, any> }) {
  if (!validation) return null;
  const checks = Array.isArray(validation.checks) ? validation.checks : [];
  return <section className="border border-border rounded-md overflow-hidden"><div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2.5"><div className="text-sm font-semibold text-text">回测有效性核验</div><StatusBadge status={validation.overall_status || 'unverified'} /></div><div className="divide-y divide-border/70">{checks.map((check: Record<string, any>) => <div key={check.key} className="flex items-start gap-2 px-3 py-2 text-xs"><StatusBadge status={check.status || 'unavailable'} /><div><div className="text-text">{check.label}</div><div className="mt-0.5 text-[11px] leading-5 text-text-secondary">{check.detail}</div></div></div>)}</div><div className="border-t border-border px-3 py-2 text-[11px] leading-5 text-text-secondary">{validation.note}</div></section>;
}

export default function ZhabanPanel() {
  const [factors, setFactors] = useState<ZhabanFactor[]>(FALLBACK_FACTORS);
  const [config, setConfig] = useState<Config>(() => ({ ...FALLBACK_CONFIG }));
  const [research, setResearch] = useState<ZhabanResearch | null>(null);
  const [backtest, setBacktest] = useState<ZhabanBacktest | null>(null);
  const [scanJob, setScanJob] = useState<BackgroundJob | null>(null);
  const [backtestJob, setBacktestJob] = useState<BackgroundJob | null>(null);
  const [targetDate, setTargetDate] = useState('');
  const [startDate, setStartDate] = useState(() => dateInput(new Date(Date.now() - 365 * 86400000)));
  const [endDate, setEndDate] = useState(() => dateInput(new Date()));
  const [capital, setCapital] = useState(100000);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null); setNotice(null);
    try {
      const response = await apiFetch<{ data: { config: { strategy: Config; factors: ZhabanFactor[] }; research: ZhabanResearch | null; backtest: ZhabanBacktest | null; warnings: string[] } }>('/quant/zhaban/bootstrap', { cache: 'no-store' });
      setConfig(response.data.config?.strategy || { ...FALLBACK_CONFIG });
      setFactors(response.data.config?.factors?.length ? response.data.config.factors : FALLBACK_FACTORS);
      setResearch(response.data.research || null);
      setBacktest(response.data.backtest || null);
      if (response.data.warnings?.length) setNotice(response.data.warnings.join('；'));
    } catch (bootstrapError) {
      try {
        const configResponse = await apiFetch<{ data: { strategy: Config; factors: ZhabanFactor[] } }>('/quant/zhaban/config', { cache: 'no-store' });
        setConfig(configResponse.data.strategy || { ...FALLBACK_CONFIG });
        setFactors(configResponse.data.factors?.length ? configResponse.data.factors : FALLBACK_FACTORS);
        setNotice('缓存研究结果暂未读取，策略参数仍可使用。');
      } catch {
        setConfig({ ...FALLBACK_CONFIG });
        setFactors(FALLBACK_FACTORS);
        setError(readableError(bootstrapError, '炸板研究接口暂时无法连接，已载入内置默认参数。'));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const update = (key: string, value: any) => setConfig((current) => ({ ...current, [key]: value }));

  const poll = useCallback((job: BackgroundJob, endpoint: (id: string) => string, done: (data: any) => void, setJob: (job: BackgroundJob | null) => void) => {
    setJob(job);
    const timer = window.setInterval(async () => {
      try {
        const response = await apiFetch<{ data: BackgroundJob & { research?: ZhabanResearch; backtest?: ZhabanBacktest } }>(endpoint(job.job_id));
        setJob(response.data);
        if (response.data.status === 'completed') { window.clearInterval(timer); done(response.data.research || response.data.backtest); }
        if (response.data.status === 'failed') { window.clearInterval(timer); setError(response.data.error || '任务执行失败'); }
      } catch (caught) { window.clearInterval(timer); setError(caught instanceof Error ? caught.message : '任务状态读取失败'); }
    }, 1200);
  }, []);

  const runScan = async () => {
    setError(null);
    try {
      const body: Config = { force: true, config };
      if (targetDate) body.target_date = targetDate;
      const response = await apiFetch<{ data: BackgroundJob }>('/quant/zhaban/scan', { method: 'POST', body: JSON.stringify(body) });
      poll(response.data, (id) => `/quant/zhaban/scan/status/${id}`, (value) => { if (value) setResearch(value as ZhabanResearch); }, setScanJob);
    } catch (caught) { setError(readableError(caught, '炸板扫描启动失败')); }
  };

  const runBacktest = async () => {
    setError(null);
    try {
      const response = await apiFetch<{ data: BackgroundJob }>('/quant/zhaban/backtest', { method: 'POST', body: JSON.stringify({ start_date: startDate, end_date: endDate, initial_capital: capital, config, force: true }) });
      poll(response.data, (id) => `/quant/zhaban/backtest/status/${id}`, (value) => { if (value) setBacktest(value as ZhabanBacktest); }, setBacktestJob);
    } catch (caught) { setError(readableError(caught, '炸板回测启动失败')); }
  };

  const summary = backtest?.summary || {};
  const latestWarnings = useMemo(() => Array.from(new Set([...(research?.warnings || []), ...(backtest?.warnings || [])])), [research?.warnings, backtest?.warnings]);

  if (loading) return <div className="py-16 text-center text-text-secondary"><Loader2 size={28} className="mx-auto animate-spin text-accent" /><div className="mt-3 text-sm">正在读取炸板策略配置与缓存研究结果</div><div className="mx-auto mt-4 h-1.5 w-64 max-w-full overflow-hidden rounded bg-[#21262D]"><div className="h-full w-2/5 bg-accent" /></div></div>;

  return <div className="space-y-4">
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-3"><div><h2 className="flex items-center gap-2 text-base font-bold text-text"><FlaskConical size={18} className="text-accent" />炸板策略研究</h2><p className="mt-1 text-xs text-text-secondary">识别涨停共识被挑战后的价格韧性，日线结果只用于研究候选和模拟回测。</p></div><button type="button" onClick={load} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-xs text-text-secondary hover:border-accent hover:text-text"><RefreshCw size={13} />刷新缓存</button></div>
    <section className="grid grid-cols-1 lg:grid-cols-[1fr_1fr] gap-3">
      <FactorEditor factors={factors} config={config} onChange={update} />
      <section className="border border-border rounded-md p-3"><div className="flex items-center gap-2 text-sm font-semibold text-text"><WalletCards size={15} className="text-accent" />研究执行</div><div className="mt-3 grid grid-cols-2 gap-2 text-xs"><label className="text-text-secondary">事件日期<input type="date" value={targetDate} onChange={(event) => setTargetDate(event.target.value)} className="mt-1 w-full rounded border border-border bg-[#0D1117] px-2 py-1.5 font-mono text-text" /></label><label className="text-text-secondary">回测初始资金<input type="number" min={10000} value={capital} onChange={(event) => setCapital(Number(event.target.value) || 100000)} className="mt-1 w-full rounded border border-border bg-[#0D1117] px-2 py-1.5 font-mono text-text" /></label><label className="text-text-secondary">回测开始<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} className="mt-1 w-full rounded border border-border bg-[#0D1117] px-2 py-1.5 font-mono text-text" /></label><label className="text-text-secondary">回测结束<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} className="mt-1 w-full rounded border border-border bg-[#0D1117] px-2 py-1.5 font-mono text-text" /></label></div><div className="mt-3 flex flex-wrap gap-2"><button type="button" onClick={runScan} disabled={Boolean(scanJob && ['queued', 'running'].includes(scanJob.status))} className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-2 text-xs text-white disabled:opacity-50"><Play size={13} />运行事件扫描</button><button type="button" onClick={runBacktest} disabled={Boolean(backtestJob && ['queued', 'running'].includes(backtestJob.status))} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-xs text-text-secondary hover:border-accent hover:text-text disabled:opacity-50"><BarChart3 size={13} />运行日线回测</button></div><div className="mt-3 text-[11px] leading-5 text-text-secondary">默认：下一交易日开盘成交、最长持有{config.holding_days || 3}日；止损{config.stop_loss_pct || 5}%、达到{config.take_profit_pct || 8}%分批止盈，余仓移至成本线。若打开历史竞价确认而缓存没有竞价数据，回测会明确返回数据不足。</div></section>
    </section>
    <ProgressBar job={scanJob} label="正在扫描炸板事件" />
    <ProgressBar job={backtestJob} label="正在回测炸板策略" />
    {notice && <div className="flex gap-2 rounded-md border border-warn/50 bg-[#D2992212] p-3 text-xs text-warn"><AlertTriangle size={14} className="shrink-0" />{notice}</div>}
    {error && <div className="flex gap-2 rounded-md border border-down/50 bg-[#EF535018] p-3 text-xs text-down"><AlertTriangle size={14} className="shrink-0" />{error}</div>}
    {research && <><section className="flex flex-wrap items-center gap-3 border border-border rounded-md p-3 text-xs"><StatusBadge status={research.status} /><span className="text-text-secondary">数据日 <b className="font-mono font-normal text-text">{research.data_date || '--'}</b></span><span className="text-text-secondary">真炸板 {research.summary?.true_zhaban || 0} · 研究候选 {research.summary?.qualified || 0}</span><span className={research.is_realtime ? 'text-up' : 'text-warn'}>{research.is_realtime ? '实时' : '缓存/日线近似'}</span><span className="ml-auto text-[11px] text-text-secondary">{research.data_quality?.missing_policy}</span></section><CandidateTable candidates={research.candidates || []} /></>}
    {backtest && <section className="space-y-3"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="flex items-center gap-2 text-sm font-semibold text-text"><Database size={15} className="text-accent" />回测报告</h3><span className="text-[11px] text-text-secondary">{backtest.period?.from || '--'} 至 {backtest.period?.to || '--'} · {backtest.data_quality?.board_label || '--'} · {backtest.data_quality?.mode || '--'}</span></div><SummaryMetrics summary={summary} /><div className="grid grid-cols-1 xl:grid-cols-2 gap-3"><ReportTable title="年度表现" rows={backtest.annual || []} columns={[["period", "年份"], ["trade_count", "交易数"], ["total_return_pct", "收益"], ["win_rate_pct", "胜率"], ["max_drawdown_pct", "回撤"]]} /><ReportTable title="月度表现" rows={(backtest.monthly || []).slice(-12)} columns={[["period", "月份"], ["trade_count", "交易数"], ["total_return_pct", "收益"], ["win_rate_pct", "胜率"], ["max_drawdown_pct", "回撤"]]} /></div><div className="grid grid-cols-1 xl:grid-cols-2 gap-3"><ReportTable title="板块独立表现" rows={backtest.board_performance || []} columns={[["label", "板块"], ["candidate_count", "候选"], ["trade_count", "交易数"], ["total_pnl", "盈亏"], ["win_rate_pct", "胜率"]]} /><ReportTable title="成本敏感性" rows={backtest.cost_sensitivity || []} columns={[["label", "情景"], ["trade_count", "交易数"], ["total_return_pct", "收益"], ["max_drawdown_pct", "回撤"]]} /></div><div className="border border-border rounded-md p-3 text-xs"><div className="font-semibold text-text mb-2">样本外与执行审计</div><div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-text-secondary"><span>样本切分：<b className="font-mono font-normal text-text">{summary.sample_split_date || '--'}</b></span><span>样本外收益：<b className={`font-mono font-normal ${numberClass(summary.out_of_sample?.total_return_pct)}`}>{pct(summary.out_of_sample?.total_return_pct)}</b></span><span>最差月份：<b className="font-mono font-normal text-text">{summary.worst_month?.period || '--'}</b></span><span>跳过边界：<b className="font-mono font-normal text-text">{summary.skipped?.period_boundary || 0}</b></span><span>竞价核验：<b className="font-mono font-normal text-warn">{backtest.data_quality?.auction_verified_count || 0}条</b></span></div></div><ValidationPanel validation={backtest.validation} /></section>}
    {latestWarnings.length > 0 && <section className="border border-warn/50 bg-[#D2992212] rounded-md p-3"><div className="flex items-center gap-2 text-sm font-semibold text-warn"><ShieldAlert size={15} />数据与研究边界</div><div className="mt-2 space-y-1 text-[11px] leading-5 text-text-secondary">{latestWarnings.slice(0, 8).map((warning) => <div key={warning}>• {warning}</div>)}</div></section>}
    <p className="text-[11px] text-text-secondary">{backtest?.disclaimer || research?.disclaimer || '本模块只用于研究与模拟，不构成投资建议。'}</p>
  </div>;
}
