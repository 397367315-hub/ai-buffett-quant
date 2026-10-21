import { useMemo, useState } from 'react';
import { get, newKey, post, refreshContext, runJob, type Job } from '../api/v1';
import type { JournalEvent, ListPage, MarketSnapshot, Portfolio, ReplaySession } from '../api/types';
import { Dialog, Empty, ErrorNote, Field, JobProgress, KV, Section, Tag } from '../components/ui';
import { InstrumentTable } from './EnvironmentPage';
import { useApp, useLoad } from '../lib/hooks';
import { formatDecimal, yuanToMinor } from '../lib/money';
import { go, href } from '../lib/route';
import { downloadJson, shanghai, shortHash } from '../lib/format';
import { parseDiary } from './PortfolioPage';

const DECISIONS = ['继续等待', '准备条件', '模拟执行（记下价位与理由）', '放弃', '仅记录'];

function ReplayDetail({ id }: { id: string }) {
  const app = useApp();
  const session = app.session;
  const rep = useLoad(() => get<ReplaySession>(`/api/v1/replays/${id}`), [id, session?.knowledge_cutoff_at]);
  const r = rep.data;
  const bound = session?.mode === 'replay' && session.replay_id === id;
  const snap = useLoad(() => bound && r?.visible_snapshot_id ? get<MarketSnapshot>(`/api/v1/snapshots/${r.visible_snapshot_id}`)
                                                             : Promise.resolve(null), [bound, r?.visible_snapshot_id]);
  const evs = useLoad(() => bound && r ? get<ListPage<JournalEvent>>(`/api/v1/journal/events?portfolio_id=${r.isolated_portfolio_id}&limit=100`)
                                       : Promise.resolve(null), [bound, r?.isolated_portfolio_id, r?.cursor_session_date]);
  const [decision, setDecision] = useState(DECISIONS[0]);
  const [text, setText] = useState('');
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [confirmReveal, setConfirmReveal] = useState(false);
  const decided = useMemo(() => (evs.data?.items ?? []).some(e => e.event_type !== 'opening_balance'
    && e.session_date === r?.cursor_session_date), [evs.data, r?.cursor_session_date]);
  if (rep.error) return <ErrorNote error={rep.error} title="读取训练失败" />;
  if (!r) return <p className="note">读取中…</p>;
  const reloadAll = async () => { await refreshContext(); await rep.reload(); await app.refreshAll(); };
  const saveDecision = async () => {
    setBusy(true); setErr(null);
    try {
      const p = await get<Portfolio>(`/api/v1/portfolios/${r.isolated_portfolio_id}`);
      await post('/api/v1/journal/events', {
        portfolio_id: p.portfolio_id, event_type: 'note', mode: 'paper', effective_at: r.knowledge_cutoff_at, symbol: null,
        side: null, shares: null, price_decimal: null, net_cash_delta_minor: null, snapshot_id: r.visible_snapshot_id, plan_id: null,
        plan_version: null, corrects_event_id: null, correction_action: null, corrected_event_type: null, replay_id: r.replay_id,
        expected_portfolio_revision: p.revision, evidence_ids: [], reported_fees: null,
        note: JSON.stringify({ schema: 'diary_v1', kind: 'replay_decision', decision, text, cursor: r.cursor_session_date }),
      }, newKey('replay-decision'));
      setText(''); await evs.reload();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const step = async () => {
    setBusy(true); setErr(null);
    try {
      await post(`/api/v1/replays/${id}/step`, { expected_cursor_session_date: r.cursor_session_date, steps: 1 }, newKey('replay-step'));
      await reloadAll();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const reveal = async () => {
    setBusy(true); setErr(null);
    try {
      await post(`/api/v1/replays/${id}/reveal`, { expected_cursor_session_date: r.cursor_session_date, acknowledge_training_end: true },
                 newKey('replay-reveal'));
      setConfirmReveal(false); await reloadAll();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const backToLive = async () => {
    setErr(null);
    try {
      await post('/api/v1/session/context', { expected_context_revision: session!.context_revision, mode: 'live', replay_id: null,
                                              acknowledge_training_end: true }, newKey('context-live'));
      await refreshContext(); await app.refreshAll(); go('training');
    } catch (e) { setErr(e); }
  };
  const enter = async () => {
    setErr(null);
    try {
      await post('/api/v1/session/context', { expected_context_revision: session!.context_revision, mode: 'replay', replay_id: id,
                                              acknowledge_training_end: false }, newKey('context-replay'));
      await refreshContext(); await app.refreshAll();
    } catch (e) { setErr(e); }
  };
  const bars = snap.data?.daily_bars.filter(b => r.symbols.includes(b.symbol)) ?? [];
  const days: string[] = Array.from(new Set<string>(bars.map(b => b.session_date))).sort().slice(-12);
  return (
    <div className="stack" data-testid="replay-detail" data-replay-id={id} data-revealed={r.revealed ? 'true' : 'false'}>
      <div className="ws-head">
        <h1>历史训练 {r.start_session_date} 至 {r.end_session_date}</h1>
        <p>{r.revealed ? '已揭晓：后续窗口只读展示，首轮决定不能再改。' : '盲测进行中：只看得到截点之前的数据。'}</p>
      </div>
      <div className={`panel${r.revealed ? '' : ' sealed'}`}>
        <KV items={[
          ['状态', <Tag tone={r.revealed ? 'muted' : r.state === 'active' ? 'seal' : r.state === 'failed' ? 'bad' : 'info'}>
            {{ queued: '准备中', active: '盲测进行中', completed: '已到最后一天（可揭晓）', revealed: '已揭晓', failed: '失败' }[r.state] ?? r.state}</Tag>],
          ['当前交易日', r.cursor_session_date], ['知识截点（上海）', shanghai(r.knowledge_cutoff_at)],
          ['标的', r.symbols.join('、')], ['归档指纹', <code>{shortHash(r.data_archive_fingerprint)}</code>],
          ['隔离组合', <code>{r.isolated_portfolio_id}</code>],
        ]} />
        <p className="note">历史数据是事后下载的：模型可能记得这段行情，所以这里的成绩只是练习，不是前瞻能力的证明。</p>
      </div>
      {!bound ? (
        <div className="alert info">当前会话在真实盘。要继续这场训练，先进入训练上下文（真实盘的计划与组合在训练中不可见）。
          {!r.revealed ? <> <button className="btn small" onClick={enter}>进入这场训练</button></> : null}</div>
      ) : null}
      {bound && snap.data ? (
        <>
          <Section title="截点内的行情" hint={`快照 ${snap.data.snapshot_id}`}>
            <div className="table-wrap"><table data-testid="replay-history">
              <thead><tr><th>标的</th>{days.map(d => <th key={d} className="num">{d.slice(5)}</th>)}</tr></thead>
              <tbody>{r.symbols.map(s => (
                <tr key={s}><td>{s}</td>{days.map(d => {
                  const b = bars.find(x => x.symbol === s && x.session_date === d);
                  return <td key={d} className="num">{b ? formatDecimal(b.close_decimal) : '--'}</td>;
                })}</tr>
              ))}</tbody>
            </table></div>
            <details className="more"><summary>全部可见股票</summary><InstrumentTable snap={snap.data} limit={30} testid="replay-instruments" /></details>
          </Section>
          {!r.revealed ? (
            <Section title="这一天的决定" hint="推进之前必须先记下决定：等待、放弃也要记">
              <div className="form">
                <div className="row" role="radiogroup" aria-label="决定">{DECISIONS.map(x => (
                  <button key={x} className={`btn small ${decision === x ? '' : 'secondary'}`} role="radio" aria-checked={decision === x}
                          onClick={() => setDecision(x)}>{x}</button>
                ))}</div>
                <Field label="理由"><textarea value={text} onChange={e => setText(e.target.value)} placeholder="例如：主线未确认，继续等待" /></Field>
                <div className="row">
                  <button className="btn" disabled={busy} onClick={saveDecision}>保存决定</button>
                  <button className="btn secondary" disabled={busy || !decided || r.state !== 'active'} onClick={step}>推进到下一交易日</button>
                  <button className="btn quiet" onClick={() => go('cases', 'new')}>研究这个截点</button>
                  <button className="btn seal" disabled={busy} onClick={() => setConfirmReveal(true)}>结束训练并揭晓</button>
                </div>
                {!decided ? <p className="note">今天还没有保存决定，推进按钮不可用。</p> : <p className="note">今天的决定已保存。</p>}
              </div>
            </Section>
          ) : (
            <Section title="训练已结束">
              <p className="lede">揭晓窗口里的后续日线已经可见（只读）。首轮决定保持原样；如果要重练，请新建一场训练，它不会计入这一轮。</p>
              <button className="btn" onClick={backToLive}>回到真实盘</button>
            </Section>
          )}
          <Section title="我的决定记录" actions={<button className="btn quiet small" data-testid="export-replay"
              onClick={() => downloadJson(`${r.replay_id}-training.json`, { replay: r, visible_snapshot: snap.data ? {
                snapshot_id: snap.data.snapshot_id, metadata: snap.data.metadata,
                bars: snap.data.daily_bars.filter(b => r.symbols.includes(b.symbol)) } : null,
                decisions: (evs.data?.items ?? []).filter(e => e.event_type === 'note') })}>导出训练记录</button>}>
            {(evs.data?.items ?? []).filter(e => e.event_type === 'note').length ? (
              <ul className="timeline">{(evs.data?.items ?? []).filter(e => e.event_type === 'note').map(e => {
                const d = parseDiary(e.note);
                return <li key={e.event_id}><strong>{e.session_date}</strong> {d?.decision ?? '记录'}：{d?.text ?? e.note}</li>;
              })}</ul>
            ) : <p className="note">还没有记录。</p>}
          </Section>
        </>
      ) : null}
      <ErrorNote error={err} />
      {confirmReveal ? (
        <Dialog title="结束训练并揭晓" onClose={() => setConfirmReveal(false)}>
          <p>揭晓后会显示之后 5 个交易日的日线，这场训练不能再推进，已经保存的决定也不能修改。</p>
          <div className="row"><button className="btn seal" disabled={busy} onClick={reveal}>确认揭晓</button>
            <button className="btn secondary" onClick={() => setConfirmReveal(false)}>继续训练</button></div>
        </Dialog>
      ) : null}
    </div>
  );
}

function NewReplay() {
  const app = useApp();
  const arch = app.index?.archive as { fingerprint: string; coverage_start: string; coverage_end: string | null; session_count: number | null } | null | undefined;
  const [symbols, setSymbols] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [cash, setCash] = useState('1000000');
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (!arch) return <Empty title="服务端没有配置历史日线归档">历史训练只能在只读归档上进行；归档目录由本机配置（ROUNDTABLE_FILE_IMPORT_ROOTS）。</Empty>;
  const create = async () => {
    setBusy(true); setErr(null);
    try {
      const syms = symbols.split(/[\s,，]+/).filter(Boolean).map(s => s.toUpperCase());
      const j = await runJob('/api/v1/replays', { symbols: syms, start_session_date: start, end_session_date: end, strict_as_of: true,
                                                  data_archive_fingerprint: arch.fingerprint, initial_cash_minor: yuanToMinor(cash) ?? '0' },
                             newKey('replay-create'), setJob);
      await refreshContext(); await app.refreshAll();
      go('training', j.resource_id);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (
    <div className="panel">
      <h3>新建一场训练</h3>
      <p className="note">归档 {arch.coverage_start} 至 {arch.coverage_end}（{arch.session_count} 个交易日），指纹 <code>{shortHash(arch.fingerprint)}</code>。
        开始后本会话会进入训练上下文：只按日线逐日推进，不能假装盘中逐分钟训练。</p>
      <div className="form-grid">
        <Field label="标的（逗号分隔）" hint="例如 600000.SH, 000001.SZ"><input type="text" value={symbols} onChange={e => setSymbols(e.target.value)} /></Field>
        <Field label="开始交易日"><input type="date" value={start} onChange={e => setStart(e.target.value)} /></Field>
        <Field label="结束交易日"><input type="date" value={end} onChange={e => setEnd(e.target.value)} /></Field>
        <Field label="训练资金（元）"><input type="text" inputMode="decimal" value={cash} onChange={e => setCash(e.target.value)} /></Field>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn" disabled={busy || !symbols || !start || !end} onClick={create}>开始训练</button>
        <JobProgress job={busy ? job : null} />
      </div>
      <ErrorNote error={err} />
    </div>
  );
}

export function TrainingPage({ id }: { id: string | null }) {
  const app = useApp();
  const session = app.session;
  if (id) return <ReplayDetail id={id} />;
  const reps = app.index?.replays ?? [];
  return (
    <>
      <div className="ws-head"><h1>历史训练</h1><p>服务端按截点过滤一切数据：先做决定，再推进，最后才揭晓。</p></div>
      <div className="grid-2">
        <div className="stack">
          {reps.length ? <div className="table-wrap"><table data-testid="replay-list">
            <thead><tr><th>区间</th><th>状态</th><th>当前交易日</th><th>创建于</th></tr></thead>
            <tbody>{reps.map(r => <tr key={r.replay_id}>
              <td><a href={href('training', r.replay_id)}>{r.start_session_date} 至 {r.end_session_date}</a></td>
              <td>{r.revealed ? '已揭晓' : r.state}</td><td className="num">{r.cursor_session_date}</td><td className="num">{shanghai(r.created_at)}</td>
            </tr>)}</tbody>
          </table></div> : <Empty title="还没有训练">在右边新建一场：选几只票和一段历史区间。</Empty>}
        </div>
        <div className="stack">
          {session?.mode === 'replay' ? <div className="alert info">正在训练：<a href={href('training', session.replay_id)}>回到当前训练</a></div> : <NewReplay />}
        </div>
      </div>
    </>
  );
}
