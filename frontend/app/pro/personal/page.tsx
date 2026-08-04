'use client';

import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  BarChart3,
  Bookmark,
  CheckCircle2,
  CircleDollarSign,
  ClipboardPenLine,
  Edit3,
  Filter,
  History,
  Loader2,
  Plus,
  RefreshCw,
  ShieldAlert,
  Target,
  Trash2,
  TrendingUp,
  X,
} from 'lucide-react';
import { apiFetch, getChangeColor } from '@/lib/api';
import PersonalWorkspaceNav from '@/components/PersonalWorkspaceNav';

type PoolKey = 'core' | 'watchlist' | 'leaders' | 'etf' | 'blacklist';
type SortKey = 'default' | 'change' | 'pnl' | 'code';

interface TechnicalData {
  ma5?: number | null;
  ma20?: number | null;
  ma60?: number | null;
  rsi?: { rsi6?: number | null; rsi14?: number | null };
  macd?: { hist?: number | null };
  volume?: { ratio?: number | null };
  history_points?: number;
}

interface PersonalItem {
  id: number;
  pool: PoolKey;
  pool_label: string;
  code: string;
  name: string;
  display_name: string;
  live_name?: string;
  asset_type: 'stock' | 'etf';
  industry: string;
  sector?: string;
  status: string;
  cost: number | null;
  entry_date: string | null;
  position_pct: number | null;
  stop_loss: number | null;
  targets: number[];
  max_position: number | null;
  thesis: string;
  risk_note: string;
  warning: string;
  etf_type: string;
  price: number | null;
  change_pct: number | null;
  previous_close: number | null;
  turnover: number | null;
  pe: number | null;
  pb: number | null;
  quote_available: boolean;
  name_verified: boolean;
  code_verified: boolean;
  quote_timestamp: string | null;
  pnl_pct: number | null;
  stop_distance_pct: number | null;
  stop_state: 'normal' | 'near' | 'triggered';
  reached_targets: number[];
  next_target: number | null;
  target_distance_pct: number | null;
  technical: TechnicalData;
  source: string;
}

interface PoolData {
  key: PoolKey;
  label: string;
  description: string;
  max_count: number | null;
  count: number;
  items: PersonalItem[];
}

interface OverviewData {
  items: PersonalItem[];
  pools: PoolData[];
  summary: {
    total_items: number;
    holding_count: number;
    watch_count: number;
    etf_count: number;
    total_position_pct: number | null;
    cash_reserve_pct: number | null;
    health_score: number;
  };
  health: {
    score: number;
    level: string;
    holding_count: number;
    total_position_pct: number | null;
    cash_reserve_pct: number | null;
    concentration: Array<{ sector: string; position_pct: number }>;
    checks: Array<{ id: string; label: string; status: string; detail: string }>;
    issues: Array<{ level: string; title: string; detail: string }>;
  };
  alerts: Array<{ level: string; type: string; code: string; name: string; message: string }>;
  quote: {
    available: boolean;
    source: string;
    data_date: string | null;
    source_updated_at: string | null;
    is_realtime: boolean;
    fetched_at: string;
    complete: boolean;
    error?: string | null;
  };
  config: {
    blacklist_reasons?: string[];
    screening_criteria?: string[];
    constitution?: string[];
    disciplines?: string[];
    management_rules?: Record<string, string[] | string>;
  };
  disclaimer: string;
}

interface InvestmentLog {
  id: number;
  action: string;
  code: string | null;
  name: string;
  price: number | null;
  shares: number | null;
  reason: string;
  reflection: string;
  violations: string[];
  created_at: string;
}

interface ItemForm {
  pool: PoolKey;
  code: string;
  name: string;
  asset_type: 'stock' | 'etf';
  industry: string;
  status: string;
  cost: string;
  entry_date: string;
  position_pct: string;
  stop_loss: string;
  targets: string;
  max_position: string;
  thesis: string;
  risk_note: string;
  warning: string;
  etf_type: string;
}

const POOLS: Array<{ key: PoolKey; label: string; icon: typeof Bookmark }> = [
  { key: 'core', label: '核心持仓', icon: Target },
  { key: 'watchlist', label: '长期观察', icon: Bookmark },
  { key: 'leaders', label: '行业龙头', icon: BarChart3 },
  { key: 'etf', label: 'ETF池', icon: CircleDollarSign },
  { key: 'blacklist', label: '黑名单', icon: ShieldAlert },
];

const STATUS_LABELS: Record<string, string> = {
  holding: '持有',
  watching: '观察',
  planned: '计划买入',
  reduce: '待减仓',
  blocked: '拉黑',
  leader: '行业锚点',
  available: '可配置',
};

