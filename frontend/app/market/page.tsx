'use client';

import { useEffect, useState, useCallback } from 'react';
import { TrendingUp, TrendingDown, DollarSign, Activity, Zap, BarChart3, Loader2, AlertCircle, Clock } from 'lucide-react';
import { formatYi, getChangeColor } from '@/lib/api';
import { apiFetch } from '@/lib/api';

interface HotSector {
  code: string;
  name: string;
  main_net_inflow: number;
  change_pct: number;
}

interface MarketOverview {
  market_index: {
    sh_index: number;
    sh_change: number;
    sh_change_pct: number;
    sh_volume: number;
    sh_amount: number;
  };
  north_bound: {
    latest_inflow: number;
    trend: string;
  };
  fund_flow: {
    top_inflow: { name: string; inflow: number }[];
    top_outflow: { name: string; outflow: number }[];
  };
  limit_board: {
    limit_up: number;
    limit_down: number;
  };
  hot_sectors: HotSector[];
}

interface OverviewResponse {
  code: number;
  data: MarketOverview;
}

function generatePlainSummary(data: MarketOverview): string[] {
  const sentences: string[] = [];
  const shChange = data.market_index.sh_change_pct;

  if (shChange > 0.5) {
    sentences.push(
      `上证指数今天上涨 ${shChange.toFixed(2)}%，显示市场整体处于偏强状态，大部分股票价格在上涨。通俗理解就是「今天多数公司都在涨价」。`
    );
  } else if (shChange < -0.5) {
    sentences.push(
      `上证指数今天下跌 ${shChange.toFixed(2).replace('-', '−')}%，市场整体偏弱，多数股票价格在回落。通俗理解就是「今天多数公司都在降价」。`
    );
  } else {
    sentences.push(
      `上证指数今天波动不大（${shChange >= 0 ? '+' : ''}${shChange.toFixed(2)}%），市场整体走势平稳，没有明显的方向性变化。`
    );
  }

  const northFlow = data.north_bound.latest_inflow;
  if (northFlow > 5_0000_0000) {
    sentences.push(
      `北向资金（外资）今天净买了 ${formatYi(northFlow)}，说明外资看好 A 股后市，积极进场。外资买入越多，市场信心越足。`
    );
  } else if (northFlow < -5_0000_0000) {
    sentences.push(
      `北向资金（外资）今天净卖了 ${formatYi(northFlow)}，说明外资今天比较谨慎。短期可能引起市场震荡，但不一定代表长期趋势。`
    );
  } else {
    sentences.push(
      `北向资金今天净流入 ${formatYi(northFlow)}，外资目前比较平静，没有大进大出的动作。`
    );
  }

  const hotNames = data.hot_sectors.map((s) => s.name).slice(0, 2);
  if (hotNames.length > 0) {
    sentences.push(
      `今天资金最活跃的板块是「${hotNames.join('」和「')}」，这些热门板块吸引了最多主力资金，值得持续关注，但要留意追高风险。`
    );
  }

  return sentences;
}

