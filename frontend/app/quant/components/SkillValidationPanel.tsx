'use client';

import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Database, FlaskConical, Loader2, Play, RefreshCw } from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';

type Skill = {
  skill_id: string;
  skill_name: string;
  skill_version: string;
  lifecycle_state: string;
  validation_status: string;
  required_data_level: string;
  sample_size: number;
  precision?: number | null;
  recall?: number | null;
  hit_rate?: number | null;
  avg_excess_return?: number | null;
  profit_loss_ratio?: number | null;
  max_drawdown?: number | null;
  brier_score?: number | null;
  description: string;
};

type Job = { job_id: string; status: string; progress?: number; phase?: string; message?: string; error?: string; result?: any };

function percent(value?: number | null, digits = 1): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : '--';
}
function number(value?: number | null, digits = 2): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '--';
}

function lifecycleClass(value: string): string {
  if (value === 'ACTIVE') return 'border-up/50 bg-[#26A69A18] text-up';
  if (value === 'DEGRADED' || value === 'SHADOW') return 'border-warn/50 bg-[#D2992218] text-warn';
  if (value === 'DEPRECATED') return 'border-down/50 bg-[#EF535018] text-down';
  return 'border-border bg-[#161B22] text-text-secondary';
}

export default function SkillValidationPanel() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [history, setHistory] = useState<any[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [startDate, setStartDate] = useState(() => {
    const date = new Date(); date.setFullYear(date.getFullYear() - 1); return date.toISOString().slice(0, 10);
  });
  const [endDate, setEndDate] = useState(() => new Date().toISOString().slice(0, 10));

  const load = async () => {
    setLoading(true); setError('');
    try {
      const [registry, runs] = await Promise.all([
        apiFetch<{ data: { skills: Skill[] } }>('/trading-skills/registry'),
        apiFetch<{ data: any[] }>('/trading-skills/validation/history?limit=18'),
      ]);
      const next = registry.data.skills || [];
      setSkills(next); setHistory(runs.data || []);
      setSelectedId((current) => next.some((item) => item.skill_id === current) ? current : next[0]?.skill_id || '');
    } catch (caught) {
      setError(friendlyApiError(caught, 'Skill注册表暂时无法读取'));
    } finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, []);

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const response = await apiFetch<{ data: Job }>(`/trading-skills/validation/status/${job.job_id}`, { cache: 'no-store' });
        if (cancelled) return;
        setJob(response.data); setProgress(response.data.progress || 0);
        if (response.data.status === 'completed') { setRunning(false); setNotice('验证报告已生成；未通过门槛的技能保持研究状态。'); void load(); }
        if (response.data.status === 'failed') { setRunning(false); setError(response.data.error || 'Skill验证失败'); }
      } catch (caught) { if (!cancelled) setError(friendlyApiError(caught, '验证状态读取失败')); }
    };
    void poll();
    const timer = window.setInterval(poll, 1600);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [job?.job_id, job?.status]);

  const selected = useMemo(() => skills.find((item) => item.skill_id === selectedId), [skills, selectedId]);
  const run = async () => {
    if (!selectedId || running) return;
    setRunning(true); setProgress(5); setError(''); setNotice('');
    try {
      const response = await apiFetch<{ data: Job }>('/trading-skills/validation', {
        method: 'POST', body: JSON.stringify({ skill_id: selectedId, start_date: startDate, end_date: endDate, max_stocks: 150 }),
      });
      setJob(response.data); setProgress(response.data.progress || 5);
    } catch (caught) { setRunning(false); setError(friendlyApiError(caught, '验证任务提交失败')); }
  };

  if (loading) return <section className="border border-border rounded-md p-5 text-center text-xs text-text-secondary"><Loader2 size={20} className="mx-auto animate-spin text-accent" /><div className="mt-2">正在读取交易技能注册表</div></section>;
  return <section className="border border-border rounded-md p-3 space-y-3">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-sm font-semibold text-text flex items-center gap-2"><FlaskConical size={15} className="text-accent" />交易 Skill 验证实验室</h2><p className="mt-1 text-[11px] text-text-secondary">PIT · Walk-Forward · 样本外 · 成本与校准；验证结果只决定生命周期，不产生下单指令。</p></div><button type="button" onClick={() => void load()} disabled={loading} className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-[11px] text-text-secondary hover:border-accent hover:text-text"><RefreshCw size={12} />刷新注册表</button></div>
    {error && <div className="flex gap-2 rounded-md border border-down/50 bg-[#EF535018] p-2 text-[11px] text-down"><AlertTriangle size={13} className="shrink-0" />{error}</div>}
    {notice && <div className="rounded-md border border-up/50 bg-[#26A69A18] p-2 text-[11px] text-up">{notice}</div>}
    <div className="overflow-x-auto rounded-md border border-border"><table className="w-full min-w-[940px] text-[11px]"><thead className="bg-[#161B22] text-text-secondary"><tr><th className="px-2.5 py-2 text-left">Skill</th><th className="px-2.5 py-2 text-left">生命周期</th><th className="px-2.5 py-2 text-left">数据等级</th><th className="px-2.5 py-2 text-right">样本</th><th className="px-2.5 py-2 text-right">Precision</th><th className="px-2.5 py-2 text-right">超额</th><th className="px-2.5 py-2 text-right">盈亏比</th><th className="px-2.5 py-2 text-right">Brier</th></tr></thead><tbody>{skills.map((item) => <tr key={item.skill_id} onClick={() => setSelectedId(item.skill_id)} className={`cursor-pointer border-t border-border/70 ${item.skill_id === selectedId ? 'bg-[#1F6FEB18]' : 'hover:bg-[#161B22]'}`}><td className="max-w-[260px] px-2.5 py-2"><div className="text-text">{item.skill_name}</div><div className="mt-0.5 truncate text-[10px] text-text-secondary">{item.description}</div></td><td className="px-2.5 py-2"><span className={`rounded border px-1.5 py-0.5 ${lifecycleClass(item.lifecycle_state)}`}>{item.lifecycle_state}</span><div className="mt-1 text-[10px] text-text-secondary">{item.validation_status}</div></td><td className="px-2.5 py-2 font-mono text-text-secondary">{item.required_data_level}</td><td className="px-2.5 py-2 text-right font-mono text-text">{item.sample_size || 0}</td><td className="px-2.5 py-2 text-right font-mono text-text">{percent(item.precision)}</td><td className="px-2.5 py-2 text-right font-mono text-text">{percent(item.avg_excess_return)}</td><td className="px-2.5 py-2 text-right font-mono text-text">{number(item.profit_loss_ratio)}</td><td className="px-2.5 py-2 text-right font-mono text-text">{number(item.brier_score, 3)}</td></tr>)}</tbody></table></div>
    <div className="grid grid-cols-1 gap-3 md:grid-cols-[minmax(0,1fr)_minmax(250px,auto)] md:items-end"><label className="text-[11px] text-text-secondary">验证 Skill<select value={selectedId} onChange={(event) => setSelectedId(event.target.value)} className="mt-1 w-full rounded-md border border-border bg-bg px-2 py-2 text-xs text-text">{skills.map((item) => <option key={item.skill_id} value={item.skill_id}>{item.skill_name} · {item.lifecycle_state}</option>)}</select></label><div className="grid grid-cols-2 gap-2"><label className="text-[11px] text-text-secondary">开始日期<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} className="mt-1 rounded-md border border-border bg-bg px-2 py-2 font-mono text-xs text-text" /></label><label className="text-[11px] text-text-secondary">结束日期<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} className="mt-1 rounded-md border border-border bg-bg px-2 py-2 font-mono text-xs text-text" /></label></div></div>
    <div className="flex flex-wrap items-center justify-between gap-3"><span className="text-[10px] text-text-secondary">当前选择：{selected?.skill_name || '--'} · 竞价 Skill 历史不足时仅实时 Shadow</span><button type="button" onClick={() => void run()} disabled={running || !selectedId} className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-2 text-xs text-white disabled:opacity-50"><Play size={13} className={running ? 'animate-pulse' : ''} />{running ? '验证计算中' : '运行 PIT 验证'}</button></div>
    {running && <div className="rounded-md border border-accent/50 bg-[#1F6FEB18] p-2.5"><div className="flex justify-between text-[11px] text-text"><span className="inline-flex items-center gap-1.5"><Loader2 size={13} className="animate-spin text-accent" />{job?.message || '正在锁定数据'}</span><span className="font-mono text-accent">{progress}%</span></div><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-bg"><div className="h-full bg-accent transition-[width]" style={{ width: `${progress}%` }} /></div><div className="mt-1 text-[10px] text-text-secondary">阶段：{job?.phase || 'queued'}</div></div>}
    <div className="flex items-start gap-2 border-t border-border pt-2 text-[10px] leading-4 text-warn"><Database size={13} className="mt-0.5 shrink-0" />当前数据库的历史点时股票池从 2026-08-03 起前向积累，实验结果会保留日线观测偏差警告，样本不足不会自动晋级 ACTIVE。</div>
    {history.length > 0 && <div className="border-t border-border pt-2 text-[10px] text-text-secondary">最近验证：{history.slice(0, 3).map((item) => `${item.skill_id} · ${item.status} · ${item.sample_size}样本`).join(' ｜ ')}</div>}
  </section>;
}
