import { useMemo, useState } from 'react';
import { ApiError, get, newKey, post, runJob, type Job } from '../api/v1';
import type { CardDeck, DecisionPlan, ListPage, MarketSnapshot, PlanLifecycleEvent, PlanRule, Portfolio, ReviewResult,
  RoundtableCase } from '../api/types';
import { Dialog, Drawer, Empty, ErrorNote, Field, JobProgress, KV, Section, Tag } from '../components/ui';
import { seatName } from '../components/SeatRunCard';
import { useApp, useLoad } from '../lib/hooks';
import { CONDITION_LABEL, ELIGIBILITY_LABEL, LIFECYCLE_LABEL, ORIGIN_LABEL, PURPOSE_LABEL, label } from '../lib/labels';
import { formatDecimal, formatMinor, formatRatioPct, ratioTone, yuanToMinor } from '../lib/money';
import { go, href } from '../lib/route';
import { dateOnly, downloadJson, nowIso, shanghai, shortHash } from '../lib/format';

const REASON: Record<string, string> = {
  draft_not_frozen: '草稿还没冻结',
  purpose_research_not_executable: '研究计划只记录观点，不能据此开模拟仓',
  user_trade_record_is_manual_declaration_not_paper_execution: '手工记录不经模拟执行',
  entry_rules_missing: '缺入场触发条件', entry_rules_missing_: '缺入场触发条件',
  cancel_rules_missing: '缺取消条件', invalidation_missing: '缺防守/失效条件', invalidation_rules_missing: '缺防守/失效条件',
  time_exit_missing: '缺时间退出规则', time_exit_rule_missing: '缺时间退出规则',
  risk_budget_missing: '缺单笔风险预算', risk_budget_minor_missing: '缺单笔风险预算',
  planned_shares_missing: '缺计划股数',
  instrument_not_in_snapshot: '标的不在计划引用的快照里', st_or_unknown_status: 'ST 或 ST 状态未知',
  suspension_status_unknown: '停复牌状态未知', instrument_suspended: '停牌',
  portfolio_binding_missing: '没有绑定模拟组合', portfolio_not_visible: '绑定的组合不可见', portfolio_not_paper: '绑定的不是模拟组合',
  insufficient_cash_at_basis_revision: '按绑定时的组合现金，买不起计划股数（含费用）',
  risk_exceeds_budget: '按防守价算的亏损（含往返费用）超过风险预算', entry_price_unspecified: '入场价没写，算不了资金与风险',
  session_bar_missing: '快照里没有该标的当日日线', snapshot_has_invalid_rows: '快照有被拒收的非法行',
  snapshot_quality_unavailable: '快照不可用', bar_status_partial: '日线盘中未收定', bar_status_unknown: '日线终结状态未确认',
};

export function reasonText(r: string): string {
  if (REASON[r]) return REASON[r];
  if (r.startsWith('snapshot_missing_')) return `快照缺 ${r.slice('snapshot_missing_'.length)}`;
  if (r.startsWith('simulation_not_supported_board:')) return `板块 ${r.split(':')[1]} 的交易制度未核验，v1 不支持模拟`;
  if (r.startsWith('planned_shares_not_multiple_of_')) return `计划股数不是 ${r.split('_').pop()} 的整数倍`;
  return r;
}

// ── 规则编辑 ──
interface RuleDraft { description: string; price: string; origin: string; ref: string; deadline: string }
const emptyRule = (): RuleDraft => ({ description: '', price: '', origin: 'user_rule', ref: '', deadline: '' });

function toRule(d: RuleDraft, kind: string, idx: number, symbol: string | null): PlanRule {
  return { rule_id: `${kind}_${idx + 1}`, description: d.description.trim(), rule_origin: d.origin,
           origin_ref: d.ref.trim() || (d.origin === 'user_rule' ? 'user:me' : ''), source_version: 'v1', rule_version: 'v1',
           kind, symbol: d.price ? symbol : null, price_decimal: d.price.trim() || null, threshold_ratio: null,
           deadline_session_date: d.deadline || null, evidence_ids: [] };
}

function fromRule(r: PlanRule): RuleDraft {
  return { description: r.description, price: r.price_decimal ?? '', origin: r.rule_origin, ref: r.origin_ref,
           deadline: r.deadline_session_date ?? '' };
}

