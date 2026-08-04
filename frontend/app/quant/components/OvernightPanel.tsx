'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, Clock3, Database, Loader2, LogOut, MoonStar, Play, RefreshCw, ShieldAlert } from 'lucide-react';
import AddToPersonalPoolButton from '@/components/AddToPersonalPoolButton';
import { apiFetch } from '@/lib/api';

type RunStatus = 'queued' | 'running' | 'completed' | 'partial' | 'unavailable' | 'failed';

interface Condition {
  key: string;
  label: string;
  status: 'passed' | 'failed' | 'unavailable';
  actual: unknown;
  expected: string;
  source: string;
  detail?: string;
}

interface Candidate {
  code: string;
  name: string;
  sector: string;
  price: number | null;
  change_pct: number | null;
  volume_ratio: number | null;
  turnover: number | null;
  market_cap_yi: number | null;
  score: number;
  daily_passed: boolean;
  minute_passed: boolean | null;
  qualified: boolean;
  selected_for_entry: boolean;
  failed_reasons: string[];
  unavailable_reasons: string[];
  conditions: Condition[];
  minute?: { latest_bar_at: string | null; market_price: number | null; entry_price: number | null } | null;
}

interface OvernightRun {
  id: number;
  stage: string;
  trigger: string;
  status: RunStatus;
  progress: number;
  message: string;
  data_date: string | null;
  is_realtime: boolean;
  scanned_count: number;
  prefiltered_count: number;
  qualified_count: number;
  candidates: Candidate[];
  data_quality: Record<string, any>;
  error: string | null;
  created_at: string | null;
  finished_at: string | null;
}

interface Position {
  id: number;
  code: string;
  name: string;
  sector: string;
  status: 'open' | 'closed';
  strategy_tag: string;
  shares: number;
  entry_at: string;
  entry_price: number;
  cost_value: number;
  allocated_pct: number;
  current_price: number | null;
  market_value: number | null;
  exit_at: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  pnl: number | null;
  pnl_pct: number | null;
}

interface Dashboard {
  updated_at: string;
  strategy: Record<string, any>;
  active_run: OvernightRun | null;
  latest_entry_run: OvernightRun | null;
  latest_preliminary_run: OvernightRun | null;
  runs: OvernightRun[];
  positions: Position[];
  open_positions: Position[];
  closed_positions: Position[];
  performance: {
    positions: number;
    open: number;
    closed: number;
    wins: number;
    losses: number;
    win_rate: number | null;
    cost_value: number | null;
    pnl: number | null;
    pnl_pct: number | null;
  };
  quote: { available: boolean; source: string; data_date: string | null; is_realtime: boolean; cache_used: boolean };
  minute_coverage: { bar_count: number; stock_count: number; from: string | null; to: string | null; collection_mode: string };
  backtest: { available: boolean; grade: string; reason: string; requirements: string[] };
  disclaimer: string;
}

const stageLabel: Record<string, string> = {
  preliminary: '14:30预扫描', entry: '14:50入场复核', exit: '早盘退出检查', force_exit: '10:00强制退出',
};

