'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts';
import { Activity, ArrowDownUp, Database, RefreshCw } from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';

type Ranking = { code: string; name: string; change_pct: number | null; main_net_inflow: number | null };
type FlowPayload = { rankings: Ranking[]; trade_date: string | null; source?: string; is_realtime?: boolean };
type SortMode = 'flow' | 'change';

function finite(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value); }
function money(value: unknown): string {
  if (!finite(value)) return '--';
  const absolute = Math.abs(value);
  const formatted = absolute >= 1e8 ? `${(absolute / 1e8).toFixed(2)}亿` : absolute >= 1e4 ? `${(absolute / 1e4).toFixed(1)}万` : absolute.toFixed(0);
  return `${value > 0 ? '+' : value < 0 ? '-' : ''}${formatted}`;
}
function pct(value: unknown): string { return finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '--'; }
function tileColor(change: unknown): string {
  if (!finite(change) || change === 0) return '#374151';
  const strength = Math.min(1, Math.max(0.25, Math.abs(change) / 6));
  const base = change > 0 ? [239, 83, 80] : [38, 166, 154];
  const floor = [57, 64, 75];
  const mix = base.map((channel, index) => Math.round(floor[index] + (channel - floor[index]) * strength));
  return `rgb(${mix.join(',')})`;
}

