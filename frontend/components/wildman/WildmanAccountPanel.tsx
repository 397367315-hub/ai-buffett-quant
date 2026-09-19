'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  CircleHelp,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  ShieldAlert,
  Trash2,
} from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';

export interface WildmanHolding {
  symbol: string;
  name?: string;
  quantity: number;
  sellable_quantity: number;
  market_value?: number | null;
  cost?: number | null;
  as_of?: string;
  source?: string;
  previous_quantity?: number | null;
  bought_today_shares?: number | null;
}

export interface WildmanNavPoint {
  as_of: string;
  nav?: number | null;
  net_asset_value?: number | null;
  unit_nav?: number | null;
  cash_in?: number | null;
  cash_out?: number | null;
  source?: string;
}

export interface WildmanAccount {
  version?: number;
  as_of: string | null;
  source?: string;
  broker_connected?: boolean;
  broker_verified?: boolean;
  verification_status?: string;
  manual_reconciliation_required?: boolean;
  manual_reconciliation_note?: string;
  cash?: number | null;
  net_asset_value?: number | null;
  unit_nav?: number | null;
  holdings: WildmanHolding[];
  holding_snapshots?: Array<{ as_of: string; source?: string; holdings: WildmanHolding[] }>;
  nav_history?: WildmanNavPoint[];
  nav_metrics?: {
    as_of?: string | null;
    current_nav_as_of?: string | null;
    basis?: string | null;
    basis_label?: string;
    current_nav?: number | null;
    peak_nav?: number | null;
    current_drawdown_pct?: number | null;
    max_drawdown_pct?: number | null;
    latest_nav?: number | null;
    eligible_points?: number;
    excluded_points?: Array<{ as_of: string; reason: string }>;
    quality?: string;
    note?: string;
  };
  t1?: {
    status?: string;
    t_allowed?: boolean;
    as_of?: string | null;
    reference_date?: string | null;
    violations?: Array<{ symbol: string; reason?: string; sellable_quantity?: number; yesterday_quantity?: number }>;
    manual_check_required?: boolean;
    note?: string;
  };
  review_metrics?: {
    max_consecutive_losses?: number | null;
    outside_loss_share?: number | null;
    expectancy?: number | null;
    sample_count?: number | null;
  };
  scan_blocked?: boolean;
  target?: {
    symbol: string;
    held: boolean;
    quantity: number;
    sellable_quantity: number;
    t_allowed: boolean;
  };
}

export interface WildmanAccountPanelProps {
  initialAccount?: WildmanAccount | null;
  onUpdated?: (account: WildmanAccount) => void;
  className?: string;
}

interface HoldingDraft {
  id: string;
  symbol: string;
  name: string;
  quantity: string;
  sellable_quantity: string;
  market_value: string;
  cost: string;
  previous_quantity: string;
  bought_today_shares: string;
}

interface NavDraft {
  id: string;
  as_of: string;
  net_asset_value: string;
  unit_nav: string;
  cash_in: string;
  cash_out: string;
}

const MANUAL_NOTE = '未连接券商；保存后仍需手工与券商账户核对。';
let draftSequence = 0;

function nextDraftId(prefix: string): string {
  draftSequence += 1;
  return `${prefix}-${draftSequence}`;
}

function inputValue(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : '';
}

function nullableNumber(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function displayNumber(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? '--' : value.toLocaleString('zh-CN', { maximumFractionDigits: digits });
}

function displayPercent(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '--' : `${value.toFixed(2)}%`;
}

function dateToday(): string {
  // The backend is authoritative for Shanghai date; this only seeds a form.
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date());
}

function holdingDraft(item?: Partial<WildmanHolding> & { id?: string }): HoldingDraft {
  return {
    id: item?.id || nextDraftId('holding'),
    symbol: String(item?.symbol || ''),
    name: String(item?.name || ''),
    quantity: inputValue(item?.quantity),
    sellable_quantity: inputValue(item?.sellable_quantity),
    market_value: inputValue(item?.market_value),
    cost: inputValue(item?.cost),
    previous_quantity: inputValue(item?.previous_quantity),
    bought_today_shares: inputValue(item?.bought_today_shares),
  };
}

