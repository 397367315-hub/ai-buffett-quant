'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch, formatYi, getChangeColor } from '@/lib/api';
import { Filter, Search, TrendingUp, HelpCircle } from 'lucide-react';

interface ScreenerStock {
  code: string;
  name: string;
  price: string;
  change_pct: string;
  turnover: string;
  pe: string;
  pb: string;
  roe: string;
  volume_ratio: string;
  main_net_inflow: string;
  market_cap: string;
}

interface ScreenerData {
  stocks: ScreenerStock[];
  total: number;
}

export default function ScreenerPage() {
  const [data, setData] = useState<ScreenerData | null>(null);
  const [loading, setLoading] = useState(true);

  const [minChange, setMinChange] = useState(2);
  const [maxPe, setMaxPe] = useState(100);
  const [minTurnover, setMinTurnover] = useState(3);

  const fetchData = useCallback(async (change: number, pe: number, turnover: number) => {
    setLoading(true);
    try {
      const res = await apiFetch<any>(
        `/screener/technical?min_change=${change}&max_pe=${pe}&min_turnover=${turnover}`
      );
      setData(res.data);
    } catch (err) {
      console.error('Failed to fetch screener data:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData(minChange, maxPe, minTurnover);
  }, []);

  const handleScreen = () => {
    fetchData(minChange, maxPe, minTurnover);
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text flex items-center gap-2">
          <Filter size={22} className="text-warn" />
          技术面筛选器
        </h1>
        <p className="text-text-secondary text-sm mt-1">
          多维条件筛选潜力标的，发现放量突破机会
        </p>
      </div>

      {/* 筛选条件 */}
      <div className="bg-card border border-border rounded-lg p-6 mb-6">
        <h3 className="text-sm font-bold text-text mb-4 flex items-center gap-2">
          <Search size={14} className="text-accent" />
          筛选条件
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
          <div>
            <label className="block text-xs text-text-secondary mb-1.5">
              最小涨幅: <span className="text-warn font-bold">{minChange}%</span>
            </label>
            <input
              type="range"
              min={1}
              max={10}
              step={0.5}
              value={minChange}
              onChange={(e) => setMinChange(parseFloat(e.target.value))}
              className="w-full h-2 bg-[#0D1117] rounded-lg appearance-none cursor-pointer accent-accent"
            />
            <div className="flex justify-between text-xs text-text-secondary mt-0.5">
              <span>1%</span>
              <span>10%</span>
            </div>
          </div>

          <div>
            <label className="block text-xs text-text-secondary mb-1.5">
              最大PE: <span className="text-warn font-bold">{maxPe}</span>
            </label>
            <input
              type="number"
              min={50}
              max={200}
              value={maxPe}
              onChange={(e) => setMaxPe(parseInt(e.target.value) || 100)}
              className="w-full bg-[#0D1117] border border-border rounded-md px-3 py-2 text-sm text-text focus:outline-none focus:border-accent"
              placeholder="50-200"
            />
            <div className="flex justify-between text-xs text-text-secondary mt-0.5">
              <span>50</span>
              <span>200</span>
            </div>
          </div>

          <div>
            <label className="block text-xs text-text-secondary mb-1.5">
              最小换手率: <span className="text-warn font-bold">{minTurnover}%</span>
            </label>
            <input
              type="range"
              min={1}
              max={20}
              step={0.5}
              value={minTurnover}
              onChange={(e) => setMinTurnover(parseFloat(e.target.value))}
              className="w-full h-2 bg-[#0D1117] rounded-lg appearance-none cursor-pointer accent-accent"
            />
            <div className="flex justify-between text-xs text-text-secondary mt-0.5">
              <span>1%</span>
              <span>20%</span>
            </div>
          </div>

          <div>
            <button
              onClick={handleScreen}
              disabled={loading}
              className="w-full px-4 py-2 bg-accent text-white text-sm rounded-md hover:opacity-90 disabled:opacity-50 transition-colors flex items-center justify-center gap-1.5"
            >
              <Search size={14} />
              {loading ? '筛选中...' : '筛选'}
            </button>
          </div>
        </div>
      </div>

      {/* 结果 */}
      {loading ? (
        <div className="text-center text-text-secondary py-12">
          <div className="animate-spin w-8 h-8 border-2 border-accent border-t-transparent rounded-full mx-auto mb-3" />
          加载中...
        </div>
      ) : data ? (
        <>
          {/* 结果计数 */}
          <div className="flex items-center gap-2 mb-4">
            <TrendingUp size={16} className="text-accent" />
            <span className="text-sm text-text">
              符合条件的股票共 <span className="text-accent font-bold">{data.total}</span> 只
            </span>
          </div>

          {/* 股票列表 */}
          <div className="bg-card border border-border rounded-lg overflow-hidden mb-6">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-text-secondary border-b border-border bg-[#0D1117]">
                    <th className="text-left px-4 py-3 font-medium">股票名称/代码</th>
                    <th className="text-right px-3 py-3 font-medium">现价</th>
                    <th className="text-right px-3 py-3 font-medium">涨跌幅</th>
                    <th className="text-right px-3 py-3 font-medium hidden md:table-cell">换手率</th>
                    <th className="text-right px-3 py-3 font-medium hidden lg:table-cell">PE</th>
                    <th className="text-right px-3 py-3 font-medium hidden lg:table-cell">PB</th>
                    <th className="text-right px-3 py-3 font-medium hidden xl:table-cell">ROE</th>
                    <th className="text-right px-3 py-3 font-medium hidden md:table-cell">量比</th>
                    <th className="text-right px-3 py-3 font-medium">主力净流入</th>
                    <th className="text-right px-3 py-3 font-medium hidden lg:table-cell">市值</th>
                  </tr>
                </thead>
                <tbody>
                  {data.stocks.map((s) => (
                    <tr key={s.code} className="border-b border-border/50 hover:bg-[#21262D] transition-colors">
                      <td className="px-4 py-2.5">
                        <div className="font-medium text-text">{s.name}</div>
                        <div className="text-xs text-text-secondary">{s.code}</div>
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-text">{s.price}</td>
                      <td className={`px-3 py-2.5 text-right font-mono font-bold ${getChangeColor(parseFloat(s.change_pct || '0'))}`}>
                        {parseFloat(s.change_pct || '0') > 0 ? '+' : ''}{s.change_pct}%
                      </td>
                      <td className="px-3 py-2.5 text-right text-text-secondary hidden md:table-cell">{s.turnover}%</td>
                      <td className="px-3 py-2.5 text-right text-text-secondary hidden lg:table-cell">{s.pe || '--'}</td>
                      <td className="px-3 py-2.5 text-right text-text-secondary hidden lg:table-cell">{s.pb || '--'}</td>
                      <td className="px-3 py-2.5 text-right text-xs hidden xl:table-cell">
                        {s.roe ? (
                          <span className={parseFloat(s.roe) >= 15 ? 'text-up' : parseFloat(s.roe) >= 8 ? 'text-text-secondary' : 'text-down'}>
                            {s.roe}%
                          </span>
                        ) : '--'}
                      </td>
                      <td className="px-3 py-2.5 text-right hidden md:table-cell">
                        {s.volume_ratio ? (
                          <span className={parseFloat(s.volume_ratio) > 1.5 ? 'text-up' : 'text-text-secondary'}>
                            {s.volume_ratio}
                          </span>
                        ) : '--'}
                      </td>
                      <td className={`px-3 py-2.5 text-right font-mono ${getChangeColor(parseFloat(s.main_net_inflow || '0'))}`}>
                        {(parseFloat(s.main_net_inflow || '0') / 1e8).toFixed(2)}亿
                      </td>
                      <td className="px-3 py-2.5 text-right text-text-secondary hidden lg:table-cell">
                        {(parseFloat(s.market_cap || '0') / 1e8).toFixed(0)}亿
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* 小白解读 */}
          <div className="bg-card border border-border rounded-lg p-6">
            <h3 className="text-base font-bold text-text mb-3 flex items-center gap-2">
              <HelpCircle size={16} className="text-accent" />
              小白解读：什么是"放量突破"？
            </h3>
            <div className="text-text-secondary text-sm leading-relaxed space-y-3">
              <p>
                <strong className="text-text">放量突破</strong>是技术分析中的重要概念，指股价在成交量明显放大的配合下，突破前期压力位（如均线、前高、箱体上沿等）。<span className="text-warn">"量在价先"</span>——成交量的放大往往是股价大幅变动的先行指标。
              </p>
              <p>
                <strong className="text-text">筛选器怎么用：</strong>
              </p>
              <ul className="list-disc list-inside space-y-1 pl-2">
                <li>
                  <span className="text-accent font-bold">最小涨幅</span>：设定当日涨幅下限。涨幅越大说明短期动能越强。建议从2-3%起步，避免关注弱势震荡股。
                </li>
                <li>
                  <span className="text-accent font-bold">最大PE</span>：设定市盈率上限，过滤估值过高的标的。PE在15-50之间通常为合理区间，超过100需谨慎。
                </li>
                <li>
                  <span className="text-accent font-bold">最小换手率</span>：设定换手率下限。换手率在3%-7%说明交易活跃但不过热，是放量突破的理想换手区间。
                </li>
                <li>
                  <span className="text-text font-bold">量比</span>：当日成交量与5日均量的比值。量比 {'>'} 1.5 即为放量，量比 {'>'} 2 为显著放量。
                </li>
              </ul>
              <p className="text-xs text-text-secondary mt-3">
                提示：筛选结果仅为技术面参考，不构成投资建议。建议结合基本面分析、行业趋势、大盘环境综合判断。
              </p>
            </div>
          </div>
        </>
      ) : (
        <div className="bg-[#D2992222] border border-[#D2992255] rounded-lg p-6 text-center">
          <p className="text-warn text-sm">暂无符合条件的股票数据。</p>
        </div>
      )}
    </div>
  );
}
