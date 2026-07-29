'use client';

import { useEffect, useState } from 'react';
import { apiFetch, formatYi, getChangeColor } from '@/lib/api';
import { ArrowUpDown, DollarSign, TrendingUp, HelpCircle } from 'lucide-react';

interface BlockTrade {
  code: string;
  name: string;
  date?: string;
  price: number;
  amount: number;
  premium: number;
  volume: number;
  buyer: string;
  seller: string;
}

interface BlockTradeData {
  trades: BlockTrade[];
  summary: {
    total: number;
    total_amount: number;
    premium_count: number;
  };
}

export default function BlockTradePage() {
  const [data, setData] = useState<BlockTradeData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    try {
      const res = await apiFetch<any>('/block-trade/list');
      setData(res.data);
    } catch (err) {
      console.error('Failed to fetch block trade data:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text flex items-center gap-2">
          <ArrowUpDown size={22} className="text-warn" />
          大宗交易监控
        </h1>
        <p className="text-text-secondary text-sm mt-1">
          追踪大宗交易平台成交记录，捕捉机构间筹码交换信号
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
                <ArrowUpDown size={14} />
                交易笔数
              </div>
              <div className="text-xl font-mono font-bold text-text">
                {data.summary.total}笔
              </div>
            </div>
            <div className="bg-card border border-border rounded-lg p-4">
              <div className="flex items-center gap-2 text-xs text-text-secondary mb-1">
                <DollarSign size={14} />
                总成交额
              </div>
              <div className="text-xl font-mono font-bold text-accent">
                {(data.summary.total_amount / 1e8).toFixed(2)}亿
              </div>
            </div>
            <div className="bg-card border border-border rounded-lg p-4">
              <div className="flex items-center gap-2 text-xs text-text-secondary mb-1">
                <TrendingUp size={14} />
                溢价交易数
              </div>
              <div className="text-xl font-mono font-bold text-up">
                {data.summary.premium_count}笔
              </div>
            </div>
          </div>

          {/* 交易列表 */}
          <div className="bg-card border border-border rounded-lg overflow-hidden mb-6">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-text-secondary border-b border-border bg-[#0D1117]">
                    <th className="text-left px-4 py-3 font-medium">股票名称/代码</th>
                    <th className="text-right px-3 py-3 font-medium">成交价</th>
                    <th className="text-right px-3 py-3 font-medium">成交额</th>
                    <th className="text-right px-3 py-3 font-medium">溢价率</th>
                    <th className="text-right px-3 py-3 font-medium hidden md:table-cell">成交量</th>
                    <th className="text-left px-3 py-3 font-medium hidden lg:table-cell">买方</th>
                    <th className="text-left px-3 py-3 font-medium hidden lg:table-cell">卖方</th>
                  </tr>
                </thead>
                <tbody>
                  {data.trades.map((t, i) => (
                    <tr key={`${t.code}-${i}`} className="border-b border-border/50 hover:bg-[#21262D] transition-colors">
                      <td className="px-4 py-2.5">
                        <div className="font-medium text-text">{t.name}</div>
                        <div className="text-xs text-text-secondary">{t.code}</div>
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-text">{t.price}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-text-secondary">
                        {(t.amount / 1e8).toFixed(2)}亿
                      </td>
                      <td className={`px-3 py-2.5 text-right font-mono font-bold ${t.premium > 0 ? 'text-up' : 'text-down'}`}>
                        {t.premium > 0 ? '+' : ''}{t.premium}%
                      </td>
                      <td className="px-3 py-2.5 text-right text-text-secondary hidden md:table-cell">
                        {(t.volume / 1e4).toFixed(1)}万股
                      </td>
                      <td className="px-3 py-2.5 text-text-secondary text-xs hidden lg:table-cell">{t.buyer || '--'}</td>
                      <td className="px-3 py-2.5 text-text-secondary text-xs hidden lg:table-cell">{t.seller || '--'}</td>
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
              小白解读：什么是大宗交易？
            </h3>
            <div className="text-text-secondary text-sm leading-relaxed space-y-3">
              <p>
                <strong className="text-text">大宗交易</strong>是指单笔交易达到规定数量或金额（A股单笔交易数量不低于30万股或金额不低于200万元）的证券买卖，通过<span className="text-warn">交易所大宗交易平台</span>完成。这类交易不直接冲击二级市场股价，但传递了重要信号。
              </p>
              <p>
                <strong className="text-text">溢价率为什么重要：</strong>
              </p>
              <ul className="list-disc list-inside space-y-1 pl-2">
                <li>
                  <span className="text-up font-bold">溢价成交（溢价率 {'>'} 0）</span>：买方愿意以高于市价的价格大量买入，说明<span className="text-up">非常看好</span>该股票后续走势。可能是机构提前布局，值得重点关注。
                </li>
                <li>
                  <span className="text-down font-bold">折价成交（溢价率 {'<'} 0）</span>：卖方愿意以低于市价的价格大量卖出，可能是大股东减持套现或机构调仓换股，<span className="text-down">需警惕短期回调风险</span>。
                </li>
                <li>
                  <span className="text-text font-bold">平价成交（溢价率 = 0）</span>：按市价协议转让，通常是大股东之间的股权转让或机构对倒，信号意义相对中性。
                </li>
              </ul>
              <p className="text-xs text-text-secondary mt-3">
                提示：大宗交易数据按日更新。结合龙虎榜和资金流向综合分析效果更佳。
              </p>
            </div>
          </div>
        </>
      ) : (
        <div className="bg-[#D2992222] border border-[#D2992255] rounded-lg p-6 text-center">
          <p className="text-warn text-sm">暂无大宗交易数据。</p>
        </div>
      )}
    </div>
  );
}
