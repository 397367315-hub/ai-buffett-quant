'use client';

import { useState } from 'react';
import { CalendarDays, History, Search, ShieldCheck } from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';
import { RociEvidence, RociFrame, RociSectionTitle, RociStatusPill } from '../components/RociFrame';

export default function RociReplayPage() {
  const [tradeDate, setTradeDate] = useState('');
  const [symbol, setSymbol] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [data, setData] = useState<any>(null);

  const replay = async () => {
    if (!tradeDate) { setError('请选择交易日期'); return; }
    setLoading(true); setError('');
    try { const response = await apiFetch<{ data: any }>('/roci/replay', { method: 'POST', body: JSON.stringify({ trade_date: tradeDate, symbol: symbol.trim() || null }), timeoutMs: 60000 }); setData(response.data); }
    catch (caught) { setError(friendlyApiError(caught, '历史复盘暂时不可用')); }
    finally { setLoading(false); }
  };

  return <RociFrame title="历史 Replay" subtitle="只使用当时已保存且当时可见的数据，禁止用事后结果污染当时结论">
    <div className="roci-panel roci-replay-form"><RociSectionTitle eyebrow="POINT-IN-TIME RECONSTRUCTION" title="选择历史截点" /><div className="roci-form-row"><label><span>交易日期</span><div className="roci-input-wrap"><CalendarDays size={15} /><input type="date" value={tradeDate} onChange={(event) => setTradeDate(event.target.value)} /></div></label><label><span>股票代码（可选）</span><div className="roci-input-wrap"><Search size={15} /><input inputMode="numeric" value={symbol} onChange={(event) => setSymbol(event.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="例如 600519" /></div></label><button className="roci-button roci-primary-button" onClick={replay} disabled={loading}>{loading ? '重建中…' : '开始 Replay'}</button></div><div className="roci-replay-rule"><ShieldCheck size={14} /><span>如果该日期没有已保存快照，系统返回 UNKNOWN，不使用今天的数据回填历史。</span></div></div>
    {error && <div className="roci-error">{error}</div>}
    {data && data.status === 'UNKNOWN' && <div className="roci-panel roci-empty"><History size={19} /><strong>无法重建</strong><p>{data.reason}</p></div>}
    {data && data.status !== 'UNKNOWN' && <><div className="roci-replay-chain"><div><span>当时生态</span><b>{data.battlefield?.regime || 'UNKNOWN'}</b></div><i>→</i><div><span>主要矛盾</span><b>{data.primary_contradiction?.statement || 'UNKNOWN'}</b></div><i>→</i><div><span>风险定价</span><b>{data.risk_pricing?.status || 'UNKNOWN'}</b></div><i>→</i><div><span>当时 ACTION</span><b>{data.action?.action || 'UNKNOWN'}</b></div></div><div className="roci-two-panel-grid"><div className="roci-panel"><RociSectionTitle eyebrow="THEN" title="当时可见证据" /><RociEvidence items={data.facts} /></div><div className="roci-panel"><RociSectionTitle eyebrow="SKILLS" title="当时触发 Skill" />{(data.skills?.items || []).filter((item: any) => item.triggered).slice(0, 10).map((item: any) => <div className="roci-replay-skill" key={item.skill_id}><span>{item.skill_id} {item.name}</span><RociStatusPill value={item.status} tone={item.status === 'SHADOW' ? 'warn' : 'blue'} /></div>)}</div></div><div className="roci-panel roci-notice"><History size={15} /><span>来源快照日期：{data.replay?.source_snapshot_date || data.trade_date}。历史结果对比只有在真实后续结果已持久化后才显示，当前不补造。</span></div></>}
  </RociFrame>;
}
