'use client';

import { useEffect, useState, useCallback } from 'react';
import { FlowRankItem, MarketSummary } from '@/lib/types';
import { apiFetch, formatYi, formatYiShort, getChangeColor } from '@/lib/api';
import FlowRankChart from '@/components/FlowRankChart';
import { RefreshCw, Database, Zap, Calendar } from 'lucide-react';
import CalendarDatePicker from '@/components/CalendarDatePicker';

type TimeRange = 'today' | 'yesterday' | 'week' | 'month' | '3month' | 'year' | 'date';

interface BackfillRun {
  id?: number;
  run_id?: number;
  status: string;
  total_tasks?: number;
  completed_tasks?: number;
  records_written?: number;
  already_running?: boolean;
  error?: string | null;
}

const RANGE_LABELS: Record<TimeRange, string> = {
  today: '今日',
  yesterday: '昨日',
  week: '本周',
  month: '本月',
  '3month': '近3月',
  year: '近1年',
  date: '按日期',
};

export default function ProDashboard() {
  const [conceptData, setConceptData] = useState<FlowRankItem[]>([]);
  const [rawRankings, setRawRankings] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [timeRange, setTimeRange] = useState<TimeRange>('today');
  const [hasData, setHasData] = useState<boolean | null>(null);
  const [generating, setGenerating] = useState(false);
  const [summary, setSummary] = useState<any>(null);
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [backfillRun, setBackfillRun] = useState<BackfillRun | null>(null);

  const fetchByDate = useCallback(async (dateStr: string) => {
    setLoading(true);
    try {
      const res = await apiFetch<any>(`/flow/concept/by-date/${dateStr}`);
      const rankings = res.data.rankings;
      const mapped = rankings.map((r: any, idx: number) => ({
        rank: idx + 1,
        code: r.code,
        name: r.name || r.code,
        close_price: 0,
        change_pct: r.change_pct || 0,
        main_net_inflow: r.main_net_inflow || 0,
        main_net_inflow_pct: r.main_net_inflow_pct || 0,
        super_large_net_inflow: r.super_large_net_inflow || 0,
        large_net_inflow: r.large_net_inflow || 0,
        up_count: r.up_count || 0,
        down_count: r.down_count || 0,
        leading_stock: r.leading_stock || '',
      }));
      setConceptData(mapped);
      setHasData(rankings.length > 0);
      setSummary({ total_main_inflow: mapped.reduce((s: number, i: any) => s + i.main_net_inflow, 0) });
    } catch (err) {
      console.error('Failed to fetch by date:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchData = useCallback(async (range: TimeRange) => {
    setLoading(true);
    try {
      const res = await apiFetch<any>(`/flow/concept/summary?range=${range}&limit=50`);
      const data = res.data;

      setHasData(data.has_data);
      setSummary(data.summary);

      if (data.rankings && data.rankings.length > 0) {
        const mapped = data.rankings.map((r: any, idx: number) => ({
          rank: idx + 1,
          code: r.code,
          name: r.name || r.code,
          close_price: 0,
          change_pct: r.avg_change_pct || 0,
          main_net_inflow: range === 'today' ? r.main_net_inflow : r.avg_daily_inflow || r.total_inflow,
          main_net_inflow_pct: 0,
          super_large_net_inflow: r.super_large_net_inflow || 0,
          large_net_inflow: r.large_net_inflow || 0,
          up_count: r.up_count || 0,
          down_count: r.down_count || 0,
          leading_stock: r.leading_stock || '',
        }));
        setConceptData(mapped);
      } else {
        setConceptData([]);
      }
    } catch (err) {
      console.error('Failed to fetch data:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (timeRange === 'date' && selectedDate) {
      fetchByDate(selectedDate);
    } else {
      fetchData(timeRange);
    }
  }, [timeRange, selectedDate, fetchData, fetchByDate]);

  useEffect(() => {
    const runId = backfillRun?.id ?? backfillRun?.run_id;
    if (!runId || !['queued', 'running'].includes(backfillRun?.status || '')) return;

    const pollRun = async () => {
      try {
        const res = await apiFetch<any>(`/data/backfill/${runId}`);
        const nextRun = res.data as BackfillRun;
        setBackfillRun(nextRun);
        if (['completed', 'partial'].includes(nextRun.status)) {
          await fetchData(timeRange);
        }
      } catch (err) {
        console.error('Failed to poll backfill status:', err);
      }
    };
    pollRun();
    const timer = window.setInterval(pollRun, 10000);
    return () => window.clearInterval(timer);
  }, [backfillRun?.id, backfillRun?.run_id, backfillRun?.status, fetchData, timeRange]);

  const handleDateSelect = (dateStr: string) => {
    setSelectedDate(dateStr);
    setTimeRange('date' as TimeRange);
  };

  const handleGenerateHistory = async () => {
    setGenerating(true);
    try {
      const res = await apiFetch<any>('/data/backfill?days=365&include_stock_bars=true', { method: 'POST' });
      setBackfillRun(res.data);
    } catch (err) {
      console.error('Failed to generate history:', err);
    } finally {
      setGenerating(false);
    }
  };

  const totalInflow = conceptData.reduce((s, i) => s + i.main_net_inflow, 0);
  const inflowCount = conceptData.filter(i => i.main_net_inflow > 0).length;
  const outflowCount = conceptData.filter(i => i.main_net_inflow < 0).length;

  // 排序后的数据
  const sorted = [...conceptData].sort((a, b) => b.main_net_inflow - a.main_net_inflow);
  const top5 = sorted.slice(0, 5);
  const bottom5 = sorted.slice(-5).reverse();
  const hasOutflow = bottom5.some(item => item.main_net_inflow < 0);

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

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      {/* 时间范围选择器 */}
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <span className="text-sm text-text-secondary mr-1">时间范围：</span>
          <div className="flex bg-[#0D1117] border border-border rounded-lg overflow-hidden">
            {(Object.keys(RANGE_LABELS) as TimeRange[]).filter(r => r !== 'date').map((range) => (
              <button
                key={range}
                className={`px-3 py-1.5 text-sm transition-colors ${
                  timeRange === range
                    ? 'bg-accent text-white'
                    : 'text-text-secondary hover:text-text'
                }`}
                onClick={() => setTimeRange(range)}
              >
                {RANGE_LABELS[range]}
              </button>
            ))}
          </div>

          {/* 日历按钮 */}
          <div className="relative">
            <button
              className={`flex items-center gap-1 px-3 py-1.5 text-sm border rounded-lg transition-colors ${
                timeRange === 'date'
                  ? 'bg-accent border-accent text-white'
                  : 'border-border text-text-secondary hover:border-accent hover:text-accent'
              }`}
              onClick={() => setCalendarOpen(!calendarOpen)}
            >
              <Calendar size={14} />
              {timeRange === 'date' && selectedDate ? selectedDate : '选日期'}
            </button>

            {calendarOpen && (
              <div className="absolute top-full left-0 mt-2 z-50">
                <CalendarDatePicker
                  selectedDate={selectedDate}
                  onSelectDate={handleDateSelect}
                  onClose={() => setCalendarOpen(false)}
                />
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          {timeRange === 'today' && (
            <span className="text-xs text-text-secondary flex items-center gap-1">
              <Zap size={12} className="text-warn" />
              实时数据
            </span>
          )}
          {timeRange === 'date' && selectedDate && (
            <span className="text-xs text-text-secondary flex items-center gap-1">
              <Database size={12} />
              {selectedDate}
            </span>
          )}
          {timeRange !== 'today' && timeRange !== 'date' && hasData !== null && (
            <span className="text-xs text-text-secondary flex items-center gap-1">
              <Database size={12} />
              历史汇总
            </span>
          )}
          {backfillRun && (
            <span className="text-xs text-text-secondary" title={backfillRun.error || undefined}>
              回补 {backfillRun.status === 'completed' ? '完成' : backfillRun.status === 'partial' ? '部分完成' : backfillRun.status === 'failed' ? '失败' : '进行中'}
              {backfillRun.total_tasks ? ` ${backfillRun.completed_tasks || 0}/${backfillRun.total_tasks}` : ''}
            </span>
          )}
          <button
            onClick={handleGenerateHistory}
            disabled={generating}
            className="flex items-center gap-1 px-3 py-1.5 text-xs border border-border rounded-md text-text-secondary hover:border-accent hover:text-accent disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={12} className={generating ? 'animate-spin' : ''} />
            {generating ? '已提交...' : '回补近一年真实数据'}
          </button>
        </div>
      </div>

      {/* 无数据提示 */}
      {timeRange === 'date' && hasData === false && (
        <div className="bg-[#D2992222] border border-[#D2992255] rounded-lg p-4 mb-6 text-sm text-warn">
          ⚠️ {selectedDate} 暂无数据。该日期可能是非交易日或数据未收录。请通过日历选择有绿点标记的日期。
        </div>
      )}

      {timeRange !== 'today' && timeRange !== 'date' && hasData === false && (
        <div className="bg-[#D2992222] border border-[#D2992255] rounded-lg p-4 mb-6 text-sm text-warn">
          该时间段暂无已验证的历史数据。可提交近一年真实数据回补任务。
        </div>
      )}

      {timeRange === 'today' && conceptData.length === 0 && (
        <div className="bg-[#D2992222] border border-[#D2992255] rounded-lg p-4 mb-6 text-sm text-warn">
          ⚠️ 当前非交易时段，实时数据为空。请切换到「昨日」或点击日历选择历史日期查看。
        </div>
      )}

      {/* 大盘概览卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="text-xs text-text-secondary mb-1">资金净额</div>
          <div className={`text-xl font-mono font-bold ${getChangeColor(totalInflow)}`}>
            {formatYi(totalInflow)}
          </div>
          <div className="text-xs text-text-secondary mt-0.5">
            {timeRange === 'date' && selectedDate ? selectedDate : RANGE_LABELS[timeRange]}
          </div>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="text-xs text-text-secondary mb-1">流入板块</div>
          <div className="text-xl font-mono font-bold text-up">{inflowCount}个</div>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="text-xs text-text-secondary mb-1">流出板块</div>
          <div className="text-xl font-mono font-bold text-down">{outflowCount}个</div>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="text-xs text-text-secondary mb-1">板块总数</div>
          <div className="text-xl font-mono font-bold text-text">{conceptData.length}个</div>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="text-xs text-text-secondary mb-1">数据来源</div>
          <div className="text-sm font-medium text-accent">
            {timeRange === 'today' ? '实时接口' : timeRange === 'date' ? '历史数据' : '数据库汇总'}
          </div>
        </div>
      </div>

      {/* 排名图表 */}
      {conceptData.length > 0 && (
        <div className="bg-card border border-border rounded-lg p-6 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-bold text-text">
              {timeRange === 'date' && selectedDate
                ? `${selectedDate} · 概念板块资金排名`
                : `${RANGE_LABELS[timeRange] || ''} · 概念板块资金排名`
              }
              {timeRange !== 'today' && timeRange !== 'date' && (
                <span className="text-sm text-text-secondary ml-2 font-normal">
                  （日均净流入排名）
                </span>
              )}
            </h3>
          </div>
          <FlowRankChart data={conceptData} />
        </div>
      )}

      {/* TOP5 / BOTTOM5 表格 */}
      {conceptData.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="bg-card border border-border rounded-lg p-6">
            <h3 className="text-lg font-bold text-up mb-4">🟢 资金流入 TOP5</h3>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-text-secondary text-left border-b border-border">
                  <th className="pb-2 font-medium">板块</th>
                  <th className="pb-2 font-medium text-right">涨跌幅</th>
                  <th className="pb-2 font-medium text-right">
                    {timeRange === 'today' || timeRange === 'date' ? '净流入' : '日均流入'}
                  </th>
                </tr>
              </thead>
              <tbody>
                {top5.map((item) => (
                  <tr key={item.code} className="border-b border-border/50">
                    <td className="py-2.5 font-medium">{item.name}</td>
                    <td className={`py-2.5 text-right ${getChangeColor(item.change_pct)}`}>
                      {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                    </td>
                    <td className={`py-2.5 text-right font-mono ${getChangeColor(item.main_net_inflow)}`}>
                      {formatYiShort(item.main_net_inflow)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="bg-card border border-border rounded-lg p-6">
            <h3 className={`text-lg font-bold mb-4 ${hasOutflow ? 'text-down' : 'text-text-secondary'}`}>
              {hasOutflow ? '资金流出 TOP5' : '净流入最低 TOP5'}
            </h3>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-text-secondary text-left border-b border-border">
                  <th className="pb-2 font-medium">板块</th>
                  <th className="pb-2 font-medium text-right">涨跌幅</th>
                  <th className="pb-2 font-medium text-right">
                    {timeRange === 'today' ? '净流入' : '日均流入'}
                  </th>
                </tr>
              </thead>
              <tbody>
                {bottom5.map((item) => (
                  <tr key={item.code} className="border-b border-border/50">
                    <td className="py-2.5 font-medium">{item.name}</td>
                    <td className={`py-2.5 text-right ${getChangeColor(item.change_pct)}`}>
                      {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                    </td>
                    <td className={`py-2.5 text-right font-mono ${getChangeColor(item.main_net_inflow)}`}>
                      {formatYiShort(item.main_net_inflow)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
