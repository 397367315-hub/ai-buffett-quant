'use client';

import { useEffect, useState } from 'react';
import { apiFetch, formatYi, formatYiShort, getChangeColor } from '@/lib/api';
import {
  TrendingUp, TrendingDown, Flame, BarChart3, HelpCircle, ArrowUp, ArrowDown, Zap
} from 'lucide-react';

interface SectorItem {
  code: string;
  name: string;
  change_pct: number;
  main_net_inflow: number;
  super_large_inflow: number;
  large_inflow: number;
  up_count: number;
  down_count: number;
}

interface RotationData {
  sectors: SectorItem[];
  hot_inflow: SectorItem[];
  hot_outflow: SectorItem[];
  hot_gainers: SectorItem[];
}

export default function RotationPage() {
  const [data, setData] = useState<RotationData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    try {
      const res = await apiFetch<any>('/flow/rotation');
      setData(res.data);
    } catch (err) {
      console.error('Failed to fetch rotation data:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, 60000);
    return () => clearInterval(timer);
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-text-secondary text-center">
          <div className="animate-spin w-8 h-8 border-2 border-accent border-t-transparent rounded-full mx-auto mb-3" />
          <span>数据加载中...</span>
        </div>
      </div>
    );
  }

  if (!data || !data.sectors || data.sectors.length === 0) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-text-secondary text-center">
          <BarChart3 size={24} className="mx-auto mb-2 opacity-50" />
          <span>暂无板块轮动数据</span>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text flex items-center gap-2">
          <Zap size={22} className="text-warn" />
          行业轮动追踪
        </h1>
        <p className="text-text-secondary text-sm mt-1">
          追踪主力资金在板块间的流向变化，发现资金轮动规律
        </p>
      </div>

      {/* 资金流入 TOP5 */}
      {data.hot_inflow && data.hot_inflow.length > 0 && (
        <div className="mb-6">
          <h3 className="text-lg font-bold text-up mb-3 flex items-center gap-2">
            <ArrowUp size={18} />
            🔥 资金流入 TOP5
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
            {data.hot_inflow.map((item, i) => (
              <div
                key={item.code}
                className="bg-card border border-border rounded-lg p-4 hover:border-[#EF535044] transition-colors"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-text-secondary font-mono">#{i + 1}</span>
                  <span className={`text-xs font-bold ${item.change_pct >= 0 ? 'text-up' : 'text-down'}`}>
                    {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                  </span>
                </div>
                <div className="text-sm font-bold text-text mb-1.5 truncate" title={item.name}>
                  {item.name}
                </div>
                <div className="text-base font-mono font-bold text-up">
                  {formatYiShort(item.main_net_inflow)}
                </div>
                <div className="text-xs text-text-secondary mt-1">
                  涨{item.up_count} / 跌{item.down_count}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 资金流出 TOP5 */}
      {data.hot_outflow && data.hot_outflow.length > 0 && (
        <div className="mb-6">
          <h3 className="text-lg font-bold text-down mb-3 flex items-center gap-2">
            <ArrowDown size={18} />
            🧊 资金流出 TOP5
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
            {data.hot_outflow.map((item, i) => (
              <div
                key={item.code}
                className="bg-card border border-[#26A69A44] rounded-lg p-4 hover:border-[#26A69A77] transition-colors"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-text-secondary font-mono">#{i + 1}</span>
                  <span className={`text-xs font-bold ${item.change_pct >= 0 ? 'text-up' : 'text-down'}`}>
                    {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                  </span>
                </div>
                <div className="text-sm font-bold text-text mb-1.5 truncate" title={item.name}>
                  {item.name}
                </div>
                <div className="text-base font-mono font-bold text-down">
                  {formatYiShort(item.main_net_inflow)}
                </div>
                <div className="text-xs text-text-secondary mt-1">
                  涨{item.up_count} / 跌{item.down_count}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 涨幅 TOP5 */}
      {data.hot_gainers && data.hot_gainers.length > 0 && (
        <div className="mb-6">
          <h3 className="text-lg font-bold text-warn mb-3 flex items-center gap-2">
            <Flame size={18} />
            📈 涨幅 TOP5
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
            {data.hot_gainers.map((item, i) => (
              <div
                key={item.code}
                className="bg-card border border-border rounded-lg p-4 hover:border-[#D2992244] transition-colors"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-text-secondary font-mono">#{i + 1}</span>
                  <span className={`text-xs font-bold ${item.change_pct >= 0 ? 'text-up' : 'text-down'}`}>
                    {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                  </span>
                </div>
                <div className="text-sm font-bold text-text mb-1.5 truncate" title={item.name}>
                  {item.name}
                </div>
                <div className={`text-base font-mono font-bold ${getChangeColor(item.main_net_inflow)}`}>
                  {formatYiShort(item.main_net_inflow)}
                </div>
                <div className="text-xs text-text-secondary mt-1">
                  涨{item.up_count} / 跌{item.down_count}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 全板块表格 */}
      <div className="bg-card border border-border rounded-lg overflow-hidden mb-6">
        <div className="px-6 py-4 border-b border-border">
          <h3 className="text-lg font-bold text-text">全板块资金流向</h3>
          <p className="text-xs text-text-secondary mt-0.5">
            共 {data.sectors.length} 个板块 · 按主力净流入排序
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-text-secondary border-b border-border bg-[#0D1117]">
                <th className="text-left px-4 py-3 font-medium">板块</th>
                <th className="text-right px-3 py-3 font-medium">涨跌幅</th>
                <th className="text-right px-3 py-3 font-medium">主力净流入</th>
                <th className="text-right px-3 py-3 font-medium hidden md:table-cell">超大单</th>
                <th className="text-right px-3 py-3 font-medium hidden md:table-cell">大单</th>
                <th className="text-right px-3 py-3 font-medium hidden sm:table-cell">涨跌家数</th>
              </tr>
            </thead>
            <tbody>
              {[...data.sectors]
                .sort((a, b) => b.main_net_inflow - a.main_net_inflow)
                .map((item) => (
                  <tr key={item.code} className="border-b border-border/50 hover:bg-[#21262D] transition-colors">
                    <td className="px-4 py-2.5">
                      <div className="font-medium text-text">{item.name}</div>
                      <div className="text-xs text-text-secondary">{item.code}</div>
                    </td>
                    <td className={`px-3 py-2.5 text-right font-mono ${getChangeColor(item.change_pct)}`}>
                      {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                    </td>
                    <td className={`px-3 py-2.5 text-right font-mono font-medium ${getChangeColor(item.main_net_inflow)}`}>
                      {formatYiShort(item.main_net_inflow)}
                    </td>
                    <td className={`px-3 py-2.5 text-right font-mono text-text-secondary hidden md:table-cell ${getChangeColor(item.super_large_inflow)}`}>
                      {formatYiShort(item.super_large_inflow)}
                    </td>
                    <td className={`px-3 py-2.5 text-right font-mono text-text-secondary hidden md:table-cell ${getChangeColor(item.large_inflow)}`}>
                      {formatYiShort(item.large_inflow)}
                    </td>
                    <td className="px-3 py-2.5 text-right text-text-secondary hidden sm:table-cell">
                      <span className="text-up">{item.up_count}</span>
                      <span className="mx-0.5">/</span>
                      <span className="text-down">{item.down_count}</span>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 小白解读 */}
      <div className="bg-card border border-border rounded-lg p-6">
        <h3 className="text-lg font-bold text-text mb-3 flex items-center gap-2">
          <HelpCircle size={18} className="text-accent" />
          小白解读
        </h3>
        <div className="text-sm text-text-secondary leading-relaxed space-y-2">
          <p>
            <strong className="text-text">行业轮动</strong>是指主力资金在不同行业板块之间
            <strong className="text-text">有规律地进出</strong>，形成板块"你方唱罢我登场"的格局。
          </p>
          <p>
            <span className="text-up">资金流入板块</span>：主力正在建仓或加仓的板块，短期可能继续走强。
            关注是否有<span className="text-warn">持续性</span>（连续多日流入）。
          </p>
          <p>
            <span className="text-down">资金流出板块</span>：主力正在减仓的板块，短期注意回避。
            可能是获利了结，也可能是调仓换股。
          </p>
          <p>
            <span className="text-accent">涨幅板块 vs 资金板块</span>：
            有时候涨幅最好的板块不是资金流入最多的，这可能是游资炒作而非主力建仓，
            需要区分<strong className="text-text">"真金白银"</strong>和<strong className="text-warn">"虚火"</strong>。
          </p>
          <p className="text-xs text-text-secondary mt-2 pt-2 border-t border-border">
            💡 提示：关注资金持续流入的方向，结合板块涨幅和技术形态综合判断。
          </p>
        </div>
      </div>
    </div>
  );
}
