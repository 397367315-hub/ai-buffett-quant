'use client';

import { useState } from 'react';
import { apiFetch, formatYi } from '@/lib/api';
import { Play, Pause, SkipForward, SkipBack } from 'lucide-react';

export default function ReplayPage() {
  const [selectedDate, setSelectedDate] = useState('');
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const handleSearch = async () => {
    if (!selectedDate) return;
    setLoading(true);
    try {
      const res = await apiFetch<any>(`/flow/concept/rank?limit=100&date=${selectedDate}`);
      setData(res.data.rankings);
    } catch (err) {
      console.error('Failed to fetch replay data:', err);
    } finally {
      setLoading(false);
    }
  };

  const sorted = [...data].sort((a, b) => b.main_net_inflow - a.main_net_inflow);

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      <h1 className="text-2xl font-bold text-text mb-2">历史回放</h1>
      <p className="text-text-secondary mb-6">选择任意交易日，查看当日的资金流向数据</p>

      <div className="bg-card border border-border rounded-lg p-6 mb-6">
        <div className="flex items-center gap-4 mb-4">
          <input
            type="date"
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
            className="bg-[#0D1117] border border-border rounded-md px-4 py-2.5 text-sm text-text focus:outline-none focus:border-accent"
          />
          <button
            onClick={handleSearch}
            disabled={!selectedDate || loading}
            className="flex items-center gap-1.5 px-5 py-2.5 bg-accent text-white text-sm rounded-md hover:opacity-90 disabled:opacity-50 transition-colors"
          >
            <Play size={14} />
            {loading ? '加载中...' : '查看'}
          </button>
        </div>
      </div>

      {data.length > 0 && (
        <div className="bg-card border border-border rounded-lg p-6">
          <h3 className="text-lg font-bold text-text mb-4">{selectedDate} 概念板块资金排名</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-text-secondary border-b border-border">
                  <th className="text-left px-4 py-2 font-medium">排名</th>
                  <th className="text-left px-4 py-2 font-medium">板块</th>
                  <th className="text-right px-4 py-2 font-medium">涨跌幅</th>
                  <th className="text-right px-4 py-2 font-medium">主力净流入</th>
                  <th className="text-right px-4 py-2 font-medium">超大单</th>
                  <th className="text-right px-4 py-2 font-medium">上涨/下跌</th>
                  <th className="text-left px-4 py-2 font-medium">领涨股</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((item) => (
                  <tr key={item.code} className="border-b border-border/50 hover:bg-[#21262D]">
                    <td className="px-4 py-2.5 text-text-secondary">{item.rank}</td>
                    <td className="px-4 py-2.5 font-medium">{item.name}</td>
                    <td className={`px-4 py-2.5 text-right ${item.change_pct >= 0 ? 'text-up' : 'text-down'}`}>
                      {item.change_pct > 0 ? '+' : ''}{item.change_pct?.toFixed(2)}%
                    </td>
                    <td className={`px-4 py-2.5 text-right font-mono ${item.main_net_inflow >= 0 ? 'text-up' : 'text-down'}`}>
                      {formatYi(item.main_net_inflow)}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-text-secondary">{formatYi(item.super_large_net_inflow)}</td>
                    <td className="px-4 py-2.5 text-right text-text-secondary">{item.up_count}/{item.down_count}</td>
                    <td className="px-4 py-2.5 text-text-secondary">{item.leading_stock}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!loading && selectedDate && data.length === 0 && (
        <div className="text-center text-text-secondary py-12">该日期暂无数据。这可能是非交易日或数据暂未入库。</div>
      )}
    </div>
  );
}