export default function MarketOverviewPage() {
  const [data, setData] = useState<MarketOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isEmpty, setIsEmpty] = useState(false);

  const fetchOverview = useCallback(async () => {
    try {
      const res = await apiFetch<OverviewResponse>('/market/overview');
      if (res.code === 0 && res.data) {
        const d = res.data;
        const hasMarketData = d.market_index && d.market_index.sh_index > 0;
        setData(d);
        setIsEmpty(!hasMarketData);
        setError(null);
      } else {
        setData(null);
        setIsEmpty(true);
      }
    } catch (err) {
      setError('数据加载失败，请检查网络连接后重试');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchOverview();
    const interval = setInterval(fetchOverview, 60000);
    return () => clearInterval(interval);
  }, [fetchOverview]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-text-secondary text-center">
          <Loader2 size={32} className="animate-spin mx-auto mb-3 text-accent" />
          <div className="text-base">正在获取今日市场数据...</div>
          <div className="text-xs mt-1 text-text-secondary/70">请在交易时段查看，数据每 60 秒自动刷新</div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-12">
        <div className="bg-card border border-border rounded-xl p-8 text-center">
          <AlertCircle size={48} className="mx-auto mb-4 text-warn" />
          <h2 className="text-lg font-bold text-text mb-2">数据加载失败</h2>
          <p className="text-text-secondary mb-4">{error}</p>
          <button
            onClick={() => { setLoading(true); setError(null); fetchOverview(); }}
            className="px-4 py-2 bg-accent text-white rounded-lg hover:brightness-110 transition-colors text-sm"
          >
            重新加载
          </button>
        </div>
      </div>
    );
  }

  if (isEmpty || !data) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-12">
        <div className="bg-card border border-border rounded-xl p-8 text-center">
          <Clock size={48} className="mx-auto mb-4 text-text-secondary" />
          <h2 className="text-lg font-bold text-text mb-2">当前非交易时段或数据暂未更新</h2>
          <p className="text-text-secondary mb-1">
            A股交易时间为每周一至周五的上午 9:30-11:30、下午 13:00-15:00。
          </p>
          <p className="text-text-secondary text-sm">
            请在工作日交易时段回来查看，数据将每 60 秒自动刷新。
          </p>
        </div>
      </div>
    );
  }

  const { market_index, north_bound, fund_flow, limit_board, hot_sectors } = data;
  const plainSentences = generatePlainSummary(data);

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text flex items-center gap-2">
          <Activity size={22} className="text-accent" />
          今日速览
        </h1>
        <p className="text-text-secondary text-sm mt-1">一眼看懂今天 A 股发生了什么，数据每 60 秒自动刷新</p>
      </div>

      {/* ── 核心指标卡片 ── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-8">
        {/* 大盘指数卡片 */}
        <div className="bg-card border border-border rounded-xl p-5 hover:border-accent/40 transition-colors">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-9 h-9 rounded-lg bg-[#1F6FEB22] flex items-center justify-center">
              <BarChart3 size={18} className="text-accent" />
            </div>
            <span className="text-sm text-text-secondary">上证指数今天怎么走</span>
          </div>
          <div className="text-2xl font-mono font-bold text-text mb-1">
            {(market_index.sh_index ?? 0).toFixed(2)}
          </div>
          <div className={`flex items-center gap-1 text-sm font-medium ${getChangeColor(market_index.sh_change_pct)}`}>
            {market_index.sh_change_pct >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
            {market_index.sh_change_pct >= 0 ? '+' : ''}{(market_index.sh_change_pct ?? 0).toFixed(2)}%
            {market_index.sh_change != null && (
              <span className="text-text-secondary text-xs ml-1">
                {market_index.sh_change >= 0 ? '+' : ''}{market_index.sh_change.toFixed(2)} 点
              </span>
            )}
          </div>
          <div className="text-xs text-text-secondary mt-2">
            成交额 {(market_index.sh_amount ?? 0) > 0 ? `${((market_index.sh_amount ?? 0) / 1e8).toFixed(0)} 亿` : '暂无'}
          </div>
        </div>

        {/* 北向资金卡片 */}
        <div className="bg-card border border-border rounded-xl p-5 hover:border-accent/40 transition-colors">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-9 h-9 rounded-lg bg-[#EF535022] flex items-center justify-center">
              <DollarSign size={18} className="text-up" />
            </div>
            <span className="text-sm text-text-secondary">北向资金今天买了多少</span>
          </div>
          <div className={`text-2xl font-mono font-bold ${getChangeColor(north_bound.latest_inflow)}`}>
            {formatYi(north_bound.latest_inflow)}
          </div>
          <div className="flex items-center gap-1 mt-2">
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
              north_bound.latest_inflow > 0
                ? 'bg-[#EF535022] text-up'
                : north_bound.latest_inflow < 0
                  ? 'bg-[#26A69A22] text-down'
                  : 'bg-[#21262D] text-text-secondary'
            }`}>
              {north_bound.latest_inflow > 0 ? '外资净买入' : north_bound.latest_inflow < 0 ? '外资净卖出' : '基本持平'}
            </span>
            <span className="text-xs text-text-secondary">{north_bound.trend}</span>
          </div>
        </div>

        {/* 主力资金方向卡片 */}
        <div className="bg-card border border-border rounded-xl p-5 hover:border-accent/40 transition-colors">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-9 h-9 rounded-lg bg-[#D2992222] flex items-center justify-center">
              <Zap size={18} className="text-warn" />
            </div>
            <span className="text-sm text-text-secondary">主力资金在买什么 / 卖什么</span>
          </div>
          <div className="space-y-2">
            <div>
              <div className="text-xs text-up mb-1 font-medium">🟢 资金流入</div>
              <div className="space-y-1">
                {fund_flow.top_inflow.length > 0 ? (
                  fund_flow.top_inflow.slice(0, 3).map((item) => (
                    <div key={item.name} className="flex items-center justify-between text-xs">
                      <span className="text-text truncate flex-1 mr-2">{item.name}</span>
                      <span className="font-mono text-up shrink-0">{formatYi(item.inflow)}</span>
                    </div>
                  ))
                ) : (
                  <div className="text-xs text-text-secondary">暂无数据</div>
                )}
              </div>
            </div>
            <div>
              <div className="text-xs text-down mb-1 font-medium">🔴 资金流出</div>
              <div className="space-y-1">
                {fund_flow.top_outflow.length > 0 ? (
                  fund_flow.top_outflow.slice(0, 3).map((item) => (
                    <div key={item.name} className="flex items-center justify-between text-xs">
                      <span className="text-text truncate flex-1 mr-2">{item.name}</span>
                      <span className="font-mono text-down shrink-0">{formatYi(item.outflow)}</span>
                    </div>
                  ))
                ) : (
                  <div className="text-xs text-text-secondary">暂无数据</div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* 涨跌停统计卡片 */}
        <div className="bg-card border border-border rounded-xl p-5 hover:border-accent/40 transition-colors">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-9 h-9 rounded-lg bg-[#1F6FEB22] flex items-center justify-center">
              <Activity size={18} className="text-accent" />
            </div>
            <span className="text-sm text-text-secondary">今天多少股票涨停 / 跌停</span>
          </div>
          <div className="flex items-center gap-6">
            <div>
              <div className="text-xs text-up mb-1">涨停</div>
              <div className="text-2xl font-mono font-bold text-up">
                {limit_board.limit_up.toLocaleString()}
              </div>
              <div className="text-xs text-text-secondary mt-0.5">只</div>
            </div>
            <div className="w-px h-12 bg-border" />
            <div>
              <div className="text-xs text-down mb-1">跌停</div>
              <div className="text-2xl font-mono font-bold text-down">
                {limit_board.limit_down.toLocaleString()}
              </div>
              <div className="text-xs text-text-secondary mt-0.5">只</div>
            </div>
          </div>
        </div>

        {/* 热门板块卡片 */}
        <div className="bg-card border border-border rounded-xl p-5 sm:col-span-2 lg:col-span-1 hover:border-accent/40 transition-colors">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-9 h-9 rounded-lg bg-[#EF535022] flex items-center justify-center">
              <TrendingUp size={18} className="text-up" />
            </div>
            <span className="text-sm text-text-secondary">哪些板块今天最热门</span>
          </div>
          <div className="space-y-3">
            {hot_sectors.length > 0 ? (
              hot_sectors.slice(0, 3).map((sector, idx) => (
                <div key={sector.code || idx} className="flex items-center justify-between">
                  <span className="text-sm text-text">{sector.name}</span>
                  <span className={`text-xs font-mono ${getChangeColor(sector.main_net_inflow)}`}>
                    {formatYi(sector.main_net_inflow)}
                  </span>
                </div>
              ))
            ) : (
              <div className="text-sm text-text-secondary">暂无热点板块数据</div>
            )}
          </div>
        </div>
      </div>

      {/* ── 小白解读 ── */}
      <div className="bg-card border border-border rounded-xl p-6">
        <h2 className="text-lg font-bold text-text flex items-center gap-2 mb-4">
          <span className="text-xl">💡</span>
          小白解读
        </h2>
        <div className="space-y-3 pl-1">
          {plainSentences.map((sentence, idx) => (
            <div key={idx} className="flex items-start gap-3">
              <span className="text-up font-bold text-sm mt-0.5 shrink-0">
                {idx + 1}.
              </span>
              <p className="text-text-secondary text-sm leading-relaxed">
                {sentence}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
