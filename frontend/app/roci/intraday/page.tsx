'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, ArrowDownRight, ArrowRight, ArrowUpRight, Clock3, Database, RefreshCw, ShieldCheck } from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';
import RociExplainability from '../components/RociExplainability';
import { RociFrame, RociSectionTitle, RociStatusPill } from '../components/RociFrame';

type AnyRecord = Record<string, any>;

const STATE_LABELS: Record<string, string> = {
  market_state: '市场状态', breadth_state: '市场广度', volume_state: '成交性质', leadership_state: '盘中领导力', migration_state: '资金迁移', risk_state: '风险状态', opportunity_state: '机会状态', scenario_validation_state: '周剧本验证',
};

function tone(value: unknown): 'good' | 'warn' | 'bad' | 'blue' {
  const state = String(value || '').toUpperCase();
  if (state.includes('RISK_OFF') || state.includes('COLLAPS') || state.includes('PANIC') || state === 'ELEVATED' || state.includes('WEAKEN')) return 'bad';
  if (state.includes('RISK_ON') || state.includes('EXPAND') || state === 'CONTROLLED' || state === 'SELECTIVE' || state.includes('SUPPORT_BULL')) return 'good';
  if (state === 'UNKNOWN' || state.includes('INSUFFICIENT') || state.includes('NO_SIGNAL')) return 'warn';
  return 'blue';
}
function number(value: unknown, suffix = ''): string { return typeof value === 'number' && Number.isFinite(value) ? `${value}${suffix}` : 'UNKNOWN'; }

