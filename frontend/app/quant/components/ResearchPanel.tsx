'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  BadgeCheck,
  CheckCircle2,
  Database,
  FileCheck2,
  FlaskConical,
  Loader2,
  Play,
  Search,
  ShieldAlert,
  SlidersHorizontal,
  XCircle,
} from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';
import type {
  BackgroundJob,
  ResearchExperiment,
  ResearchFactor,
  ResearchReport,
  ResearchWorkspace,
} from '../types';

function signed(value: number | undefined, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`;
}

function numeric(value: number | undefined, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
  return value.toFixed(digits);
}

function statusClass(status: string): string {
  if (['AUDITED', 'VALIDATED', 'COMPOSABLE'].includes(status)) return 'border-up/50 bg-[#26A69A18] text-up';
  if (['BLOCKED_DATA', 'DRAFT', 'DECAYING'].includes(status)) return 'border-warn/50 bg-[#D2992218] text-warn';
  if (['REJECTED', 'RETIRED'].includes(status)) return 'border-down/50 bg-[#EF535018] text-down';
  return 'border-border bg-[#161B22] text-text-secondary';
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    AUDITED: '已审计',
    VALIDATED: '已验证',
    COMPOSABLE: '可组合',
    DRAFT: '草稿',
    BLOCKED_DATA: '该实验缺关键数据',
    READY_RESEARCH_ONLY: '可研究',
    RESEARCH_ONLY: '仅研究',
    INSUFFICIENT_DATA: '数据不足',
    VALIDATION_PENDING: '待验证',
  };
  return labels[status] || status;
}

function inventoryLabel(status: string): { label: string; className: string } {
  if (status === 'ready' || status === 'derived_ready') return { label: status === 'derived_ready' ? '日线推导可研究' : '可读取', className: 'border-up/50 bg-[#26A69A18] text-up' };
  if (status === 'collecting' || status === 'partial' || status === 'derived_partial') return { label: status === 'derived_partial' ? '部分推导' : '持续积累', className: 'border-warn/50 bg-[#D2992218] text-warn' };
  if (status === 'forward_only') return { label: '仅前向采集', className: 'border-warn/50 bg-[#D2992218] text-warn' };
  if (status === 'missing') return { label: '尚未采集', className: 'border-down/50 bg-[#EF535018] text-down' };
  return { label: status || '未知', className: 'border-border bg-[#161B22] text-text-secondary' };
}

function DatasetSnapshot({ dataset }: { dataset: ResearchWorkspace['dataset'] }) {
  const [from, to] = dataset.date_range || [null, null];
  const inventory = dataset.data_inventory || [];
  return <section className="border border-border rounded-md p-3">
    <div className="flex flex-wrap items-center justify-between gap-2 mb-3"><h2 className="text-sm font-semibold text-text flex items-center gap-2"><Database size={15} className="text-accent" />数据快照</h2><span className={`rounded border px-1.5 py-0.5 text-[11px] ${dataset.available ? 'border-up/50 bg-[#26A69A18] text-up' : 'border-warn/50 bg-[#D2992218] text-warn'}`}>{dataset.available ? '可读取' : '不可用'}</span></div>
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
      <div><div className="text-text-secondary">日线记录</div><div className="font-mono text-text mt-1">{(dataset.record_count || 0).toLocaleString('zh-CN')}</div></div>
      <div><div className="text-text-secondary">股票数量</div><div className="font-mono text-text mt-1">{(dataset.stock_count || 0).toLocaleString('zh-CN')}</div></div>
      <div><div className="text-text-secondary">覆盖区间</div><div className="font-mono text-text mt-1">{from || '--'} 至 {to || '--'}</div></div>
      <div><div className="text-text-secondary">来源</div><div className="text-text mt-1 truncate" title={(dataset.source || []).join(',')}>{(dataset.source || []).join(', ') || '缓存不可用'}</div></div>
    </div>
    <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-text-secondary"><span>数据集 {dataset.dataset_id}</span><span>历史股票池 {dataset.universe?.status === 'ready' ? '完整' : dataset.universe?.status === 'partial' ? '部分覆盖（可研究、有偏差）' : dataset.universe?.observed_from_daily_bars ? '日线观测（有偏差）' : '仅前向采集'}</span><span>点时状态 {dataset.point_in_time?.status || '--'}</span><span>行情缓存参与 {dataset.cache_used ? '是' : '否'}</span><span>清单缓存 {dataset.manifest_cache_used ? '命中' : '刚刷新'}</span></div>
    {(dataset.warnings || []).length > 0 && <div className="mt-3 space-y-1 text-[11px] text-warn">{dataset.warnings?.slice(0, 3).map((warning) => <div key={warning} className="flex gap-1.5"><AlertTriangle size={12} className="mt-0.5 shrink-0" />{warning}</div>)}</div>}
    {inventory.length > 0 && <div className="mt-3 overflow-x-auto rounded-md border border-border"><table className="w-full min-w-[760px] text-[11px]"><thead className="bg-[#161B22] text-text-secondary"><tr><th className="px-2.5 py-2 text-left">数据集</th><th className="px-2.5 py-2 text-left">状态</th><th className="px-2.5 py-2 text-right">记录</th><th className="px-2.5 py-2 text-right">交易日</th><th className="px-2.5 py-2 text-left">覆盖区间</th><th className="px-2.5 py-2 text-left">口径</th></tr></thead><tbody>{inventory.map((item) => { const badge = inventoryLabel(item.status); const range = item.date_range || []; const sessions = item.complete_sessions ?? item.session_count ?? 0; const observed = item.observed_sessions ?? item.session_count ?? 0; const sessionLabel = observed !== sessions ? `${sessions}/${item.target_sessions || '--'}（观测${observed}）` : `${sessions}/${item.target_sessions || '--'}`; return <tr key={item.key} className="border-t border-border/70 align-top"><td className="px-2.5 py-2 text-text">{item.label}</td><td className="px-2.5 py-2"><span className={`rounded border px-1.5 py-0.5 ${badge.className}`}>{badge.label}</span></td><td className="px-2.5 py-2 text-right font-mono text-text">{(item.record_count || 0).toLocaleString('zh-CN')}</td><td className="px-2.5 py-2 text-right font-mono text-text">{sessionLabel}</td><td className="px-2.5 py-2 font-mono text-text-secondary">{range[0] || '--'} 至 {range[1] || '--'}</td><td className="max-w-[300px] px-2.5 py-2 text-text-secondary">{item.note || '--'}</td></tr>; })}</tbody></table></div>}
  </section>;
}

function ExperimentCard({ item, selected, onSelect }: { item: ResearchExperiment; selected: boolean; onSelect: () => void }) {
  return <button type="button" onClick={onSelect} className={`w-full text-left border rounded-md p-3 transition-colors ${selected ? 'border-accent bg-[#1F6FEB18]' : 'border-border hover:border-accent/60'}`}>
    <div className="flex items-start justify-between gap-2"><div className="min-w-0"><div className="text-sm font-semibold text-text truncate">{item.name}</div><div className="text-[11px] text-text-secondary mt-1">{item.cadence} · {item.family}</div></div><span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${statusClass(item.status)}`}>{statusLabel(item.status)}</span></div>
    <div className="text-xs text-text-secondary mt-2 leading-5">{item.description}</div>
    <div className="flex flex-wrap gap-1 mt-2">{item.factor_names.map((name) => <span key={name} className="rounded border border-border px-1.5 py-0.5 text-[10px] text-text-secondary">{name}</span>)}</div>
    {item.blockers.length > 0 && <div className="mt-2 text-[11px] text-warn">{item.supported ? '研究限制' : '该实验缺口'}：{item.blockers[0]}</div>}
  </button>;
}

