'use client';

import { useEffect, useMemo, useState } from 'react';
import { Check, CopyPlus, Eye, Plus, Save, Trash2 } from 'lucide-react';
import type { PositionConfig, RuleGroup, RuleMeta, RuleOperator, SectorOption, Strategy, StrategyDraft, StrategyRule } from '../types';

const operatorLabels: Record<RuleOperator, string> = {
  gt: '大于', gte: '大于等于', lt: '小于', lte: '小于等于', eq: '等于', ne: '不等于',
  in: '属于', not_in: '不属于', between: '介于',
};

export const emptyStrategyDraft = (): StrategyDraft => ({
  name: '新建量化策略',
  active: true,
  scan_schedule: 'daily',
  filter: { logic: 'AND', rules: [] },
  entry: { logic: 'AND', rules: [] },
  exit: { stop_loss_pct: 5, take_profit_pct: 15, max_holding_days: 20, rules: [] },
  position: { method: 'equal_weight', max_holdings: 5, max_position_pct: 20, fixed_amount: null },
});

function toDraft(strategy?: Strategy | StrategyDraft | null): StrategyDraft {
  if (!strategy) return emptyStrategyDraft();
  const { id: _id, created_at: _createdAt, updated_at: _updatedAt, description: _description, ...rest } = strategy as Strategy & { description?: string };
  return JSON.parse(JSON.stringify(rest)) as StrategyDraft;
}

function newRule(rules: RuleMeta[]): StrategyRule {
  const meta = rules[0];
  return { type: meta?.type || 'change_pct', operator: (meta?.operators?.[0] || 'gte') as RuleOperator, value: meta?.default ?? 0 };
}

function RuleValue({ rule, meta, sectors, onChange }: {
  rule: StrategyRule;
  meta?: RuleMeta;
  sectors: SectorOption[];
  onChange: (value: StrategyRule['value']) => void;
}) {
  if (!meta) return null;
  if (meta.value_type === 'multi-select') {
    const selected = new Set(Array.isArray(rule.value) ? rule.value.map(String) : []);
    return (
      <select
        multiple
        value={Array.from(selected)}
        onChange={(event) => onChange(Array.from(event.target.selectedOptions).map((option) => option.value))}
        className="min-h-20 w-full bg-bg border border-border rounded-md px-2 py-1.5 text-xs text-text focus:outline-none focus:border-accent"
        aria-label={`${meta.label}板块`}
      >
        {sectors.map((sector) => <option key={sector.code} value={sector.code}>{sector.name}</option>)}
      </select>
    );
  }
  if (meta.value_type === 'boolean') {
    return (
      <select value={String(Boolean(rule.value))} onChange={(event) => onChange(event.target.value === 'true')} className="w-full bg-bg border border-border rounded-md px-2 py-1.5 text-xs text-text">
        <option value="true">是</option><option value="false">否</option>
      </select>
    );
  }
  if (meta.value_type === 'select') {
    return (
      <select value={String(rule.value)} onChange={(event) => onChange(meta.options?.find((item) => String(item) === event.target.value) ?? event.target.value)} className="w-full bg-bg border border-border rounded-md px-2 py-1.5 text-xs text-text">
        {(meta.options || []).map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}
      </select>
    );
  }
  if (rule.operator === 'between') {
    const values = Array.isArray(rule.value) ? rule.value : [0, 0];
    return (
      <div className="grid grid-cols-2 gap-1.5">
        {[0, 1].map((index) => <input key={index} type="number" value={Number(values[index] ?? 0)} onChange={(event) => {
          const next = [...values]; next[index] = Number(event.target.value); onChange(next as number[]);
        }} className="min-w-0 bg-bg border border-border rounded-md px-2 py-1.5 text-xs text-text" />)}
      </div>
    );
  }
  return <input type="number" value={Number(rule.value ?? 0)} onChange={(event) => onChange(Number(event.target.value))} className="w-full bg-bg border border-border rounded-md px-2 py-1.5 text-xs text-text" />;
}

