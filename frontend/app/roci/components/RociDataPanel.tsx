'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Database, RefreshCw } from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';
import { RociBar, RociEvidence, RociFrame, RociSectionTitle, RociStatusPill } from './RociFrame';
import RociExplainability from './RociExplainability';

type RecordValue = Record<string, any>;

function value(value: any): string {
  if (value === null || value === undefined || value === '') return 'UNKNOWN';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function tone(value: any): 'good' | 'warn' | 'bad' | 'blue' | 'neutral' {
  const text = String(value || '').toUpperCase();
  if (['ALLY', 'RESILIENT', 'ANTIFRAGILE', 'MOSTLY_PRICED', 'FAVORABLE', 'ACTIVE'].includes(text)) return 'good';
  if (['ENEMY', 'FRAGILE', 'NOT_PRICED', 'UNFAVORABLE', 'DISABLED'].includes(text)) return 'bad';
  if (['CONVERTIBLE', 'PARTIALLY_PRICED', 'SHADOW', 'WAIT', 'UNKNOWN'].includes(text)) return 'warn';
  return 'blue';
}

export function RociDataPage({ title, subtitle, endpoint, children }: { title: string; subtitle: string; endpoint: string; children: (data: RecordValue) => React.ReactNode }) {
  const [data, setData] = useState<RecordValue | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async (force = false) => {
    setError(''); force ? setRefreshing(true) : setLoading(true);
    try { const result = await apiFetch<{ data: RecordValue }>(`${endpoint}${force ? (endpoint.includes('?') ? '&' : '?') + 'refresh=true' : ''}`, { timeoutMs: 60000 }); setData(result.data); }
    catch (caught) { setError(friendlyApiError(caught, 'ROCI数据暂时不可用')); }
    finally { setLoading(false); setRefreshing(false); }
  }, [endpoint]);
  useEffect(() => { void load(); }, [load]);

  return <RociFrame title={title} subtitle={subtitle} refresh={refreshing} onRefresh={() => void load(true)}>
    {loading && <div className="roci-loading"><div className="roci-spinner" /><span>正在读取可审计快照…</span></div>}
    {!loading && error && <div className="roci-error"><AlertTriangle size={20} /><div><strong>数据读取失败</strong><p>{error}</p><button className="roci-button" onClick={() => void load(true)}><RefreshCw size={14} />重新读取</button></div></div>}
    {!loading && !error && data && <><RociExplainability explanation={data.explanation || data.explanations?.market} compact />{children(data)}<div className="roci-footer-audit"><span><Database size={13} /> 数据截止：{value(data.data_cutoff_time)}</span><span><CheckCircle2 size={13} /> 只读适配器：{data.audit?.read_only_adapters === false ? 'UNKNOWN' : '已启用'}</span><span>缺失字段不会被填充为中性值</span></div></>}
  </RociFrame>;
}

export function RociMetric({ label, value: metricValue, sub, bar, tone: metricTone = 'blue' }: { label: string; value: any; sub?: string; bar?: number | null; tone?: 'good' | 'warn' | 'bad' | 'blue' | 'neutral' }) {
  return <div className="roci-metric"><span className="roci-label">{label}</span><strong className={`roci-metric-value roci-${metricTone}`}>{value(metricValue)}</strong>{sub && <small>{sub}</small>}{bar !== undefined && <RociBar value={bar} tone={metricTone === 'neutral' ? 'blue' : metricTone} />}</div>;
}

export function RociKeyValue({ label, value: itemValue, tone: itemTone }: { label: string; value: any; tone?: 'good' | 'warn' | 'bad' | 'blue' | 'neutral' }) {
  return <div className="roci-key-value"><span>{label}</span><RociStatusPill value={value(itemValue)} tone={itemTone || tone(itemValue)} /></div>;
}

export { value as displayValue, tone as valueTone, RociEvidence, RociSectionTitle };
