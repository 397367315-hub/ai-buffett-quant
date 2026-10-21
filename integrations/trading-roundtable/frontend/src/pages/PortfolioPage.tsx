import { useEffect, useMemo, useState } from 'react';
import { ApiError, get, newKey, post } from '../api/v1';
import type { DecisionPlan, JournalEvent, ListPage, Portfolio, PortfolioControlEvent } from '../api/types';
import { Dialog, Empty, ErrorNote, Field, KV, Section, Tag } from '../components/ui';
import { useApp, useLoad, useLocalState } from '../lib/hooks';
import { EVENT_LABEL, ORIGIN_LABEL, label } from '../lib/labels';
import { addMinor, formatDecimal, formatMinor, formatRatioPct, ratioTone, yuanToMinor } from '../lib/money';
import { go, href } from '../lib/route';
import { canonicalJson, downloadJson, nowIso, sha256Hex, shanghai } from '../lib/format';
import { assetUrl } from '../lib/assets';

interface PsyRule { rule_id: string; rule_origin: string; origin_ref?: string; source_ref?: string; text?: string;
  check?: 'must_yes' | 'warn_if_no' | 'info' }

const FEELINGS = ['平静', '犹豫', '焦虑', '兴奋', '后悔', '想追回亏损', '怕错过'];

function noteBody(p: Portfolio, note: string, extra: Partial<Record<string, unknown>> = {}) {
  return { portfolio_id: p.portfolio_id, event_type: 'note', mode: p.mode === 'paper' ? 'paper' : 'user_reported',
           effective_at: nowIso(), symbol: null, side: null, shares: null, price_decimal: null, net_cash_delta_minor: null,
           snapshot_id: null, plan_id: null, plan_version: null, corrects_event_id: null, correction_action: null,
           corrected_event_type: null, note, replay_id: p.replay_id, expected_portfolio_revision: p.revision, evidence_ids: [],
           reported_fees: null, ...extra };
}

function parseDiary(note: string): Record<string, any> | null {
  if (!note.trim().startsWith('{')) return null;
  try { const d = JSON.parse(note); return d && d.schema === 'diary_v1' ? d : null; } catch { return null; }
}

