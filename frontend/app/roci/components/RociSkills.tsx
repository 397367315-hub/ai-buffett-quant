'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { BookOpen, Filter, Search, ShieldAlert } from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';
import { RociEvidence, RociFrame, RociSectionTitle, RociStatusPill } from './RociFrame';

type AnyRecord = Record<string, any>;

const CATEGORIES = ['全部', '认知边界', '风险管理', '生态', '机会', '操盘之神', '十全武功', '数据质量', '供应承接', '赔率', '预期差', '执行'];
const STATES = ['全部', 'ACTIVE', 'SHADOW', 'DETECT_ONLY', 'KNOWLEDGE_ONLY', 'DEGRADED', 'DISABLED'];

export default function RociSkills() {
  const [items, setItems] = useState<AnyRecord[]>([]);
  const [sourceSummary, setSourceSummary] = useState<AnyRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('全部');
  const [state, setState] = useState('全部');
  const [source, setSource] = useState('全部');
  const [selected, setSelected] = useState<AnyRecord | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await apiFetch<{ data: { items: AnyRecord[]; source_summary?: AnyRecord[] } }>('/roci/skills', { timeoutMs: 60000 });
      setItems(response.data.items || []);
      setSourceSummary(response.data.source_summary || []);
    }
    catch (caught) { setError(friendlyApiError(caught, 'Skill Registry暂时不可用')); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const filtered = useMemo(() => items.filter((item) => (
    (!query || `${item.skill_id} ${item.name} ${item.source_name}`.toLowerCase().includes(query.toLowerCase()))
    && (category === '全部' || item.category === category)
    && (state === '全部' || item.status === state)
    && (source === '全部' || item.source_key === source)
  )), [items, query, category, state, source]);

  return <RociFrame title="Skill 技能中心" subtitle="76 个 Skill 全部可见、可追溯、可解释；Skill 是证据，不是投票" compact>
    <div className="roci-registry-banner"><div><span className="roci-eyebrow">SKILL REGISTRY · ROCI V1.0</span><strong>资料主张、工程化规则、运行触发和验证表现分开保存</strong></div><div className="roci-registry-count"><b>{items.length || 76}</b><span>个 Skill · {sourceSummary.length || '—'} 个来源</span></div></div>
    <div className="roci-filter-bar"><label className="roci-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 Skill、来源或编号" /></label><label><Filter size={14} /><select value={category} onChange={(event) => setCategory(event.target.value)}>{CATEGORIES.map((item) => <option key={item}>{item}</option>)}</select></label><label><ShieldAlert size={14} /><select value={state} onChange={(event) => setState(event.target.value)}>{STATES.map((item) => <option key={item}>{item}</option>)}</select></label><label><BookOpen size={14} /><select value={source} onChange={(event) => setSource(event.target.value)}><option value="全部">全部来源</option>{sourceSummary.map((item) => <option key={item.source_key} value={item.source_key}>{item.name} ({item.skill_count})</option>)}</select></label></div>
    {loading && <div className="roci-loading"><div className="roci-spinner" /><span>读取 Skill Registry…</span></div>}
    {error && <div className="roci-error">{error}</div>}
    {!loading && !error && <div className="roci-skill-table"><div className="roci-table-head"><span>编号 / 名称</span><span>类别</span><span>状态</span><span>当前运行</span><span>可用性</span><span>来源</span><span>验证</span></div>{filtered.map((item) => <button className="roci-table-row" key={item.skill_id} onClick={() => setSelected(item)}><span><b>{item.skill_id}</b><small>{item.name}</small></span><span>{item.category}</span><span><RociStatusPill value={item.status} tone={item.status === 'ACTIVE' ? 'good' : item.status === 'SHADOW' ? 'warn' : 'blue'} /></span><span>{item.triggered ? <RociStatusPill value={`触发${item.contribution != null ? ` · +${Math.round(item.contribution)}` : ''}`} tone={item.status === 'ACTIVE' ? 'good' : 'warn'} /> : <span className="roci-muted">未触发</span>}</span><span><RociStatusPill value={item.state?.availability?.available ? '可用' : item.enabled === false ? '已禁用' : '待数据'} tone={item.state?.availability?.available ? 'good' : item.enabled === false ? 'bad' : 'warn'} /></span><span className="roci-source-short">{item.source_name}</span><span>{item.validation_status === 'NOT_TESTED' ? <span className="roci-muted">未验证</span> : item.validation_status}</span></button>)}</div>}
    {!loading && !filtered.length && <div className="roci-empty">没有符合当前筛选条件的 Skill。</div>}
    {selected && <SkillDrawer skill={selected} onClose={() => setSelected(null)} />}
  </RociFrame>;
}

