'use client';

import { useCallback, useEffect, useState } from 'react';
import { Database, Filter, RefreshCw, Search, SlidersHorizontal, TrendingUp } from 'lucide-react';
import AddToPersonalPoolButton from '@/components/AddToPersonalPoolButton';
import { apiFetch, formatYi, getChangeColor } from '@/lib/api';

type Preset = 'basic' | 'short' | 'long' | 'custom';
type RangeKey = 'change_pct' | 'turnover_pct' | 'volume_ratio' | 'pe_ttm' | 'market_cap_yi' | 'price';

interface Criteria {
  preset: Preset;
  change_pct: number[];
  turnover_pct: number[];
  volume_ratio: number[];
  pe_ttm: number[];
  pb_max: number;
  roe_min: number;
  market_cap_yi: number[];
  main_net_inflow_yi_min: number;
  main_net_inflow_pct_min: number;
  amount_yi_min: number;
  amplitude_pct_max: number;
  price: number[];
  require_profitable: boolean;
  exclude_special: boolean;
  exclude_star_market: boolean;
  exclude_gem: boolean;
  exclude_bse: boolean;
  sort_by: 'volume_ratio' | 'main_inflow' | 'change_pct' | 'turnover' | 'roe';
  limit: number;
}

interface ScreenerStock {
  code: string;
  name: string;
  sector?: string;
  price: number;
  change_pct: number;
  turnover: number;
  pe: number | string | null;
  pb: number | string | null;
  roe: number | string | null;
  volume_ratio: number;
  main_net_inflow: number;
  main_net_inflow_pct?: number;
  market_cap: number;
  amount?: number;
  amplitude?: number;
}

interface ScreenerData {
  stocks: ScreenerStock[];
  total: number;
  returned_count: number;
  candidate_count: number;
  criteria: Criteria;
  rejection_counts: Record<string, number>;
  source: string;
  cache_used: boolean;
  coverage_complete: boolean;
  data_date: string | null;
  source_updated_at: string | null;
  is_realtime: boolean;
  updated_at: string;
}

const baseCriteria: Criteria = {
  preset: 'basic',
  change_pct: [2, 20],
  turnover_pct: [3, 100],
  volume_ratio: [1.2, 20],
  pe_ttm: [-1000, 100],
  pb_max: 100,
  roe_min: -100,
  market_cap_yi: [0, 100000],
  main_net_inflow_yi_min: -1000,
  main_net_inflow_pct_min: -100,
  amount_yi_min: 0,
  amplitude_pct_max: 100,
  price: [0.01, 10000],
  require_profitable: false,
  exclude_special: true,
  exclude_star_market: false,
  exclude_gem: false,
  exclude_bse: true,
  sort_by: 'volume_ratio',
  limit: 100,
};

const presets: Record<'basic' | 'short' | 'long', Criteria> = {
  basic: baseCriteria,
  short: {
    ...baseCriteria,
    preset: 'short',
    change_pct: [0.5, 7],
    turnover_pct: [2, 18],
    volume_ratio: [1.2, 6],
    pe_ttm: [-1000, 120],
    market_cap_yi: [20, 1500],
    main_net_inflow_yi_min: 0,
    main_net_inflow_pct_min: 0,
    amount_yi_min: 0.5,
    amplitude_pct_max: 12,
    sort_by: 'volume_ratio',
  },
  long: {
    ...baseCriteria,
    preset: 'long',
    change_pct: [-4, 6],
    turnover_pct: [0.2, 10],
    volume_ratio: [1.2, 4],
    pe_ttm: [0.01, 50],
    pb_max: 6,
    roe_min: 8,
    market_cap_yi: [50, 10000],
    main_net_inflow_yi_min: -5,
    main_net_inflow_pct_min: -10,
    amount_yi_min: 0.2,
    amplitude_pct_max: 10,
    require_profitable: true,
    sort_by: 'roe',
  },
};

const rejectionLabels: Record<string, string> = {
  change_pct: '涨跌幅', turnover: '换手率', volume_ratio: '量比', market_cap: '市值',
  pe: 'PE', pb: 'PB', roe: 'ROE', profitable: '未盈利', main_inflow: '主力净流入',
  main_inflow_pct: '主力占比', amount: '成交额', amplitude: '振幅', price: '股价',
  special: 'ST/退市', star_market: '科创板', gem: '创业板', bse: '北交所',
  change_pct_missing: '缺涨跌幅', turnover_missing: '缺换手率', volume_ratio_missing: '缺量比',
  market_cap_missing: '缺市值', pe_missing: '缺PE', pb_missing: '缺PB', roe_missing: '缺ROE',
  main_inflow_missing: '缺主力资金', main_inflow_pct_missing: '缺主力占比',
  amount_missing: '缺成交额', amplitude_missing: '缺振幅', price_missing: '缺股价',
};

