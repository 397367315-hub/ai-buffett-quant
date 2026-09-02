'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Bell,
  CheckCircle2,
  Clock3,
  Database,
  ExternalLink,
  FileText,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Tag,
  Wifi,
  X,
} from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';

type AnyMap = Record<string, any>;

interface RadarTopic {
  name: string;
  relevance_score?: number | null;
  direction?: string;
  reason?: string;
}

interface RadarEvent {
  event_id: string;
  canonical_title: string;
  summary?: string;
  event_type?: string;
  source?: string;
  source_kind?: string;
  source_level?: string;
  source_score?: number | null;
  certainty_score?: number | null;
  novelty_score?: number | null;
  impact_score?: number | null;
  market_confirmation_score?: number | null;
  event_score?: number | null;
  alert_level?: string;
  direction?: string;
  status?: string;
  provider?: string;
  topics?: RadarTopic[];
  published_at?: string | null;
  last_updated_at?: string | null;
  cached?: boolean;
  url?: string | null;
  data_cutoff_time?: string | null;
}

interface RadarDetail extends RadarEvent {
  stocks?: Array<{
    code?: string;
    name?: string;
    relation_type?: string;
    relation_score?: number | null;
    benefit_score?: number | null;
    total_score?: number | null;
    business_evidence?: string;
    evidence_tag?: string;
  }>;
  alerts?: Array<{ alert_id: string; level: string; status: string; created_at: string }>;
  quality?: AnyMap;
  data_cutoff_time?: string | null;
}

interface ProviderHealth {
  provider: string;
  status?: string;
  latency_ms?: number | null;
  error_count?: number;
  empty_count?: number;
  last_success_at?: string | null;
  details?: AnyMap;
}

const EVENT_TYPES = [
  ['', '全部类型'],
  ['policy', '政策'],
  ['macro', '宏观'],
  ['earnings', '业绩'],
  ['risk_event', '风险事件'],
  ['market_abnormal_move', '市场异动'],
  ['contract_order', '订单合同'],
  ['technology_breakthrough', '技术突破'],
  ['company_announcement', '公司公告'],
] as const;

const LEVELS = [
  ['', '全部级别'],
  ['S', 'S级关注'],
  ['A', 'A级关注'],
  ['B', 'B级观察'],
  ['C', 'C级线索'],
] as const;

const EVENT_LABELS: Record<string, string> = {
  policy: '政策',
  macro: '宏观',
  earnings: '业绩',
  risk_event: '风险事件',
  market_abnormal_move: '市场异动',
  contract_order: '订单合同',
  technology_breakthrough: '技术突破',
  company_announcement: '公司公告',
  industry_data: '行业数据',
  rumor: '传闻线索',
  clarification: '澄清说明',
};

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function timeText(value: string | null | undefined): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Shanghai',
  }).format(date);
}

function scoreText(value: number | null | undefined): string {
  return finite(value) ? value.toFixed(0) : '--';
}

function levelClass(level: string | undefined): string {
  if (level === 'S') return 'border-down/50 bg-down/10 text-down';
  if (level === 'A') return 'border-warn/50 bg-warn/10 text-warn';
  if (level === 'B') return 'border-accent/40 bg-accent/10 text-accent';
  return 'border-border text-text-secondary';
}

function directionClass(direction: string | undefined): string {
  if (direction === 'positive') return 'text-up';
  if (direction === 'negative') return 'text-down';
  return 'text-warn';
}

function providerLabel(provider: string): string {
  const labels: Record<string, string> = {
    official_policy: '官方政策',
    cls_news: '财联社',
    company_announcements: '公司公告',
    market_snapshot: '行情异动',
    cls_http: '财联社公开源',
    akshare_cls: 'AKShare',
  };
  return labels[provider] || provider.replaceAll('_', ' ') || '未知来源';
}

function Metric({ label, value, detail, tone = 'text-text' }: { label: string; value: string; detail: string; tone?: string }) {
  return <div className="min-w-0 border-l-2 border-accent/60 bg-[#151D27] px-3 py-3"><div className="truncate text-[10px] text-text-secondary">{label}</div><div className={`mt-1 truncate font-mono text-lg font-semibold ${tone}`}>{value}</div><div className="mt-1 truncate text-[10px] text-text-secondary" title={detail}>{detail}</div></div>;
}

function Panel({ title, icon: Icon, action, children }: { title: string; icon: typeof Bell; action?: React.ReactNode; children: React.ReactNode }) {
  return <section className="min-w-0 overflow-hidden rounded-md border border-border bg-card"><header className="flex min-w-0 items-center justify-between gap-3 border-b border-border px-4 py-3"><h2 className="flex min-w-0 items-center gap-2 text-sm font-semibold text-text"><Icon size={15} className="shrink-0 text-accent" />{title}</h2>{action}</header>{children}</section>;
}