function ReportView({ report }: { report: ResearchReport }) {
  const result = report.result || {};
  const partitions = Object.entries(report.partitions || {});
  const stress = Object.entries(report.stress_tests || {});
  return <div className="space-y-4">
    <section className={`border rounded-md p-3 ${report.status === 'BLOCKED_DATA' ? 'border-warn/50 bg-[#D2992212]' : 'border-accent/50 bg-[#1F6FEB12]'}`}>
      <div className="flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2 text-sm font-semibold text-text">{report.status === 'BLOCKED_DATA' ? <ShieldAlert size={16} className="text-warn" /> : <FileCheck2 size={16} className="text-accent" />}{report.experiment.name} · {statusLabel(report.status)}</div><div className="text-xs text-text-secondary">生命周期：{report.promotion_stage}</div></div>
      <div className="mt-2 text-[11px] text-text-secondary break-all">参数锁定哈希：<span className="font-mono text-text">{report.strategy_lock_hash}</span></div>
    </section>

    {result.available !== false && <>
      <section className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 border border-border rounded-md divide-x divide-y md:divide-y-0 divide-border">
        {[
          ['总收益', signed(result.total_return), result.total_return != null && result.total_return >= 0 ? 'text-up' : 'text-down'],
          ['基准收益', signed(result.benchmark_return), result.benchmark_return != null && result.benchmark_return >= 0 ? 'text-up' : 'text-down'],
          ['胜率', result.win_rate == null ? '--' : `${numeric(result.win_rate, 1)}%`, result.win_rate != null && result.win_rate >= 50 ? 'text-up' : 'text-text'],
          ['最大回撤', result.max_drawdown == null ? '--' : `${numeric(result.max_drawdown)}%`, 'text-warn'],
          ['夏普', numeric(result.sharpe_ratio), 'text-text'],
          ['IC', numeric(result.information_coefficient, 4), 'text-text'],
        ].map(([label, value, color]) => <div key={label as string} className="p-3"><div className="text-xs text-text-secondary">{label}</div><div className={`font-mono mt-1 text-base ${color}`}>{value}</div></div>)}
      </section>
      <section className="border border-border rounded-md overflow-x-auto"><table className="w-full min-w-[760px] text-xs"><thead className="bg-[#161B22] text-text-secondary"><tr><th className="px-3 py-2 text-left">分区</th><th className="px-3 py-2 text-right">交易期</th><th className="px-3 py-2 text-left">区间</th><th className="px-3 py-2 text-right">收益</th><th className="px-3 py-2 text-right">胜率</th><th className="px-3 py-2 text-right">Profit Factor</th><th className="px-3 py-2 text-right">最大回撤</th><th className="px-3 py-2 text-right">数据量</th></tr></thead><tbody>{partitions.map(([key, item]) => <tr key={key} className="border-t border-border/70"><td className="px-3 py-2 text-text font-medium">{{ train: '训练集', validation: '验证集', out_of_sample: '样本外' }[key] || key}</td><td className="px-3 py-2 text-right font-mono">{item.trading_periods}</td><td className="px-3 py-2 text-text-secondary">{item.from || '--'} 至 {item.to || '--'}</td><td className={`px-3 py-2 text-right font-mono ${item.total_return >= 0 ? 'text-up' : 'text-down'}`}>{signed(item.total_return)}</td><td className="px-3 py-2 text-right font-mono">{numeric(item.win_rate, 1)}%</td><td className="px-3 py-2 text-right font-mono">{numeric(item.profit_factor, 3)}</td><td className="px-3 py-2 text-right font-mono text-warn">{numeric(item.max_drawdown)}%</td><td className="px-3 py-2 text-right">{item.data_sufficient ? <span className="text-up">充分</span> : <span className="text-warn">不足</span>}</td></tr>)}</tbody></table></section>
      <section className="border border-border rounded-md overflow-x-auto"><table className="w-full min-w-[700px] text-xs"><thead className="bg-[#161B22] text-text-secondary"><tr><th className="px-3 py-2 text-left">压力情景</th><th className="px-3 py-2 text-right">收益</th><th className="px-3 py-2 text-right">最大回撤</th><th className="px-3 py-2 text-right">可计算</th><th className="px-3 py-2 text-left">口径</th></tr></thead><tbody>{stress.map(([key, item]) => <tr key={key} className="border-t border-border/70"><td className="px-3 py-2 text-text">{{ base: '基准成本', cost_plus_50pct: '成本+50%', slippage_x2: '滑点×2', fill_rate_70pct: '成交率70%', fill_rate_50pct: '成交率50%' }[key] || key}</td><td className={`px-3 py-2 text-right font-mono ${(item.total_return || 0) >= 0 ? 'text-up' : 'text-down'}`}>{item.total_return == null ? '--' : signed(item.total_return)}</td><td className="px-3 py-2 text-right font-mono">{item.max_drawdown == null ? '--' : `${numeric(item.max_drawdown)}%`}</td><td className="px-3 py-2 text-right">{item.available ? <span className="text-up">是</span> : <span className="text-warn">否</span>}</td><td className="px-3 py-2 text-text-secondary">{item.note}</td></tr>)}</tbody></table></section>
    </>}

    {result.error && <div className="border border-warn/50 bg-[#D2992212] rounded-md p-3 text-xs text-warn flex gap-2"><AlertTriangle size={14} className="shrink-0" />{result.error}</div>}
    {report.gates.length > 0 && <section className="border border-border rounded-md overflow-x-auto"><div className="px-3 py-2 border-b border-border text-sm font-semibold text-text flex items-center gap-2"><BadgeCheck size={15} className="text-accent" />研究硬门槛</div><table className="w-full min-w-[820px] text-xs"><thead className="bg-[#161B22] text-text-secondary"><tr><th className="px-3 py-2 text-left">门槛</th><th className="px-3 py-2 text-left">标准</th><th className="px-3 py-2 text-right">实际</th><th className="px-3 py-2 text-right">状态</th><th className="px-3 py-2 text-left">备注</th></tr></thead><tbody>{report.gates.map((gate) => <tr key={gate.id} className="border-t border-border/70"><td className="px-3 py-2 text-text">{gate.label}</td><td className="px-3 py-2 text-text-secondary">{gate.threshold}</td><td className="px-3 py-2 text-right font-mono">{gate.actual == null ? '--' : typeof gate.actual === 'number' ? numeric(gate.actual) : gate.actual}</td><td className="px-3 py-2 text-right">{gate.passed ? <span className="inline-flex items-center gap-1 text-up"><CheckCircle2 size={12} />通过</span> : <span className="inline-flex items-center gap-1 text-warn"><XCircle size={12} />未通过</span>}</td><td className="px-3 py-2 text-text-secondary">{gate.reason || '--'}</td></tr>)}</tbody></table></section>}
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4"><section className="border border-border rounded-md p-3"><div className="text-sm font-semibold text-text mb-2">审计日志</div><div className="space-y-2 text-xs text-text-secondary">{report.audit_log.map((item, index) => <div key={`${index}-${item}`} className="flex gap-2"><span className="font-mono text-accent">{String(index + 1).padStart(2, '0')}</span><span>{item}</span></div>)}</div></section><section className="border border-border rounded-md p-3"><div className="text-sm font-semibold text-text mb-2">下一步</div><div className="space-y-2 text-xs text-text-secondary">{report.next_actions.map((item) => <div key={item} className="flex gap-2"><span className="text-accent">•</span><span>{item}</span></div>)}</div></section></div>
  </div>;
}

