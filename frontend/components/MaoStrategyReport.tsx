'use client';

import {
  Activity,
  ClipboardCheck,
  Compass,
  Database,
  Gauge,
  ShieldAlert,
  Target,
  UsersRound,
} from 'lucide-react';

interface SourceAudit {
  name: string;
  available: boolean;
  source?: string;
  data_date?: string | null;
  is_realtime?: boolean;
  cache_used?: boolean;
}

interface StrategyFactor {
  id: string;
  name: string;
  score: number | null;
  status: string;
  evidence: string[];
  missing: string[];
  interpretation: string;
}

interface ResearchHypothesis {
  id: string;
  name: string;
  hypothesis: string;
  status: string;
  evidence: string[];
}

export interface MaoStrategyReport {
  generated_at?: string | null;
  scope: { type: 'stock' | 'market'; stock_codes: string[] };
  data_audit: {
    grade: string;
    score: number;
    decision_gate: string;
    data_mode: string;
    is_realtime: boolean;
    data_date?: string | null;
    missing: string[];
    sources: SourceAudit[];
    warning: string;
  };
  main_contradiction: {
    title: string;
    summary: string;
    supporting_evidence: string[];
    counter_evidence: string[];
    falsification: string[];
  };
  camps: Array<{
    key: string;
    label: string;
    stance: string;
    summary: string;
    evidence: string[];
  }>;
  cycle: {
    stage: string;
    label: string;
    score: number | null;
    confidence: number;
    evidence: string[];
  };
  strategy_factors: StrategyFactor[];
  research_hypotheses: ResearchHypothesis[];
  tactics: {
    action: string;
    posture: string;
    total_position_range_pct: [number, number];
    single_position_cap_pct: number;
    absolute_single_position_cap_pct: number;
    time_stop_days?: [number, number] | null;
    entry_conditions: string[];
    retreat_conditions: string[];
    stop_loss: { percent?: number | null; reference: string; rule: string };
    red_lines: string[];
  };
  stock_reports: Array<{
    code: string;
    name: string;
    price?: number | null;
    change_pct?: number | null;
    data_date?: string | null;
    is_realtime: boolean;
    signal_score: number;
    evidence: string[];
    risks: string[];
  }>;
  review: {
    hypothesis: string;
    verification_window: string;
    checkpoints: string[];
    status: string;
  };
  disclaimer: string;
}

function stanceClass(stance: string) {
  if (stance === 'supportive') return 'text-up';
  if (stance === 'pressured') return 'text-down';
  return 'text-text-secondary';
}

function scoreClass(value: number | null) {
  if (value == null) return 'text-text-secondary';
  if (value >= 20) return 'text-up';
  if (value <= -20) return 'text-down';
  return 'text-warn';
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    available: '可计算',
    partial: '部分数据',
    blocked: '数据阻断',
    not_applicable: '本轮不适用',
    candidate: '待验证',
    not_triggered: '未触发',
    blocked_data: '数据阻断',
    awaiting_breadth_confirmation: '等待板块宽度确认',
    active_research: '当前研究',
    regime_not_matched: '阶段不匹配',
    watch_reversal_confirmation: '等待转衰确认',
    portfolio_guardrail: '组合红线',
  };
  return labels[status] || status;
}