export default function EventRadarPage() {
  const [events, setEvents] = useState<RadarEvent[]>([]);
  const [topics, setTopics] = useState<Array<{ name: string; event_count: number; max_relevance: number | null }>>([]);
  const [alerts, setAlerts] = useState<Array<{ alert_id: string; event_id: string; level: string; title: string; message: string; created_at: string }>>([]);
  const [providers, setProviders] = useState<ProviderHealth[]>([]);
  const [selected, setSelected] = useState<RadarDetail | null>(null);
  const [level, setLevel] = useState('');
  const [eventType, setEventType] = useState('');
  const [topic, setTopic] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [interpretLoading, setInterpretLoading] = useState(false);
  const [interpretation, setInterpretation] = useState('');
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');

  const load = useCallback(async (refresh = false) => {
    setError('');
    if (refresh) setRefreshing(true); else setLoading(true);
    try {
      const eventQuery = new URLSearchParams({ limit: '80' });
      if (level) eventQuery.set('level', level);
      if (eventType) eventQuery.set('event_type', eventType);
      if (topic) eventQuery.set('topic', topic);
      if (refresh) eventQuery.set('refresh', 'true');
      const [eventResponse, topicResponse, alertResponse, providerResponse] = await Promise.allSettled([
        apiFetch<{ data: { events: RadarEvent[] } }>(`/radar/events?${eventQuery.toString()}`, { timeoutMs: 30000 }),
        apiFetch<{ data: { topics: typeof topics } }>('/radar/topics/hot?limit=20', { timeoutMs: 20000 }),
        apiFetch<{ data: { alerts: typeof alerts } }>('/radar/alerts?limit=20', { timeoutMs: 20000 }),
        apiFetch<{ data: { providers: ProviderHealth[] } }>('/radar/providers/status', { timeoutMs: 20000 }),
      ]);
      const fulfilled = [eventResponse, topicResponse, alertResponse, providerResponse].filter((item) => item.status === 'fulfilled');
      if (eventResponse.status === 'fulfilled') setEvents(eventResponse.value.data.events || []);
      if (topicResponse.status === 'fulfilled') setTopics(topicResponse.value.data.topics || []);
      if (alertResponse.status === 'fulfilled') setAlerts(alertResponse.value.data.alerts || []);
      if (providerResponse.status === 'fulfilled') setProviders(providerResponse.value.data.providers || []);
      if (!fulfilled.length) throw new Error('事件雷达接口暂时无法连接');
      if (eventResponse.status === 'rejected') setError(friendlyApiError(eventResponse.reason, '事件列表暂时无法读取，已保留其他状态信息'));
    } catch (caught) {
      setError(friendlyApiError(caught, '事件雷达暂时无法读取'));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [eventType, level, topic]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(true), 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const openDetail = async (event: RadarEvent) => {
    setSelected(event as RadarDetail);
    setInterpretation('');
    setDetailError('');
    setDetailLoading(true);
    try {
      const response = await apiFetch<{ data: RadarDetail }>(`/radar/events/${encodeURIComponent(event.event_id)}`, { timeoutMs: 20000 });
      setSelected(response.data);
    } catch (caught) {
      setDetailError(friendlyApiError(caught, '事件详情暂时不可用'));
    } finally {
      setDetailLoading(false);
    }
  };

  const interpret = async () => {
    if (!selected) return;
    setInterpretLoading(true);
    setDetailError('');
    try {
      const response = await apiFetch<{ data: { interpretation: string } }>(`/radar/events/${encodeURIComponent(selected.event_id)}/interpretation`, { method: 'POST', timeoutMs: 60000 });
      setInterpretation(String(response.data.interpretation || '').replaceAll('**', '').replaceAll('__', '').replaceAll('```', '').trim());
    } catch (caught) {
      setDetailError(friendlyApiError(caught, 'AI解读暂时不可用'));
    } finally {
      setInterpretLoading(false);
    }
  };

  const healthyProviders = useMemo(() => providers.filter((item) => /OK|SUCCESS|HEALTHY|LIVE/i.test(String(item.status || ''))).length, [providers]);
  const highAlerts = useMemo(() => events.filter((item) => item.alert_level === 'S' || item.alert_level === 'A').length, [events]);

  return <div className="min-h-screen bg-bg text-text"><div className="mx-auto max-w-[1560px] space-y-4 p-3 sm:p-5">
    <header className="flex min-w-0 flex-wrap items-start justify-between gap-4 border-b border-border pb-4"><div className="min-w-0"><div className="flex items-center gap-2 text-[11px] font-mono text-accent"><Bell size={14} />RESEARCH ALERT / EVENT RADAR</div><h1 className="mt-2 text-xl font-semibold sm:text-2xl">AI实时事件雷达</h1><p className="mt-1 max-w-4xl text-xs leading-5 text-text-secondary">把国内政策、国际宏观、公司公告和市场异动放进同一条可核验事件链。来源优先级、确定性和市场确认分开显示，不把新闻标题直接当成交易结论。</p></div><div className="flex shrink-0 items-center gap-2"><Link href="/market" className="command-button"><Database size={13} />返回决策中枢</Link><button type="button" onClick={() => void load(true)} disabled={refreshing} className="command-button command-button-primary"><RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />刷新雷达</button></div></header>

    {error && <div className="flex items-start gap-2 rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-xs text-warn"><AlertTriangle size={14} className="mt-0.5 shrink-0" />{error}</div>}
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4"><Metric label="当前事件" value={loading && !events.length ? '...' : String(events.length)} detail="按当前筛选条件" /><Metric label="S / A关注" value={String(highAlerts)} detail="需要优先核验的事件" tone={highAlerts ? 'text-warn' : 'text-text'} /><Metric label="热题材" value={String(topics.length)} detail="近3日事件聚合" /><Metric label="可用来源" value={`${healthyProviders}/${providers.length || '--'}`} detail="公开源/缓存健康状态" tone={healthyProviders ? 'text-up' : 'text-warn'} /></div>

    <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_390px]"><div className="min-w-0 space-y-4">
      <Panel title="事件筛选" icon={Search} action={<span className="text-[10px] text-text-secondary">自动刷新 60 秒</span>}><div className="grid gap-3 p-4 sm:grid-cols-3"><label className="text-[11px] text-text-secondary">关注级别<select value={level} onChange={(event) => setLevel(event.target.value)} className="input mt-1 w-full">{LEVELS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label className="text-[11px] text-text-secondary">事件类型<select value={eventType} onChange={(event) => setEventType(event.target.value)} className="input mt-1 w-full">{EVENT_TYPES.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label className="text-[11px] text-text-secondary">题材<select value={topic} onChange={(event) => setTopic(event.target.value)} className="input mt-1 w-full"><option value="">全部题材</option>{topics.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label></div></Panel>

      <Panel title="事件流" icon={Wifi} action={<span className="text-[10px] text-text-secondary">{events.length} 条 · 点击查看因果证据</span>}>
        {loading && !events.length ? <div className="flex items-center justify-center gap-2 py-14 text-xs text-text-secondary"><Loader2 size={15} className="animate-spin text-accent" />读取事件源与缓存</div> : events.length ? <div className="divide-y divide-border">{events.map((event) => <button type="button" key={event.event_id} onClick={() => void openDetail(event)} className={`flex w-full min-w-0 items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-[#18212C] ${selected?.event_id === event.event_id ? 'bg-accent/10' : ''}`}><span className={`mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded border font-mono text-[10px] ${levelClass(event.alert_level)}`}>{event.alert_level || 'C'}</span><span className="min-w-0 flex-1"><span className="flex min-w-0 flex-wrap items-center gap-2"><b className="min-w-0 truncate text-xs font-medium text-text">{event.canonical_title}</b><span className={`shrink-0 text-[10px] ${directionClass(event.direction)}`}>{event.direction === 'positive' ? '偏正向' : event.direction === 'negative' ? '偏负向' : '方向未定'}</span></span><span className="mt-1 block truncate text-[10px] text-text-secondary">{event.summary || '暂无摘要'}</span><span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[9px] text-text-secondary"><span>{EVENT_LABELS[event.event_type || ''] || event.event_type || '事件'}</span><span>{event.source || providerLabel(event.provider || '')}</span><span>{timeText(event.published_at || event.last_updated_at)}</span>{event.cached && <span className="text-warn">缓存样本</span>}</span></span><span className="shrink-0 text-right"><span className="block font-mono text-xs text-text">{scoreText(event.event_score)}</span><span className="mt-1 block text-[9px] text-text-secondary">事件分</span></span></button>)}</div> : <div className="py-14 text-center text-xs text-text-secondary">当前筛选没有可核验事件</div>}
      </Panel>

      <Panel title="高优先级提醒" icon={AlertTriangle} action={<span className="text-[10px] text-text-secondary">S/A 事件</span>}><div className="divide-y divide-border">{alerts.length ? alerts.slice(0, 10).map((item) => <button type="button" key={item.alert_id} onClick={() => { const event = events.find((candidate) => candidate.event_id === item.event_id); if (event) void openDetail(event); }} className="flex w-full min-w-0 items-start gap-3 px-4 py-3 text-left hover:bg-[#18212C]"><span className={`mt-0.5 rounded border px-1.5 py-1 font-mono text-[9px] ${levelClass(item.level)}`}>{item.level}</span><span className="min-w-0 flex-1"><span className="block truncate text-xs text-text">{item.title}</span><span className="mt-1 block truncate text-[10px] text-text-secondary">{item.message}</span></span><span className="shrink-0 text-[9px] text-text-secondary">{timeText(item.created_at)}</span></button>) : <div className="py-8 text-center text-xs text-text-secondary">暂无高优先级提醒</div>}</div></Panel>
    </div>

    <aside className="min-w-0 space-y-4"><Panel title="热题材" icon={Tag} action={<span className="text-[10px] text-text-secondary">近3日</span>}><div className="divide-y divide-border">{topics.length ? topics.slice(0, 12).map((item, index) => <button type="button" key={item.name} onClick={() => setTopic(item.name)} className={`flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-[#18212C] ${topic === item.name ? 'bg-accent/10' : ''}`}><span className="w-5 shrink-0 text-center font-mono text-[10px] text-text-secondary">{index + 1}</span><span className="min-w-0 flex-1 truncate text-xs text-text">{item.name}</span><span className="shrink-0 text-right"><span className="block font-mono text-xs text-text">{item.event_count}</span><span className="block text-[9px] text-text-secondary">事件</span></span></button>) : <div className="py-8 text-center text-xs text-text-secondary">暂无题材聚合</div>}</div></Panel>

      <Panel title="来源健康" icon={ShieldCheck}><div className="divide-y divide-border">{providers.length ? providers.map((item) => <div key={item.provider} className="flex min-w-0 items-center gap-2 px-3 py-2.5"><span className={`h-1.5 w-1.5 shrink-0 rounded-full ${/OK|SUCCESS|HEALTHY|LIVE/i.test(String(item.status || '')) ? 'bg-up' : 'bg-warn'}`} /><span className="min-w-0 flex-1 truncate text-[11px] text-text">{providerLabel(item.provider)}</span><span className="shrink-0 text-[9px] text-text-secondary">{item.latency_ms != null ? `${Math.round(item.latency_ms)}ms` : item.status || '未知'}</span></div>) : <div className="py-8 text-center text-xs text-text-secondary">来源状态尚未建立</div>}</div><div className="border-t border-border px-3 py-3 text-[10px] leading-5 text-text-secondary">官方政策、公司公告优先；公开资讯可能延迟或限流。市场确认分只使用可核验行情，不用旧数据冒充实时。</div></Panel>

      <Panel title="数据使用边界" icon={CheckCircle2}><div className="space-y-2 p-4 text-[10px] leading-5 text-text-secondary"><div className="flex items-start gap-2"><CheckCircle2 size={13} className="mt-0.5 shrink-0 text-up" />事件标题与市场异动分开统计，避免把相关性当因果。</div><div className="flex items-start gap-2"><CheckCircle2 size={13} className="mt-0.5 shrink-0 text-up" />AI只解释结构化事实、传导和反证，不改变事件评分。</div><div className="flex items-start gap-2"><CheckCircle2 size={13} className="mt-0.5 shrink-0 text-up" />实时源失败时保留缓存，并明确标注缓存范围和时间。</div></div></Panel>
    </aside></div>

    {selected && <div className="fixed inset-0 z-[70] flex items-end justify-center bg-black/60 p-0 sm:items-center sm:p-5" role="dialog" aria-modal="true" aria-label="事件详情"><div className="flex max-h-[92vh] w-full min-w-0 max-w-5xl flex-col overflow-hidden rounded-t-lg border border-border bg-card shadow-2xl sm:rounded-lg"><header className="flex min-w-0 items-start justify-between gap-3 border-b border-border px-4 py-3 sm:px-5"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2 text-[10px] text-text-secondary"><span className={`rounded border px-1.5 py-1 font-mono ${levelClass(selected.alert_level)}`}>{selected.alert_level || 'C'}级</span><span>{EVENT_LABELS[selected.event_type || ''] || selected.event_type || '事件'}</span><span>{selected.source || providerLabel(selected.provider || '')}</span><span>{timeText(selected.published_at || selected.last_updated_at)}</span></div><h2 className="mt-2 text-base font-semibold leading-6 text-text">{selected.canonical_title}</h2></div><button type="button" onClick={() => setSelected(null)} className="command-icon" aria-label="关闭事件详情" title="关闭"><X size={16} /></button></header><div className="min-h-0 overflow-y-auto p-4 sm:p-5"><div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_310px]"><div className="min-w-0 space-y-4"><div className="border-l-2 border-accent bg-[#151D27] px-3 py-3 text-xs leading-6 text-text-secondary">{selected.summary || '暂无摘要。'}<div className="mt-2 text-[10px] text-text-secondary">事件分 {scoreText(selected.event_score)} · 数据截点 {timeText(selected.data_cutoff_time || selected.last_updated_at)} · {selected.cached ? '缓存样本' : '本次采集'}</div></div><div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{[['来源确定性', selected.source_score], ['新鲜度', selected.novelty_score], ['影响度', selected.impact_score], ['市场确认', selected.market_confirmation_score]].map(([label, value]) => <div key={String(label)} className="border border-border bg-[#151D27] px-3 py-2"><div className="text-[9px] text-text-secondary">{label}</div><div className="mt-1 font-mono text-sm text-text">{scoreText(value as number)}</div></div>)}</div>{selected.topics?.length ? <div><h3 className="text-xs font-medium text-text">题材与传导线索</h3><div className="mt-2 flex flex-wrap gap-2">{selected.topics.map((item) => <span key={item.name} className="rounded border border-border px-2 py-1 text-[10px] text-text-secondary">{item.name} · {scoreText(item.relevance_score)}</span>)}</div></div> : null}{selected.stocks?.length ? <div><h3 className="text-xs font-medium text-text">关联标的线索</h3><div className="mt-2 overflow-x-auto border border-border"><table className="w-full min-w-[600px] text-[10px]"><thead className="border-b border-border bg-[#151D27] text-text-secondary"><tr><th className="px-3 py-2 text-left font-normal">标的</th><th className="px-3 py-2 text-left font-normal">关系</th><th className="px-3 py-2 text-left font-normal">证据</th><th className="px-3 py-2 text-right font-normal">关联分</th></tr></thead><tbody>{selected.stocks.slice(0, 10).map((stock) => <tr key={`${stock.code}-${stock.name}`} className="border-b border-border/70 last:border-0"><td className="px-3 py-2"><Link href={`/pro/stock?code=${encodeURIComponent(stock.code || '')}`} className="text-accent hover:text-text">{stock.name || stock.code || '--'}</Link><div className="mt-1 font-mono text-[9px] text-text-secondary">{stock.code || '--'}</div></td><td className="px-3 py-2 text-text-secondary">{stock.relation_type || '--'}</td><td className="max-w-[270px] px-3 py-2 leading-4 text-text-secondary">{stock.business_evidence || '需进一步核验主营关系'}</td><td className="px-3 py-2 text-right font-mono text-text">{scoreText(stock.total_score)}</td></tr>)}</tbody></table></div></div> : null}</div><aside className="min-w-0 space-y-3"><div className="border border-accent/30 bg-accent/5 p-3"><div className="flex items-center gap-2 text-xs font-medium text-accent"><Sparkles size={14} />专业事件解读</div><p className="mt-2 text-[10px] leading-5 text-text-secondary">基于已验真的来源、题材、市场确认和关联证据生成。解读不修改数据分数，也不输出买卖指令。</p><button type="button" onClick={() => void interpret()} disabled={interpretLoading || detailLoading} className="command-button command-button-primary mt-3 w-full justify-center"><Sparkles size={13} />{interpretLoading ? '解读中' : interpretation ? '重新解读' : '生成AI解读'}</button>{interpretation && <div className="mt-3 max-h-72 overflow-y-auto whitespace-pre-line border-l-2 border-accent pl-3 text-[11px] leading-6 text-text">{interpretation}</div>}</div>{detailLoading && <div className="flex items-center gap-2 text-xs text-text-secondary"><Loader2 size={14} className="animate-spin" />读取事件详情</div>}{detailError && <div className="flex items-start gap-2 border border-warn/30 bg-warn/5 px-3 py-2 text-[10px] leading-5 text-warn"><AlertTriangle size={13} className="mt-0.5 shrink-0" />{detailError}</div>}<div className="border border-border p-3 text-[10px] leading-5 text-text-secondary"><div className="flex items-center gap-2 text-text"><Clock3 size={13} className="text-accent" />生命周期</div><div className="mt-2">{selected.status || '待确认'} · 最后更新 {timeText(selected.last_updated_at)}</div>{selected.url && <a href={selected.url} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-accent hover:text-text">查看原始来源 <ExternalLink size={12} /></a>}</div></aside></div></div></div></div>}
  </div></div>;
}
