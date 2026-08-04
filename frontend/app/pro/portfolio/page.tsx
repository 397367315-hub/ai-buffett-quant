'use client';

import { FormEvent, useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Loader2, PieChart, RefreshCw, Settings2, ShieldAlert, X } from 'lucide-react';
import PersonalWorkspaceNav from '@/components/PersonalWorkspaceNav';
import { apiFetch } from '@/lib/api';

interface AllocationData {
  account: { total_assets: number | null; equity_value: number | null; cash_value: number | null; equity_pct: number; cash_pct: number; configured: boolean };
  limits: Record<string, number | string | null>;
  positions: Array<{ code: string; name: string; industry: string; weight_pct: number; market_value: number | null; price: number | null; pnl_pct: number | null; add_allowed: boolean; add_block_reason: string | null }>;
  industries: Array<{ industry: string; weight_pct: number; status: string }>;
  checks: Array<{ rule: string; code?: string; label: string; value: number; limit: number; status: string; detail: string }>;
  daily_additions: Array<{ code: string; amount: number; pct: number | null; status: string }>;
  risk_metrics: { data_points: number; max_drawdown_pct: number | null; volatility_pct: number | null; sharpe: number | null };
  new_buy_blocked: boolean;
  advice: string[];
  quote: { data_date: string | null; is_realtime: boolean; cache_used?: boolean; complete: boolean };
  methodology: string;
}

