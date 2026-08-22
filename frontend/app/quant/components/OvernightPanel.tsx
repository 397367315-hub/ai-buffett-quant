'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, Clock3, Database, GitCompareArrows, Loader2, LogOut, MoonStar, Play, RefreshCw, Save, Settings2, ShieldAlert } from 'lucide-react';
import AddToPersonalPoolButton from '@/components/AddToPersonalPoolButton';
import StockKlineButton from '@/components/StockKlineButton';
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
  tail_qualified?: boolean;
  awaiting_auction?: boolean;
  auction_passed?: boolean | null;
  research_only?: boolean;
  research_qualified?: boolean;
  research_status?: string;
  source_strategy_id?: string;
  source_strategy_name?: string;
  source_entry_run_id?: number;
  failed_reasons: string[];
  unavailable_reasons: string[];
  conditions: Condition[];
  minute?: { latest_bar_at: string | null; market_price: number | null; entry_price: number | null } | null;
  auction?: {
    auction_price: number | null;
    auction_volume: number | null;
    auction_volume_ratio: number | null;
    high_open_pct: number | null;
    previous_close: number | null;
    quote_at: string | null;
    source: string;
    is_realtime: boolean;
    agent_decision?: { agent?: string; decision?: string; reason?: string; checked_at?: string };
  } | null;
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
  research_only?: boolean;
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
  strategy_store: {
    active_id: string;
    strategies: Array<Record<string, any>>;
    factor_schema: Array<Record<string, any>>;
    validation_note: string;
  };
  auction_strategy?: Record<string, any>;
  active_run: OvernightRun | null;
  latest_entry_run: OvernightRun | null;
  latest_auction_run: OvernightRun | null;
  latest_preliminary_run: OvernightRun | null;
  latest_research_run?: OvernightRun | null;
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
  loss_alert: {
    blocked: boolean;
    warning: boolean;
    level: string;
    consecutive_losses: number;
    reason: string;
  };
  quote: { available: boolean; source: string; data_date: string | null; is_realtime: boolean; cache_used: boolean };
  minute_coverage: { bar_count: number; stock_count: number; from: string | null; to: string | null; collection_mode: string };
  backtest: { available: boolean; grade: string; reason: string; requirements: string[] };
  disclaimer: string;
}

