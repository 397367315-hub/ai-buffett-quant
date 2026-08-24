'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { ArrowLeft, CheckCircle2, FlaskConical, History, ShieldAlert } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { apiFetch, friendlyApiError } from '@/lib/api';
import { RociBar, RociEvidence, RociFrame, RociSectionTitle, RociStatusPill } from '../../components/RociFrame';

export default function RociPatternDetailPage() {
  const params = useParams<{ pattern: string }>();
  const pattern = decodeURIComponent(String(params.pattern || ''));
  const [data, setData] = useState<any>(null); const [error, setError] = useState('');
  const load = useCallback(async () => { try { const result = await apiFetch<{ data: any }>(`/roci/opportunities/${encodeURIComponent(pattern)}`); setData(result.data); } catch (caught) { setError(friendlyApiError(caught, '机会形态暂时不可用')); } }, [pattern]);
  useEffect(() => { void load(); }, [load]);
  return <RociFrame title="机会形态详情" subtitle="形态名不是买点；识别、展示、统计和验证必须分开"><Link href="/roci/opportunities" className="roci-back-inline"><ArrowLeft size={14} />返回机会战术库</Link>{error && <div className="roci-error">{error}</div>}{!data && !error && <div className="roci-loading"><div className="roci-spinner" />读取形态记录…</div>}{data && <><div className="roci-panel roci-pattern-detail-hero"><div className="roci-eyebrow">{data.pattern_id} · {data.category}</div><div className="roci-detail-head"><div><h2>{data.name}</h2><p>{data.definition}</p></div><RociStatusPill value={data.status} tone={data.status === 'SHADOW' ? 'warn' : 'blue'} /></div><div className="roci-shadow-banner"><FlaskConical size={15} /><span>{data.status === 'SHADOW' ? '该形态尚未通过 Active 验证，只提供观察，不参与最终 ACTION。' : '该形态仍需持续验证。'}</span></div></div><div className="roci-two-panel-grid"><div className="roci-panel"><RociSectionTitle eyebrow="ENGINEERING" title="检测规则" />{(data.detection_rule?.needs || []).map((item: string) => <div className="roci-condition good" key={item}><CheckCircle2 size={14} />{item}</div>)}<RociEvidence items={data.evidence} /></div><div className="roci-panel"><RociSectionTitle eyebrow="VALIDATION" title="历史表现" /><div className="roci-performance-grid"><div><span>样本</span><b>{data.validation?.sample_size || '未验证'}</b></div><div><span>命中率</span><b>{data.validation?.hit_rate == null ? '未验证' : `${data.validation.hit_rate}%`}</b></div><div><span>期望R</span><b>{data.validation?.expectancy_r == null ? '未验证' : data.validation.expectancy_r}</b></div><div><span>状态</span><b>{data.unverified_label || '未验证'}</b></div></div><p className="roci-muted-block">没有 PIT、成本、Walk-forward 和样本外记录时，系统拒绝显示伪造统计。</p></div></div><div className="roci-panel"><RociSectionTitle eyebrow="TRIGGER HISTORY" title="最近触发记录" />{(data.history || []).length ? <div className="roci-history-list">{data.history.map((item: any, index: number) => <div className="roci-history-row" key={`${item.snapshot_key}-${index}`}><History size={14} /><span>{item.observed_at || 'UNKNOWN'}</span><span>{item.symbol || '市场级'}</span><RociStatusPill value={item.triggered ? '触发' : '未触发'} tone={item.triggered ? 'good' : 'neutral'} /><span>{item.score == null ? '—' : Math.round(item.score)}</span></div>)}</div> : <div className="roci-empty">暂无历史触发记录。</div>}</div></>}</RociFrame>;
}