function FactorCatalog({ factors }: { factors: ResearchFactor[] }) {
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const [status, setStatus] = useState('all');
  const categories = useMemo(() => Array.from(new Set(factors.map((item) => item.category))).sort(), [factors]);
  const filtered = useMemo(() => factors.filter((item) => {
    const text = `${item.id} ${item.name} ${item.formula} ${item.economic_logic}`.toLowerCase();
    return (!query || text.includes(query.toLowerCase())) && (category === 'all' || item.category === category) && (status === 'all' || item.status === status);
  }), [factors, query, category, status]);
  return <section className="border border-border rounded-md overflow-hidden"><div className="p-3 border-b border-border flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-sm font-semibold text-text flex items-center gap-2"><Search size={15} className="text-accent" />因子检索</h2><div className="text-[11px] text-text-secondary mt-1">{filtered.length}/{factors.length} 个注册因子</div></div><div className="flex flex-wrap gap-2"><label className="relative"><span className="sr-only">搜索因子</span><Search size={13} className="absolute left-2 top-2.5 text-text-secondary" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="名称、公式或字段" className="w-48 max-w-[45vw] rounded-md border border-border bg-bg py-1.5 pl-7 pr-2 text-xs text-text placeholder:text-text-secondary focus:border-accent focus:outline-none" /></label><select value={category} onChange={(event) => setCategory(event.target.value)} className="rounded-md border border-border bg-bg px-2 py-1.5 text-xs text-text focus:border-accent focus:outline-none"><option value="all">全部类别</option>{categories.map((item) => <option key={item} value={item}>{item}</option>)}</select><select value={status} onChange={(event) => setStatus(event.target.value)} className="rounded-md border border-border bg-bg px-2 py-1.5 text-xs text-text focus:border-accent focus:outline-none"><option value="all">全部状态</option><option value="AUDITED">已审计</option><option value="DRAFT">草稿</option></select></div></div><div className="overflow-x-auto"><table className="w-full min-w-[1050px] text-xs"><thead className="bg-[#161B22] text-text-secondary"><tr><th className="px-3 py-2 text-left">因子</th><th className="px-3 py-2 text-left">类别</th><th className="px-3 py-2 text-left">公式</th><th className="px-3 py-2 text-left">可用时点</th><th className="px-3 py-2 text-left">来源</th><th className="px-3 py-2 text-left">经济逻辑</th><th className="px-3 py-2 text-left">状态</th></tr></thead><tbody>{filtered.map((item) => <tr key={item.id} className="border-t border-border/70 align-top"><td className="px-3 py-2.5 text-text"><div className="font-medium">{item.name}</div><div className="font-mono text-[10px] text-text-secondary mt-1">{item.id} · v{item.version}</div></td><td className="px-3 py-2.5 text-text-secondary">{item.category}</td><td className="px-3 py-2.5 font-mono text-text-secondary max-w-[270px]">{item.formula}</td><td className="px-3 py-2.5 text-text-secondary">{item.available_at}</td><td className="px-3 py-2.5 text-text-secondary">{item.source}</td><td className="px-3 py-2.5 text-text-secondary max-w-[260px]">{item.economic_logic}</td><td className="px-3 py-2.5"><span className={`rounded border px-1.5 py-0.5 text-[10px] ${statusClass(item.status)}`}>{item.status_label}</span>{item.blocker && <div className="mt-1 text-[10px] text-warn">{item.blocker}</div>}</td></tr>)}</tbody></table></div></section>;
}

