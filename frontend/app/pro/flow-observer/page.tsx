'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  BrainCircuit,
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
import { apiFetch } from '@/lib/api';
import FlowObserverCanvas, {
  FlowObserverBoardType,
  FlowObserverMode,
  ObserverDateEntry,
  ObserverFlowData,
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

type AnalysisWindow = 'week' | 'two_weeks' | 'month';

interface FlowAnalysisData {
  available: boolean;
  board_label: string;
  window: { id: AnalysisWindow; label: string; sessions: number };
  period: { start: string | null; end: string | null };
  coverage: { actual_sessions: number; requested_sessions: number; board_count: number; complete: boolean };
  analysis: {
    score: number; tone: string; headline: string; summary: string;
    latest_breadth_pct: number; concentration_top3_pct: number; aggregate_inflow: number;
    top_inflows: Array<{ code: string; name: string; total_inflow: number; positive_days: number }>;
    top_outflows: Array<{ code: string; name: string; total_inflow: number; negative_days: number }>;
    turning_positive: Array<{ code: string; name: string; latest_inflow: number }>;
    turning_negative: Array<{ code: string; name: string; latest_inflow: number }>;
    suggestions: string[]; risks: string[];
  };
  ai_narrative: string | null;
  ai_generated: boolean;
  method: string;
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
  const [analysisWindow, setAnalysisWindow] = useState<AnalysisWindow>('week');
  const [analysis, setAnalysis] = useState<FlowAnalysisData | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

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
  const historyDateValues = useMemo(() => dates.map((item) => item.date), [dates]);

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

  const runAnalysis = useCallback(async (window: AnalysisWindow) => {
    setAnalysisWindow(window);
    setAnalysisLoading(true);
    setAnalysisError(null);
    try {
      const response = await apiFetch<{ code: number; data: FlowAnalysisData }>('/flow/observer/analysis', {
        method: 'POST', body: JSON.stringify({ board_type: boardType, window }),
      });
      setAnalysis(response.data);
    } catch (caught) {
      setAnalysisError(caught instanceof Error ? caught.message : '资金流分析暂时不可用');
    } finally {
      setAnalysisLoading(false);
    }
  }, [boardType]);

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
    setAnalysis(null);
    setAnalysisError(null);
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

  const isRealtimeSnapshot = observer?.is_realtime === true;
  const statusLabel = observer
    ? isRealtimeSnapshot
      ? '实时'
      : mode === 'history'
        ? '历史缓存'
        : '休市缓存'
    : mode === 'history' ? '历史缓存' : '行情加载中';
  const sourceLabel = observer
    ? isRealtimeSnapshot
      ? observer.source === 'eastmoney' ? '东方财富实时源' : `${observer.source}实时源`
      : mode === 'history' ? '本地缓存' : '最近有效缓存'
    : mode === 'history' ? '本地缓存' : '等待数据源';

  const summaryCards = [
    {
      label: '展示流入',
      value: formatSignedAmount(observer?.summary.inflow_total),
      hint: `TOP ${observer?.summary.requested_limit ?? limit} · ${observer?.summary.inflow_count ?? 0} 个板块`,
    },
    {
      label: '展示流出',
      value: formatSignedAmount(observer?.summary.outflow_total),
      hint: `TOP ${observer?.summary.requested_limit ?? limit} · ${observer?.summary.outflow_count ?? 0} 个板块`,
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
            <span className={`inline-block h-2 w-2 rounded-full ${isRealtimeSnapshot ? 'bg-down' : mode === 'history' ? 'bg-accent' : 'bg-warn'}`} />
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
          <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 ${isRealtimeSnapshot ? 'border-[#26A69A44] bg-[#26A69A18] text-down' : mode === 'history' ? 'border-[#58A6FF44] bg-[#58A6FF18] text-accent' : 'border-[#D2992244] bg-[#D2992218] text-warn'}`}>
            {isRealtimeSnapshot ? <Zap size={12} /> : <Database size={12} />}
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
              {isRealtimeSnapshot ? '15 秒自动刷新' : '15 秒自动检查'}
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

      <section className="mx-auto min-w-0 max-w-[1120px] overflow-hidden rounded-xl border border-border bg-[#020303]">
        <div className="h-[820px] min-w-0 sm:h-[780px] lg:h-[840px]">
          <FlowObserverCanvas
            data={observer}
            mode={mode}
            playbackProgress={mode === 'history' && dates.length > 1 && currentIndex >= 0 ? currentIndex / (dates.length - 1) : undefined}
            historyDates={mode === 'history' ? historyDateValues : []}
          />
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/70 bg-[#060908] px-4 py-3 text-xs text-text-secondary">
          <span>{observer?.flow_inference?.label || '板块迁移为净流量推断'}</span>
          <span>真实净流量 · 推断迁移路径 · 不代表逐笔资金去向</span>
        </div>
      </section>

      <section className="mt-4 rounded-lg border border-border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="flex items-center gap-2 text-sm font-semibold text-text"><BrainCircuit size={16} className="text-warn" />资金流周期分析</h2>{analysis && <p className="mt-1 text-xs text-text-secondary">{analysis.period.start || '--'} 至 {analysis.period.end || '--'} · {analysis.coverage.actual_sessions}/{analysis.coverage.requested_sessions} 个交易日</p>}</div>
          <div className="flex items-center gap-2">
            <div className="flex rounded-md bg-[#0D1117] p-1" role="group" aria-label="资金流分析周期">{([['week', '一周'], ['two_weeks', '两周'], ['month', '一月']] as const).map(([id, label]) => <button key={id} type="button" onClick={() => { setAnalysisWindow(id); if (analysis) void runAnalysis(id); }} className={`px-3 py-1.5 text-xs rounded ${analysisWindow === id ? 'bg-[#1F6FEB33] text-accent' : 'text-text-secondary hover:text-text'}`}>{label}</button>)}</div>
            <button type="button" onClick={() => runAnalysis(analysisWindow)} disabled={analysisLoading} className="inline-flex h-9 items-center gap-1.5 rounded-md bg-accent px-3 text-xs text-white disabled:opacity-50">{analysisLoading ? <RefreshCw size={13} className="animate-spin" /> : <BrainCircuit size={13} />}分析</button>
          </div>
        </div>
        {analysisError && <div className="mt-3 text-xs text-up">{analysisError}</div>}
        {analysisLoading && !analysis && <div className="mt-4 flex items-center gap-2 border-t border-border pt-4 text-xs text-text-secondary"><RefreshCw size={13} className="animate-spin text-accent" />聚合历史板块资金并生成解读</div>}
        {analysis && <div className="mt-4 border-t border-border pt-4">
          <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><div className="text-sm font-medium text-text">{analysis.analysis.headline}</div><p className="mt-1 text-xs leading-5 text-text-secondary">{analysis.analysis.summary}</p></div><span className={`shrink-0 border rounded px-2 py-1 text-xs ${analysis.analysis.score >= 62 ? 'border-up/50 text-up' : analysis.analysis.score <= 38 ? 'border-down/50 text-down' : 'border-warn/50 text-warn'}`}>{analysis.analysis.tone} · {analysis.analysis.score.toFixed(0)}</span></div>
          <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4 text-xs"><AnalysisMetric label="周期净流入" value={formatSignedAmount(analysis.analysis.aggregate_inflow)} /><AnalysisMetric label="最新资金广度" value={`${analysis.analysis.latest_breadth_pct.toFixed(1)}%`} /><AnalysisMetric label="前三集中度" value={`${analysis.analysis.concentration_top3_pct.toFixed(1)}%`} /><AnalysisMetric label="覆盖板块" value={`${analysis.coverage.board_count}个`} /></div>
          <div className="mt-4 grid gap-4 lg:grid-cols-2"><AnalysisList title="持续流入 / 转强" items={[...analysis.analysis.top_inflows.slice(0, 3).map((item) => `${item.name} ${formatSignedAmount(item.total_inflow)}`), ...analysis.analysis.turning_positive.slice(0, 2).map((item) => `${item.name} 刚转强`)]} tone="text-up" /><AnalysisList title="持续流出 / 转弱" items={[...analysis.analysis.top_outflows.slice(0, 3).map((item) => `${item.name} ${formatSignedAmount(item.total_inflow)}`), ...analysis.analysis.turning_negative.slice(0, 2).map((item) => `${item.name} 刚转弱`)]} tone="text-down" /></div>
          <div className="mt-4 grid gap-4 border-t border-border pt-4 lg:grid-cols-2"><AnalysisList title="观察建议" items={analysis.analysis.suggestions} tone="text-accent" /><AnalysisList title="风险与缺口" items={analysis.analysis.risks.length ? analysis.analysis.risks : ['当前覆盖未发现额外数据缺口']} tone="text-warn" /></div>
          {analysis.ai_narrative && <div className="mt-4 border-l-2 border-warn pl-3"><div className="text-xs font-medium text-text">AI 综合解读</div><p className="mt-1 whitespace-pre-line text-xs leading-6 text-text-secondary">{analysis.ai_narrative}</p></div>}
        </div>}
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

function AnalysisMetric({ label, value }: { label: string; value: string }) {
  return <div><div className="text-text-secondary">{label}</div><div className="mt-1 font-mono text-base text-text">{value}</div></div>;
}

function AnalysisList({ title, items, tone }: { title: string; items: string[]; tone: string }) {
  return <div><div className={`text-xs font-medium ${tone}`}>{title}</div><div className="mt-2 space-y-1.5">{items.length ? items.map((item, index) => <p key={`${item}-${index}`} className="text-xs leading-5 text-text-secondary">{item}</p>) : <p className="text-xs text-text-secondary">--</p>}</div></div>;
}
