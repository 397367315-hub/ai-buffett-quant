import { useState } from 'react';
import { errorText, newKey, post } from '../api/v1';
import { Empty, ErrorNote, Field, Section, Tag } from '../components/ui';
import { useApp } from '../lib/hooks';

interface CmdStep { n: number; tool: string; args: Record<string, unknown>; ok: boolean; summary: string }
interface CmdDispatch {
  seat_id: string; seat_name: string; ok: boolean; answer?: string; reason?: string;
  kb_refs?: { kb_id: string; book?: string | null; locator: string }[]; tokens?: number; steps?: number;
}
interface CmdReport {
  summary: string; seat_findings: { seat_id: string; seat_name: string; point: string }[];
  disagreements: string[]; watch_next: string[]; missing_inputs: string[]; data_notes: string[];
}
interface CmdOut {
  status: string; question: string; report: CmdReport; steps: CmdStep[]; dispatches: CmdDispatch[];
  tokens: number; audit: { model_run_id: string };
}
interface Turn { id: number; q: string; out?: CmdOut; err?: unknown }
const HORIZON: Record<string, string> = { short: '短线', swing: '波段', long: '长期' };

function TurnView({ turn }: { turn: Turn }) {
  const o = turn.out;
  return (
    <div className="stack" data-testid="commander-turn">
      <div className="panel">
        <span className="note">你问</span>
        <p className="lede" style={{ margin: '2px 0 0' }}>{turn.q}</p>
      </div>
      {turn.err ? <ErrorNote error={turn.err} title="这次调度失败了" /> : null}
      {o ? (
        <>
          {o.steps?.length ? (
            <details className="more" data-testid="commander-steps">
              <summary>他做了 {o.steps.length} 步</summary>
              <ol style={{ paddingLeft: 20, fontSize: 'var(--fs-1)' }}>
                {o.steps.map(s => <li key={s.n} className={s.ok ? '' : 'unknown'}>
                  <code>{s.tool}</code>：{s.summary}{s.ok ? '' : '（没成功，他换了办法）'}</li>)}
              </ol>
            </details>
          ) : null}
          {o.dispatches?.length ? (
            <div className="stack" data-testid="commander-dispatch">
              <span className="note">各席交上来的结果（他们用自己的书与模型回答）</span>
              {o.dispatches.map(d => (
                <div className="seatrun" key={d.seat_id} data-testid={`commander-seat-${d.seat_id}`}>
                  <header><h3>{d.seat_name}</h3>
                    {d.ok ? <Tag tone="ok">已交</Tag> : <Tag tone="bad">没交成</Tag>}</header>
                  {d.ok ? <>
                    <p style={{ margin: '6px 0 0' }}>{d.answer}</p>
                    {d.steps ? <p className="note">这一席自己先查了 {d.steps} 步（工具调用）</p> : null}
                    {d.kb_refs?.length ? <p className="note">引用 {d.kb_refs.length} 条：{d.kb_refs.map(r => r.locator).filter(Boolean).slice(0, 3).join('；')}</p> : null}
                  </> : <p className="unknown">原因：{d.reason}</p>}
                </div>
              ))}
            </div>
          ) : null}
          <div className="panel" data-testid="commander-report">
            <div className="row spread"><h3 style={{ margin: 0 }}>汇总报告</h3><Tag tone="seal">总指挥</Tag></div>
            <p className="lede" style={{ margin: '6px 0' }}>{o.report.summary}</p>
            {o.report.seat_findings.length ? (
              <div><strong>各席要点</strong>
                <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>
                  {o.report.seat_findings.map(f => <li key={f.seat_id}><b>{f.seat_name}</b>：{f.point}</li>)}
                </ul></div>
            ) : null}
            {o.report.disagreements.length ? (
              <div><strong>分歧</strong>
                <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>
                  {o.report.disagreements.map(x => <li key={x}>{x}</li>)}
                </ul></div>
            ) : null}
            {o.report.watch_next.length ? (
              <div><strong>接下来看</strong>
                <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>
                  {o.report.watch_next.map(x => <li key={x}>{x}</li>)}
                </ul></div>
            ) : null}
            {o.report.missing_inputs.length ? <p className="unknown">缺的数据：{o.report.missing_inputs.join('、')}</p> : null}
            {o.report.data_notes.length ? <p className="note">数据来源：{o.report.data_notes.join('；')}</p> : null}
            <p className="note">审计 {o.audit.model_run_id} · {o.tokens ?? 0} tokens · 结论只做解释，不构成买卖指令</p>
          </div>
        </>
      ) : null}
    </div>
  );
}