function RuleList({ title, hint, rules, setRules, withPrice, single }: { title: string; hint: string; rules: RuleDraft[];
  setRules: (r: RuleDraft[]) => void; withPrice?: boolean; single?: boolean }) {
  return (
    <fieldset>
      <legend>{title}</legend>
      <p className="note" style={{ marginTop: 0 }}>{hint}</p>
      {rules.map((r, i) => (
        <div key={i} className="form-grid" style={{ marginBottom: 8 }}>
          <Field label="条件"><input type="text" value={r.description}
            onChange={e => setRules(rules.map((x, k) => k === i ? { ...x, description: e.target.value } : x))} /></Field>
          {withPrice ? <Field label="价格（元，可空）" hint="0.01 元一档"><input type="text" inputMode="decimal" value={r.price}
            onChange={e => setRules(rules.map((x, k) => k === i ? { ...x, price: e.target.value } : x))} /></Field> : null}
          <Field label="来源">
            <select value={r.origin} onChange={e => setRules(rules.map((x, k) => k === i ? { ...x, origin: e.target.value } : x))}>
              {Object.entries(ORIGIN_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </Field>
          {r.origin !== 'user_rule' ? <Field label="出处" hint={r.origin === 'author_quote' ? '书名与章节/页码，例如《交易心理分析》第11章'
              : r.origin === 'engineering_policy' ? '决策记录编号，例如 ADR-psy-1' : '笔记位置'}>
            <input type="text" value={r.ref} onChange={e => setRules(rules.map((x, k) => k === i ? { ...x, ref: e.target.value } : x))} />
          </Field> : null}
          {single ? <Field label="截止交易日（可空）"><input type="date" value={r.deadline}
            onChange={e => setRules(rules.map((x, k) => k === i ? { ...x, deadline: e.target.value } : x))} /></Field> : null}
          {!single ? <div style={{ alignSelf: 'end' }}><button className="btn quiet small" onClick={() => setRules(rules.filter((_, k) => k !== i))}>删除这条</button></div> : null}
        </div>
      ))}
      {!single || rules.length === 0 ? <button className="btn secondary small" onClick={() => setRules([...rules, emptyRule()])}>添加一条</button> : null}
    </fieldset>
  );
}

interface PlanForm {
  title: string; hypothesis: string; purpose: string; symbols: string[]; horizon: string; portfolio_id: string;
  planned_shares: string; risk_yuan: string; max_ratio: string; fee_model: string; window_start: string; window_end: string;
  effective_from: string; change_reason: string; entry: RuleDraft[]; exit: RuleDraft[]; cancel: RuleDraft[];
  invalidation: RuleDraft[]; time_exit: RuleDraft[]; exit_deadline: string;
}

function formFromPlan(p: DecisionPlan): PlanForm {
  return { title: p.title, hypothesis: p.hypothesis, purpose: p.purpose, symbols: p.symbols, horizon: p.horizon,
           portfolio_id: p.portfolio_id ?? '', planned_shares: p.planned_shares ? String(p.planned_shares) : '',
           risk_yuan: p.risk_budget_minor ? formatMinor(p.risk_budget_minor).replace(/,/g, '') : '',
           max_ratio: p.max_position_ratio ?? '', fee_model: p.fee_model_version ?? 'legacy-paper-fee-v1',
           window_start: p.evaluation_window.start_session_date, window_end: p.evaluation_window.end_session_date,
           effective_from: nowIso(), change_reason: '', entry: p.entry_rules.map(fromRule), exit: p.exit_rules.map(fromRule),
           cancel: p.cancel_rules.map(fromRule), invalidation: p.invalidation_rules.map(fromRule),
           time_exit: p.time_exit_rule ? [fromRule(p.time_exit_rule)] : [],
           exit_deadline: p.exit_deadline ? p.exit_deadline.slice(0, 10) : '' };
}

async function buildBody(f: PlanForm, kase: RoundtableCase, replayId: string | null) {
  const sym = f.symbols[0] ?? null;
  let rev: number | null = null;
  if (f.purpose === 'simulation' && f.portfolio_id) rev = (await get<Portfolio>(`/api/v1/portfolios/${f.portfolio_id}`)).revision;
  const risk = f.risk_yuan.trim() ? yuanToMinor(f.risk_yuan) : null;
  if (f.risk_yuan.trim() && risk === null) throw new ApiError(422, 'VALIDATION_ERROR', '风险预算格式不对（元，最多两位小数）');
  return {
    change_reason: f.change_reason.trim() || 'initial', effective_from: f.effective_from,
    portfolio_id: f.purpose === 'simulation' ? (f.portfolio_id || null) : null,
    basis_portfolio_revision: f.purpose === 'simulation' ? rev : null,
    case_id: kase.case_id, snapshot_id: kase.snapshot_id, primary_strategy_id: kase.primary_strategy_id, purpose: f.purpose,
    planned_shares: f.planned_shares ? Number(f.planned_shares) : null,
    cancel_rules: f.cancel.filter(r => r.description.trim()).map((r, i) => toRule(r, 'cancel', i, sym)),
    time_exit_rule: f.time_exit.filter(r => r.description.trim()).map((r, i) => toRule(r, 'time_exit', i, sym))[0] ?? null,
    exit_deadline: f.exit_deadline ? `${f.exit_deadline}T07:00:00Z` : null,
    title: f.title.trim(), hypothesis: f.hypothesis.trim(), symbols: f.symbols, horizon: f.horizon,
    entry_rules: f.entry.filter(r => r.description.trim()).map((r, i) => toRule(r, 'entry', i, sym)),
    exit_rules: f.exit.filter(r => r.description.trim()).map((r, i) => toRule(r, 'exit', i, sym)),
    invalidation_rules: f.invalidation.filter(r => r.description.trim()).map((r, i) => toRule(r, 'invalidation', i, sym)),
    risk_budget_minor: risk, max_position_ratio: f.max_ratio.trim() || null, benchmark_id: null,
    evaluation_window: { start_session_date: f.window_start, end_session_date: f.window_end },
    fee_model_version: f.fee_model || null, replay_id: replayId,
  };
}

function PlanEditor({ kase, init, onSubmit, submitLabel, amend }: { kase: RoundtableCase; init: PlanForm;
  onSubmit: (body: any) => Promise<void>; submitLabel: string; amend?: boolean }) {
  const app = useApp();
  const [f, setF] = useState<PlanForm>(init);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof PlanForm, v: any) => setF({ ...f, [k]: v });
  const paper = (app.index?.portfolios ?? []).filter(p => p.mode === 'paper');
  const submit = async () => {
    setBusy(true); setErr(null);
    try { await onSubmit(await buildBody(f, kase, app.session?.mode === 'replay' ? app.session.replay_id : null)); }
    catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const fieldErr = (name: string) => err instanceof ApiError ? err.details.filter(d => d.field.includes(name)).map(d => d.reason).join('；') || null : null;
  return (
    <div className="form">
      <div className="form-grid">
        <Field label="标题"><input type="text" value={f.title} onChange={e => set('title', e.target.value)} /></Field>
        <Field label="用途" hint="研究计划可以不完整，但不能用于开仓">
          <select value={f.purpose} onChange={e => set('purpose', e.target.value)} disabled={amend}>
            {Object.entries(PURPOSE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
        <Field label="持有周期">
          <select value={f.horizon} onChange={e => set('horizon', e.target.value)}>
            <option value="short">短线</option><option value="swing">波段</option><option value="long">长期</option>
          </select>
        </Field>
        <Field label="标的" hint={f.purpose === 'simulation' ? '模拟计划只能一只' : '可多只'}>
          <select multiple={f.purpose !== 'simulation'} value={f.purpose === 'simulation' ? (f.symbols[0] ?? '') : f.symbols}
                  onChange={e => set('symbols', Array.from(e.target.selectedOptions).map(o => o.value))}>
            {f.purpose === 'simulation' ? <option value="">选择一只</option> : null}
            {kase.symbols.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </Field>
      </div>
      <Field label="假设（为什么这样计划）"><textarea value={f.hypothesis} onChange={e => set('hypothesis', e.target.value)} /></Field>
      <p className="note">主方法固定为 Case 的主方法：<strong>{seatName(kase.primary_strategy_id, app.registry?.seats)}</strong>。换主方法请另建 Case。</p>
      <RuleList title="入场触发" hint="满足才考虑入场；达到价格不等于成交" rules={f.entry} setRules={v => set('entry', v)} withPrice />
      <RuleList title="取消条件" hint="还没入场时，什么情况下放弃这笔计划（和失效不是一回事）" rules={f.cancel} setRules={v => set('cancel', v)} withPrice />
      <RuleList title="防守 / 失效" hint="论点失效或亏到哪里就退出；多头计划的防守价必须低于入场价" rules={f.invalidation} setRules={v => set('invalidation', v)} withPrice />
      <RuleList title="其它退出" hint="止盈、减仓等（可空）" rules={f.exit} setRules={v => set('exit', v)} withPrice />
      <RuleList title="时间退出" hint="持有多久没走出预期就离场" rules={f.time_exit} setRules={v => set('time_exit', v)} single />
      {fieldErr('defense_price') || fieldErr('cancel_price') ? <div className="alert">{fieldErr('defense_price') ?? ''} {fieldErr('cancel_price') ?? ''}</div> : null}
      <div className="form-grid">
        {f.purpose === 'simulation' ? <>
          <Field label="绑定模拟组合">
            <select value={f.portfolio_id} onChange={e => set('portfolio_id', e.target.value)}>
              <option value="">选择组合</option>
              {paper.map(p => <option key={p.portfolio_id} value={p.portfolio_id}>{p.name || p.portfolio_id}</option>)}
            </select>
          </Field>
          <Field label="计划股数" hint="主板 100 股一手"><input type="number" min={100} step={100} value={f.planned_shares}
            onChange={e => set('planned_shares', e.target.value)} /></Field>
        </> : null}
        <Field label="单笔风险预算（元）" error={fieldErr('risk_budget')}><input type="text" inputMode="decimal" value={f.risk_yuan}
          onChange={e => set('risk_yuan', e.target.value)} placeholder="例如 500" /></Field>
        <Field label="单票仓位上限（比例，可空）" hint="0.1 表示 10%"><input type="text" value={f.max_ratio} onChange={e => set('max_ratio', e.target.value)} /></Field>
        <Field label="费用模型">
          <select value={f.fee_model} onChange={e => set('fee_model', e.target.value)}>
            <option value="legacy-paper-fee-v1">legacy-paper-fee-v1（软件兼容默认，未核验现行费率）</option>
            <option value="fee_fixture_v1">fee_fixture_v1（合成测试费率）</option>
          </select>
        </Field>
        <Field label="评估窗口开始"><input type="date" value={f.window_start} onChange={e => set('window_start', e.target.value)} /></Field>
        <Field label="评估窗口结束"><input type="date" value={f.window_end} onChange={e => set('window_end', e.target.value)} /></Field>
        <Field label="最迟离场日（可空）"><input type="date" value={f.exit_deadline} onChange={e => set('exit_deadline', e.target.value)} /></Field>
      </div>
      {amend ? (
        <div className="form-grid">
          <Field label="改动原因（必填）" error={fieldErr('change_reason')}><input type="text" value={f.change_reason}
            onChange={e => set('change_reason', e.target.value)} placeholder="例如：出现新的反证" /></Field>
          <Field label="新版本生效时间（UTC）" hint="不能早于上一版的冻结时间" error={fieldErr('effective_from')}>
            <input type="text" value={f.effective_from} onChange={e => set('effective_from', e.target.value)} /></Field>
        </div>
      ) : null}
      <div className="row"><button className="btn" onClick={submit} disabled={busy || !f.title.trim() || !f.symbols.length}>{busy ? '保存中…' : submitLabel}</button></div>
      <ErrorNote error={err} />
    </div>
  );
}

function NewPlan({ caseId }: { caseId: string }) {
  const app = useApp();
  const c = useLoad(() => get<RoundtableCase>(`/api/v1/cases/${caseId}`), [caseId]);
  if (c.error) return <ErrorNote error={c.error} />;
  if (!c.data) return <p className="note">读取 Case…</p>;
  const k = c.data;
  const today = dateOnly(nowIso());
  const init: PlanForm = { title: `${k.title}：计划`, hypothesis: '', purpose: 'research', symbols: k.symbols.slice(0, 1),
    horizon: k.horizon === 'multi' ? 'swing' : k.horizon, portfolio_id: '', planned_shares: '', risk_yuan: '', max_ratio: '',
    fee_model: 'legacy-paper-fee-v1', window_start: today, window_end: today, effective_from: nowIso(), change_reason: '',
    entry: [emptyRule()], exit: [], cancel: [emptyRule()], invalidation: [emptyRule()], time_exit: [emptyRule()], exit_deadline: '' };
  return (
    <>
      <div className="ws-head"><h1>起草事前计划</h1><p>来自 Case「{k.title}」，固定在快照 {k.snapshot_id}。先存草稿，确认后再冻结。</p></div>
      <PlanEditor kase={k} init={init} submitLabel="保存草稿" onSubmit={async body => {
        const { data } = await post<DecisionPlan>('/api/v1/plans', body, newKey('plan-create'));
        await app.refreshIndex();
        go('plans', data.plan_id);
      }} />
    </>
  );
}

function RulesView({ p }: { p: DecisionPlan }) {
  const block = (t: string, rs: PlanRule[]) => (
    <div><h3>{t}</h3>{rs.length ? <ul style={{ paddingLeft: 18, margin: 0, fontSize: 'var(--fs-1)' }}>{rs.map(r => (
      <li key={r.rule_id}>{r.description}{r.price_decimal ? `（${r.price_decimal} 元）` : ''}
        <span className="note">　来源：{label(ORIGIN_LABEL, r.rule_origin)}{r.rule_origin !== 'user_rule' ? ` ${r.origin_ref}` : ''}</span></li>
    ))}</ul> : <p className="unknown" style={{ fontSize: 'var(--fs-1)', margin: 0 }}>未写</p>}</div>
  );
  return (
    <div className="grid-cols">
      {block('入场触发', p.entry_rules)}
      {block('取消条件', p.cancel_rules)}
      {block('防守 / 失效', p.invalidation_rules)}
      {block('其它退出', p.exit_rules)}
      {block('时间退出', p.time_exit_rule ? [p.time_exit_rule] : [])}
      <div><h3>数量与风险</h3>
        <KV items={[['计划股数', p.planned_shares ?? '未写'], ['风险预算', p.risk_budget_minor ? `${formatMinor(p.risk_budget_minor)} 元` : '未写'],
          ['仓位上限', p.max_position_ratio ?? '未写'], ['费用模型', p.fee_model_version ?? '未写'],
          ['最迟离场', p.exit_deadline ? shanghai(p.exit_deadline) : '未写']]} />
      </div>
    </div>
  );
}

const DIFF_FIELDS: [keyof DecisionPlan, string][] = [
  ['title', '标题'], ['hypothesis', '假设'], ['symbols', '标的'], ['entry_rules', '入场'], ['cancel_rules', '取消'],
  ['invalidation_rules', '防守/失效'], ['exit_rules', '其它退出'], ['time_exit_rule', '时间退出'], ['exit_deadline', '最迟离场'],
  ['planned_shares', '股数'], ['risk_budget_minor', '风险预算'], ['max_position_ratio', '仓位上限'], ['evaluation_window', '评估窗口'],
  ['fee_model_version', '费用模型'], ['portfolio_id', '绑定组合'],
];

function ReviewView({ r }: { r: ReviewResult }) {
  const ret = (v: string | null) => v === null ? <span className="unknown">空（{r.review_kind === 'preflight' ? '交易尚未发生' : '无证据'}）</span>
    : <span className={ratioTone(v) === 'up' ? 'rise' : ratioTone(v) === 'down' ? 'fall' : ''}>{formatRatioPct(v)}</span>;
  return (
    <div className="stack" data-testid="review" data-review-kind={r.review_kind}>
      <KV items={[
        ['类型', r.review_kind === 'preflight' ? '事前审查（不判违纪）' : '事后复盘'],
        ['计划版本', `v${r.plan_version}${r.frozen_hash ? `，冻结 ${shortHash(r.frozen_hash)}` : '（草稿）'}`],
        ['此刻可执行性', r.preflight_eligibility ? label(ELIGIBILITY_LABEL, r.preflight_eligibility) : '--'],
        ['方向价格变化', ret(r.directional_price_return_ratio)],
        ['执行收益', ret(r.executed_return_ratio)],
        ['含息总回报', ret(r.total_return_ratio)],
        ['最大回撤', ret(r.max_drawdown_ratio)],
        ['as of 快照', <code>{r.as_of_snapshot_id}</code>],
      ]} />
      <h3>逐条核对（只按事前冻结且已生效的规则）</h3>
      <ul className="conds">{r.rule_adherence.map(c => (
        <li key={c.condition_id}><span className={`res ${c.result}`}>{label(CONDITION_LABEL, c.result)}</span><span>{c.description}</span></li>
      ))}</ul>
      {r.missing_inputs.length ? <p className="unknown" style={{ fontSize: 'var(--fs-1)' }}>缺：{r.missing_inputs.join('、')}</p> : null}
      <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>{r.conclusions.map((c, i) => <li key={i}>{c}</li>)}</ul>
      <p className="note">纪律与盈亏分开看：守规亏损仍是守规，违规盈利仍是违规。</p>
    </div>
  );
}

/** 计划依据的快照与最新快照的价格对比：有更新的事实包时提示，但不会改动计划。 */
function SnapshotDiff({ p }: { p: DecisionPlan }) {
  const app = useApp();
  const latest = app.snapshot;
  const base = useLoad(() => latest && latest.snapshot_id !== p.snapshot_id ? get<MarketSnapshot>(`/api/v1/snapshots/${p.snapshot_id}`)
                                                                            : Promise.resolve(null), [p.snapshot_id, latest?.snapshot_id]);
  if (!latest || latest.snapshot_id === p.snapshot_id || !base.data) return null;
  const rows = p.symbols.map(sym => ({ sym, was: base.data!.instruments.find(i => i.symbol === sym)?.last_price_decimal ?? null,
                                       now: latest.instruments.find(i => i.symbol === sym)?.last_price_decimal ?? null }));
  return (
    <div className="alert info" data-testid="snapshot-diff">
      有较新的事实包（{latest.metadata.quote_date}）。计划仍按冻结时的快照 {base.data.metadata.quote_date}：
      {rows.map(r => <span key={r.sym}>　{r.sym} {formatDecimal(r.was)} → {formatDecimal(r.now)}</span>)}。
      要改价位请生成新版本并写原因。
    </div>
  );
}

function PlanDetail({ id, sub }: { id: string; sub: string | null }) {
  const app = useApp();
  const latest = useLoad(() => get<DecisionPlan>(`/api/v1/plans/${id}`), [id]);
  const n = latest.data?.version ?? 0;
  const versions = useLoad(async () => {
    const out: DecisionPlan[] = [];
    for (let v = 1; v <= n; v++) out.push(await get<DecisionPlan>(`/api/v1/plans/${id}/versions/${v}`));
    return out;
  }, [id, n]);
  const events = useLoad(() => get<ListPage<PlanLifecycleEvent>>(`/api/v1/plans/${id}/events`), [id, n]);
  const kase = useLoad(() => latest.data ? get<RoundtableCase>(`/api/v1/cases/${latest.data.case_id}`) : Promise.resolve(null),
                       [latest.data?.case_id]);
  const [mode, setMode] = useState<null | 'freeze' | 'amend' | 'event'>(null);
  const [review, setReview] = useState<ReviewResult | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [evType, setEvType] = useState('activated');
  const [evReason, setEvReason] = useState('');
  const [evJournal, setEvJournal] = useState('');
  const sel = useMemo(() => {
    const vs = versions.data ?? [];
    const want = sub ? Number(sub) : n;
    return vs.find(v => v.version === want) ?? latest.data;
  }, [versions.data, sub, n, latest.data]);
  if (latest.error) return <ErrorNote error={latest.error} title="读取计划失败" />;
  if (!latest.data || !sel) return <p className="note">读取中…</p>;
  const p = sel;
  const prev = (versions.data ?? []).find(v => v.version === p.version - 1);
  const changed = prev ? DIFF_FIELDS.filter(([k]) => JSON.stringify(prev[k]) !== JSON.stringify(p[k])) : [];
  const isLatest = p.version === n;
  const reviews = (app.index?.reviews ?? []).filter((r: any) => r.plan_id === id);
  const evs = (events.data?.items ?? []).filter(e => e.plan_version === p.version);
  const doFreeze = async () => {
    setBusy(true); setErr(null);
    try {
      await post<DecisionPlan>(`/api/v1/plans/${id}/freeze`, { expected_version: p.version }, newKey('plan-freeze'));
      setMode(null); await latest.reload(); await versions.reload(); await app.refreshIndex();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const doReview = async (kind: 'preflight' | 'post_trade') => {
    setBusy(true); setErr(null); setReview(null);
    try {
      const snapId = kind === 'preflight' ? (app.snapshot?.snapshot_id ?? p.snapshot_id) : (app.snapshot?.snapshot_id ?? p.snapshot_id);
      const j = await runJob('/api/v1/reviews', { plan_id: id, plan_version: p.version, frozen_hash: p.frozen_hash, review_kind: kind,
                                                  as_of_snapshot_id: snapId }, newKey(`review-${kind}`), setJob);
      setReview(await get<ReviewResult>(`/api/v1/reviews/${j.resource_id}`));
      await app.refreshIndex();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const doDeck = async () => {
    setErr(null);
    try {
      const { data } = await post<CardDeck>('/api/v1/decks', { source_type: 'plan', source_id: id, source_version: String(p.version),
                                                                title: `计划要件：${p.title}` }, newKey('deck-plan'));
      await app.refreshIndex(); go('seats', data.deck_id);
    } catch (e) { setErr(e); }
  };
  const doEvent = async () => {
    setBusy(true); setErr(null);
    try {
      const last = evs.length ? evs[evs.length - 1].event_id : null;
      await post(`/api/v1/plans/${id}/events`, { plan_version: p.version, frozen_hash: p.frozen_hash, event_type: evType,
        effective_at: nowIso(), snapshot_id: null, condition_ids: [], journal_event_id: evJournal || null, reason: evReason,
        expected_previous_event_id: last }, newKey('plan-event'));
      setMode(null); setEvReason(''); setEvJournal(''); await events.reload();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (
    <div className="stack" data-testid="plan-detail" data-plan-id={id} data-version={p.version} data-status={p.status}>
      <div className="ws-head">
        <h1>{p.title}</h1>
        <p>{p.hypothesis || '（未写假设）'}</p>
      </div>
      <div className="row" role="tablist" aria-label="版本">
        {(versions.data ?? []).map(v => (
          <a key={v.version} className={`btn small ${v.version === p.version ? '' : 'secondary'}`} href={href('plans', id, String(v.version))}
             role="tab" aria-selected={v.version === p.version}>v{v.version}{v.status === 'frozen' ? '（已冻结）' : '（草稿）'}</a>
        ))}
      </div>
      <div className={`panel${p.status === 'frozen' ? ' sealed' : ''}`} data-testid="plan-seal">
        <div className="row spread">
          <div className="row">
            <Tag tone={p.status === 'frozen' ? 'seal' : 'info'}>{p.status === 'frozen' ? '已冻结' : '草稿'}</Tag>
            <Tag tone={p.execution_eligibility === 'eligible' ? 'ok' : p.execution_eligibility === 'ineligible' ? 'bad' : 'warn'}
                 testid="eligibility">{label(ELIGIBILITY_LABEL, p.execution_eligibility)}</Tag>
            <span className="note">{label(PURPOSE_LABEL, p.purpose)}；主方法 {seatName(p.primary_strategy_id, app.registry?.seats)}</span>
          </div>
          {p.status === 'frozen' ? <div className="seal-line"><span>冻结于 {shanghai(p.frozen_at)}</span><span>哈希 <code>{shortHash(p.frozen_hash)}</code></span></div> : null}
        </div>
        {p.execution_eligibility_reasons.length ? (
          <ul style={{ paddingLeft: 18, margin: '8px 0 0', fontSize: 'var(--fs-1)' }} data-testid="eligibility-reasons">
            {p.execution_eligibility_reasons.map(r => <li key={r} data-reason={r}>{reasonText(r)}</li>)}
          </ul>
        ) : null}
        <p className="note">标的 {p.symbols.join('、')}；快照 <code>{p.snapshot_id}</code>；生效于 {shanghai(p.effective_from)}；
          改动原因：{p.change_reason}{kase.data ? <>；来自 <a href={href('cases', kase.data.case_id)}>{kase.data.title}</a></> : null}</p>
      </div>
      {changed.length ? <div className="alert info">与 v{p.version - 1} 相比改动了：{changed.map(([, t]) => t).join('、')}</div> : null}
      <SnapshotDiff p={p} />
      <Section title="计划内容"><RulesView p={p} /></Section>
      <Section title="操作" hint={isLatest ? undefined : '这不是最新版本：只读'}>
        <div className="row">
          {p.status === 'draft' && isLatest ? <button className="btn seal" onClick={() => setMode('freeze')}>冻结事前计划</button> : null}
          {isLatest && kase.data ? <button className="btn secondary" onClick={() => setMode('amend')}>修改（生成新版本）</button> : null}
          <button className="btn secondary" disabled={busy} onClick={() => doReview('preflight')}>事前审查（此刻还能不能执行）</button>
          <button className="btn secondary" disabled={busy || p.status !== 'frozen'} onClick={() => doReview('post_trade')}>事后复盘</button>
          {p.status === 'frozen' ? <button className="btn secondary" onClick={() => setMode('event')}>记录计划事件</button> : null}
          <button className="btn quiet" onClick={doDeck}>生成复习卡组</button>
          <button className="btn quiet" onClick={() => downloadJson(`${id}-v${p.version}.json`, { versions: versions.data, events: events.data?.items })}>导出 JSON</button>
        </div>
        <JobProgress job={busy ? job : null} />
        <ErrorNote error={err} />
        {review ? <div className="panel" style={{ marginTop: 10 }}><ReviewView r={review} /></div> : null}
      </Section>
      <Section title="计划事件" hint="只追加：触发证据、取消、过期、未成交、成交">
        {evs.length ? <ul className="timeline">{evs.map(e => (
          <li key={e.event_id}><strong>{label(LIFECYCLE_LABEL, e.event_type)}</strong> {shanghai(e.effective_at)}：{e.reason}
            {e.journal_event_id ? <span className="note">（成交事件 {e.journal_event_id}）</span> : null}</li>
        ))}</ul> : <p className="note">还没有事件。达到价格不等于成交；成交要在模拟仓里录入并引用。</p>}
      </Section>
      {reviews.length ? (
        <Section title="历次审查与复盘">
          <ul className="timeline">{reviews.map((r: any) => (
            <li key={r.review_id}><button className="btn quiet small" onClick={async () => setReview(await get<ReviewResult>(`/api/v1/reviews/${r.review_id}`))}>
              {r.review_kind === 'preflight' ? '事前审查' : '事后复盘'} v{r.plan_version}</button> {shanghai(r.created_at)}</li>
          ))}</ul>
        </Section>
      ) : null}
      {mode === 'freeze' ? (
        <Dialog title="冻结前确认" onClose={() => setMode(null)}>
          <p>冻结后这个版本不能再改；以后改动只能生成新版本并写明原因。冻结不会自动获得执行资格——资格由服务端按快照、组合与交易规则判定。</p>
          <RulesView p={p} />
          <p className="note">数据时点：快照 {p.snapshot_id}；评估窗口 {p.evaluation_window.start_session_date} 至 {p.evaluation_window.end_session_date}。</p>
          <div className="row"><button className="btn seal" disabled={busy} onClick={doFreeze}>确认冻结 v{p.version}</button>
            <button className="btn secondary" onClick={() => setMode(null)}>再想想</button></div>
          <ErrorNote error={err} />
        </Dialog>
      ) : null}
      {mode === 'amend' && kase.data ? (
        <Drawer title={`修改：生成 v${n + 1}`} onClose={() => setMode(null)}>
          <PlanEditor kase={kase.data} init={formFromPlan(latest.data)} amend submitLabel="保存为新版本" onSubmit={async body => {
            await post<DecisionPlan>(`/api/v1/plans/${id}/versions`, { base_version: n, content: body }, newKey('plan-version'));
            setMode(null); await latest.reload(); await app.refreshIndex();
          }} />
        </Drawer>
      ) : null}
      {mode === 'event' ? (
        <Dialog title="记录计划事件" onClose={() => setMode(null)} dirty={!!evReason}>
          <div className="form">
            <Field label="事件">
              <select value={evType} onChange={e => setEvType(e.target.value)}>
                {Object.entries(LIFECYCLE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </Field>
            <Field label="原因 / 证据"><textarea value={evReason} onChange={e => setEvReason(e.target.value)} /></Field>
            {evType === 'execution_filled' ? <Field label="引用的成交事件 ID" hint="成交必须引用模拟仓里真实存在的成交">
              <input type="text" value={evJournal} onChange={e => setEvJournal(e.target.value)} /></Field> : null}
            <div className="row"><button className="btn" disabled={busy || !evReason.trim()} onClick={doEvent}>追加事件</button></div>
            <ErrorNote error={err} />
          </div>
        </Dialog>
      ) : null}
    </div>
  );
}

export function PlansPage({ id, sub }: { id: string | null; sub: string | null }) {
  const app = useApp();
  if (id === 'new' && sub) return <NewPlan caseId={sub} />;
  if (id && id !== 'new') return <PlanDetail id={id} sub={sub} />;
  const plans = app.index?.plans ?? [];
  return (
    <>
      <div className="ws-head"><h1>事前计划</h1><p>先写下触发、取消、防守和时间退出，再冻结。冻结后只能追加新版本。</p></div>
      {plans.length ? (
        <div className="table-wrap">
          <table data-testid="plan-list">
            <thead><tr><th>标题</th><th>版本</th><th>状态</th><th>用途</th><th>执行资格</th><th>标的</th><th>冻结于</th></tr></thead>
            <tbody>{plans.map(p => (
              <tr key={p.plan_id}>
                <td><a href={href('plans', p.plan_id)}>{p.title}</a></td><td className="num">v{p.version}</td>
                <td>{p.status === 'frozen' ? <Tag tone="seal">已冻结</Tag> : <Tag tone="info">草稿</Tag>}</td>
                <td>{label(PURPOSE_LABEL, p.purpose)}</td>
                <td>{label(ELIGIBILITY_LABEL, p.execution_eligibility)}</td><td>{p.symbols.join('、')}</td>
                <td className="num">{p.frozen_at ? shanghai(p.frozen_at) : '--'}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : <Empty title="还没有计划">在一个 Case 里点「由此起草计划」。选择不交易也是一个完整的结果。</Empty>}
    </>
  );
}

export { ReviewView };