function navDraft(item?: WildmanNavPoint): NavDraft {
  return {
    id: nextDraftId('nav'),
    as_of: String(item?.as_of || dateToday()),
    net_asset_value: inputValue(item?.net_asset_value ?? item?.nav),
    unit_nav: inputValue(item?.unit_nav),
    cash_in: inputValue(item?.cash_in),
    cash_out: inputValue(item?.cash_out),
  };
}

function panelState(account: WildmanAccount | null) {
  const referenceDate = account?.t1?.reference_date || '';
  const snapshot = account?.holding_snapshots?.find((item) => item.as_of === referenceDate);
  return {
    asOf: account?.as_of || dateToday(),
    source: 'manual_user_input',
    cash: inputValue(account?.cash),
    netAssetValue: inputValue(account?.net_asset_value),
    unitNav: inputValue(account?.unit_nav),
    holdings: (account?.holdings || []).map(holdingDraft),
    navHistory: (account?.nav_history || []).map(navDraft),
    snapshotDate: referenceDate,
    snapshotHoldings: (snapshot?.holdings || []).map(holdingDraft),
  };
}

function statusLabel(status?: string): string {
  if (status === 'passed') return 'T+1形式校验通过';
  if (status === 'failed') return 'T+1校验未通过';
  if (status === 'stale') return '账户快照已过期';
  return 'T+1待补前一交易日快照';
}

