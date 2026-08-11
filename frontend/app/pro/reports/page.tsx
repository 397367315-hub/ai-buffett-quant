'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CalendarClock, CalendarDays, CheckCircle2, Database, Loader2, RefreshCw } from 'lucide-react';
import PersonalWorkspaceNav from '@/components/PersonalWorkspaceNav';
import StockKlineButton from '@/components/StockKlineButton';
import { apiFetch } from '@/lib/api';

interface UpcomingReport {
  code: string;
  name: string;
  pool: string;
  holding: boolean;
  relation: string;
  report_type: string;
  publish_date: string | null;
  actual_publish_date: string | null;
  days_until: number | null;
  changed: boolean;
  source: string;
}

interface PublishedReport {
  code: string;
  name: string;
  pool: string;
  holding: boolean;
  relation: string;
  report_type: string;
  notice_date: string | null;
  report_date: string | null;
  metrics: {
    revenue: number | null;
    net_profit: number | null;
    revenue_growth_pct: number | null;
    profit_growth_pct: number | null;
    gross_margin_pct: number | null;
    roe_pct: number | null;
    cashflow_per_share: number | null;
    debt_ratio_pct: number | null;
  };
  comparison: {
    previous_report_type: string | null;
    previous_revenue_growth_pct: number | null;
    previous_profit_growth_pct: number | null;
    revenue_acceleration_pct: number | null;
    profit_acceleration_pct: number | null;
  };
  anomalies: string[];
  source: string;
}

interface ReportDashboard {
  updated_at: string;
  snapshot_updated_at: string;
  universe_count: number;
  upcoming: UpcomingReport[];
  published: PublishedReport[];
  source_status: { appointments: string; financials: string };
  cache_used: boolean;
  automation: {
    extract_metrics: boolean;
    compare_previous: boolean;
    flag_anomalies: boolean;
    update_selection_features: boolean;
    push_configured: boolean;
    message: string;
  };
  disclaimer: string;
}

const number = (value: number | null | undefined, digits = 1) => value == null ? '--' : value.toFixed(digits);
const signed = (value: number | null | undefined) => value == null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`;
const tone = (value: number | null | undefined) => value == null ? 'text-text-secondary' : value >= 0 ? 'text-up' : 'text-down';
const amount = (value: number | null | undefined) => {
  if (value == null) return '--';
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(1)}万`;
  return value.toFixed(2);
};
const time = (value: string | null | undefined) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '--';