function cloneCriteria(criteria: Criteria): Criteria {
  return {
    ...criteria,
    change_pct: [...criteria.change_pct], turnover_pct: [...criteria.turnover_pct],
    volume_ratio: [...criteria.volume_ratio], pe_ttm: [...criteria.pe_ttm],
    market_cap_yi: [...criteria.market_cap_yi], price: [...criteria.price],
  };
}

function number(value: number | string | null | undefined, digits = 2): string {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : '--';
}

function statusSource(data: ScreenerData): string {
  if (data.is_realtime) return '盘中实时行情';
  if (data.cache_used) return data.data_date ? `休市缓存 · ${data.data_date}` : '系统缓存';
  return data.data_date ? `最近交易快照 · ${data.data_date}` : '来源快照';
}

export default function ScreenerPage() {
  const [draft, setDraft] = useState<Criteria>(() => cloneCriteria(presets.basic));
  const [data, setData] = useState<ScreenerData | null>(null);
  const [loading, setLoading] = useState(true);
  const [progress, setProgress] = useState(8);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (criteria: Criteria) => {
    setLoading(true);
    setProgress(8);
    setError(null);
    try {
      const response = await apiFetch<{ code: number; data: ScreenerData }>('/screener/technical/run', {
        method: 'POST', body: JSON.stringify(criteria),
      });
      setData(response.data);
      setProgress(100);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '技术筛选暂时无法完成');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { run(cloneCriteria(presets.basic)); }, [run]);

  useEffect(() => {
    if (!loading) return undefined;
    const timer = window.setInterval(() => setProgress((current) => Math.min(92, current + 4)), 450);
    return () => window.clearInterval(timer);
  }, [loading]);

  const applyPreset = (preset: 'basic' | 'short' | 'long') => setDraft(cloneCriteria(presets[preset]));
  function setField<K extends keyof Criteria>(key: K, value: Criteria[K]) {
    setDraft((current) => ({ ...current, preset: 'custom', [key]: value }));
  }
  const setRange = (key: RangeKey, index: number, value: number) => setDraft((current) => {
    const values = [...current[key]];
    values[index] = value;
    return { ...current, preset: 'custom', [key]: values };
  });

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 overflow-x-hidden">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div><h1 className="text-2xl font-bold text-text flex items-center gap-2"><Filter size={22} className="text-warn" />技术面筛选器</h1><p className="text-sm text-text-secondary mt-1">短线动量 · 长期质量 · 自定义因子</p></div>
        {data && <div className="text-right text-xs text-text-secondary leading-5"><div className="flex items-center justify-end gap-1.5"><span className={`h-2 w-2 rounded-full ${data.is_realtime ? 'bg-down' : 'bg-warn'}`} />{statusSource(data)}</div><div>{data.source === 'cache' ? '系统缓存' : '东方财富'} · {data.coverage_complete ? '全市场快照' : '排行候选池'}</div></div>}
      </header>

      <section className="border border-border bg-card rounded-lg p-4 mb-6">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 text-sm font-medium text-text"><SlidersHorizontal size={15} className="text-accent" />策略预设</div>
            <div className="flex rounded-md bg-[#0D1117] p-1" role="group" aria-label="技术筛选预设">
              {([['basic', '基础'], ['short', '短线'], ['long', '长期']] as const).map(([id, label]) => <button key={id} type="button" onClick={() => applyPreset(id)} className={`px-3 py-1.5 text-xs rounded ${draft.preset === id ? 'bg-[#1F6FEB33] text-accent' : 'text-text-secondary hover:text-text'}`}>{label}</button>)}
              {draft.preset === 'custom' && <span className="px-3 py-1.5 text-xs rounded bg-[#1F6FEB33] text-accent">自定义</span>}
            </div>
          </div>
          <div className="flex items-end gap-2">
            <label><span className="block text-[11px] text-text-secondary mb-1">排序</span><select value={draft.sort_by} onChange={(event) => setField('sort_by', event.target.value as Criteria['sort_by'])} className="bg-[#0D1117] border border-border rounded-md px-2.5 py-2 text-xs text-text"><option value="volume_ratio">量比</option><option value="main_inflow">主力净流入</option><option value="change_pct">涨幅</option><option value="turnover">换手率</option><option value="roe">ROE</option></select></label>
            <label><span className="block text-[11px] text-text-secondary mb-1">最多显示</span><select value={draft.limit} onChange={(event) => setField('limit', Number(event.target.value))} className="bg-[#0D1117] border border-border rounded-md px-2.5 py-2 text-xs text-text">{[50, 100, 150, 200].map((value) => <option key={value} value={value}>{value}只</option>)}</select></label>
            <button type="button" onClick={() => run(draft)} disabled={loading} className="h-9 px-4 bg-accent text-white text-xs rounded-md disabled:opacity-50 inline-flex items-center gap-1.5">{loading ? <RefreshCw size={14} className="animate-spin" /> : <Search size={14} />}筛选</button>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-x-5 gap-y-4 pt-4">
          <RangeField label="当日涨跌幅" unit="%" values={draft.change_pct} step={0.5} onChange={(index, value) => setRange('change_pct', index, value)} />
          <RangeField label="换手率" unit="%" values={draft.turnover_pct} step={0.5} onChange={(index, value) => setRange('turnover_pct', index, value)} />
          <RangeField label="量比（下限严格大于）" values={draft.volume_ratio} step={0.1} onChange={(index, value) => setRange('volume_ratio', index, value)} />
          <RangeField label="PE(TTM)" values={draft.pe_ttm} step={1} onChange={(index, value) => setRange('pe_ttm', index, value)} />
          <NumberField label="最高 PB" value={draft.pb_max} step={0.1} onChange={(value) => setField('pb_max', value)} />
          <NumberField label="最低 ROE" unit="%" value={draft.roe_min} step={0.5} onChange={(value) => setField('roe_min', value)} />
          <RangeField label="总市值" unit="亿元" values={draft.market_cap_yi} step={10} onChange={(index, value) => setRange('market_cap_yi', index, value)} />
          <NumberField label="最低主力净流入" unit="亿元" value={draft.main_net_inflow_yi_min} step={0.1} onChange={(value) => setField('main_net_inflow_yi_min', value)} />
          <NumberField label="最低主力净流入占比" unit="%" value={draft.main_net_inflow_pct_min} step={0.5} onChange={(value) => setField('main_net_inflow_pct_min', value)} />
          <NumberField label="最低成交额" unit="亿元" value={draft.amount_yi_min} step={0.1} onChange={(value) => setField('amount_yi_min', value)} />
          <NumberField label="最高振幅" unit="%" value={draft.amplitude_pct_max} step={0.5} onChange={(value) => setField('amplitude_pct_max', value)} />
          <RangeField label="股价" unit="元" values={draft.price} step={0.1} onChange={(index, value) => setRange('price', index, value)} />
        </div>
        <div className="mt-4 pt-3 border-t border-border flex flex-wrap gap-x-5 gap-y-2 text-xs">
          <Flag label="要求盈利" checked={draft.require_profitable} onChange={(value) => setField('require_profitable', value)} />
          <Flag label="排除 ST/退市" checked={draft.exclude_special} onChange={(value) => setField('exclude_special', value)} />
          <Flag label="排除科创板" checked={draft.exclude_star_market} onChange={(value) => setField('exclude_star_market', value)} />
          <Flag label="排除创业板" checked={draft.exclude_gem} onChange={(value) => setField('exclude_gem', value)} />
          <Flag label="排除北交所" checked={draft.exclude_bse} onChange={(value) => setField('exclude_bse', value)} />
        </div>
      </section>

      {loading && <section className="border border-border bg-card rounded-lg p-4 mb-6" role="status"><div className="flex justify-between text-xs"><span className="text-text flex items-center gap-2"><RefreshCw size={14} className="animate-spin text-accent" />读取行情并执行因子过滤</span><span className="font-mono text-accent">{progress}%</span></div><div className="h-1.5 bg-[#0D1117] rounded mt-3 overflow-hidden"><div className="h-full bg-accent transition-all" style={{ width: `${progress}%` }} /></div></section>}
      {error && <div className="mb-6 border border-down/50 bg-[#EF535018] rounded-lg px-4 py-3 text-sm text-down">{error}</div>}

      {!loading && data && <>
        <section className="mb-4 border-b border-border pb-4">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs"><span className="text-text flex items-center gap-2"><TrendingUp size={15} className="text-accent" />候选 {data.candidate_count} 只 · 匹配 <strong className="text-accent">{data.total}</strong> 只 · 显示 {data.returned_count} 只</span>{Object.entries(data.rejection_counts).slice(0, 10).map(([reason, count]) => <span key={reason} className="text-text-secondary">{rejectionLabels[reason] || reason} {count}</span>)}</div>
        </section>

        {data.stocks.length ? <section className="border border-border bg-card rounded-lg overflow-hidden">
          <div className="overflow-x-auto"><table className="w-full min-w-[1120px] text-sm"><thead><tr className="text-text-secondary border-b border-border bg-[#0D1117] text-xs"><th className="text-left px-4 py-3 font-medium">股票</th><th className="text-right px-3">现价</th><th className="text-right px-3">涨跌幅</th><th className="text-right px-3">量比</th><th className="text-right px-3">换手</th><th className="text-right px-3">PE / PB</th><th className="text-right px-3">ROE</th><th className="text-right px-3">主力净流入</th><th className="text-right px-3">成交额</th><th className="text-right px-3">市值</th><th className="text-right px-3">个人池</th></tr></thead><tbody>{data.stocks.map((stock) => <tr key={stock.code} className="border-b border-border/50 hover:bg-[#21262D]"><td className="px-4 py-3"><div className="text-text font-medium">{stock.name}</div><div className="text-xs text-text-secondary mt-0.5">{stock.code}{stock.sector ? ` · ${stock.sector}` : ''}</div></td><td className="px-3 py-3 text-right font-mono text-text">{number(stock.price)}</td><td className={`px-3 py-3 text-right font-mono ${getChangeColor(Number(stock.change_pct))}`}>{Number(stock.change_pct) >= 0 ? '+' : ''}{number(stock.change_pct)}%</td><td className="px-3 py-3 text-right font-mono text-text">{number(stock.volume_ratio)}</td><td className="px-3 py-3 text-right font-mono text-text-secondary">{number(stock.turnover)}%</td><td className="px-3 py-3 text-right font-mono text-text-secondary">{number(stock.pe, 1)} / {number(stock.pb, 1)}</td><td className="px-3 py-3 text-right font-mono text-text-secondary">{number(stock.roe, 1)}%</td><td className={`px-3 py-3 text-right font-mono ${getChangeColor(Number(stock.main_net_inflow))}`}>{formatYi(Number(stock.main_net_inflow || 0))}</td><td className="px-3 py-3 text-right font-mono text-text-secondary">{number(Number(stock.amount || 0) / 1e8)}亿</td><td className="px-3 py-3 text-right font-mono text-text-secondary">{number(Number(stock.market_cap || 0) / 1e8, 0)}亿</td><td className="px-3 py-3 text-right"><AddToPersonalPoolButton code={stock.code} name={stock.name} industry={stock.sector} thesis={`技术筛选：${data.criteria.preset}，涨跌幅${number(stock.change_pct)}%，量比${number(stock.volume_ratio)}，换手率${number(stock.turnover)}%，主力净流入${formatYi(Number(stock.main_net_inflow || 0))}`} source="technical_screener" compact /></td></tr>)}</tbody></table></div>
        </section> : <section className="py-12 text-center text-sm text-text-secondary border-y border-border"><Database size={20} className="mx-auto mb-2" />当前条件没有匹配股票</section>}
      </>}
    </div>
  );
}

