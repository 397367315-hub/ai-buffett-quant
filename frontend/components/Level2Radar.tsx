'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ChevronDown, Database, RefreshCw, ShieldAlert } from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';

type AnyMap = Record<string, any>;

interface Level2RadarProps {
  symbol: string;
  tradeDate?: string;
}

const METRICS: Array<{ key: string; label: string; hint: string }> = [
  { key: 'hfi', label: 'HFI 隐性资金', hint: '可观测隐性资金行为代理' },
  { key: 'qas', label: 'QAS 量化活跃', hint: '订单质量与活跃度评分' },
  { key: 'absorption', label: 'ABS 承接', hint: '买卖双方承接特征' },
  { key: 'distribution', label: 'DIS 派发风险', hint: '价格效率与上方压力代理' },
  { key: 'split', label: 'SPLIT 拆单', hint: '同方向拆单特征' },
  { key: 'replenishment', label: 'REPL 补单', hint: '买卖盘补单特征' },
  { key: 'spoof', label: 'SPOOF 挂撤异常', hint: '盘口异常挂撤单代理' },
  { key: 'obi', label: 'OBI 盘口失衡', hint: '加权十档盘口失衡' },
  { key: 'microstructure', label: 'MICRO 结构质量', hint: '微观结构综合评分' },
];

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function valueText(value: unknown, digits = 1): string {
  return finite(value) ? value.toFixed(digits) : '--';
}

function valueTone(value: unknown): string {
  if (!finite(value) || value === 0) return 'text-text';
  return value > 0 ? 'text-up' : 'text-down';
}

function metricValue(metric: AnyMap, key: string): unknown {
  const value = metric?.value;
  if (key === 'absorption' || key === 'split' || key === 'replenishment') return null;
  return value;
}

function metricLabel(metric: AnyMap, key: string): string {
  if (key === 'absorption') return `买 ${valueText(metric?.buy?.value)} / 卖 ${valueText(metric?.sell?.value)}`;
  if (key === 'split') return `买 ${valueText(metric?.buy?.value)} / 卖 ${valueText(metric?.sell?.value)}`;
  if (key === 'replenishment') return `买 ${valueText(metric?.bid?.value)} / 卖 ${valueText(metric?.ask?.value)}`;
  return metric?.label || (finite(metricValue(metric, key)) ? valueText(metricValue(metric, key)) : '暂无样本');
}

function metricTone(metric: AnyMap, key: string): string {
  if (key === 'absorption' || key === 'split') {
    const buy = metric?.buy?.value;
    const sell = metric?.sell?.value;
    if (!finite(buy) && !finite(sell)) return 'text-text';
    return (buy || 0) >= (sell || 0) ? 'text-up' : 'text-down';
  }
  if (key === 'replenishment') {
    const bid = metric?.bid?.value;
    const ask = metric?.ask?.value;
    if (!finite(bid) && !finite(ask)) return 'text-text';
    return (bid || 0) >= (ask || 0) ? 'text-up' : 'text-down';
  }
  return valueTone(metricValue(metric, key));
}

function qualityLabel(quality: AnyMap): string {
  const status = String(quality?.status || 'not_available');
  return {
    complete: '完整',
    degraded: '部分覆盖',
    partial: '分页未完成',
    no_data: '暂无数据',
    not_available: '未形成快照',
  }[status] || status;
}

