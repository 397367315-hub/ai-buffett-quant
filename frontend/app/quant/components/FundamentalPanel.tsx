'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, BarChart3, CheckCircle2, Clock3, Database, Layers3, Loader2, RefreshCw, ShieldAlert, SlidersHorizontal, Wifi } from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';
import AddToPersonalPoolButton from '@/components/AddToPersonalPoolButton';
import StockKlineButton from '@/components/StockKlineButton';
import type { BackgroundJob, FQEDataSyncStatus, FQEHolding, FQEPortfolio, FQEResult } from '../types';

const CONTRACT_LABELS: Record<string, string> = {
  pit_financial: '财务 PIT',
  ttm: 'TTM 指标',
  listing_history: '上市历史',
  pe_history_percentile: 'PE 历史分位',
  survivorship_bias: '生存者偏差',
};

const PHASE_LABELS: Record<string, string> = {
  queued: '任务排队',
  market_snapshot: '行情快照',
  pit_ttm: 'PIT / TTM 财务合并',
  reference_data: '上市历史 / PE 分位合并',
  retail_engine: '零售轻量引擎',
  institutional_engine: '机构重构与组合优化',
  completed: '已完成',
  failed: '任务失败',
};

function time(value?: string | null) {
  if (!value) return '--';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false });
}

function number(value: number | null | undefined, digits = 2) {
  if (value == null || !Number.isFinite(Number(value))) return '--';
  return Number(value).toFixed(digits);
}

function percentage(value: number | null | undefined, digits = 2) {
  const formatted = number(value, digits);
  return formatted === '--' ? formatted : `${formatted}%`;
}

function contractStatus(status: string) {
  if (status === 'available' || status === 'current_as_of') return { label: '可审计', className: 'text-down border-down/50' };
  if (status === 'partial') return { label: '部分覆盖', className: 'text-warn border-warn/50' };
  if (status === 'unresolved' || status === 'missing') return { label: '有缺口', className: 'text-warn border-warn/50' };
  return { label: status || '未知', className: 'text-text-secondary border-border' };
}

const SYNC_PHASE_LABELS: Record<string, string> = {
  queued: '补数排队',
  security_master: '证券主表与上市状态',
  valuation_history: '三年 PE 历史',
  market_evidence: '市场宽度与情绪历史',
  completed: '补数完成',
  failed: '补数失败',
};

function DataSyncProgress({ status }: { status: FQEDataSyncStatus }) {
  const run = status.run;
  if (!run) return null;
  const active = run.status === 'queued' || run.status === 'running';
  const failed = run.status === 'failed';
  const partial = run.status === 'partial';
  return (
    <div className={`rounded-md border p-3 ${failed ? 'border-down/50 bg-[#EF535022]' : partial ? 'border-warn/50 bg-[#D299221A]' : 'border-accent/50 bg-[#1F6FEB1A]'}`}>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="flex min-w-0 items-center gap-2 text-text">
          {active ? <Loader2 size={14} className="shrink-0 animate-spin text-accent" /> : failed || partial ? <AlertTriangle size={14} className={`shrink-0 ${failed ? 'text-down' : 'text-warn'}`} /> : <CheckCircle2 size={14} className="shrink-0 text-down" />}
          <span>{SYNC_PHASE_LABELS[run.stage] || run.stage}：{run.message || '--'}</span>
        </span>
        <span className="shrink-0 font-mono text-accent">{Math.round(run.progress)}%</span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#0D1117]"><div className="h-full bg-accent transition-all duration-300" style={{ width: `${Math.max(2, Math.min(100, run.progress))}%` }} /></div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-secondary">
        <span>证券主表 {run.master_count || status.coverage.security_total}</span>
        <span>上市日期 {status.coverage.listing_dated}/{status.coverage.currently_listed}</span>
        <span>PE 历史 {status.coverage.current_valuation_series ?? status.coverage.valuation_series}/{status.coverage.currently_listed}</span>
        <span>正 PE 分位 {status.coverage.current_valuation_percentiles ?? status.coverage.valuation_percentiles}</span>
        <span>历史非活跃 {status.coverage.inactive_dated}/{status.coverage.inactive_total}</span>
        {run.total_securities > 0 && <span>处理 {run.completed_securities}/{run.total_securities}</span>}
        {run.failed_count > 0 && <span className="text-warn">上游失败 {run.failed_count}</span>}
      </div>
      {run.error && !active && <div className="mt-2 text-[11px] text-warn">{run.error}</div>}
    </div>
  );
}

