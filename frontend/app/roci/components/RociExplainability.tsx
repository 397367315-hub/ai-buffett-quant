'use client';

import { useState } from 'react';
import { ChevronDown, ChevronRight, CircleAlert, Database, GitBranch, ShieldCheck, Target } from 'lucide-react';

type AnyRecord = Record<string, any>;

function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'UNKNOWN';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
function percent(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)}%` : 'UNKNOWN';
}

function strength(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '○○○ UNKNOWN';
  if (value >= .75) return '●●● 强';
  if (value >= .45) return '●●○ 中';
  return '●○○ 弱';
}

function Group({ title, icon, children, open = true }: { title: string; icon: React.ReactNode; children: React.ReactNode; open?: boolean }) {
  const [expanded, setExpanded] = useState(open);
  return <div className="roci-explain-group">
    <button type="button" className="roci-explain-group-head" onClick={() => setExpanded((value) => !value)}>
      <span>{icon}{title}</span>{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
    </button>
    {expanded && <div className="roci-explain-group-body">{children}</div>}
  </div>;
}

export default function RociExplainability({ explanation, compact = false }: { explanation?: AnyRecord; compact?: boolean }) {
  const why = explanation?.why || {};
  const quality = explanation?.data_quality || {};
  const drivers = (why.primary_drivers || []) as AnyRecord[];
  const facts = (why.supporting_evidence || []) as AnyRecord[];
  const counter = (why.counter_evidence || []) as AnyRecord[];
  const alternatives = (why.alternative_hypotheses || []) as AnyRecord[];
  const chain = (why.transmission_chain || []) as AnyRecord[];
  const validations = (why.validation_signals || []) as string[];
  const invalidations = (why.invalidation_signals || []) as string[];
  if (!explanation) return <div className="roci-empty">当前没有可用的因果解释快照。</div>;

  return <section className={`roci-explain-panel ${compact ? 'compact' : ''}`}>
    <div className="roci-explain-intro">
      <div><div className="roci-eyebrow">ENGINE 15 · WHY / EVIDENCE TRACE</div><h3>为什么这样判断？</h3><p>{why.summary || '当前没有足够证据形成解释。'}</p></div>
      <div className="roci-explain-quality"><strong>{quality.score_pct != null ? `${Math.round(quality.score_pct)}%` : 'UNKNOWN'}</strong><span>证据质量</span><small>{quality.data_cutoff_time || '截止时间 UNKNOWN'}</small></div>
    </div>
    <div className="roci-explain-note">{why.contribution_note || '解释贡献度是模型权重，不代表现实世界严格因果比例。'}</div>
    <div className="roci-explain-grid">
      <Group title="主要驱动因子" icon={<Target size={13} />}>
        {drivers.slice(0, compact ? 3 : 4).map((item, index) => <div className="roci-driver-row" key={`${item.name}-${index}`}><div className="roci-driver-main"><strong>{index + 1}. {item.name || 'UNKNOWN'}</strong><span>{item.description || 'UNKNOWN'}</span></div><b>{percent(item.importance)}</b><small>{strength(item.evidence_strength)}</small></div>)}
        {!drivers.length && <div className="roci-empty">暂无主要驱动因子。</div>}
      </Group>
      {!compact && <Group title="支持证据与反证" icon={<ShieldCheck size={13} />}><div className="roci-evidence-compact"><div><span className="roci-explain-subtitle good">支持</span>{facts.slice(0, 6).map((item, index) => <div className="roci-explain-evidence" key={`s-${index}`}><span>✓</span><div><strong>{item.claim || 'UNKNOWN'}</strong><small>{text(item.value)} · {strength(item.evidence_strength)} · {item.source_table || '来源 UNKNOWN'}</small></div></div>)}</div><div><span className="roci-explain-subtitle bad">反证 / 缺口</span>{counter.slice(0, 6).map((item, index) => <div className="roci-explain-evidence" key={`c-${index}`}><span>!</span><div><strong>{item.claim || 'UNKNOWN'}</strong><small>{text(item.value)} · {strength(item.evidence_strength)} · {item.source_table || '来源 UNKNOWN'}</small></div></div>)}</div></div></Group>}
      <Group title="可能的传导路径" icon={<GitBranch size={13} />}><div className="roci-chain-list">{chain.map((item, index) => <div className="roci-chain-row" key={`${item.from}-${index}`}><span>{item.from || 'UNKNOWN'}</span><i>→</i><span>{item.to || 'UNKNOWN'}</span><small>{item.status || 'INFERRED'} · {percent(item.confidence)}</small></div>)}</div>{!chain.length && <div className="roci-empty">暂无可追溯传导链。</div>}</Group>
      {!compact && <Group title="其他解释" icon={<CircleAlert size={13} />}><div className="roci-alternative-list">{alternatives.slice(0, 4).map((item, index) => <div className="roci-alternative-row" key={`${item.hypothesis}-${index}`}><strong>{String.fromCharCode(65 + index)}. {item.hypothesis || 'UNKNOWN'}</strong><b>{item.support_score != null ? item.support_score : 'UNKNOWN'}</b><small>解释支持度，不是统计概率</small></div>)}</div></Group>}
      <Group title="下一步验证与失效条件" icon={<ShieldCheck size={13} />}><div className="roci-validation-grid"><div><span className="roci-explain-subtitle good">验证</span>{validations.slice(0, 4).map((item, index) => <p key={`v-${index}`}>□ {item}</p>)}</div><div><span className="roci-explain-subtitle bad">失效</span>{invalidations.slice(0, 4).map((item, index) => <p key={`i-${index}`}>× {item}</p>)}</div></div></Group>
      {!compact && <Group title="数据血缘" icon={<Database size={13} />} open={false}><div className="roci-lineage-list">{(explanation.lineage || []).slice(0, 12).map((item: AnyRecord, index: number) => <div className="roci-lineage-row" key={`${item.claim}-${index}`}><strong>{item.claim || 'UNKNOWN'}</strong><span>{item.source_table || 'UNKNOWN'} · {item.source_field || 'UNKNOWN'}</span><small>原始值 {text(item.raw_value)} · {item.timestamp || '时间 UNKNOWN'}</small></div>)}</div></Group>}
    </div>
    <div className="roci-explain-footer">{explanation.llm_boundary || '结构化规则产生判断，语言模型不改变概率、权重或行情数据。'}</div>
  </section>;
}