const money = (value: number | null | undefined) => value == null ? '--' : `¥${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const number = (value: number | null | undefined, digits = 2) => value == null ? '--' : value.toFixed(digits);
const signed = (value: number | null | undefined) => value == null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;

export default function PortfolioPage() {
  const [data, setData] = useState<AllocationData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch<{ data: AllocationData }>('/personal/allocation');
      setData(response.data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '仓位数据加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const openSettings = () => {
    const limits = data?.limits || {};
    setForm(Object.fromEntries(['total_assets', 'equity_ceiling_pct', 'cash_floor_pct', 'single_stock_limit_pct', 'sector_limit_pct', 'daily_add_limit_pct', 'loss_add_block_pct'].map((key) => [key, limits[key] == null ? '' : String(limits[key])])));
    setSettingsOpen(true);
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const payload = Object.fromEntries(Object.entries(form).map(([key, value]) => [key, value.trim() === '' ? null : Number(value)]));
      await apiFetch('/personal/allocation/config', { method: 'PUT', body: JSON.stringify(payload) });
      setSettingsOpen(false);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '仓位规则保存失败');
    } finally {
      setSaving(false);
    }
  };

  return <div className="max-w-7xl mx-auto px-4 py-5 md:py-6">
    <PersonalWorkspaceNav />
    <header className="flex flex-wrap items-start justify-between gap-4 mb-5"><div><h1 className="text-xl md:text-2xl font-bold text-text flex items-center gap-2"><PieChart size={22} className="text-accent" />仓位管理</h1><p className="text-xs text-text-secondary mt-1">账户资产、行业集中度与纪律阈值</p></div><div className="flex gap-2"><button type="button" onClick={load} disabled={loading} className="p-2 border border-border rounded-md text-text-secondary hover:text-accent" title="刷新" aria-label="刷新仓位"><RefreshCw size={15} className={loading ? 'animate-spin' : ''} /></button><button type="button" onClick={openSettings} className="inline-flex items-center gap-1.5 px-3 py-2 bg-accent text-white rounded-md text-xs"><Settings2 size={14} />账户与阈值</button></div></header>
    {error && <div className="mb-4 border border-up/50 bg-[#EF535014] rounded-md p-3 text-xs text-up flex gap-2"><AlertTriangle size={15} />{error}</div>}
    {loading && !data ? <Loading /> : data && <>
      <section className={`border rounded-md p-3 mb-4 flex items-start gap-2 text-xs ${data.new_buy_blocked ? 'border-up/50 bg-[#EF535010] text-up' : 'border-down/40 bg-[#26A69A0D] text-down'}`}>{data.new_buy_blocked ? <ShieldAlert size={15} className="shrink-0" /> : <CheckCircle2 size={15} className="shrink-0" />}<div>{data.new_buy_blocked ? '当前股票总仓位或现金安全垫触发新买入限制。' : '当前总仓位和现金安全垫未触发禁止新买入规则。'}<span className="text-text-secondary ml-2">行情日期 {data.quote.data_date || '--'} · {data.quote.is_realtime ? '盘中实时' : data.quote.cache_used ? '最近交易日缓存' : '非实时快照'}</span></div></section>

      <section className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 border border-border rounded-md divide-x divide-y lg:divide-y-0 divide-border mb-5">
        <Metric label="总资产" value={money(data.account.total_assets)} />
        <Metric label="股票资产" value={money(data.account.equity_value)} />
        <Metric label="现金" value={money(data.account.cash_value)} />
        <Metric label="股票仓位" value={`${number(data.account.equity_pct, 1)}%`} className={data.account.equity_pct > Number(data.limits.equity_ceiling_pct) ? 'text-up' : ''} />
        <Metric label="现金比例" value={`${number(data.account.cash_pct, 1)}%`} className={data.account.cash_pct < Number(data.limits.cash_floor_pct) ? 'text-up' : ''} />
        <Metric label="最大回撤" value={signed(data.risk_metrics.max_drawdown_pct)} />
        <Metric label="夏普 / 波动率" value={`${number(data.risk_metrics.sharpe)} / ${number(data.risk_metrics.volatility_pct)}%`} />
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)_minmax(0,1fr)] gap-5 mb-6">
        <section className="border border-border rounded-md p-4"><h2 className="text-sm font-semibold text-text">资产配置</h2><div className="mx-auto mt-5 w-40 h-40 rounded-full relative" style={{ background: `conic-gradient(#EF5350 0 ${Math.max(0, Math.min(100, data.account.equity_pct))}%, #26A69A ${Math.max(0, Math.min(100, data.account.equity_pct))}% 100%)` }}><div className="absolute inset-5 bg-card rounded-full flex items-center justify-center text-center"><div><div className="text-xs text-text-secondary">股票仓位</div><div className="text-xl font-mono text-text mt-1">{number(data.account.equity_pct, 1)}%</div></div></div></div><div className="flex justify-center gap-4 mt-5 text-xs"><span className="flex items-center gap-1.5 text-text-secondary"><i className="w-2.5 h-2.5 bg-up inline-block" />股票</span><span className="flex items-center gap-1.5 text-text-secondary"><i className="w-2.5 h-2.5 bg-down inline-block" />现金</span></div></section>
        <section className="border border-border rounded-md p-4"><h2 className="text-sm font-semibold text-text">行业分布</h2>{data.industries.length ? <div className="space-y-3 mt-4">{data.industries.map((item) => <div key={item.industry}><div className="flex justify-between text-xs"><span className="text-text truncate mr-3">{item.industry}</span><span className={item.status === 'danger' ? 'text-up font-mono' : 'text-text-secondary font-mono'}>{number(item.weight_pct, 1)}%</span></div><div className="h-1.5 bg-[#21262D] mt-1.5 overflow-hidden rounded"><div className={`h-full ${item.status === 'danger' ? 'bg-up' : 'bg-accent'}`} style={{ width: `${Math.min(item.weight_pct / Math.max(Number(data.limits.sector_limit_pct), 1) * 100, 100)}%` }} /></div></div>)}</div> : <Empty text="尚无记录仓位的持仓" />}</section>
        <section className="border border-border rounded-md p-4"><h2 className="text-sm font-semibold text-text">风控状态</h2><div className="mt-3 space-y-2 max-h-64 overflow-y-auto">{data.checks.map((check, index) => <div key={`${check.rule}-${check.code || index}`} className="flex items-start gap-2 text-xs"><span className={`mt-1 w-1.5 h-1.5 rounded-full shrink-0 ${check.status === 'danger' ? 'bg-up' : 'bg-down'}`} /><div><div className="text-text">{check.label}</div><div className="text-text-secondary mt-0.5">{check.detail}</div></div></div>)}</div></section>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_320px] gap-5">
        <section className="border border-border rounded-md overflow-hidden"><div className="px-4 py-3 border-b border-border"><h2 className="text-sm font-semibold text-text">持仓明细</h2></div>{data.positions.length ? <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-xs"><thead className="text-text-secondary border-b border-border"><tr><th className="text-left px-4 py-2.5">股票</th><th className="text-left px-3">行业</th><th className="text-right px-3">仓位</th><th className="text-right px-3">估算市值</th><th className="text-right px-3">现价</th><th className="text-right px-3">浮盈亏</th><th className="text-left px-4">加仓纪律</th></tr></thead><tbody>{data.positions.map((item) => <tr key={item.code} className="border-b border-border/60"><td className="px-4 py-3 text-text">{item.name}<span className="font-mono text-text-secondary ml-2">{item.code}</span></td><td className="px-3 text-text-secondary">{item.industry}</td><td className="px-3 text-right font-mono">{number(item.weight_pct, 1)}%</td><td className="px-3 text-right font-mono">{money(item.market_value)}</td><td className="px-3 text-right font-mono">{item.price == null ? '--' : `¥${number(item.price)}`}</td><td className={`px-3 text-right font-mono ${(item.pnl_pct || 0) >= 0 ? 'text-up' : 'text-down'}`}>{signed(item.pnl_pct)}</td><td className={`px-4 ${item.add_allowed ? 'text-down' : 'text-up'}`}>{item.add_allowed ? '未触发亏损加仓限制' : item.add_block_reason}</td></tr>)}</tbody></table></div> : <Empty text="尚无持仓记录" />}</section>
        <aside className="space-y-4"><section className="border border-border rounded-md p-4"><h2 className="text-sm font-semibold text-text">调整提示</h2><div className="mt-3 space-y-2">{data.advice.map((item) => <div key={item} className="text-xs text-text-secondary leading-5 border-l-2 border-warn pl-2.5">{item}</div>)}</div></section><section className="border border-border rounded-md p-4"><h2 className="text-sm font-semibold text-text">单日加仓</h2>{data.daily_additions.length ? <div className="mt-3 space-y-2">{data.daily_additions.map((item) => <div key={item.code} className="flex justify-between text-xs"><span className="font-mono text-text">{item.code}</span><span className={item.status === 'danger' ? 'text-up' : 'text-text-secondary'}>{money(item.amount)} · {item.pct == null ? '--' : `${number(item.pct)}%`}</span></div>)}</div> : <p className="text-xs text-text-secondary mt-3">今日没有已记录买入。</p>}</section><p className="text-[11px] text-text-secondary leading-5">{data.methodology}</p></aside>
      </div>
    </>}

    {settingsOpen && <div className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4"><form onSubmit={save} className="w-full max-w-xl bg-card border border-border rounded-md p-5"><div className="flex items-center justify-between"><h2 className="text-base font-semibold text-text">账户与仓位阈值</h2><button type="button" onClick={() => setSettingsOpen(false)} className="p-1 text-text-secondary" title="关闭"><X size={18} /></button></div><div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-4"><Field label="账户总资产（元）" name="total_assets" value={form.total_assets} setForm={setForm} /><Field label="股票总仓位上限 %" name="equity_ceiling_pct" value={form.equity_ceiling_pct} setForm={setForm} /><Field label="现金下限 %" name="cash_floor_pct" value={form.cash_floor_pct} setForm={setForm} /><Field label="单股上限 %" name="single_stock_limit_pct" value={form.single_stock_limit_pct} setForm={setForm} /><Field label="行业上限 %" name="sector_limit_pct" value={form.sector_limit_pct} setForm={setForm} /><Field label="单日加仓上限 %" name="daily_add_limit_pct" value={form.daily_add_limit_pct} setForm={setForm} /><Field label="亏损加仓限制 %" name="loss_add_block_pct" value={form.loss_add_block_pct} setForm={setForm} /></div><div className="flex justify-end gap-2 mt-5 pt-4 border-t border-border"><button type="button" onClick={() => setSettingsOpen(false)} className="px-3 py-2 text-xs border border-border rounded-md text-text-secondary">取消</button><button type="submit" disabled={saving} className="inline-flex items-center gap-1.5 px-3 py-2 bg-accent text-white rounded-md text-xs disabled:opacity-50">{saving && <Loader2 size={13} className="animate-spin" />}保存</button></div></form></div>}
  </div>;
}

function Metric({ label, value, className = '' }: { label: string; value: string; className?: string }) { return <div className="p-3 min-w-0"><div className="text-[11px] text-text-secondary">{label}</div><div className={`font-mono mt-1 text-sm truncate ${className || 'text-text'}`}>{value}</div></div>; }
function Empty({ text }: { text: string }) { return <div className="py-12 text-center text-xs text-text-secondary">{text}</div>; }
function Loading() { return <div className="py-24 text-center"><Loader2 size={28} className="animate-spin text-accent mx-auto" /><div className="text-xs text-text-secondary mt-3">正在计算仓位与风险指标</div></div>; }
function Field({ label, name, value = '', setForm }: { label: string; name: string; value?: string; setForm: React.Dispatch<React.SetStateAction<Record<string, string>>> }) { return <label className="text-xs text-text-secondary">{label}<input type="number" step="0.1" value={value} onChange={(event) => setForm((current) => ({ ...current, [name]: event.target.value }))} className="mt-1 w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text font-mono" /></label>; }
