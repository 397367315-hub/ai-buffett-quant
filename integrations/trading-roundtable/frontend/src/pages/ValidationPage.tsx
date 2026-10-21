import { useEffect, useState } from 'react';
import { get, newKey, runJob, type Job } from '../api/v1';
import type { DecisionPlan, ValidationRun } from '../api/types';
import { Empty, ErrorNote, Field, JobProgress, KV, Section, Tag } from '../components/ui';
import { seatName } from '../components/SeatRunCard';
import { useApp, useLoad } from '../lib/hooks';
import { formatRatioPct, ratioTone } from '../lib/money';
import { go, href } from '../lib/route';
import { dateOnly, downloadJson, nowIso, sha256Hex, shanghai, shortHash } from '../lib/format';

function Ratio({ v, empty }: { v: string | null; empty: string }) {
  if (v === null) return <span className="unknown">{empty}</span>;
  const t = ratioTone(v);
  return <span className={t === 'up' ? 'rise' : t === 'down' ? 'fall' : ''}>{formatRatioPct(v, 3)}</span>;
}

function LegacyMaterial() {
  const app = useApp();
  const replay = app.session?.mode === 'replay';
  const d = useLoad(() => replay ? Promise.resolve(null) : get<any>('/api/dashboard'), [replay]);
  if (replay) return <p className="note">训练中不显示旧资料（旧接口会带出截点之后的真实数据）。</p>;
  if (d.error) return <ErrorNote error={d.error} title="旧资料读取失败" />;
  const m = d.data?.legacyEvaluation;
  if (!m) return <p className="note">读取中…</p>;
  return (
    <div className="stack" data-testid="legacy-evaluation" data-status={m.status} data-sampled-days={m.sampled_days ?? ''}>
      <div className="alert warn">
        <strong>历史资料，待复核：</strong>这些数字来自 2026-10-19 的一次手工脚本运行，没有经过冻结登记，不参与可比排行，也不能当作原书方法的检验结果。
      </div>
      <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }} data-testid="legacy-disclosures">
        {m.disclosures.map((x: string, i: number) => <li key={i}>{x}</li>)}
      </ul>
      <div className="table-wrap"><table data-testid="legacy-rows">
        <thead><tr><th>席位</th><th className="num">采样日</th><th className="num">超额（旧口径）</th><th className="num">t（旧口径）</th><th>怎么读</th></tr></thead>
        <tbody>{m.rows.map((r: any) => (
          <tr key={r.seat} data-lookahead={r.lookahead_contaminated ? 'true' : 'false'} data-clean={r.clean_performance ? 'true' : 'false'}>
            <td>{r.seat}</td><td className="num">{r.sampled_days ?? '--'}</td>
            <td className={`num ${!r.lookahead_contaminated && r.excess_pct_legacy < 0 ? 'fall' : !r.lookahead_contaminated && r.excess_pct_legacy > 0 ? 'rise' : ''}`}>
              {r.lookahead_contaminated ? <span className="unknown">不计（前视污染）</span> : `${r.excess_pct_legacy > 0 ? '+' : ''}${r.excess_pct_legacy}%`}</td>
            <td className="num">{r.lookahead_contaminated ? '--' : r.t_legacy}</td>
            <td className="note" style={{ margin: 0 }}>{r.display_rule}</td>
          </tr>
        ))}</tbody>
      </table></div>
      <details className="more"><summary>原件登记（只读、按字节哈希）</summary>
        <ul style={{ fontSize: 'var(--fs-1)', paddingLeft: 18 }}>{m.source_artifacts.map((a: any) => (
          <li key={a.file}>{a.file}：{a.present ? `${a.size} 字节，${shortHash(a.sha256)}` : '源包里不存在'}</li>
        ))}</ul>
      </details>
    </div>
  );
}