function Checklist({ onState }: { onState: (ok: boolean, warns: string[], rules: PsyRule[]) => void }) {
  const [rules, setRules] = useState<PsyRule[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [own, setOwn] = useLocalState<PsyRule[]>('rt.user_rules.v1', []);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [draft, setDraft] = useState('');
  useEffect(() => {
    fetch(assetUrl('psychology_rules.v1.json'), { cache: 'no-store' }).then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(d => setRules(d.rules as PsyRule[])).catch(e => setErr(String(e)));
  }, []);
  const all = useMemo(() => [...(rules ?? []), ...own], [rules, own]);
  useEffect(() => {
    const must = all.filter(r => r.check === 'must_yes');
    const ok = must.every(r => checked[r.rule_id]);
    const warns = all.filter(r => r.check === 'warn_if_no' && !checked[r.rule_id]).map(r => r.rule_id);
    onState(ok, warns, all);
  }, [all, checked]); // eslint-disable-line react-hooks/exhaustive-deps
  if (err) return <div className="alert">自检规则读取失败（{err}）：不能下单前自检，买入按钮保持不可用。</div>;
  if (!rules) return <p className="note">读取自检规则…</p>;
  return (
    <fieldset data-testid="psychology-checklist">
      <legend>下单前自检</legend>
      <p className="note" style={{ marginTop: 0 }}>来源分开标注：原书原文、编者提炼、系统工程规则、你自己的规则。自检只记录你的回答，不做心理诊断。</p>
      {all.map(r => (
        <div key={r.rule_id} className="check" data-rule-id={r.rule_id} data-origin={r.rule_origin}>
          {r.check === 'info' ? <span style={{ width: 13 }} /> :
            <input type="checkbox" id={`psy-${r.rule_id}`} checked={!!checked[r.rule_id]}
                   onChange={e => setChecked({ ...checked, [r.rule_id]: e.target.checked })} />}
          <label htmlFor={`psy-${r.rule_id}`}>
            <span className={`tag ${r.rule_origin === 'author_quote' ? 'seal' : r.rule_origin === 'user_rule' ? 'ok' : 'muted'}`}
                  data-testid="origin-badge">{label(ORIGIN_LABEL, r.rule_origin)}</span>{' '}
            {r.text ?? r.rule_id}
            {r.rule_origin === 'author_quote' || r.rule_origin === 'engineering_policy' || r.rule_origin === 'editorial_summary'
              ? <span className="note">　出处：{r.origin_ref ?? r.source_ref ?? '未登记'}</span> : null}
            {r.check === 'must_yes' ? <span className="note">　（必须确认）</span> : r.check === 'warn_if_no' ? <span className="note">　（不确认会记一条提醒）</span> : null}
          </label>
        </div>
      ))}
      <div className="row" style={{ marginTop: 6 }}>
        <input type="text" aria-label="添加我自己的规则" value={draft} onChange={e => setDraft(e.target.value)} placeholder="添加我自己的规则，例如：亏损后当天不再开新仓" />
        <button className="btn secondary small" disabled={!draft.trim()} onClick={() => {
          setOwn([...own, { rule_id: `user_${Date.now()}`, rule_origin: 'user_rule', origin_ref: 'user:me', text: draft.trim(), check: 'must_yes' }]);
          setDraft('');
        }}>添加</button>
      </div>
    </fieldset>
  );
}

function FillDialog({ p, onDone, onClose }: { p: Portfolio; onDone: () => void; onClose: () => void }) {
  const app = useApp();
  const snap = app.snapshot;
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [planKey, setPlanKey] = useState('');
  const [symbol, setSymbol] = useState('');
  const [shares, setShares] = useState('100');
  const [psy, setPsy] = useState<{ ok: boolean; warns: string[] }>({ ok: false, warns: [] });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [key] = useState(() => newKey('paper-fill'));
  const plans = useLoad(async () => {
    const frozen = (app.index?.plans ?? []).filter(x => x.status === 'frozen' && x.purpose === 'simulation');
    const full = await Promise.all(frozen.map(x => get<DecisionPlan>(`/api/v1/plans/${x.plan_id}/versions/${x.version}`)));
    return full.filter(x => x.portfolio_id === p.portfolio_id);
  }, [app.index, p.portfolio_id]);
  const plan = plans.data?.find(x => `${x.plan_id}:${x.version}` === planKey) ?? null;
  const sym = side === 'buy' ? (plan?.symbols[0] ?? '') : symbol;
  const inst = snap?.instruments.find(i => i.symbol === sym);
  const price = inst?.last_price_decimal ?? null;
  const pos = p.positions.find(x => x.symbol === symbol);
  const submit = async () => {
    if (!snap || !price) return;
    setBusy(true); setErr(null);
    try {
      await post('/api/v1/journal/events', {
        portfolio_id: p.portfolio_id, event_type: 'paper_fill', mode: 'paper', effective_at: nowIso(), symbol: sym, side,
        shares: Number(shares), price_decimal: price, net_cash_delta_minor: null, snapshot_id: snap.snapshot_id,
        plan_id: plan?.plan_id ?? null, plan_version: plan?.version ?? null, corrects_event_id: null, correction_action: null,
        corrected_event_type: null, replay_id: p.replay_id, expected_portfolio_revision: p.revision, evidence_ids: [],
        reported_fees: null, note: side === 'buy' && psy.warns.length ? `下单前自检提醒：${psy.warns.join('、')}` : '',
      }, key);
      onDone();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const fillStatus = err instanceof ApiError ? err.details.find(d => d.field === 'fill_status')?.reason : null;
  return (
    <Dialog title="录入模拟成交" onClose={onClose}>
      <div className="form">
        <p className="note" style={{ marginTop: 0 }}>按快照报价的模拟假设，不是撮合；只在连续竞价时段、同一会话、报价不超过 30 分钟时可用。不接券商。</p>
        <div className="row" role="radiogroup" aria-label="方向">
          <button className={`btn small ${side === 'buy' ? '' : 'secondary'}`} role="radio" aria-checked={side === 'buy'} onClick={() => setSide('buy')}>买入</button>
          <button className={`btn small ${side === 'sell' ? '' : 'secondary'}`} role="radio" aria-checked={side === 'sell'} onClick={() => setSide('sell')}>卖出</button>
        </div>
        {side === 'buy' ? (
          <Field label="依据的冻结计划" hint="只有已冻结、绑定本组合、执行资格为「可用于模拟执行」的计划能买入">
            <select value={planKey} onChange={e => setPlanKey(e.target.value)}>
              <option value="">选择计划</option>
              {(plans.data ?? []).map(x => <option key={`${x.plan_id}:${x.version}`} value={`${x.plan_id}:${x.version}`}
                disabled={x.execution_eligibility !== 'eligible'}>{x.title} v{x.version}{x.execution_eligibility !== 'eligible' ? '（不可执行）' : ''}</option>)}
            </select>
          </Field>
        ) : (
          <Field label="卖出哪只" hint="可卖数量 = 持股 − 今天买入（T+1）">
            <select value={symbol} onChange={e => setSymbol(e.target.value)}>
              <option value="">选择持仓</option>
              {p.positions.map(x => <option key={x.symbol} value={x.symbol}>{x.symbol}（可卖 {x.available_shares} / 持有 {x.shares}）</option>)}
            </select>
          </Field>
        )}
        <div className="form-grid">
          <Field label="股数"><input type="number" min={1} step={side === 'buy' ? 100 : 1} value={shares} onChange={e => setShares(e.target.value)} /></Field>
          <Field label="价格（来自快照）" hint={snap ? `快照 ${snap.metadata.quote_date}，获取于 ${shanghai(snap.metadata.captured_at)}` : '没有快照'}>
            <input type="text" value={price ?? '无报价'} readOnly /></Field>
        </div>
        {side === 'sell' && pos && Number(shares) > pos.available_shares ?
          <div className="alert warn">可卖 {pos.available_shares} 股：今天买入的部分下一交易日才能卖。这是交易规则约束，不是违纪。</div> : null}
        {side === 'buy' ? <Checklist onState={(ok, warns) => setPsy({ ok, warns })} /> : null}
        <div className="row">
          <button className="btn" onClick={submit} disabled={busy || !sym || !price || (side === 'buy' && (!plan || !psy.ok))}>
            {side === 'buy' ? '确认模拟买入' : '确认模拟卖出'}</button>
        </div>
        {err ? <div className="alert" role="alert">
          {fillStatus ? <strong>{fillStatus === 'unknown' ? '成交状态未知：' : '未成交：'}</strong> : null}
          {(err as Error).message}{err instanceof ApiError && err.details.length ? `（${err.details.filter(d => d.field !== 'fill_status').map(d => d.reason).join('；')}）` : ''}
          {fillStatus === 'unknown' ? <div>风险敞口仍在：持仓没有变化。</div> : null}
        </div> : null}
      </div>
    </Dialog>
  );
}

function CooldownPanel({ p }: { p: Portfolio }) {
  const ev = useLoad(() => get<ListPage<PortfolioControlEvent>>(`/api/v1/portfolios/${p.portfolio_id}/controls/events`), [p.portfolio_id]);
  const [reason, setReason] = useState('');
  const [policy, setPolicy] = useState('cooldown_manual');
  const [end, setEnd] = useState('');
  const [err, setErr] = useState<unknown>(null);
  const items = ev.data?.items ?? [];
  let active: PortfolioControlEvent | null = null;
  for (const e of items) {
    if (e.event_type === 'enabled') active = e;
    else if (active && e.related_control_event_id === active.event_id) active = null;
  }
  if (active?.scheduled_end_at && Date.parse(active.scheduled_end_at) <= Date.now()) active = null;
  const last = items.length ? items[items.length - 1].event_id : null;
  const send = async (type: 'enabled' | 'released') => {
    setErr(null);
    try {
      await post(`/api/v1/portfolios/${p.portfolio_id}/controls/events`, {
        control_type: 'cooldown', event_type: type, policy_id: type === 'enabled' ? policy : active!.policy_id,
        policy_version: type === 'enabled' ? 'v1' : active!.policy_version, effective_at: nowIso(),
        scheduled_end_at: type === 'enabled' && end ? `${end}T01:30:00Z` : null,
        related_control_event_id: type === 'released' ? active!.event_id : null, reason, expected_previous_event_id: last,
      }, newKey(`cooldown-${type}`));
      setReason(''); await ev.reload();
    } catch (e) { setErr(e); }
  };
  return (
    <div className="panel" data-testid="cooldown" data-active={active ? 'true' : 'false'}>
      <h3>冷却</h3>
      {active ? (
        <>
          <p><Tag tone="warn">冷却中</Tag> 自 {shanghai(active.effective_at)}{active.scheduled_end_at ? `，预定到 ${shanghai(active.scheduled_end_at)}` : ''}。原因：{active.reason}</p>
          <p className="note">暂停新开仓和加仓；查看行情、查看持仓风险、记录、取消计划、减仓和风险退出照常。</p>
          <div className="row"><input type="text" aria-label="解除原因" value={reason} onChange={e => setReason(e.target.value)} placeholder="解除原因（会留档，不算违规）" />
            <button className="btn secondary small" disabled={!reason.trim()} onClick={() => send('released')}>解除冷却</button></div>
        </>
      ) : (
        <>
          <p className="note">冷却是可选的：开启后只暂停新增风险的操作。可以随时解除并写下原因。</p>
          <div className="form-grid">
            <Field label="方式"><select value={policy} onChange={e => setPolicy(e.target.value)}>
              <option value="cooldown_manual">手动解除</option><option value="cooldown_until_next_session">到预定时间自动结束</option></select></Field>
            {policy === 'cooldown_until_next_session' ? <Field label="结束日（上海 09:30）"><input type="date" value={end} onChange={e => setEnd(e.target.value)} /></Field> : null}
            <Field label="原因"><input type="text" value={reason} onChange={e => setReason(e.target.value)} placeholder="例如：连续两笔止损" /></Field>
          </div>
          <button className="btn secondary small" style={{ marginTop: 8 }} disabled={!reason.trim() || (policy === 'cooldown_until_next_session' && !end)}
                  onClick={() => send('enabled')}>开启冷却</button>
        </>
      )}
      {items.length ? <details className="more"><summary>冷却事件 {items.length} 条（只追加）</summary>
        <ul className="timeline">{items.map(e => <li key={e.event_id}>{shanghai(e.recorded_at)} {e.event_type === 'enabled' ? '开启' : e.event_type === 'released' ? '解除' : '到期'}：{e.reason}</li>)}</ul>
      </details> : null}
      <ErrorNote error={err} />
    </div>
  );
}

function DiaryForm({ p, onDone }: { p: Portfolio; onDone: () => void }) {
  const app = useApp();
  const [d, setD] = useState({ saw: '', want: '', did: '', next: '', link: '' });
  const [feelings, setFeelings] = useState<string[]>([]);
  const [kind, setKind] = useState('diary');
  const [err, setErr] = useState<unknown>(null);
  const dirty = Object.values(d).some(Boolean);
  const save = async () => {
    setErr(null);
    try {
      await post('/api/v1/journal/events', noteBody(p, JSON.stringify({ schema: 'diary_v1', kind, saw: d.saw, want: d.want,
        feelings, did: d.did, next_check: d.next, link: d.link || null }), { plan_id: null }), newKey('diary'));
      setD({ saw: '', want: '', did: '', next: '', link: '' }); setFeelings([]); onDone();
    } catch (e) { setErr(e); }
  };
  return (
    <div className="form" data-testid="diary-form">
      <div className="form-grid">
        <Field label="类型"><select value={kind} onChange={e => setKind(e.target.value)}>
          <option value="diary">日记</option><option value="impulse_observed">我注意到一次冲动</option>
          <option value="plan_deviation_observed">我偏离了计划</option><option value="emotion_observed">情绪记录</option></select></Field>
        <Field label="关联（可空）"><select value={d.link} onChange={e => setD({ ...d, link: e.target.value })}>
          <option value="">不关联</option>
          {(app.index?.plans ?? []).map(x => <option key={x.plan_id} value={`plan:${x.plan_id}`}>计划：{x.title}</option>)}
          {(app.index?.cases ?? []).map(x => <option key={x.case_id} value={`case:${x.case_id}`}>Case：{x.title}</option>)}
        </select></Field>
      </div>
      <Field label="当时看到了什么"><textarea value={d.saw} onChange={e => setD({ ...d, saw: e.target.value })} /></Field>
      <Field label="想做什么"><input type="text" value={d.want} onChange={e => setD({ ...d, want: e.target.value })} /></Field>
      <div>
        <span className="lbl" style={{ fontSize: 'var(--fs-1)', fontWeight: 600 }}>感受（你自己选，系统不做诊断）</span>
        <div className="row">{FEELINGS.map(f => (
          <label key={f} className="check"><input type="checkbox" checked={feelings.includes(f)}
            onChange={e => setFeelings(e.target.checked ? [...feelings, f] : feelings.filter(x => x !== f))} />{f}</label>
        ))}</div>
      </div>
      <Field label="实际做了什么"><input type="text" value={d.did} onChange={e => setD({ ...d, did: e.target.value })} /></Field>
      <Field label="下次检查什么"><input type="text" value={d.next} onChange={e => setD({ ...d, next: e.target.value })} /></Field>
      <div className="row"><button className="btn" disabled={!dirty} onClick={save}>保存日记</button></div>
      <ErrorNote error={err} />
    </div>
  );
}

function EventRow({ e, onVoid }: { e: JournalEvent; onVoid?: () => void }) {
  const d = parseDiary(e.note);
  const cash = e.net_cash_delta_minor;
  return (
    <tr data-event-type={e.event_type}>
      <td className="num">{e.sequence}</td>
      <td className="num">{shanghai(e.effective_at)}</td>
      <td>{label(EVENT_LABEL, e.event_type)}{e.mode === 'user_reported' ? '（自报，未核验）' : e.mode === 'imported_legacy' ? '（旧账本导入）' : ''}</td>
      <td>{e.symbol ?? ''}{e.side ? ` ${e.side === 'buy' ? '买' : '卖'} ${e.shares} 股 @${formatDecimal(e.price_decimal)}` : ''}</td>
      <td className={`num ${cash && cash.startsWith('-') ? 'fall' : cash && cash !== '0' ? 'rise' : ''}`}>{cash ? formatMinor(cash, { sign: true }) : ''}</td>
      <td style={{ maxWidth: 360 }}>{d ? <>
        {d.kind === 'window_decision' ? `看盘决定：${d.decision}；${d.text ?? ''}` : null}
        {d.kind === 'no_action' ? `暂不行动：${d.text ?? ''}` : null}
        {d.kind === 'replay_decision' ? `训练决定：${d.decision}；${d.text ?? ''}` : null}
        {['diary', 'impulse_observed', 'plan_deviation_observed', 'emotion_observed'].includes(d.kind) ?
          `${d.kind === 'diary' ? '' : `${{ impulse_observed: '冲动', plan_deviation_observed: '偏离计划', emotion_observed: '情绪' }[d.kind as string]}：`}${d.saw ?? ''}${d.want ? `；想：${d.want}` : ''}${d.did ? `；做了：${d.did}` : ''}${d.feelings?.length ? `；感受：${d.feelings.join('、')}` : ''}` : null}
      </> : e.note}</td>
      <td>{onVoid ? <button className="btn quiet small" onClick={onVoid}>撤销</button> : null}</td>
    </tr>
  );
}

function PortfolioDetail({ id }: { id: string }) {
  const app = useApp();
  const pf = useLoad(() => get<Portfolio>(`/api/v1/portfolios/${id}${app.snapshot ? `?snapshot_id=${app.snapshot.snapshot_id}` : ''}`),
                     [id, app.snapshot?.snapshot_id]);
  const evs = useLoad(() => get<ListPage<JournalEvent>>(`/api/v1/journal/events?portfolio_id=${id}&limit=100`), [id, pf.data?.revision]);
  const [fill, setFill] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [watch, setWatch] = useState('');
  const [stripPrivate, setStripPrivate] = useState(true);
  const p = pf.data;
  if (pf.error) return <ErrorNote error={pf.error} title="读取组合失败" />;
  if (!p) return <p className="note">读取中…</p>;
  const reload = async () => { await pf.reload(); await evs.reload(); };
  const voidEvent = async (e: JournalEvent) => {
    if (!window.confirm('撤销会追加一条更正事件，原记录保留。确定？')) return;
    setErr(null);
    try {
      await post('/api/v1/journal/events', { ...noteBody(p, '撤销记录'), event_type: 'correction', corrects_event_id: e.event_id,
        correction_action: 'void', corrected_event_type: e.event_type }, newKey('correction'));
      await reload();
    } catch (x) { setErr(x); }
  };
  const addWatch = async (type: 'watch_added' | 'watch_removed', sym: string) => {
    setErr(null);
    try { await post('/api/v1/journal/events', { ...noteBody(p, type === 'watch_added' ? '加入观察' : '移出观察'), event_type: type, symbol: sym }, newKey(type)); setWatch(''); await reload(); }
    catch (x) { setErr(x); }
  };
  const valued = p.positions.filter(x => x.market_value_minor !== null);
  const known = addMinor(p.cash_minor, ...valued.map(x => x.market_value_minor));
  const items = evs.data?.items ?? [];
  const voided = new Set(items.filter(e => e.event_type === 'correction' && e.correction_action === 'void').map(e => e.corrects_event_id));
  const obs = items.map(e => parseDiary(e.note)).filter(d => d && ['impulse_observed', 'plan_deviation_observed', 'emotion_observed'].includes(d.kind));
  return (
    <div className="stack" data-testid="portfolio-detail" data-portfolio-id={id}>
      <div className="ws-head">
        <h1>{p.mode === 'paper' ? '模拟组合' : '自报账户（未经券商核验）'}</h1>
        <p>所有金额都是{p.mode === 'paper' ? '模拟' : '你手工报的'}，不是券商账户。</p>
      </div>
      <div className="grid-2">
        <div className="stack">
          <div className="panel" data-testid="valuation" data-quality={p.quality}>
            <KV items={[
              ['现金', `${formatMinor(p.cash_minor)} 元`],
              ['持仓市值', p.market_value_minor === null ? <span className="unknown">未知：有 {p.positions.length - valued.length} 只没有报价</span> : `${formatMinor(p.market_value_minor)} 元`],
              ['总资产', p.total_equity_minor === null ? <span className="unknown">未知（已知部分：{formatMinor(known)} 元，估值覆盖 {valued.length}/{p.positions.length}）</span> : `${formatMinor(p.total_equity_minor)} 元`],
              ['版本', p.revision], ['估值快照', app.snapshot ? `${app.snapshot.metadata.quote_date}（${app.snapshot.snapshot_id}）` : '无'],
            ]} />
            {p.missing_inputs.length ? <p className="unknown" style={{ fontSize: 'var(--fs-1)' }}>缺：{p.missing_inputs.join('、')}</p> : null}
          </div>
          <Section title="持仓" hint="可卖 = 持有 − 今天买入（T+1）">
            {p.positions.length ? (
              <div className="table-wrap"><table data-testid="positions">
                <thead><tr><th>标的</th><th className="num">持有</th><th className="num">可卖</th><th className="num">成本（元）</th>
                  <th className="num">最新价</th><th className="num">市值（元）</th><th className="num">浮动盈亏</th></tr></thead>
                <tbody>{p.positions.map(x => (
                  <tr key={x.symbol} data-symbol={x.symbol}>
                    <td>{x.symbol}</td><td className="num">{x.shares}</td><td className="num">{x.available_shares}</td>
                    <td className="num">{formatMinor(x.cost_basis_minor)}</td>
                    <td className="num" data-field="last_price">{formatDecimal(x.last_price_decimal)}</td>
                    <td className="num" data-field="market_value">{formatMinor(x.market_value_minor)}</td>
                    <td className={`num ${x.unrealized_pnl_minor?.startsWith('-') ? 'fall' : x.unrealized_pnl_minor && x.unrealized_pnl_minor !== '0' ? 'rise' : ''}`} data-field="pnl">
                      {x.unrealized_pnl_minor === null ? '--' : `${formatMinor(x.unrealized_pnl_minor, { sign: true })}（${formatRatioPct(x.unrealized_return_ratio)}）`}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            ) : <p className="note">没有持仓。</p>}
            <p className="note">没有报价的持仓：市值和盈亏显示「--」，不会用成本价冒充，也不会按 0 计。</p>
          </Section>
          <Section title="账本与日记" hint="只追加；更正保留原记录" actions={<>
            <label className="check"><input type="checkbox" checked={stripPrivate} onChange={e => setStripPrivate(e.target.checked)} />导出时去掉感受</label>
            <button className="btn quiet small" onClick={() => downloadJson(`${id}-journal.json`, items.map(e => {
              const d = parseDiary(e.note);
              return stripPrivate && d ? { ...e, note: JSON.stringify({ ...d, feelings: undefined }) } : e;
            }))}>导出</button></>}>
            {items.length ? <div className="table-wrap"><table data-testid="journal">
              <thead><tr><th className="num">#</th><th>时间（上海）</th><th>类型</th><th>内容</th><th className="num">现金变动</th><th>备注</th><th /></tr></thead>
              <tbody>{items.map(e => <EventRow key={e.event_id} e={e}
                onVoid={['paper_fill', 'manual_trade_record', 'watch_added', 'note'].includes(e.event_type) && !voided.has(e.event_id) && e.event_type !== 'note' ? () => voidEvent(e) : undefined} />)}</tbody>
            </table></div> : <p className="note">还没有记录。</p>}
          </Section>
        </div>
        <div className="stack">
          <Section title="操作">
            <div className="stack">
              {p.mode === 'paper' ? <button className="btn" onClick={() => setFill(true)} disabled={!app.snapshot}>录入模拟成交</button> : null}
              <CooldownPanel p={p} />
              <div className="panel"><h3>观察清单</h3>
                <div className="row"><input type="text" aria-label="加入观察的代码" value={watch} onChange={e => setWatch(e.target.value.toUpperCase())} placeholder="600000.SH" />
                  <button className="btn secondary small" disabled={!/^\d{6}\.(SH|SZ|BJ)$/.test(watch)} onClick={() => addWatch('watch_added', watch)}>加入观察</button></div>
                <p className="note">加入观察只保存研究链接，不会创建持仓。</p>
              </div>
            </div>
          </Section>
          <Section title="写日记"><DiaryForm p={p} onDone={reload} /></Section>
          <Section title="行为记录">
            <p data-testid="behavior-summary">你自己记下的观察：{obs.length} 条。</p>
            <p className="note">单次或少数几次记录不说明长期习惯；系统不做心理诊断。建议逐条复盘当时的计划与证据。</p>
          </Section>
        </div>
      </div>
      <ErrorNote error={err} />
      {fill ? <FillDialog p={p} onClose={() => setFill(false)} onDone={async () => { setFill(false); await reload(); }} /> : null}
    </div>
  );
}

function LegacyImport() {
  const app = useApp();
  const [ledger, setLedger] = useState<string | null>(null);
  const [trades, setTrades] = useState<string | null>(null);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const read = async (f: File | undefined, set: (t: string | null) => void) => {
    if (!f) return set(null);
    const buf = await f.arrayBuffer();
    set(new TextDecoder('utf-8', { ignoreBOM: true }).decode(buf));       // 保留 BOM 与 CRLF：服务端按原文本计算哈希
  };
  const [kind, setKind] = useState<'uploaded_json' | 'browser_local_storage'>('uploaded_json');
  const fromBrowser = () => {
    let t: string | null = null;
    try { t = window.localStorage.getItem('agi-quant-portfolio-v1'); } catch { t = null; }
    if (!t) { setErr(new ApiError(0, 'NO_LOCAL_LEDGER', '这个浏览器里没有旧面板的账本（agi-quant-portfolio-v1）')); return; }
    setLedger(t); setTrades(null); setKind('browser_local_storage'); setRes(null); setErr(null);
  };
  const body = async (dry: boolean) => {
    const enc = (t: string) => new TextEncoder().encode(t).length;
    const ls = await sha256Hex(ledger!);
    const ts = trades !== null ? await sha256Hex(trades) : null;
    const items = [['ledger_json', enc(ledger!), ls], trades !== null ? ['trades_jsonl', enc(trades), ts] : null];
    return { dry_run: dry, source_kind: kind, source_sha256: await sha256Hex(canonicalJson(items)),
             ledger_json_text: ledger, ledger_sha256: ls, trades_jsonl_text: trades, trades_sha256: ts, allow_cash_adjustment: false };
  };
  const run = async (dry: boolean) => {
    setBusy(true); setErr(null);
    try {
      const { data } = await post<any>('/api/v1/portfolio/import-legacy', await body(dry), newKey(dry ? 'import-dry' : 'import-commit'));
      setRes(data);
      if (!dry && data.portfolio_id) { await app.refreshIndex(); go('portfolio', data.portfolio_id); }
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (
    <div className="panel">
      <h3>导入旧账本</h3>
      <p className="note">旧面板的 portfolio.json（和可选的 trades.jsonl）。先预览，有任何对不上的记录都会整批拒绝，不会自动合并或补现金。原文件不会被改动。</p>
      <div className="form-grid">
        <Field label="portfolio.json"><input type="file" accept=".json,application/json" onChange={e => { setKind('uploaded_json'); read(e.target.files?.[0], setLedger); }} /></Field>
        <Field label="trades.jsonl（可空）"><input type="file" accept=".jsonl,.txt" onChange={e => read(e.target.files?.[0], setTrades)} /></Field>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn quiet small" onClick={fromBrowser}>改用本浏览器里旧面板的账本</button>
        {ledger ? <span className="note">已读入：{kind === 'browser_local_storage' ? '浏览器旧存储' : '上传文件'}（{new TextEncoder().encode(ledger).length} 字节）</span> : null}
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn secondary" disabled={!ledger || busy} onClick={() => run(true)}>预览导入</button>
        <button className="btn" disabled={!ledger || busy || !res || res.status !== 'validated'} onClick={() => run(false)}>确认导入为新组合</button>
      </div>
      {res ? <div className="stack" style={{ marginTop: 8 }}>
        <KV items={[['结果', res.status === 'validated' ? '可以导入' : res.status === 'committed' ? '已导入' : '已拒绝'],
          ['可导入事件', res.accepted_event_count], ['重复', res.duplicate_event_count],
          ['现金对账差', res.cash_reconciliation_delta_minor === null ? '--' : `${formatMinor(res.cash_reconciliation_delta_minor)} 元`]]} />
        {res.rejected_rows?.length ? <ul className="unknown" style={{ fontSize: 'var(--fs-1)', paddingLeft: 18 }}>
          {res.rejected_rows.slice(0, 20).map((r: any, i: number) => <li key={i}>{r.legacy_trade_id ?? '（无 id）'}：{r.reason}</li>)}</ul> : null}
      </div> : null}
      <ErrorNote error={err} />
    </div>
  );
}

export function PortfolioPage({ id }: { id: string | null }) {
  const app = useApp();
  const [mode, setMode] = useState<'paper' | 'user_reported'>('paper');
  const [name, setName] = useState('');
  const [cash, setCash] = useState('1000000');
  const [err, setErr] = useState<unknown>(null);
  if (id) return <PortfolioDetail id={id} />;
  const create = async () => {
    setErr(null);
    try {
      const minor = yuanToMinor(cash);
      if (minor === null) throw new ApiError(422, 'VALIDATION_ERROR', '初始资金格式不对（元，最多两位小数）');
      const { data } = await post<Portfolio>('/api/v1/portfolios', { mode, initial_cash_minor: minor, name: name.trim() || (mode === 'paper' ? '模拟组合' : '自报账户') }, newKey('portfolio-create'));
      await app.refreshIndex(); go('portfolio', data.portfolio_id);
    } catch (e) { setErr(e); }
  };
  const list = app.index?.portfolios ?? [];
  const replay = app.session?.mode === 'replay';
  return (
    <>
      <div className="ws-head"><h1>模拟仓与日记</h1><p>服务端账本是唯一真源；浏览器只提交意图，费用与可卖量由服务端计算。</p></div>
      <div className="grid-2">
        <div className="stack">
          {list.length ? <div className="table-wrap"><table data-testid="portfolio-list">
            <thead><tr><th>名称</th><th>类型</th><th className="num">版本</th><th>创建于</th></tr></thead>
            <tbody>{list.map(p => <tr key={p.portfolio_id}><td><a href={href('portfolio', p.portfolio_id)}>{p.name || p.portfolio_id}</a></td>
              <td>{p.mode === 'paper' ? '模拟' : '自报（未核验）'}</td><td className="num">{p.revision}</td><td className="num">{shanghai(p.created_at)}</td></tr>)}</tbody>
          </table></div> : <Empty title="还没有组合">建一个模拟组合开始；它和任何券商账户都没有关系。</Empty>}
        </div>
        <div className="stack">
          {!replay ? <div className="panel">
            <h3>新建组合</h3>
            <div className="form">
              <Field label="类型"><select value={mode} onChange={e => setMode(e.target.value as 'paper' | 'user_reported')}>
                <option value="paper">模拟组合</option><option value="user_reported">自报账户（只记录，不核验）</option></select></Field>
              <Field label="名称"><input type="text" value={name} onChange={e => setName(e.target.value)} /></Field>
              <Field label="初始资金（元）"><input type="text" inputMode="decimal" value={cash} onChange={e => setCash(e.target.value)} /></Field>
              <button className="btn" onClick={create}>新建组合</button>
              <ErrorNote error={err} />
            </div>
          </div> : <div className="alert info">训练中只用这场训练自己的隔离组合（在「历史训练」里）。</div>}
          {!replay ? <LegacyImport /> : null}
        </div>
      </div>
    </>
  );
}

export { parseDiary, ratioTone };