/** 总指挥（ADR-013）：派活给任意席位组合、自己查全席知识与猫爪数据，最后给汇总报告。 */
export function CommanderPage() {
  const app = useApp();
  const seats = app.registry?.seats ?? [];
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState('');
  const [prefer, setPrefer] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const toggle = (id: string) => setPrefer(p => p.includes(id) ? p.filter(x => x !== id) : (p.length >= 4 ? p : [...p, id]));
  const send = async () => {
    if (!q.trim() || busy) return;
    const question = q.trim();
    const id = Date.now();
    setQ(''); setBusy(true); setErr(null);
    setTurns(t => [...t, { id, q: question }]);
    try {
      const { data } = await post<CmdOut>('/api/v1/commander/sessions', {
        question, snapshot_id: app.snapshot?.snapshot_id ?? null, max_steps: 6,
        prefer_seats: prefer.length ? prefer : null,
      }, newKey('commander'));
      setTurns(t => t.map(x => x.id === id ? { ...x, out: data } : x));
    } catch (e) {
      setErr(e);
      setTurns(t => t.map(x => x.id === id ? { ...x, err: e } : x));
    } finally { setBusy(false); }
  };
  return (
    <div className="stack" data-testid="commander">
      <div className="ws-head">
        <h1>总指挥</h1>
        <p>站在 13 席之上：该派谁就派谁，各席用自己的书和模型回答；他汇总、列分歧、写缺口。不选边，也不替你做决定。</p>
      </div>
      <div className="grid-2">
        <div className="stack">
          <Section title="交给总指挥" hint="可以说「挑三个角度看看现在能不能动手」，也可以只是聊书里的方法">
            <div className="form">
              <Field label="你的要求">
                <input type="text" value={q} onChange={e => setQ(e.target.value)} data-testid="commander-question"
                       placeholder="例如：帮我从 13 席里挑 3 个角度，看看现在该不该动手；顺便说说分歧在哪"
                       onKeyDown={e => { if (e.key === 'Enter') void send(); }} />
              </Field>
              <div className="row">
                <button className="btn" onClick={send} disabled={busy || !q.trim()} data-testid="commander-send">
                  {busy ? '在调度…（会派活给席位）' : '交给总指挥'}</button>
                <span className="note">一次最多 6 步；派活最多 2 轮、每轮 3 席；所有调用算进当天 token</span>
              </div>
              <ErrorNote error={err} title={err ? errorText(err) : undefined} />
            </div>
          </Section>
          {turns.length === 0 ? (
            <Empty title="还没有对话">
              直接写你的要求。总指挥会先看谁能干这活，再派人；也会自己查全席知识库、当前快照和猫爪数据。
            </Empty>
          ) : turns.map(t => <TurnView key={t.id} turn={t} />)}
        </div>
        <div className="stack">
          <Section title="指定席位（可选）" hint={`已选 ${prefer.length}/4；不选就让他自己挑`}>
            <div className="stack" data-testid="commander-prefer">
              {seats.map(s => (
                <label key={s.seat_id} className="check">
                  <input type="checkbox" checked={prefer.includes(s.seat_id)}
                         disabled={!prefer.includes(s.seat_id) && prefer.length >= 4}
                         onChange={() => toggle(s.seat_id)} data-testid={`commander-pick-${s.seat_id}`} />
                  <span>{s.name}<span className="note"> {(s.horizons ?? []).map(h => HORIZON[h] ?? h).join('、')}</span></span>
                </label>
              ))}
            </div>
          </Section>
          <Section title="他能做什么">
            <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>
              <li>派活：1–3 席，各自用自己的模型、书与脚本回答（每席还能自己查书、读技能、跑脚本）</li>
              <li>自己查：全部 13 席知识库检索、读任一席技能原文、当前快照的市场与板块</li>
              <li>查猫爪：指数日线、个股估值、涨停事件流、交易日历（固定白名单，只读）</li>
              <li>汇总：结论、各席要点、分歧、观察项、缺口、数据来源</li>
              <li>聊天学习：不派活也能问，用全部席位的书回答</li>
            </ul>
            <p className="note">他不给买卖指令、目标价或胜率；报告里点到的席位必须是真派过活的，数字必须有出处。</p>
          </Section>
        </div>
      </div>
    </div>
  );
}
