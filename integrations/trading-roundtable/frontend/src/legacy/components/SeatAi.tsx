import { useEffect, useState } from 'react';
import { errorText, get, newKey, post } from '../../api/v1';
import type { LlmStatus } from '../../components/LlmSettings';
import type { SeatPick, StrategySeat } from '../types';

interface AiStep { n: number; tool: string; summary: string; ok: boolean }
interface AiAnswer {
  answer: string; facts_used: string[];
  kb_refs: { kb_id: string; book?: string | null; locator: string; quote?: string }[];
  missing_inputs: string[]; uncertainties: string[];
}
interface ChatOut { status: string; mode?: string; steps?: AiStep[]; answer: AiAnswer; tokens?: number; audit: { model_run_id: string } }

function pickContext(p: SeatPick): string {
  const bits = [`股票：${p.name}（${p.code}）`, `所属池：${p.poolLabel || p.pool}`, p.role ? `角色：${p.role}` : '',
    p.close != null ? `收盘：${p.close}` : '', p.stop != null ? `脚本给的止损：${p.stop}（${p.risk_pct?.toFixed(2)}%）` : '',
    p.risk_flag ? `风险标记：${p.risk_flag}` : '', p.stage ? `阶段：${p.stage}` : ''];
  return bits.filter(Boolean).join('；');
}

/**
 * 席位抽屉里的「AI 大脑」：这一席自己的模型、自己的书、自己的脚本。
 * 候选票点一下就把上下文带进问题；回答必须带可回查引用（后端拒收不合格的）。
 */
