'use client';

import { useEffect, useState, useCallback } from 'react';
import { TrendingUp, TrendingDown, DollarSign, Activity, Zap, BarChart3, Loader2, AlertCircle, Clock, RefreshCw } from 'lucide-react';
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
    sh_index: number | null;
    sh_change: number | null;
    sh_change_pct: number | null;
    sh_volume: number | null;
    sh_amount: number | null;
  };
  north_bound: {
    latest_deal_amount: number | null;
    latest_inflow: number | null;
    net_inflow_available: boolean;
  };
  fund_flow: {
    top_inflow: { name: string; inflow: number }[];
    top_outflow: { name: string; outflow: number }[];
  };
  limit_board: {
    limit_up: number | null;
    limit_down: number | null;
  };
  hot_sectors: HotSector[];
  source?: string;
  data_date?: string | null;
  source_updated_at?: string | null;
  snapshot_saved_at?: string | null;
  is_realtime?: boolean;
  cache_used?: boolean;
}

interface OverviewResponse {
  code: number;
  data: MarketOverview;
}

function generatePlainSummary(data: MarketOverview): string[] {
  const sentences: string[] = [];
  const shChange = data.market_index.sh_change_pct;

  if (shChange == null) {
    sentences.push('当前有效快照没有返回上证指数涨跌幅，系统保留为空，不根据指数点位推算。');
  } else if (shChange > 0.5) {
    sentences.push(
      `上证指数今天上涨 ${shChange.toFixed(2)}%，大盘整体偏强。`
    );
  } else if (shChange < -0.5) {
    sentences.push(
      `上证指数今天下跌 ${shChange.toFixed(2).replace('-', '−')}%，大盘整体偏弱。`
    );
  } else {
    sentences.push(
      `上证指数今天波动不大（${shChange >= 0 ? '+' : ''}${shChange.toFixed(2)}%），市场整体走势平稳，没有明显的方向性变化。`
    );
  }

  const northDeal = data.north_bound.latest_deal_amount;
  if (northDeal != null) {
    sentences.push(`北向资金当天成交额为 ${formatYi(northDeal)}。交易所当前未公开北向汇总净买入，因此系统不对外资买卖方向作推断。`);
  }

  const hotNames = data.hot_sectors.map((s) => s.name).slice(0, 2);
  if (hotNames.length > 0) {
    sentences.push(
      `主力资金排名靠前的板块是「${hotNames.join('」和「')}」，可结合资金净流入和涨跌幅继续观察。`
    );
  }

  return sentences;
}