export default function MaoStrategyReportView({ report }: { report: MaoStrategyReport }) {
  const audit = report.data_audit;
  const tactics = report.tactics;
  const cycleTone = report.cycle.stage === 'counteroffensive' ? 'text-up' : report.cycle.stage === 'defense' ? 'text-down' : 'text-warn';

  return (
    <div className="overflow-hidden rounded-md border border-border bg-[#11161D] text-sm text-text">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 font-semibold">
            <Compass size={17} className="text-warn" />
            毛选战略研判
          </div>
          <div className="mt-1 text-[11px] text-text-secondary">
            {report.scope.type === 'stock' ? `标的 ${report.scope.stock_codes.join('、')}` : 'A股市场'} · 综合证据日 {audit.data_date || '--'}
          </div>
        </div>
        <div className="text-right">
          <div className={`font-mono text-sm ${audit.grade === '充分' ? 'text-up' : audit.grade === '不足' ? 'text-down' : 'text-warn'}`}>
            证据{audit.grade} {audit.score.toFixed(0)}分
          </div>
          <div className="mt-1 text-[10px] text-text-secondary">
            {audit.data_mode === 'mixed' ? '实时行情 + 缓存证据' : '最近有效缓存/历史'}
          </div>
        </div>
      </header>

      <section className="px-4 py-3">
        <div className="flex items-center gap-2 text-xs font-semibold text-text-secondary">
          <Target size={14} />主要矛盾
        </div>
        <h3 className="mt-1.5 text-base font-semibold">{report.main_contradiction.title}</h3>
        <p className="mt-1 text-xs leading-5 text-text-secondary">{report.main_contradiction.summary}</p>
        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
          <EvidenceColumn label="支持证据" items={report.main_contradiction.supporting_evidence} tone="up" />
          <EvidenceColumn label="反方证据" items={report.main_contradiction.counter_evidence} tone="down" />
        </div>
      </section>

      <section className="border-t border-border px-4 py-3">
        <div className="flex items-center gap-2 text-xs font-semibold text-text-secondary">
          <UsersRound size={14} />阵营与资金博弈
        </div>
        <div className="mt-3 grid grid-cols-1 gap-x-5 gap-y-4 md:grid-cols-2">
          {report.camps.map((camp) => (
            <div key={camp.key} className="border-l-2 border-border pl-3">
              <div className={`text-xs font-medium ${stanceClass(camp.stance)}`}>{camp.label}</div>
              <p className="mt-1 text-[11px] leading-5 text-text-secondary">{camp.summary}</p>
              <p className="mt-1 text-[11px] leading-5 text-text">{camp.evidence.slice(0, 2).join('；')}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="grid grid-cols-1 border-t border-border lg:grid-cols-[0.8fr_1.2fr]">
        <div className="border-b border-border px-4 py-3 lg:border-b-0 lg:border-r">
          <div className="flex items-center gap-2 text-xs font-semibold text-text-secondary">
            <Gauge size={14} />周期状态
          </div>
          <div className={`mt-2 text-base font-semibold ${cycleTone}`}>{report.cycle.label}</div>
          <div className="mt-1 font-mono text-xs text-text-secondary">
            评分 {report.cycle.score == null ? '--' : report.cycle.score.toFixed(1)} · 置信度 {report.cycle.confidence.toFixed(0)}%
          </div>
          <p className="mt-2 text-[11px] leading-5 text-text-secondary">{report.cycle.evidence.slice(0, 3).join('；')}</p>
        </div>
        <div className="px-4 py-3">
          <div className="flex items-center gap-2 text-xs font-semibold text-text-secondary">
            <ShieldAlert size={14} />战术部署
          </div>
          <p className="mt-2 text-xs leading-5">{tactics.posture}</p>
          <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 font-mono text-xs">
            <span>总仓 {tactics.total_position_range_pct[0]}%-{tactics.total_position_range_pct[1]}%</span>
            <span>单票上限 {tactics.single_position_cap_pct}%</span>
            {tactics.stop_loss.percent != null && <span>初始风控 {tactics.stop_loss.percent.toFixed(1)}%</span>}
            {tactics.time_stop_days && <span>时间止损 {tactics.time_stop_days[0]}-{tactics.time_stop_days[1]}日</span>}
          </div>
        </div>
        <div className="grid grid-cols-1 border-t border-border lg:col-span-2 md:grid-cols-3">
          <TacticRules label="入场确认" items={tactics.entry_conditions} />
          <TacticRules label="撤退条件" items={tactics.retreat_conditions} bordered />
          <TacticRules label="风控红线" items={tactics.red_lines} bordered />
        </div>
      </section>

      <section className="border-t border-border px-4 py-3">
        <div className="flex items-center gap-2 text-xs font-semibold text-text-secondary">
          <Activity size={14} />五个斗争因子
        </div>
        <div className="mt-3 divide-y divide-border">
          {report.strategy_factors.map((factor) => (
            <div key={factor.id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 py-2.5 first:pt-0 last:pb-0">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                  <span className="font-medium">{factor.name}</span>
                  <span className="text-[10px] text-text-secondary">{statusLabel(factor.status)}</span>
                </div>
                <p className="mt-1 text-[11px] leading-5 text-text-secondary">{factor.evidence.slice(0, 2).join('；') || factor.interpretation}</p>
                {factor.missing.length > 0 && <p className="mt-1 text-[10px] leading-4 text-warn">待补：{factor.missing.join('、')}</p>}
              </div>
              <div className={`font-mono text-sm ${scoreClass(factor.score)}`}>{factor.score == null ? '--' : `${factor.score >= 0 ? '+' : ''}${factor.score.toFixed(1)}`}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="border-t border-border px-4 py-3">
        <div className="flex items-center gap-2 text-xs font-semibold text-text-secondary">
          <ClipboardCheck size={14} />可证伪研究假设
        </div>
        <div className="mt-3 space-y-3">
          {report.research_hypotheses.map((item) => (
            <div key={item.id} className="grid grid-cols-[32px_minmax(0,1fr)] gap-2 text-xs">
              <span className="font-mono text-accent">{item.id}</span>
              <div>
                <div className="font-medium">{item.name} <span className="ml-1 text-[10px] font-normal text-text-secondary">{statusLabel(item.status)}</span></div>
                <p className="mt-1 text-[11px] leading-5 text-text-secondary">{item.hypothesis}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {report.stock_reports.length > 0 && (
        <section className="border-t border-border px-4 py-3">
          <div className="text-xs font-semibold text-text-secondary">标的实证摘要</div>
          <div className="mt-2 divide-y divide-border">
            {report.stock_reports.map((stock) => (
              <div key={stock.code} className="grid grid-cols-1 gap-1 py-2.5 first:pt-0 last:pb-0 md:grid-cols-[1fr_auto]">
                <div className="min-w-0">
                  <div className="text-xs font-medium">
                    {stock.name}{stock.name !== stock.code && <span className="ml-1 font-mono text-text-secondary">{stock.code}</span>}
                  </div>
                  <p className="mt-1 text-[11px] leading-5 text-text-secondary">{stock.evidence.slice(0, 2).join('；') || '支持证据不足'}</p>
                  {stock.risks.length > 0 && <p className="mt-1 text-[11px] leading-5 text-down">{stock.risks.slice(0, 2).join('；')}</p>}
                </div>
                <div className="font-mono text-xs text-right">
                  <div>{stock.price == null ? '--' : stock.price.toFixed(2)}</div>
                  <div className={scoreClass(stock.signal_score)}>{stock.signal_score >= 0 ? '+' : ''}{stock.signal_score.toFixed(1)}</div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="border-t border-border px-4 py-3">
        <div className="text-xs font-semibold text-text-secondary">闭环复盘</div>
        <p className="mt-2 text-xs leading-5">{report.review.hypothesis}</p>
        <p className="mt-1 text-[11px] text-text-secondary">{report.review.verification_window}</p>
      </section>

      <footer className="border-t border-border px-4 py-2.5 text-[10px] leading-4 text-text-secondary">
        <div className="mb-1 flex flex-wrap gap-x-3 gap-y-1">
          <Database size={11} className="mt-0.5" />
          {audit.sources.filter((source) => source.available).map((source) => (
            <span key={source.name}>{source.name} {source.data_date || ''}{source.is_realtime ? ' 实时' : ''}</span>
          ))}
        </div>
        {report.disclaimer}
      </footer>
    </div>
  );
}

function EvidenceColumn({ label, items, tone }: { label: string; items: string[]; tone: 'up' | 'down' }) {
  return (
    <div className={`border-l-2 pl-3 ${tone === 'up' ? 'border-up' : 'border-down'}`}>
      <div className={`text-[11px] font-medium ${tone === 'up' ? 'text-up' : 'text-down'}`}>{label}</div>
      <p className="mt-1 text-[11px] leading-5 text-text-secondary">{items.slice(0, 3).join('；') || '暂无可核验证据'}</p>
    </div>
  );
}

function TacticRules({ label, items, bordered = false }: { label: string; items: string[]; bordered?: boolean }) {
  return (
    <div className={`px-4 py-3 ${bordered ? 'border-t border-border md:border-l md:border-t-0' : ''}`}>
      <div className="text-[11px] font-medium text-text-secondary">{label}</div>
      <ul className="mt-1.5 space-y-1 text-[10px] leading-4 text-text-secondary">
        {items.map((item) => <li key={item}>· {item}</li>)}
      </ul>
    </div>
  );
}