export default function ReportsPage() {
  const [data, setData] = useState<ReportDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [progress, setProgress] = useState(8);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch<{ data: ReportDashboard }>('/personal/reports');
      setData(response.data);
      setProgress(100);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '财报日历加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!loading) return;
    const timer = window.setInterval(() => setProgress((value) => Math.min(92, value + 6)), 400);
    return () => window.clearInterval(timer);
  }, [loading]);

  const refresh = async () => {
    setRefreshing(true);
    setError(null);
    try {
      const response = await apiFetch<{ data: ReportDashboard }>('/personal/reports/refresh', { method: 'POST' });
      setData(response.data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '财报快照刷新失败');
    } finally {
      setRefreshing(false);
    }
  };

  return <div className="max-w-7xl mx-auto px-4 py-5 md:py-6">
    <PersonalWorkspaceNav />
    <header className="flex flex-wrap items-start justify-between gap-4 mb-5">
      <div><h1 className="text-xl md:text-2xl font-bold text-text flex items-center gap-2"><CalendarDays size={22} className="text-accent" />财报日历</h1><p className="text-xs text-text-secondary mt-1">个人池披露计划、财务指标与环比变化</p></div>
      <button type="button" onClick={refresh} disabled={refreshing || loading} className="inline-flex items-center gap-1.5 px-3 py-2 bg-accent text-white rounded-md text-xs disabled:opacity-50"><RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />刷新财报快照</button>
    </header>

    {error && <div className="mb-4 border border-up/50 bg-[#EF535014] rounded-md p-3 text-xs text-up flex gap-2"><AlertTriangle size={15} className="shrink-0" />{error}</div>}
    {loading && !data ? <div className="py-24 text-center"><Loader2 size={28} className="animate-spin text-accent mx-auto" /><div className="text-xs text-text-secondary mt-3">正在核对个人池财报与披露预约</div><div className="h-1.5 max-w-sm mx-auto bg-[#21262D] mt-5 overflow-hidden rounded"><div className="h-full bg-accent transition-all" style={{ width: `${progress}%` }} /></div><div className="text-xs text-text-secondary font-mono mt-2">{progress}%</div></div> : data && <>
      <section className="border border-border rounded-md px-3 py-2.5 mb-5 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-text-secondary">
        <span className={data.cache_used ? 'text-warn' : 'text-up'}>{data.cache_used ? '部分数据来自最近缓存' : '数据源本次可用'}</span>
        <span>个人池股票：<b className="text-text font-mono font-normal">{data.universe_count}</b>只</span>
        <span>披露预约：{sourceLabel(data.source_status.appointments)}</span>
        <span>财务数据：{sourceLabel(data.source_status.financials)}</span>
        <span className="sm:ml-auto">{data.cache_used ? '缓存快照' : '更新'}：{time(data.cache_used ? data.snapshot_updated_at : data.updated_at)}</span>
      </section>

      <section className="border border-border rounded-md overflow-hidden mb-5">
        <div className="px-4 py-3 border-b border-border flex flex-wrap items-center justify-between gap-2"><h2 className="text-sm font-semibold text-text flex items-center gap-2"><CalendarClock size={15} className="text-accent" />未来两周披露</h2><span className="text-xs text-text-secondary">{data.upcoming.length}项</span></div>
        {data.upcoming.length ? <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-xs"><thead className="border-b border-border text-text-secondary"><tr><th className="text-left px-4 py-2.5">日期</th><th className="text-left px-3">股票</th><th className="text-left px-3">关联</th><th className="text-left px-3">报告</th><th className="text-left px-3">状态</th><th className="text-right px-4">倒计时</th></tr></thead><tbody>{data.upcoming.map((item, index) => <tr key={`${item.code}-${item.publish_date}-${index}`} className="border-b border-border/60 last:border-b-0"><td className="px-4 py-3 font-mono text-text">{item.publish_date || '--'}</td><td className="px-3 text-text"><StockKlineButton code={item.code} name={item.name} className="text-text">{item.name}<span className="font-mono text-text-secondary ml-2">{item.code}</span></StockKlineButton></td><td className={item.holding ? 'px-3 text-warn' : 'px-3 text-text-secondary'}>{item.relation}</td><td className="px-3 text-text-secondary">{item.report_type}</td><td className="px-3">{item.changed ? <span className="text-warn">日期有变更</span> : item.actual_publish_date ? <span className="text-up">已披露</span> : <span className="text-text-secondary">预约</span>}</td><td className="px-4 text-right font-mono text-text">{item.days_until == null ? '--' : item.days_until === 0 ? '今天' : `${item.days_until}天`}</td></tr>)}</tbody></table></div> : <Empty text="未来两周个人池暂无预约披露" />}
      </section>

      <section className="mb-5"><div className="flex items-center justify-between gap-3 mb-3"><h2 className="text-sm font-semibold text-text">已发布财报自动对比</h2><span className="text-xs text-text-secondary">最近 {data.published.length} 只</span></div>{data.published.length ? <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">{data.published.map((item) => <article key={`${item.code}-${item.report_date}`} className="border border-border rounded-md p-4 min-w-0"><div className="flex flex-wrap items-start justify-between gap-2"><div><StockKlineButton code={item.code} name={item.name} className="text-sm font-medium text-text">{item.name}<span className="font-mono text-text-secondary ml-2 text-xs">{item.code}</span></StockKlineButton><div className="text-[11px] text-text-secondary mt-1">{item.report_type} · 报告期 {item.report_date || '--'} · {item.relation}</div></div><span className="text-[11px] font-mono text-text-secondary">{item.notice_date || '--'}</span></div><div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-3 mt-4"><ReportMetric label="营收" value={amount(item.metrics.revenue)} /><ReportMetric label="归母净利润" value={amount(item.metrics.net_profit)} /><ReportMetric label="营收同比" value={signed(item.metrics.revenue_growth_pct)} className={tone(item.metrics.revenue_growth_pct)} /><ReportMetric label="利润同比" value={signed(item.metrics.profit_growth_pct)} className={tone(item.metrics.profit_growth_pct)} /><ReportMetric label="毛利率" value={`${number(item.metrics.gross_margin_pct)}%`} /><ReportMetric label="ROE" value={`${number(item.metrics.roe_pct)}%`} /><ReportMetric label="每股经营现金流" value={number(item.metrics.cashflow_per_share, 2)} className={tone(item.metrics.cashflow_per_share)} /><ReportMetric label="利润增速变化" value={signed(item.comparison.profit_acceleration_pct)} className={tone(item.comparison.profit_acceleration_pct)} /></div>{item.anomalies.length > 0 && <div className="mt-4 pt-3 border-t border-border flex flex-wrap gap-2">{item.anomalies.map((warning) => <span key={warning} className="text-[11px] text-warn inline-flex items-center gap-1"><AlertTriangle size={12} />{warning}</span>)}</div>}</article>)}</div> : <Empty text="个人池暂无可比财报数据" />}</section>

      <section className="border-t border-border pt-4"><h2 className="text-sm font-semibold text-text">自动处理状态</h2><div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 mt-3"><Automation label="提取核心财务指标" enabled={data.automation.extract_metrics} /><Automation label="对比上一报告期" enabled={data.automation.compare_previous} /><Automation label="识别异常指标" enabled={data.automation.flag_anomalies} /><Automation label="同步选股特征" enabled={data.automation.update_selection_features} /></div><div className="mt-3 flex items-start gap-2 text-[11px] text-text-secondary leading-5"><Database size={13} className="shrink-0 mt-1" /><div>{data.automation.message}<br />{data.disclaimer}</div></div></section>
    </>}
  </div>;
}

function sourceLabel(status: string) { return status === 'available' ? '本次获取' : status === 'cache' ? '最近缓存' : '暂不可用'; }
function ReportMetric({ label, value, className = '' }: { label: string; value: string; className?: string }) { return <div className="min-w-0"><div className="text-[10px] text-text-secondary">{label}</div><div className={`font-mono text-xs mt-1 truncate ${className || 'text-text'}`}>{value}</div></div>; }
function Automation({ label, enabled }: { label: string; enabled: boolean }) { return <div className="border border-border rounded-md px-3 py-2.5 text-xs flex items-center gap-2"><CheckCircle2 size={14} className={enabled ? 'text-down' : 'text-text-secondary'} /><span className="text-text">{label}</span></div>; }
function Empty({ text }: { text: string }) { return <div className="py-14 text-center text-xs text-text-secondary">{text}</div>; }
