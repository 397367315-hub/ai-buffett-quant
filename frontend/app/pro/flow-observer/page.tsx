'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Database,
  Pause,
  Play,
  RefreshCw,
  SlidersHorizontal,
  Zap,
} from 'lucide-react';
import { apiFetch, getChangeColor } from '@/lib/api';
import FlowObserverCanvas, {
  FlowObserverBoardType,
  FlowObserverMode,
  ObserverDateEntry,
  ObserverFlowData,
  ObserverRow,
} from '@/components/FlowObserverCanvas';

interface ObserverDatesResponse {
  board_type: FlowObserverBoardType;
  dates: ObserverDateEntry[];
  count: number;
  source: string;
}

interface ObserverApiResponse {
  code: number;
  data: ObserverFlowData;
}

const MODE_OPTIONS: Array<{ id: FlowObserverMode; label: string; icon: typeof Zap }> = [
  { id: 'live', label: '实时', icon: Zap },
  { id: 'history', label: '历史', icon: Clock3 },
];

const BOARD_OPTIONS: Array<{ id: FlowObserverBoardType; label: string }> = [
  { id: 'industry', label: '行业板块' },
  { id: 'concept', label: '概念板块' },
];

const LIMIT_OPTIONS = [4, 6, 8, 9, 10, 12];

function formatTime(value: string | null | undefined): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  }).format(date);
}

function formatAmount(value: number | undefined): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${(value / 1e8).toFixed(2)}亿`;
}

function formatSignedAmount(value: number | undefined): string {
  if (value == null || Number.isNaN(value)) return '--';
  const sign = value >= 0 ? '+' : '';
  return `${sign}${(value / 1e8).toFixed(2)}亿`;
}

function formatPercent(value: number | undefined): string {
  if (value == null || Number.isNaN(value)) return '--';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

function formatDateBadge(date: ObserverDateEntry): string {
  return `${date.date} · ${date.board_count} 个板块 · ${date.is_complete ? '完整' : '部分'}`;
}

function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="text-xs text-text-secondary">{label}</div>
      <div className="mt-1 text-lg font-semibold text-text truncate">{value}</div>
      <div className="mt-1 text-xs text-text-secondary truncate">{hint}</div>
    </div>
  );
}

function FlowListPanel({
  title,
  items,
  tone,
  emptyText,
}: {
  title: string;
  items: ObserverRow[];
  tone: 'positive' | 'negative';
  emptyText: string;
}) {
  const maxAbs = Math.max(1, ...items.map((item) => Math.abs(item.main_net_inflow || 0)));
  const isPositive = tone === 'positive';
  const accent = isPositive ? 'text-up' : 'text-down';
  const ring = isPositive ? 'border-[#EF535044] bg-[#EF535018]' : 'border-[#26A69A44] bg-[#26A69A18]';
  const marker = isPositive ? 'bg-up' : 'bg-down';

  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className={`flex items-center gap-2 text-sm font-bold ${accent}`}>
            {isPositive ? <ArrowUpRight size={16} /> : <ArrowDownRight size={16} />}
            {title}
          </h2>
        </div>
        <span className="shrink-0 text-xs text-text-secondary">{items.length} 个板块</span>
      </div>

      <div className="space-y-2 max-h-[620px] overflow-y-auto pr-1">
        {items.length > 0 ? (
          items.map((item, index) => {
            const barWidth = Math.max(8, Math.round((Math.abs(item.main_net_inflow || 0) / maxAbs) * 100));
            return (
              <article key={item.code} className={`rounded-lg border px-3 py-2 ${ring}`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-mono text-white ${marker}`}>
                        {index + 1}
                      </span>
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-text" title={item.name}>
                          {item.name}
                        </div>
                        <div className="truncate text-[11px] text-text-secondary">
                          {item.code}
                          {item.leading_stock ? ` · 领涨 ${item.leading_stock}` : ''}
                        </div>
                      </div>
                    </div>
                  </div>
                  <div className={`shrink-0 text-right font-mono text-sm font-semibold ${getChangeColor(item.main_net_inflow)}`}>
                    {formatSignedAmount(item.main_net_inflow)}
                  </div>
                </div>

                <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] text-text-secondary">
                  <div>涨 {item.up_count} / 跌 {item.down_count}</div>
                  <div className="text-right">{formatPercent(item.change_pct)}</div>
                </div>

                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#0D1117]">
                  <div
                    className={`h-full rounded-full ${marker}`}
                    style={{ width: `${barWidth}%` }}
                  />
                </div>
              </article>
            );
          })
        ) : (
          <div className="rounded-lg border border-dashed border-border px-3 py-4 text-xs text-text-secondary">
            {emptyText}
          </div>
        )}
      </div>
    </section>
  );
}