const stageLabel: Record<string, string> = {
  preliminary: '14:30预扫描', entry: '14:50尾盘复核', auction: '09:25 AI竞价盯盘', exit: '早盘退出检查', force_exit: '10:00强制退出',
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
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, any> | null>(null);
  const [newName, setNewName] = useState('');
  const [saving, setSaving] = useState(false);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [comparison, setComparison] = useState<Record<string, any> | null>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const response = await apiFetch<{ data: Dashboard }>('/quant/overnight');
      setData(response.data);
      setDraft((current) => current || { ...response.data.strategy });
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

  const run = async (stage: string, options: { strategyId?: string; researchOnly?: boolean } = {}) => {
    setSubmitting(stage); setError(null);
    try {
      await apiFetch('/quant/overnight/runs', {
        method: 'POST',
        body: JSON.stringify({
          stage,
          strategy_id: options.strategyId ?? data?.strategy.id,
          research_only: Boolean(options.researchOnly),
        }),
      });
      await load(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '策略任务提交失败');
    } finally {
      setSubmitting(null);
    }
  };

  const activate = async (strategy: Record<string, any>) => {
    setSaving(true); setError(null);
    try {
      await apiFetch(`/quant/overnight/strategies/${strategy.id}/activate`, { method: 'POST' });
      setDraft({ ...strategy }); setComparison(null);
      await load(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '策略切换失败');
    } finally { setSaving(false); }
  };

  const saveStrategy = async (asNew: boolean) => {
    if (!draft) return;
    const name = asNew ? newName.trim() : String(draft.name || '').trim();
    if (!name) { setError('请填写新策略名称'); return; }
    setSaving(true); setError(null);
    try {
      const body: Record<string, any> = { ...draft, name };
      delete body.id; delete body.is_builtin; delete body.updated_at;
      const response = await apiFetch<{ data: Record<string, any> }>(
        asNew ? '/quant/overnight/strategies' : `/quant/overnight/strategies/${draft.id}`,
        { method: asNew ? 'POST' : 'PUT', body: JSON.stringify(body) },
      );
      await apiFetch(`/quant/overnight/strategies/${response.data.id}/activate`, { method: 'POST' });
      setDraft({ ...response.data }); setNewName(''); setEditing(false); setComparison(null);
      await load(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '策略保存失败');
    } finally { setSaving(false); }
  };

  const compare = async () => {
    if (compareIds.length < 2) { setError('至少选择两个策略进行对比'); return; }
    setSaving(true); setError(null);
    try {
      const response = await apiFetch<{ data: Record<string, any> }>('/quant/overnight/compare', {
        method: 'POST', body: JSON.stringify({ strategy_ids: compareIds }),
      });
      setComparison(response.data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '策略对比失败');
    } finally { setSaving(false); }
  };

  const setFactor = (key: string, value: unknown) => setDraft((current) => current ? ({ ...current, [key]: value }) : current);
  const setRange = (key: string, index: number, value: number) => setDraft((current) => {
    if (!current) return current;
    const values = [...(current[key] || [0, 0])]; values[index] = value;
    return { ...current, [key]: values };
  });

  const latest = [data?.latest_research_run, data?.latest_auction_run, data?.latest_entry_run, data?.latest_preliminary_run]
    .filter((item): item is OvernightRun => Boolean(item))
    .sort((left, right) => right.id - left.id)[0];
  const candidates = useMemo(() => latest?.candidates || [], [latest]);
  const active = data?.active_run;

  if (loading && !data) return <div className="py-20 text-center"><Loader2 size={28} className="animate-spin text-accent mx-auto" /><div className="text-sm text-text mt-3">正在读取分钟缓存与策略审计</div><div className="h-1.5 max-w-sm mx-auto bg-[#21262D] mt-5 overflow-hidden rounded"><div className="h-full bg-accent transition-all" style={{ width: `${loadProgress}%` }} /></div><div className="text-xs font-mono text-text-secondary mt-2">{loadProgress}%</div></div>;

  return <div className="space-y-4">
    <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
      <div><h2 className="text-base font-bold text-text flex items-center gap-2"><MoonStar size={18} className="text-accent" />一夜持股</h2><p className="text-xs text-text-secondary mt-1">交易日14:30预扫 · 14:55尾盘复核 · 竞价确认版次日09:24-09:27 AI盯盘 · 10:00前退出</p></div>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => run('preliminary')} disabled={Boolean(active) || Boolean(submitting)} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs border border-border text-text-secondary rounded-md hover:border-accent hover:text-text disabled:opacity-50"><RefreshCw size={14} className={submitting === 'preliminary' ? 'animate-spin' : ''} />预扫描</button>
        <button type="button" onClick={() => run('entry')} disabled={Boolean(active) || Boolean(submitting)} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs bg-accent text-white rounded-md disabled:opacity-50"><Play size={14} />尾盘复核</button>
        <button type="button" onClick={() => run('auction', { strategyId: data?.auction_strategy?.id || 'overnight_auction_confirm_v1' })} disabled={Boolean(submitting)} title="独立于当前一夜策略的竞价 Agent" className="inline-flex items-center gap-1.5 px-3 py-2 text-xs border border-accent/60 text-accent rounded-md disabled:opacity-40"><Clock3 size={14} className={submitting === 'auction' ? 'animate-pulse' : ''} />AI竞价盯盘</button>
        <button type="button" onClick={() => run('exit')} disabled={Boolean(active) || Boolean(submitting)} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs border border-up/50 text-up rounded-md disabled:opacity-50"><LogOut size={14} />退出检查</button>
        <button type="button" onClick={() => run('preliminary', { researchOnly: true })} disabled={Boolean(submitting)} title="使用最近完整缓存进行研究，不建立模拟仓位" className="inline-flex items-center gap-1.5 px-3 py-2 text-xs border border-warn/60 text-warn rounded-md disabled:opacity-40"><Database size={14} />缓存研究</button>
      </div>
    </header>

    {data && draft && <section className="border border-border rounded-md overflow-hidden">
      <div className="px-3 py-2.5 flex flex-wrap items-center gap-2 border-b border-border">
        <Settings2 size={15} className="text-accent" />
        <select value={data.strategy.id} onChange={(event) => { const strategy = data.strategy_store.strategies.find((item) => item.id === event.target.value); if (strategy) activate(strategy); }} disabled={saving} className="bg-[#0D1117] border border-border rounded-md px-2.5 py-1.5 text-xs text-text min-w-[210px]">
          {data.strategy_store.strategies.map((strategy) => <option key={strategy.id} value={strategy.id}>{strategy.name}{strategy.is_builtin ? '（内置）' : ''}</option>)}
        </select>
        <button type="button" onClick={() => { setDraft({ ...data.strategy }); setEditing((value) => !value); }} className="inline-flex items-center gap-1.5 px-2.5 py-1.5 border border-border rounded-md text-xs text-text-secondary hover:text-text"><Settings2 size={13} />因子微调</button>
        <div className="sm:ml-auto flex flex-wrap items-center gap-1.5">
          {data.strategy_store.strategies.map((strategy) => <label key={strategy.id} className="inline-flex items-center gap-1 text-[11px] text-text-secondary"><input type="checkbox" checked={compareIds.includes(strategy.id)} onChange={(event) => setCompareIds((current) => event.target.checked ? [...current, strategy.id] : current.filter((item) => item !== strategy.id))} />{strategy.name}</label>)}
          <button type="button" onClick={compare} disabled={saving || compareIds.length < 2} className="inline-flex items-center gap-1 px-2.5 py-1.5 border border-border rounded-md text-xs text-text-secondary disabled:opacity-40"><GitCompareArrows size={13} />对比</button>
        </div>
      </div>
      {editing && <div className="p-3">
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 text-xs">
          <FactorRange label="当日涨幅" values={draft.change_pct} unit="%" onChange={(index, value) => setRange('change_pct', index, value)} />
          <FactorNumber label="量比主阈值（严格大于）" value={draft.volume_ratio_min} step={0.1} onChange={(value) => setFactor('volume_ratio_min', value)} />
          <FactorRange label="换手率" values={draft.turnover_pct} unit="%" onChange={(index, value) => setRange('turnover_pct', index, value)} />
          <FactorRange label="总市值" values={draft.market_cap_yi} unit="亿元" onChange={(index, value) => setRange('market_cap_yi', index, value)} />
          <FactorNumber label="最低相对强度" value={draft.relative_strength_min_pct} step={0.1} unit="%" onChange={(value) => setFactor('relative_strength_min_pct', value)} />
          <FactorNumber label="尾盘5分钟最大涨幅" value={draft.last_five_minute_change_max} step={0.1} unit="%" onChange={(value) => setFactor('last_five_minute_change_max', value)} />
          <FactorNumber label="最低股价" value={draft.minimum_price} step={0.1} unit="元" onChange={(value) => setFactor('minimum_price', value)} />
          <FactorNumber label="最低上市交易日" value={draft.minimum_listing_sessions} step={1} unit="日" onChange={(value) => setFactor('minimum_listing_sessions', Math.round(value))} />
          <FactorNumber label="竞价量比主阈值（严格大于）" value={draft.auction_volume_ratio_min} step={0.1} onChange={(value) => setFactor('auction_volume_ratio_min', value)} />
          <FactorRange label="竞价高开幅度" values={draft.auction_high_open_pct} unit="%" onChange={(index, value) => setRange('auction_high_open_pct', index, value)} />
          <FactorNumber label="止盈幅度" value={draft.take_profit_pct} step={0.5} unit="%" onChange={(value) => setFactor('take_profit_pct', value)} />
          <FactorNumber label="止损幅度" value={draft.stop_loss_pct} step={0.5} unit="%" onChange={(value) => setFactor('stop_loss_pct', value)} />
          <FactorNumber label="最多持股数" value={draft.max_positions} step={1} unit="只" onChange={(value) => setFactor('max_positions', Math.round(value))} />
          <div className="border border-border rounded-md p-2.5 space-y-2">
            <FactorToggle label="排除科创板 688/689" checked={Boolean(draft.exclude_star_market)} onChange={(value) => setFactor('exclude_star_market', value)} />
            <FactorToggle label="排除创业板 300/301/302" checked={Boolean(draft.exclude_chinext)} onChange={(value) => setFactor('exclude_chinext', value)} />
            <FactorToggle label="上证站上MA20" checked={Boolean(draft.require_market_ma20)} onChange={(value) => setFactor('require_market_ma20', value)} />
            <FactorToggle label="股价高于MA10" checked={Boolean(draft.require_price_above_ma10)} onChange={(value) => setFactor('require_price_above_ma10', value)} />
            <FactorToggle label="次日竞价双条件确认" checked={Boolean(draft.requires_auction_confirmation)} onChange={(value) => setFactor('requires_auction_confirmation', value)} />
            <FactorToggle label="AI竞价盯盘Agent" checked={Boolean(draft.ai_auction_monitor)} onChange={(value) => setFactor('ai_auction_monitor', value)} />
            <FactorToggle label="近3日台阶放量" checked={Boolean(draft.require_volume_staircase)} onChange={(value) => setFactor('require_volume_staircase', value)} />
            <FactorToggle label="分时强于上证" checked={Boolean(draft.require_relative_strength)} onChange={(value) => setFactor('require_relative_strength', value)} />
            <FactorToggle label="尾盘守住VWAP" checked={Boolean(draft.require_vwap_hold)} onChange={(value) => setFactor('require_vwap_hold', value)} />
            <FactorToggle label="14:55新高回踩" checked={Boolean(draft.require_late_high_retest)} onChange={(value) => setFactor('require_late_high_retest', value)} />
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2"><input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="新策略名称" className="bg-[#0D1117] border border-border rounded-md px-3 py-2 text-xs text-text min-w-[220px]" /><button type="button" onClick={() => saveStrategy(true)} disabled={saving || !newName.trim()} className="inline-flex items-center gap-1.5 px-3 py-2 bg-accent text-white rounded-md text-xs disabled:opacity-40"><Save size={13} />另存新策略</button>{!draft.is_builtin && <button type="button" onClick={() => saveStrategy(false)} disabled={saving} className="inline-flex items-center gap-1.5 px-3 py-2 border border-border text-text-secondary rounded-md text-xs"><Save size={13} />保存修改</button>}<span className="text-[11px] text-warn">{data.strategy_store.validation_note}</span></div>
      </div>}
      {comparison && <div className="border-t border-border overflow-x-auto"><table className="w-full min-w-[760px] text-xs"><thead className="text-text-secondary bg-[#161B22]"><tr><th className="text-left px-3 py-2">策略</th><th className="text-right px-3">运行</th><th className="text-right px-3">空仓日</th><th className="text-right px-3">已完成</th><th className="text-right px-3">胜率</th><th className="text-right px-3">收益率</th><th className="text-right px-3">累计盈亏</th></tr></thead><tbody>{(comparison.comparisons || []).map((item: Record<string, any>) => <tr key={item.strategy_id} className="border-t border-border"><td className="px-3 py-2 text-text">{item.name}</td><td className="px-3 py-2 text-right font-mono">{item.run_count}</td><td className="px-3 py-2 text-right font-mono">{item.cash_days}</td><td className="px-3 py-2 text-right font-mono">{item.closed_positions}</td><td className="px-3 py-2 text-right font-mono">{item.win_rate == null ? '--' : `${item.win_rate.toFixed(1)}%`}</td><td className={`px-3 py-2 text-right font-mono ${pnlClass(item.return_pct)}`}>{signed(item.return_pct)}</td><td className={`px-3 py-2 text-right font-mono ${pnlClass(item.total_pnl)}`}>{money(item.total_pnl)}</td></tr>)}</tbody></table><p className="px-3 py-2 text-[11px] text-warn">{comparison.limitation}</p></div>}
    </section>}

    {error && <div className="border border-down/50 bg-[#EF535018] rounded-md p-3 text-xs text-down flex gap-2"><AlertTriangle size={15} className="shrink-0" />{error}</div>}
    {data?.loss_alert?.warning && <section className="border border-warn/50 bg-[#D2992218] rounded-md p-3 text-xs text-warn flex gap-2"><AlertTriangle size={15} className="shrink-0" /><div><div className="font-semibold">连续亏损提醒</div><div className="mt-1 text-text-secondary">{data.loss_alert.reason} 当前行情扫描、候选观察和人工操作保持开放。</div></div></section>}
    {active && <section className="border border-accent/50 rounded-md p-3"><div className="flex justify-between gap-3 text-xs"><span className="text-text flex items-center gap-2"><Loader2 size={14} className="animate-spin text-accent" />{active.message}</span><span className="font-mono text-accent">{active.progress}%</span></div><div className="h-1.5 bg-[#21262D] mt-2 overflow-hidden rounded"><div className="h-full bg-accent transition-all" style={{ width: `${Math.max(3, active.progress)}%` }} /></div></section>}

    {data && <section className="border border-accent/40 rounded-md p-3"><div className="flex flex-wrap items-center gap-2"><CheckCircle2 size={15} className="text-accent" /><h3 className="text-sm font-semibold text-text">AI竞价盯盘 Agent</h3><span className="text-[11px] text-text-secondary">独立于当前一夜策略 · 次日09:24-09:27 · 量比严格&gt;{data.auction_strategy?.auction_volume_ratio_min ?? 3} · 高开{data.auction_strategy?.auction_high_open_pct?.[0] ?? 2}%-{data.auction_strategy?.auction_high_open_pct?.[1] ?? 5}%双条件</span></div>{data.latest_auction_run ? <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-text-secondary"><span>{data.latest_auction_run.message}</span><span>覆盖 {(data.latest_auction_run.data_quality?.auction?.covered ?? 0)}只</span><span>通过 {(data.latest_auction_run.data_quality?.auction?.passed ?? 0)}只</span><span className={data.latest_auction_run.is_realtime ? 'text-up' : 'text-warn'}>{data.latest_auction_run.is_realtime ? '实时竞价' : '当前无可执行实时竞价'}</span></div> : <div className="mt-2 text-xs text-text-secondary">尾盘候选生成后，Agent会在下一交易日独立检查；竞价数据缺失时不建仓。</div>}</section>}

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
        <RuleBand title="行情活跃度" text={`涨幅${data?.strategy.change_pct?.[0] ?? 3}%-${data?.strategy.change_pct?.[1] ?? 5}% · 量比>${data?.strategy.volume_ratio_min ?? 1.2} · 换手${data?.strategy.turnover_pct?.[0] ?? 5}%-${data?.strategy.turnover_pct?.[1] ?? 10}%`} />
        <RuleBand title="规模与趋势" text={`市值${data?.strategy.market_cap_yi?.[0] ?? 50}-${data?.strategy.market_cap_yi?.[1] ?? 200}亿 · 台阶放量 · MA多头`} />
        <RuleBand title="硬性排雷" text={`ST/次新/近5日跌停/重大利空/财报前3日${data?.strategy.exclude_chinext ? ' · 排除创业板' : ''}${data?.strategy.exclude_star_market ? ' · 排除科创板' : ''}`} />
        <RuleBand title={data?.strategy.requires_auction_confirmation ? '尾盘+竞价确认' : '分钟确认'} text={data?.strategy.requires_auction_confirmation ? `尾盘合格只记录候选 · 次日量比>${data?.strategy.auction_volume_ratio_min ?? 3}且高开${data?.strategy.auction_high_open_pct?.[0] ?? 2}%-${data?.strategy.auction_high_open_pct?.[1] ?? 5}%才买入` : '强于上证 · 守住VWAP · 14:55新高回踩 · 排除急拉'} />
      </div>
    </section>

    {latest && <section className="border border-border rounded-md overflow-hidden">
      <div className="px-3 py-2.5 border-b border-border flex flex-wrap items-center gap-x-4 gap-y-1"><h3 className="text-sm font-semibold text-text">最近扫描</h3><span className="text-xs text-text-secondary">{stageLabel[latest.stage] || latest.stage} · {latest.message}</span><span className="sm:ml-auto text-xs font-mono text-text-secondary">{latest.data_date || '--'}</span></div>
      <div className="px-3 py-2 text-xs text-text-secondary border-b border-border flex flex-wrap gap-x-4 gap-y-1"><span>扫描 {latest.scanned_count}只</span><span>静态通过 {latest.prefiltered_count}只</span><span>最终合格 {latest.qualified_count}只</span>{latest.data_quality?.research_only && <span className="text-warn">缓存研究 · 观察候选 {latest.data_quality?.research_candidate_count ?? 0}只</span>}<span className={latest.is_realtime ? 'text-up' : 'text-warn'}>{latest.is_realtime ? '当日实时，可按规则执行' : '非实时，仅供观察'}</span></div>
      <ScanDiagnostics run={latest} />
      {candidates.length ? <div className="overflow-x-auto"><table className="w-full min-w-[1120px] text-xs"><thead className="text-text-secondary bg-[#161B22]"><tr><th className="text-left px-3 py-2">股票</th><th className="text-right px-3">评分</th><th className="text-right px-3">涨幅</th><th className="text-right px-3">量比</th><th className="text-right px-3">换手</th><th className="text-right px-3">市值</th><th className="text-left px-3">状态</th><th className="text-left px-3">分钟/竞价证据</th><th className="text-right px-3">个人池</th></tr></thead><tbody>{candidates.map((candidate) => <CandidateRow key={candidate.code} candidate={candidate} />)}</tbody></table></div> : <Empty text={latest.data_quality?.research_only ? '缓存中没有满足当前静态条件的观察候选，请查看上方淘汰原因' : '本轮没有股票通过预筛，具体原因见上方扫描诊断'} />}
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

function FactorRange({ label, values, unit, onChange }: { label: string; values: number[]; unit: string; onChange: (index: number, value: number) => void }) {
  return <label className="border border-border rounded-md p-2.5 text-text-secondary"><span>{label}</span><div className="mt-2 grid grid-cols-[1fr_auto_1fr_auto] items-center gap-1.5"><input type="number" value={values?.[0] ?? 0} onChange={(event) => onChange(0, Number(event.target.value))} className="min-w-0 bg-[#0D1117] border border-border rounded px-2 py-1.5 font-mono text-text" /><span>至</span><input type="number" value={values?.[1] ?? 0} onChange={(event) => onChange(1, Number(event.target.value))} className="min-w-0 bg-[#0D1117] border border-border rounded px-2 py-1.5 font-mono text-text" /><span>{unit}</span></div></label>;
}

function FactorNumber({ label, value, step, unit = '', onChange }: { label: string; value: number; step: number; unit?: string; onChange: (value: number) => void }) {
  return <label className="border border-border rounded-md p-2.5 text-text-secondary"><span>{label}</span><div className="mt-2 flex items-center gap-1.5"><input type="number" step={step} value={value ?? 0} onChange={(event) => onChange(Number(event.target.value))} className="min-w-0 flex-1 bg-[#0D1117] border border-border rounded px-2 py-1.5 font-mono text-text" /><span>{unit}</span></div></label>;
}

function FactorToggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="flex items-center justify-between gap-3 text-text-secondary"><span>{label}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="accent-[#2F81F7]" /></label>;
}

function Empty({ text }: { text: string }) {
  return <div className="py-12 text-center text-xs text-text-secondary">{text}</div>;
}

function diagnosticEntries(value: unknown) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>)
    .filter(([, count]) => Number(count) > 0)
    .sort(([, left], [, right]) => Number(right) - Number(left));
}

function DiagnosticList({ label, value }: { label: string; value: unknown }) {
  const entries = diagnosticEntries(value);
  return <div className="min-w-0"><div className="text-[11px] font-medium text-text">{label}</div>{entries.length ? <div className="mt-1.5 space-y-1">{entries.slice(0, 6).map(([reason, count]) => <div key={reason} className="flex items-start justify-between gap-3 text-[11px] leading-4"><span className="min-w-0 text-text-secondary">{reason}</span><span className="shrink-0 font-mono text-warn">{String(count)}只</span></div>)}{entries.length > 6 && <div className="text-[10px] text-text-secondary">另有 {entries.length - 6} 项，详见后端审计</div>}</div> : <div className="mt-1.5 text-[11px] text-text-secondary">暂无记录</div>}</div>;
}

function ScanDiagnostics({ run }: { run: OvernightRun }) {
  const reasons = run.data_quality?.rejection_reasons || {};
  const researchOnly = Boolean(run.data_quality?.research_only || run.research_only);
  return <div className="border-b border-border bg-[#0D1117] px-3 py-3"><div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]"><span className="font-medium text-text">扫描诊断</span><span className={researchOnly ? 'text-warn' : run.is_realtime ? 'text-up' : 'text-warn'}>{researchOnly ? '缓存研究模式：不建立模拟仓位' : run.is_realtime ? '实时执行模式' : '数据不可执行'}</span>{run.data_quality?.execution_allowed === false && <span className="text-text-secondary">执行权限：关闭</span>}<span className="text-text-secondary">运行 {run.id} · {time(run.finished_at || run.created_at)}</span></div><div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3"><DiagnosticList label="静态预筛淘汰" value={reasons.prefilter} /><DiagnosticList label="日线规则未通过" value={reasons.daily_failed} /><DiagnosticList label="证据待补/未覆盖" value={reasons.evidence_pending} /></div>{researchOnly && <p className="mt-3 border-l-2 border-warn pl-2 text-[11px] leading-5 text-warn">缓存研究只回答“哪些股票值得继续观察”，不能替代当日14:52-14:59分钟行情、公告和风控校验；真实尾盘扫描仍会重新核验全部强制条件。</p>}</div>;
}