export default function HeatmapPage() {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);
  const [rows, setRows] = useState<Ranking[]>([]);
  const [meta, setMeta] = useState<FlowPayload | null>(null);
  const [sortMode, setSortMode] = useState<SortMode>('flow');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const [inflowResponse, outflowResponse] = await Promise.all([
        apiFetch<{ data: FlowPayload }>('/flow/concept/rank?sort=main_net_inflow&order=desc&limit=40', { timeoutMs: 30000 }),
        apiFetch<{ data: FlowPayload }>('/flow/concept/rank?sort=main_net_inflow&order=asc&limit=40', { timeoutMs: 30000 }),
      ]);
      const byCode = new Map<string, Ranking>();
      [...(inflowResponse.data.rankings || []), ...(outflowResponse.data.rankings || [])].forEach((row) => { if (row?.code && !byCode.has(row.code)) byCode.set(row.code, row); });
      setRows([...byCode.values()]); setMeta(inflowResponse.data);
    } catch (caught) { setError(friendlyApiError(caught, '板块资金热力数据暂时不可用')); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const displayRows = useMemo(() => [...rows].sort((left, right) => sortMode === 'flow'
    ? Math.abs(Number(right.main_net_inflow) || 0) - Math.abs(Number(left.main_net_inflow) || 0)
    : Math.abs(Number(right.change_pct) || 0) - Math.abs(Number(left.change_pct) || 0)).slice(0, 36), [rows, sortMode]);

  useEffect(() => {
    if (!chartRef.current || !displayRows.length) return;
    const chart = chartInstance.current || echarts.init(chartRef.current); chartInstance.current = chart;
    chart.setOption({ animationDuration: 350, backgroundColor: 'transparent', tooltip: {
      backgroundColor: '#111820', borderColor: 'rgba(255,255,255,.14)', padding: 12, textStyle: { color: '#F2F4F7', fontSize: 12 },
      formatter: (params: any) => { const item = params.data as Ranking; return `<b>${item.name}</b><br/>涨跌幅：${pct(item.change_pct)}<br/>主力净额：${money(item.main_net_inflow)}`; },
    }, series: [{ type: 'treemap', width: '100%', height: '100%', top: 0, left: 0, roam: false, nodeClick: false, breadcrumb: { show: false }, upperLabel: { show: false },
      label: { show: true, overflow: 'break', formatter: (params: any) => { const item = params.data as Ranking; return `{name|${item.name}}\n{change|${pct(item.change_pct)}}\n{flow|${money(item.main_net_inflow)}}`; }, rich: {
        name: { color: '#FFFFFF', fontSize: 13, fontWeight: 600, lineHeight: 20 }, change: { color: '#FFFFFF', fontSize: 12, fontWeight: 600, lineHeight: 18 }, flow: { color: 'rgba(255,255,255,.78)', fontSize: 10, lineHeight: 16 },
      } }, itemStyle: { borderColor: '#0D1117', borderWidth: 2, gapWidth: 2 }, emphasis: { itemStyle: { borderColor: '#FFFFFF', borderWidth: 2 } },
      data: displayRows.map((row) => ({ ...row, value: Math.max(Math.abs(Number(row.main_net_inflow) || 0), 1), itemStyle: { color: tileColor(row.change_pct) } })),
    }] }, true);
    const onClick = (params: any) => { const code = params?.data?.code; if (code) window.location.href = `/pro/topic-strength?view=period&board=${encodeURIComponent(code)}`; };
    const onResize = () => chart.resize(); chart.on('click', onClick); window.addEventListener('resize', onResize);
    return () => { chart.off('click', onClick); window.removeEventListener('resize', onResize); };
  }, [displayRows]);
  useEffect(() => () => { chartInstance.current?.dispose(); chartInstance.current = null; }, []);

  const inflowCount = rows.filter((row) => finite(row.main_net_inflow) && row.main_net_inflow > 0).length;
  const outflowCount = rows.filter((row) => finite(row.main_net_inflow) && row.main_net_inflow < 0).length;
  return <main className="mx-auto w-full max-w-[1540px] px-3 py-5 sm:px-5">
    <header className="flex flex-col gap-4 border-b border-border pb-4 lg:flex-row lg:items-end lg:justify-between"><div>
      <h1 className="text-xl font-semibold text-text sm:text-2xl">板块轮动热力图</h1><p className="mt-1 text-xs text-text-secondary">面积表示主力资金净额规模，颜色只表示涨跌幅：红涨、绿跌、灰色平盘</p>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-text-secondary"><span className="inline-flex items-center gap-1"><Database size={12} />{meta?.source === 'cache' ? '数据库缓存' : '行情聚合'}</span><span>数据日 {meta?.trade_date || '--'}</span><span>{meta?.is_realtime ? '盘中实时' : '最近收盘/缓存'}</span><span>覆盖 {rows.length} 个板块</span></div>
    </div><div className="flex items-center gap-2"><div className="flex rounded-md border border-border bg-card p-1" role="group" aria-label="热力图排序"><button type="button" onClick={() => setSortMode('flow')} className={`h-8 rounded px-3 text-xs ${sortMode === 'flow' ? 'bg-accent/15 text-accent' : 'text-text-secondary'}`}>按资金规模</button><button type="button" onClick={() => setSortMode('change')} className={`h-8 rounded px-3 text-xs ${sortMode === 'change' ? 'bg-accent/15 text-accent' : 'text-text-secondary'}`}>按涨跌强度</button></div><button type="button" onClick={() => void load()} disabled={loading} className="inline-flex h-10 items-center gap-1.5 rounded-md border border-border px-3 text-xs text-text-secondary hover:border-accent hover:text-text disabled:opacity-50"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} />刷新</button></div></header>
    <section className="my-4 grid grid-cols-2 gap-px border border-border bg-border sm:grid-cols-4">{[['展示板块', `${displayRows.length}个`], ['资金净流入', `${inflowCount}个`], ['资金净流出', `${outflowCount}个`], ['图形口径', sortMode === 'flow' ? '资金规模优先' : '涨跌强度优先']].map(([label, value]) => <div key={label} className="bg-card px-4 py-3"><div className="text-[11px] text-text-secondary">{label}</div><div className="mt-1 font-mono text-base font-semibold text-text">{value}</div></div>)}</section>
    {error && <div className="mb-4 flex items-start gap-2 border border-warn/40 bg-warn/5 px-3 py-3 text-xs text-warn"><Activity size={14} />{error}</div>}
    <section className="overflow-hidden rounded-md border border-border bg-card p-2 sm:p-3"><div className="mb-2 flex flex-wrap items-center justify-between gap-2 px-1 text-[11px] text-text-secondary"><div className="flex items-center gap-4"><span className="inline-flex items-center gap-1.5"><i className="h-2.5 w-2.5 bg-up" />上涨</span><span className="inline-flex items-center gap-1.5"><i className="h-2.5 w-2.5 bg-down" />下跌</span><span className="inline-flex items-center gap-1.5"><i className="h-2.5 w-2.5 bg-[#374151]" />平盘/缺失</span></div><span className="inline-flex items-center gap-1"><ArrowDownUp size={12} />点击板块进入题材强弱详情</span></div><div className="relative min-h-[540px] sm:min-h-[680px]">{loading && !rows.length && <div className="absolute inset-0 z-10 grid place-items-center bg-card/90 text-xs text-text-secondary"><span className="inline-flex items-center gap-2"><RefreshCw size={15} className="animate-spin text-accent" />正在读取资金流入与流出两侧数据</span></div>}{!loading && !rows.length && !error && <div className="absolute inset-0 grid place-items-center text-sm text-text-secondary">当前没有可核验的板块资金数据</div>}<div ref={chartRef} className="h-[540px] w-full sm:h-[680px]" /></div></section>
  </main>;
}
