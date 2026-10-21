import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { get, newKey, post, runJob, type Job } from '../api/v1';
import type { CardDeck, ListPage, MarketSnapshot, Portfolio, RoleAssignment, RoundtableCase, SeatRun } from '../api/types';
import { Empty, ErrorNote, Field, JobProgress, KV, Section, Tag } from '../components/ui';
import { SeatRunCard, seatName } from '../components/SeatRunCard';
import { useApp, useLoad } from '../lib/hooks';
import { changeRatio, formatDecimal, formatMinor, formatRatioPct, ratioTone } from '../lib/money';
import type { SeatDefinition } from '../api/types';
import { CONDITION_LABEL, ROLE_LABEL, label, missingText } from '../lib/labels';
import { go, href } from '../lib/route';
import { downloadJson, downloadText, nowIso, shanghai } from '../lib/format';

type Horizon = 'short' | 'swing' | 'long' | 'multi';
const HORIZON_LABEL: Record<string, string> = { short: '短线', swing: '波段', long: '长期', multi: '多周期' };

const TEMPLATES: { id: string; title: string; horizon: Horizon; seats: [string, RoleAssignment['role']][]; why: string }[] = [
  { id: 'relay', title: '短线接力计划审查', horizon: 'short',
    seats: [['trading-wildman-v2', 'primary'], ['trading-yangjia-v1', 'context'], ['trading-tuixue-v1', 'discipline']],
    why: '主方法定触发；养家看赚钱效应是否适配；退学查执行偏差。主方法可换成剑门或杨楠。' },
  { id: 'pullback', title: '趋势回踩研究', horizon: 'swing',
    seats: [['trading-livermore-v1', 'primary'], ['trading-candle-v1', 'validator'], ['trading-wyckoff-v1', 'validator']],
    why: '利弗莫尔看关键点与顺势；蜡烛图核形态确认；威科夫给供需反证。主方法可换成一路奔行。' },
  { id: 'longterm', title: '长期企业研究', horizon: 'long',
    seats: [['trading-darwin-v1', 'primary'], ['trading-lidaxiao-v1', 'validator'], ['trading-tuixue-v1', 'discipline']],
    why: '研究企业质量与重大风险；不生成短线情绪评分。' },
  { id: 'chase', title: '追涨杀跌复盘', horizon: 'short',
    seats: [['trading-wildman-v2', 'primary'], ['trading-tuixue-v1', 'discipline'], ['trading-yangjia-v1', 'context']],
    why: '对照事前条件、当时环境与行为链；不按事后涨跌判对错。主方法用原计划的那个。' },
  { id: 'multi', title: '多周期校验', horizon: 'swing',
    seats: [['trading-livermore-v1', 'primary'], ['trading-chenkai-v1', 'validator'], ['trading-candle-v1', 'validator']],
    why: '陈凯需要真实小时线；本环境没有，相关条件会显示待核。' },
];

/** 本快照能否满足席位的输入要求（只按快照里实际有的数据集判断）。 */
export function inputCoverage(snap: MarketSnapshot): Record<string, boolean | string> {
  const paths = new Set(snap.evidence.map(e => e.field_path ?? ''));
  const hist = snap.evidence.filter(e => e.field_path?.startsWith('seat_inputs.daily_history[')).length + 1;
  return {
    daily_history_70: hist >= 70 ? true : `只有 ${hist} 个交易日`,
    limit_pool_session: paths.has('seat_inputs.limit_pool'),
    industry_map: paths.has('seat_inputs.industry_map'),
    valuation_session: paths.has('seat_inputs.valuation'),
    dividend_history: paths.has('seat_inputs.dividend_years_current'),
    limit_events_session: paths.has('seat_inputs.limit_events'),
    hourly_bars: '本环境没有小时线',
    minute_bars: '本环境没有分钟线',
    level2: '本环境没有 Level2/逐笔',
    decision_plan: '需要事前计划（研究阶段没有）',
  };
}

