'use client';

import { useState } from 'react';
import { apiFetch, formatYi } from '@/lib/api';

export default function StockPage() {
  const [stockCode, setStockCode] = useState('');
  const [flowData, setFlowData] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSearch = async () => {
    if (!stockCode.trim()) return;
    setLoading(true);
    setError('');
    try {
      const res = await apiFetch<any>(`/flow/stock/${stockCode}`);
      setFlowData(res.data.flow_data || []);
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
    </div>
  );
}