function ValidationDetail({ id }: { id: string }) {
  const app = useApp();
  const v = useLoad(() => get<ValidationRun>(`/api/v1/validations/${id}`), [id]);
  const run = v.data;
  useEffect(() => {
    if (run && ['queued', 'running'].includes(run.state)) {
      const t = setTimeout(() => v.reload(), 2000);
      return () => clearTimeout(t);
    }
  }, [run]); // eslint-disable-line react-hooks/exhaustive-deps
  if (v.error) return <ErrorNote error={v.error} title="读取验证失败" />;
  if (!run) return <p className="note">读取中…</p>;
  const m = run.metrics;
  const mi = run.evaluation_manifest.content;
  const category = run.target_type === 'seat' ? '候选研究' : '前瞻登记';
  return (
    <div className="stack" data-testid="validation-detail" data-validation-id={id} data-state={run.state}>
      <div className="ws-head"><h1>{category}：{run.target_type === 'seat' ? seatName(run.target_id, app.registry?.seats) : run.target_id}</h1>
        <p>{mi.hypothesis}</p></div>
      <div className="panel sealed">
        <KV items={[
          ['状态', <Tag tone={run.state === 'succeeded' ? 'ok' : run.state === 'failed' ? 'bad' : 'warn'}>
            {{ queued: '排队中', running: '运行中', succeeded: '已完成', partial: '部分完成或已停止', failed: '失败或完整性停止', skipped: '跳过' }[run.state] ?? run.state}</Tag>],
          ['登记冻结于', shanghai(run.evaluation_manifest.frozen_at)],
          ['登记哈希', <code>{shortHash(run.evaluation_manifest.frozen_hash)}</code>],
          ['窗口', `${run.window.start_session_date} 至 ${run.window.end_session_date}`],
          ['数据指纹', <code>{shortHash(run.dataset_fingerprint)}</code>],
          ['规则版本', run.rule_version], ['费用模型', run.fee_model_version], ['成交/评价模型', run.execution_model_version],
          ['样本规则', mi.sample_selection_policy], ['预算（最多调度次数）', mi.trial_budget], ['随机种子', run.seed],
          ['统计方法', mi.statistical_plan.confidence_interval_method],
        ]} />
      </div>
      <Section title="结果" hint="三类结果分开：候选后验变化 ≠ 执行收益">
        <div className="grid-cols">
          <div className="panel"><h3>样本</h3><p className="num">{m.sample_count} 个可评价样本；成交 {m.executed_trade_count} 笔</p></div>
          <div className="panel"><h3>扣成本后超额（相对等权基准）</h3><p><Ratio v={m.excess_return_ratio} empty="未出结论" /></p></div>
          <div className="panel"><h3>候选方向变化</h3><p><Ratio v={m.directional_price_return_ratio} empty="未出结论" /></p></div>
          <div className="panel"><h3>执行收益</h3><p><Ratio v={m.execution_return_ratio} empty="空：没有成交证据，不用候选变化冒充" /></p></div>
        </div>
      </Section>
      <Section title="限制与说明">
        <ul style={{ paddingLeft: 18 }} data-testid="limit-notes">{run.limit_notes.map((x, i) => <li key={i}>{x}</li>)}</ul>
        <p className="note">样本外没有可靠超额，只说明这个实现、这份数据和这个口径没有提供证据；不能据此判定原书或作者的方法无效。</p>
        <button className="btn quiet small" onClick={() => downloadJson(`${id}.json`, run)}>导出 JSON</button>
      </Section>
    </div>
  );
}