function Progress({ job }: { job: BackgroundJob }) {
  const active = job.status === 'queued' || job.status === 'running';
  return (
    <div className="border border-accent/50 bg-[#1F6FEB22] rounded-md p-3">
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="flex min-w-0 items-center gap-2 text-text">
          {active ? <Loader2 size={14} className="shrink-0 animate-spin text-accent" /> : <CheckCircle2 size={14} className="shrink-0 text-down" />}
          <span className="truncate">{PHASE_LABELS[job.phase] || job.phase}：{job.message}</span>
        </span>
        <span className="shrink-0 font-mono text-accent">{Math.round(job.progress)}%</span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#0D1117]">
        <div className="h-full bg-accent transition-all duration-300" style={{ width: `${Math.max(2, Math.min(100, job.progress))}%` }} />
      </div>
    </div>
  );
}

function PortfolioSummary({ portfolio }: { portfolio: FQEPortfolio }) {
  const warning = portfolio.warnings?.length > 0;
  return (
    <div className="grid grid-cols-2 divide-x divide-border border border-border rounded-md sm:grid-cols-4">
      <div className="p-3"><div className="text-xs text-text-secondary">入选数量</div><div className="mt-1 font-mono text-lg text-accent">{portfolio.count}</div></div>
      <div className="p-3"><div className="text-xs text-text-secondary">可选样本</div><div className="mt-1 font-mono text-lg text-text">{portfolio.eligible_count}</div></div>
      <div className="p-3"><div className="text-xs text-text-secondary">组合状态</div><div className={`mt-1 text-sm ${portfolio.data_quality.status === 'ready' ? 'text-down' : 'text-warn'}`}>{portfolio.data_quality.status === 'ready' ? '约束满足' : portfolio.data_quality.status === 'research_only' ? '研究参考' : '数据不足'}</div></div>
      <div className="p-3"><div className="text-xs text-text-secondary">数据提示</div><div className={`mt-1 text-sm ${warning ? 'text-warn' : 'text-down'}`}>{warning ? `${portfolio.warnings.length} 项` : '无'}</div></div>
    </div>
  );
}

