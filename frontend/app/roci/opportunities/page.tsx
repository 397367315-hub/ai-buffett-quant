'use client';

import Link from 'next/link';
import { ArrowRight, Eye, FlaskConical, Sparkles } from 'lucide-react';
import { RociDataPage } from '../components/RociDataPanel';
import { RociSectionTitle, RociStatusPill } from '../components/RociFrame';
import RiskAdaptedRecommendations from '../components/RiskAdaptedRecommendations';

const GROUPS = ['妖股', '反脆弱', '突破', '机会迁徙', '十全武功'];

export default function RociOpportunitiesPage() {
  return <RociDataPage title="机会战术库" subtitle="先按风险生态筛板块和优秀个股，再验证战术形态；研究清单不等于买入指令" endpoint="/roci/opportunities">{(data) => { const patterns = data.patterns || []; return <><div className="roci-panel roci-opportunity-intro"><RociSectionTitle eyebrow="ENGINE 08" title="风险并非机会的反义词" /><p>ROCI 先判断风险是否被承受、吸收、定价，再寻找结构、预期差、反脆弱和赔率。未验证的形态只进入观察层，不进入最终 ACTION。</p><div className="roci-opportunity-stats"><div><b>{patterns.filter((item: any) => item.triggered).length}</b><span>今日观察触发</span></div><div><b>{patterns.filter((item: any) => item.status === 'SHADOW').length}</b><span>Shadow 形态</span></div><div><b>{data.risk_adapted?.stocks?.length || 0}</b><span>风险适配个股</span></div></div></div><RiskAdaptedRecommendations data={data.risk_adapted} />{GROUPS.map((group) => <section className="roci-panel roci-pattern-section" key={group}><RociSectionTitle eyebrow={group === '十全武功' ? 'CAOPAN ZHISHEN · SHADOW LAB' : 'PATTERN GROUP'} title={group} action={group === '十全武功' ? <Link href="/roci/lab" className="roci-text-link">进入 Shadow Lab <ArrowRight size={13} /></Link> : undefined} /><div className="roci-pattern-grid">{patterns.filter((item: any) => item.category === group).map((item: any) => <Link href={`/roci/opportunities/${encodeURIComponent(item.pattern_id)}`} className="roci-pattern-card large" key={item.pattern_id}><div className="roci-pattern-head"><span>{item.name}</span><RociStatusPill value={item.status} tone={item.status === 'SHADOW' ? 'warn' : 'blue'} /></div><p>{item.definition}</p><div className="roci-pattern-rule"><span>检测需求</span><span>{(item.detection_rule?.needs || item.rule?.needs || []).join(' · ') || 'UNKNOWN'}</span></div><div className="roci-pattern-foot"><span>{item.triggered ? <><Sparkles size={12} /> 已触发观察</> : <><Eye size={12} /> 未触发</>}</span><span>{item.score != null ? `观察分 ${Math.round(item.score)}` : '未评分'}</span></div>{item.status === 'SHADOW' && <div className="roci-shadow-note"><FlaskConical size={12} /> Shadow：不参与最终 ACTION</div>}</Link>)}</div>{!patterns.some((item: any) => item.category === group) && <div className="roci-empty">该类别尚未注册模式。</div>}</section>)}</>; }}</RociDataPage>;
}