function CandidateRow({ candidate }: { candidate: Candidate }) {
  const auctionChecked = candidate.auction_passed !== undefined && candidate.auction_passed !== null;
  const state = candidate.research_qualified
    ? (candidate.daily_passed ? '缓存研究候选，待实时复核' : '近似观察候选，存在硬约束')
    : candidate.selected_for_entry
    ? '竞价确认后已模拟买入'
    : candidate.awaiting_auction
      ? '等待次日竞价'
      : candidate.auction_passed === true
        ? '竞价确认通过'
        : candidate.auction_passed === false
          ? (candidate.unavailable_reasons.length ? '竞价数据不足' : '竞价未通过，放弃')
          : candidate.qualified ? '合格未入场' : candidate.daily_passed && candidate.minute_passed == null ? '待分钟复核' : candidate.unavailable_reasons.length ? '数据不足' : '未通过';
  const stateClass = candidate.research_qualified ? 'text-warn border-warn/50' : candidate.selected_for_entry || candidate.auction_passed === true ? 'text-up border-up/50' : candidate.awaiting_auction ? 'text-accent border-accent/50' : candidate.unavailable_reasons.length ? 'text-warn border-warn/50' : 'text-down border-down/50';
  const auction = candidate.auction;
  return <tr className="border-t border-border/70 align-top"><td className="px-3 py-3"><StockKlineButton code={candidate.code} name={candidate.name} className="text-text font-medium">{candidate.name}<span className="font-mono text-text-secondary ml-2">{candidate.code}</span></StockKlineButton><div className="text-[10px] text-text-secondary mt-1">{candidate.sector || '板块未返回'}</div></td><td className="px-3 py-3 text-right font-mono text-text">{number(candidate.score, 1)}</td><td className={`px-3 py-3 text-right font-mono ${pnlClass(candidate.change_pct)}`}>{signed(candidate.change_pct)}</td><td className="px-3 py-3 text-right font-mono text-text">{number(candidate.volume_ratio)}</td><td className="px-3 py-3 text-right font-mono text-text">{number(candidate.turnover)}%</td><td className="px-3 py-3 text-right font-mono text-text">{number(candidate.market_cap_yi, 1)}亿</td><td className="px-3 py-3"><span className={`inline-block border rounded px-1.5 py-0.5 ${stateClass}`}>{state}</span>{candidate.research_status && <div className="mt-1 text-[10px] leading-4 text-warn">{candidate.research_status}</div>}<details className="mt-2"><summary className="text-accent cursor-pointer">规则审计</summary><div className="mt-2 w-[320px] space-y-1.5">{candidate.conditions.map((item) => <div key={item.key} className="grid grid-cols-[14px_1fr] gap-1.5"><span className={item.status === 'passed' ? 'text-up' : item.status === 'failed' ? 'text-down' : 'text-warn'}>{item.status === 'passed' ? '✓' : item.status === 'failed' ? '×' : '?'}</span><div><div className="text-text">{item.label}：{actualText(item.actual)}</div><div className="text-[10px] text-text-secondary">要求 {item.expected} · {item.source}</div>{item.detail && <div className="text-[10px] text-text-secondary">{item.detail}</div>}</div></div>)}</div></details></td><td className="px-3 py-3 text-text-secondary"><div>尾盘分钟：{candidate.minute?.latest_bar_at ? time(candidate.minute.latest_bar_at) : '--'}</div><div className="font-mono mt-1">尾盘成交参考 {candidate.minute?.entry_price ? `¥${number(candidate.minute.entry_price, 4)}` : '--'}</div>{candidate.source_strategy_name && <div className="mt-1 text-[10px] text-text-secondary">候选来源：{candidate.source_strategy_name}</div>}{auctionChecked && <div className="mt-2 border-t border-border/70 pt-2"><div className={candidate.auction_passed ? 'text-up' : 'text-down'}>竞价：{candidate.auction_passed ? '通过' : '放弃'}</div><div className="font-mono mt-1">量比&gt;{number(auction?.auction_volume_ratio)} · 高开{signed(auction?.high_open_pct)}</div><div className="mt-1">报价 {auction?.quote_at ? time(auction.quote_at) : '--'} · {auction?.source || '无来源'}</div><div className="mt-1 text-[10px] text-text-secondary">{auction?.agent_decision?.reason || 'AI竞价盯盘Agent未形成可执行结论'}</div></div>}</td><td className="px-3 py-3 text-right"><AddToPersonalPoolButton code={candidate.code} name={candidate.name} industry={candidate.sector} thesis={`一夜持股：评分${candidate.score}，${state}`} source="quant_overnight" compact /></td></tr>;
}