function HoldingTable({ portfolio }: { portfolio: FQEPortfolio }) {
  const institutional = portfolio.engine_type === 'Institutional_Heavy';
  return (
    <div className="overflow-x-auto border border-border rounded-md">
      <table className="w-full min-w-[900px] text-xs">
        <thead className="bg-[#161B22] text-text-secondary">
          <tr>
            <th className="px-3 py-2 text-left">股票</th>
            <th className="px-3 py-2 text-left">行业</th>
            {institutional ? <>
              <th className="px-3 py-2 text-right">中性 Alpha</th>
              <th className="px-3 py-2 text-right">ROE TTM</th>
              <th className="px-3 py-2 text-right">PE TTM</th>
            </> : <>
              <th className="px-3 py-2 text-right">综合分</th>
              <th className="px-3 py-2 text-right">PEG</th>
              <th className="px-3 py-2 text-right">ROE TTM</th>
            </>}
            <th className="px-3 py-2 text-right">权重</th>
            <th className="px-3 py-2 text-left">财务披露</th>
            <th className="px-3 py-2 text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          {portfolio.holdings.map((holding: FQEHolding) => (
            <tr key={holding.code} className="border-t border-border/70 align-top hover:bg-[#161B22]">
              <td className="px-3 py-2.5"><StockKlineButton code={holding.code} name={holding.name} className="font-medium text-text">{holding.name || '--'}</StockKlineButton><div className="mt-0.5 font-mono text-text-secondary">{holding.code}</div></td>
              <td className="px-3 py-2.5 text-text-secondary">{holding.industry || '未知行业'}</td>
              {institutional ? <>
                <td className="px-3 py-2.5 text-right font-mono text-accent">{number(holding.alpha_neutral, 4)}</td>
                <td className="px-3 py-2.5 text-right font-mono text-text">{percentage(holding.roe_ttm)}</td>
                <td className="px-3 py-2.5 text-right font-mono text-text">{number(holding.pe_ttm)}</td>
              </> : <>
                <td className="px-3 py-2.5 text-right font-mono text-accent">{number(holding.score, 4)}</td>
                <td className="px-3 py-2.5 text-right font-mono text-text">{number(holding.peg, 3)}</td>
                <td className="px-3 py-2.5 text-right font-mono text-text">{percentage(holding.roe_ttm)}</td>
              </>}
              <td className="px-3 py-2.5 text-right font-mono text-text">{percentage(holding.weight_pct)}</td>
              <td className="px-3 py-2.5 text-text-secondary">{holding.financial_disclosed_at || '--'}{holding.data_warnings?.length ? <div className="mt-1 text-warn">{holding.data_warnings[0]}</div> : null}</td>
              <td className="px-3 py-2.5 text-right"><AddToPersonalPoolButton code={holding.code} name={holding.name} industry={holding.industry} thesis={`${portfolio.label}：${portfolio.method}；${institutional ? `中性Alpha ${number(holding.alpha_neutral, 4)}` : `PEG ${number(holding.peg, 3)}`}；权重 ${percentage(holding.weight_pct)}`} source={institutional ? 'fqe_institutional' : 'fqe_retail'} compact /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {!portfolio.holdings.length && <div className="py-10 text-center text-sm text-text-secondary">暂无满足条件的组合标的，先检查数据覆盖或切换宽松模式。</div>}
    </div>
  );
}

function PortfolioBlock({ portfolio }: { portfolio: FQEPortfolio }) {
  const institutional = portfolio.engine_type === 'Institutional_Heavy';
  const audit = portfolio.optimizer?.constraint_audit;
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2 border-b border-border pb-2">
        <div><h3 className="flex items-center gap-2 text-sm font-semibold text-text"><BarChart3 size={16} className={institutional ? 'text-warn' : 'text-accent'} />{portfolio.label}</h3><p className="mt-1 text-xs text-text-secondary">{portfolio.method}</p></div>
        <span className={`border rounded px-2 py-1 text-[11px] ${portfolio.data_quality.auditable ? 'border-down/50 text-down' : 'border-warn/50 text-warn'}`}>{portfolio.data_quality.auditable ? 'PIT 条件可审计' : '研究参考，不等同历史回测'}</span>
      </div>
      <PortfolioSummary portfolio={portfolio} />
      {portfolio.warnings?.length > 0 && <div className="space-y-1 border border-warn/50 bg-[#D299221A] rounded-md p-3 text-xs text-warn"><div className="flex items-center gap-2 font-medium"><AlertTriangle size={14} />数据与模型提示</div>{portfolio.warnings.slice(0, 5).map((warning) => <div key={warning} className="pl-5">{warning}</div>)}</div>}
      <HoldingTable portfolio={portfolio} />
      {institutional && audit && <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-border pt-2 text-[11px] text-text-secondary"><span>权重合计 {percentage(audit.weight_sum, 4)}</span><span>单股区间 {percentage(audit.min_weight)} - {percentage(audit.max_weight)}</span><span>最大行业权重 {percentage(audit.max_industry_weight)}</span><span className={audit.violations.length ? 'text-warn' : 'text-down'}>{audit.violations.length ? `约束异常：${audit.violations.join('、')}` : '约束审计通过'}</span></div>}
    </section>
  );
}

