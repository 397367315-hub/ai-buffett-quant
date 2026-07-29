'use client';

import { useEffect, useState } from 'react';
import { apiFetch, formatYi, getChangeColor } from '@/lib/api';
import { TrendingUp, Users, DollarSign, HelpCircle } from 'lucide-react';

interface DragonStock {
  code: string;
  name: string;
  price: string;
  change_pct: string;
  turnover: string;
  pe: string;
  main_net_inflow: string;
  super_large_net_inflow: string;
  large_net_inflow: string;
  market_cap: string;
}

interface DragonBoardData {
  stocks: DragonStock[];
  summary: {
    total: number;
    institution_active: number;
    total_main_inflow: number;
  };
}

export default function DragonBoardPage() {
  const [data, setData] = useState<DragonBoardData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    try {
      const res = await apiFetch<any>('/dragon/board');
      setData(res.data);
    } catch (err) {
      console.error('Failed to fetch dragon board data:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, 60000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text flex items-center gap-2">
          <TrendingUp size={22} className="text-warn" />
          龙虎榜分析
        </h1>
        <p className="text-text-secondary text-sm mt-1">
          追踪每日龙虎榜数据，洞察主力资金动向
        </p>
      </div>

      {loading ? (
        <div className="text-center text-text-secondary py-12">
          <div className="animate-spin w-8 h-8 border-2 border-accent border-t-transparent rounded-full mx-auto mb-3" />
          加载中...
        </div>
      ) : data ? (
        <>
          {/* 概览卡片 */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            <div className="bg-card border border-border rounded-lg p-4">
              <div className="flex items-center gap-2 text-xs text-text-secondary mb-1">
                <TrendingUp size={14} />
                上榜股票总数
              </div>
              <div className="text-xl font-mono font-bold text-text">
                {data.summary.total}只
              </div>
            </div>
            <div className="bg-card border border-border rounded-lg p-4">
              <div className="flex items-center gap-2 text-xs text-text-secondary mb-1">
                <Users size={14} />
                机构活跃数
              </div>
              <div className="text-xl font-mono font-bold text-accent">
                {data.summary.institution_active}家
              </div>
            </div>
            <div className="bg-card border border-border rounded-lg p-4">
              <div className="flex items-center gap-2 text-xs text-text-secondary mb-1">
                <DollarSign size={14} />
                总主力净流入
              </div>
              <div className={`text-xl font-mono font-bold ${getChangeColor(data.summary.total_main_inflow)}`}>
                {(data.summary.total_main_inflow / 1e8).toFixed(2)}亿
              </div>
            </div>
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
                    <th className="text-right px-3 py-3 font-medium">主力净流入</th>
                    <th className="text-right px-3 py-3 font-medium hidden md:table-cell">超大单净流入</th>
                    <th className="text-right px-3 py-3 font-medium hidden lg:table-cell">大单净流入</th>
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
                      <td className={`px-3 py-2.5 text-right font-mono ${getChangeColor(parseFloat(s.main_net_inflow || '0'))}`}>
                        {(parseFloat(s.main_net_inflow || '0') / 1e8).toFixed(2)}亿
                      </td>
                      <td className={`px-3 py-2.5 text-right font-mono hidden md:table-cell ${getChangeColor(parseFloat(s.super_large_net_inflow || '0'))}`}>
                        {(parseFloat(s.super_large_net_inflow || '0') / 1e8).toFixed(2)}亿
                      </td>
                      <td className={`px-3 py-2.5 text-right font-mono hidden lg:table-cell ${getChangeColor(parseFloat(s.large_net_inflow || '0'))}`}>
                        {(parseFloat(s.large_net_inflow || '0') / 1e8).toFixed(2)}亿
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
              小白解读：什么是龙虎榜？
            </h3>
            <div className="text-text-secondary text-sm leading-relaxed space-y-3">
              <p>
                <strong className="text-text">龙虎榜</strong>是沪深交易所每日公布的当日涨跌幅偏离值达7%、换手率达20%、连续3日涨跌幅偏离值累计达20%等异常波动股票的买卖席位数据。简单说，就是<span className="text-warn">当天最活跃、最受关注的股票榜单</span>。
              </p>
              <p>
                <strong className="text-text">怎么看：</strong>
              </p>
              <ul className="list-disc list-inside space-y-1 pl-2">
                <li><span className="text-up font-bold">主力净流入</span>：该股当日主力资金（机构+游资）的净买卖金额。正值越大，说明主力越看好。</li>
                <li><span className="text-accent font-bold">超大单净流入</span>：单笔成交超过100万元的大资金动向，通常代表机构行为。</li>
                <li><span className="text-text font-bold">换手率</span>：当日成交股数占总股本的比例。换手率高（{'>'}10%）说明筹码交换活跃，短线机会多但风险也大。</li>
                <li><span className="text-text font-bold">PE（市盈率）</span>：股价与每股收益的比值。PE越低通常估值越便宜，但要结合行业平均来看。</li>
              </ul>
              <p className="text-xs text-text-secondary mt-3">
                提示：龙虎榜数据每60秒自动刷新。上榜不等于推荐买入，需结合基本面和技术面综合判断。
              </p>
            </div>
          </div>
        </>
      ) : (
        <div className="bg-[#D2992222] border border-[#D2992255] rounded-lg p-6 text-center">
          <p className="text-warn text-sm">当前非交易时段，龙虎榜数据为空。交易时段自动获取。</p>
        </div>
      )}
    </div>
  );
}