export default function WildmanAccountPanel({ initialAccount = null, onUpdated, className = '' }: WildmanAccountPanelProps) {
  const [account, setAccount] = useState<WildmanAccount | null>(initialAccount);
  const [loading, setLoading] = useState(!initialAccount);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [form, setForm] = useState(() => panelState(initialAccount));

  const applyAccount = useCallback((next: WildmanAccount) => {
    setAccount(next);
    setForm(panelState(next));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch<{ data: WildmanAccount }>('/wildman/account');
      applyAccount(response.data);
    } catch (caught) {
      setError(friendlyApiError(caught, '账户读取失败，请稍后重试'));
    } finally {
      setLoading(false);
    }
  }, [applyAccount]);

  useEffect(() => {
    if (initialAccount) {
      applyAccount(initialAccount);
      setLoading(false);
      return;
    }
    void load();
  }, [applyAccount, initialAccount, load]);

  const updateHolding = (index: number, patch: Partial<HoldingDraft>, snapshot = false) => {
    const key = snapshot ? 'snapshotHoldings' : 'holdings';
    setForm((current) => ({
      ...current,
      [key]: current[key].map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item),
    }));
  };

  const removeHolding = (index: number, snapshot = false) => {
    const key = snapshot ? 'snapshotHoldings' : 'holdings';
    setForm((current) => ({ ...current, [key]: current[key].filter((_, itemIndex) => itemIndex !== index) }));
  };

  const addHolding = (snapshot = false) => {
    const key = snapshot ? 'snapshotHoldings' : 'holdings';
    setForm((current) => ({ ...current, [key]: [...current[key], holdingDraft()] }));
  };

  const save = async () => {
    setSaving(true);
    setError('');
    setMessage('');
    const snapshotDates = account?.holding_snapshots || [];
    const retainedSnapshots = snapshotDates.filter((item) => item.as_of !== form.snapshotDate);
    if (form.snapshotDate && form.snapshotHoldings.length) {
      retainedSnapshots.push({
        as_of: form.snapshotDate,
        source: 'manual_user_input',
        holdings: form.snapshotHoldings.map((item) => ({
          symbol: item.symbol,
          name: item.name,
          quantity: nullableNumber(item.quantity) ?? 0,
          sellable_quantity: nullableNumber(item.sellable_quantity) ?? nullableNumber(item.quantity) ?? 0,
        })),
      });
    }
    const payload = {
      version: 1,
      as_of: form.asOf,
      source: 'manual_user_input',
      cash: nullableNumber(form.cash),
      net_asset_value: nullableNumber(form.netAssetValue),
      unit_nav: nullableNumber(form.unitNav),
      holdings: form.holdings.map((item) => ({
        symbol: item.symbol,
        name: item.name,
        quantity: nullableNumber(item.quantity) ?? 0,
        sellable_quantity: nullableNumber(item.sellable_quantity) ?? 0,
        market_value: nullableNumber(item.market_value),
        cost: nullableNumber(item.cost),
        previous_quantity: nullableNumber(item.previous_quantity),
        bought_today_shares: nullableNumber(item.bought_today_shares),
      })),
      holding_snapshots: retainedSnapshots,
      nav_history: form.navHistory.map((item) => ({
        as_of: item.as_of,
        net_asset_value: nullableNumber(item.net_asset_value),
        unit_nav: nullableNumber(item.unit_nav),
        cash_in: nullableNumber(item.cash_in),
        cash_out: nullableNumber(item.cash_out),
        source: 'manual_user_input',
      })),
      review_metrics: account?.review_metrics || {},
    };
    try {
      const response = await apiFetch<{ data: WildmanAccount }>('/wildman/account', {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      applyAccount(response.data);
      onUpdated?.(response.data);
      setMessage('账户快照已保存。');
    } catch (caught) {
      setError(friendlyApiError(caught, '账户保存失败，请检查日期和数量'));
    } finally {
      setSaving(false);
    }
  };

  const t1 = account?.t1;
  const metrics = account?.nav_metrics;
  const review = account?.review_metrics;
  const hasLossWarning = Number(review?.max_consecutive_losses || 0) > 0;
  const t1Tone = t1?.status === 'passed' ? 'border-up/40 bg-up/10 text-up' : 'border-warn/40 bg-warn/10 text-warn';
  const navHistory = useMemo(() => [...form.navHistory].sort((a, b) => a.as_of.localeCompare(b.as_of)), [form.navHistory]);

  if (loading) {
    return <section className={`border border-border bg-surface ${className}`}><div className="flex min-h-40 items-center justify-center gap-2 text-xs text-text-secondary"><Loader2 size={16} className="animate-spin text-accent" />正在读取个人账户</div></section>;
  }

  return (
    <section className={`min-w-0 border border-border bg-surface ${className}`} aria-label="野人哥个人账户核对">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <ShieldAlert size={16} className="shrink-0 text-accent" />
            <h2 className="text-sm font-semibold text-text">个人账户核对</h2>
          <span className="border border-warn/40 bg-warn/10 px-2 py-0.5 text-[10px] text-warn">账户核对</span>
          </div>
          <p className="mt-1 max-w-2xl text-[10px] leading-4 text-text-secondary">账户风险与已有持仓做T依据。</p>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading || saving} className="inline-flex shrink-0 items-center gap-1.5 border border-border px-2.5 py-1.5 text-[11px] text-text-secondary hover:border-accent hover:text-accent disabled:opacity-50" title="重新读取账户"><RefreshCw size={13} />刷新</button>
      </header>

      <div className="space-y-4 p-4">
        <div className="flex items-start gap-2 border border-warn/35 bg-warn/5 px-3 py-2.5 text-[11px] leading-4 text-warn"><AlertTriangle size={15} className="mt-0.5 shrink-0" /><span>{account?.manual_reconciliation_note || MANUAL_NOTE}</span></div>
        {error && <div role="alert" className="border border-down/30 bg-down/5 px-3 py-2 text-xs text-down">{error}</div>}
        {message && <div className="flex items-center gap-1.5 border border-up/30 bg-up/5 px-3 py-2 text-xs text-up"><Check size={14} />{message}</div>}

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-[10px] text-text-secondary">快照日期<input type="date" value={form.asOf} onChange={(event) => setForm((current) => ({ ...current, asOf: event.target.value }))} className="mt-1 block w-full border border-border bg-bg px-2 py-1.5 text-xs text-text [color-scheme:dark]" /></label>
          <div className="border border-border bg-bg px-3 py-2"><span className="block text-[10px] text-text-secondary">数据来源</span><strong className="mt-1 block text-xs text-text-secondary">用户手工核对</strong></div>
          <div className="border-l-2 border-accent/60 bg-bg pl-3"><span className="block text-[10px] text-text-secondary">账户状态</span><strong className="mt-1 block text-xs text-text">账户快照</strong></div>
          <div className={`border px-3 py-2 text-xs ${t1Tone}`}><span className="block text-[10px]">T+1</span><strong className="mt-1 block">{statusLabel(t1?.status)}</strong><small className="mt-1 block leading-4">{t1?.reference_date ? `参考前一交易日 ${t1.reference_date}` : t1?.t_allowed ? '依据已录入的前一交易日数量与今日新增数量核对。' : '补充前一交易日持仓快照或数量后再核对。'}</small></div>
        </div>

        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          <label className="border border-border bg-bg px-3 py-2 text-[10px] text-text-secondary">现金<input type="number" min="0" step="any" value={form.cash} onChange={(event) => setForm((current) => ({ ...current, cash: event.target.value }))} className="mt-1 block w-full border border-border bg-bg px-2 py-1 text-sm text-text" /></label>
          <label className="border border-border bg-bg px-3 py-2 text-[10px] text-text-secondary">最新净资产值<input type="number" min="0" step="any" value={form.netAssetValue} onChange={(event) => setForm((current) => ({ ...current, netAssetValue: event.target.value }))} className="mt-1 block w-full border border-border bg-bg px-2 py-1 text-sm text-text" /></label>
          <label className="border border-border bg-bg px-3 py-2 text-[10px] text-text-secondary">最新单位净值<input type="number" min="0" step="any" value={form.unitNav} onChange={(event) => setForm((current) => ({ ...current, unitNav: event.target.value }))} className="mt-1 block w-full border border-border bg-bg px-2 py-1 text-sm text-text" /></label>
          <Metric label="净值峰值" value={displayNumber(metrics?.peak_nav)} />
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <Metric label="当前回撤" value={displayPercent(metrics?.current_drawdown_pct)} tone={metrics?.current_drawdown_pct != null && metrics.current_drawdown_pct < 0 ? 'warn' : undefined} />
          <Metric label="最大回撤" value={displayPercent(metrics?.max_drawdown_pct)} tone={metrics?.max_drawdown_pct != null && metrics.max_drawdown_pct < 0 ? 'down' : undefined} />
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-y border-border py-2 text-[10px] text-text-secondary"><span>回撤口径：<b className="text-text">{metrics?.basis_label || '暂无'}</b></span><span>有效点：{metrics?.eligible_points ?? 0}</span><span>净值截至：{metrics?.current_nav_as_of || metrics?.as_of || account?.as_of || '--'}</span>{(metrics?.excluded_points?.length || 0) > 0 && <span className="text-warn">{metrics?.excluded_points?.length} 条含出入金且缺单位净值，未计入回撤</span>}</div>

        <HoldingEditor title="当前持仓与可卖数量" holdings={form.holdings} onAdd={() => addHolding()} onRemove={(index) => removeHolding(index)} onChange={(index, patch) => updateHolding(index, patch)} />

        <details className="group border border-border" open={Boolean(form.snapshotDate)}>
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2.5 text-xs text-text [&::-webkit-details-marker]:hidden"><span className="flex items-center gap-2"><ChevronDown size={14} className="transition-transform group-open:rotate-180" />前一交易日持仓快照</span><span className="text-[10px] text-text-secondary">用于T+1形式校验</span></summary>
          <div className="space-y-3 border-t border-border p-3"><div className="flex flex-wrap items-end gap-2"><label className="text-[10px] text-text-secondary">参考日期<input type="date" value={form.snapshotDate} onChange={(event) => setForm((current) => ({ ...current, snapshotDate: event.target.value }))} className="mt-1 block border border-border bg-bg px-2 py-1.5 text-xs text-text [color-scheme:dark]" /></label><button type="button" onClick={() => addHolding(true)} className="inline-flex items-center gap-1 border border-border px-2 py-1.5 text-[10px] text-text-secondary hover:border-accent hover:text-accent"><Plus size={12} />添加前一交易日持仓</button></div><HoldingEditor compact title="前一交易日持仓" holdings={form.snapshotHoldings} onAdd={() => addHolding(true)} onRemove={(index) => removeHolding(index, true)} onChange={(index, patch) => updateHolding(index, patch, true)} /></div>
        </details>

        <details className="group border border-border" open>
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2.5 text-xs text-text [&::-webkit-details-marker]:hidden"><span className="flex items-center gap-2"><ChevronDown size={14} className="transition-transform group-open:rotate-180" />净值历史</span><span className="text-[10px] text-text-secondary">最多保存最近90日</span></summary>
          <div className="overflow-x-auto border-t border-border"><table className="w-full min-w-[700px] text-left text-[10px]"><thead className="bg-bg text-text-secondary"><tr><th className="px-3 py-2 font-medium">日期</th><th className="px-3 py-2 font-medium">净资产值</th><th className="px-3 py-2 font-medium">单位净值</th><th className="px-3 py-2 font-medium">入金</th><th className="px-3 py-2 font-medium">出金</th><th className="w-12 px-3 py-2" /></tr></thead><tbody>{navHistory.map((item) => <tr key={item.id} className="border-t border-border"><td className="px-3 py-1.5"><input type="date" value={item.as_of} onChange={(event) => setForm((current) => ({ ...current, navHistory: current.navHistory.map((row) => row.id === item.id ? { ...row, as_of: event.target.value } : row) }))} className="border border-border bg-bg px-2 py-1 text-xs text-text [color-scheme:dark]" /></td>{(['net_asset_value', 'unit_nav', 'cash_in', 'cash_out'] as const).map((field) => <td className="px-3 py-1.5" key={field}><input type="number" step="any" value={item[field]} onChange={(event) => setForm((current) => ({ ...current, navHistory: current.navHistory.map((row) => row.id === item.id ? { ...row, [field]: event.target.value } : row) }))} className="w-28 border border-border bg-bg px-2 py-1 text-xs text-text" /></td>)}<td className="px-3 py-1.5"><button type="button" onClick={() => setForm((current) => ({ ...current, navHistory: current.navHistory.filter((row) => row.id !== item.id) }))} className="p-1 text-text-secondary hover:text-down" title="删除净值记录" aria-label="删除净值记录"><Trash2 size={13} /></button></td></tr>)}</tbody></table></div><div className="border-t border-border px-3 py-2"><button type="button" onClick={() => setForm((current) => ({ ...current, navHistory: [...current.navHistory, navDraft()] }))} className="inline-flex items-center gap-1 text-[10px] text-accent hover:underline"><Plus size={12} />添加净值记录</button></div></details>

        {hasLossWarning && <div className="flex items-start gap-2 border border-warn/30 bg-warn/5 px-3 py-2 text-[10px] leading-4 text-warn"><CircleHelp size={14} className="mt-0.5 shrink-0" /><span>复盘记录有 {review?.max_consecutive_losses} 次最大连续亏损，请复核风险纪律。该提醒不会封锁野人哥扫描。</span></div>}
        {metrics?.note && <p className="text-[10px] leading-4 text-text-secondary">{metrics.note}</p>}
        <div className="flex flex-wrap justify-end gap-2"><button type="button" onClick={() => void save()} disabled={saving} className="inline-flex items-center gap-1.5 bg-accent px-3 py-2 text-xs font-medium text-white hover:bg-accent/90 disabled:cursor-wait disabled:opacity-60">{saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}保存账户快照</button></div>
      </div>
    </section>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: 'warn' | 'down' }) {
  const color = tone === 'down' ? 'text-down' : tone === 'warn' ? 'text-warn' : 'text-text';
  return <div className="border border-border bg-bg px-3 py-2"><span className="block text-[10px] text-text-secondary">{label}</span><strong className={`mt-1 block text-sm ${color}`}>{value}</strong></div>;
}

