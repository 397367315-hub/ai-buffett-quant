'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import { ArrowRight, Ban, CircleDollarSign, Database, ShieldCheck, Target } from 'lucide-react';
import { RociSectionTitle, RociStatusPill } from './RociFrame';

type AnyRecord = Record<string, any>;

function score(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(1) : 'UNKNOWN';
}

function money(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '资金 UNKNOWN';
  const amount = Math.abs(value);
  const formatted = amount >= 100_000_000 ? `${(amount / 100_000_000).toFixed(2)}亿` : `${(amount / 10_000).toFixed(0)}万`;
  return `${value >= 0 ? '净流入' : '净流出'} ${formatted}`;
}

function riskTone(value: unknown): 'good' | 'warn' | 'bad' {
  if (value === 'LOW') return 'good';
  if (value === 'HIGH') return 'bad';
  return 'warn';
}

export default function RiskAdaptedRecommendations({ data, compact = false }: { data?: AnyRecord; compact?: boolean }) {
  const [excludeGem, setExcludeGem] = useState(true);
  const [excludeStar, setExcludeStar] = useState(true);
  const [excludeBse, setExcludeBse] = useState(true);
  const sectors = (data?.sectors || []).slice(0, compact ? 4 : 5);
  const allStocks = data?.stocks || [];
  const permittedStocks = useMemo(() => allStocks.filter((item: AnyRecord) => {
    if (excludeGem && item.board === '创业板') return false;
    if (excludeStar && item.board === '科创板') return false;
    if (excludeBse && item.board === '北交所') return false;
    return true;
  }), [allStocks, excludeBse, excludeGem, excludeStar]);
  const visibleStocks = permittedStocks.slice(0, compact ? 6 : 12);
  const hiddenCount = Math.max(0, allStocks.length - permittedStocks.length);

  return <section className="roci-panel roci-recommendation-panel">
    <RociSectionTitle
      eyebrow="RISK-ADAPTED RESEARCH SHORTLIST"
      title="按当前风险推荐板块与优秀个股"
      action={<RociStatusPill value={data?.posture_label || 'UNKNOWN'} tone={data?.posture === 'DEFENSIVE' ? 'bad' : data?.posture === 'OFFENSIVE' ? 'good' : 'warn'} />}
    />
    {!data || data.status !== 'AVAILABLE' ? <div className="roci-empty roci-recommendation-empty">{data?.note || '没有足够的可审计板块与个股数据，系统未生成推荐。'}</div> : <>
      <div className="roci-recommendation-summary">
        <span><ShieldCheck size={13} /> 风险定价 {data.risk_pricing || 'UNKNOWN'}</span>
        <span><Target size={13} /> ACTION {data.action_alignment || 'UNKNOWN'}</span>
        <span><Database size={13} /> 数据日 {data.data_date || 'UNKNOWN'} · {data.is_realtime ? '实时' : '最近缓存'}</span>
        <strong>{data.execution_policy}</strong>
      </div>
      <div className="roci-board-filters" aria-label="交易板块权限筛选">
        <span>交易权限</span>
        <label><input type="checkbox" checked={excludeGem} onChange={(event) => setExcludeGem(event.target.checked)} />排除创业板</label>
        <label><input type="checkbox" checked={excludeStar} onChange={(event) => setExcludeStar(event.target.checked)} />排除科创板</label>
        <label><input type="checkbox" checked={excludeBse} onChange={(event) => setExcludeBse(event.target.checked)} />排除北交所</label>
        {hiddenCount > 0 && <small>已隐藏 {hiddenCount} 只权限标的</small>}
      </div>
      <div className="roci-recommendation-layout">
        <div className="roci-recommendation-column">
          <div className="roci-subsection-head"><span>风险适配板块</span><small>强度、宽度、资金与板块内质量联合排序</small></div>
          <div className="roci-sector-shortlist">
            {sectors.map((item: AnyRecord) => <div className="roci-sector-shortlist-row" key={item.name}>
              <b>{item.rank}</b>
              <div><strong>{item.name}</strong><small>{item.classification} · 龙头 {item.leader?.name || 'UNKNOWN'}</small></div>
              <div className="roci-sector-signals"><span className={item.main_net_inflow >= 0 ? 'roci-up' : 'roci-down'}>{money(item.main_net_inflow)}</span><small>宽度 {score(item.breadth)}%</small></div>
              <div className="roci-shortlist-score"><strong>{score(item.fit_score)}</strong><small>适配分</small></div>
            </div>)}
          </div>
          {!!data.avoided_sectors?.length && <details className="roci-avoided-sectors"><summary><Ban size={12} />查看当前回避板块</summary>{data.avoided_sectors.map((item: AnyRecord) => <div key={item.name}><strong>{item.name}</strong><span>{(item.risk_flags || []).join(' · ') || '风险确认不足'}</span></div>)}</details>}
        </div>
        <div className="roci-recommendation-column">
          <div className="roci-subsection-head"><span>板块内优秀个股</span><small>盈利质量、风险安全、资金、量比与趋势联合筛选</small></div>
          <div className="roci-stock-shortlist">
            {visibleStocks.map((item: AnyRecord, index: number) => <Link href={`/roci/stock/${encodeURIComponent(item.code)}`} className="roci-stock-shortlist-row" key={item.code}>
              <div className="roci-stock-rank">{index + 1}</div>
              <div className="roci-stock-identity"><strong>{item.name}</strong><span>{item.code} · {item.sector}</span><small>{item.role} · {item.board}</small></div>
              <div className="roci-stock-reason"><span>{(item.reasons || [])[0] || '入选依据 UNKNOWN'}</span><small>{item.risk || '风险说明 UNKNOWN'}</small></div>
              <RociStatusPill value={item.risk_level === 'LOW' ? '低风险' : item.risk_level === 'HIGH' ? '高风险' : '中风险'} tone={riskTone(item.risk_level)} />
              <div className="roci-shortlist-score"><strong>{score(item.fit_score)}</strong><small>适配分</small></div>
              <ArrowRight size={13} />
            </Link>)}
            {!visibleStocks.length && <div className="roci-empty">当前权限筛选下没有通过风险门槛的优秀个股；可取消上方板块排除后查看完整研究清单。</div>}
          </div>
        </div>
      </div>
      <div className="roci-recommendation-method"><CircleDollarSign size={13} /><span>{data.method}</span><strong>{data.note}</strong></div>
    </>}
  </section>;
}
