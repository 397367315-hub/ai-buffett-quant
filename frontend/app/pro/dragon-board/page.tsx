'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  BrainCircuit,
  Building2,
  CalendarDays,
  RefreshCw,
  TrendingDown,
  TrendingUp,
  WalletCards,
} from 'lucide-react';
import AddToPersonalPoolButton from '@/components/AddToPersonalPoolButton';
import { apiFetch, formatYi, getChangeColor } from '@/lib/api';

type WindowKey = 'week' | 'two_weeks' | 'month';

interface DragonStock {
  code: string;
  name: string;
  date: string;
  price: number | null;
  change_pct: number | null;
  turnover: number | null;
  amount: number | null;
  buy_amount: number | null;
  sell_amount: number | null;
  net_amount: number | null;
  market_cap: number | null;
  institution_count: number;
  institution_net_amount: number | null;
  reason: string;
}

interface DragonBoardData {
  stocks: DragonStock[];
  summary: {
    total: number;
    institution_active: number;
    institution_stock_count: number;
    total_buy_amount: number;
    total_sell_amount: number;
    total_net_amount: number;
  };
  available: boolean;
  source: string;
  is_realtime: boolean;
  data_date: string | null;
  updated_at: string;
}

interface RankedStock {
  code: string;
  name: string;
  appearances: number;
  net_amount: number;
  institution_count: number;
}

interface DragonAnalysis {
  available: boolean;
  window: { id: WindowKey; label: string; sessions: number };
  period: { start: string | null; end: string | null };
  coverage: { actual_sessions: number; requested_sessions: number; stock_count: number; complete: boolean };
  analysis: {
    score: number;
    tone: string;
    headline: string;
    summary: string;
    aggregate_net_amount: number;
    positive_ratio_pct: number;
    top_net_buys: RankedStock[];
    top_net_sells: RankedStock[];
    recurring: RankedStock[];
    institutional: RankedStock[];
    suggestions: string[];
    risks: string[];
  };
  ai_narrative: string | null;
  ai_generated: boolean;
  method: string;
}

interface DateOption {
  date: string;
  stock_count: number;
}

const WINDOW_OPTIONS: Array<{ key: WindowKey; label: string }> = [
  { key: 'week', label: '近一周' },
  { key: 'two_weeks', label: '近两周' },
  { key: 'month', label: '近一月' },
];

function number(value: number | null | undefined, digits = 2): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '--';
}

function money(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? formatYi(value) : '--';
}