export default function FlowObserverPage() {
  const [mode, setMode] = useState<FlowObserverMode>('live');
  const [boardType, setBoardType] = useState<FlowObserverBoardType>('industry');
  const [limit, setLimit] = useState(9);
  const [observer, setObserver] = useState<ObserverFlowData | null>(null);
  const [dates, setDates] = useState<ObserverDateEntry[]>([]);
  const [selectedDate, setSelectedDate] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [datesLoading, setDatesLoading] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const observerLoadedRef = useRef(false);
  const observerRequestRef = useRef(0);
  const datesRequestRef = useRef(0);
  const selectedDateRef = useRef('');

  const currentIndex = useMemo(() => {
    if (!selectedDate) return -1;
    return dates.findIndex((item) => item.date === selectedDate);
  }, [dates, selectedDate]);

  const currentDateEntry = currentIndex >= 0 ? dates[currentIndex] : null;
  const progress = dates.length > 1 && currentIndex >= 0 ? (currentIndex / (dates.length - 1)) * 100 : 0;

  useEffect(() => {
    selectedDateRef.current = selectedDate;
  }, [selectedDate]);

  const loadDates = useCallback(async (nextBoardType: FlowObserverBoardType) => {
    const requestId = ++datesRequestRef.current;
    setDatesLoading(true);
    try {
      const res = await apiFetch<{ code: number; data: ObserverDatesResponse }>(`/flow/observer/dates?board_type=${nextBoardType}`);
      if (requestId !== datesRequestRef.current) return;
      const nextDates = res.data.dates || [];
      setDates(nextDates);
      if (nextDates.length > 0 && mode === 'history') {
        const currentSelected = selectedDateRef.current;
        const existing = nextDates.find((item) => item.date === currentSelected);
        setSelectedDate((existing || nextDates[nextDates.length - 1])?.date || '');
      } else if (mode === 'history' && nextDates.length === 0) {
        setLoading(false);
      }
    } catch (fetchError) {
      console.error('Failed to load observer dates:', fetchError);
      if (requestId === datesRequestRef.current) {
        setDates([]);
        if (mode === 'history') {
          setSelectedDate('');
          setLoading(false);
        }
      }
    } finally {
      if (requestId === datesRequestRef.current) {
        setDatesLoading(false);
      }
    }
  }, [mode]);

  const fetchObserver = useCallback(async (date: string | null, silent = false) => {
    const requestId = ++observerRequestRef.current;
    if (silent || observerLoadedRef.current) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    try {
      const params = new URLSearchParams({
        board_type: boardType,
        limit: String(limit),
      });
      if (date) {
        params.set('date', date);
      }
      const res = await apiFetch<ObserverApiResponse>(`/flow/observer?${params.toString()}`);
      if (requestId !== observerRequestRef.current) return;
      setObserver(res.data);
      observerLoadedRef.current = true;
      setError(null);
    } catch (fetchError) {
      console.error('Failed to load observer snapshot:', fetchError);
      if (requestId === observerRequestRef.current) {
        setError('资金流观察数据暂时不可用，请稍后再试。');
      }
    } finally {
      if (requestId === observerRequestRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [boardType, limit]);

  useEffect(() => {
    void loadDates(boardType);
    setPlaying(false);
  }, [boardType, loadDates]);

  useEffect(() => {
    if (mode !== 'history') {
      setPlaying(false);
      return;
    }
    if (datesLoading || dates.length === 0) return;
    if (!selectedDate || currentIndex < 0) {
      setSelectedDate(dates[dates.length - 1].date);
    }
  }, [currentIndex, dates, datesLoading, mode, selectedDate]);

  useEffect(() => {
    if (mode === 'history') {
      if (datesLoading || !selectedDate) return;
      void fetchObserver(selectedDate, false);
      return;
    }
    void fetchObserver(null, false);
  }, [datesLoading, fetchObserver, mode, selectedDate]);

  useEffect(() => {
    if (mode !== 'live') return;
    const timer = window.setInterval(() => {
      void fetchObserver(null, true);
    }, 15000);
    return () => window.clearInterval(timer);
  }, [fetchObserver, mode]);

  useEffect(() => {
    if (mode !== 'history' || !playing || datesLoading || dates.length === 0) return;
    const timer = window.setInterval(() => {
      setSelectedDate((current) => {
        const index = dates.findIndex((item) => item.date === current);
        if (index < 0) {
          return dates[0].date;
        }
        if (index >= dates.length - 1) {
          setPlaying(false);
          return current;
        }
        return dates[index + 1].date;
      });
    }, 1600);
    return () => window.clearInterval(timer);
  }, [dates, datesLoading, mode, playing]);

  const handleManualRefresh = () => {
    const activeDate = mode === 'history' ? selectedDate || null : null;
    void fetchObserver(activeDate, false);
  };

  const handleDateChange = (nextDate: string) => {
    setPlaying(false);
    setSelectedDate(nextDate);
  };

  const handleModeChange = (nextMode: FlowObserverMode) => {
    if (nextMode === mode) return;
    observerRequestRef.current += 1;
    observerLoadedRef.current = false;
    setPlaying(false);
    setObserver(null);
    setError(null);
    setMode(nextMode);
  };

  const handleBoardTypeChange = (nextBoardType: FlowObserverBoardType) => {
    observerRequestRef.current += 1;
    observerLoadedRef.current = false;
    setPlaying(false);
    setObserver(null);
    setError(null);
    setSelectedDate('');
    setDates([]);
    setBoardType(nextBoardType);
  };

  const handleStep = (delta: number) => {
    if (dates.length === 0) return;
    setPlaying(false);
    const baseIndex = currentIndex >= 0 ? currentIndex : dates.length - 1;
    const nextIndex = Math.max(0, Math.min(dates.length - 1, baseIndex + delta));
    setSelectedDate(dates[nextIndex].date);
  };

  const togglePlayback = () => {
    if (dates.length === 0) return;
    if (playing) {
      setPlaying(false);
      return;
    }
    if (currentIndex >= dates.length - 1) {
      setSelectedDate(dates[0].date);
    }
    setPlaying(true);
  };

  const statusLabel = mode === 'live' ? '实时' : '历史缓存';
  const sourceLabel = mode === 'history' ? '本地缓存' : '东方财富实时源';

  const summaryCards = [
    {
      label: '流入合计',
      value: formatSignedAmount(observer?.summary.inflow_total),
      hint: `${observer?.summary.inflow_count ?? 0} 个流入板块`,
    },
    {
      label: '流出合计',
      value: formatSignedAmount(observer?.summary.outflow_total),
      hint: `${observer?.summary.outflow_count ?? 0} 个流出板块`,
    },
    {
      label: mode === 'live' ? '沪市成交额' : '历史覆盖',
      value: mode === 'live'
        ? formatAmount(observer?.market?.sh_amount)
        : observer?.history_coverage
          ? (observer.history_coverage.is_complete ? '完整' : '部分')
          : '未知',
      hint: mode === 'live'
        ? `上证 ${observer?.market?.sh_index?.toFixed(2) ?? '--'} · ${formatPercent(observer?.market?.sh_change_pct)}`
        : observer?.history_coverage
          ? `${observer.history_coverage.snapshot_board_count}/${observer.history_coverage.directory_board_count}`
          : '历史缓存信息未返回',
    },
    {
      label: '更新时间',
      value: formatTime(observer?.updated_at),
      hint: observer?.data_date ? `数据日期 ${observer.data_date}` : '等待数据日期',
    },
  ] as const;

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <h1 className="flex items-center gap-2 text-2xl font-bold text-text">
          <Activity size={22} className="text-accent" />
          资金流观察
        </h1>
        <div className="text-right text-xs text-text-secondary">
          <div className="flex items-center justify-end gap-1.5">
            <span className={`inline-block h-2 w-2 rounded-full ${mode === 'live' ? 'bg-down' : 'bg-accent'}`} />
            {statusLabel} · {sourceLabel}
          </div>
          <div>更新 {formatTime(observer?.updated_at)}</div>
        </div>
      </div>

      <section className="mb-4 rounded-xl border border-border bg-card p-4">
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-end">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)]">
            <div className="block min-w-0">
              <span className="mb-1.5 flex items-center gap-1.5 text-xs text-text-secondary">
                <Zap size={12} className="text-warn" />
                模式
              </span>
              <div className="grid grid-cols-2 gap-1 rounded-md bg-[#0D1117] p-1" role="group" aria-label="观察模式">
                {MODE_OPTIONS.map((item) => {
                  const Icon = item.icon;
                  const active = mode === item.id;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => handleModeChange(item.id)}
                      aria-pressed={active}
                      className={`flex min-h-10 items-center justify-center gap-2 rounded-md px-3 text-sm transition-colors ${active ? 'bg-[#1F6FEB33] text-text' : 'text-text-secondary hover:text-text'}`}
                    >
                      <Icon size={14} />
                      <span>{item.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            <label className="block min-w-0">
              <span className="mb-1.5 flex items-center gap-1.5 text-xs text-text-secondary">
                <Activity size={12} className="text-accent" />
                板块类型
              </span>
              <select
                value={boardType}
                onChange={(event) => handleBoardTypeChange(event.target.value as FlowObserverBoardType)}
                className="w-full rounded-md border border-border bg-[#0D1117] px-3 py-2 text-sm text-text focus:border-accent focus:outline-none"
              >
                {BOARD_OPTIONS.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="block min-w-0">
              <span className="mb-1.5 flex items-center gap-1.5 text-xs text-text-secondary">
                <SlidersHorizontal size={12} className="text-text-secondary" />
                显示数量
              </span>
              <select
                value={limit}
                onChange={(event) => setLimit(Number(event.target.value))}
                className="w-full rounded-md border border-border bg-[#0D1117] px-3 py-2 text-sm text-text focus:border-accent focus:outline-none"
              >
                {LIMIT_OPTIONS.map((item) => (
                  <option key={item} value={item}>
                    {item} 组
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2">
            {mode === 'history' && (
              <>
              <button
                type="button"
                onClick={() => handleStep(-1)}
                disabled={datesLoading || dates.length === 0 || currentIndex <= 0}
                className="flex min-h-10 items-center gap-1 rounded-md border border-border px-3 py-2 text-xs text-text-secondary transition-colors hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
                title="上一条缓存"
              >
                  <ChevronLeft size={14} />
                </button>
              <button
                type="button"
                onClick={togglePlayback}
                disabled={datesLoading || dates.length === 0}
                className="flex min-h-10 items-center gap-1 rounded-md border border-border px-3 py-2 text-xs text-text-secondary transition-colors hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
                title={playing ? '暂停回放' : '开始回放'}
              >
                  {playing ? <Pause size={14} /> : <Play size={14} />}
                  {playing ? '暂停' : '回放'}
                </button>
              <button
                type="button"
                onClick={() => handleStep(1)}
                disabled={datesLoading || dates.length === 0 || currentIndex >= dates.length - 1}
                className="flex min-h-10 items-center gap-1 rounded-md border border-border px-3 py-2 text-xs text-text-secondary transition-colors hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
                title="下一条缓存"
              >
                  <ChevronRight size={14} />
                </button>
              </>
            )}
            <button
              type="button"
              onClick={handleManualRefresh}
              disabled={mode === 'history' && (!selectedDate || datesLoading)}
              className="flex min-h-10 items-center gap-1.5 rounded-md bg-accent px-3 py-2 text-xs font-medium text-white transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              title="手动刷新"
            >
              <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
              {refreshing ? '刷新中' : '刷新'}
            </button>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-text-secondary">
          <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 ${mode === 'live' ? 'border-[#26A69A44] bg-[#26A69A18] text-down' : 'border-[#58A6FF44] bg-[#58A6FF18] text-accent'}`}>
            {mode === 'live' ? <Zap size={12} /> : <Database size={12} />}
            {statusLabel}
          </span>
          <span className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1">
            <Database size={12} />
            {sourceLabel}
          </span>
          <span className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1">
            <Clock3 size={12} />
            {observer?.data_date || '--'}
          </span>
          {mode === 'live' ? (
            <span className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1">
              <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
              15 秒自动刷新
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1">
              <Clock3 size={12} />
              {datesLoading ? '历史缓存加载中' : `${dates.length} 个缓存日`}
            </span>
          )}
          {observer?.history_coverage && mode === 'history' && (
            <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 ${observer.history_coverage.is_complete ? 'border-[#26A69A44] bg-[#26A69A18] text-down' : 'border-[#D2992244] bg-[#D2992218] text-warn'}`}>
              {observer.history_coverage.is_complete ? '完整缓存' : '部分缓存'}
            </span>
          )}
        </div>

        {mode === 'history' && dates.length > 0 && (
          <div className="mt-4 space-y-2">
            <div className="flex items-center justify-between gap-2 text-xs text-text-secondary">
              <span>
                {currentDateEntry ? formatDateBadge(currentDateEntry) : '请选择一个缓存日期'}
              </span>
              <span>
                {currentIndex >= 0 ? `${currentIndex + 1}/${dates.length}` : `0/${dates.length}`}
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-[#0D1117]">
              <div className="h-full bg-accent transition-all" style={{ width: `${progress}%` }} />
            </div>
            <select
              value={selectedDate}
              onChange={(event) => handleDateChange(event.target.value)}
              disabled={datesLoading}
              className="w-full rounded-md border border-border bg-[#0D1117] px-3 py-2 text-sm text-text focus:border-accent focus:outline-none"
            >
              {dates.map((item) => (
                <option key={item.date} value={item.date}>
                  {formatDateBadge(item)}
                </option>
              ))}
            </select>
          </div>
        )}
      </section>

      {error && (
        <div className="mb-4 rounded-lg border border-[#EF535055] bg-[#EF535018] px-4 py-3 text-sm text-up">
          {error}
        </div>
      )}

      <section className="grid gap-4 xl:grid-cols-[minmax(0,0.84fr)_minmax(0,1.32fr)_minmax(0,0.84fr)]">
        <FlowListPanel
          title="左侧流出"
          tone="negative"
          items={[...(observer?.outflows || [])].slice(0, limit)}
          emptyText={mode === 'history' && datesLoading ? '正在加载历史缓存' : '暂无流出板块'}
        />

        <div className="order-first rounded-xl border border-border bg-[#05070A] xl:order-none">
          <div className="border-b border-border/70 px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm font-semibold text-text">
                  {observer?.board_label || (boardType === 'industry' ? '行业板块' : '概念板块')}
                </div>
                <div className="mt-0.5 text-xs text-text-secondary">
                  {mode === 'live'
                    ? sourceLabel
                    : observer?.history_coverage
                      ? `历史缓存 · ${observer.history_coverage.is_complete ? '完整' : '部分'}`
                      : '历史缓存回放'}
                </div>
              </div>
              <div className="text-right text-xs text-text-secondary">
                <div>{statusLabel}</div>
                <div>{formatTime(observer?.updated_at)}</div>
              </div>
            </div>
          </div>
          <div className="h-[520px] sm:h-[620px] lg:h-[720px]">
            <FlowObserverCanvas data={observer} mode={mode} />
          </div>
        </div>

        <FlowListPanel
          title="右侧流入"
          tone="positive"
          items={[...(observer?.inflows || [])].slice(0, limit)}
          emptyText={mode === 'history' && datesLoading ? '正在加载历史缓存' : '暂无流入板块'}
        />
      </section>

      <section className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {summaryCards.map((item) => (
          <StatCard key={item.label} {...item} />
        ))}
      </section>

      <section className="mt-4 rounded-xl border border-border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-text">来源状态</h2>
          <div className="flex flex-wrap gap-2 text-xs text-text-secondary">
            <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 ${observer?.source_status.inflows ? 'border-[#26A69A44] bg-[#26A69A18] text-down' : 'border-border'}`}>
              流入 {observer?.source_status.inflows ? '已接入' : '未接入'}
            </span>
            <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 ${observer?.source_status.outflows ? 'border-[#26A69A44] bg-[#26A69A18] text-down' : 'border-border'}`}>
              流出 {observer?.source_status.outflows ? '已接入' : '未接入'}
            </span>
            <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 ${observer?.source_status.market ? 'border-[#58A6FF44] bg-[#58A6FF18] text-accent' : 'border-border'}`}>
              市场 {observer?.source_status.market ? '已接入' : '未接入'}
            </span>
          </div>
        </div>
        {observer?.history_coverage && mode === 'history' && (
          <div className="mt-3 text-xs text-text-secondary">
            历史覆盖：{observer.history_coverage.snapshot_board_count}/{observer.history_coverage.directory_board_count} ·
            {observer.history_coverage.is_complete ? '完整快照' : '缺口快照'}
          </div>
        )}
        {datesLoading && mode === 'history' && (
          <div className="mt-3 flex items-center gap-2 text-xs text-text-secondary">
            <RefreshCw size={12} className="animate-spin" />
            正在读取缓存日期列表
          </div>
        )}
        {loading && !observer && (
          <div className="mt-3 text-xs text-text-secondary">正在加载资金流观察数据...</div>
        )}
      </section>
    </div>
  );
}
