'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { ArrowRight, ChevronRight, CircleAlert, Clock3, Database, Info, ShieldCheck, Sparkles, Swords, Target, TriangleAlert } from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';
import { RociBar, RociEvidence, RociFrame, RociSectionTitle, RociStatusPill } from './RociFrame';
import { SkillDrawer } from './RociSkills';
import RiskAdaptedRecommendations from './RiskAdaptedRecommendations';
import RociExplainability from './RociExplainability';

type AnyRecord = Record<string, any>;

function pct(value: unknown): string { return typeof value === 'number' ? `${Math.round(value)}%` : 'UNKNOWN'; }
function scoreTone(value: unknown): 'good' | 'warn' | 'bad' | 'blue' { if (typeof value !== 'number') return 'blue'; return value >= 65 ? 'good' : value <= 35 ? 'bad' : 'warn'; }

export default function RociDashboard() {
  const [data, setData] = useState<AnyRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [skill, setSkill] = useState<AnyRecord | null>(null);

  const pollRefreshStatus = useCallback(async () => {
    // The backend now owns one serialized refresh coordinator. Poll its
    // authoritative status and reload the persisted snapshot when the job
    // finishes, so the cockpit updates without visiting each linked board.
    setRefreshing(true);
    try {
      for (let attempt = 0; attempt < 90; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
        try {
          const response = await apiFetch<{ data: AnyRecord }>('/roci/refresh-status', { timeoutMs: 12000 });
          const unified = response.data?.unified_refresh || {};
          const legacy = response.data?.refresh_job || {};
          const status = unified.status ? unified : legacy;
          setData((previous) => previous ? {
            ...previous,
            refresh_report: {
              ...(previous.refresh_report || {}),
              coordinator: unified,
              linked_sources: {
                ...((previous.refresh_report || {}).linked_sources || {}),
                ...(unified.sources || {}),
                v4_data_pipeline: unified.sources?.v4_data_pipeline || legacy,
              },
            },
          } : previous);
          const normalized = String(status.status || '').toLowerCase();
          if (['queued', 'running'].includes(normalized)) continue;
          if (unified.status && ['completed', 'completed_with_gaps'].includes(normalized)) {
            try {
              const latest = await apiFetch<{ data: AnyRecord }>('/roci/dashboard', { timeoutMs: 30000 });
              setData(latest.data);
            } catch {
              // Keep the previous snapshot and the completed status visible.
            }
          }
          break;
        } catch {
          break;
        }
      }
    } finally {
      setRefreshing(false);
    }
  }, []);

  const load = useCallback(async (refresh = false) => {
    setError(''); refresh ? setRefreshing(true) : setLoading(true);
    try {
      const response = await apiFetch<{ data: AnyRecord }>(`/roci/dashboard${refresh ? '?refresh=true' : ''}`, { timeoutMs: 60000 });
      setData(response.data);
      const coordinator = response.data?.refresh_report?.coordinator || {};
      if (refresh || ['queued', 'running'].includes(String(coordinator.status || '').toLowerCase())) void pollRefreshStatus();
    }
    catch (caught) { setError(friendlyApiError(caught, 'ROCI快照暂时不可用')); }
    finally { setLoading(false); setRefreshing(false); }
  }, [pollRefreshStatus]);
  useEffect(() => { void load(); }, [load]);

  if (loading) return <RociFrame title="ROCI 风险机会认知中枢" subtitle="战场 · 力量 · 矛盾 · 风险 · 机会"><div className="roci-loading"><div className="roci-spinner" /><span>正在读取现有 V4 / V5 / V5.1 快照…</span></div></RociFrame>;
  if (error) return <RociFrame title="ROCI 风险机会认知中枢" subtitle="独立 Sidecar"><div className="roci-error"><CircleAlert size={20} /><div><strong>快照读取失败</strong><p>{error}</p><button className="roci-button" onClick={() => void load(true)}>重新读取</button></div></div></RociFrame>;
  const battle = data?.battlefield || {}; const primary = data?.primary_contradiction || {}; const pricing = data?.risk_pricing || {}; const stress = data?.stress_test || {}; const cognition = data?.cognitive_risk || {}; const forces = data?.forces?.forces || []; const opportunities = data?.opportunities || {}; const action = data?.action || {}; const riskAdapted = opportunities.risk_adapted || {}; const intraday = data?.intraday || {}; const refreshCoordinator = data?.refresh_report?.coordinator || {};
  return <RociFrame title="ROCI 风险机会认知中枢" subtitle="把风险拆成力量、定价、压力与失效，把机会拆成结构、预期差、反脆弱与赔率" refresh={refreshing} onRefresh={() => void load(true)}>
    <div className="roci-meta-row"><span>交易日 {data?.trade_date || 'UNKNOWN'}</span><span>数据截止 {data?.data_cutoff_time || 'UNKNOWN'}</span><span>完整度 {pct(data?.data_completeness_pct)}</span><span>盘中数据 {intraday?.data_status || 'UNKNOWN'}</span><span className={data?.cache_used ? 'roci-cache' : 'roci-live'}>{data?.cache_used ? '缓存快照' : '已重新计算'}</span></div>
    <section className="roci-hero-grid">
      <div className="roci-panel roci-battle-card"><RociSectionTitle eyebrow="ENGINE 01 · BATTLEFIELD REGIME" title="当前打的是什么仗？" action={<RociStatusPill value={battle.label || battle.regime} tone={battle.regime === 'STRONG_OFFENSE' ? 'good' : battle.regime === 'CAPITULATION' || battle.regime === 'DEFENSIVE' ? 'bad' : 'warn'} />} /><div className="roci-regime-code">{battle.regime || 'UNKNOWN'}</div><p className="roci-lead-copy">{battle.market_reward || '市场正在奖励什么：UNKNOWN'}</p><div className="roci-two-column"><div><span className="roci-label">正在奖励</span><p>{battle.market_reward || 'UNKNOWN'}</p></div><div><span className="roci-label">正在惩罚</span><p>{battle.market_penalty || 'UNKNOWN'}</p></div></div><RociEvidence items={battle.facts} /></div>
      <div className="roci-panel roci-action-card"><RociSectionTitle eyebrow="DECISION LAYER" title="当前动作" /><div className={`roci-action-value roci-action-${String(action.action || 'WAIT').toLowerCase()}`}>{action.action || 'UNKNOWN'}</div><p>{action.reason || '暂无动作解释'}</p><div className="roci-action-metrics"><div><span>风险预算</span><strong>{action.risk_budget != null ? `${action.risk_budget}%` : 'UNKNOWN'}</strong></div><div><span>置信度</span><strong>{pct(action.confidence)}</strong></div></div><div className="roci-disclaimer">Shadow Skill 不参与最终 ACTION。ROCI 仅用于研究、模拟和复盘。</div></div>
    </section>
    <div className="roci-quick-grid">{[{ href: '/roci/battlefield', label: '敌友地图', icon: Swords, value: `${forces.length} 个力量对象` }, { href: '/roci/contradiction', label: '主要矛盾', icon: Target, value: primary.statement || 'UNKNOWN' }, { href: '/roci/risk-pricing', label: '风险定价', icon: ShieldCheck, value: pricing.status || 'UNKNOWN' }, { href: '/roci/stress-test', label: '压力测试', icon: TriangleAlert, value: stress.state || 'UNKNOWN' }, { href: '/roci/opportunities', label: '机会战术库', icon: Sparkles, value: `${(opportunities.patterns || []).filter((item: AnyRecord) => item.triggered).length} 个观察触发` }, { href: '/roci/skills', label: 'Skill 技能中心', icon: Info, value: `${data?.skills?.count || 76} 个可追溯 Skill` }].map((item) => { const Icon = item.icon; return <Link className="roci-quick-link" href={item.href} key={item.href}><Icon size={17} /><span><strong>{item.label}</strong><small>{item.value}</small></span><ChevronRight size={15} /></Link>; })}</div>
    <section className="roci-section-grid">
      <div className="roci-panel"><RociSectionTitle eyebrow="ENGINE 02" title="敌友力量地图" action={<Link href="/roci/battlefield" className="roci-text-link">展开 <ArrowRight size={13} /></Link>} /><div className="roci-force-list">{forces.slice(0, 8).map((item: AnyRecord) => <div className="roci-force-row" key={item.force_id}><span className={`roci-force-marker ${String(item.side || '').toLowerCase()}`} /><span className="roci-force-name">{item.name}</span><RociStatusPill value={item.side} tone={item.side === 'ALLY' ? 'good' : item.side === 'ENEMY' ? 'bad' : 'warn'} /><span className="roci-force-strength">{item.strength != null ? Math.round(item.strength) : '—'}</span><span className="roci-force-direction">{item.direction || 'UNKNOWN'}</span></div>)}</div><div className="roci-convert-note"><span className="roci-label">可转化力量</span><p>高换手、分歧和压力事件不能立即加减分，等待价格、承接和相对强度验证。</p></div></div>
      <div className="roci-panel"><RociSectionTitle eyebrow="ENGINE 03" title="主要矛盾" /><div className="roci-contradiction-statement">{primary.statement || '主要矛盾 UNKNOWN'}</div><p className="roci-body-copy">{primary.why || '当前证据不足，无法解释为何它是主要矛盾。'}</p><div className="roci-check-columns"><div><span className="roci-label">解决条件</span>{(primary.what_would_resolve || []).slice(0, 3).map((item: string) => <div className="roci-check" key={item}>+ {item}</div>)}</div><div><span className="roci-label">恶化条件</span>{(primary.what_would_worsen || []).slice(0, 3).map((item: string) => <div className="roci-risk-line" key={item}>− {item}</div>)}</div></div></div>
      <div className="roci-panel"><RociSectionTitle eyebrow="ENGINES 04 / 05" title="风险定价与压力" /><div className="roci-stat-line"><span>风险定价</span><RociStatusPill value={pricing.status} tone={pricing.status === 'MOSTLY_PRICED' ? 'good' : pricing.status === 'NOT_PRICED' ? 'bad' : 'warn'} /></div><div className="roci-stat-line"><span>压力响应</span><RociStatusPill value={stress.state} tone={stress.state === 'ANTIFRAGILE' ? 'good' : stress.state === 'FRAGILE' ? 'bad' : 'warn'} /></div><RociBar value={stress.confidence} tone={stress.state === 'FRAGILE' ? 'bad' : 'blue'} /><p className="roci-muted-block">{stress.summary || '压力测试需要足够的日线历史。'}</p><Link href="/roci/stress-test" className="roci-inline-link">查看最近压力事件 <ArrowRight size={13} /></Link></div>
    </section>
    {data?.refresh_report && <div className="roci-refresh-report"><div className="roci-refresh-report-head"><strong>统一刷新报告</strong><span>{refreshCoordinator.message || (data.refresh_report.requested ? '核心快照已返回，关联长任务按真实进度继续更新' : '当前使用统一快照')}</span><b>{typeof refreshCoordinator.progress === 'number' ? `${refreshCoordinator.progress}%` : '—'}</b></div><div className="roci-refresh-track"><span style={{ width: `${Math.max(0, Math.min(100, Number(refreshCoordinator.progress) || 0))}%` }} /></div><div className="roci-refresh-sources">{Object.entries(data.refresh_report.linked_sources || {}).map(([key, value]: [string, any]) => <small key={key}>{key}: {value?.status || 'UNKNOWN'}{value?.progress != null ? ` · ${value.progress}%` : ''}{value?.message ? ` · ${value.message}` : ''}</small>)}</div></div>}
    <RiskAdaptedRecommendations data={riskAdapted} compact />
    <RociExplainability explanation={data?.explanation || data?.explanations?.market} compact />
    <section className="roci-panel roci-cognitive-panel"><RociSectionTitle eyebrow="ENGINE 10 · COGNITIVE / MODEL RISK" title="人的风险与模型风险分开记录" action={<RociStatusPill value={cognition.level || 'UNKNOWN'} tone={cognition.level === 'HIGH' || cognition.level === 'EXTREME' ? 'bad' : cognition.level === 'MEDIUM' ? 'warn' : 'blue'} />} /><div className="roci-cognitive-grid"><div><span className="roci-label">模型风险</span><strong>{cognition.model_risks?.length || 0} 项</strong><p>{(cognition.model_risks || []).slice(0, 2).map((item: AnyRecord) => item.risk).join(' · ') || '当前没有已观测模型风险'}</p></div><div><span className="roci-label">人的风险</span><strong>{cognition.human_risks?.length || 0} 项</strong><p>{cognition.unknown_human_risk ? '未接入用户行为记录，不从价格图表推断' : (cognition.human_risks || []).map((item: AnyRecord) => item.risk).join(' · ')}</p></div><div><span className="roci-label">行动约束</span><strong>Shadow 不参与</strong><p>{cognition.policy || '未验证模型不参与最终 ACTION。'}</p></div></div></section>
    <section className="roci-panel roci-opportunity-panel"><RociSectionTitle eyebrow="ENGINE 08 · OPPORTUNITY ARSENAL" title="机会不是荐股榜，而是正在出现的战术结构" action={<Link href="/roci/opportunities" className="roci-text-link">查看完整机会库 <ArrowRight size={13} /></Link>} /><div className="roci-pattern-grid">{(opportunities.patterns || []).filter((item: AnyRecord) => ['妖股', '反脆弱', '突破', '机会迁徙'].includes(item.category)).slice(0, 8).map((item: AnyRecord) => <Link href={`/roci/opportunities/${encodeURIComponent(item.pattern_id)}`} className="roci-pattern-card" key={item.pattern_id}><div className="roci-pattern-head"><span>{item.name}</span><RociStatusPill value={item.status} tone={item.status === 'SHADOW' ? 'warn' : 'blue'} /></div><p>{item.definition}</p><div className="roci-pattern-foot"><span>{item.triggered ? '今日检测到观察信号' : '今日未触发'}</span><span>{item.score != null ? Math.round(item.score) : '—'}</span></div></Link>)}</div></section>
    <section className="roci-panel"><RociSectionTitle eyebrow="SKILL TRACE" title="今日触发 Skill（证据可点开）" action={<Link href="/roci/skills" className="roci-text-link">进入技能中心 <ArrowRight size={13} /></Link>} /><div className="roci-skill-strip">{(data?.skills?.items || []).filter((item: AnyRecord) => item.triggered).slice(0, 12).map((item: AnyRecord) => <button key={item.skill_id} className={`roci-skill-chip ${item.status === 'SHADOW' ? 'shadow' : ''}`} onClick={() => setSkill(item)}><span>{item.skill_id}</span><strong>{item.name}</strong><small>{item.status}{item.score != null ? ` · ${Math.round(item.score)}` : ''}</small></button>)}</div>{!(data?.skills?.items || []).some((item: AnyRecord) => item.triggered) && <div className="roci-empty">当前没有满足触发条件的 Skill；知识-only 和缺失数据不会被伪造为信号。</div>}</section>
    <div className="roci-footer-audit"><span><Clock3 size={13} /> 截止：{data?.data_cutoff_time || 'UNKNOWN'}</span><span><Database size={13} /> 来源：{Object.entries(data?.source_status || {}).map(([key, value]) => `${key}:${value}`).join(' · ') || 'UNKNOWN'}</span><span><Info size={13} /> FACT / INFERENCE / SOURCE_CLAIM 已分离</span></div>
    {skill && <SkillDrawer skill={skill} onClose={() => setSkill(null)} />}
  </RociFrame>;
}
