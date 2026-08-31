'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  BarChart3,
  Building2,
  Database,
  Gauge,
  History,
  LineChart,
  Loader2,
  RefreshCw,
  Search,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
  WalletCards,
} from 'lucide-react';
import MarginTrendChart from '@/components/MarginTrendChart';
import { apiFetch, friendlyApiError } from '@/lib/api';

type AnyMap = Record<string, any>;
type RankMetric = 'balance' | 'net_buy' | 'growth_5d' | 'growth_20d' | 'ratio' | 'lri' | 'divergence';
type SectorType = 'industry' | 'concept' | 'region';
type SectorSort = 'balance' | 'net_buy' | 'net_buy_5d' | 'net_buy_20d' | 'growth_5d' | 'growth_20d' | 'ratio' | 'crowding';

interface MarketRow {
  trade_date: string;
  margin_balance: number | null;
  financing_balance: number | null;
  securities_balance: number | null;
  financing_buy: number | null;
  financing_repay: number | null;
  financing_net_buy: number | null;
  float_market_cap: number | null;
  market_index_close: number | null;
  market_index_change_pct: number | null;
  market_turnover_amount: number | null;
  financing_ratio: number | null;
  lmi_score: number | null;
  lmi_level: string | null;
}

interface MarketPayload {
  available: boolean;
  latest: MarketRow | null;
  history: MarketRow[];
  lmi: AnyMap | null;
  meta: AnyMap;
  refresh: AnyMap;
}

interface SectorRow {
  rank: number;
  trade_date: string;
  sector_type: string;
  sector_code: string;
  sector_name: string;
  financing_balance: number | null;
  financing_net_buy: number | null;
  financing_net_buy_5d: number | null;
  financing_net_buy_20d: number | null;
  financing_change_5d: number | null;
  financing_change_20d: number | null;
  financing_ratio: number | null;
  crowding_score: number | null;
  divergence_type: string | null;
  window_end_date_5d: string | null;
  window_end_date_20d: string | null;
}

interface MetricPayload {
  financing_change_1d?: number | null;
  financing_change_3d?: number | null;
  financing_change_5d?: number | null;
  financing_change_10d?: number | null;
  financing_change_20d?: number | null;
  percentile_60?: number | null;
  percentile_120?: number | null;
  percentile_250?: number | null;
  price_change_5d?: number | null;
  price_change_20d?: number | null;
  divergence_type?: string | null;
  lri_score?: number | null;
  lri_level?: string | null;
  coverage_pct?: number | null;
  components?: Record<string, AnyMap>;
  risk_reasons?: string[];
  validation_conditions?: string[];
  invalidation_conditions?: string[];
}

interface StockRow {
  rank?: number;
  rank_metric?: string;
  rank_value?: number | null;
  stock_code: string;
  stock_name: string;
  sector_name: string | null;
  trade_market: string | null;
  trade_date: string;
  close_price: number | null;
  pct_change: number | null;
  financing_balance: number | null;
  financing_buy: number | null;
  financing_repay: number | null;
  financing_net_buy: number | null;
  margin_balance: number | null;
  turnover_amount: number | null;
  float_market_cap: number | null;
  financing_ratio: number | null;
  financing_ratio_level: { level: string; severity: string; score: number | null };
  financing_buy_ratio: number | null;
  metric: MetricPayload | null;
}

interface StockDetailPayload {
  available: boolean;
  eligible: boolean | null;
  message?: string;
  risk_message?: string;
  stock?: StockRow;
  history?: MarginHistoryItem[];
  history_count?: number;
  risk_explanation?: AnyMap;
  meta: AnyMap;
}

interface MarginHistoryItem {
  trade_date: string;
  financing_balance?: number | null;
  financing_buy?: number | null;
  financing_repay?: number | null;
  financing_net_buy?: number | null;
  financing_ratio?: number | null;
  financing_buy_ratio?: number | null;
  close_price?: number | null;
  pct_change?: number | null;
}

const RANK_OPTIONS: Array<{ key: RankMetric; label: string }> = [
  { key: 'balance', label: '融资余额' },
  { key: 'net_buy', label: '当日净买入' },
  { key: 'growth_5d', label: '5日增长' },
  { key: 'growth_20d', label: '20日增长' },
  { key: 'ratio', label: '融资杠杆率' },
  { key: 'lri', label: 'LRI风险' },
  { key: 'divergence', label: '异常背离' },
];

const SECTOR_TYPES: Array<{ key: SectorType; label: string }> = [
  { key: 'industry', label: '行业' },
  { key: 'concept', label: '概念' },
  { key: 'region', label: '地域' },
];