function NewValidation() {
  const app = useApp();
  const arch = app.index?.archive as any;
  const reg = app.registry?.seats ?? [];
  const [kind, setKind] = useState<'seat' | 'plan'>('seat');
  const [seat, setSeat] = useState('trading-wyckoff-v1');
  const [planKey, setPlanKey] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [every, setEvery] = useState('5');
  const [horizon, setHorizon] = useState('5');
  const [budget, setBudget] = useState('40');
  const [seed, setSeed] = useState('20261019');
  const [lag, setLag] = useState('10');
  const [block, setBlock] = useState('10');
  const [hyp, setHyp] = useState('');
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const frozen = (app.index?.plans ?? []).filter(p => p.status === 'frozen');
  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      const statistical_plan = { primary_metric: kind === 'seat' ? 'excess_return_ratio' : 'direction_return_ratio',
        secondary_metrics: [], null_hypothesis: '均值为 0', minimum_sample_size: 2,
        confidence_interval_method: `hac lag=${lag} block=${block} replicates=2000`, significance_level_ratio: '0.05',
        multiple_testing_policy: 'single_preregistered_test', outlier_policy: 'keep_all' };
      const stopping_rules = [{ condition_id: 'stop_on_budget', description: '下一次调度会超出预算时停止，并记录停止事件' },
                              { condition_id: 'stop_on_data_leak', description: '输入里出现截点之后的数据立即停止，不出干净结论' }];
      let mi: any;
      if (kind === 'seat') {
        const s = reg.find(x => x.seat_id === seat)!;
        mi = { target_type: 'seat', target_id: seat, target_version: s.rule_version, dataset_fingerprint: arch.fingerprint,
               rule_version: s.rule_version, calendar_version: arch.calendar_version, fee_model_version: 'legacy-paper-fee-v1',
               execution_model_version: 'candidate_research_t1_open_th_close_v1', seed: Number(seed),
               window: { start_session_date: start, end_session_date: end }, split_policy: 'full_sample_descriptive',
               hypothesis: hyp || `${s.name} 的候选在 T+1 开盘到 T+${horizon} 收盘，扣成本后相对等权基准的变化`,
               skill_sha256: s.skill_sha256, code_revision: 'ui', universe_policy_id: 'universe_cn_mainboard_v1',
               universe_fingerprint: '0'.repeat(64), sample_selection_policy: `every_${every}_sessions horizon=${horizon}`,
               inclusion_criteria: ['沪深主板非 ST（当日名称）'], exclusion_criteria: ['当日无日线'], training_window: null,
               benchmark_id: 'equal_weight_universe', baseline_policy: '同日候选池等权', allocation_budget_minor: null,
               trial_budget: Number(budget), statistical_plan, stopping_rules };
      } else {
        const [pid, ver] = planKey.split(':');
        const plan = await get<DecisionPlan>(`/api/v1/plans/${pid}/versions/${ver}`);
        const fp = arch?.fingerprint ?? await sha256Hex('daily_bar_availability_policy_v1');
        const s = reg.find(x => x.seat_id === plan.primary_strategy_id)!;
        mi = { target_type: 'plan', target_id: pid, target_version: ver, dataset_fingerprint: fp, rule_version: s.rule_version,
               calendar_version: arch?.calendar_version ?? 'provider_calendar', fee_model_version: plan.fee_model_version ?? 'legacy-paper-fee-v1',
               execution_model_version: 'plan_prospective_v1', seed: Number(seed),
               window: { start_session_date: start || plan.evaluation_window.start_session_date, end_session_date: end || plan.evaluation_window.end_session_date },
               split_policy: 'holdout', hypothesis: hyp || plan.hypothesis || plan.title, skill_sha256: s.skill_sha256, code_revision: 'ui',
               universe_policy_id: 'universe_cn_mainboard_v1', universe_fingerprint: '0'.repeat(64),
               sample_selection_policy: `single_plan horizon=${horizon}`, inclusion_criteria: plan.symbols,
               exclusion_criteria: ['停牌日不计'], training_window: null, benchmark_id: 'equal_weight_universe',
               baseline_policy: '冻结时的候选池等权', allocation_budget_minor: null, trial_budget: Number(budget), statistical_plan, stopping_rules };
      }
      const j = await runJob('/api/v1/validations', { evaluation_manifest: mi }, newKey('validation'), setJob);
      await app.refreshIndex();
      go('validation', j.resource_id);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  if (!arch && kind === 'seat') return <Empty title="服务端没有历史归档">候选研究需要只读历史日线归档。</Empty>;
  return (
    <div className="panel">
      <h3>登记一项验证</h3>
      <p className="note">登记在排队前冻结：之后不能换票、改窗口、调参数或改预算。要改就重新登记一项。</p>
      <div className="row" role="radiogroup" aria-label="类别">
        <button className={`btn small ${kind === 'seat' ? '' : 'secondary'}`} role="radio" aria-checked={kind === 'seat'} onClick={() => setKind('seat')}>候选研究（历史归档）</button>
        <button className={`btn small ${kind === 'plan' ? '' : 'secondary'}`} role="radio" aria-checked={kind === 'plan'} onClick={() => setKind('plan')}>前瞻登记（冻结计划）</button>
      </div>
      <div className="form-grid" style={{ marginTop: 8 }}>
        {kind === 'seat' ? <Field label="席位"><select value={seat} onChange={e => setSeat(e.target.value)}>
          {reg.map(s => <option key={s.seat_id} value={s.seat_id}>{s.name}</option>)}</select></Field>
          : <Field label="冻结计划"><select value={planKey} onChange={e => setPlanKey(e.target.value)}>
            <option value="">选择计划</option>{frozen.map(p => <option key={p.plan_id} value={`${p.plan_id}:${p.version}`}>{p.title} v{p.version}</option>)}</select></Field>}
        <Field label="窗口开始" hint={kind === 'seat' && arch ? `归档 ${arch.coverage_start} 起` : '默认用计划的评估窗口'}><input type="date" value={start} onChange={e => setStart(e.target.value)} /></Field>
        <Field label="窗口结束" hint={kind === 'seat' && arch ? `归档到 ${arch.coverage_end}` : undefined}><input type="date" value={end} onChange={e => setEnd(e.target.value)} /></Field>
        {kind === 'seat' ? <Field label="每隔几个交易日取一个样本"><input type="number" min={1} value={every} onChange={e => setEvery(e.target.value)} /></Field> : null}
        <Field label="持有交易日（标签窗口）"><input type="number" min={1} value={horizon} onChange={e => setHorizon(e.target.value)} /></Field>
        <Field label="预算：最多调度次数"><input type="number" min={1} value={budget} onChange={e => setBudget(e.target.value)} /></Field>
        <Field label="随机种子"><input type="number" min={0} value={seed} onChange={e => setSeed(e.target.value)} /></Field>
        <Field label="HAC 滞后"><input type="number" min={1} value={lag} onChange={e => setLag(e.target.value)} /></Field>
        <Field label="bootstrap 块长"><input type="number" min={1} value={block} onChange={e => setBlock(e.target.value)} /></Field>
      </div>
      <Field label="假设（事前写下）"><textarea value={hyp} onChange={e => setHyp(e.target.value)} /></Field>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn seal" disabled={busy || (kind === 'seat' ? !start || !end : !planKey)} onClick={submit}>冻结登记并排队</button>
        <JobProgress job={busy ? job : null} />
      </div>
      <ErrorNote error={err} />
    </div>
  );
}