export default function Level2Radar({ symbol, tradeDate }: Level2RadarProps) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [payload, setPayload] = useState<AnyMap | null>(null);
  const [events, setEvents] = useState<AnyMap[]>([]);
  const [tab, setTab] = useState<'metrics' | 'timeline' | 'events'>('metrics');
  const [error, setError] = useState('');
  const requestIdRef = useRef(0);

  const query = useMemo(() => {
    const params = new URLSearchParams();
    if (tradeDate) params.set('trade_date', tradeDate);
    return params.toString();
  }, [tradeDate]);

  const loadSummary = useCallback(async (force = false) => {
    if (!symbol) return;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    if (force) setRefreshing(true);
    setError('');
    try {
      const suffix = query ? `?${query}${force ? '&refresh=true' : ''}` : (force ? '?refresh=true' : '');
      const response = await apiFetch<{ data: AnyMap }>(`/stocks/${encodeURIComponent(symbol)}/level2/summary${suffix}`, { timeoutMs: 30000 });
      if (requestId !== requestIdRef.current) return;
      setPayload(response.data);
    } catch (caught) {
      if (requestId === requestIdRef.current) setError(friendlyApiError(caught, 'Level-2数据暂时不可用'));
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [query, symbol]);

  useEffect(() => {
    requestIdRef.current += 1;
    setExpanded(false);
    setLoading(false);
    setRefreshing(false);
    setPayload(null);
    setEvents([]);
    setTab('metrics');
    setError('');
  }, [symbol, tradeDate]);

  useEffect(() => {
    if (!expanded || !payload?.pending) return;
    const timer = window.setInterval(() => { void loadSummary(false); }, 3000);
    return () => window.clearInterval(timer);
  }, [expanded, loadSummary, payload?.pending]);

  const loadEvents = useCallback(async () => {
    if (!symbol) return;
    const requestId = requestIdRef.current;
    try {
      const response = await apiFetch<{ data: AnyMap }>(`/stocks/${encodeURIComponent(symbol)}/level2/events${query ? `?${query}` : ''}`, { timeoutMs: 30000 });
      if (requestId !== requestIdRef.current) return;
      setEvents(response.data.events || []);
    } catch (caught) {
      if (requestId === requestIdRef.current) setError(friendlyApiError(caught, 'Level-2事件暂时不可用'));
    }
  }, [query, symbol]);

  const open = () => {
    if (!expanded && !payload) void loadSummary(false);
    setExpanded((current) => !current);
  };

  const summary = payload?.summary || {};
  const quality = payload?.data_quality || {};
  const timeline = summary.timeline || [];

  return (
    <section className="border-b border-border py-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button type="button" onClick={open} className="flex min-w-0 items-center gap-2 text-left">
          <Activity size={16} className="shrink-0 text-accent" />
          <span className="text-sm font-semibold text-text">Level-2隐性资金雷达</span>
          <ChevronDown size={15} className={`text-text-secondary transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </button>
        {expanded && payload && (
          <button type="button" onClick={() => void loadSummary(true)} disabled={loading} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-xs text-text-secondary hover:text-text disabled:opacity-50" title="重新同步Level-2历史样本">
            <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />刷新样本
          </button>
        )}
      </div>
      <p className="mt-1 text-[10px] leading-4 text-text-secondary">按需加载逐笔成交、委托和十档盘口；只描述可观测结构，不识别真实账户身份。</p>

      {!expanded && <div className="mt-3 flex items-center gap-2 text-[10px] text-text-secondary"><Database size={12} />点击后加载，普通个股画像不会等待Level-2。</div>}

      {expanded && (
        <div className="mt-4">
          {loading && !payload ? (
            <div className="border border-border bg-card px-4 py-5 text-xs text-text-secondary"><div className="flex items-center gap-2"><RefreshCw size={14} className="animate-spin text-accent" />正在读取缓存并检查Level-2同步状态</div><div className="mt-3 h-1 overflow-hidden rounded-full bg-border"><div className="h-full w-2/3 animate-pulse bg-accent" /></div></div>
          ) : error ? (
            <div className="border-l-2 border-warn bg-warn/5 px-3 py-3 text-xs leading-5 text-warn">{error}</div>
          ) : !payload ? null : (
            <>
              {!payload.configured && !payload.available && (
                <div className="border border-border bg-card px-4 py-4 text-xs leading-5 text-text-secondary"><div className="font-medium text-text">服务端未配置Level-2数据源</div><div className="mt-1">当前普通行情、K线和个股决策仍正常。配置供应商API密钥后，可同步历史逐笔成交、逐笔委托和十档盘口；密钥只保存在服务端，不会下发浏览器。</div><div className="mt-1 text-warn">当前适配器未声明实时流式Level-2，不能把普通行情当作Level-2。</div></div>
              )}
              {payload.pending && <div className="mb-3 flex items-center gap-2 border-l-2 border-accent bg-accent/5 px-3 py-2 text-[10px] leading-4 text-accent"><RefreshCw size={12} className="animate-spin" />历史Level-2样本正在后台同步，当前先展示已有缓存。</div>}
              {payload.available && (
                <>
                  <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-text-secondary"><span>数据日 {payload.trade_date || '--'}</span><span>来源 {payload.provider || '--'}</span><span>质量 {qualityLabel(quality)}</span><span>质量置信度 {valueText(quality.confidence, 0)}%</span></div>
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
                    {METRICS.map((item) => {
                      const metric = summary[item.key] || {};
                      return <div key={item.key} className="min-w-0 border border-border bg-card px-3 py-2.5"><div className="text-[10px] text-text-secondary" title={item.hint}>{item.label}</div><div className={`mt-1 break-words font-mono text-sm font-semibold ${metricTone(metric, item.key)}`}>{metricLabel(metric, item.key)}</div><div className="mt-1 text-[10px] text-text-secondary">置信 {valueText(metric?.confidence, 0)}%</div></div>;
                    })}
                  </div>
                  <div className="mt-4 flex flex-wrap gap-1 border-b border-border">
                    {([['metrics', '指标摘要'], ['timeline', '分钟时间线'], ['events', '异常事件']] as const).map(([key, label]) => <button key={key} type="button" onClick={() => { setTab(key); if (key === 'events' && events.length === 0) void loadEvents(); }} className={`border-b-2 px-3 py-2 text-[11px] ${tab === key ? 'border-accent text-accent' : 'border-transparent text-text-secondary hover:text-text'}`}>{label}</button>)}
                  </div>
                  {tab === 'metrics' && <div className="mt-3 space-y-1.5 text-[10px] leading-4 text-text-secondary">{(summary.explanation || []).slice(0, 5).map((item: string) => <div key={item}>· {item}</div>)}</div>}
                  {tab === 'timeline' && <div className="mt-3 max-h-64 overflow-auto"><div className="min-w-[860px] divide-y divide-border">{timeline.length ? timeline.slice(-120).reverse().map((row: AnyMap) => <div key={String(row.minute)} className="grid grid-cols-[120px_repeat(5,minmax(95px,1fr))] gap-2 py-2 text-[10px]"><span className="font-mono text-text-secondary">{String(row.minute || '--').replace('T', ' ').slice(0, 16)}</span><span className={valueTone(row.hfi)}>HFI {valueText(row.hfi)}</span><span className="text-text">QAS {valueText(row.qas)}</span><span className={valueTone(row.order_imbalance)}>OBI {valueText(row.order_imbalance, 2)}</span><span className="text-text">MICRO {valueText(row.micro_score)}</span><span className="text-text-secondary">{row.data_quality === 'complete' ? '完整' : '部分'}</span></div>) : <div className="py-4 text-xs text-text-secondary">暂无分钟特征样本</div>}</div></div>}
                  {tab === 'events' && <div className="mt-3 space-y-2">{events.length ? events.map((item, index) => <div key={`${item.event_type}-${item.minute}-${index}`} className="border-l-2 border-warn bg-warn/5 px-3 py-2 text-[10px] leading-4"><div className="flex flex-wrap justify-between gap-2 text-text"><span>{item.label}</span><span className="font-mono text-text-secondary">{String(item.minute || '--').replace('T', ' ').slice(0, 16)}</span></div><div className="mt-1 text-text-secondary">{item.reason} · 置信 {valueText(item.confidence, 0)}%</div></div>) : <div className="py-4 text-xs text-text-secondary">暂无达到阈值的异常事件</div>}</div>}
                  {(quality.warnings || []).length > 0 && <div className="mt-3 flex items-start gap-2 border-t border-border pt-3 text-[10px] leading-4 text-warn"><ShieldAlert size={13} className="mt-0.5 shrink-0" /><span>{quality.warnings.join('；')}</span></div>}
                </>
              )}
              {payload.available && <div className="mt-3 text-[10px] leading-4 text-text-secondary">HFI、SPOOF、DIS、SPLIT、REPL均为模型代理或结构特征，不代表“主力”身份；任何交易判断需结合行情、板块、基本面和风控。</div>}
            </>
          )}
        </div>
      )}
    </section>
  );
}