function RuleGroupEditor({ title, group, rules, sectors, onChange, allowEmpty = true, logicLocked = false }: {
  title: string;
  group: RuleGroup;
  rules: RuleMeta[];
  sectors: SectorOption[];
  onChange: (group: RuleGroup) => void;
  allowEmpty?: boolean;
  logicLocked?: boolean;
}) {
  const metaByType = useMemo(() => new Map(rules.map((item) => [item.type, item])), [rules]);
  const updateRule = (index: number, patch: Partial<StrategyRule>) => {
    const next = group.rules.map((rule, ruleIndex) => ruleIndex === index ? { ...rule, ...patch } : rule);
    onChange({ ...group, rules: next });
  };
  return (
    <section className="border border-border rounded-md overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 bg-[#161B22] border-b border-border">
        <div className="text-sm font-semibold text-text">{title}</div>
        {logicLocked
          ? <span className="px-2.5 py-1 text-xs border border-border rounded-md text-text-secondary">OR</span>
          : <div className="inline-flex border border-border rounded-md overflow-hidden" aria-label={`${title}逻辑`}>
              {(['AND', 'OR'] as const).map((logic) => <button key={logic} type="button" onClick={() => onChange({ ...group, logic })} className={`px-2.5 py-1 text-xs ${group.logic === logic ? 'bg-accent text-white' : 'text-text-secondary hover:bg-[#21262D]'}`}>{logic}</button>)}
            </div>}
      </div>
      <div className="p-3 space-y-2">
        {group.rules.map((rule, index) => {
          const meta = metaByType.get(rule.type);
          return (
            <div key={`${rule.type}-${index}`} className="grid grid-cols-1 sm:grid-cols-[minmax(120px,1.1fr)_minmax(110px,.8fr)_minmax(150px,1fr)_32px] gap-2 items-start bg-bg/60 border border-border/70 rounded-md p-2">
              <select value={rule.type} onChange={(event) => {
                const nextMeta = metaByType.get(event.target.value);
                updateRule(index, { type: event.target.value, operator: (nextMeta?.operators[0] || 'gte') as RuleOperator, value: nextMeta?.default ?? 0 });
              }} className="min-w-0 bg-card border border-border rounded-md px-2 py-1.5 text-xs text-text">
                {rules.map((option) => <option key={option.type} value={option.type}>{option.label}</option>)}
              </select>
              <select value={rule.operator} onChange={(event) => updateRule(index, { operator: event.target.value as RuleOperator })} className="min-w-0 bg-card border border-border rounded-md px-2 py-1.5 text-xs text-text">
                {(meta?.operators || []).map((operator) => <option key={operator} value={operator}>{operatorLabels[operator]}</option>)}
              </select>
              <div className="min-w-0"><RuleValue rule={rule} meta={meta} sectors={sectors} onChange={(value) => updateRule(index, { value })} /></div>
              <button type="button" onClick={() => onChange({ ...group, rules: group.rules.filter((_, ruleIndex) => ruleIndex !== index) })} className="h-7 w-7 inline-flex items-center justify-center text-text-secondary hover:text-down hover:bg-[#EF535022] rounded-md" title="删除规则" aria-label="删除规则"><Trash2 size={14} /></button>
            </div>
          );
        })}
        {!group.rules.length && <div className="text-xs text-text-secondary py-2">{allowEmpty ? '不设置则不限制该条件。' : '至少添加一条买入规则。'}</div>}
        <button type="button" onClick={() => onChange({ ...group, rules: [...group.rules, newRule(rules)] })} className="inline-flex items-center gap-1 text-xs text-accent hover:text-white px-1 py-1" disabled={!rules.length}><Plus size={14} />添加规则</button>
      </div>
    </section>
  );
}

