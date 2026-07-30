'use client';

import { useState } from 'react';
import { apiFetch, formatYi, formatYiShort } from '@/lib/api';

export default function StockPage() {
  const [stockCode, setStockCode] = useState('');
  const [flowData, setFlowData] = useState<any[]>([]);
  const [priceHistory, setPriceHistory] = useState<any[]>([]);
  const [hasSearched, setHasSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSearch = async () => {
    if (!stockCode.trim()) return;
    setLoading(true);
    setError('');
    try {
      const [flowResult, historyResult] = await Promise.allSettled([
        apiFetch<any>(`/flow/stock/${stockCode}`),
        apiFetch<any>(`/data/stock/${stockCode}/history?days=365`),
      ]);
      if (flowResult.status === 'fulfilled') {
        setFlowData(flowResult.value.data.flow_data || []);
      } else {
        setFlowData([]);
      }
      if (historyResult.status === 'fulfilled') {
        setPriceHistory(historyResult.value.data.history || []);
      } else {
        setPriceHistory([]);
      }
      setHasSearched(true);
      if (flowResult.status === 'rejected' && historyResult.status === 'rejected') {
        setError('查询失败，请检查股票代码');
      }
    } catch (err) {
      setError('查询失败，请检查股票代码');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <h1 className="text-2xl font-bold text-text mb-2">个股资金透视</h1>
      <p className="text-text-secondary mb-6">输入股票代码查询资金流向明细</p>

      <div className="bg-card border border-border rounded-lg p-6 mb-6">
        <div className="flex gap-3 mb-4">
          <input
            type="text"
            value={stockCode}
            onChange={(e) => setStockCode(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            placeholder="输入股票代码，如 000858（五粮液）"
            className="flex-1 bg-[#0D1117] border border-border rounded-md px-4 py-2.5 text-sm text-text placeholder:text-text-secondary focus:outline-none focus:border-accent"
          />
          <button
            onClick={handleSearch}
            disabled={loading || !stockCode.trim()}
            className="px-6 py-2.5 bg-accent text-white text-sm rounded-md hover:opacity-90 disabled:opacity-50 transition-colors"
          >
            {loading ? '查询中...' : '查询'}
          </button>
        </div>
        {error && <div className="text-down text-sm">{error}</div>}
      </div>

      {flowData.length > 0 && (
        <div className="bg-card border border-border rounded-lg overflow-hidden">
          <div className="px-4 pt-4 text-sm font-medium text-text">近期资金流向</div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-text-secondary border-b border-border">
                  <th className="text-left px-4 py-3 font-medium">日期</th>
                  <th className="text-right px-4 py-3 font-medium">主力净流入</th>
                  <th className="text-right px-4 py-3 font-medium">超大单</th>
                  <th className="text-right px-4 py-3 font-medium">大单</th>
                  <th className="text-right px-4 py-3 font-medium">中单</th>
                  <th className="text-right px-4 py-3 font-medium">小单</th>
                </tr>
              </thead>
              <tbody>
                {flowData.map((row, i) => (
                  <tr key={i} className="border-b border-border/50 hover:bg-[#21262D]">
                    <td className="px-4 py-3 font-medium">{row.date}</td>
                    <td className={`px-4 py-3 text-right font-mono ${row.main_net_inflow >= 0 ? 'text-up' : 'text-down'}`}>
                      {formatYi(row.main_net_inflow)}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{formatYi(row.super_large_net_inflow)}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{formatYi(row.large_net_inflow)}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{formatYi(row.medium_net_inflow)}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{formatYi(row.small_net_inflow)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {priceHistory.length > 0 && (
        <div className="bg-card border border-border rounded-lg overflow-hidden mt-6">
          <div className="px-4 pt-4">
            <div className="text-sm font-medium text-text">近一年已缓存日线</div>
            <div className="text-xs text-text-secondary mt-1">显示最近 30 个交易日</div>
          </div>
          <div className="overflow-x-auto mt-3">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-text-secondary border-b border-border">
                  <th className="text-left px-4 py-3 font-medium">日期</th>
                  <th className="text-right px-4 py-3 font-medium">开盘</th>
                  <th className="text-right px-4 py-3 font-medium">收盘</th>
                  <th className="text-right px-4 py-3 font-medium">最高</th>
                  <th className="text-right px-4 py-3 font-medium">最低</th>
                  <th className="text-right px-4 py-3 font-medium">涨跌幅</th>
                  <th className="text-right px-4 py-3 font-medium">成交额</th>
                </tr>
              </thead>
              <tbody>
                {priceHistory.slice(-30).reverse().map((row) => (
                  <tr key={row.date} className="border-b border-border/50 hover:bg-[#21262D]">
                    <td className="px-4 py-3 font-medium">{row.date}</td>
                    <td className="px-4 py-3 text-right font-mono">{Number(row.open).toFixed(2)}</td>
                    <td className="px-4 py-3 text-right font-mono">{Number(row.close).toFixed(2)}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{Number(row.high).toFixed(2)}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{Number(row.low).toFixed(2)}</td>
                    <td className={`px-4 py-3 text-right font-mono ${Number(row.change_pct) >= 0 ? 'text-up' : 'text-down'}`}>
                      {row.change_pct == null ? '--' : <>{Number(row.change_pct) > 0 ? '+' : ''}{Number(row.change_pct).toFixed(2)}%</>}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{row.amount == null ? '--' : formatYiShort(row.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {hasSearched && !loading && priceHistory.length === 0 && !error && (
        <div className="mt-6 text-center text-text-secondary text-sm">该股票的近一年日线尚未完成回补。</div>
      )}
    </div>
  );
}