function HoldingEditor({
  title,
  holdings,
  onAdd,
  onRemove,
  onChange,
  compact = false,
}: {
  title: string;
  holdings: HoldingDraft[];
  onAdd: () => void;
  onRemove: (index: number) => void;
  onChange: (index: number, patch: Partial<HoldingDraft>) => void;
  compact?: boolean;
}) {
  return <div className="min-w-0"><div className="mb-2 flex flex-wrap items-center justify-between gap-2"><h3 className="text-xs font-semibold text-text">{title}</h3><button type="button" onClick={onAdd} disabled={holdings.length >= 100} className="inline-flex items-center gap-1 text-[10px] text-accent hover:underline disabled:opacity-40"><Plus size={12} />添加持仓</button></div><div className="overflow-x-auto border border-border"><table className={`w-full text-left text-[10px] ${compact ? 'min-w-[760px]' : 'min-w-[980px]'}`}><thead className="bg-bg text-text-secondary"><tr><th className="px-2 py-2 font-medium">代码</th><th className="px-2 py-2 font-medium">名称</th><th className="px-2 py-2 font-medium">持仓数量</th><th className="px-2 py-2 font-medium">可卖数量</th><th className="px-2 py-2 font-medium">前一交易日持仓</th><th className="px-2 py-2 font-medium">今日新增不可卖</th>{!compact && <><th className="px-2 py-2 font-medium">市值</th><th className="px-2 py-2 font-medium">成本</th></>}<th className="w-10 px-2 py-2" /></tr></thead><tbody>{holdings.map((item, index) => <tr className="border-t border-border" key={item.id}><td className="px-2 py-1.5"><input value={item.symbol} onChange={(event) => onChange(index, { symbol: event.target.value.toUpperCase() })} placeholder="600519.SH" className="w-24 border border-border bg-bg px-2 py-1 text-xs text-text" /></td><td className="px-2 py-1.5"><input value={item.name} onChange={(event) => onChange(index, { name: event.target.value })} placeholder="名称" className="w-24 border border-border bg-bg px-2 py-1 text-xs text-text" /></td><td className="px-2 py-1.5"><input type="number" min="0" step="1" value={item.quantity} onChange={(event) => onChange(index, { quantity: event.target.value })} className="w-24 border border-border bg-bg px-2 py-1 text-xs text-text" /></td><td className="px-2 py-1.5"><input type="number" min="0" step="1" value={item.sellable_quantity} onChange={(event) => onChange(index, { sellable_quantity: event.target.value })} className="w-24 border border-border bg-bg px-2 py-1 text-xs text-text" /></td><td className="px-2 py-1.5"><input type="number" min="0" step="1" value={item.previous_quantity} onChange={(event) => onChange(index, { previous_quantity: event.target.value })} className="w-24 border border-border bg-bg px-2 py-1 text-xs text-text" /></td><td className="px-2 py-1.5"><input type="number" min="0" step="1" value={item.bought_today_shares} onChange={(event) => onChange(index, { bought_today_shares: event.target.value })} className="w-24 border border-border bg-bg px-2 py-1 text-xs text-text" /></td>{!compact && <><td className="px-2 py-1.5"><input type="number" min="0" step="any" value={item.market_value} onChange={(event) => onChange(index, { market_value: event.target.value })} className="w-28 border border-border bg-bg px-2 py-1 text-xs text-text" /></td><td className="px-2 py-1.5"><input type="number" min="0" step="any" value={item.cost} onChange={(event) => onChange(index, { cost: event.target.value })} className="w-24 border border-border bg-bg px-2 py-1 text-xs text-text" /></td></>}<td className="px-2 py-1.5"><button type="button" onClick={() => onRemove(index)} className="p-1 text-text-secondary hover:text-down" title="删除持仓" aria-label="删除持仓"><Trash2 size={13} /></button></td></tr>)}</tbody></table>{!holdings.length && <div className="px-3 py-4 text-center text-[10px] text-text-secondary">暂无记录，添加后可进行T+1核对。</div>}</div></div>;
}