const ACTION_LABELS: Record<string, string> = {
  buy: '买入',
  sell: '卖出',
  hold: '持有',
  review: '复核',
  move: '调池',
};

function numberText(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? '--' : value.toFixed(digits);
}

function signed(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return '--';
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`;
}

function timeText(value?: string | null): string {
  if (!value) return '--';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function emptyForm(pool: PoolKey = 'watchlist'): ItemForm {
  return {
    pool,
    code: '',
    name: '',
    asset_type: pool === 'etf' ? 'etf' : 'stock',
    industry: '',
    status: pool === 'core' ? 'planned' : pool === 'blacklist' ? 'blocked' : 'watching',
    cost: '',
    entry_date: '',
    position_pct: '',
    stop_loss: '',
    targets: '',
    max_position: '',
    thesis: '',
    risk_note: '',
    warning: '',
    etf_type: '',
  };
}

function itemToForm(item: PersonalItem): ItemForm {
  return {
    pool: item.pool,
    code: item.code,
    name: item.name,
    asset_type: item.asset_type,
    industry: item.industry,
    status: item.status,
    cost: item.cost == null ? '' : String(item.cost),
    entry_date: item.entry_date || '',
    position_pct: item.position_pct == null ? '' : String(item.position_pct),
    stop_loss: item.stop_loss == null ? '' : String(item.stop_loss),
    targets: item.targets.join(', '),
    max_position: item.max_position == null ? '' : String(item.max_position),
    thesis: item.thesis,
    risk_note: item.risk_note,
    warning: item.warning,
    etf_type: item.etf_type,
  };
}

export default function PersonalPage() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [logs, setLogs] = useState<InvestmentLog[]>([]);
  const [activePool, setActivePool] = useState<PoolKey>('core');
  const [query, setQuery] = useState('');
  const [etfType, setEtfType] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('default');
  const [compareCodes, setCompareCodes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [progress, setProgress] = useState(5);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<PersonalItem | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<ItemForm>(emptyForm());
  const [saving, setSaving] = useState(false);
  const [showLogForm, setShowLogForm] = useState(false);
  const [logForm, setLogForm] = useState({ action: 'review', code: '', name: '', price: '', reason: '', reflection: '' });
  const [savingLog, setSavingLog] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setProgress(8);
    setError(null);
    try {
      const [overview, logResponse] = await Promise.allSettled([
        apiFetch<{ data: OverviewData }>('/personal/overview'),
        apiFetch<{ data: { logs: InvestmentLog[] } }>('/personal/logs?limit=20'),
      ]);
      if (overview.status === 'rejected') throw overview.reason;
      setProgress(90);
      setData(overview.value.data);
      if (logResponse.status === 'fulfilled') setLogs(logResponse.value.data.logs || []);
      setProgress(100);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '个人板块加载失败');
    } finally {
      window.setTimeout(() => setLoading(false), 160);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedPool = params.get('pool') as PoolKey | null;
    if (requestedPool && POOLS.some((pool) => pool.key === requestedPool)) {
      setActivePool(requestedPool);
    }
    const requestedCode = params.get('q')?.trim();
    if (requestedCode) setQuery(requestedCode);
  }, []);

  useEffect(() => {
    if (!loading) return undefined;
    const timer = window.setInterval(() => setProgress((value) => Math.min(88, value + 4)), 500);
    return () => window.clearInterval(timer);
  }, [loading]);

  const active = useMemo(() => {
    const pool = data?.pools.find((item) => item.key === activePool);
    const filtered = (pool?.items || []).filter((item) => {
      const text = `${item.display_name} ${item.name} ${item.code} ${item.industry} ${item.etf_type}`.toLowerCase();
      return text.includes(query.trim().toLowerCase()) && (!etfType || item.etf_type === etfType);
    });
    return filtered.sort((a, b) => {
      if (sortKey === 'change') return (b.change_pct || -Infinity) - (a.change_pct || -Infinity);
      if (sortKey === 'pnl') return (b.pnl_pct || -Infinity) - (a.pnl_pct || -Infinity);
      if (sortKey === 'code') return a.code.localeCompare(b.code);
      return a.id - b.id;
    });
  }, [activePool, data, etfType, query, sortKey]);

  const currentPool = data?.pools.find((item) => item.key === activePool);
  const etfItems = data?.pools.find((item) => item.key === 'etf')?.items || [];
  const etfTypes = Array.from(new Set(etfItems.map((item) => item.etf_type).filter(Boolean)));
  const compareItems = etfItems.filter((item) => compareCodes.includes(item.code));

  const toggleCompare = (code: string) => {
    setCompareCodes((current) => current.includes(code)
      ? current.filter((item) => item !== code)
      : current.length < 4 ? [...current, code] : current);
  };

  const refresh = async () => {
    setNotice(null);
    await load();
    setNotice('个人池行情已刷新');
    window.setTimeout(() => setNotice(null), 2600);
  };

  const openAdd = () => {
    setEditing(null);
    setForm(emptyForm(activePool));
    setShowForm(true);
  };

  const openEdit = (item: PersonalItem) => {
    setEditing(item);
    setForm(itemToForm(item));
    setShowForm(true);
  };

  const saveItem = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const numeric = (value: string) => value.trim() === '' ? undefined : Number(value);
    try {
      const payload = {
        ...form,
        targets: form.targets.split(',').map((value) => Number(value.trim())).filter((value) => Number.isFinite(value) && value > 0),
        cost: numeric(form.cost),
        position_pct: numeric(form.position_pct),
        stop_loss: numeric(form.stop_loss),
        max_position: numeric(form.max_position),
      };
      await apiFetch(editing ? `/personal/items/${editing.id}` : '/personal/items', {
        method: editing ? 'PUT' : 'POST',
        body: JSON.stringify(payload),
      });
      setShowForm(false);
      await load();
      setNotice(editing ? '个人池条目已更新' : '已加入个人股票池');
      window.setTimeout(() => setNotice(null), 2600);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const removeItem = async (item: PersonalItem) => {
    if (!window.confirm(`确认将 ${item.display_name}（${item.code}）移出个人池吗？`)) return;
    try {
      await apiFetch(`/personal/items/${item.id}`, { method: 'DELETE' });
      await load();
      setNotice('已移出个人股票池');
      window.setTimeout(() => setNotice(null), 2600);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '移出失败');
    }
  };

  const moveToWatchlist = async (item: PersonalItem) => {
    try {
      await apiFetch(`/personal/items/${item.id}/move-to-watchlist`, { method: 'POST' });
      await load();
      setActivePool('watchlist');
      setNotice(`${item.display_name} 已移入长期观察池`);
      window.setTimeout(() => setNotice(null), 2600);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '移入观察池失败');
    }
  };

  const saveLog = async (event: FormEvent) => {
    event.preventDefault();
    if (!logForm.reason.trim()) return;
    setSavingLog(true);
    try {
      await apiFetch('/personal/logs', {
        method: 'POST',
        body: JSON.stringify({
          ...logForm,
          price: logForm.price.trim() ? Number(logForm.price) : undefined,
        }),
      });
      setLogForm({ action: 'review', code: '', name: '', price: '', reason: '', reflection: '' });
      setShowLogForm(false);
      const response = await apiFetch<{ data: { logs: InvestmentLog[] } }>('/personal/logs?limit=20');
      setLogs(response.data.logs || []);
      setNotice('投资日志已记录');
      window.setTimeout(() => setNotice(null), 2600);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '日志保存失败');
    } finally {
      setSavingLog(false);
    }
  };

  if (loading && !data) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-20 text-center text-text-secondary">
        <Loader2 size={30} className="animate-spin text-accent mx-auto mb-4" />
        <div className="text-sm text-text">正在读取个人投资工作台</div>
        <div className="text-xs mt-2">校验股票代码、拉取实时行情和历史技术指标</div>
        <div className="max-w-sm mx-auto mt-5 h-1.5 bg-[#21262D] rounded-full overflow-hidden"><div className="h-full bg-accent transition-all" style={{ width: `${progress}%` }} /></div>
        <div className="mt-2 text-xs font-mono">{progress}%</div>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-5 md:py-6">
      <PersonalWorkspaceNav />
      <header className="flex flex-wrap items-start justify-between gap-4 mb-5">
        <div>
          <h1 className="text-xl md:text-2xl font-bold text-text flex items-center gap-2"><Bookmark size={22} className="text-accent" />个人投资工作台</h1>
          <p className="text-xs md:text-sm text-text-secondary mt-1">股票池、持仓纪律、实时风险和投资复盘集中管理</p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => setShowLogForm(true)} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs border border-border rounded-md text-text-secondary hover:border-accent hover:text-accent"><ClipboardPenLine size={14} />记录决策</button>
          <button type="button" onClick={refresh} disabled={loading} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs bg-accent text-white rounded-md hover:brightness-110 disabled:opacity-50"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} />刷新行情</button>
        </div>
      </header>

      {error && <div className="mb-4 border border-up/50 bg-[#EF535018] rounded-md p-3 text-xs text-up flex items-start gap-2"><AlertTriangle size={15} className="shrink-0" />{error}</div>}
      {notice && <div className="mb-4 border border-down/50 bg-[#26A69A18] rounded-md p-3 text-xs text-down flex items-center gap-2"><CheckCircle2 size={15} />{notice}</div>}

      {data && <>
        <section className="border border-border rounded-md grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 divide-x divide-y sm:divide-y-0 divide-border mb-4">
          <div className="p-3"><div className="text-[11px] text-text-secondary">池内标的</div><div className="mt-1 font-mono text-lg text-text">{data.summary.total_items}</div></div>
          <div className="p-3"><div className="text-[11px] text-text-secondary">记录持仓</div><div className="mt-1 font-mono text-lg text-text">{data.summary.holding_count}</div></div>
          <div className="p-3"><div className="text-[11px] text-text-secondary">记录仓位</div><div className="mt-1 font-mono text-lg text-text">{numberText(data.summary.total_position_pct, 1)}%</div></div>
          <div className="p-3"><div className="text-[11px] text-text-secondary">现金安全垫</div><div className={`mt-1 font-mono text-lg ${(data.summary.cash_reserve_pct ?? 100) >= 20 ? 'text-down' : 'text-warn'}`}>{numberText(data.summary.cash_reserve_pct, 1)}%</div></div>
          <div className="p-3"><div className="text-[11px] text-text-secondary">健康度</div><div className={`mt-1 font-mono text-lg ${data.summary.health_score >= 85 ? 'text-down' : data.summary.health_score >= 65 ? 'text-warn' : 'text-up'}`}>{data.summary.health_score}</div></div>
          <div className="p-3 col-span-2 sm:col-span-1"><div className="text-[11px] text-text-secondary">行情状态</div><div className={`mt-1 text-xs ${data.quote.is_realtime ? 'text-down' : 'text-text-secondary'}`}>{data.quote.is_realtime ? '盘中实时' : '最近可验证行情'}</div></div>
          <div className="p-3 col-span-2 sm:col-span-1"><div className="text-[11px] text-text-secondary">行情日期</div><div className="mt-1 text-xs font-mono text-text">{data.quote.data_date || '--'}</div></div>
        </section>

        <section className="border border-border rounded-md px-3 py-2.5 mb-5 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-text-secondary">
          <span className="inline-flex items-center gap-1"><CircleDollarSign size={13} className="text-accent" />来源：{data.quote.source === 'eastmoney' ? '东方财富' : data.quote.source}</span>
          <span>源端更新时间：{timeText(data.quote.source_updated_at)}</span>
          <span>拉取：{timeText(data.quote.fetched_at)}</span>
          <span className={data.quote.complete ? 'text-down' : 'text-warn'}>{data.quote.complete ? '全部代码已返回' : '部分代码未返回，未据此生成结论'}</span>
          <span className="basis-full sm:basis-auto sm:ml-auto text-[11px]">{data.disclaimer}</span>
        </section>

        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_300px] gap-5">
          <main className="min-w-0">
            <div className="border border-border rounded-md overflow-x-auto mb-4">
              <div className="flex min-w-[650px]">
                {POOLS.map(({ key, label, icon: Icon }) => {
                  const pool = data.pools.find((item) => item.key === key);
                  return <button key={key} type="button" onClick={() => { setActivePool(key); setEtfType(''); }} className={`flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-3 text-xs whitespace-nowrap border-r last:border-r-0 border-border ${activePool === key ? 'bg-[#1F6FEB22] text-accent font-semibold' : 'text-text-secondary hover:bg-[#161B22] hover:text-text'}`}><Icon size={14} />{label}<span className="font-mono opacity-70">{pool?.count || 0}</span></button>;
                })}
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
              <div><h2 className="text-base font-semibold text-text">{currentPool?.label || '个人池'}</h2><p className="text-xs text-text-secondary mt-1">{currentPool?.description}</p></div>
              <div className="flex flex-wrap items-center gap-2">
                <label className="relative"><Filter size={13} className="absolute left-2.5 top-2.5 text-text-secondary" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索名称 / 代码" className="w-36 bg-bg border border-border rounded-md pl-8 pr-2 py-2 text-xs text-text focus:outline-none focus:border-accent" /></label>
                {activePool === 'etf' && <select value={etfType} onChange={(event) => setEtfType(event.target.value)} className="bg-bg border border-border rounded-md px-2 py-2 text-xs text-text"><option value="">全部类型</option>{etfTypes.map((type) => <option key={type} value={type}>{type}</option>)}</select>}
                <select value={sortKey} onChange={(event) => setSortKey(event.target.value as SortKey)} className="bg-bg border border-border rounded-md px-2 py-2 text-xs text-text"><option value="default">默认排序</option><option value="change">按涨跌幅</option><option value="pnl">按浮盈亏</option><option value="code">按代码</option></select>
                <button type="button" onClick={openAdd} className="inline-flex items-center gap-1.5 px-2.5 py-2 bg-accent text-white rounded-md text-xs hover:brightness-110"><Plus size={14} />新增</button>
              </div>
            </div>

            {activePool === 'blacklist' && <section className="border border-up/40 bg-[#EF53500D] rounded-md p-4 mb-4"><h3 className="text-sm font-semibold text-up flex items-center gap-2"><ShieldAlert size={16} />黑名单规则</h3><div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2">{(data.config.blacklist_reasons || []).map((reason) => <div key={reason} className="text-xs text-text-secondary border-l-2 border-up/60 pl-2">{reason}</div>)}</div><p className="text-[11px] text-text-secondary mt-3">黑名单是纪律过滤器，不替代对具体公司的事实核验。具体标的可新增后用“移入观察池”恢复跟踪。</p></section>}

            {activePool === 'etf' && compareItems.length >= 2 && <section className="border border-accent/50 rounded-md p-3 mb-4 overflow-x-auto"><div className="flex items-center justify-between gap-3 mb-3"><div><h3 className="text-sm font-semibold text-text">ETF快速对比</h3><p className="text-[11px] text-text-secondary mt-1">最多选择4只，比较实时价格、波动和技术状态</p></div><button type="button" onClick={() => setCompareCodes([])} className="text-xs text-text-secondary hover:text-text">清空选择</button></div><table className="w-full min-w-[560px] text-xs"><thead className="text-text-secondary border-b border-border"><tr><th className="text-left py-2">ETF</th><th className="text-right py-2">现价</th><th className="text-right py-2">涨跌幅</th><th className="text-right py-2">换手率</th><th className="text-right py-2">RSI6</th><th className="text-right py-2">量比</th></tr></thead><tbody>{compareItems.map((item) => <tr key={item.code} className="border-b border-border/50"><td className="py-2 text-text">{item.display_name}<span className="text-text-secondary font-mono ml-2">{item.code}</span></td><td className="py-2 text-right font-mono">{item.price == null ? '--' : numberText(item.price)}</td><td className={`py-2 text-right font-mono ${getChangeColor(item.change_pct || 0)}`}>{signed(item.change_pct)}</td><td className="py-2 text-right font-mono text-text-secondary">{numberText(item.turnover)}</td><td className="py-2 text-right font-mono text-text-secondary">{numberText(item.technical?.rsi?.rsi6, 1)}</td><td className="py-2 text-right font-mono text-text-secondary">{numberText(item.technical?.volume?.ratio)}</td></tr>)}</tbody></table></section>}
            {active.length === 0 ? <div className="border border-border rounded-md py-16 text-center text-sm text-text-secondary"><Bookmark size={24} className="mx-auto mb-2 text-border" />{query || etfType ? '没有匹配的标的' : '这个池暂时为空'}<div className="text-xs mt-2">可以从智能选股、量化信号或技术筛选直接加入。</div></div> : <div className="grid grid-cols-1 md:grid-cols-2 gap-3">{active.map((item) => <article key={item.id} className="border border-border rounded-md bg-card p-4 hover:border-accent/60 transition-colors">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0"><div className="flex items-center gap-2"><h3 className="font-semibold text-text truncate">{item.display_name}</h3><span className={`text-[11px] border rounded px-1.5 py-0.5 ${item.status === 'holding' ? 'border-down/50 text-down' : item.status === 'reduce' ? 'border-up/50 text-up' : 'border-border text-text-secondary'}`}>{STATUS_LABELS[item.status] || item.status}</span></div><div className="text-xs text-text-secondary mt-1 font-mono">{item.code}<span className="font-sans ml-2">{item.industry || item.sector || '行业未标注'}</span>{item.asset_type === 'etf' && item.etf_type ? <span className="ml-2 text-accent">{item.etf_type}</span> : null}</div></div>
                <div className="text-right shrink-0"><div className="flex items-center justify-end gap-2">{item.asset_type === 'etf' && <input type="checkbox" checked={compareCodes.includes(item.code)} onChange={() => toggleCompare(item.code)} className="accent-[#58A6FF]" title="加入ETF对比" aria-label={`将${item.display_name}加入ETF对比`} />}<span className="font-mono text-lg text-text">{item.price == null ? '--' : `¥${numberText(item.price)}`}</span></div><div className={`font-mono text-xs ${getChangeColor(item.change_pct || 0)}`}>{signed(item.change_pct)}</div></div>
              </div>
              <div className="grid grid-cols-3 gap-2 border-y border-border/70 py-3 mt-3 text-xs"><div><div className="text-text-secondary">成本 / 浮盈亏</div><div className="font-mono text-text mt-1">{item.cost == null ? '--' : `¥${numberText(item.cost)}`} <span className={item.pnl_pct != null && item.pnl_pct >= 0 ? 'text-up' : 'text-down'}>{signed(item.pnl_pct)}</span></div></div><div><div className="text-text-secondary">止损</div><div className={`font-mono mt-1 ${item.stop_state === 'triggered' ? 'text-up' : item.stop_state === 'near' ? 'text-warn' : 'text-text'}`}>{item.stop_loss == null ? '--' : `¥${numberText(item.stop_loss)}`}</div></div><div><div className="text-text-secondary">下一目标</div><div className="font-mono text-text mt-1">{item.next_target == null ? '--' : `¥${numberText(item.next_target)}`}</div></div></div>
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-text-secondary mt-3"><span>RSI6 {numberText(item.technical?.rsi?.rsi6, 1)}</span><span>MA20 {numberText(item.technical?.ma20)}</span><span>MACD {item.technical?.macd?.hist == null ? '--' : item.technical.macd.hist >= 0 ? '偏强' : '偏弱'}</span><span>量比 {numberText(item.technical?.volume?.ratio, 2)}</span></div>
              {item.thesis && <p className="text-xs text-text-secondary leading-5 mt-3 line-clamp-2">逻辑：{item.thesis}</p>}
              {item.warning && <p className="text-xs text-warn leading-5 mt-2 flex items-start gap-1"><AlertTriangle size={13} className="shrink-0 mt-0.5" />{item.warning}</p>}
              <div className="flex items-center justify-between gap-2 mt-4 pt-3 border-t border-border/70"><span className={`text-[11px] ${item.quote_available ? item.name_verified ? 'text-down' : 'text-warn' : 'text-text-secondary'}`}>{item.quote_available ? item.name_verified ? '代码/名称已由行情源核验' : '代码有效，名称待核验' : '暂无可验证现价'}</span><div className="flex items-center gap-1"><button type="button" onClick={() => openEdit(item)} className="p-1.5 text-text-secondary hover:text-accent rounded-md" title="编辑条目" aria-label={`编辑${item.display_name}`}><Edit3 size={14} /></button>{item.pool === 'blacklist' && <button type="button" onClick={() => moveToWatchlist(item)} className="p-1.5 text-text-secondary hover:text-down rounded-md" title="移入长期观察池" aria-label={`将${item.display_name}移入观察池`}><TrendingUp size={14} /></button>}<button type="button" onClick={() => removeItem(item)} className="p-1.5 text-text-secondary hover:text-up rounded-md" title="移出个人池" aria-label={`移出${item.display_name}`}><Trash2 size={14} /></button></div></div>
            </article>)}</div>}
          </main>

          <aside className="space-y-4">
            <section className="border border-border rounded-md p-4"><h2 className="text-sm font-semibold text-text flex items-center gap-2"><ShieldAlert size={16} className="text-warn" />池健康度 <span className="ml-auto font-mono text-accent">{data.health.score}</span></h2><div className="mt-3 h-1.5 bg-[#21262D] rounded-full overflow-hidden"><div className={`${data.health.score >= 85 ? 'bg-down' : data.health.score >= 65 ? 'bg-warn' : 'bg-up'} h-full`} style={{ width: `${data.health.score}%` }} /></div><div className="text-xs text-text-secondary mt-2">{data.health.level} · 只对记录完整度和集中度评分</div><div className="mt-4 space-y-2">{data.health.checks.map((check) => <div key={check.id} className="flex items-start gap-2 text-xs"><CheckCircle2 size={13} className={check.status === 'ok' ? 'text-down shrink-0' : check.status === 'danger' ? 'text-up shrink-0' : 'text-warn shrink-0'} /><span className="text-text">{check.label}<span className="text-text-secondary ml-1">{check.detail}</span></span></div>)}</div>{data.health.concentration.length > 0 && <div className="mt-4 pt-3 border-t border-border"><div className="text-xs text-text-secondary mb-2">记录仓位行业分布</div>{data.health.concentration.slice(0, 4).map((item) => <div key={item.sector} className="flex items-center justify-between text-xs py-1"><span className="text-text-secondary truncate mr-2">{item.sector}</span><span className="font-mono text-text">{item.position_pct.toFixed(1)}%</span></div>)}</div>}</section>
            <section className="border border-border rounded-md p-4"><h2 className="text-sm font-semibold text-text flex items-center gap-2"><AlertTriangle size={16} className="text-warn" />持仓与池内提醒 <span className="ml-auto font-mono text-warn">{data.alerts.length}</span></h2>{data.alerts.length ? <div className="mt-3 space-y-2 max-h-80 overflow-y-auto">{data.alerts.slice(0, 12).map((alert, index) => <div key={`${alert.code}-${alert.type}-${index}`} className={`border-l-2 pl-2.5 text-xs ${alert.level === 'danger' ? 'border-up' : alert.level === 'warning' ? 'border-warn' : 'border-accent'}`}><div className="text-text">{alert.name} <span className="font-mono text-text-secondary">{alert.code}</span></div><div className="text-text-secondary mt-0.5 leading-5">{alert.message}</div></div>)}</div> : <div className="mt-3 text-xs text-text-secondary">暂无触发提醒。</div>}</section>
            <section className="border border-border rounded-md p-4"><div className="flex items-center justify-between gap-2"><h2 className="text-sm font-semibold text-text flex items-center gap-2"><History size={16} className="text-accent" />最近决策日志</h2><button type="button" onClick={() => setShowLogForm(true)} className="p-1 text-text-secondary hover:text-accent" title="记录决策" aria-label="记录决策"><Plus size={15} /></button></div>{logs.length ? <div className="mt-3 space-y-3">{logs.slice(0, 5).map((log) => <div key={log.id} className="border-t border-border pt-2.5"><div className="flex items-center justify-between text-xs"><span className="text-text">{ACTION_LABELS[log.action] || log.action} · {log.name || log.code || '系统复核'}</span><span className="text-text-secondary">{timeText(log.created_at)}</span></div><div className="text-xs text-text-secondary mt-1 leading-5 line-clamp-2">{log.reason}</div></div>)}</div> : <div className="mt-3 text-xs text-text-secondary">还没有决策记录。</div>}</section>
            <section className="border-t border-border pt-4"><h2 className="text-xs font-semibold text-text-secondary">投资筛选五问</h2><ol className="mt-2 space-y-1.5 text-xs text-text-secondary list-decimal list-inside">{(data.config.screening_criteria || []).map((item) => <li key={item}>{item}</li>)}</ol></section>
            <details className="border-t border-border pt-4"><summary className="cursor-pointer text-xs font-semibold text-text-secondary">展开投资宪法与纪律</summary><div className="mt-3 space-y-3"><div><div className="text-[11px] text-text-secondary mb-1.5">十条宪法</div><ol className="space-y-1 text-[11px] text-text-secondary list-decimal list-inside">{(data.config.constitution || []).map((item) => <li key={item}>{item}</li>)}</ol></div><div><div className="text-[11px] text-text-secondary mb-1.5">五条纪律</div><ol className="space-y-1 text-[11px] text-text-secondary list-decimal list-inside">{(data.config.disciplines || []).map((item) => <li key={item}>{item}</li>)}</ol></div></div></details>
          </aside>
        </div>
      </>}

      {showForm && <div className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4"><form onSubmit={saveItem} className="w-full max-w-2xl max-h-[92vh] overflow-y-auto bg-card border border-border rounded-md p-5"><div className="flex items-center justify-between mb-4"><h2 className="text-base font-semibold text-text">{editing ? '编辑个人池条目' : '新增个人池条目'}</h2><button type="button" onClick={() => setShowForm(false)} className="p-1 text-text-secondary hover:text-text" title="关闭" aria-label="关闭"><X size={18} /></button></div><div className="grid grid-cols-1 sm:grid-cols-2 gap-3"><label className="text-xs text-text-secondary">股票池<select value={form.pool} onChange={(event) => setForm({ ...form, pool: event.target.value as PoolKey, asset_type: event.target.value === 'etf' ? 'etf' : form.asset_type })} className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text"><option value="core">核心持仓池</option><option value="watchlist">长期观察池</option><option value="leaders">行业龙头池</option><option value="etf">ETF池</option><option value="blacklist">黑名单</option></select></label><label className="text-xs text-text-secondary">资产类型<select value={form.asset_type} onChange={(event) => setForm({ ...form, asset_type: event.target.value as 'stock' | 'etf' })} className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text"><option value="stock">股票</option><option value="etf">ETF</option></select></label><label className="text-xs text-text-secondary">证券代码 *<input required value={form.code} onChange={(event) => setForm({ ...form, code: event.target.value })} placeholder="如 600519 / 510300" className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text font-mono" /></label><label className="text-xs text-text-secondary">名称 *<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text" /></label><label className="text-xs text-text-secondary">行业 / 方向<input value={form.industry} onChange={(event) => setForm({ ...form, industry: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text" /></label><label className="text-xs text-text-secondary">状态<select value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text"><option value="holding">持有</option><option value="watching">观察</option><option value="planned">计划买入</option><option value="reduce">待减仓</option><option value="blocked">拉黑</option><option value="leader">行业锚点</option></select></label><label className="text-xs text-text-secondary">成本价<input type="number" step="0.0001" value={form.cost} onChange={(event) => setForm({ ...form, cost: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text font-mono" /></label><label className="text-xs text-text-secondary">买入日期<input type="date" value={form.entry_date} onChange={(event) => setForm({ ...form, entry_date: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text" /></label><label className="text-xs text-text-secondary">记录仓位 %<input type="number" min="0" max="100" step="0.1" value={form.position_pct} onChange={(event) => setForm({ ...form, position_pct: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text font-mono" /></label><label className="text-xs text-text-secondary">最大仓位 %<input type="number" min="0" max="100" step="0.1" value={form.max_position} onChange={(event) => setForm({ ...form, max_position: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text font-mono" /></label><label className="text-xs text-text-secondary">止损价<input type="number" min="0" step="0.0001" value={form.stop_loss} onChange={(event) => setForm({ ...form, stop_loss: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text font-mono" /></label><label className="text-xs text-text-secondary">目标价（逗号分隔）<input value={form.targets} onChange={(event) => setForm({ ...form, targets: event.target.value })} placeholder="如 13.5, 16" className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text font-mono" /></label><label className="text-xs text-text-secondary sm:col-span-2">ETF分类<input value={form.etf_type} onChange={(event) => setForm({ ...form, etf_type: event.target.value })} placeholder="宽基 / 行业 / 主题 / 商品 / 跨境" className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text" /></label><label className="text-xs text-text-secondary sm:col-span-2">投资逻辑 / 研究依据<textarea rows={3} value={form.thesis} onChange={(event) => setForm({ ...form, thesis: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text resize-y" /></label><label className="text-xs text-text-secondary sm:col-span-2">风险提示<textarea rows={2} value={form.risk_note} onChange={(event) => setForm({ ...form, risk_note: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text resize-y" /></label><label className="text-xs text-text-secondary sm:col-span-2">复核提醒<textarea rows={2} value={form.warning} onChange={(event) => setForm({ ...form, warning: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text resize-y" /></label></div><div className="flex justify-end gap-2 mt-5 pt-4 border-t border-border"><button type="button" onClick={() => setShowForm(false)} className="px-3 py-2 text-xs border border-border rounded-md text-text-secondary hover:text-text">取消</button><button type="submit" disabled={saving} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs bg-accent text-white rounded-md disabled:opacity-50"><SaveIcon saving={saving} />{saving ? '保存中' : '保存条目'}</button></div></form></div>}

      {showLogForm && <div className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4"><form onSubmit={saveLog} className="w-full max-w-lg bg-card border border-border rounded-md p-5"><div className="flex items-center justify-between mb-4"><h2 className="text-base font-semibold text-text">记录投资决策</h2><button type="button" onClick={() => setShowLogForm(false)} className="p-1 text-text-secondary hover:text-text" title="关闭" aria-label="关闭"><X size={18} /></button></div><div className="grid grid-cols-2 gap-3"><label className="text-xs text-text-secondary">动作<select value={logForm.action} onChange={(event) => setLogForm({ ...logForm, action: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-2 text-sm text-text">{Object.entries(ACTION_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label className="text-xs text-text-secondary">代码<input value={logForm.code} onChange={(event) => setLogForm({ ...logForm, code: event.target.value })} placeholder="可不填" className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-2 text-sm text-text font-mono" /></label><label className="text-xs text-text-secondary">标的名称<input value={logForm.name} onChange={(event) => setLogForm({ ...logForm, name: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-2 text-sm text-text" /></label><label className="text-xs text-text-secondary">价格<input type="number" step="0.0001" value={logForm.price} onChange={(event) => setLogForm({ ...logForm, price: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-2 text-sm text-text font-mono" /></label><label className="text-xs text-text-secondary col-span-2">理由 *<textarea required rows={3} value={logForm.reason} onChange={(event) => setLogForm({ ...logForm, reason: event.target.value })} placeholder="这次决策基于什么事实和假设？" className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-2 text-sm text-text resize-y" /></label><label className="text-xs text-text-secondary col-span-2">复盘 / 反思<textarea rows={2} value={logForm.reflection} onChange={(event) => setLogForm({ ...logForm, reflection: event.target.value })} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-2 text-sm text-text resize-y" /></label></div><div className="flex justify-end gap-2 mt-5 pt-4 border-t border-border"><button type="button" onClick={() => setShowLogForm(false)} className="px-3 py-2 text-xs border border-border rounded-md text-text-secondary">取消</button><button type="submit" disabled={savingLog} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs bg-accent text-white rounded-md disabled:opacity-50"><ClipboardPenLine size={14} />{savingLog ? '记录中' : '保存日志'}</button></div></form></div>}
    </div>
  );
}

function SaveIcon({ saving }: { saving: boolean }) {
  return saving ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />;
}