const DEFAULT_DSL = JSON.stringify({
  strategy_id: 'research_demo',
  name: '周频动量研究示例',
  family: 'weekly',
  version: '1.0.0',
  universe: { market: 'A_SHARE' },
  entry: { all: [{ factor: 'momentum_20d', operator: '>', value: 0 }] },
  exit: { stop_loss_pct: 5, force_exit_time: '14:50' },
  portfolio: { max_single_weight: 0.2 },
  cost_model: { name: 'research_protocol' },
}, null, 2);

export default function ResearchPanel() {
  const [workspace, setWorkspace] = useState<ResearchWorkspace | null>(null);
  const [report, setReport] = useState<ResearchReport | null>(null);
  const [selectedId, setSelectedId] = useState('weekly_momentum_baseline_v1');
  const [days, setDays] = useState(365);
  const [topN, setTopN] = useState(10);
  const [lookback, setLookback] = useState(20);
  const [holding, setHolding] = useState(5);
  const [capital, setCapital] = useState(400000);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [job, setJob] = useState<BackgroundJob | null>(null);
  const [progress, setProgress] = useState(8);
  const [error, setError] = useState<string | null>(null);
  const [dsl, setDsl] = useState(DEFAULT_DSL);
  const [dslResult, setDslResult] = useState<{ valid: boolean; status_label: string; errors: string[]; warnings: string[]; dsl_hash?: string } | null>(null);

  const loadWorkspace = useCallback(async (force = false) => {
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch<{ data: ResearchWorkspace }>(`/quant/research/workspace${force ? '?refresh=true' : ''}`);
      setWorkspace(response.data);
      if (response.data.latest_report) setReport(response.data.latest_report);
      if (response.data.active_job) {
        setJob(response.data.active_job);
        setRunning(['queued', 'running'].includes(response.data.active_job.status));
        setProgress(response.data.active_job.progress || 0);
      } else {
        setJob(null);
        setRunning(false);
      }
      setSelectedId((current) => response.data.experiments.some((item) => item.id === current) ? current : response.data.experiments[0]?.id || '');
    } catch (caught) {
      setError(`研究工作台读取失败：${friendlyApiError(caught, '研究数据暂时无法读取')}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadWorkspace(); }, [loadWorkspace]);

  const selected = workspace?.experiments.find((item) => item.id === selectedId) || null;

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return;
    let cancelled = false;
    let failures = 0;
    const poll = async () => {
      try {
        const response = await apiFetch<{ data: BackgroundJob }>(`/quant/research/run/status/${job.job_id}`);
        if (cancelled) return;
        failures = 0;
        const current = response.data;
        setJob(current);
        setProgress(current.progress || 0);
        if (current.status === 'completed') {
          const completedReport = current.result as unknown as ResearchReport | null;
          if (completedReport) setReport(completedReport);
          setRunning(false);
        } else if (current.status === 'failed') {
          setRunning(false);
          setError(current.error ? `研究失败：${current.error}` : current.message || '研究任务运行失败');
        }
      } catch (caught) {
        if (!cancelled) {
          failures += 1;
          setError(`研究任务仍在后台运行，状态读取暂时中断（第${failures}次）：${friendlyApiError(caught, '状态读取失败')}`);
        }
      }
    };
    void poll();
    const timer = window.setInterval(poll, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [job?.job_id, job?.status]);

  const runResearch = async () => {
    if (!selected || !selected.supported) return;
    setRunning(true);
    setProgress(8);
    setError(null);
    try {
      const response = await apiFetch<{ data: BackgroundJob }>('/quant/research/run', {
        method: 'POST',
        body: JSON.stringify({ experiment_id: selected.id, days, top_n: topN, lookback_days: lookback, holding_days: holding, capital }),
      });
      setJob(response.data);
      setProgress(response.data.progress || 0);
    } catch (caught) {
      setError(`研究任务提交失败：${friendlyApiError(caught, '研究任务提交失败')}`);
      setRunning(false);
    }
  };

  const validateDsl = async () => {
    setDslResult(null);
    try {
      const definition = JSON.parse(dsl) as Record<string, unknown>;
      const response = await apiFetch<{ data: { valid: boolean; status_label: string; errors: string[]; warnings: string[]; dsl_hash?: string } }>('/quant/research/dsl/validate', { method: 'POST', body: JSON.stringify({ definition }) });
      setDslResult(response.data);
    } catch (caught) {
      setDslResult({ valid: false, status_label: 'JSON格式错误', errors: [caught instanceof Error ? caught.message : 'DSL校验失败'], warnings: [] });
    }
  };

  if (loading) return <div className="py-20 text-center text-text-secondary"><Loader2 size={28} className="mx-auto animate-spin text-accent" /><div className="text-sm mt-3">正在读取研究数据清单与因子注册表</div><div className="w-64 max-w-full h-1.5 mx-auto mt-4 bg-[#21262D] rounded-full overflow-hidden"><div className="h-full bg-accent transition-all" style={{ width: '38%' }} /></div></div>;
  if (!workspace) return <div className="border border-border rounded-md py-16 text-center"><FlaskConical size={26} className="mx-auto text-warn" /><div className="text-sm text-text mt-3">研究工作台暂不可用</div><button type="button" onClick={() => loadWorkspace()} className="mt-4 inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-xs text-text-secondary hover:border-accent hover:text-text"><Play size={13} />重新读取</button></div>;

  return <div className="space-y-4">
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-3"><div><h2 className="text-base font-bold text-text flex items-center gap-2"><FlaskConical size={18} className="text-accent" />量化研究工作台</h2><p className="text-xs text-text-secondary mt-1">数据快照、因子假设、回测分区和晋级门槛统一留痕。</p></div><div className="flex items-center gap-3"><div className="text-[11px] text-text-secondary">研究结果只进入报告与模拟盘，不连接券商下单</div><button type="button" onClick={() => loadWorkspace(true)} disabled={loading} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-xs text-text-secondary hover:border-accent hover:text-text disabled:opacity-50"><Database size={13} className={loading ? 'animate-pulse' : ''} />{loading ? '读取中' : '刷新数据清单'}</button></div></div>
    <DatasetSnapshot dataset={workspace.dataset} />

    <section className="space-y-3"><div className="flex items-center justify-between gap-2"><h2 className="text-sm font-semibold text-text flex items-center gap-2"><SlidersHorizontal size={15} className="text-accent" />实验轨道</h2><span className="text-[11px] text-text-secondary">选择一个实验后锁定参数运行</span></div><div className="grid grid-cols-1 lg:grid-cols-3 gap-3">{workspace.experiments.map((item) => <ExperimentCard key={item.id} item={item} selected={item.id === selectedId} onSelect={() => setSelectedId(item.id)} />)}</div></section>

    <section className="border border-border rounded-md p-3"><div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 items-end"><label className="text-xs text-text-secondary">研究窗口<input type="number" min={30} max={730} value={days} onChange={(event) => setDays(Math.min(730, Math.max(30, Number(event.target.value) || 365)))} className="mt-1 w-full rounded-md border border-border bg-bg px-2 py-2 font-mono text-sm text-text focus:border-accent focus:outline-none" /></label><label className="text-xs text-text-secondary">持仓数量<input type="number" min={1} max={50} value={topN} onChange={(event) => setTopN(Math.min(50, Math.max(1, Number(event.target.value) || 10)))} className="mt-1 w-full rounded-md border border-border bg-bg px-2 py-2 font-mono text-sm text-text focus:border-accent focus:outline-none" /></label><label className="text-xs text-text-secondary">动量回看<input type="number" min={10} max={120} value={lookback} onChange={(event) => setLookback(Math.min(120, Math.max(10, Number(event.target.value) || 20)))} className="mt-1 w-full rounded-md border border-border bg-bg px-2 py-2 font-mono text-sm text-text focus:border-accent focus:outline-none" /></label><label className="text-xs text-text-secondary">持有期<input type="number" min={1} max={20} value={holding} onChange={(event) => setHolding(Math.min(20, Math.max(1, Number(event.target.value) || 5)))} className="mt-1 w-full rounded-md border border-border bg-bg px-2 py-2 font-mono text-sm text-text focus:border-accent focus:outline-none" /></label><label className="text-xs text-text-secondary">参考资金<input type="number" min={10000} max={100000000} step={10000} value={capital} onChange={(event) => setCapital(Math.min(100000000, Math.max(10000, Number(event.target.value) || 400000)))} className="mt-1 w-full rounded-md border border-border bg-bg px-2 py-2 font-mono text-sm text-text focus:border-accent focus:outline-none" /></label><button type="button" onClick={runResearch} disabled={running || !selected?.supported} className="inline-flex items-center justify-center gap-1.5 rounded-md bg-accent px-3 py-2 text-xs text-white disabled:opacity-50"><Play size={14} className={running ? 'animate-pulse' : ''} />{running ? '研究计算中' : selected?.supported ? '运行研究' : '该实验缺关键数据'}</button></div>{running && <div className="mt-3 border border-accent/50 bg-[#1F6FEB18] rounded-md p-2.5"><div className="flex justify-between gap-3 text-xs text-text"><span className="inline-flex items-center gap-1.5"><Loader2 size={13} className="animate-spin text-accent shrink-0" />{job?.message || '正在提交研究任务'}</span><span className="font-mono text-accent">{progress}%</span></div><div className="mt-2 h-1.5 bg-bg rounded-full overflow-hidden"><div className="h-full bg-accent transition-[width] duration-300" style={{ width: `${progress}%` }} /></div><div className="mt-1 text-[10px] text-text-secondary">阶段：{job?.phase || 'queued'} · 可留在本页等待，也可稍后返回查看</div></div>}</section>
    {error && <div className="border border-down/50 bg-[#EF535022] rounded-md p-3 text-xs text-down flex gap-2"><AlertTriangle size={14} className="shrink-0" />{error}</div>}

    {report && <ReportView report={report} />}

    <FactorCatalog factors={workspace.factor_catalog} />

    <section className="border border-border rounded-md p-3"><div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-sm font-semibold text-text flex items-center gap-2"><FileCheck2 size={15} className="text-accent" />安全 DSL 校验</h2><div className="text-[11px] text-text-secondary mt-1">AI 只能提交结构化因子定义，校验通过后才进入研究登记。</div></div><button type="button" onClick={validateDsl} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-xs text-text-secondary hover:border-accent hover:text-text"><ShieldAlert size={13} />校验 DSL</button></div><textarea value={dsl} onChange={(event) => setDsl(event.target.value)} spellCheck={false} className="mt-3 min-h-52 w-full rounded-md border border-border bg-bg p-3 font-mono text-[11px] leading-5 text-text focus:border-accent focus:outline-none" aria-label="研究 DSL" />{dslResult && <div className={`mt-3 rounded-md border p-3 text-xs ${dslResult.valid ? 'border-up/50 bg-[#26A69A18] text-up' : 'border-down/50 bg-[#EF535018] text-down'}`}><div className="flex items-center gap-2 font-medium">{dslResult.valid ? <CheckCircle2 size={14} /> : <XCircle size={14} />}{dslResult.status_label}</div>{dslResult.dsl_hash && <div className="mt-1 break-all font-mono text-[10px]">哈希：{dslResult.dsl_hash}</div>}{dslResult.errors.map((item) => <div key={item} className="mt-1">{item}</div>)}{dslResult.warnings.map((item) => <div key={item} className="mt-1 text-warn">{item}</div>)}</div>}</section>
  </div>;
}