export function SkillDrawer({ skill: initial, onClose }: { skill: AnyRecord; onClose: () => void }) {
  const [skill, setSkill] = useState(initial);
  useEffect(() => { void apiFetch<{ data: AnyRecord }>(`/roci/skills/${initial.skill_id}`).then((response) => setSkill(response.data)).catch(() => undefined); }, [initial.skill_id]);
  const runtime = skill.runtime || {};
  const availability = skill.availability || runtime.state?.availability || {};
  const runs = skill.runs || [];
  const today = runtime.trade_date;
  const todayTriggers = runs.filter((item: AnyRecord) => item.triggered && (!today || item.trade_date === today)).length;
  const list = (items: unknown[]) => items?.length ? items.join(' · ') : '未定义';
  return <div className="roci-drawer-backdrop" onClick={onClose}><aside className="roci-drawer" onClick={(event) => event.stopPropagation()}><button className="roci-drawer-close" onClick={onClose}>×</button><div className="roci-eyebrow">{skill.skill_id} · {skill.category} · {skill.version || 'roci-v1.0'}</div><h2>{skill.name}</h2><div className="roci-drawer-pills"><RociStatusPill value={skill.status} tone={skill.status === 'ACTIVE' ? 'good' : skill.status === 'SHADOW' ? 'warn' : skill.status === 'DISABLED' ? 'bad' : 'blue'} /><RociStatusPill value={availability.available ? '当前可用' : skill.status === 'DISABLED' ? '已禁用' : '数据待补'} tone={availability.available ? 'good' : skill.status === 'DISABLED' ? 'bad' : 'warn'} /></div><div className="roci-drawer-summary"><div><span>当前影响</span><b>{runtime.contribution == null ? '不参与' : `+${runtime.contribution}`}</b></div><div><span>今日触发</span><b>{todayTriggers || 0}</b></div><div><span>运行结果</span><b>{runtime.triggered ? '已触发' : '未触发'}</b></div></div><h3>资料来源</h3><p><BookOpen size={14} /> {skill.source_name || 'UNKNOWN'} · {skill.source_section || '章节 UNKNOWN'} · 页码 {skill.source_pages || '未提供'}</p><div className="roci-source-claim"><span>SOURCE_CLAIM</span>{skill.source_claim || '暂无资料主张'}</div><h3>工程化定义</h3><p>{skill.engineered_definition || '暂无定义'}</p><h3>数据依赖与适用范围</h3><div className="roci-drawer-detail-grid"><div><span>依赖</span><strong>{list(skill.data_requirements || [])}</strong></div><div><span>适用生态</span><strong>{list(skill.applicable_regimes || [])}</strong></div><div><span>禁用生态</span><strong>{list(skill.forbidden_regimes || [])}</strong></div><div><span>可用性</span><strong>{availability.reason || 'UNKNOWN'}</strong></div></div><h3>当前检测与证据</h3><p>{runtime.state?.shadow_excluded_from_action ? 'Shadow 只进入观察和实验，不参与最终 ACTION。' : skill.status === 'DISABLED' ? '该 Skill 已被禁用，当前不会触发，也不会贡献到动作。' : availability.reason || '规则在当前数据截面上运行，结果受数据完整度约束。'}</p><RociEvidence items={runtime.evidence || skill.evidence} /><h3>验证表现</h3><div className="roci-performance-grid"><div><span>样本</span><b>{skill.performance?.sample_size || '未验证'}</b></div><div><span>胜率</span><b>{skill.performance?.hit_rate == null ? '未验证' : `${skill.performance.hit_rate}%`}</b></div><div><span>Profit Factor</span><b>{skill.performance?.profit_factor == null ? '未验证' : skill.performance.profit_factor}</b></div><div><span>期望R</span><b>{skill.performance?.expectancy_r == null ? '未验证' : skill.performance.expectancy_r}</b></div><div><span>最大回撤</span><b>{skill.performance?.max_drawdown == null ? '未验证' : skill.performance.max_drawdown}</b></div><div><span>衰减</span><b>{skill.recent_decay || '未验证'}</b></div></div></aside></div>;
}
