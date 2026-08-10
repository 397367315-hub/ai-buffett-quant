'use client';

import { useCallback, useEffect, useState } from 'react';
import { BarChart3, RefreshCw, Search } from 'lucide-react';
import KlineChart, { KlineRow } from '@/components/KlineChart';
import { apiFetch, formatYi, formatYiShort } from '@/lib/api';

type KlineCategory = 4 | 5 | 6 | 11;

interface KlineData {
  stock_code: string;
  stock_name: string;
  category: KlineCategory;
  category_label: string;
  rows: KlineRow[];
  count: number;
  available: boolean;
  source: string;
  data_date: string | null;
  is_realtime: boolean;
  warning: string | null;
}
const CATEGORY_OPTIONS: Array<{ value: KlineCategory; label: string }> = [
  { value: 4, label: '日K' },
  { value: 5, label: '周K' },
  { value: 6, label: '月K' },
  { value: 11, label: '60分钟' },
];

function number(value: number | null | undefined, digits = 2): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '--';
}

export default function StockPage() {
  const [stockCode, setStockCode] = useState('');
  const [flowData, setFlowData] = useState<any[]>([]);
  const [kline, setKline] = useState<KlineData | null>(null);
  const [category, setCategory] = useState<KlineCategory>(4);
  const [hasSearched, setHasSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [klineLoading, setKlineLoading] = useState(false);
  const [error, setError] = useState('');

  const loadKline = useCallback(async (code: string, nextCategory: KlineCategory) => {
    setKlineLoading(true);
    try {
      const offset = nextCategory === 6 ? 120 : nextCategory === 5 ? 160 : 120;
      const response = await apiFetch<{ data: KlineData }>(`/kline?code=${encodeURIComponent(code)}&category=${nextCategory}&offset=${offset}`);
      setKline(response.data);
    } catch {
      setKline(null);
    } finally {
      setKlineLoading(false);
    }
  }, []);

  const handleSearch = useCallback(async (codeInput?: string) => {
    const code = String(codeInput ?? stockCode).trim();
    if (!code) return;
    setStockCode(code);
    setLoading(true);
    setError('');
    setCategory(4);
    try {
      const [flowResult, klineResult] = await Promise.allSettled([
        apiFetch<any>(`/flow/stock/${encodeURIComponent(code)}`),
        apiFetch<{ data: KlineData }>(`/kline?code=${encodeURIComponent(code)}&category=4&offset=120`),
      ]);
      setFlowData(flowResult.status === 'fulfilled' ? flowResult.value.data.flow_data || [] : []);
      setKline(klineResult.status === 'fulfilled' ? klineResult.value.data : null);
      setHasSearched(true);
      if (flowResult.status === 'rejected' && klineResult.status === 'rejected') {
        setError('查询失败，请检查股票代码或稍后重试');
      }
    } finally {
      setLoading(false);
    }
  }, [stockCode]);

  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get('code')?.trim();
    if (code) void handleSearch(code);
    // URL initialization should run once; subsequent searches use the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const switchCategory = async (nextCategory: KlineCategory) => {
    if (!stockCode.trim() || nextCategory === category) return;
    setCategory(nextCategory);
    await loadKline(stockCode.trim(), nextCategory);
  };

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <header className="mb-6 border-b border-border pb-5">
        <h1 className="flex items-center gap-2 text-xl font-bold text-text"><BarChart3 size={21} className="text-accent" />个股资金透视</h1>
        <p className="mt-1 text-xs text-text-secondary">资金流、缓存行情与多周期K线核验</p>
      </header>

      <section className="mb-6 border-y border-border py-4">
        <div className="flex flex-col gap-3 sm:flex-row">
          <input
            type="text"
            value={stockCode}
            onChange={(event) => setStockCode(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') void handleSearch(); }}
            placeholder="输入6位股票代码，如 000858"
            className="h-10 flex-1 rounded-md border border-border bg-[#0D1117] px-3 text-sm text-text placeholder:text-text-secondary focus:border-accent focus:outline-none"
          />
          <button
            type="button"
            onClick={() => void handleSearch()}
            disabled={loading || !stockCode.trim()}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-accent px-5 text-sm text-white hover:opacity-90 disabled:opacity-50"
          >
            {loading ? <RefreshCw size={15} className="animate-spin" /> : <Search size={15} />}{loading ? '查询中' : '查询'}
          </button>
        </div>
        {error && <div className="mt-3 text-sm text-down">{error}</div>}
      </section>

      {kline && (
        <section className="mb-6 overflow-hidden rounded-md border border-border bg-card">
          <div className="flex flex-col gap-3 border-b border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="text-sm font-semibold text-text">{kline.stock_name || stockCode} <span className="ml-1 font-mono text-xs font-normal text-text-secondary">{kline.stock_code}</span></div>
              <div className="mt-1 text-[10px] text-text-secondary">{kline.category_label} · 数据日 {kline.data_date || '--'} · {kline.source} · {kline.is_realtime ? '实时' : '历史/缓存'}</div>
            </div>
            <div className="inline-flex h-8 self-start overflow-hidden rounded-md border border-border">
              {CATEGORY_OPTIONS.map((item) => (
                <button
                  type="button"
                  key={item.value}
                  onClick={() => void switchCategory(item.value)}
                  disabled={klineLoading}
                  className={`border-r border-border px-3 text-xs last:border-r-0 ${category === item.value ? 'bg-accent text-white' : 'bg-[#0D1117] text-text-secondary hover:text-text'} disabled:opacity-50`}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
          {klineLoading ? (
            <div className="grid h-[360px] place-items-center text-xs text-text-secondary"><RefreshCw size={19} className="mb-2 animate-spin text-accent" />正在切换K线周期</div>
          ) : (
            <KlineChart rows={kline.rows} height={390} />
          )}
          {kline.warning && <div className="border-t border-warn/30 px-4 py-2 text-[10px] text-warn">{kline.warning}</div>}
        </section>
      )}

      {flowData.length > 0 && (
        <section className="mb-6 overflow-hidden rounded-md border border-border bg-card">
          <div className="border-b border-border px-4 py-3 text-sm font-medium text-text">近期资金流向</div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="bg-[#0D1117] text-text-secondary">
                <tr><th className="px-4 py-3 text-left font-medium">日期</th><th className="px-4 py-3 text-right font-medium">主力净流入</th><th className="px-4 py-3 text-right font-medium">超大单</th><th className="px-4 py-3 text-right font-medium">大单</th><th className="px-4 py-3 text-right font-medium">中单</th><th className="px-4 py-3 text-right font-medium">小单</th></tr>
              </thead>
              <tbody>
                {flowData.map((row) => (
                  <tr key={row.date} className="border-t border-border/60">
                    <td className="px-4 py-3 font-medium text-text">{row.date}</td>
                    <td className={`px-4 py-3 text-right font-mono ${row.main_net_inflow >= 0 ? 'text-up' : 'text-down'}`}>{formatYi(row.main_net_inflow)}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{formatYi(row.super_large_net_inflow)}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{formatYi(row.large_net_inflow)}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{formatYi(row.medium_net_inflow)}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{formatYi(row.small_net_inflow)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {kline?.rows && kline.rows.length > 0 && (
        <section className="overflow-hidden rounded-md border border-border bg-card">
          <div className="border-b border-border px-4 py-3">
            <div className="text-sm font-medium text-text">{kline.category_label}明细</div>
            <div className="mt-1 text-[10px] text-text-secondary">显示最近 {Math.min(kline.rows.length, 30)} 条</div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="bg-[#0D1117] text-text-secondary"><tr><th className="px-4 py-3 text-left font-medium">日期</th><th className="px-4 py-3 text-right font-medium">开盘</th><th className="px-4 py-3 text-right font-medium">收盘</th><th className="px-4 py-3 text-right font-medium">最高</th><th className="px-4 py-3 text-right font-medium">最低</th><th className="px-4 py-3 text-right font-medium">涨跌幅</th><th className="px-4 py-3 text-right font-medium">成交额</th></tr></thead>
              <tbody>
                {kline.rows.slice(-30).reverse().map((row) => (
                  <tr key={row.date} className="border-t border-border/60">
                    <td className="px-4 py-3 font-medium text-text">{row.date}</td>
                    <td className="px-4 py-3 text-right font-mono">{number(row.open)}</td>
                    <td className="px-4 py-3 text-right font-mono">{number(row.close)}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{number(row.high)}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{number(row.low)}</td>
                    <td className={`px-4 py-3 text-right font-mono ${(row.change_pct ?? 0) >= 0 ? 'text-up' : 'text-down'}`}>{row.change_pct == null ? '--' : `${row.change_pct > 0 ? '+' : ''}${number(row.change_pct)}%`}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{row.amount == null ? '--' : formatYiShort(row.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {hasSearched && !loading && !kline?.available && !error && (
        <div className="border-y border-border py-12 text-center text-sm text-text-secondary">该股票当前没有可核验K线。</div>
      )}
    </div>
  );
}