function NewCase({ supersedes }: { supersedes: string | null }) {
  const app = useApp();
  const snap = app.snapshot;
  const reg = app.registry?.seats ?? [];
  const [tpl, setTpl] = useState(TEMPLATES[0].id);
  const t = TEMPLATES.find(x => x.id === tpl)!;
  const [seats, setSeats] = useState<[string, RoleAssignment['role']][]>(t.seats);
  const [horizon, setHorizon] = useState<Horizon>(t.horizon);
  const [title, setTitle] = useState('');
  const [question, setQuestion] = useState('');
  const [symbols, setSymbols] = useState<string[]>([]);
  const [q, setQ] = useState('');
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const prev = useLoad(() => supersedes ? get<RoundtableCase>(`/api/v1/cases/${supersedes}`) : Promise.resolve(null), [supersedes]);
  useEffect(() => {
    const p = prev.data;
    if (p) {
      setTitle(`${p.title}（改论点）`); setQuestion(p.question); setSymbols(p.symbols); setHorizon(p.horizon as Horizon);
      setSeats(p.role_assignments.map(r => [r.seat_id, r.role]));
    }
  }, [prev.data]);
  const combo: string[] = (() => { try { return JSON.parse(window.localStorage.getItem('rt.seat_combo.v1') ?? '[]'); } catch { return []; } })();
  const pickTemplate = (id: string) => {
    const x = TEMPLATES.find(v => v.id === id)!;
    setTpl(id); setSeats(x.seats); setHorizon(x.horizon);
  };
  const cov = useMemo(() => snap ? inputCoverage(snap) : {}, [snap]);
  const primaries = seats.filter(s => s[1] === 'primary').length;
  const matches = snap && q ? snap.instruments.filter(i => i.symbol.includes(q.toUpperCase()) || i.name.includes(q)).slice(0, 8) : [];
  const valid = !!snap && title.trim() && question.trim() && seats.length >= 1 && primaries === 1;
  const submit = async () => {
    if (!snap) return;
    setBusy(true); setErr(null);
    try {
      const primary = seats.find(s => s[1] === 'primary')![0];
      const j = await runJob('/api/v1/cases', {
        supersedes_case_id: supersedes, snapshot_id: snap.snapshot_id, title: title.trim(), question: question.trim(),
        symbols, horizon, seat_ids: seats.map(s => s[0]), primary_strategy_id: primary,
        role_assignments: seats.map(([seat_id, role]) => ({ seat_id, role })), llm_mode: 'disabled', provider_id: null,
        model_id: null, user_context_id: null, replay_id: app.session?.mode === 'replay' ? app.session.replay_id : null,
      }, newKey('case-create'), setJob);
      await app.refreshIndex();
      go('cases', j.resource_id);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  if (!snap) return <Empty title="需要先有一份快照">Case 必须固定在一个快照上。先在顶部采集数据。</Empty>;
  return (
    <div className="grid-2">
      <div className="stack">
        <Section title={supersedes ? '改论点：新建一个 Case' : '新建案例研究'}
                 hint={`固定在快照 ${snap.metadata.quote_date}（${snap.snapshot_id}）`}>
          <div className="form">
            {supersedes ? <div className="alert info">新 Case 会记录替代关系；旧 Case 和它的结论原样保留，不会被改写。</div> : null}
            <Field label="标题"><input type="text" value={title} onChange={e => setTitle(e.target.value)} placeholder="例如：600000 回踩 20 日线是否值得等" /></Field>
            <Field label="研究问题" hint="写清楚要回答什么；用户陈述与事实会分开展示">
              <textarea value={question} onChange={e => setQuestion(e.target.value)} placeholder="例如：按威科夫，这是吸筹末段还是派发？需要什么证据才算确认？" />
            </Field>
            <div className="form-grid">
              <Field label="持有周期">
                <select value={horizon} onChange={e => setHorizon(e.target.value as Horizon)}>
                  {Object.entries(HORIZON_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </Field>
              <Field label="添加标的" hint="只能选这份快照里有的股票">
                <input type="text" value={q} onChange={e => setQ(e.target.value)} placeholder="代码或名称" />
              </Field>
            </div>
            {matches.length ? <div className="row">{matches.map(m => (
              <button key={m.symbol} className="btn secondary small" disabled={symbols.includes(m.symbol)}
                      onClick={() => { setSymbols([...symbols, m.symbol]); setQ(''); }}>{m.symbol} {m.name}</button>
            ))}</div> : null}
            <div className="row">{symbols.length ? symbols.map(s => (
              <span key={s} className="tag info">{s}<button className="btn quiet small" aria-label={`移除 ${s}`}
                onClick={() => setSymbols(symbols.filter(x => x !== s))}>移除</button></span>
            )) : <span className="note">没有标的：也可以研究市场问题或行为事件。</span>}</div>
          </div>
        </Section>
        <Section title="席位组合" hint="默认 3 席，最多建议 4 席；恰好一个主方法">
          <div className="row" role="radiogroup" aria-label="任务模板">
            {TEMPLATES.map(x => (
              <button key={x.id} className={`btn ${tpl === x.id ? '' : 'secondary'} small`} role="radio" aria-checked={tpl === x.id}
                      onClick={() => pickTemplate(x.id)}>{x.title}</button>
            ))}
          </div>
          <p className="note">{t.why}</p>
          {combo.length ? <button className="btn quiet small" onClick={() => setSeats(combo.map((c, i) => [c, i === 0 ? 'primary' : 'validator']))}>
            使用席位库里的组合（{combo.length} 席）</button> : null}
          <div className="table-wrap" style={{ marginTop: 8 }}>
            <table>
              <thead><tr><th>席位</th><th>角色</th><th>本快照能否满足输入</th><th /></tr></thead>
              <tbody>{seats.map(([sid, role], i) => {
                const def = reg.find(s => s.seat_id === sid);
                const req = def?.required_inputs ?? [];
                return (
                  <tr key={sid}>
                    <td>{seatName(sid, reg)}</td>
                    <td>
                      <select aria-label={`${seatName(sid, reg)} 的角色`} value={role}
                              onChange={e => setSeats(seats.map((s, k) => k === i ? [s[0], e.target.value as RoleAssignment['role']] : s))}>
                        {Object.entries(ROLE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                      </select>
                    </td>
                    <td>{req.map(r => {
                      const c = cov[r];
                      return <div key={r} className={c === true ? '' : 'unknown'}>{r}：{c === true ? '有' : c === false ? '缺' : String(c ?? '未知')}</div>;
                    })}</td>
                    <td><button className="btn quiet small" onClick={() => setSeats(seats.filter((_, k) => k !== i))}>移除</button></td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <select aria-label="添加席位" value="" onChange={e => { if (e.target.value) setSeats([...seats, [e.target.value, 'validator']]); }}>
              <option value="">添加席位…</option>
              {reg.filter(s => !seats.some(x => x[0] === s.seat_id)).map(s => <option key={s.seat_id} value={s.seat_id}>{s.name}</option>)}
            </select>
            {seats.length > 4 ? <span className="unknown">超过 4 席：运行更慢，也不代表结论更可靠，请写明为什么需要</span> : null}
            {seats.length === 1 ? <span className="note">只有 1 席：这是方法学习，结果不能叫「圆桌共识」</span> : null}
            {primaries !== 1 ? <span className="unknown">需要恰好一个主方法（现在 {primaries} 个）</span> : null}
          </div>
        </Section>
        <div className="row">
          <button className="btn" disabled={!valid || busy} onClick={submit}>{busy ? '运行中…' : '开始研究'}</button>
          <JobProgress job={busy ? job : null} />
        </div>
        <ErrorNote error={err} />
      </div>
      <div className="stack">
        <Section title="研究前须知">
          <ul className="note" style={{ paddingLeft: 18 }}>
            <li>所有席位读同一份快照；研究过程中不会混入之后刷新的数据。</li>
            <li>席位不投票。分歧原样保留，主方法只有一个。</li>
            <li>需要分钟线、Level2 或小时线的条件一律「待核」，不用日线代替。</li>
            <li>模型解释默认关闭；没有模型也能看到规则结论和证据。</li>
          </ul>
        </Section>
      </div>
    </div>
  );
}

function CaseDetail({ id }: { id: string }) {
  const app = useApp();
  const reg = app.registry?.seats ?? [];
  const c = useLoad(() => get<RoundtableCase>(`/api/v1/cases/${id}`), [id]);
  const runs = useLoad(() => get<ListPage<SeatRun>>(`/api/v1/seat-runs?case_id=${encodeURIComponent(id)}&limit=100`), [id]);
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [pid, setPid] = useState('');
  const [why, setWhy] = useState('');
  const kase = c.data;
  useEffect(() => {
    if (kase && !['succeeded', 'partial', 'failed'].includes(kase.state)) {
      const t = setTimeout(() => { c.reload(); runs.reload(); }, 1500);
      return () => clearTimeout(t);
    }
  }, [kase]); // eslint-disable-line react-hooks/exhaustive-deps
  if (c.error) return <ErrorNote error={c.error} title="读取 Case 失败" />;
  if (!kase) return <p className="note">读取中…</p>;
  const roles = Object.fromEntries(kase.role_assignments.map(r => [r.seat_id, r.role]));
  const ROLE_ORDER: Record<string, number> = { primary: 0, validator: 1, context: 2, discipline: 3 };
  const initial = (runs.data?.items ?? []).filter(r => r.round === 'initial')
    .sort((a, b) => (ROLE_ORDER[roles[a.seat_id]] ?? 9) - (ROLE_ORDER[roles[b.seat_id]] ?? 9)
      || kase.seat_ids.indexOf(a.seat_id) - kase.seat_ids.indexOf(b.seat_id));
  const challenge = (runs.data?.items ?? []).filter(r => r.round === 'challenge');
  const fin = kase.final_result;
  const supersededBy = app.index?.cases.filter(x => x.supersedes_case_id === kase.case_id) ?? [];
  const deck = async () => {
    setErr(null);
    try {
      const { data } = await post<CardDeck>('/api/v1/decks', { source_type: 'case', source_id: kase.case_id, source_version: null,
                                                                title: `复习：${kase.title}` }, newKey('deck-case'));
      await app.refreshIndex();
      go('seats', data.deck_id);
    } catch (e) { setErr(e); }
  };
  const noAction = async () => {
    setErr(null); setMsg(null);
    try {
      const p = await get<Portfolio>(`/api/v1/portfolios/${pid}`);
      await post('/api/v1/journal/events', {
        portfolio_id: pid, event_type: 'note', mode: p.mode === 'paper' ? 'paper' : 'user_reported', effective_at: nowIso(),
        symbol: kase.symbols[0] ?? null, side: null, shares: null, price_decimal: null, net_cash_delta_minor: null,
        snapshot_id: kase.snapshot_id, plan_id: null, plan_version: null, corrects_event_id: null, correction_action: null,
        corrected_event_type: null, replay_id: kase.replay_id, expected_portfolio_revision: p.revision, evidence_ids: [],
        reported_fees: null,
        note: JSON.stringify({ schema: 'diary_v1', kind: 'no_action', case_id: kase.case_id, text: why || '暂不行动' }),
      }, newKey('case-no-action'));
      setMsg('已记下「暂不行动」'); setWhy('');
    } catch (e) { setErr(e); }
  };
  return (
    <div className="stack" data-testid="case-detail" data-case-id={kase.case_id} data-state={kase.state}>
      <div className="ws-head">
        <h1>{kase.title}</h1>
        <p>{kase.question}</p>
      </div>
      <div className="panel">
        <KV items={[
          ['状态', <Tag tone={kase.state === 'succeeded' ? 'ok' : kase.state === 'failed' ? 'bad' : 'warn'}>{
            { succeeded: '已完成', partial: '部分完成（有席位失败或缺输入）', failed: '失败', queued: '排队中', preparing: '准备中',
              initial_round: '首轮独立运行中', challenge_round: '质询中', synthesizing: '汇总中' }[kase.state] ?? kase.state}</Tag>],
          ['快照', <code>{kase.snapshot_id}</code>],
          ['标的', kase.symbols.join('、') || '（市场问题）'],
          ['持有周期', HORIZON_LABEL[kase.horizon] ?? kase.horizon],
          ['主方法', seatName(kase.primary_strategy_id, reg)],
          ['模型解释', kase.llm_mode === 'disabled' ? '关闭（只看规则与证据）' : kase.llm_mode],
          ['创建于', shanghai(kase.created_at)],
          ...(kase.supersedes_case_id ? [['替代的 Case', <a href={href('cases', kase.supersedes_case_id)}>{kase.supersedes_case_id}</a>] as [string, ReactNode]] : []),
          ...(supersededBy.length ? [['已被新 Case 替代', <>{supersededBy.map(x => <a key={x.case_id} href={href('cases', x.case_id)}>{x.title}</a>)}</>] as [string, ReactNode]] : []),
        ]} />
      </div>
      <CaseFacts kase={kase} />
      {kase.supersedes_case_id ? <CaseCompare kase={kase} runs={initial} /> : null}
      <Section title="各席独立结论" hint="首轮互不可见；质询只记录反证，不改规则结论">
        <div className="grid-cols">
          {initial.map(r => <SeatRunCard key={r.seat_run_id} run={r} role={roles[r.seat_id]} reg={reg}
                                          challenges={challenge.filter(x => x.seat_id === r.seat_id)} />)}
        </div>
        {!initial.length ? <p className="note">席位还在运行…</p> : null}
      </Section>
      {fin ? (
        <Section title="汇总（不计票）">
          <div className="stack">
            <p className="lede" data-testid="case-summary">{fin.summary}</p>
            <div className="grid-cols">
              <div className="panel"><h3>共同事实</h3>
                {fin.agreements.length ? <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>{fin.agreements.map((a, i) => <li key={i}>{a}</li>)}</ul>
                  : <p className="note">没有被多个席位共同引用的事实。</p>}</div>
              <div className="panel" data-testid="disagreements"><h3>方法分歧</h3>
                {fin.disagreements.length ? <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>{fin.disagreements.map((a, i) => <li key={i}>{a}</li>)}</ul>
                  : <p className="note">没有登记分歧。</p>}</div>
              <div className="panel"><h3>主方法下的条件</h3>
                <ul className="conds">{fin.confirmation_conditions.slice(0, 8).map(cr => (
                  <li key={cr.condition_id}><span className={`res ${cr.result}`}>{label(CONDITION_LABEL, cr.result)}</span><span>{cr.description}</span></li>
                ))}</ul>
                {fin.invalidation_conditions.length ? <><h3 style={{ marginTop: 8 }}>失效 / 反证</h3>
                  <ul className="conds">{fin.invalidation_conditions.slice(0, 8).map(cr => (
                    <li key={cr.condition_id}><span className={`res ${cr.result}`}>{label(CONDITION_LABEL, cr.result)}</span><span>{cr.description}</span></li>
                  ))}</ul></> : null}
              </div>
              <div className="panel"><h3>待核证据</h3>
                {fin.missing_inputs.length ? <p className="unknown" style={{ fontSize: 'var(--fs-1)' }}>{fin.missing_inputs.map(missingText).join('、')}</p> : <p className="note">无</p>}
                <p className="note">执行收益：{fin.executed_return_ratio ?? '无（研究不是成交）'}</p>
              </div>
            </div>
          </div>
        </Section>
      ) : null}
      <Section title="接下来">
        <div className="stack">
          <div className="row">
            <button className="btn" disabled={!['succeeded', 'partial'].includes(kase.state)} onClick={() => go('plans', 'new', kase.case_id)}>由此起草计划</button>
            <button className="btn secondary" disabled={!['succeeded', 'partial', 'failed'].includes(kase.state)} onClick={deck}>生成复习卡组</button>
            <button className="btn secondary" onClick={() => go('cases', 'new', kase.case_id)}>改论点重新研究</button>
            <button className="btn quiet" onClick={() => downloadJson(`${kase.case_id}.json`, { case: kase, seat_runs: runs.data?.items ?? [] })}>导出 JSON</button>
            <button className="btn quiet" onClick={() => downloadText(`${kase.case_id}.md`, caseMarkdown(kase, initial, reg))}>导出 Markdown</button>
          </div>
          <div className="form-grid">
            <Field label="记录暂不行动：写到哪个组合">
              <select value={pid} onChange={e => setPid(e.target.value)}>
                <option value="">选择组合</option>
                {(app.index?.portfolios ?? []).map(p => <option key={p.portfolio_id} value={p.portfolio_id}>{p.name || p.portfolio_id}</option>)}
              </select>
            </Field>
            <Field label="原因"><input type="text" value={why} onChange={e => setWhy(e.target.value)} placeholder="条件不全，等确认" /></Field>
          </div>
          <div><button className="btn secondary small" disabled={!pid} onClick={noAction}>记录暂不行动</button></div>
          {msg ? <p className="note" role="status">{msg}</p> : null}
          <ErrorNote error={err} />
        </div>
      </Section>
    </div>
  );
}

export function CasesPage({ id, sub }: { id: string | null; sub: string | null }) {
  const app = useApp();
  if (id === 'new') return <NewCase supersedes={sub} />;
  if (id) return <CaseDetail id={id} />;
  const cases = app.index?.cases ?? [];
  return (
    <>
      <div className="ws-head">
        <h1>案例研究</h1>
        <p>一个明确的问题、一份固定的快照、3–4 个各说各话的方法。</p>
        <button className="btn" onClick={() => go('cases', 'new')} disabled={!app.snapshot}>新建案例研究</button>
      </div>
      {cases.length ? (
        <div className="table-wrap">
          <table data-testid="case-list">
            <thead><tr><th>标题</th><th>状态</th><th>标的</th><th>主方法</th><th>快照</th><th>创建于</th></tr></thead>
            <tbody>{cases.map(c => (
              <tr key={c.case_id}>
                <td><a href={href('cases', c.case_id)}>{c.title}</a>{c.supersedes_case_id ? <span className="note">（替代旧 Case）</span> : null}</td>
                <td>{c.state}</td><td>{c.symbols.join('、') || '--'}</td><td>{seatName(c.primary_strategy_id, app.registry?.seats)}</td>
                <td><code>{c.snapshot_id}</code></td><td className="num">{shanghai(c.created_at)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : <Empty title="还没有研究过任何问题">从「新建案例研究」开始：写下问题，选 3 个互补的席位。</Empty>}
    </>
  );
}


function useSnapshot(id: string) {
  const app = useApp();
  return useLoad(() => app.snapshot?.snapshot_id === id ? Promise.resolve(app.snapshot) : get<MarketSnapshot>(`/api/v1/snapshots/${id}`), [id]);
}

/** 事实区：研究标的在 Case 快照里的日线（图表右边界 = 快照会话日），每条带可知时间。 */
function CaseFacts({ kase }: { kase: RoundtableCase }) {
  const snap = useSnapshot(kase.snapshot_id);
  if (!kase.symbols.length) return null;
  if (snap.error) return <ErrorNote error={snap.error} title="读取 Case 快照失败" />;
  const s = snap.data;
  if (!s) return <p className="note">读取事实…</p>;
  return (
    <Section title="事实" hint={`快照 ${s.metadata.quote_date}，右边界就是这一天；之后的数据不会出现在这里`}>
      <div className="grid-cols">
        {kase.symbols.map(sym => {
          const inst = s.instruments.find(i => i.symbol === sym);
          const bars = s.daily_bars.filter(b => b.symbol === sym).sort((a, b) => a.session_date.localeCompare(b.session_date)).slice(-8);
          const r = changeRatio(inst?.last_price_decimal, inst?.prev_close_decimal);
          const t = ratioTone(r);
          return (
            <div className="panel" key={sym} data-testid="case-facts" data-symbol={sym}>
              <h3>{sym} {inst?.name ?? ''}</h3>
              <p className="num">最新价 {formatDecimal(inst?.last_price_decimal)}
                <span className={t === 'up' ? 'rise' : t === 'down' ? 'fall' : ''}>（{r === null ? '--' : formatRatioPct(r)}）</span>
                {inst?.suspended ? '；停牌' : inst?.suspended === null ? '；停牌状态未知' : ''}</p>
              <table className="mini"><thead><tr><th>日期</th><th className="num">收盘</th><th className="num">成交额（元）</th><th>可知于</th></tr></thead>
                <tbody>{bars.map(b => <tr key={b.session_date}><td className="num">{b.session_date.slice(5)}</td><td className="num">{formatDecimal(b.close_decimal)}</td>
                  <td className="num">{formatMinor(b.amount_minor)}</td><td className="num">{b.available_at ? shanghai(b.available_at).slice(5) : '--'}</td></tr>)}</tbody></table>
              <p className="note">只有日线；分时、盘口与公告不在这份快照里。</p>
            </div>
          );
        })}
      </div>
    </Section>
  );
}

/** 与被替代的 Case 比较：事实变化（快照）与判断变化（各席结论）分开列。 */
function CaseCompare({ kase, runs }: { kase: RoundtableCase; runs: SeatRun[] }) {
  const prev = useLoad(() => get<RoundtableCase>(`/api/v1/cases/${kase.supersedes_case_id}`), [kase.supersedes_case_id]);
  const prevRuns = useLoad(() => get<ListPage<SeatRun>>(`/api/v1/seat-runs?case_id=${kase.supersedes_case_id}&limit=100`), [kase.supersedes_case_id]);
  const app = useApp();
  if (!prev.data) return null;
  const p = prev.data;
  const before = Object.fromEntries((prevRuns.data?.items ?? []).filter(r => r.round === 'initial').map(r => [r.seat_id, r]));
  const judge = runs.map(r => ({ seat: r.seat_id, now: r.verdict, was: before[r.seat_id]?.verdict ?? '（上次没参与）' }))
    .filter(x => x.now !== x.was);
  return (
    <Section title={`与被替代的 Case 比较`} hint={p.title}>
      <div className="grid-cols">
        <div className="panel"><h3>事实变化</h3>
          <p style={{ fontSize: 'var(--fs-1)' }}>{p.snapshot_id === kase.snapshot_id ? '同一份快照：事实没有变化，变化的只是问题或判断'
            : `快照从 ${p.snapshot_id} 换成 ${kase.snapshot_id}`}</p>
          <p className="note">问题：{p.question === kase.question ? '未改' : `由「${p.question}」改为「${kase.question}」`}</p></div>
        <div className="panel"><h3>判断变化</h3>
          {judge.length ? <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>{judge.map(j => (
            <li key={j.seat}>{seatName(j.seat, app.registry?.seats)}：{j.was} → {j.now}</li>))}</ul>
            : <p className="note">各席结论没有变化。</p>}</div>
      </div>
    </Section>
  );
}

function caseMarkdown(k: RoundtableCase, runs: SeatRun[], reg: SeatDefinition[]): string {
  const lines = [`# ${k.title}`, '', `- 问题：${k.question}`, `- Case：${k.case_id}`, `- 快照：${k.snapshot_id}`,
    `- 标的：${k.symbols.join('、') || '（市场问题）'}`, `- 主方法：${seatName(k.primary_strategy_id, reg)}`,
    `- 模型解释：${k.llm_mode}`, `- 创建于：${shanghai(k.created_at)}（上海）`, '', '## 各席结论', ''];
  for (const r of runs) {
    lines.push(`### ${seatName(r.seat_id, reg)}（规则 ${String(r.fingerprints?.rule_version ?? '')}）`, '',
      `- 运行状态：${r.run_state}；适用性：${r.applicability}（${r.applicability_reason}）；结论：${r.verdict}`,
      ...r.condition_results.map(c => `- [${c.result}] ${c.description}`),
      ...(r.missing_inputs.length ? [`- 缺：${r.missing_inputs.join('、')}`] : []), '');
  }
  const f = k.final_result;
  if (f) lines.push('## 汇总（不计票）', '', f.summary, '', ...f.disagreements.map(d => `- 分歧：${d}`), '');
  lines.push('> 导出不含凭据与私人日记；席位结论是规则在这份快照上的判断，不是买卖建议。');
  return lines.join('\n');
}