const money = (value: number | null | undefined) => value == null ? '--' : `¥${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const number = (value: number | null | undefined, digits = 2) => value == null ? '--' : value.toFixed(digits);
const signed = (value: number | null | undefined) => value == null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
const pnlClass = (value: number | null | undefined) => value == null ? 'text-text-secondary' : value >= 0 ? 'text-up' : 'text-down';
const time = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '--';

function actualText(value: unknown) {
  if (value == null || value === '') return '--';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export default function OvernightPanel() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadProgress, setLoadProgress] = useState(10);
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const response = await apiFetch<{ data: Dashboard }>('/quant/overnight');
      setData(response.data);
      setLoadProgress(100);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '一夜持股策略加载失败');
    } finally {
      if (!quiet) window.setTimeout(() => setLoading(false), 100);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!loading) return;
    const timer = window.setInterval(() => setLoadProgress((value) => Math.min(92, value + 6)), 400);
    return () => window.clearInterval(timer);
  }, [loading]);
  useEffect(() => {
    if (!data?.active_run || !['queued', 'running'].includes(data.active_run.status)) return;
    const timer = window.setInterval(() => load(true), 1800);
    return () => window.clearInterval(timer);
  }, [data?.active_run?.id, data?.active_run?.status, load]);

  const run = async (stage: string) => {
    setSubmitting(stage); setError(null);
    try {
      await apiFetch('/quant/overnight/runs', { method: 'POST', body: JSON.stringify({ stage }) });
      await load(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '策略任务提交失败');
    } finally {
      setSubmitting(null);
    }
  };

  const latest = [data?.latest_entry_run, data?.latest_preliminary_run]
    .filter((item): item is OvernightRun => Boolean(item))
    .sort((left, right) => right.id - left.id)[0];
  const candidates = useMemo(() => latest?.candidates || [], [latest]);
  const active = data?.active_run;

  if (loading && !data) return <div className="py-20 text-center"><Loader2 size={28} className="animate-spin text-accent mx-auto" /><div className="text-sm text-text mt-3">正在读取分钟缓存与策略审计</div><div className="h-1.5 max-w-sm mx-auto bg-[#21262D] mt-5 overflow-hidden rounded"><div className="h-full bg-accent transition-all" style={{ width: `${loadProgress}%` }} /></div><div className="text-xs font-mono text-text-secondary mt-2">{loadProgress}%</div></div>;

  return <div className="space-y-4">
    <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
      <div><h2 className="text-base font-bold text-text flex items-center gap-2"><MoonStar size={18} className="text-accent" />一夜持股</h2><p className="text-xs text-text-secondary mt-1">交易日14:30预扫 · 14:50分钟复核 · 次日10:00前退出</p></div>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => run('preliminary')} disabled={Boolean(active) || Boolean(submitting)} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs border border-border text-text-secondary rounded-md hover:border-accent hover:text-text disabled:opacity-50"><RefreshCw size={14} className={submitting === 'preliminary' ? 'animate-spin' : ''} />预扫描</button>
        <button type="button" onClick={() => run('entry')} disabled={Boolean(active) || Boolean(submitting)} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs bg-accent text-white rounded-md disabled:opacity-50"><Play size={14} />尾盘复核</button>
        <button type="button" onClick={() => run('exit')} disabled={Boolean(active) || Boolean(submitting)} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs border border-up/50 text-up rounded-md disabled:opacity-50"><LogOut size={14} />退出检查</button>
      </div>
    </header>

    {error && <div className="border border-down/50 bg-[#EF535018] rounded-md p-3 text-xs text-down flex gap-2"><AlertTriangle size={15} className="shrink-0" />{error}</div>}
    {active && <section className="border border-accent/50 rounded-md p-3"><div className="flex justify-between gap-3 text-xs"><span className="text-text flex items-center gap-2"><Loader2 size={14} className="animate-spin text-accent" />{active.message}</span><span className="font-mono text-accent">{active.progress}%</span></div><div className="h-1.5 bg-[#21262D] mt-2 overflow-hidden rounded"><div className="h-full bg-accent transition-all" style={{ width: `${Math.max(3, active.progress)}%` }} /></div></section>}

    {data && <section className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 border border-border rounded-md divide-x divide-y xl:divide-y-0 divide-border">
      <Metric label="开放仓位" value={`${data.performance.open}只`} />
      <Metric label="已完成交易" value={`${data.performance.closed}笔`} />
      <Metric label="真实样本胜率" value={data.performance.win_rate == null ? '待积累' : `${data.performance.win_rate.toFixed(1)}%`} />
      <Metric label="模拟成本" value={money(data.performance.cost_value)} />
      <Metric label="净盈亏" value={money(data.performance.pnl)} className={pnlClass(data.performance.pnl)} />
      <Metric label="收益率" value={signed(data.performance.pnl_pct)} className={pnlClass(data.performance.pnl_pct)} />
    </section>}

    <section className="border border-border rounded-md overflow-hidden">
      <div className="px-3 py-2.5 border-b border-border flex flex-wrap items-center gap-x-4 gap-y-1"><h3 className="text-sm font-semibold text-text">执行规则</h3><span className="text-[11px] text-text-secondary">参考资金100万 · 单股100股且不超过10% · 总仓位不超过50%</span></div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 divide-y sm:divide-y-0 sm:divide-x divide-border text-xs">
        <RuleBand title="行情活跃度" text="涨幅3%-5% · 量比>1.2 · 换手3%-9%" />
        <RuleBand title="规模与趋势" text="市值40-230亿 · MA10>MA20>MA30 · 站上MA5/MA10" />
        <RuleBand title="硬性排雷" text="ST/次新/近5日跌停/重大利空/财报前3日" />
        <RuleBand title="分钟确认" text="最近5分钟涨幅<=2% · 排除脉冲爆量 · 0.618保护" />
      </div>
    </section>

    {latest && <section className="border border-border rounded-md overflow-hidden">
      <div className="px-3 py-2.5 border-b border-border flex flex-wrap items-center gap-x-4 gap-y-1"><h3 className="text-sm font-semibold text-text">最近扫描</h3><span className="text-xs text-text-secondary">{stageLabel[latest.stage] || latest.stage} · {latest.message}</span><span className="sm:ml-auto text-xs font-mono text-text-secondary">{latest.data_date || '--'}</span></div>
      <div className="px-3 py-2 text-xs text-text-secondary border-b border-border flex flex-wrap gap-x-4 gap-y-1"><span>扫描 {latest.scanned_count}只</span><span>静态通过 {latest.prefiltered_count}只</span><span>最终合格 {latest.qualified_count}只</span><span className={latest.is_realtime ? 'text-up' : 'text-warn'}>{latest.is_realtime ? '当日实时' : '非实时/不可执行'}</span></div>
      {candidates.length ? <div className="overflow-x-auto"><table className="w-full min-w-[1050px] text-xs"><thead className="text-text-secondary bg-[#161B22]"><tr><th className="text-left px-3 py-2">股票</th><th className="text-right px-3">评分</th><th className="text-right px-3">涨幅</th><th className="text-right px-3">量比</th><th className="text-right px-3">换手</th><th className="text-right px-3">市值</th><th className="text-left px-3">状态</th><th className="text-left px-3">分钟证据</th><th className="text-right px-3">个人池</th></tr></thead><tbody>{candidates.map((candidate) => <CandidateRow key={candidate.code} candidate={candidate} />)}</tbody></table></div> : <Empty text="本轮没有股票通过3%-5%涨幅、量比、换手率和市值预筛" />}
    </section>}

    {data && <section className="border border-border rounded-md overflow-hidden">
      <div className="px-3 py-2.5 border-b border-border"><h3 className="text-sm font-semibold text-text">模拟持仓与已完成交易</h3></div>
      {data.positions.length ? <div className="overflow-x-auto"><table className="w-full min-w-[980px] text-xs"><thead className="text-text-secondary bg-[#161B22]"><tr><th className="text-left px-3 py-2">股票</th><th className="text-left px-3">状态</th><th className="text-right px-3">入场价</th><th className="text-right px-3">当前/退出价</th><th className="text-right px-3">成本</th><th className="text-right px-3">净盈亏</th><th className="text-left px-3">离场纪律</th><th className="text-right px-3">个人池</th></tr></thead><tbody>{data.positions.map((position) => <PositionRow key={position.id} position={position} />)}</tbody></table></div> : <Empty text="尚无完全通过分钟条件的模拟持仓" />}
    </section>}

    {data && <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <section className="border border-border rounded-md p-3"><h3 className="text-sm font-semibold text-text flex items-center gap-2"><Database size={15} className="text-accent" />分钟数据覆盖</h3><div className="grid grid-cols-3 gap-3 mt-3 text-xs"><MetricInline label="股票" value={`${data.minute_coverage.stock_count}只`} /><MetricInline label="分钟K线" value={`${data.minute_coverage.bar_count}条`} /><MetricInline label="区间" value={data.minute_coverage.from ? `${data.minute_coverage.from.slice(0, 10)} 至 ${data.minute_coverage.to?.slice(0, 10)}` : '待采集'} /></div><p className="text-[11px] text-text-secondary mt-3">{data.minute_coverage.collection_mode}</p></section>
      <section className="border border-warn/50 rounded-md p-3"><h3 className="text-sm font-semibold text-text flex items-center gap-2"><ShieldAlert size={15} className="text-warn" />回测审计</h3><p className="text-xs text-warn mt-2">{data.backtest.reason}</p><div className="mt-2 flex flex-wrap gap-1.5">{data.backtest.requirements.map((item) => <span key={item} className="text-[10px] border border-border rounded px-1.5 py-0.5 text-text-secondary">{item}</span>)}</div></section>
    </div>}
    {data && <p className="text-[11px] text-text-secondary">{data.disclaimer}</p>}
  </div>;
}

function Metric({ label, value, className = '' }: { label: string; value: string; className?: string }) {
  return <div className="p-3 min-w-0"><div className="text-[11px] text-text-secondary">{label}</div><div className={`font-mono text-sm mt-1 truncate ${className || 'text-text'}`}>{value}</div></div>;
}

function MetricInline({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0"><div className="text-text-secondary">{label}</div><div className="text-text font-mono mt-1 break-words">{value}</div></div>;
}

function RuleBand({ title, text }: { title: string; text: string }) {
  return <div className="px-3 py-3"><div className="text-text font-medium">{title}</div><div className="text-text-secondary mt-1 leading-5">{text}</div></div>;
}

function Empty({ text }: { text: string }) {
  return <div className="py-12 text-center text-xs text-text-secondary">{text}</div>;
}

function CandidateRow({ candidate }: { candidate: Candidate }) {
  const state = candidate.selected_for_entry ? '已模拟买入' : candidate.qualified ? '合格未入场' : candidate.daily_passed && candidate.minute_passed == null ? '待分钟复核' : candidate.unavailable_reasons.length ? '数据不足' : '未通过';
  const stateClass = candidate.selected_for_entry ? 'text-up border-up/50' : candidate.qualified ? 'text-accent border-accent/50' : candidate.unavailable_reasons.length ? 'text-warn border-warn/50' : 'text-down border-down/50';
  return <tr className="border-t border-border/70 align-top"><td className="px-3 py-3"><div className="text-text font-medium">{candidate.name}<span className="font-mono text-text-secondary ml-2">{candidate.code}</span></div><div className="text-[10px] text-text-secondary mt-1">{candidate.sector || '板块未返回'}</div></td><td className="px-3 py-3 text-right font-mono text-text">{number(candidate.score, 1)}</td><td className={`px-3 py-3 text-right font-mono ${pnlClass(candidate.change_pct)}`}>{signed(candidate.change_pct)}</td><td className="px-3 py-3 text-right font-mono text-text">{number(candidate.volume_ratio)}</td><td className="px-3 py-3 text-right font-mono text-text">{number(candidate.turnover)}%</td><td className="px-3 py-3 text-right font-mono text-text">{number(candidate.market_cap_yi, 1)}亿</td><td className="px-3 py-3"><span className={`inline-block border rounded px-1.5 py-0.5 ${stateClass}`}>{state}</span><details className="mt-2"><summary className="text-accent cursor-pointer">规则审计</summary><div className="mt-2 w-[320px] space-y-1.5">{candidate.conditions.map((item) => <div key={item.key} className="grid grid-cols-[14px_1fr] gap-1.5"><span className={item.status === 'passed' ? 'text-up' : item.status === 'failed' ? 'text-down' : 'text-warn'}>{item.status === 'passed' ? '✓' : item.status === 'failed' ? '×' : '?'}</span><div><div className="text-text">{item.label}：{actualText(item.actual)}</div><div className="text-[10px] text-text-secondary">要求 {item.expected} · {item.source}</div>{item.detail && <div className="text-[10px] text-text-secondary">{item.detail}</div>}</div></div>)}</div></details></td><td className="px-3 py-3 text-text-secondary"><div>{candidate.minute?.latest_bar_at ? time(candidate.minute.latest_bar_at) : '--'}</div><div className="font-mono mt-1">成交参考 {candidate.minute?.entry_price ? `¥${number(candidate.minute.entry_price, 4)}` : '--'}</div></td><td className="px-3 py-3 text-right"><AddToPersonalPoolButton code={candidate.code} name={candidate.name} industry={candidate.sector} thesis={`一夜持股：评分${candidate.score}，${state}`} source="quant_overnight" compact /></td></tr>;
}

function PositionRow({ position }: { position: Position }) {
  return <tr className="border-t border-border/70 align-top"><td className="px-3 py-3"><div className="text-text font-medium">{position.name}<span className="font-mono text-text-secondary ml-2">{position.code}</span></div><div className="text-[10px] text-text-secondary mt-1">{time(position.entry_at)} · {position.shares}股</div></td><td className="px-3 py-3"><span className={`border rounded px-1.5 py-0.5 ${position.status === 'open' ? 'border-accent/50 text-accent' : 'border-border text-text-secondary'}`}>{position.status === 'open' ? '持仓待退出' : '已按纪律退出'}</span></td><td className="px-3 py-3 text-right font-mono text-text">¥{number(position.entry_price, 4)}</td><td className="px-3 py-3 text-right font-mono text-text">{position.current_price == null ? '--' : `¥${number(position.current_price, 4)}`}</td><td className="px-3 py-3 text-right font-mono text-text">{money(position.cost_value)}<div className="text-[10px] text-text-secondary mt-1">{number(position.allocated_pct, 2)}%</div></td><td className={`px-3 py-3 text-right font-mono ${pnlClass(position.pnl)}`}>{money(position.pnl)}<div>{signed(position.pnl_pct)}</div></td><td className="px-3 py-3 text-text-secondary max-w-[260px]">{position.exit_reason || '次日09:30-10:00执行退出规则'}</td><td className="px-3 py-3 text-right"><AddToPersonalPoolButton code={position.code} name={position.name} industry={position.sector} thesis={`一夜持股模拟：${position.status === 'open' ? '持仓待退出' : position.exit_reason || '已退出'}`} source="ai_robot_overnight" compact /></td></tr>;
}