function finiteNumber(value: unknown): number | null {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeOverview(input: any): MarketOverview {
  const marketIndex = input?.market_index || {};
  const northBound = input?.north_bound || {};
  const fundFlow = input?.fund_flow || {};
  const limitBoard = input?.limit_board || {};
  return {
    ...input,
    market_index: {
      sh_index: finiteNumber(marketIndex.sh_index),
      sh_change: finiteNumber(marketIndex.sh_change),
      sh_change_pct: finiteNumber(marketIndex.sh_change_pct),
      sh_volume: finiteNumber(marketIndex.sh_volume),
      sh_amount: finiteNumber(marketIndex.sh_amount),
    },
    north_bound: {
      latest_deal_amount: finiteNumber(northBound.latest_deal_amount),
      latest_inflow: finiteNumber(northBound.latest_inflow),
      net_inflow_available: Boolean(northBound.net_inflow_available),
    },
    fund_flow: {
      top_inflow: Array.isArray(fundFlow.top_inflow) ? fundFlow.top_inflow : [],
      top_outflow: Array.isArray(fundFlow.top_outflow) ? fundFlow.top_outflow : [],
    },
    limit_board: {
      limit_up: finiteNumber(limitBoard.limit_up),
      limit_down: finiteNumber(limitBoard.limit_down),
    },
    hot_sectors: Array.isArray(input?.hot_sectors) ? input.hot_sectors : [],
  };
}

export default function MarketOverviewPage() {
  const [data, setData] = useState<MarketOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [cacheStats, setCacheStats] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [isEmpty, setIsEmpty] = useState(false);

  const fetchOverview = useCallback(async () => {
    try {
      const res = await apiFetch<OverviewResponse>('/market/overview');
      if (res.code === 0 && res.data) {
        const d = normalizeOverview(res.data);
        const hasMarketData = d.market_index.sh_index != null && d.market_index.sh_index > 0;
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
    fetchCacheStats();
    const interval = setInterval(fetchOverview, 60000);
    return () => clearInterval(interval);
  }, [fetchOverview]);

  const fetchCacheStats = async () => {
    try {
      const res = await apiFetch<any>('/data/cache-stats');
      setCacheStats(res.data);
    } catch {}
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      await apiFetch<any>('/data/sync?force=true', { method: 'POST' });
      await fetchOverview();
      await fetchCacheStats();
    } catch (e) { console.error(e); }
    setSyncing(false);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-text-secondary text-center">
          <Loader2 size={32} className="animate-spin mx-auto mb-3 text-accent" />
          <div className="text-base">正在读取市场行情...</div>
          <div className="text-xs mt-1 text-text-secondary/70">盘中读取实时源，休市读取最近有效缓存</div>
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
          <h2 className="text-lg font-bold text-text mb-2">尚无可用市场快照</h2>
          <p className="text-text-secondary mb-1">
            A股交易时间为每周一至周五的上午 9:30-11:30、下午 13:00-15:00。
          </p>
          <p className="text-text-secondary text-sm">
            系统会在午间和收盘后自动缓存；首次缓存建立前暂不展示推测数据。
          </p>
        </div>
      </div>
    );
  }

  const { market_index, north_bound, fund_flow, limit_board, hot_sectors } = data;
  const plainSentences = generatePlainSummary(data);

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-6 flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-text flex items-center gap-2">
            <Activity size={22} className="text-accent" />
            市场速览
        </h1>
        <p className="text-text-secondary text-sm mt-1">盘中实时更新，休市自动延用最近交易日已核验快照</p>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 text-xs text-text-secondary">
          <span className={data.is_realtime ? 'text-up' : 'text-warn'}>{data.is_realtime ? '盘中实时行情' : '最近交易日缓存（非实时）'}</span>
          <span>数据日期：<b className="font-mono font-normal text-text">{data.data_date || '--'}</b></span>
          <span>来源：{data.source || '--'}</span>
        </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleSync}
            disabled={syncing}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-accent text-white rounded-md hover:opacity-90 disabled:opacity-50"
          >
            <RefreshCw size={12} className={syncing ? 'animate-spin' : ''} />
            {syncing ? '同步中...' : '同步最新数据'}
          </button>
          {cacheStats && (
            <span className="max-w-full text-xs text-text-secondary text-right">
              缓存覆盖: 板块快照 概念 {cacheStats.concept_flow?.coverage?.today_snapshot_boards || 0}/{cacheStats.concept_flow?.coverage?.directory_boards || 0}、行业 {cacheStats.industry_flow?.coverage?.today_snapshot_boards || 0}/{cacheStats.industry_flow?.coverage?.directory_boards || 0} | 股票 {cacheStats.stock_bars?.stocks || 0} 只 / 日线 {cacheStats.stock_bars?.records || 0} 条
            </span>
          )}
        </div>
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
          <div className={`flex items-center gap-1 text-sm font-medium ${getChangeColor(market_index.sh_change_pct ?? 0)}`}>
            {(market_index.sh_change_pct ?? 0) >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
            {market_index.sh_change_pct == null ? '--' : `${market_index.sh_change_pct >= 0 ? '+' : ''}${market_index.sh_change_pct.toFixed(2)}%`}
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
            <span className="text-sm text-text-secondary">北向资金当天成交额</span>
          </div>
          <div className="text-2xl font-mono font-bold text-text">
            {north_bound.latest_deal_amount == null ? '--' : formatYi(north_bound.latest_deal_amount)}
          </div>
          <div className="flex items-center gap-1 mt-2">
            <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-[#21262D] text-text-secondary">
              {north_bound.net_inflow_available ? '净买入已公开' : '净买入未公开'}
            </span>
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
              <div className="text-xs text-text-secondary mb-1 font-medium">主力资金净额较高</div>
              <div className="space-y-1">
                {fund_flow.top_inflow.length > 0 ? (
                  fund_flow.top_inflow.slice(0, 3).map((item) => (
                    <div key={item.name} className="flex items-center justify-between text-xs">
                      <span className="text-text truncate flex-1 mr-2">{item.name}</span>
                      <span className={`font-mono shrink-0 ${getChangeColor(item.inflow)}`}>{formatYi(item.inflow)}</span>
                    </div>
                  ))
                ) : (
                  <div className="text-xs text-text-secondary">暂无数据</div>
                )}
              </div>
            </div>
            <div>
              <div className="text-xs text-text-secondary mb-1 font-medium">主力资金净额较低</div>
              <div className="space-y-1">
                {fund_flow.top_outflow.length > 0 ? (
                  fund_flow.top_outflow.slice(0, 3).map((item) => (
                    <div key={item.name} className="flex items-center justify-between text-xs">
                      <span className="text-text truncate flex-1 mr-2">{item.name}</span>
                      <span className={`font-mono shrink-0 ${getChangeColor(item.outflow)}`}>{formatYi(item.outflow)}</span>
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
                {limit_board.limit_up == null ? '--' : limit_board.limit_up.toLocaleString()}
              </div>
              <div className="text-xs text-text-secondary mt-0.5">只</div>
            </div>
            <div className="w-px h-12 bg-border" />
            <div>
              <div className="text-xs text-down mb-1">跌停</div>
              <div className="text-2xl font-mono font-bold text-down">
                {limit_board.limit_down == null ? '--' : limit_board.limit_down.toLocaleString()}
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