export function SeatAi({ seat, snapshotId, onOpenSettings }: {
  seat: StrategySeat; snapshotId: string | null; onOpenSettings: () => void;
}) {
  const seatId = seat.version?.startsWith('trading-') ? seat.version : null;
  const [st, setSt] = useState<LlmStatus | null>(null);
  const [bound, setBound] = useState('');
  const [q, setQ] = useState('');
  const [ctx, setCtx] = useState('');
  const [pickedCode, setPickedCode] = useState('');
  const [agent, setAgent] = useState(true);
  const [res, setRes] = useState<ChatOut | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [bindBusy, setBindBusy] = useState(false);

  const reload = () => {
    get<LlmStatus>('/api/v1/llm/status').then(s => {
      setSt(s);
      setBound(seatId ? (s.seat_bindings?.[seatId]?.profile_id ?? '') : '');
    }, e => setErr(e));
  };
  useEffect(reload, [seatId]); // eslint-disable-line react-hooks/exhaustive-deps

  const boundProfile = bound ? (st?.profiles ?? []).find(p => p.id === bound) : undefined;
  const enabled = boundProfile ? boundProfile.enabled : st?.enabled;
  const why = boundProfile ? boundProfile.reason : st?.reason;
  const effective = boundProfile?.name ?? st?.profile_name ?? '当前存档';

  const pickModel = async (value: string) => {
    if (!seatId) return;
    setBindBusy(true); setErr(null);
    try {
      await post('/api/v1/llm/seat-model', { seat_id: seatId, profile_id: value || null }, newKey('seat-model'));
      setBound(value); reload();
    } catch (e) { setErr(e); } finally { setBindBusy(false); }
  };

  const choose = (p: SeatPick) => {
    setPickedCode(p.code);
    setCtx(pickContext(p));
    setQ(`按你的方法看，${p.name}（${p.code}）现在是什么状态？符合你的哪个买点，哪里不成立？`);
    setRes(null); setErr(null);
  };

  const ask = async () => {
    if (!seatId || !q.trim()) return;
    setBusy(true); setErr(null); setRes(null);
    try {
      const { data } = await post<ChatOut>(`/api/v1/seats/${seatId}/chat`, {
        question: q.trim(), snapshot_id: snapshotId, context: ctx || null, max_steps: agent ? 4 : 2,
      }, newKey('seat-ai'));
      setRes(data);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };

  if (!seatId) return <p className="seat-ai-note">这一席还没有接入真实策略标识，先确认席位配置再问。</p>;
  return (
    <div className="seat-ai" data-testid="seat-ai">
      <div className="seat-ai-head">
        <span className="seat-ai-state">
          {st ? (enabled ? <>模型：<b>{effective}</b> · {st.model || '默认模型'}</> : <>模型未启用：{why}</>) : '读取模型状态…'}
        </span>
        <span className="seat-ai-controls">
          <select value={bound} disabled={bindBusy || !st} onChange={e => void pickModel(e.target.value)}
                  data-testid="seat-ai-model" title="这一席单独用哪个模型">
            <option value="">跟随全局（{st?.profile_name ?? '当前存档'}）</option>
            {(st?.profiles ?? []).map(p => <option key={p.id} value={p.id}>{p.name}{p.enabled ? '' : '（不可用）'}</option>)}
          </select>
          <button className="outline-btn mini" onClick={onOpenSettings} data-testid="seat-ai-settings">模型设置</button>
        </span>
      </div>

      {seat.picks && seat.picks.length > 0 ? (
        <div className="seat-ai-picks">
          <span>拿候选票来问：</span>
          {seat.picks.slice(0, 8).map(p => (
            <button key={p.code} className={`seat-ai-pick${pickedCode === p.code ? ' is-on' : ''}`}
                    onClick={() => choose(p)} data-testid={`seat-ai-pick-${p.code}`}>{p.name}</button>
          ))}
        </div>
      ) : null}

      <div className="seat-ai-ask">
        <input type="text" value={q} onChange={e => setQ(e.target.value)} data-testid="seat-ai-question"
               placeholder="问这一席：这只票现在是什么状态？哪里不成立？" />
        <button className="solid-btn" disabled={busy || !q.trim() || enabled === false} onClick={ask} data-testid="seat-ai-send">
          {busy ? (agent ? '在查资料…' : '在写…') : '对话'}
        </button>
      </div>
      <label className="seat-ai-toggle">
        <input type="checkbox" checked={agent} onChange={e => setAgent(e.target.checked)} data-testid="seat-ai-agent" />
        让它自己查资料（检索自己的书、读技能、必要时跑一遍自己的脚本）
      </label>
      {ctx ? <p className="seat-ai-context">本次带上：{ctx}</p> : null}
      {err ? <p className="seat-ai-err" role="alert">{errorText(err)}</p> : null}

      {res ? (
        <div className="seat-ai-answer" data-testid="seat-ai-answer">
          {res.steps?.length ? (
            <details className="seat-ai-steps">
              <summary>它查了 {res.steps.length} 步</summary>
              <ol>{res.steps.map(s => <li key={s.n}><code>{s.tool}</code>：{s.summary}</li>)}</ol>
            </details>
          ) : null}
          <p className="seat-ai-text">{res.answer.answer}</p>
          {res.answer.kb_refs.length ? (
            <details className="seat-ai-refs" open>
              <summary>依据（{res.answer.kb_refs.length} 条，可回查）</summary>
              <ul>{res.answer.kb_refs.map(r => (
                <li key={r.kb_id}><b>{r.book ?? '资料'}</b> <code>{r.locator}</code>
                  {r.quote ? <span className="seat-ai-quote">“{r.quote}”</span> : null}</li>))}</ul>
            </details>
          ) : null}
          {res.answer.missing_inputs.length ? <p className="seat-ai-miss">缺的数据：{res.answer.missing_inputs.join('、')}</p> : null}
          {res.answer.uncertainties.length ? <p className="seat-ai-miss">不确定：{res.answer.uncertainties.join('；')}</p> : null}
          <p className="seat-ai-audit">审计 {res.audit.model_run_id} · {res.tokens ?? 0} tokens{res.mode === 'agent' ? ` · ${res.steps?.length ?? 0} 次工具` : ''}</p>
        </div>
      ) : null}
    </div>
  );
}