export default function FundamentalPanel() {
  const [result, setResult] = useState<FQEResult | null>(null);
  const [job, setJob] = useState<BackgroundJob | null>(null);
  const [syncStatus, setSyncStatus] = useState<FQEDataSyncStatus | null>(null);
  const [topN, setTopN] = useState(10);
  const [candidatePool, setCandidatePool] = useState(60);
  const [mode, setMode] = useState<'strict' | 'pragmatic'>('pragmatic');
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [syncWorking, setSyncWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const loadGeneration = useRef(0);
  const errorRef = useRef<string | null>(null);
  const syncErrorRef = useRef<string | null>(null);
  errorRef.current = error;
  syncErrorRef.current = syncError;

  const loadInitial = useCallback(async () => {
    const generation = ++loadGeneration.current;
    setLoading(true);
    setError(null);
    setSyncError(null);
    try {
      const response = await apiFetch<{
        data: {
          result: FQEResult | null;
          job: BackgroundJob | null;
          sync: FQEDataSyncStatus;
        };
      }>('/quant/fqe/bootstrap');
      if (generation !== loadGeneration.current) return;
      setResult(response.data.result || null);
      setJob(response.data.job || null);
      setSyncStatus(response.data.sync || null);
      setNotice(null);
      setLoading(false);
      return;
    } catch (bootstrapError) {
      // Keep compatibility with a backend that is still rolling out the
      // refresh-safe bootstrap route.
      const results = await Promise.allSettled([
        apiFetch<{ data: FQEResult | null }>('/quant/fqe/latest'),
        apiFetch<{ data: FQEDataSyncStatus }>('/quant/fqe/data/status'),
      ]);
      if (generation !== loadGeneration.current) return;
      const [latest, sync] = results;
      setJob(null);
      if (latest.status === 'fulfilled') {
        setResult(latest.value.data || null);
      } else {
        setError(`最近结果读取失败：${friendlyApiError(latest.reason, 'FQE结果暂时无法读取')}`);
      }
      if (sync.status === 'fulfilled') {
        setSyncStatus(sync.value.data);
      } else {
        setSyncError(`审计状态读取失败：${friendlyApiError(sync.reason, '审计数据状态暂时无法读取')}`);
      }
      if (latest.status === 'rejected' && sync.status === 'rejected') {
        setError(`FQE工作台读取失败：${friendlyApiError(bootstrapError, '后端暂时无法连接')}`);
      }
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadInitial();
    return () => { loadGeneration.current += 1; };
  }, [loadInitial]);

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return;
    let mounted = true;
    let failures = 0;
    const poll = async () => {
      try {
        const response = await apiFetch<{ data: BackgroundJob }>(`/quant/fqe/status/${job.job_id}`);
        if (!mounted) return;
        failures = 0;
        const next = response.data;
        setJob(next);
        if (failures === 0 && errorRef.current?.startsWith('任务仍在后台运行')) setError(null);
        if (next.status === 'completed' && next.result) setResult(next.result as unknown as FQEResult);
        if (next.status === 'failed') setError(next.error || '双引擎任务失败，请检查数据源和缓存。');
        if (next.status === 'completed') setNotice('双引擎计算完成，结果已更新。');
      } catch (caught) {
        if (mounted) {
          failures += 1;
          setNotice(`双引擎状态读取暂时中断，系统将继续重试（第${failures}次）。`);
          if (failures >= 3) setError(`任务仍在后台运行：${friendlyApiError(caught, '状态读取失败')}`);
        }
      }
    };
    poll();
    const timer = window.setInterval(poll, 1200);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [job?.job_id, job?.status]);

  useEffect(() => {
    const run = syncStatus?.run;
    if (!run || !['queued', 'running'].includes(run.status)) return;
    let mounted = true;
    let failures = 0;
    const poll = async () => {
      try {
        const response = await apiFetch<{ data: FQEDataSyncStatus }>('/quant/fqe/data/status');
        if (mounted) {
          failures = 0;
          setSyncStatus(response.data);
          if (syncErrorRef.current?.startsWith('审计状态读取暂时中断')) setSyncError(null);
          if (['completed', 'partial', 'failed'].includes(response.data.run?.status || '')) {
            setNotice(response.data.run?.status === 'completed' ? '审计数据补齐完成，覆盖状态已更新。' : '审计数据任务已结束，请查看覆盖提示。');
          }
        }
      } catch (caught) {
        if (mounted) {
          failures += 1;
          setSyncError(`审计状态读取暂时中断，正在自动重试（第${failures}次）：${friendlyApiError(caught, '状态读取失败')}`);
        }
      }
    };
    const timer = window.setInterval(poll, 1500);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [syncStatus?.run?.run_id, syncStatus?.run?.status]);

  const run = async (force: boolean) => {
    setWorking(true); setError(null);
    setNotice(force ? '强制更新已提交，正在获取最新行情与审计数据。' : '双引擎任务已提交，正在后台计算。');
    try {
      const response = await apiFetch<{ data: BackgroundJob }>('/quant/fqe/compare', {
        method: 'POST',
        body: JSON.stringify({ top_n: topN, candidate_pool: candidatePool, mode, force }),
      });
      setJob(response.data);
    } catch (caught) {
      setNotice(null);
      setError(friendlyApiError(caught, '双引擎启动失败'));
    } finally {
      setWorking(false);
    }
  };

  const startDataSync = async () => {
    setSyncWorking(true);
    setSyncError(null);
    setNotice('审计数据补齐已提交，正在登记行情、PIT 与市场证据。');
    try {
      const response = await apiFetch<{ data: FQEDataSyncStatus['run'] }>('/quant/fqe/data/sync', {
        method: 'POST',
        body: JSON.stringify({ full: true, years: 3, force: false }),
      });
      setSyncStatus((current) => ({
        run: response.data,
        coverage: current?.coverage || {
          security_total: 0, currently_listed: 0, listing_dated: 0,
          inactive_total: 0, inactive_dated: 0, status_events: 0,
          valuation_series: 0, valuation_percentiles: 0,
          current_valuation_series: 0, current_valuation_percentiles: 0, valuation_date: null,
        },
      }));
    } catch (caught) {
      setNotice(null);
      setSyncError(friendlyApiError(caught, '审计数据补齐任务启动失败'));
    } finally {
      setSyncWorking(false);
    }
  };

  const active = Boolean(job && ['queued', 'running'].includes(job.status));
  const syncActive = Boolean(syncStatus?.run && ['queued', 'running'].includes(syncStatus.run.status));
  const sourceLabel = result?.is_realtime ? '盘中实时' : result?.cache_used ? '缓存快照' : result?.source || '暂无数据';
  const sourceClass = result?.is_realtime ? 'text-down' : result?.cache_used ? 'text-warn' : 'text-text-secondary';

  if (loading) return <div className="flex flex-col items-center py-16 text-text-secondary"><Loader2 size={26} className="mb-2 animate-spin text-accent" /><span className="text-sm">正在读取双引擎最近结果</span></div>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-3">
        <div>
          <h2 className="flex items-center gap-2 text-base font-bold text-text"><Layers3 size={17} className="text-accent" />FQE 基本面双引擎</h2>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-secondary"><span className="inline-flex items-center gap-1"><Clock3 size={12} />研究日 {result?.as_of_date || '--'}</span><span className={`inline-flex items-center gap-1 ${sourceClass}`}>{result?.is_realtime ? <Wifi size={12} /> : <Database size={12} />}{sourceLabel}</span><span>最近生成 {time(result?.generated_at)}</span></div>
        </div>
        <div className="flex items-center gap-3"><div className="text-xs text-text-secondary">零售侧重 GARP；机构侧重中性化与风险约束</div><button type="button" onClick={() => void loadInitial()} disabled={loading} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-xs text-text-secondary hover:border-accent hover:text-text disabled:opacity-50"><RefreshCw size={13} className={loading ? 'animate-spin' : ''} />{loading ? '读取中' : '重新读取'}</button></div>
      </div>

      <div className="grid grid-cols-1 gap-3 border border-border rounded-md p-3 md:grid-cols-[1fr_1fr_1fr_auto_auto] md:items-end">
        <label className="text-xs text-text-secondary"><span className="mb-1 flex items-center gap-1"><SlidersHorizontal size={12} />组合数量</span><input type="number" min={5} max={15} value={topN} onChange={(event) => setTopN(Math.min(15, Math.max(5, Number(event.target.value) || 10)))} className="w-full rounded-md border border-border bg-bg px-2.5 py-2 font-mono text-sm text-text focus:border-accent focus:outline-none" /></label>
        <label className="text-xs text-text-secondary"><span className="mb-1 block">机构候选池</span><input type="number" min={20} max={120} step={10} value={candidatePool} onChange={(event) => setCandidatePool(Math.min(120, Math.max(20, Number(event.target.value) || 60)))} className="w-full rounded-md border border-border bg-bg px-2.5 py-2 font-mono text-sm text-text focus:border-accent focus:outline-none" /></label>
        <label className="text-xs text-text-secondary"><span className="mb-1 block">财务数据模式</span><select value={mode} onChange={(event) => setMode(event.target.value as 'strict' | 'pragmatic')} className="w-full rounded-md border border-border bg-bg px-2.5 py-2 text-sm text-text focus:border-accent focus:outline-none"><option value="pragmatic">宽松研究</option><option value="strict">严格可审计</option></select></label>
        <button type="button" onClick={() => run(false)} disabled={active || working} aria-busy={working} className="inline-flex items-center justify-center gap-1.5 rounded-md bg-accent px-3 py-2 text-xs text-white disabled:opacity-50"><RefreshCw size={14} className={active || working ? 'animate-spin' : ''} />{working ? '提交中' : active ? '计算中' : '运行双引擎'}</button>
        <button type="button" onClick={() => run(true)} disabled={active || working} aria-busy={working} className="inline-flex items-center justify-center gap-1.5 rounded-md border border-border px-3 py-2 text-xs text-text-secondary hover:border-accent hover:text-text disabled:opacity-50" title="忽略短期行情缓存并重新获取"><RefreshCw size={13} className={working ? 'animate-spin' : ''} />{working ? '提交中' : '强制更新'}</button>
      </div>

      <div className="text-xs text-text-secondary">优先使用盘中行情；非交易时段或源站失败时使用最近有效缓存。严格模式缺失上市历史或 PE 历史分位时会明确排除，不用零值补齐。</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-y border-border py-3">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-secondary">
          <span>证券主表 <b className="font-mono font-normal text-text">{syncStatus?.coverage.security_total ?? '--'}</b></span>
          <span>上市日期 <b className="font-mono font-normal text-text">{syncStatus ? `${syncStatus.coverage.listing_dated}/${syncStatus.coverage.currently_listed}` : '--'}</b></span>
          <span>PE 历史 <b className="font-mono font-normal text-text">{syncStatus ? `${syncStatus.coverage.current_valuation_series ?? syncStatus.coverage.valuation_series}/${syncStatus.coverage.currently_listed}` : '--'}</b></span>
          <span>正 PE 分位 <b className="font-mono font-normal text-text">{syncStatus?.coverage.current_valuation_percentiles ?? syncStatus?.coverage.valuation_percentiles ?? '--'}</b></span>
          <span>估值日期 <b className="font-mono font-normal text-text">{syncStatus?.coverage.valuation_date || '--'}</b></span>
        </div>
        <button type="button" onClick={startDataSync} disabled={syncActive || syncWorking} aria-busy={syncWorking} className="inline-flex items-center justify-center gap-1.5 rounded-md border border-accent px-3 py-2 text-xs text-accent hover:bg-[#1F6FEB1A] disabled:opacity-50"><Database size={14} className={syncWorking ? 'animate-pulse' : ''} />{syncWorking ? '提交中' : syncActive ? '审计补数中' : '补齐审计数据'}</button>
      </div>
      {syncStatus?.run && <DataSyncProgress status={syncStatus} />}
      {syncError && <div className="flex items-start gap-2 rounded-md border border-down/50 bg-[#EF535022] p-3 text-xs text-down"><AlertTriangle size={15} className="shrink-0" />{syncError}</div>}
      {notice && <div className="flex items-start gap-2 rounded-md border border-accent/50 bg-[#1F6FEB1A] p-3 text-xs text-accent"><CheckCircle2 size={15} className="shrink-0" />{notice}</div>}
      {job && (active || job.status === 'completed') && <Progress job={job} />}
      {job?.status === 'failed' && <div className="flex items-start gap-2 rounded-md border border-down/50 bg-[#EF535022] p-3 text-xs text-down"><AlertTriangle size={15} className="shrink-0" />{job.error || '任务失败，请重试。'}</div>}
      {error && job?.status !== 'failed' && <div className="flex items-start gap-2 rounded-md border border-down/50 bg-[#EF535022] p-3 text-xs text-down"><AlertTriangle size={15} className="shrink-0" />{error}</div>}

      {result ? <>
        {result.warnings?.length > 0 && <div className="space-y-1 rounded-md border border-warn/50 bg-[#D299221A] p-3 text-xs text-warn"><div className="flex items-center gap-2 font-medium"><ShieldAlert size={14} />全局数据提示</div>{result.warnings.slice(0, 6).map((warning) => <div key={warning} className="pl-5">{warning}</div>)}</div>}
        <section className="space-y-4 border-y border-border py-4">
          <PortfolioBlock portfolio={result.retail_portfolio} />
          <PortfolioBlock portfolio={result.institutional_portfolio} />
        </section>

        <section className="space-y-3 border-t border-border pt-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-text"><ShieldAlert size={15} className="text-warn" />数据合同与可审计范围</div>
          <div className="overflow-x-auto rounded-md border border-border"><table className="w-full min-w-[720px] text-xs"><thead className="bg-[#161B22] text-text-secondary"><tr><th className="px-3 py-2 text-left">字段</th><th className="px-3 py-2 text-left">状态</th><th className="px-3 py-2 text-right">覆盖</th><th className="px-3 py-2 text-left">说明</th></tr></thead><tbody>{Object.entries(result.data_contract || {}).map(([key, item]) => { const status = contractStatus(item.status); return <tr key={key} className="border-t border-border/70"><td className="px-3 py-2 text-text">{CONTRACT_LABELS[key] || key}</td><td className="px-3 py-2"><span className={`rounded border px-1.5 py-0.5 ${status.className}`}>{status.label}</span></td><td className="px-3 py-2 text-right font-mono text-text-secondary">{item.covered != null ? `${item.covered}/${item.total ?? '--'}` : '--'}</td><td className="px-3 py-2 text-text-secondary">{item.note || item.formula || (key === 'survivorship_bias' ? '历史退市证券主表尚未接入' : '--')}</td></tr>; })}</tbody></table></div>
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-text-secondary"><span>数据日期 {result.data_date || '--'}</span><span>来源 {result.source || '--'}</span><span>缓存参与 {result.cache_used ? '是' : '否'}</span><span>当前页面仅供研究参考，不构成投资建议</span></div>
        </section>
        <div className="border-t border-border pt-3 text-[11px] leading-5 text-text-secondary">{result.disclaimer}</div>
      </> : <div className="border border-border rounded-md py-16 text-center text-sm text-text-secondary"><Layers3 size={24} className="mx-auto mb-2 text-border" />还没有双引擎结果，设置参数后运行一次。</div>}
    </div>
  );
}