export default function DragonBoardPage() {
  const [data, setData] = useState<DragonBoardData | null>(null);
  const [dates, setDates] = useState<DateOption[]>([]);
  const [selectedDate, setSelectedDate] = useState('');
  const [windowKey, setWindowKey] = useState<WindowKey>('week');
  const [analysis, setAnalysis] = useState<DragonAnalysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [analysisLoading, setAnalysisLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const loadDates = useCallback(async () => {
    const response = await apiFetch<{ data: { dates: DateOption[] } }>('/dragon/board/dates');
    setDates(response.data.dates || []);
  }, []);

  const loadBoard = useCallback(async (targetDate?: string) => {
    setLoading(true);
    setError('');
    try {
      const query = targetDate ? `?date=${encodeURIComponent(targetDate)}` : '';
      const response = await apiFetch<{ data: DragonBoardData }>(`/dragon/board${query}`);
      setData(response.data);
      setSelectedDate(response.data.data_date || targetDate || '');
    } catch (err) {
      setError(err instanceof Error ? err.message : '龙虎榜读取失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadAnalysis = useCallback(async (nextWindow: WindowKey) => {
    setAnalysisLoading(true);
    try {
      const response = await apiFetch<{ data: DragonAnalysis }>('/dragon/board/analysis', {
        method: 'POST',
        body: JSON.stringify({ window: nextWindow }),
      });
      setAnalysis(response.data);
    } catch {
      setAnalysis(null);
    } finally {
      setAnalysisLoading(false);
    }
  }, []);

  useEffect(() => {
    void Promise.all([loadBoard(), loadDates().catch(() => undefined)]);
  }, [loadBoard, loadDates]);

  useEffect(() => {
    void loadAnalysis(windowKey);
  }, [loadAnalysis, windowKey]);

  const refresh = async () => {
    setRefreshing(true);
    setError('');
    try {
      const response = await apiFetch<{ data: DragonBoardData }>('/dragon/board/refresh', {
        method: 'POST',
        body: JSON.stringify(selectedDate ? { date: selectedDate } : {}),
      });
      setData(response.data);
      setSelectedDate(response.data.data_date || selectedDate);
      await Promise.all([loadDates(), loadAnalysis(windowKey)]);
    } catch (err) {
      setError(err instanceof Error ? err.message : '刷新失败');
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="max-w-[1440px] mx-auto px-4 py-5">
      <header className="flex flex-col gap-4 border-b border-border pb-5 mb-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <TrendingUp size={21} className="text-warn" />
            龙虎榜
          </h1>
          <div className="mt-1.5 text-xs text-text-secondary flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>数据日 {data?.data_date || '--'}</span>
            <span>来源 {data?.source === 'database_cache' ? '盘后缓存' : '暂无'}</span>
            <span>{data?.is_realtime ? '实时' : '非实时披露数据'}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-text-secondary">
            <CalendarDays size={15} />
            <select
              value={selectedDate}
              onChange={(event) => void loadBoard(event.target.value)}
              className="h-9 min-w-[150px] rounded-md border border-border bg-[#0D1117] px-2.5 text-xs text-text"
              aria-label="龙虎榜交易日"
            >
              {!selectedDate && <option value="">最近有效日</option>}
              {selectedDate && !dates.some((item) => item.date === selectedDate) && <option value={selectedDate}>{selectedDate}</option>}
              {dates.map((item) => <option key={item.date} value={item.date}>{item.date} · {item.stock_count}只</option>)}
            </select>
          </label>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={refreshing}
            className="h-9 w-9 grid place-items-center rounded-md border border-border bg-card text-text-secondary hover:text-text disabled:opacity-50"
            title="刷新龙虎榜缓存"
            aria-label="刷新龙虎榜缓存"
          >
            <RefreshCw size={16} className={refreshing ? 'animate-spin' : ''} />
          </button>
        </div>
      </header>

      {error && <div className="mb-4 border border-down/40 bg-down/10 px-4 py-3 text-sm text-down rounded-md">{error}</div>}

      {loading ? (
        <div className="h-52 grid place-items-center text-sm text-text-secondary">
          <div className="text-center"><RefreshCw size={24} className="animate-spin mx-auto mb-3 text-accent" />正在读取龙虎榜缓存</div>
        </div>
      ) : !data?.available ? (
        <div className="border-y border-border py-16 text-center text-sm text-text-secondary">
          当前日期没有已核验的龙虎榜缓存。盘后任务会自动补充，也可以点击刷新重试。
        </div>
      ) : (
        <>
          <section className="grid grid-cols-2 gap-px bg-border border border-border mb-5 lg:grid-cols-4">
            <Metric icon={<WalletCards size={15} />} label="上榜股票" value={`${data.summary.total}只`} />
            <Metric icon={<Building2 size={15} />} label="机构涉及股票" value={`${data.summary.institution_stock_count}只`} accent />
            <Metric icon={<TrendingUp size={15} />} label="榜单买入额" value={money(data.summary.total_buy_amount)} />
            <Metric icon={data.summary.total_net_amount >= 0 ? <TrendingUp size={15} /> : <TrendingDown size={15} />} label="榜单净买额" value={money(data.summary.total_net_amount)} trend={data.summary.total_net_amount} />
          </section>

          <section className="border border-border bg-card mb-5 rounded-md overflow-hidden">
            <div className="flex flex-col gap-3 border-b border-border px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex items-center gap-2 text-sm font-semibold text-text"><BrainCircuit size={16} className="text-accent" />周期分析</div>
              <div className="inline-flex h-8 border border-border rounded-md overflow-hidden self-start">
                {WINDOW_OPTIONS.map((item) => (
                  <button
                    type="button"
                    key={item.key}
                    onClick={() => setWindowKey(item.key)}
                    className={`px-3 text-xs border-r border-border last:border-r-0 ${windowKey === item.key ? 'bg-accent text-white' : 'bg-[#0D1117] text-text-secondary hover:text-text'}`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
            {analysisLoading ? (
              <div className="h-28 grid place-items-center text-xs text-text-secondary"><RefreshCw size={17} className="animate-spin mb-2" />分析缓存数据</div>
            ) : analysis?.available ? (
              <div className="p-4">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                  <div>
                    <div className="text-sm font-semibold text-text">{analysis.analysis.headline}</div>
                    <div className="text-xs text-text-secondary mt-1">{analysis.analysis.summary}</div>
                  </div>
                  <div className="flex items-center gap-3 text-xs shrink-0">
                    <span className="text-text-secondary">强弱分 <strong className="font-mono text-text">{analysis.analysis.score.toFixed(1)}</strong></span>
                    <span className={`font-medium ${getChangeColor(analysis.analysis.aggregate_net_amount)}`}>{money(analysis.analysis.aggregate_net_amount)}</span>
                  </div>
                </div>
                <div className="grid gap-4 mt-4 lg:grid-cols-3">
                  <RankList title="周期净买居前" rows={analysis.analysis.top_net_buys.filter((item) => item.net_amount > 0)} />
                  <RankList title="重复上榜" rows={analysis.analysis.recurring} />
                  <RankList title="机构席位活跃" rows={analysis.analysis.institutional} />
                </div>
                {(analysis.ai_narrative || analysis.analysis.suggestions.length > 0 || analysis.analysis.risks.length > 0) && (
                  <div className="grid gap-4 mt-4 border-t border-border pt-4 lg:grid-cols-2">
                    <NarrativeText text={analysis.ai_narrative || analysis.analysis.suggestions.join('\n')} />
                    <NarrativeText text={analysis.analysis.risks.length ? analysis.analysis.risks.join('\n') : '未识别到额外周期风险；仍需结合公告、量价位置和后续承接。'} />
                  </div>
                )}
              </div>
            ) : (
              <div className="h-28 grid place-items-center text-xs text-text-secondary">历史覆盖不足，暂不能生成周期分析</div>
            )}
          </section>

          <section className="border border-border bg-card rounded-md overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1240px] text-sm">
                <thead>
                  <tr className="text-xs text-text-secondary border-b border-border bg-[#0D1117]">
                    <th className="text-left px-4 py-3 font-medium">股票</th>
                    <th className="text-right px-3 font-medium">收盘价</th>
                    <th className="text-right px-3 font-medium">涨跌幅</th>
                    <th className="text-right px-3 font-medium">换手率</th>
                    <th className="text-right px-3 font-medium">榜单买入</th>
                    <th className="text-right px-3 font-medium">榜单卖出</th>
                    <th className="text-right px-3 font-medium">榜单净买</th>
                    <th className="text-right px-3 font-medium">机构席位</th>
                    <th className="text-left px-3 font-medium">上榜原因</th>
                    <th className="text-right px-4 font-medium">个人池</th>
                  </tr>
                </thead>
                <tbody>
                  {data.stocks.map((stock) => (
                    <tr key={`${stock.date}-${stock.code}`} className="border-b border-border/60 last:border-b-0 hover:bg-[#21262D]">
                      <td className="px-4 py-3"><div className="font-medium text-text">{stock.name}</div><div className="font-mono text-xs text-text-secondary mt-0.5">{stock.code}</div></td>
                      <td className="px-3 py-3 text-right font-mono text-text">{number(stock.price)}</td>
                      <td className={`px-3 py-3 text-right font-mono ${getChangeColor(stock.change_pct || 0)}`}>{typeof stock.change_pct === 'number' && stock.change_pct > 0 ? '+' : ''}{number(stock.change_pct)}%</td>
                      <td className="px-3 py-3 text-right font-mono text-text-secondary">{number(stock.turnover)}%</td>
                      <td className="px-3 py-3 text-right font-mono text-text-secondary">{money(stock.buy_amount)}</td>
                      <td className="px-3 py-3 text-right font-mono text-text-secondary">{money(stock.sell_amount)}</td>
                      <td className={`px-3 py-3 text-right font-mono font-medium ${getChangeColor(stock.net_amount || 0)}`}>{money(stock.net_amount)}</td>
                      <td className="px-3 py-3 text-right font-mono text-text-secondary">{stock.institution_count || 0}</td>
                      <td className="px-3 py-3 text-xs text-text-secondary max-w-[320px]"><span className="line-clamp-2" title={stock.reason}>{stock.reason || '--'}</span></td>
                      <td className="px-4 py-3 text-right"><AddToPersonalPoolButton code={stock.code} name={stock.name} thesis={`龙虎榜 ${stock.date}：净买额${money(stock.net_amount)}，${stock.reason || '上榜原因未返回'}`} source="dragon_board" compact /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <div className="mt-3 text-[11px] text-text-secondary">
            龙虎榜为交易所异常交易公开信息，通常盘后更新；上榜和机构席位均不代表未来收益。
          </div>
        </>
      )}
    </div>
  );
}

function Metric({ icon, label, value, accent = false, trend }: { icon: React.ReactNode; label: string; value: string; accent?: boolean; trend?: number }) {
  const valueClass = typeof trend === 'number' ? getChangeColor(trend) : accent ? 'text-accent' : 'text-text';
  return <div className="bg-card px-4 py-3 min-h-[78px]"><div className="flex items-center gap-2 text-xs text-text-secondary">{icon}{label}</div><div className={`mt-2 font-mono text-lg font-semibold ${valueClass}`}>{value}</div></div>;
}

function RankList({ title, rows }: { title: string; rows: RankedStock[] }) {
  return (
    <div>
      <div className="text-[11px] text-text-secondary mb-2">{title}</div>
      <div className="space-y-1.5">
        {rows.slice(0, 4).map((item, index) => (
          <div key={`${title}-${item.code}`} className="grid grid-cols-[18px_1fr_auto] items-center gap-2 text-xs">
            <span className="font-mono text-text-secondary">{index + 1}</span>
            <span className="text-text truncate">{item.name}</span>
            <span className={`font-mono ${getChangeColor(item.net_amount)}`}>{item.appearances > 1 ? `${item.appearances}次` : money(item.net_amount)}</span>
          </div>
        ))}
        {!rows.length && <div className="text-xs text-text-secondary">暂无符合项</div>}
      </div>
    </div>
  );
}

function NarrativeText({ text }: { text: string }) {
  const lines = text.split('\n').map((line) => line.trim()).filter(Boolean);
  return (
    <div className="space-y-1.5 text-xs leading-6 text-text-secondary">
      {lines.map((line, index) => {
        const heading = line.match(/^#{1,6}\s+(.+)$/);
        const bullet = line.match(/^(?:[-*]|\d+[.)])\s+(.+)$/);
        const value = (heading?.[1] || bullet?.[1] || line)
          .replace(/\*\*(.*?)\*\*/g, '$1')
          .replace(/`([^`]+)`/g, '$1');
        if (heading) {
          return <div key={`${index}-${line}`} className="pt-1 font-semibold text-text">{value}</div>;
        }
        if (bullet) {
          return <div key={`${index}-${line}`} className="grid grid-cols-[6px_1fr] gap-2"><span className="mt-[10px] h-1 w-1 rounded-full bg-accent" /><span>{value}</span></div>;
        }
        return <p key={`${index}-${line}`}>{value}</p>;
      })}
    </div>
  );
}