const SECTOR_SORTS: Array<{ key: SectorSort; label: string }> = [
  { key: 'net_buy', label: '当日净买入' },
  { key: 'net_buy_5d', label: '5日净买入' },
  { key: 'net_buy_20d', label: '20日净买入' },
  { key: 'balance', label: '融资余额' },
  { key: 'growth_5d', label: '5日变化率' },
  { key: 'ratio', label: '融资杠杆率' },
  { key: 'crowding', label: '拥挤度' },
];

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const numberText = (value: unknown, digits = 2) => finite(value) ? value.toFixed(digits) : '--';
const percentText = (value: unknown, digits = 2) => finite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%` : '--';
const plainPercent = (value: unknown, digits = 2) => finite(value) ? `${value.toFixed(digits)}%` : '--';

function sourceText(value: unknown): string {
  const source = String(value || '');
  if (source.includes('RZRQ_LSHJ') || source.includes('margin_disclosure')) {
    return '东方财富两融官方汇总';
  }
  return source || '暂无缓存';
}

function moneyText(value: unknown, signed = false): string {
  if (!finite(value)) return '--';
  const sign = signed && value > 0 ? '+' : '';
  const absolute = Math.abs(value);
  if (absolute >= 1e12) return `${sign}${(value / 1e12).toFixed(2)}万亿`;
  if (absolute >= 1e8) return `${sign}${(value / 1e8).toFixed(2)}亿`;
  if (absolute >= 1e4) return `${sign}${(value / 1e4).toFixed(1)}万`;
  return `${sign}${value.toFixed(0)}`;
}

function changeTone(value: unknown): string {
  if (!finite(value) || value === 0) return 'text-text-secondary';
  return value > 0 ? 'text-up' : 'text-down';
}

function riskTone(level: string | null | undefined): string {
  if (!level) return 'text-text-secondary';
  if (/极端|监管|高风险/.test(level)) return 'text-up';
  if (/偏高|高杠杆|高拥挤|偏热/.test(level)) return 'text-warn';
  return 'text-down';
}

function ProgressBar({ value, tone = 'bg-accent' }: { value: number; tone?: string }) {
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-[#21262D]">
      <div className={`h-full rounded-full transition-[width] duration-300 ${tone}`} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

function Segmented<T extends string>({ options, value, onChange }: { options: Array<{ key: T; label: string }>; value: T; onChange: (value: T) => void }) {
  return (
    <div className="flex max-w-full gap-1 overflow-x-auto rounded-md border border-border bg-bg p-1">
      {options.map((item) => (
        <button
          key={item.key}
          type="button"
          onClick={() => onChange(item.key)}
          className={`h-7 shrink-0 rounded px-2.5 text-[11px] transition-colors ${value === item.key ? 'bg-[#1F6FEB33] text-accent' : 'text-text-secondary hover:bg-[#21262D] hover:text-text'}`}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

function DataMetric({ label, value, sub, tone = 'text-text' }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="min-w-0 px-3 py-3 sm:px-4">
      <div className="truncate text-[11px] text-text-secondary">{label}</div>
      <div className={`mt-1 truncate font-mono text-lg font-semibold ${tone}`} title={value}>{value}</div>
      {sub && <div className="mt-1 truncate text-[10px] text-text-secondary" title={sub}>{sub}</div>}
    </div>
  );
}

export default function MarginLeveragePage() {
  const [market, setMarket] = useState<MarketPayload | null>(null);
  const [sectors, setSectors] = useState<SectorRow[]>([]);
  const [stocks, setStocks] = useState<StockRow[]>([]);
  const [sectorMeta, setSectorMeta] = useState<AnyMap>({});
  const [stockMeta, setStockMeta] = useState<AnyMap>({});
  const [rankMetric, setRankMetric] = useState<RankMetric>('balance');
  const [sectorType, setSectorType] = useState<SectorType>('industry');
  const [sectorSort, setSectorSort] = useState<SectorSort>('net_buy');
  const [trendDays, setTrendDays] = useState(60);
  const [loading, setLoading] = useState(true);
  const [loadingProgress, setLoadingProgress] = useState(4);
  const [rankLoading, setRankLoading] = useState(false);
  const [sectorLoading, setSectorLoading] = useState(false);
  const [error, setError] = useState('');
  const [refreshStatus, setRefreshStatus] = useState<AnyMap | null>(null);
  const [searchCode, setSearchCode] = useState('');
  const [stockDetail, setStockDetail] = useState<StockDetailPayload | null>(null);
  const [stockLoading, setStockLoading] = useState(false);
  const detailRef = useRef<HTMLElement>(null);

  const loadMarket = useCallback(async () => {
    const response = await apiFetch<{ data: MarketPayload }>('/margin/market?days=300', { timeoutMs: 30000 });
    setMarket(response.data);
    setRefreshStatus(response.data.refresh || null);
    return response.data;
  }, []);

  const loadSectors = useCallback(async (nextType: SectorType, nextSort: SectorSort) => {
    setSectorLoading(true);
    try {
      const response = await apiFetch<{ data: { rankings: SectorRow[]; meta: AnyMap } }>(
        `/margin/sectors?sector_type=${nextType}&sort=${nextSort}&order=desc&limit=100`,
      );
      setSectors(response.data.rankings || []);
      setSectorMeta(response.data.meta || {});
    } finally {
      setSectorLoading(false);
    }
  }, []);

  const loadRankings = useCallback(async (nextMetric: RankMetric) => {
    setRankLoading(true);
    try {
      const response = await apiFetch<{ data: { rankings: StockRow[]; meta: AnyMap } }>(
        `/margin/stocks/ranking?metric=${nextMetric}&order=desc&limit=100`,
      );
      setStocks(response.data.rankings || []);
      setStockMeta(response.data.meta || {});
    } finally {
      setRankLoading(false);
    }
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setLoadingProgress(5);
    setError('');
    try {
      const marketTask = loadMarket().then((result) => { setLoadingProgress(38); return result; });
      const sectorTask = loadSectors(sectorType, sectorSort).then(() => setLoadingProgress((value) => Math.max(value, 68)));
      const rankTask = loadRankings(rankMetric).then(() => setLoadingProgress((value) => Math.max(value, 88)));
      await Promise.all([marketTask, sectorTask, rankTask]);
      setLoadingProgress(100);
    } catch (caught) {
      setError(friendlyApiError(caught, '两融杠杆中心加载失败'));
    } finally {
      setLoading(false);
    }
  }, [loadMarket, loadRankings, loadSectors, rankMetric, sectorSort, sectorType]);

  useEffect(() => { void loadAll(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const changeRankMetric = (next: RankMetric) => {
    setRankMetric(next);
    void loadRankings(next).catch((caught) => setError(friendlyApiError(caught)));
  };

  const changeSectorType = (next: SectorType) => {
    setSectorType(next);
    void loadSectors(next, sectorSort).catch((caught) => setError(friendlyApiError(caught)));
  };

  const changeSectorSort = (next: SectorSort) => {
    setSectorSort(next);
    void loadSectors(sectorType, next).catch((caught) => setError(friendlyApiError(caught)));
  };

  const loadStock = useCallback(async (code: string, refresh = false) => {
    const normalized = code.trim().replace(/\.(SH|SZ)$/i, '');
    if (!/^\d{6}$/.test(normalized)) {
      setError('请输入六位股票代码');
      return;
    }
    setStockLoading(true);
    setError('');
    try {
      const response = await apiFetch<{ data: StockDetailPayload }>(
        `/margin/stocks/${normalized}?history_limit=260&refresh=${refresh ? 'true' : 'false'}`,
        { timeoutMs: 70000 },
      );
      setStockDetail(response.data);
      setSearchCode(normalized);
      window.setTimeout(() => detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
    } catch (caught) {
      setError(friendlyApiError(caught, '个股两融核验失败'));
    } finally {
      setStockLoading(false);
    }
  }, []);

  const startRefresh = async () => {
    setError('');
    try {
      const response = await apiFetch<{ data: AnyMap }>('/margin/refresh?full=false&prewarm=false', {
        method: 'POST', timeoutMs: 15000,
      });
      setRefreshStatus(response.data);
    } catch (caught) {
      setError(friendlyApiError(caught, '刷新任务提交失败'));
    }
  };

  useEffect(() => {
    if (!refreshStatus?.running && !['queued', 'running'].includes(refreshStatus?.status)) return;
    const poll = async () => {
      try {
        const response = await apiFetch<{ data: AnyMap }>('/margin/refresh/status', { timeoutMs: 10000 });
        setRefreshStatus(response.data);
        if (response.data.status === 'completed') await loadAll();
      } catch {
        // The next poll retains visible progress and retries a transient wake-up.
      }
    };
    const timer = window.setInterval(() => void poll(), 2500);
    return () => window.clearInterval(timer);
  }, [loadAll, refreshStatus?.running, refreshStatus?.status]);

  const latest = market?.latest;
  const lmi = market?.lmi || {};
  const history = useMemo(() => (market?.history || []).slice(-trendDays), [market?.history, trendDays]);
  const activeStock = stockDetail?.stock;
  const activeMetric = activeStock?.metric;

  if (loading) {
    return (
      <main className="mx-auto grid min-h-[70vh] max-w-7xl place-items-center px-4">
        <div className="w-full max-w-md text-center">
          <Loader2 size={28} className="mx-auto animate-spin text-accent" />
          <div className="mt-3 text-sm text-text">正在读取两融收盘数据</div>
          <div className="mt-1 text-xs text-text-secondary">市场趋势、板块排名、个股榜单</div>
          <div className="mt-4"><ProgressBar value={loadingProgress} /></div>
          <div className="mt-2 font-mono text-xs text-accent">{loadingProgress}%</div>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-[1540px] px-3 py-5 sm:px-4 sm:py-6">
      <header className="mb-5 flex flex-col gap-4 border-b border-border pb-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-xl font-semibold text-text"><Gauge size={21} className="text-accent" />两融杠杆中心</h1>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-secondary">
            <span>数据日 {market?.meta?.data_date || '--'}</span>
            <span>来源 {sourceText(market?.meta?.source)}</span>
            <span className="text-warn">收盘数据 · 非实时</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <form
            className="flex min-w-0 items-center overflow-hidden rounded-md border border-border bg-bg focus-within:border-accent"
            onSubmit={(event) => { event.preventDefault(); void loadStock(searchCode); }}
          >
            <Search size={14} className="ml-3 shrink-0 text-text-secondary" />
            <input
              value={searchCode}
              onChange={(event) => setSearchCode(event.target.value)}
              placeholder="输入股票代码"
              className="h-9 min-w-0 w-32 bg-transparent px-2 text-xs text-text outline-none sm:w-40"
            />
            <button type="submit" disabled={stockLoading} className="h-9 border-l border-border px-3 text-xs text-accent hover:bg-[#1F6FEB18] disabled:opacity-50">
              {stockLoading ? <Loader2 size={14} className="animate-spin" /> : '查询'}
            </button>
          </form>
          <button
            type="button"
            onClick={startRefresh}
            disabled={Boolean(refreshStatus?.running)}
            className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border px-3 text-xs text-text-secondary hover:border-accent hover:text-text disabled:opacity-50"
          >
            <RefreshCw size={14} className={refreshStatus?.running ? 'animate-spin' : ''} />
            刷新两融数据
          </button>
        </div>
      </header>

      {error && (
        <div className="mb-4 flex items-start gap-2 rounded-md border border-up/40 bg-[#EF535010] p-3 text-xs text-up">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />{error}
        </div>
      )}

      {refreshStatus && ['queued', 'running', 'failed', 'stale'].includes(refreshStatus.status) && (
        <section className="mb-4 border-y border-border py-3">
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className={refreshStatus.status === 'failed' ? 'text-up' : 'text-text'}>{refreshStatus.stage || '后台同步'}</span>
            <span className="font-mono text-accent">{refreshStatus.progress || 0}%</span>
          </div>
          <div className="mt-2"><ProgressBar value={refreshStatus.progress || 0} tone={refreshStatus.status === 'failed' ? 'bg-up' : 'bg-accent'} /></div>
          {(refreshStatus.status === 'failed' || refreshStatus.status === 'stale') && <div className="mt-2 text-[11px] text-text-secondary">本次更新未完成，页面继续使用最近一次完整缓存。{refreshStatus.error ? `原因：${refreshStatus.error}` : ''}</div>}
        </section>
      )}

      {!market?.available ? (
        <section className="border-y border-border py-16 text-center">
          <Database size={28} className="mx-auto text-text-secondary" />
          <div className="mt-3 text-sm text-text">尚未建立两融缓存</div>
          <button type="button" onClick={startRefresh} className="mt-4 rounded-md border border-accent px-4 py-2 text-xs text-accent hover:bg-[#1F6FEB18]">开始首次同步</button>
        </section>
      ) : (
        <>
          <section className="overflow-hidden rounded-md border border-border bg-card">
            <div className="grid grid-cols-2 divide-x divide-y divide-border sm:grid-cols-3 xl:grid-cols-6 xl:divide-y-0">
              <DataMetric label="两市两融余额" value={moneyText(latest?.margin_balance)} sub={latest?.trade_date} />
              <DataMetric label="融资余额" value={moneyText(latest?.financing_balance)} sub={`占流通市值 ${plainPercent(latest?.financing_ratio, 2)}`} />
              <DataMetric label="融券余额" value={moneyText(latest?.securities_balance)} />
              <DataMetric label="当日融资买入" value={moneyText(latest?.financing_buy)} />
              <DataMetric label="当日融资偿还" value={moneyText(latest?.financing_repay)} />
              <DataMetric label="当日融资净买入" value={moneyText(latest?.financing_net_buy, true)} tone={changeTone(latest?.financing_net_buy)} />
            </div>
          </section>

          <section className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
            <div className="min-w-0 rounded-md border border-border bg-card">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
                <div>
                  <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><LineChart size={15} className="text-accent" />市场两融趋势</h2>
                  <div className="mt-1 text-[10px] text-text-secondary">融资余额 · 上证指数 · 融资净买入</div>
                </div>
                <Segmented
                  options={[20, 60, 120, 250].map((value) => ({ key: String(value), label: `${value}日` }))}
                  value={String(trendDays)}
                  onChange={(value) => setTrendDays(Number(value))}
                />
              </div>
              <div className="p-2 sm:p-3"><MarginTrendChart rows={history} /></div>
            </div>

            <div className="rounded-md border border-border bg-card">
              <div className="border-b border-border px-4 py-3">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Gauge size={15} className="text-accent" />LMI 市场杠杆温度</h2>
              </div>
              <div className="p-4">
                <div className="flex items-end justify-between gap-4">
                  <div className={`font-mono text-4xl font-semibold ${riskTone(lmi.level || latest?.lmi_level)}`}>{finite(lmi.score) ? lmi.score.toFixed(1) : '--'}</div>
                  <div className={`pb-1 text-sm ${riskTone(lmi.level || latest?.lmi_level)}`}>{lmi.level || latest?.lmi_level || '样本不足'}</div>
                </div>
                <div className="mt-3"><ProgressBar value={finite(lmi.score) ? lmi.score : 0} tone={finite(lmi.score) && lmi.score >= 80 ? 'bg-up' : finite(lmi.score) && lmi.score >= 60 ? 'bg-warn' : 'bg-down'} /></div>
                <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
                  <SmallFact label="250日分位" value={plainPercent(lmi.components?.market_percentile_250?.raw, 1)} />
                  <SmallFact label="5日余额变化" value={percentText(lmi.financing_change_5d)} tone={changeTone(lmi.financing_change_5d)} />
                  <SmallFact label="20日余额变化" value={percentText(lmi.financing_change_20d)} tone={changeTone(lmi.financing_change_20d)} />
                  <SmallFact label="高杠杆股占比" value={plainPercent(lmi.high_leverage_stock_ratio, 1)} />
                  <SmallFact label="行业集中度" value={plainPercent(lmi.sector_concentration_ratio, 1)} />
                  <SmallFact label="评分覆盖" value={plainPercent(lmi.coverage_pct, 0)} />
                </div>
                <div className="mt-4 border-t border-border pt-3 text-[10px] leading-5 text-text-secondary">{market?.meta?.disclosure_note}</div>
              </div>
            </div>
          </section>

          <section className="mt-4 rounded-md border border-border bg-card">
            <div className="flex flex-col gap-3 border-b border-border px-4 py-3 xl:flex-row xl:items-center xl:justify-between">
              <div>
                <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Building2 size={15} className="text-accent" />板块两融排名</h2>
                <div className="mt-1 text-[10px] text-text-secondary">数据日 {sectorMeta.data_date || '--'} · 5/20日窗口日期单独列示</div>
              </div>
              <div className="flex max-w-full flex-col gap-2 sm:flex-row">
                <Segmented options={SECTOR_TYPES} value={sectorType} onChange={changeSectorType} />
                <Segmented options={SECTOR_SORTS} value={sectorSort} onChange={changeSectorSort} />
              </div>
            </div>
            <div className="relative overflow-x-auto">
              {sectorLoading && <TableLoader label="更新板块榜" />}
              <table className="w-full min-w-[1140px] text-xs">
                <thead><tr className="border-b border-border bg-bg text-text-secondary">
                  <th className="px-3 py-2.5 text-left font-medium">排名</th><th className="px-3 py-2.5 text-left font-medium">板块</th>
                  <th className="px-3 py-2.5 text-right font-medium">融资余额</th><th className="px-3 py-2.5 text-right font-medium">当日净买入</th>
                  <th className="px-3 py-2.5 text-right font-medium">5日净买入</th><th className="px-3 py-2.5 text-right font-medium">20日净买入</th>
                  <th className="px-3 py-2.5 text-right font-medium">5日变化</th><th className="px-3 py-2.5 text-right font-medium">融资率</th>
                  <th className="px-3 py-2.5 text-right font-medium">拥挤度</th><th className="px-3 py-2.5 text-left font-medium">结构判断</th>
                </tr></thead>
                <tbody>{sectors.slice(0, 100).map((row) => (
                  <tr key={`${row.sector_type}-${row.sector_code}`} className="border-b border-border/60 hover:bg-[#21262D]">
                    <td className="px-3 py-2.5 font-mono text-text-secondary">{row.rank}</td>
                    <td className="px-3 py-2.5"><div className="font-medium text-text">{row.sector_name}</div><div className="mt-0.5 text-[9px] text-text-secondary">{row.window_end_date_5d || '--'} / {row.window_end_date_20d || '--'}</div></td>
                    <td className="px-3 py-2.5 text-right font-mono text-text">{moneyText(row.financing_balance)}</td>
                    <td className={`px-3 py-2.5 text-right font-mono ${changeTone(row.financing_net_buy)}`}>{moneyText(row.financing_net_buy, true)}</td>
                    <td className={`px-3 py-2.5 text-right font-mono ${changeTone(row.financing_net_buy_5d)}`}>{moneyText(row.financing_net_buy_5d, true)}</td>
                    <td className={`px-3 py-2.5 text-right font-mono ${changeTone(row.financing_net_buy_20d)}`}>{moneyText(row.financing_net_buy_20d, true)}</td>
                    <td className={`px-3 py-2.5 text-right font-mono ${changeTone(row.financing_change_5d)}`}>{percentText(row.financing_change_5d)}</td>
                    <td className="px-3 py-2.5 text-right font-mono text-text">{plainPercent(row.financing_ratio)}</td>
                    <td className={`px-3 py-2.5 text-right font-mono ${riskTone(finite(row.crowding_score) && row.crowding_score >= 80 ? '高拥挤' : '正常')}`}>{finite(row.crowding_score) ? row.crowding_score.toFixed(1) : '--'}</td>
                    <td className="max-w-[210px] px-3 py-2.5 text-[11px] text-text-secondary">{row.divergence_type || '--'}</td>
                  </tr>
                ))}</tbody>
              </table>
              {!sectorLoading && !sectors.length && <EmptyTable label="暂无板块两融缓存" />}
            </div>
          </section>

          <section className="mt-4 rounded-md border border-border bg-card">
            <div className="flex flex-col gap-3 border-b border-border px-4 py-3 xl:flex-row xl:items-center xl:justify-between">
              <div>
                <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><BarChart3 size={15} className="text-accent" />个股两融 TOP100</h2>
                <div className="mt-1 text-[10px] text-text-secondary">数据日 {stockMeta.data_date || '--'} · LRI使用个股自身历史分位</div>
              </div>
              <Segmented options={RANK_OPTIONS} value={rankMetric} onChange={changeRankMetric} />
            </div>
            <div className="relative overflow-x-auto">
              {rankLoading && <TableLoader label="更新个股榜" />}
              <table className="w-full min-w-[1340px] text-xs">
                <thead><tr className="border-b border-border bg-bg text-text-secondary">
                  <th className="px-3 py-2.5 text-left font-medium">排名</th><th className="px-3 py-2.5 text-left font-medium">股票</th>
                  <th className="px-3 py-2.5 text-left font-medium">板块</th><th className="px-3 py-2.5 text-right font-medium">收盘/涨跌</th>
                  <th className="px-3 py-2.5 text-right font-medium">融资余额</th><th className="px-3 py-2.5 text-right font-medium">融资杠杆率</th>
                  <th className="px-3 py-2.5 text-right font-medium">当日净买入</th><th className="px-3 py-2.5 text-right font-medium">5日变化</th>
                  <th className="px-3 py-2.5 text-right font-medium">买入/成交额</th><th className="px-3 py-2.5 text-right font-medium">250日分位</th>
                  <th className="px-3 py-2.5 text-right font-medium">LRI</th><th className="px-3 py-2.5 text-left font-medium">价格-融资</th><th className="px-3 py-2.5 text-left font-medium">决策入口</th>
                </tr></thead>
                <tbody>{stocks.map((row) => (
                  <tr key={row.stock_code} className="border-b border-border/60 hover:bg-[#21262D]">
                    <td className="px-3 py-2.5 font-mono text-text-secondary">{row.rank}</td>
                    <td className="px-3 py-2.5">
                      <button type="button" onClick={() => void loadStock(row.stock_code)} className="text-left font-medium text-text hover:text-accent">{row.stock_name}</button>
                      <div className="font-mono text-[10px] text-text-secondary">{row.stock_code}</div>
                    </td>
                    <td className="max-w-[130px] truncate px-3 py-2.5 text-text-secondary" title={row.sector_name || ''}>{row.sector_name || '--'}</td>
                    <td className="px-3 py-2.5 text-right"><div className="font-mono text-text">{numberText(row.close_price)}</div><div className={`font-mono text-[10px] ${changeTone(row.pct_change)}`}>{percentText(row.pct_change)}</div></td>
                    <td className="px-3 py-2.5 text-right font-mono text-text">{moneyText(row.financing_balance)}</td>
                    <td className="px-3 py-2.5 text-right"><div className={`font-mono ${riskTone(row.financing_ratio_level?.level)}`}>{plainPercent(row.financing_ratio)}</div><div className={`text-[9px] ${riskTone(row.financing_ratio_level?.level)}`}>{row.financing_ratio_level?.level || '--'}</div></td>
                    <td className={`px-3 py-2.5 text-right font-mono ${changeTone(row.financing_net_buy)}`}>{moneyText(row.financing_net_buy, true)}</td>
                    <td className={`px-3 py-2.5 text-right font-mono ${changeTone(row.metric?.financing_change_5d)}`}>{percentText(row.metric?.financing_change_5d)}</td>
                    <td className="px-3 py-2.5 text-right font-mono text-text-secondary">{plainPercent(row.financing_buy_ratio)}</td>
                    <td className="px-3 py-2.5 text-right font-mono text-text">{plainPercent(row.metric?.percentile_250, 1)}</td>
                    <td className={`px-3 py-2.5 text-right font-mono font-semibold ${riskTone(row.metric?.lri_level)}`}>{finite(row.metric?.lri_score) ? row.metric!.lri_score!.toFixed(1) : '--'}<div className="text-[9px] font-normal">{row.metric?.lri_level || '待自历史'}</div></td>
                    <td className="max-w-[140px] px-3 py-2.5 text-[11px] text-text-secondary">{row.metric?.divergence_type || '待历史核验'}</td>
                    <td className="px-3 py-2.5"><div className="flex items-center gap-2 whitespace-nowrap"><Link href={`/pro/stock?code=${row.stock_code}`} className="text-accent hover:underline">个股画像</Link><Link href={`/strong-stock-decision?code=${row.stock_code}`} className="text-text-secondary hover:text-text">强势决策</Link></div></td>
                  </tr>
                ))}</tbody>
              </table>
              {!rankLoading && !stocks.length && <EmptyTable label={rankMetric === 'lri' ? '重点标的自身250日历史仍在回补' : '暂无个股两融缓存'} />}
            </div>
          </section>
        </>
      )}

      {(stockLoading || stockDetail) && (
        <section ref={detailRef} className="mt-4 scroll-mt-20 rounded-md border border-border bg-card">
          {stockLoading ? (
            <div className="flex min-h-48 items-center justify-center gap-2 text-xs text-text-secondary"><Loader2 size={16} className="animate-spin text-accent" />正在核验个股自身两融历史</div>
          ) : stockDetail && !stockDetail.available ? (
            <div className="p-6">
              <div className="flex items-center gap-2 text-sm text-warn"><ShieldAlert size={16} />{stockDetail.message || '暂无两融风险评分'}</div>
              <div className="mt-2 text-xs text-text-secondary">{stockDetail.risk_message || '暂无两融风险评分'}</div>
              <div className="mt-4 flex gap-3"><Link href={`/pro/stock?code=${searchCode}`} className="text-xs text-accent">仍可查看个股决策画像</Link><Link href={`/strong-stock-decision?code=${searchCode}`} className="text-xs text-text-secondary hover:text-text">查看强势股决策</Link></div>
            </div>
          ) : activeStock ? (
            <StockDetail
              stock={activeStock}
              metric={activeMetric}
              history={stockDetail?.history || []}
              meta={stockDetail?.meta || {}}
              explanation={stockDetail?.risk_explanation || {}}
              onRefresh={() => void loadStock(activeStock.stock_code, true)}
              refreshing={stockLoading}
            />
          ) : null}
        </section>
      )}
    </main>
  );
}

function SmallFact({ label, value, tone = 'text-text' }: { label: string; value: string; tone?: string }) {
  return <div><div className="text-[10px] text-text-secondary">{label}</div><div className={`mt-0.5 font-mono ${tone}`}>{value}</div></div>;
}

function TableLoader({ label }: { label: string }) {
  return <div className="absolute inset-0 z-10 flex items-center justify-center gap-2 bg-[#0D1117CC] text-xs text-text-secondary"><Loader2 size={15} className="animate-spin text-accent" />{label}</div>;
}

function EmptyTable({ label }: { label: string }) {
  return <div className="border-t border-border py-10 text-center text-xs text-text-secondary">{label}</div>;
}

function RiskScale({ ratio }: { ratio: number | null }) {
  const position = finite(ratio) ? Math.max(0, Math.min(100, ratio / 25 * 100)) : 0;
  return (
    <div>
      <div className="relative mt-7 h-2 overflow-visible rounded-full bg-[#21262D]">
        <div className="absolute inset-y-0 left-0 w-[20%] rounded-l-full bg-[#26A69A]" />
        <div className="absolute inset-y-0 left-[20%] w-[12%] bg-[#D29922]" />
        <div className="absolute inset-y-0 left-[32%] w-[16%] bg-[#D97706]" />
        <div className="absolute inset-y-0 left-[48%] w-[12%] bg-[#EF5350]" />
        <div className="absolute inset-y-0 left-[60%] w-[20%] bg-[#C2413B]" />
        <div className="absolute inset-y-0 left-[80%] w-[20%] rounded-r-full bg-[#991B1B]" />
        {finite(ratio) && <div className="absolute -top-5 h-6 w-px bg-text" style={{ left: `${position}%` }}><span className="absolute -translate-x-1/2 -top-4 whitespace-nowrap font-mono text-[10px] text-text">{ratio.toFixed(2)}%</span></div>}
      </div>
      <div className="mt-2 grid grid-cols-6 text-center font-mono text-[8px] text-text-secondary"><span>0-5%</span><span>5-8%</span><span>8-12%</span><span>12-15%</span><span>15-20%</span><span>20-25%+</span></div>
    </div>
  );
}

function StockDetail({ stock, metric, history, meta, explanation, onRefresh, refreshing }: { stock: StockRow; metric: MetricPayload | null | undefined; history: MarginHistoryItem[]; meta: AnyMap; explanation: AnyMap; onRefresh: () => void; refreshing: boolean }) {
  const components = Object.values(metric?.components || {}) as AnyMap[];
  return (
    <div>
      <div className="flex flex-col gap-3 border-b border-border px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2"><h2 className="text-base font-semibold text-text">{stock.stock_name}</h2><span className="font-mono text-xs text-text-secondary">{stock.stock_code}</span><span className={`rounded border border-border px-2 py-0.5 text-[10px] ${riskTone(stock.financing_ratio_level?.level)}`}>{stock.financing_ratio_level?.level}</span></div>
          <div className="mt-1 text-[10px] text-text-secondary">{stock.sector_name || '--'} · 数据日 {meta.data_date || stock.trade_date} · {meta.cache_state || 'cache'}</div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={onRefresh} disabled={refreshing} className="inline-flex h-8 items-center gap-1 rounded border border-border px-2.5 text-[11px] text-text-secondary hover:text-text"><RefreshCw size={12} />刷新个股历史</button>
          <Link href={`/pro/stock?code=${stock.stock_code}`} className="inline-flex h-8 items-center gap-1 rounded border border-accent/50 px-2.5 text-[11px] text-accent">个股决策画像<ArrowRight size={12} /></Link>
          <Link href={`/strong-stock-decision?code=${stock.stock_code}`} className="inline-flex h-8 items-center gap-1 rounded border border-border px-2.5 text-[11px] text-text-secondary hover:text-text">强势股决策<ArrowRight size={12} /></Link>
        </div>
      </div>

      <div className="grid grid-cols-2 divide-x divide-y divide-border sm:grid-cols-4 lg:grid-cols-8 lg:divide-y-0">
        <DataMetric label="融资余额" value={moneyText(stock.financing_balance)} />
        <DataMetric label="融资杠杆率" value={plainPercent(stock.financing_ratio)} tone={riskTone(stock.financing_ratio_level?.level)} />
        <DataMetric label="融资净买入" value={moneyText(stock.financing_net_buy, true)} tone={changeTone(stock.financing_net_buy)} />
        <DataMetric label="买入/成交额" value={plainPercent(stock.financing_buy_ratio)} />
        <DataMetric label="5日变化" value={percentText(metric?.financing_change_5d)} tone={changeTone(metric?.financing_change_5d)} />
        <DataMetric label="20日变化" value={percentText(metric?.financing_change_20d)} tone={changeTone(metric?.financing_change_20d)} />
        <DataMetric label="250日分位" value={plainPercent(metric?.percentile_250, 1)} sub={metric?.percentile_250 == null ? '自身历史待回补' : undefined} />
        <DataMetric label="LRI" value={finite(metric?.lri_score) ? `${metric!.lri_score!.toFixed(1)} / 100` : '暂无评分'} tone={riskTone(metric?.lri_level)} sub={metric?.lri_level || '样本不足'} />
      </div>

      <div className="grid gap-0 border-t border-border xl:grid-cols-[minmax(0,1.25fr)_minmax(360px,0.75fr)] xl:divide-x xl:divide-border">
        <div className="min-w-0 p-3 sm:p-4">
          <div className="flex items-center justify-between gap-3"><h3 className="flex items-center gap-2 text-xs font-semibold text-text"><History size={14} className="text-accent" />个股两融历史</h3><span className="text-[10px] text-text-secondary">{history.length}个交易日</span></div>
          <MarginTrendChart rows={history} mode="stock" height={310} />
          <div className="mt-2 border-t border-border pt-3">
            <div className="flex items-center justify-between text-[11px]"><span className="text-text-secondary">融资杠杆率风险刻度</span><span className={riskTone(stock.financing_ratio_level?.level)}>{stock.financing_ratio_level?.level}</span></div>
            <RiskScale ratio={stock.financing_ratio} />
            <div className="mt-3 text-[10px] leading-5 text-text-secondary" title={meta.reference_line_note}>{meta.reference_line_note}</div>
          </div>
        </div>

        <div className="border-t border-border p-4 xl:border-t-0">
          <h3 className="flex items-center gap-2 text-xs font-semibold text-text"><ShieldAlert size={14} className="text-warn" />LRI因子与风险解释</h3>
          <div className="mt-3 space-y-3">
            {components.length ? components.map((item) => (
              <div key={item.label}>
                <div className="flex items-center justify-between gap-2 text-[11px]"><span className="truncate text-text-secondary">{item.label}</span><span className="shrink-0 font-mono text-text">{finite(item.score) ? item.score.toFixed(1) : '--'} · {finite(item.contribution) ? item.contribution.toFixed(1) : '--'}</span></div>
                <div className="mt-1"><ProgressBar value={finite(item.score) ? item.score : 0} tone={finite(item.score) && item.score >= 70 ? 'bg-up' : finite(item.score) && item.score >= 50 ? 'bg-warn' : 'bg-down'} /></div>
              </div>
            )) : <div className="text-[11px] leading-5 text-text-secondary">自身250日融资历史不足，LRI不计算为0，待回补后再评分。</div>}
          </div>
          <div className="mt-4 border-t border-border pt-3">
            <div className="text-[10px] text-text-secondary">价格-融资关系</div>
            <div className="mt-1 text-sm text-text">{metric?.divergence_type || explanation.relation || '数据不足'}</div>
          </div>
          <ReasonList label="风险依据" items={explanation.reasons || metric?.risk_reasons || []} tone="text-warn" />
          <ReasonList label="验证条件" items={explanation.validation_conditions || metric?.validation_conditions || []} tone="text-down" />
          <ReasonList label="失效条件" items={explanation.invalidation_conditions || metric?.invalidation_conditions || []} tone="text-up" />
        </div>
      </div>
    </div>
  );
}

function ReasonList({ label, items, tone }: { label: string; items: string[]; tone: string }) {
  if (!items.length) return null;
  return <div className="mt-4"><div className="text-[10px] text-text-secondary">{label}</div><div className={`mt-1 space-y-1 text-[11px] leading-5 ${tone}`}>{items.slice(0, 4).map((item) => <div key={item}>{item}</div>)}</div></div>;
}
