'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, BrainCircuit, ClipboardList, Layers3, LineChart, Loader2, MoonStar, Plus, Radio, Trash2, WalletCards } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import BacktestPanel from './components/BacktestPanel';
import PaperPanel from './components/PaperPanel';
import SignalList from './components/SignalList';
import StrategyBuilder from './components/StrategyBuilder';
import OvernightPanel from './components/OvernightPanel';
import FundamentalPanel from './components/FundamentalPanel';
import type { BackgroundJob, BacktestResult, RuleMeta, SectorOption, SignalSnapshot, Strategy, StrategyDraft, TradeSignal } from './types';

type TabId = 'strategies' | 'signals' | 'fundamental' | 'overnight' | 'backtest' | 'paper';
type Template = StrategyDraft & { id: string; description?: string };

const tabs: Array<{ id: TabId; label: string; icon: typeof ClipboardList }> = [
  { id: 'strategies', label: '策略管理', icon: ClipboardList },
  { id: 'signals', label: '信号看板', icon: Radio },
  { id: 'fundamental', label: '基本面双引擎', icon: Layers3 },
  { id: 'overnight', label: '一夜持股', icon: MoonStar },
  { id: 'backtest', label: '回测中心', icon: LineChart },
  { id: 'paper', label: '模拟盘', icon: WalletCards },
];