export default function RociIntradayPage() {
  const [data, setData] = useState<AnyRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async (refresh = false) => {
    setError('');
    refresh ? setRefreshing(true) : setLoading(true);
    try {
      const response = await apiFetch<{ data: AnyRecord }>(`/roci/intraday/current${refresh ? '?refresh=true' : ''}`, { timeoutMs: 60000 });
      setData(response.data);
    } catch (caught) {
      setError(friendlyApiError(caught, '盘中数据暂时不可用'));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 60000); return () => window.clearInterval(timer); }, [load]);

  if (loading) return <RociFrame title="实时盘中认知" subtitle="竞价 · 广度 · 成交性质 · 领导力 · 资金迁移"><div className="roci-loading"><div className="roci-spinner" /><span>正在读取盘中行情与最近验证快照…</span></div></RociFrame>;
  if (error) return <RociFrame title="实时盘中认知" subtitle="ENGINE-16 Intraday Market Reasoning"><div className="roci-error"><AlertTriangle size={20} /><div><strong>盘中快照读取失败</strong><p>{error}</p><button className="roci-button" onClick={() => void load(true)}><RefreshCw size={14} />重新读取</button></div></div></RociFrame>;

  const states = data?.states || {};
  const indexes = data?.indexes || {};
  const migration = data?.migration || {};
  const changes = data?.state_changes || [];
  const events = data?.events || [];
  const alerts = data?.alerts || [];

  return <RociFrame title="实时盘中认知" subtitle="盘前剧本 → 盘中验证 → 结构变化 → 下一步观察" refresh={refreshing} onRefresh={() => void load(true)}>
    <div className="roci-meta-row"><span><Clock3 size={12} /> 盘中时间 {data?.snapshot_time || 'UNKNOWN'}</span><span><Database size={12} /> 源时间 {data?.provider_timestamp || 'UNKNOWN'}</span><span>分辨率 {data?.resolution || 'UNKNOWN'}</span><RociStatusPill value={data?.data_status || 'INSUFFICIENT_DATA'} tone={data?.data_status === 'REALTIME' ? 'good' : data?.data_status === 'STALE' ? 'bad' : 'warn'} /></div>
    <section className="roci-intraday-hero">
      <div className="roci-panel roci-intraday-state-card"><RociSectionTitle eyebrow="ENGINE 16 · INTRADAY MARKET REASONING" title="盘中市场作战面板" action={<RociStatusPill value={states.market_state || 'UNKNOWN'} tone={tone(states.market_state)} />} /><div className="roci-intraday-headline"><div><span>风险</span><strong className={tone(states.risk_state) === 'bad' ? 'roci-bad' : 'roci-warn'}>{number(data?.risk_score, ' / 100')}</strong></div><div><span>机会</span><strong className="roci-blue">{number(data?.opportunity_score, ' / 100')}</strong></div><div><span>周剧本</span><strong>{states.scenario_validation_state || 'UNKNOWN'}</strong></div></div><p className="roci-body-copy">{data?.method || '结构化行情状态，不修改正式周度概率，不连接交易执行。'}</p></div>
      <div className="roci-panel roci-intraday-index-card"><RociSectionTitle eyebrow="INDEX CONTEXT" title="指数与相对表现" />{[['shanghai', '上证'], ['chinext', '创业板'], ['hs300', '沪深300']].map(([key, label]) => <div className="roci-index-row" key={key}><span>{label}</span><strong>{number(indexes[key]?.value)}</strong><b className={indexes[key]?.change_pct >= 0 ? 'roci-up' : 'roci-down'}>{number(indexes[key]?.change_pct, '%')}</b><small>{indexes[key]?.source || '来源 UNKNOWN'}</small></div>)}</div>
    </section>
    <section className="roci-intraday-state-grid">{Object.entries(STATE_LABELS).map(([key, label]) => <div className="roci-panel roci-intraday-metric" key={key}><span>{label}</span><strong className={`roci-${tone(states[key])}`}>{states[key] || 'UNKNOWN'}</strong><small>{key === 'volume_state' ? `较前一基准 ${number(data?.turnover?.change_vs_previous_pct, '%')}` : key === 'migration_state' ? `${(migration.source_sectors || []).join('、') || '来源 UNKNOWN'} → ${(migration.destination_sectors || []).join('、') || '去向 UNKNOWN'}` : '连续窗口验证中'}</small></div>)}</section>
    <RociExplainability explanation={data?.explanation} />
    <section className="roci-two-panel-grid"><div className="roci-panel"><RociSectionTitle eyebrow="MIGRATION MAP" title="资金迁移观察" /><div className="roci-migration-path"><div><span>来源战场</span>{(migration.source_sectors || []).map((item: string) => <b key={item}>{item}<ArrowDownRight size={13} /></b>)}</div><ArrowRight size={18} /><div><span>目的战场</span>{(migration.destination_sectors || []).map((item: string) => <b key={item}>{item}<ArrowUpRight size={13} /></b>)}</div></div><p className="roci-muted-block">{migration.method || '板块资金方向代理；不能识别真实账户身份。'}</p></div><div className="roci-panel"><RociSectionTitle eyebrow="30–60 MIN WATCH" title="下一步情景观察" />{(data?.next_30_60m || []).map((item: AnyRecord) => <div className="roci-watch-row" key={item.scenario}><strong>{item.scenario}</strong><span>触发：{item.trigger}</span><small>确认：{item.confirmation} · 失效：{item.invalidation}</small></div>)}</div></section>
    <section className="roci-two-panel-grid"><div className="roci-panel"><RociSectionTitle eyebrow="EVENT STREAM" title="盘中关键变化" />{events.length ? events.map((item: AnyRecord, index: number) => <div className="roci-event-row" key={`${item.time}-${index}`}><time>{item.time}</time><div><strong>{item.event}</strong><span>{item.change}</span><small>{item.impact} · {item.scenario_impact}</small></div></div>) : <div className="roci-empty">当前没有检测到状态切换；系统继续等待下一个验证窗口。</div>}</div><div className="roci-panel"><RociSectionTitle eyebrow="STRUCTURE ALERTS" title="市场状态告警" />{alerts.length ? alerts.map((item: AnyRecord, index: number) => <div className="roci-alert-row" key={`${item.type}-${index}`}><AlertTriangle size={14} /><div><strong>{item.type}</strong><span>{item.message}</span></div></div>) : <div className="roci-empty">当前没有结构性告警。</div>}<div className="roci-intraday-source"><ShieldCheck size={13} />延迟 {number(data?.latency_ms, ' ms')} · 数据状态 {data?.data_status || 'UNKNOWN'} · 盘中建议不修改正式周度概率</div></div></section>
  </RociFrame>;
}