function PositionRow({ position }: { position: Position }) {
  return <tr className="border-t border-border/70 align-top"><td className="px-3 py-3"><StockKlineButton code={position.code} name={position.name} className="text-text font-medium">{position.name}<span className="font-mono text-text-secondary ml-2">{position.code}</span></StockKlineButton><div className="text-[10px] text-text-secondary mt-1">{time(position.entry_at)} · {position.shares}股</div></td><td className="px-3 py-3"><span className={`border rounded px-1.5 py-0.5 ${position.status === 'open' ? 'border-accent/50 text-accent' : 'border-border text-text-secondary'}`}>{position.status === 'open' ? '持仓待退出' : '已按纪律退出'}</span></td><td className="px-3 py-3 text-right font-mono text-text">¥{number(position.entry_price, 4)}</td><td className="px-3 py-3 text-right font-mono text-text">{position.current_price == null ? '--' : `¥${number(position.current_price, 4)}`}</td><td className="px-3 py-3 text-right font-mono text-text">{money(position.cost_value)}<div className="text-[10px] text-text-secondary mt-1">{number(position.allocated_pct, 2)}%</div></td><td className={`px-3 py-3 text-right font-mono ${pnlClass(position.pnl)}`}>{money(position.pnl)}<div>{signed(position.pnl_pct)}</div></td><td className="px-3 py-3 text-text-secondary max-w-[260px]">{position.exit_reason || '次日09:30-10:00执行退出规则'}</td><td className="px-3 py-3 text-right"><AddToPersonalPoolButton code={position.code} name={position.name} industry={position.sector} thesis={`一夜持股模拟：${position.status === 'open' ? '持仓待退出' : position.exit_reason || '已退出'}`} source="ai_robot_overnight" compact /></td></tr>;
}
