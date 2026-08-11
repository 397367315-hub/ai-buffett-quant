'use client';

import { useEffect, useState } from 'react';
import { apiFetch, formatYi, getChangeColor } from '@/lib/api';
import { TrendingUp, TrendingDown, Zap, AlertTriangle } from 'lucide-react';
import StockKlineButton from '@/components/StockKlineButton';

interface LimitStock {
  code: string;
  name: string;
  price: string;
  change_pct: string;
  volume: string;
  amount: string;
  turnover: string;
  pe: string;
  market_cap: string;
  limit_status?: string;
  continuous_days?: string;
  sector: string;
  main_net_inflow: string;
}

export default function LimitBoardPage() {
  const [activeTab, setActiveTab] = useState<'up' | 'down'>('up');
  const [upStocks, setUpStocks] = useState<LimitStock[]>([]);
  const [downStocks, setDownStocks] = useState<LimitStock[]>([]);
  const [upStats, setUpStats] = useState<any>(null);
  const [downStats, setDownStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [upRes, downRes] = await Promise.all([
        apiFetch<any>('/flow/limit-up'),
        apiFetch<any>('/flow/limit-down'),
      ]);
      setUpStocks(upRes.data.stocks || []);
      setUpStats(upRes.data.stats);
      setDownStocks(downRes.data.stocks || []);
      setDownStats(downRes.data.stats);
    } catch (err) {
      console.error('Failed to fetch limit data:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, 60000);
    return () => clearInterval(timer);
  }, []);

  const stocks = activeTab === 'up' ? upStocks : downStocks;
  const stats = activeTab === 'up' ? upStats : downStats;

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text flex items-center gap-2">
          <Zap size={22} className="text-warn" />
          涨跌停板看板
        </h1>
        <p className="text-text-secondary text-sm mt-1">
          实时监控涨停/跌停股票，把握市场极端情绪
        </p>
      </div>

      {/* 切换标签 */}
      <div className="flex bg-[#0D1117] border border-border rounded-lg overflow-hidden mb-6 w-fit">
        <button
          className={`flex items-center gap-1.5 px-4 py-2 text-sm transition-colors ${
            activeTab === 'up' ? 'bg-up text-white' : 'text-text-secondary hover:text-text'
          }`}
          onClick={() => setActiveTab('up')}
        >
          <TrendingUp size={14} />
          涨停板 ({upStocks.length})
        </button>
        <button
          className={`flex items-center gap-1.5 px-4 py-2 text-sm transition-colors ${
            activeTab === 'down' ? 'bg-down text-white' : 'text-text-secondary hover:text-text'
          }`}
          onClick={() => setActiveTab('down')}
        >
          <TrendingDown size={14} />
          跌停板 ({downStocks.length})
        </button>
      </div>

      {/* 统计概览 */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="text-xs text-text-secondary mb-1">
              {activeTab === 'up' ? '涨停数量' : '跌停数量'}
            </div>
            <div className={`text-xl font-mono font-bold ${activeTab === 'up' ? 'text-up' : 'text-down'}`}>
              {stats.total}只
            </div>
          </div>
          {activeTab === 'up' && (
            <div className="bg-card border border-border rounded-lg p-4">
              <div className="text-xs text-text-secondary mb-1">连板数量</div>
              <div className="text-xl font-mono font-bold text-warn">{stats.continuous_boards}只</div>
            </div>
          )}
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="text-xs text-text-secondary mb-1">涉及板块</div>
            <div className="text-xl font-mono font-bold text-text">
              {Object.keys(stats.by_sector || {}).length}个
            </div>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="text-xs text-text-secondary mb-1">实时刷新</div>
            <div className="text-sm text-accent">每60秒自动</div>
          </div>
        </div>
      )}

      {/* 板块分布 */}
      {stats && stats.by_sector && Object.keys(stats.by_sector).length > 0 && (
        <div className="bg-card border border-border rounded-lg p-4 mb-6">
          <h3 className="text-sm font-bold text-text mb-3">板块分布</h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(stats.by_sector as Record<string, number>)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 15)
              .map(([sector, count]) => (
                <span
                  key={sector}
                  className={`px-2.5 py-1 text-xs rounded-full ${
                    count >= 5
                      ? 'bg-[#26A69A22] text-down border border-[#26A69A44]'
                      : count >= 3
                      ? 'bg-[#D2992222] text-warn border border-[#D2992244]'
                      : 'bg-[#21262D] text-text-secondary border border-border'
                  }`}
                >
                  {sector} ×{count}
                </span>
              ))}
          </div>
        </div>
      )}

      {/* 股票列表 */}
      {loading ? (
        <div className="text-center text-text-secondary py-12">
          <div className="animate-spin w-8 h-8 border-2 border-accent border-t-transparent rounded-full mx-auto mb-3" />
          加载中...
        </div>
      ) : stocks.length === 0 ? (
        <div className="bg-[#D2992222] border border-[#D2992255] rounded-lg p-6 text-center">
          <AlertTriangle size={24} className="text-warn mx-auto mb-2" />
          <p className="text-warn text-sm">
            {activeTab === 'up'
              ? '当前非交易时段，涨停数据为空。交易时段自动获取。'
              : '当前非交易时段或今日无跌停股票。'}
          </p>
        </div>
      ) : (
        <div className="bg-card border border-border rounded-lg overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-text-secondary border-b border-border bg-[#0D1117]">
                  <th className="text-left px-4 py-3 font-medium">股票</th>
                  <th className="text-right px-3 py-3 font-medium">现价</th>
                  <th className="text-right px-3 py-3 font-medium">涨跌幅</th>
                  <th className="text-right px-3 py-3 font-medium">换手率</th>
                  <th className="text-right px-3 py-3 font-medium hidden md:table-cell">成交额</th>
                  <th className="text-right px-3 py-3 font-medium hidden lg:table-cell">PE</th>
                  <th className="text-right px-3 py-3 font-medium hidden md:table-cell">主力净流入</th>
                  {activeTab === 'up' && (
                    <th className="text-left px-3 py-3 font-medium hidden sm:table-cell">连板</th>
                  )}
                  <th className="text-left px-3 py-3 font-medium hidden lg:table-cell">板块</th>
                </tr>
              </thead>
              <tbody>
                {stocks.map((s, i) => (
                  <tr key={s.code} className="border-b border-border/50 hover:bg-[#21262D] transition-colors">
                    <td className="px-4 py-2.5">
                      <StockKlineButton code={s.code} name={s.name} className="font-medium text-text">{s.name}</StockKlineButton>
                      <div className="text-xs text-text-secondary">{s.code}</div>
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-text">{s.price}</td>
                    <td className={`px-3 py-2.5 text-right font-mono font-bold ${getChangeColor(parseFloat(s.change_pct))}`}>
                      {parseFloat(s.change_pct) > 0 ? '+' : ''}{s.change_pct}%
                    </td>
                    <td className="px-3 py-2.5 text-right text-text-secondary">{s.turnover}%</td>
                    <td className="px-3 py-2.5 text-right text-text-secondary hidden md:table-cell">
                      {(parseFloat(s.amount) / 1e8).toFixed(1)}亿
                    </td>
                    <td className="px-3 py-2.5 text-right text-text-secondary hidden lg:table-cell">{s.pe || '--'}</td>
                    <td className={`px-3 py-2.5 text-right font-mono hidden md:table-cell ${getChangeColor(parseFloat(s.main_net_inflow || '0'))}`}>
                      {(parseFloat(s.main_net_inflow || '0') / 1e8).toFixed(2)}亿
                    </td>
                    {activeTab === 'up' && (
                      <td className="px-3 py-2.5 hidden sm:table-cell">
                        {s.continuous_days && parseInt(s.continuous_days) >= 2 ? (
                          <span className="px-1.5 py-0.5 text-xs rounded bg-[#D2992222] text-warn font-bold">
                            {s.continuous_days}板
                          </span>
                        ) : (
                          <span className="text-text-secondary">首板</span>
                        )}
                      </td>
                    )}
                    <td className="px-3 py-2.5 text-text-secondary text-xs hidden lg:table-cell">{s.sector || '--'}</td>
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