export function ValidationPage({ id }: { id: string | null }) {
  const app = useApp();
  if (id) return <ValidationDetail id={id} />;
  const vals = app.index?.validations ?? [];
  const cand = vals.filter(v => v.target_type === 'seat');
  const pros = vals.filter(v => v.target_type !== 'seat');
  const reviews = (app.index?.reviews ?? []).filter((r: any) => r.review_kind === 'post_trade');
  const list = (xs: typeof vals) => xs.length ? (
    <ul className="timeline">{xs.map(v => <li key={v.validation_id}><a href={href('validation', v.validation_id)}>{v.validation_id}</a>
      {' '}{v.target_type === 'seat' ? seatName(v.target_id, app.registry?.seats) : v.target_id}：{v.state}，{dateOnly(v.created_at)}</li>)}</ul>
  ) : <p className="note">还没有。</p>;
  return (
    <>
      <div className="ws-head"><h1>方法验证</h1><p>候选研究、执行模拟、前瞻登记三类结果分开看，不放在同一张收益排名里。</p></div>
      <div className="grid-2">
        <div className="stack">
          <Section title="候选研究" hint="冻结截面上挑出的样本之后怎样变化；不是个人执行收益">{list(cand)}</Section>
          <Section title="执行模拟" hint="来自有成交证据的事后复盘">
            {reviews.length ? <ul className="timeline">{reviews.map((r: any) => <li key={r.review_id}>
              <a href={href('plans', r.plan_id, String(r.plan_version))}>{r.plan_id} v{r.plan_version}</a> 的事后复盘，{shanghai(r.created_at)}</li>)}</ul>
              : <p className="note">还没有事后复盘。执行收益只来自模拟成交事件；没有成交就是空，不用冻结价虚拟入场。</p>}
          </Section>
          <Section title="前瞻登记" hint="冻结后等窗口结束；未到期不出结论">{list(pros)}</Section>
          <Section title="历史资料（待复核）"><LegacyMaterial /></Section>
        </div>
        <div className="stack"><NewValidation /><p className="note">登记时间：{shanghai(nowIso())}（以服务端冻结时间为准）</p></div>
      </div>
    </>
  );
}