export default function QuantPage() {
  const [tab, setTab] = useState<TabId>('strategies');
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [rules, setRules] = useState<RuleMeta[]>([]);
  const [sectors, setSectors] = useState<SectorOption[]>([]);
  const [signals, setSignals] = useState<SignalSnapshot | null>(null);
  const [signalHistory, setSignalHistory] = useState<SignalSnapshot[]>([]);
  const [scanJob, setScanJob] = useState<BackgroundJob | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [builderNonce, setBuilderNonce] = useState(0);
  const [initialBacktestStrategy, setInitialBacktestStrategy] = useState<string | null>(null);
  const [paperDraft, setPaperDraft] = useState<TradeSignal | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSignals = useCallback(async () => {
    const [latest, history] = await Promise.allSettled([
      apiFetch<{ data: SignalSnapshot }>('/quant/signals'),
      apiFetch<{ data: SignalSnapshot[] }>('/quant/signals/history?limit=12'),
    ]);
    if (latest.status === 'fulfilled') setSignals(latest.value.data);
    if (history.status === 'fulfilled') setSignalHistory(history.value.data || []);
  }, []);

  const loadCore = useCallback(async () => {
    setLoading(true); setError(null);
    const responses = await Promise.allSettled([
      apiFetch<{ data: Strategy[] }>('/quant/strategies'),
      apiFetch<{ data: Template[] }>('/quant/templates'),
      apiFetch<{ data: { rules: RuleMeta[] } }>('/quant/rules'),
      apiFetch<{ data: SignalSnapshot }>('/quant/signals'),
      apiFetch<{ data: SignalSnapshot[] }>('/quant/signals/history?limit=12'),
    ]);
    const [strategyResponse, templateResponse, ruleResponse, signalResponse, historyResponse] = responses;
    if (strategyResponse.status === 'fulfilled') setStrategies(strategyResponse.value.data || []);
    if (templateResponse.status === 'fulfilled') setTemplates(templateResponse.value.data || []);
    if (ruleResponse.status === 'fulfilled') setRules(ruleResponse.value.data.rules || []);
    if (signalResponse.status === 'fulfilled') setSignals(signalResponse.value.data);
    if (historyResponse.status === 'fulfilled') setSignalHistory(historyResponse.value.data || []);
    const failures = responses.filter((item) => item.status === 'rejected');
    if (failures.length === responses.length) setError('量化模块暂时无法连接后端，请检查部署状态后重试。');
    setLoading(false);
    // Sector options are helpful for the rule builder but should not hold the
    // entire workspace hostage while a remote market directory is waking up.
    try {
      const response = await apiFetch<{ data: { sectors: SectorOption[] } }>('/quant/sectors?limit=500');
      setSectors(response.data.sectors || []);
    } catch { /* Builder remains usable with non-sector rules. */ }
  }, []);

  useEffect(() => { loadCore(); }, [loadCore]);
  useEffect(() => {
    if (!scanJob || !['queued', 'running'].includes(scanJob.status)) return;
    const poll = async () => {
      try {
        const response = await apiFetch<{ data: BackgroundJob }>(`/quant/scan/status/${scanJob.job_id}`);
        setScanJob(response.data);
        if (response.data.status === 'completed') await loadSignals();
      } catch (caught) { setError(caught instanceof Error ? caught.message : '扫描状态读取失败'); }
    };
    poll();
    const timer = window.setInterval(poll, 1200);
    return () => window.clearInterval(timer);
  }, [scanJob?.job_id, scanJob?.status, loadSignals]);

  const saveStrategy = async (draft: StrategyDraft, strategyId?: string) => {
    setSaving(true); setError(null);
    try {
      const response = await apiFetch<{ data: Strategy }>(strategyId ? `/quant/strategy/${strategyId}` : '/quant/strategy', {
        method: strategyId ? 'PUT' : 'POST', body: JSON.stringify(draft),
      });
      // Confirm the committed record through the read path before showing success.
      const persisted = await apiFetch<{ data: Strategy[] }>('/quant/strategies');
      const saved = (persisted.data || []).find((item) => item.id === response.data.id);
      if (!saved) throw new Error('后端未确认策略已持久化，请重试');
      setStrategies(persisted.data || []);
      setEditingId(saved.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '策略保存失败');
    } finally {
      setSaving(false);
    }
  };

  const previewStrategy = async (draft: StrategyDraft) => {
    const response = await apiFetch<{
      data: {
        count: number;
        warning?: string | null;
        feature_coverage?: SignalSnapshot['feature_coverage'];
      };
    }>('/quant/preview', { method: 'POST', body: JSON.stringify({ strategy: draft, limit: 20 }) });
    return response.data;
  };

  const deleteStrategy = async (strategy: Strategy) => {
    if (!window.confirm(`确认删除策略“${strategy.name}”吗？其历史回测文件不会被删除。`)) return;
    try {
      await apiFetch(`/quant/strategy/${strategy.id}`, { method: 'DELETE' });
      setStrategies((current) => current.filter((item) => item.id !== strategy.id));
      if (editingId === strategy.id) { setEditingId(null); setBuilderNonce((value) => value + 1); }
    } catch (caught) { setError(caught instanceof Error ? caught.message : '删除策略失败'); }
  };

  const startScan = async () => {
    setError(null);
    try {
      const response = await apiFetch<{ data: BackgroundJob }>('/quant/scan', { method: 'POST', body: JSON.stringify({ force: true }) });
      setScanJob(response.data);
    } catch (caught) { setError(caught instanceof Error ? caught.message : '扫描启动失败'); }
  };

  const moveToBacktest = (strategyId: string) => { setInitialBacktestStrategy(strategyId); setTab('backtest'); };
  const moveSignalToPaper = (signal: TradeSignal) => { setPaperDraft(signal); setTab('paper'); };
  const handleResult = useCallback((_result: BacktestResult) => undefined, []);
  const currentStrategy = strategies.find((item) => item.id === editingId) || null;

  if (loading) return <div className="max-w-7xl mx-auto px-4 py-20 flex flex-col items-center text-text-secondary"><Loader2 size={28} className="animate-spin text-accent mb-3" /><div className="text-sm">正在初始化量化工作台</div><div className="text-xs mt-1">加载策略、规则与缓存信号</div></div>;
  return <div className="max-w-7xl mx-auto px-4 py-5 md:py-6">
    <div className="flex flex-wrap items-start justify-between gap-3 mb-4"><div><h1 className="text-xl md:text-2xl font-bold text-text flex items-center gap-2"><BrainCircuit size={22} className="text-accent" />量化策略工作台</h1><p className="text-xs md:text-sm text-text-secondary mt-1">把选股逻辑固化为可回测、可追踪、人工确认执行的规则。</p></div><div className="text-xs text-text-secondary border border-border rounded-md px-2.5 py-1.5">实时信号 + 一年缓存日线 + 独立模拟盘</div></div>
    <div className="border border-border rounded-md overflow-x-auto mb-4"><div className="flex w-full min-w-[350px]">{tabs.map((item) => { const Icon = item.icon; return <button type="button" key={item.id} onClick={() => setTab(item.id)} className={`flex-1 inline-flex items-center justify-center gap-1 px-2 sm:px-4 py-2.5 text-xs sm:text-sm whitespace-nowrap border-r border-border last:border-r-0 ${tab === item.id ? 'bg-[#1F6FEB22] text-accent font-semibold' : 'text-text-secondary hover:bg-[#161B22] hover:text-text'}`}><Icon size={15} />{item.label}</button>; })}</div></div>
    {error && <div className="mb-4 border border-down/50 bg-[#EF535022] rounded-md p-3 text-xs text-down flex gap-2"><AlertTriangle size={15} className="shrink-0" />{error}</div>}

    {tab === 'strategies' && <div className="grid grid-cols-1 xl:grid-cols-[250px_minmax(0,1fr)] gap-4"><aside className="border border-border rounded-md overflow-hidden h-fit"><div className="flex items-center justify-between px-3 py-2 border-b border-border"><span className="text-sm font-semibold text-text">我的策略</span><button type="button" onClick={() => { setEditingId(null); setBuilderNonce((value) => value + 1); }} className="h-7 w-7 inline-flex items-center justify-center rounded-md text-accent hover:bg-[#1F6FEB22]" title="新建策略" aria-label="新建策略"><Plus size={16} /></button></div><div className="p-2 space-y-1">{strategies.map((strategy) => <div key={strategy.id} className={`group flex items-center gap-2 p-2 rounded-md ${editingId === strategy.id ? 'bg-[#1F6FEB22]' : 'hover:bg-[#161B22]'}`}><button type="button" onClick={() => setEditingId(strategy.id)} className="min-w-0 flex-1 text-left"><div className="text-xs font-medium text-text truncate">{strategy.name}</div><div className="mt-1 flex items-center gap-1.5 text-[11px] text-text-secondary"><span className={`w-1.5 h-1.5 rounded-full ${strategy.active ? 'bg-up' : 'bg-text-secondary'}`} />{strategy.builtin ? '内置 · ' : ''}{strategy.active ? '启用中' : '已停用'} · {strategy.entry.rules.length} 条买入规则</div></button>{!strategy.builtin && <button type="button" onClick={() => deleteStrategy(strategy)} className="h-6 w-6 hidden group-hover:inline-flex items-center justify-center text-text-secondary hover:text-down rounded-md" title="删除策略" aria-label="删除策略"><Trash2 size={13} /></button>}</div>)}{!strategies.length && <div className="p-4 text-center text-xs text-text-secondary">尚未保存策略。可从右侧模板开始。</div>}</div></aside><section className="border border-border rounded-md p-3 md:p-4"><StrategyBuilder key={currentStrategy?.id || `new-${builderNonce}`} strategy={currentStrategy} templates={templates} rules={rules} sectors={sectors} onSave={saveStrategy} onPreview={previewStrategy} onBacktest={moveToBacktest} saving={saving} /></section></div>}
    {tab === 'signals' && <section className="border border-border rounded-md p-3 md:p-4"><SignalList snapshot={signals} job={scanJob} onRefresh={startScan} onAddToPaper={moveSignalToPaper} history={signalHistory} /></section>}
    {tab === 'fundamental' && <section className="border border-border rounded-md p-3 md:p-4"><FundamentalPanel /></section>}
    {tab === 'overnight' && <OvernightPanel />}
    {tab === 'backtest' && <section className="border border-border rounded-md p-3 md:p-4"><BacktestPanel strategies={strategies} initialStrategyId={initialBacktestStrategy} onResult={handleResult} /></section>}
    {tab === 'paper' && <section className="border border-border rounded-md p-3 md:p-4"><PaperPanel draftSignal={paperDraft} onClearDraft={() => setPaperDraft(null)} /></section>}
  </div>;
}