export default function StrategyBuilder({ strategy, templates, rules, sectors, onSave, onPreview, onBacktest, saving }: {
  strategy: Strategy | null;
  templates: Array<StrategyDraft & { id?: string; description?: string }>;
  rules: RuleMeta[];
  sectors: SectorOption[];
  onSave: (draft: StrategyDraft, strategyId?: string) => Promise<void>;
  onPreview: (draft: StrategyDraft) => Promise<{ count: number; warning?: string | null }>;
  onBacktest: (strategyId: string) => void;
  saving: boolean;
}) {
  const [draft, setDraft] = useState<StrategyDraft>(() => toDraft(strategy));
  const [preview, setPreview] = useState<{ count: number; warning?: string | null } | null>(null);
  const [previewing, setPreviewing] = useState(false);

  useEffect(() => { setDraft(toDraft(strategy)); setPreview(null); }, [strategy]);
  const setGroup = (key: 'filter' | 'entry', value: RuleGroup) => setDraft((current) => ({ ...current, [key]: value }));
  const valid = Boolean(draft.name.trim() && draft.entry.rules.length);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
        <div>
          <h2 className="text-base font-bold text-text">{strategy ? '编辑策略' : '创建策略'}</h2>
          <p className="text-xs text-text-secondary mt-1">规则在收盘后计算，信号仅供人工确认，不会自动下单。</p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={async () => { setPreviewing(true); try { setPreview(await onPreview(draft)); } finally { setPreviewing(false); } }} disabled={!valid || previewing} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs border border-border text-text-secondary rounded-md hover:border-accent hover:text-text disabled:opacity-50"><Eye size={14} />{previewing ? '预览中' : '实时预览'}</button>
          <button type="button" onClick={() => onSave(draft, strategy?.id)} disabled={!valid || saving} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs bg-accent text-white rounded-md hover:brightness-110 disabled:opacity-50"><Save size={14} />{saving ? '保存中' : '保存策略'}</button>
          {strategy && <button type="button" onClick={() => onBacktest(strategy.id)} className="inline-flex items-center gap-1.5 px-3 py-2 text-xs border border-up/50 text-up rounded-md hover:bg-[#26A69A22]"><Check size={14} />回测</button>}
        </div>
      </div>

      {templates.length > 0 && <div className="flex flex-wrap gap-2">
        {templates.map((template) => <button type="button" key={template.id || template.name} onClick={() => { setDraft(toDraft(template)); setPreview(null); }} className="inline-flex items-center gap-1.5 px-2.5 py-1.5 border border-border rounded-md text-xs text-text-secondary hover:border-accent hover:text-text" title={template.description}><CopyPlus size={13} />{template.name}</button>)}
      </div>}

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.55fr)_minmax(260px,.65fr)] gap-4">
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto_auto] gap-3 border border-border rounded-md p-3">
            <label className="min-w-0"><span className="block text-xs text-text-secondary mb-1">策略名称</span><input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} maxLength={80} className="w-full bg-bg border border-border rounded-md px-2.5 py-2 text-sm text-text focus:outline-none focus:border-accent" /></label>
            <label className="text-xs text-text-secondary flex items-end gap-2 pb-2 cursor-pointer"><input type="checkbox" checked={draft.active} onChange={(event) => setDraft({ ...draft, active: event.target.checked })} className="accent-[#58A6FF]" />启用扫描</label>
            <label className="text-xs text-text-secondary"><span className="block mb-1">扫描方式</span><select value={draft.scan_schedule} onChange={(event) => setDraft({ ...draft, scan_schedule: event.target.value as 'daily' | 'manual' })} className="bg-bg border border-border rounded-md px-2 py-1.5 text-xs text-text"><option value="daily">盘中定时</option><option value="manual">仅手动</option></select></label>
          </div>
          <RuleGroupEditor title="选股条件" group={draft.filter} rules={rules} sectors={sectors} onChange={(value) => setGroup('filter', value)} />
          <RuleGroupEditor title="买入信号" group={draft.entry} rules={rules} sectors={sectors} onChange={(value) => setGroup('entry', value)} allowEmpty={false} />
          <RuleGroupEditor title="自定义离场（任一触发）" group={{ logic: 'OR', rules: draft.exit.rules }} rules={rules} sectors={sectors} onChange={(value) => setDraft({ ...draft, exit: { ...draft.exit, rules: value.rules } })} logicLocked />
        </div>

        <aside className="space-y-4">
          <section className="border border-border rounded-md p-3">
            <h3 className="text-sm font-semibold text-text mb-3">离场规则</h3>
            <div className="space-y-3">
              <label className="block text-xs text-text-secondary">止损幅度 %<input type="number" min="0.1" max="30" value={draft.exit.stop_loss_pct} onChange={(event) => setDraft({ ...draft, exit: { ...draft.exit, stop_loss_pct: Number(event.target.value) } })} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-1.5 text-text" /></label>
              <label className="block text-xs text-text-secondary">止盈幅度 %<input type="number" min="0.1" max="100" value={draft.exit.take_profit_pct} onChange={(event) => setDraft({ ...draft, exit: { ...draft.exit, take_profit_pct: Number(event.target.value) } })} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-1.5 text-text" /></label>
              <label className="block text-xs text-text-secondary">最长持有交易日<input type="number" min="1" max="250" value={draft.exit.max_holding_days} onChange={(event) => setDraft({ ...draft, exit: { ...draft.exit, max_holding_days: Number(event.target.value) } })} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-1.5 text-text" /></label>
            </div>
          </section>
          <section className="border border-border rounded-md p-3">
            <h3 className="text-sm font-semibold text-text mb-3">仓位控制</h3>
            <div className="space-y-3">
              <label className="block text-xs text-text-secondary">分配方式<select value={draft.position.method} onChange={(event) => setDraft({ ...draft, position: { ...draft.position, method: event.target.value as PositionConfig['method'] } })} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-1.5 text-text"><option value="equal_weight">等权分配</option><option value="kelly">保守 Kelly</option><option value="fixed_amount">固定金额</option></select></label>
              <label className="block text-xs text-text-secondary">最大持仓数<input type="number" min="1" max="50" value={draft.position.max_holdings} onChange={(event) => setDraft({ ...draft, position: { ...draft.position, max_holdings: Number(event.target.value) } })} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-1.5 text-text" /></label>
              <label className="block text-xs text-text-secondary">单股上限 %<input type="number" min="1" max="100" value={draft.position.max_position_pct} onChange={(event) => setDraft({ ...draft, position: { ...draft.position, max_position_pct: Number(event.target.value) } })} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-1.5 text-text" /></label>
              {draft.position.method === 'fixed_amount' && <label className="block text-xs text-text-secondary">每笔金额<input type="number" min="1000" value={draft.position.fixed_amount || 10000} onChange={(event) => setDraft({ ...draft, position: { ...draft.position, fixed_amount: Number(event.target.value) } })} className="mt-1 w-full bg-bg border border-border rounded-md px-2 py-1.5 text-text" /></label>}
            </div>
          </section>
        </aside>
      </div>
      {preview && <div className="border border-accent/50 bg-[#1F6FEB22] rounded-md px-3 py-2 text-xs text-text flex flex-wrap items-center gap-x-4 gap-y-1"><span className="text-accent font-semibold">当前规则匹配 {preview.count} 只股票</span>{preview.warning && <span className="text-warn">{preview.warning}</span>}</div>}
    </div>
  );
}
