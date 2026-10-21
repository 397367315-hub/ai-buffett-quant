import type { SeatDefinition, SeatRun } from '../api/types';
import { CONDITION_LABEL, ROLE_LABEL, label, missingText, seatStatusLabel, seatStatusTone } from '../lib/labels';
import { Tag } from './ui';
import caps from '../data/seat-capabilities.json';
import { assetUrl } from '../lib/assets';

const CAPS = (caps as { seats: Record<string, { name: string; avatar: string | null }> }).seats;

export function seatName(id: string, reg?: SeatDefinition[] | null): string {
  return reg?.find(s => s.seat_id === id)?.name ?? CAPS[id]?.name ?? id;
}

export function seatAvatar(id: string): string | null {
  return CAPS[id]?.avatar ? assetUrl(CAPS[id].avatar) : null;
}

export function SeatRunCard({ run, role, challenges = [], reg }: { run: SeatRun; role?: string; challenges?: SeatRun[];
  reg?: SeatDefinition[] | null }) {
  const tone = seatStatusTone(run);
  return (
    <article className="seatrun" data-testid="seat-run" data-seat-id={run.seat_id} data-run-state={run.run_state}
             data-applicability={run.applicability} data-verdict={run.verdict}>
      <header>
        <div>
          <h3>{seatName(run.seat_id, reg)}</h3>
          <div className="role">{role ? `${label(ROLE_LABEL, role)}` : '单席运行'}；规则 {String(run.fingerprints?.rule_version ?? '')}</div>
        </div>
        <Tag tone={tone} testid="seat-status">{seatStatusLabel(run)}</Tag>
      </header>
      <p className="note" style={{ marginTop: 6 }}>{run.applicability_reason}</p>
      {run.explanation ? <p style={{ margin: '6px 0 0', fontSize: 'var(--fs-1)' }}>{run.explanation}</p> : null}
      {run.error ? <p className="unknown" style={{ fontSize: 'var(--fs-1)' }}>失败：{run.error?.error?.code} {run.error?.error?.message}</p> : null}
      {run.condition_results.length ? (
        <ul className="conds">
          {run.condition_results.slice(0, 12).map(c => (
            <li key={c.condition_id} data-condition={c.condition_id} data-result={c.result}>
              <span className={`res ${c.result}`}>{label(CONDITION_LABEL, c.result)}</span>
              <span>{c.description}{c.missing_inputs.length ? `（缺${c.missing_inputs.map(missingText).join('、')}）` : ''}</span>
            </li>
          ))}
          {run.condition_results.length > 12 ? <li><span /> <span className="note">另有 {run.condition_results.length - 12} 条条件</span></li> : null}
        </ul>
      ) : null}
      {run.candidates.length ? (
        <details className="more">
          <summary>本席候选 {run.candidates.length} 只（只是本方法的研究对象，不是买入建议）</summary>
          <ul style={{ fontSize: 'var(--fs-1)', paddingLeft: 18 }}>
            {run.candidates.slice(0, 30).map(c => <li key={c.symbol}>{c.symbol}：{c.reason}</li>)}
          </ul>
        </details>
      ) : null}
      {run.missing_inputs.length ? <div className="missing">本环境缺：{run.missing_inputs.map(missingText).join('、')}（缺什么就判不了什么，不用日线代替）</div> : null}
      {run.counter_evidence.length ? (
        <details className="more"><summary>反证与待核 {run.counter_evidence.length} 条</summary>
          <ul style={{ fontSize: 'var(--fs-1)', paddingLeft: 18 }}>{run.counter_evidence.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </details>
      ) : null}
      {challenges.length ? (
        <details className="more"><summary>质询记录 {challenges.length} 条（规则结论不因质询改变）</summary>
          {challenges.map(c => <p key={c.seat_run_id} className="note">{c.explanation}<br />{c.counter_evidence.join('；')}</p>)}
        </details>
      ) : null}
      <div className="note">证据 {run.evidence_ids.length} 条；运行 {run.seat_run_id}</div>
    </article>
  );
}