function RangeField({ label, unit = '', values, step, onChange }: { label: string; unit?: string; values: number[]; step: number; onChange: (index: number, value: number) => void }) {
  return <label className="text-xs text-text-secondary"><span>{label}</span><div className="mt-1.5 grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto] items-center gap-1.5"><input type="number" step={step} value={values[0]} onChange={(event) => onChange(0, Number(event.target.value))} className="min-w-0 bg-[#0D1117] border border-border rounded px-2 py-1.5 font-mono text-text" /><span>至</span><input type="number" step={step} value={values[1]} onChange={(event) => onChange(1, Number(event.target.value))} className="min-w-0 bg-[#0D1117] border border-border rounded px-2 py-1.5 font-mono text-text" /><span className="whitespace-nowrap">{unit}</span></div></label>;
}

function NumberField({ label, unit = '', value, step, onChange }: { label: string; unit?: string; value: number; step: number; onChange: (value: number) => void }) {
  return <label className="text-xs text-text-secondary"><span>{label}</span><div className="mt-1.5 flex items-center gap-1.5"><input type="number" step={step} value={value} onChange={(event) => onChange(Number(event.target.value))} className="min-w-0 flex-1 bg-[#0D1117] border border-border rounded px-2 py-1.5 font-mono text-text" /><span className="whitespace-nowrap">{unit}</span></div></label>;
}

function Flag({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="inline-flex items-center gap-2 text-text-secondary"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="accent-[#2F81F7]" />{label}</label>;
}
