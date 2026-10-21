import { useMemo, useRef, useState } from 'react';
import { get, newKey, post } from '../api/v1';
import type { CardDeck, ListPage, SeatDefinition, SeatRun } from '../api/types';
import { Empty, ErrorNote, Field, KV, Section, Tag } from '../components/ui';
import { LlmSettingsDialog, type LlmStatus } from '../components/LlmSettings';
import { seatAvatar } from '../components/SeatRunCard';
import { useApp, useLoad, useLocalState } from '../lib/hooks';
import { seatStatusLabel, seatStatusTone } from '../lib/labels';
import { go, href } from '../lib/route';
import { downloadJson, nowIso, shanghai, shortHash } from '../lib/format';
import capsJson from '../data/seat-capabilities.json';

interface Cap { name: string; job: string; script: string; outputs: string; a_level: string[]; b_level: string[]; c_level: string[];
  undecidable: string[]; deviations: string[]; source: string; avatar: string | null }
const CAPS = (capsJson as unknown as { seats: Record<string, Cap> }).seats;
const ROLE: Record<string, string> = { selector: '出研究候选', context: '环境判断', reviewer: '纪律审查', confirmer: '形态确认', mixed: '周期与角色' };
const HOR: Record<string, string> = { short: '短线', swing: '波段', long: '长期' };

function Avatar({ id, name }: { id: string; name: string }) {
  const src = seatAvatar(id);
  const [bad, setBad] = useState(false);
  return src && !bad ? <img src={src} alt="" onError={() => setBad(true)} /> : <span className="initial" aria-hidden="true">{name.slice(0, 1)}</span>;
}

interface SeatAnswer {
  status: string; seat_name: string; used_search: boolean; tokens: number | null;
  answer: { answer: string; facts_used: string[]; kb_refs: { kb_id: string; quote: string; locator: string; book: string | null; kind?: string }[];
    uncertainties: string[]; missing_inputs: string[]; action: string };
  audit: { model_run_id: string };
}

/** 工具调用的一步（ADR-012）：它自己决定查什么，每一步都留审计。 */
interface AgentStep { n: number; tool: string; args: Record<string, unknown>; ok: boolean; summary: string }
interface AgentOut extends SeatAnswer { mode?: string; steps?: AgentStep[] }

/**
 * 问这一席：默认「自己查资料」——模型可以在步数内检索自己的书、读技能原文、看规则结论、必要时跑一遍自己的脚本，
 * 最后结论仍要带可回查的引用，否则后端拒收。也可以按次切换成单轮问答。
 */
function AskSeat({ seat }: { seat: SeatDefinition }) {
  const app = useApp();
  const st = useLoad(() => get<LlmStatus>('/api/v1/llm/status'), []);
  const [q, setQ] = useState('');
  const [agent, setAgent] = useState(true);
  const [search, setSearch] = useState(false);
  const [res, setRes] = useState<AgentOut | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [cfgOpen, setCfgOpen] = useState(false);
  const [bindingBusy, setBindingBusy] = useState(false);
  const enabled = st.data?.enabled;
  const bound = st.data?.seat_bindings?.[seat.seat_id]?.profile_id ?? '';
  const boundProfile = bound ? (st.data?.profiles ?? []).find(p => p.id === bound) : undefined;
  const usable = boundProfile ? boundProfile.enabled : enabled;
  const whyNot = boundProfile ? boundProfile.reason : st.data?.reason;
  const effectiveName = bound
    ? (st.data?.profiles ?? []).find(p => p.id === bound)?.name ?? '这一席的存档'
    : (st.data?.profile_name ?? '当前存档');
  const pickModel = async (value: string) => {
    setBindingBusy(true); setErr(null);
    try {
      await post('/api/v1/llm/seat-model', { seat_id: seat.seat_id, profile_id: value || null }, newKey('seat-model'));
      st.reload();
    } catch (e) { setErr(e); } finally { setBindingBusy(false); }
  };
  const ask = async () => {
    if (!q.trim()) return;
    setBusy(true); setErr(null); setRes(null);
    try {
      const snapshot_id = app.snapshot?.snapshot_id ?? null;
      const { data } = agent
        ? await post<AgentOut>(`/api/v1/seats/${seat.seat_id}/chat`,
            { question: q.trim(), snapshot_id, max_steps: 4 }, newKey('seat-ask'))
        : await post<AgentOut>(`/api/v1/seats/${seat.seat_id}/ask`,
            { question: q.trim(), snapshot_id, use_search: search }, newKey('seat-ask'));
      setRes(data);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (
    <Section title="问这一席" hint="用自己的书回答；每条结论要能回到书里的句子"
             actions={<button className="btn quiet small" onClick={() => setCfgOpen(true)} data-testid="open-llm-settings">模型设置</button>}>
      {usable === false ? (
        <p className="note" data-testid="llm-disabled">模型未启用（{whyNot}）。点右上「模型设置」选一份存档、把密钥填进去就行
          （也可以继续用环境变量 <code>DEEPSEEK_API_KEY</code>）；默认关闭，每次提问才会联网调用一次。</p>
      ) : (
        <div className="form" data-testid="ask-seat">
          <div className="form-grid">
            <Field label="这一席用哪个模型" hint={`当前生效：${effectiveName}；不选就是跟随全局`}>
              <select value={bound} disabled={bindingBusy} onChange={e => pickModel(e.target.value)}
                      data-testid="seat-model">
                <option value="">跟随全局（{st.data?.profile_name ?? '当前存档'}）</option>
                {(st.data?.profiles ?? []).map(p => <option key={p.id} value={p.id}>{p.name}{p.enabled ? '' : '（不可用）'}</option>)}
              </select>
            </Field>
            <Field label="问一个问题" hint="例如：这只票现在符合你的哪个买点？哪里不成立？">
              <input type="text" value={q} onChange={e => setQ(e.target.value)} placeholder="按你的方法，这只票现在是什么状态？" />
            </Field>
          </div>
          <div className="row">
            <label className="check"><input type="checkbox" checked={agent} onChange={e => setAgent(e.target.checked)}
                                            data-testid="ask-agent" />
              让它自己查资料（多步）：检索自己的书、读技能原文、看规则结论，必要时跑一遍自己的脚本</label>
          </div>
          {!agent ? (
            <div className="row">
              <label className="check"><input type="checkbox" checked={search} disabled={st.data?.search_available === false}
                                              onChange={e => setSearch(e.target.checked)} />
                允许联网检索（补两融/北向/外围等未接入的数据，结果标注为未核验）
                {st.data?.search_available === false ? <span className="note">当前模型供应商不支持搜索，换回 deepseek 或另配搜索源</span> : null}</label>
            </div>
          ) : null}
          <div className="row">
            <button className="btn" disabled={busy || !q.trim() || !usable} onClick={ask} data-testid="ask-submit">
              {busy ? (agent ? '在查资料…（最多 4 步）' : '在写…（会调用一次模型）') : (agent ? '提问（它会先查资料）' : '提问（调用模型）')}</button>
            {enabled ? <span className="note">这一席当前用「{effectiveName}」；今日已用 {st.data?.budget.used ?? 0} / {st.data?.budget.limit ?? 0} tokens</span> : null}
          </div>
          <ErrorNote error={err} />
          {res ? (
            <div className="panel" data-testid="ask-answer">
              {res.steps?.length ? (
                <details className="more" data-testid="ask-steps">
                  <summary>它查了 {res.steps.length} 步（每一步都有审计）</summary>
                  <ol style={{ paddingLeft: 20, fontSize: 'var(--fs-1)' }}>
                    {res.steps.map(s => <li key={s.n} className={s.ok ? '' : 'unknown'}>
                      <code>{s.tool}</code>：{s.summary}{s.ok ? '' : '（没成功，它自己换了办法）'}</li>)}
                  </ol>
                </details>
              ) : null}
              <p className="lede" style={{ marginTop: 0 }}>{res.answer.answer}</p>
              {res.answer.kb_refs.length ? (
                <details className="more" open><summary>依据（{res.answer.kb_refs.length} 条，可回查）</summary>
                  <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>
                    {res.answer.kb_refs.map(r => <li key={r.kb_id}><strong>{r.book ?? r.kind}</strong> <code>{r.locator}</code>
                      {r.quote ? <div className="note">“{r.quote}”</div> : null}</li>)}
                  </ul>
                </details>
              ) : null}
              {res.answer.facts_used.length ? <p className="note">引用的事实：{res.answer.facts_used.join('、')}</p> : null}
              {res.answer.missing_inputs.length ? <p className="unknown">缺的数据：{res.answer.missing_inputs.join('、')}（这些条件本次判断不了）</p> : null}
              {res.answer.uncertainties.length ? <p className="note">不确定：{res.answer.uncertainties.join('；')}</p> : null}
              <p className="note">审计 {res.audit.model_run_id} · {res.tokens ?? 0} tokens
                {res.mode === 'agent' ? ` · ${res.steps?.length ?? 0} 次工具调用` : ''}{res.used_search ? ' · 用了联网检索（未核验）' : ''}</p>
            </div>
          ) : null}
        </div>
      )}
      <LlmSettingsDialog open={cfgOpen} onClose={() => setCfgOpen(false)} onSaved={() => st.reload()} />
    </Section>
  );
}

function SeatDetail({ s }: { s: SeatDefinition }) {
  const cap = CAPS[s.seat_id];
  const runs = useLoad(() => get<ListPage<SeatRun>>('/api/v1/seat-runs?limit=100'), [s.seat_id]);
  const mine = (runs.data?.items ?? []).filter(r => r.seat_id === s.seat_id && r.round === 'initial').slice(0, 8);
  return (
    <div className="stack" data-testid="seat-detail" data-seat-id={s.seat_id}>
      <div className="ws-head"><h1>{s.name}</h1><p>{cap?.job}</p></div>
      <div className="grid-2">
        <div className="stack">
          <Section title="能判断什么，不能判断什么">
            <div className="grid-cols">
              <div className="panel"><h3>A 级：日线 / 日终可判</h3><ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>{(cap?.a_level ?? []).map(x => <li key={x}>{x}</li>)}</ul></div>
              <div className="panel"><h3>B 级：需要分钟线（本环境待核）</h3>{cap?.b_level?.length ? <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }} className="unknown">{cap.b_level.map(x => <li key={x}>{x}</li>)}</ul> : <p className="note">无</p>}</div>
              <div className="panel"><h3>C 级：需要 Level2 / 逐笔（本环境待核）</h3>{cap?.c_level?.length ? <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }} className="unknown">{cap.c_level.map(x => <li key={x}>{x}</li>)}</ul> : <p className="note">无</p>}</div>
              <div className="panel"><h3>判不了的与实现偏差</h3><ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>{[...(cap?.undecidable ?? []), ...(cap?.deviations ?? [])].map(x => <li key={x}>{x}</li>)}</ul></div>
            </div>
          </Section>
          <Section title="最近的运行" hint="只列本机记录">
            {mine.length ? <ul className="timeline">{mine.map(r => (
              <li key={r.seat_run_id}><Tag tone={seatStatusTone(r)}>{seatStatusLabel(r)}</Tag> {shanghai(r.created_at)}
                {r.case_id ? <> <a href={href('cases', r.case_id)}>查看 Case</a></> : null}</li>
            ))}</ul> : <p className="note">还没有运行过。</p>}
          </Section>
        </div>
        <div className="stack">
          <AskSeat seat={s} />
          <SeatAnalyze seat={s} />
          <div className="panel">
            <KV items={[['资料', cap?.source ?? '--'], ['输出', cap?.outputs ?? '--'], ['职责', ROLE[s.role] ?? s.role],
              ['适用周期', s.horizons.map(h => HOR[h] ?? h).join('、')], ['必需输入', s.required_inputs.join('、')],
              ['规则版本', s.rule_version], ['技能目录哈希', <code>{shortHash(s.skill_sha256)}</code>], ['脚本', cap?.script ?? '--']]} />
            <p className="note">人物名是方法标签，不代表作者本人参与或背书；资料存在不等于实现忠实，实现能跑不等于有收益优势。</p>
          </div>
        </div>
      </div>
    </div>
  );
}

function DeckView({ id }: { id: string }) {
  const d = useLoad(() => get<CardDeck>(`/api/v1/decks/${id}`), [id]);
  const [flipped, setFlipped] = useState<Record<string, boolean>>({});
  const [done, setDone] = useLocalState<Record<string, boolean>>('rt.learning.decks.v1', {});
  if (d.error) return <ErrorNote error={d.error} title="读取卡组失败" />;
  if (!d.data) return <p className="note">读取中…</p>;
  const deck = d.data;
  const src = deck.source_type === 'case' ? href('cases', deck.source_id) : deck.source_type === 'plan'
    ? href('plans', deck.source_id, deck.source_version) : null;
  return (
    <div className="stack" data-testid="deck-view" data-deck-id={deck.deck_id}>
      <div className="ws-head"><h1>{deck.title}</h1>
        <p>来自{deck.source_type === 'case' ? '案例研究' : deck.source_type === 'plan' ? `计划 v${deck.source_version}` : '复盘'}
          {src ? <> <a href={src} data-testid="deck-source">打开来源</a></> : <>（{deck.source_id}）</>}；第 {deck.version} 版，{shanghai(deck.created_at)}</p></div>
      <div className="grid-cols">
        {deck.cards.map(c => (
          <div key={c.card_id} className="flash" data-testid="flash-card">
            <div className="front">{c.front}</div>
            {flipped[c.card_id] ? <div className="back">{c.back}</div> : null}
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn secondary small" aria-expanded={!!flipped[c.card_id]}
                      onClick={() => setFlipped({ ...flipped, [c.card_id]: !flipped[c.card_id] })}>{flipped[c.card_id] ? '收起' : '看答案'}</button>
              <span className="note" data-testid="card-evidence">证据 {c.evidence_ids.length} 条{c.evidence_ids.length ? `：${c.evidence_ids.slice(0, 2).join('、')}` : ''}</span>
            </div>
          </div>
        ))}
      </div>
      <label className="check"><input type="checkbox" checked={!!done[deck.deck_id]} onChange={e => setDone({ ...done, [deck.deck_id]: e.target.checked })} />
        我复习完这组卡了（只记在本机，和交易次数、盈亏无关）</label>
      <div className="row">
        <button className="btn quiet small" data-testid="export-deck"
                onClick={() => downloadJson(`${deck.deck_id}-v${deck.version}.json`, deck)}>导出这组卡（JSON）</button>
        <span className="note">卡组存在本机数据库里，随运行目录备份与恢复；来源没变时重新生成会得到同一版本。</span>
      </div>
    </div>
  );
}

/** 本机学习记录（收藏、组合、已读、复习完成、来源纠错）：只在浏览器 localStorage，可导出成文件、从文件恢复。 */
const LEARNING_KEYS = {
  favorites: 'rt.seat_favorites.v1', combo: 'rt.seat_combo.v1', read: 'rt.learning.read.v1',
  decks_done: 'rt.learning.decks.v1', source_fixes: 'rt.learning.source_fixes.v1',
} as const;
type LearningKey = keyof typeof LEARNING_KEYS;
const LEARNING_SHAPE: Record<LearningKey, 'string[]' | 'flags'> = {
  favorites: 'string[]', combo: 'string[]', read: 'flags', decks_done: 'flags', source_fixes: 'string[]',
};

function readLearning(): Record<LearningKey, unknown> {
  const out = {} as Record<LearningKey, unknown>;
  for (const k of Object.keys(LEARNING_KEYS) as LearningKey[]) {
    try { out[k] = JSON.parse(window.localStorage.getItem(LEARNING_KEYS[k]) ?? 'null'); } catch { out[k] = null; }
  }
  return out;
}

function validLearning(k: LearningKey, v: unknown): boolean {
  if (LEARNING_SHAPE[k] === 'string[]') return Array.isArray(v) && v.every(x => typeof x === 'string' && x.length <= 500) && v.length <= 2000;
  return !!v && typeof v === 'object' && !Array.isArray(v) && Object.entries(v as object).every(([a, b]) => a.length <= 200 && typeof b === 'boolean');
}

function LearningBackup() {
  const [msg, setMsg] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const restore = async (f: File) => {
    setMsg(null);
    try {
      if (f.size > 1 << 20) throw new Error('文件超过 1MB');
      const obj = JSON.parse(await f.text()) as { schema?: string; data?: Record<string, unknown> };
      if (obj.schema !== 'roundtable_learning_v1' || !obj.data) throw new Error('不是学习记录文件（schema 应为 roundtable_learning_v1）');
      const ok: LearningKey[] = [];
      const skipped: string[] = [];
      for (const k of Object.keys(LEARNING_KEYS) as LearningKey[]) {
        if (!(k in obj.data)) continue;
        if (validLearning(k, obj.data[k])) { window.localStorage.setItem(LEARNING_KEYS[k], JSON.stringify(obj.data[k])); ok.push(k); }
        else skipped.push(k);
      }
      setMsg(`已恢复 ${ok.length} 项${skipped.length ? `，格式不对跳过 ${skipped.join('、')}` : ''}。刷新页面后生效。`);
    } catch (e) { setMsg(`恢复失败：${(e as Error).message}`); }
  };
  return (
    <div className="row" style={{ marginTop: 8 }}>
      <button className="btn quiet small" data-testid="export-learning"
              onClick={() => downloadJson('roundtable-learning.json', { schema: 'roundtable_learning_v1', exported_at: nowIso(), data: readLearning() })}>
        导出学习记录</button>
      <button className="btn quiet small" data-testid="restore-learning" onClick={() => fileRef.current?.click()}>从文件恢复</button>
      <input ref={fileRef} type="file" accept="application/json,.json" hidden aria-hidden="true" tabIndex={-1}
             onChange={e => { const f = e.target.files?.[0]; if (f) void restore(f); e.target.value = ''; }} />
      {msg ? <span className="note" role="status">{msg}</span> : null}
    </div>
  );
}

// ── M3 个股分析：规则先跑，模型只解释，并必须表态（一致 / 分歧 / 数据不够） ──
interface AnalyzeOut {
  status: string; seat_name: string; symbol: string; model: string; tokens: number | null;
  rule: { seat_run_id: string; symbol: string; verdict: string; verdict_label: string; applicability: string;
    applicability_reason: string; explanation: string; counter_evidence: string[];
    conditions: { condition_id: string; description: string; result: string; missing_inputs: string[] }[];
    missing_inputs: string[] };
  answer: SeatAnswer['answer'] & { stance: string; stance_label: string; disagreements: string[] };
  audit: { model_run_id: string };
}

const VERDICT_TONE: Record<string, 'ok' | 'bad' | 'warn' | 'info' | 'muted'> = {
  conditions_met: 'ok', conditions_failed: 'bad', watch: 'info', unknown: 'muted' };
const STANCE_TONE: Record<string, 'ok' | 'bad' | 'warn'> = { consistent: 'ok', divergent: 'bad', insufficient: 'warn' };
const COND_LABEL: Record<string, string> = { met: '满足', failed: '不满足', unknown: '判不了', inapplicable: '不适用' };

function SeatAnalyze({ seat }: { seat: SeatDefinition }) {
  const st = useLoad(() => get<LlmStatus>('/api/v1/llm/status'), []);
  const runs = useLoad(() => get<ListPage<SeatRun>>('/api/v1/seat-runs?limit=100'), [seat.seat_id]);
  const [runId, setRunId] = useState('');
  const [symbol, setSymbol] = useState('');
  const [res, setRes] = useState<AnalyzeOut | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const bound = st.data?.seat_bindings?.[seat.seat_id]?.profile_id ?? '';
  const boundProfile = bound ? (st.data?.profiles ?? []).find(p => p.id === bound) : undefined;
  const llmOn = boundProfile ? boundProfile.enabled : st.data?.enabled;
  const llmWhy = boundProfile ? boundProfile.reason : st.data?.reason;
  const usable = (runs.data?.items ?? []).filter(r => r.seat_id === seat.seat_id
    && (r.run_state === 'succeeded' || r.run_state === 'partial'));
  const run = usable.find(r => r.seat_run_id === runId) ?? usable[0] ?? null;
  const symbols = run ? (run.symbols?.length ? run.symbols : (run.candidates ?? []).map(c => c.symbol)) : [];
  const sym = symbol && symbols.includes(symbol) ? symbol : (symbols[0] ?? '');
  const run_ = async () => {
    if (!run || !sym) return;
    setBusy(true); setErr(null); setRes(null);
    try {
      const { data } = await post<AnalyzeOut>(`/api/v1/seats/${seat.seat_id}/analyze`,
        { seat_run_id: run.seat_run_id, symbol: sym }, newKey('seat-analyze'));
      setRes(data);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (
    <Section title="个股分析" hint="规则先跑完，模型只解释：必须给出 一致 / 分歧 / 数据不够"
             actions={<a className="btn quiet small" href={href('cases', 'new')}>先跑一次（新建案例）</a>}>
      <div data-testid="seat-analyze">
        {st.data && llmOn === false ? (
          <p className="note" data-testid="analyze-disabled">模型未启用（{llmWhy}）。点「问这一席」右上角的「模型设置」选一份存档并填密钥即可；
            规则结论本身不需要模型，照常可看。</p>
        ) : null}
        {!run ? (
          <p className="note" data-testid="analyze-no-run">这一席还没有跑完的运行：先在「案例研究」里新建一个问题（会跑选中的席位），
            回来就能把规则结论和席位解读并排看。</p>
        ) : (
          <>
            <div className="row">
              <Field label="哪一次运行">
                <select value={run.seat_run_id} onChange={e => { setRunId(e.target.value); setRes(null); }}
                        data-testid="analyze-run">
                  {usable.map(r => <option key={r.seat_run_id} value={r.seat_run_id}>
                    {shanghai(r.created_at)} · {seatStatusLabel(r)} · {(r.symbols ?? []).join(' ') || `${(r.candidates ?? []).length} 只候选`}
                  </option>)}
                </select>
              </Field>
              <Field label="哪只票">
                <select value={sym} onChange={e => { setSymbol(e.target.value); setRes(null); }} data-testid="analyze-symbol">
                  {symbols.map(s => <option key={s} value={s}>{s}</option>)}
                </select>
              </Field>
              {symbols.length === 0 ? <span className="note">这次运行没有记录研究标的（旧版本运行），换一次运行试试。</span> : null}
              <button className="btn" disabled={busy || !sym || llmOn === false} onClick={run_}
                      data-testid="analyze-run-btn">{busy ? '在解释…（调用一次模型）' : '对照分析（调用模型）'}</button>
            </div>
            <ErrorNote error={err} />
            {res ? (
              <div className="grid-2" data-testid="seat-analyze-out" style={{ marginTop: 10 }}>
                <div className="panel" data-testid="analyze-rule">
                  <div className="row spread"><h3 style={{ margin: 0 }}>规则结论（{res.rule.symbol}）</h3>
                    <Tag tone={VERDICT_TONE[res.rule.verdict] ?? 'muted'}>{res.rule.verdict_label}</Tag></div>
                  <p className="note">适用性：{res.rule.applicability}（{res.rule.applicability_reason}）</p>
                  <ul className="conds">
                    {res.rule.conditions.map(c => (
                      <li key={c.condition_id}><span className={`res ${c.result}`}>{COND_LABEL[c.result] ?? c.result}</span>
                        <span>{c.description}</span></li>
                    ))}
                  </ul>
                  {res.rule.explanation ? <p className="note">{res.rule.explanation}</p> : null}
                  {res.rule.counter_evidence.length
                    ? <p className="unknown">规则自带的反证/待核：{res.rule.counter_evidence.join('；')}</p> : null}
                </div>
                <div className="panel" data-testid="analyze-answer">
                  <div className="row spread"><h3 style={{ margin: 0 }}>席位解读（{res.seat_name}）</h3>
                    <Tag tone={STANCE_TONE[res.answer.stance] ?? 'info'} testid="analyze-stance">{res.answer.stance_label}</Tag></div>
                  <p className="lede" style={{ marginTop: 4 }}>{res.answer.answer}</p>
                  {res.answer.disagreements.length ? (
                    <div><strong>分歧</strong><ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>
                      {res.answer.disagreements.map(x => <li key={x}>{x}</li>)}</ul></div>
                  ) : null}
                  {res.answer.kb_refs.length ? (
                    <details className="more"><summary>依据（{res.answer.kb_refs.length} 条，可回查）</summary>
                      <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>
                        {res.answer.kb_refs.map(r => <li key={r.kb_id}><strong>{r.book ?? '资料'}</strong> <code>{r.locator}</code>
                          {r.quote ? <div className="note">“{r.quote}”</div> : null}</li>)}
                      </ul>
                    </details>
                  ) : null}
                  {res.answer.missing_inputs.length
                    ? <p className="unknown">缺的数据：{res.answer.missing_inputs.join('、')}（这些条件本次判断不了）</p> : null}
                  {res.answer.uncertainties.length
                    ? <p className="note">不确定：{res.answer.uncertainties.join('；')}</p> : null}
                  <p className="note">审计 {res.audit.model_run_id} · {res.tokens ?? 0} tokens</p>
                </div>
              </div>
            ) : null}
          </>
        )}
      </div>
    </Section>
  );
}

// ── M4 圆桌：几席一起看同一个问题（并列结论，不投票、不折中） ──
interface RoundOut {
  status: string; question: string; note: string; tokens: number;
  seats: (SeatAnswer & { seat_id: string; role: string; role_label: string })[];
}

export function RoundtableView() {
  const app = useApp();
  const reg = app.registry?.seats ?? [];
  const st = useLoad(() => get<LlmStatus>('/api/v1/llm/status'), []);
  const [picked, setPicked] = useState<string[]>(() => {
    // 从「独立策略席位」抽屉里的「带上这一席去圆桌」过来时，已经替你选好了
    try {
      const raw = JSON.parse(sessionStorage.getItem('rt.roundtable.picks') || '[]');
      return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string').slice(0, 4) : [];
    } catch { return []; }
  });
  const [q, setQ] = useState('');
  const [search, setSearch] = useState(false);
  const [res, setRes] = useState<RoundOut | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const ROLES = ['primary', 'validator', 'context', 'discipline'];
  const ROLE_CN: Record<string, string> = { primary: '主方法', validator: '反证', context: '环境背景', discipline: '纪律' };
  const toggle = (id: string) => setPicked(p => p.includes(id) ? p.filter(x => x !== id) : (p.length >= 4 ? p : [...p, id]));
  const seatOn = (id: string) => {
    const pid = st.data?.seat_bindings?.[id]?.profile_id ?? '';
    const prof = pid ? (st.data?.profiles ?? []).find(x => x.id === pid) : undefined;
    return prof ? prof.enabled : !!st.data?.enabled;
  };
  const allOn = picked.length >= 2 && picked.every(seatOn);
  const run = async () => {
    if (!q.trim() || picked.length < 2) return;
    setBusy(true); setErr(null); setRes(null);
    try {
      const { data } = await post<RoundOut>('/api/v1/roundtable/sessions', {
        question: q.trim(), seats: picked.map((id, i) => ({ seat_id: id, role: ROLES[i] ?? 'context' })),
        snapshot_id: app.snapshot?.snapshot_id ?? null, use_search: search }, newKey('roundtable'));
      setRes(data);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (
    <div className="stack" data-testid="roundtable">
      <div className="ws-head"><h1>圆桌</h1><p>让 2–4 席看同一个问题：按顺序各答一轮，结论并列。<strong>不投票、不折中</strong>，分歧由你自己判断。</p></div>
      <Section title="选席位与问题" hint="第一位当主方法，第二位当反证，其余按环境背景/纪律">
        <div data-testid="roundtable-form">
          {st.data && picked.length >= 2 && !allOn ? (
            <p className="note" data-testid="roundtable-disabled">有席位没配好模型（看「问这一席」里的选择）。先点「问这一席」右上角「模型设置」配一份存档。</p>
          ) : null}
          <div className="grid-cols">
            {reg.map(s => (
              <label key={s.seat_id} className={`check ${picked.includes(s.seat_id) ? 'is-on' : ''}`}>
                <input type="checkbox" checked={picked.includes(s.seat_id)} disabled={!picked.includes(s.seat_id) && picked.length >= 4}
                       onChange={() => toggle(s.seat_id)} data-testid={`roundtable-seat-${s.seat_id}`} />
                <span>{s.name}<span className="note"> {picked.includes(s.seat_id) ? ROLE_CN[ROLES[picked.indexOf(s.seat_id)] ?? 'context'] : ''}</span></span>
              </label>
            ))}
          </div>
          <Field label="问题" hint="例如：现在这个位置，威科夫会说等什么，利弗莫尔会说缺什么？">
            <input type="text" value={q} onChange={e => setQ(e.target.value)}
                   placeholder="按各自的方法看，这只票现在是什么状态？" data-testid="roundtable-question" />
          </Field>
          <div className="row">
            <label className="check"><input type="checkbox" checked={search} disabled={st.data?.search_available === false}
              onChange={e => setSearch(e.target.checked)} />允许联网检索（只作背景，引用须注明未核验）</label>
            <button className="btn" disabled={busy || picked.length < 2 || !q.trim() || !allOn}
                    onClick={run} data-testid="roundtable-run">
              {busy ? `在开会…（${picked.length} 席各调用一次模型）` : `开一次圆桌（${picked.length} 席）`}</button>
            <span className="note">已选 {picked.length}/4 席；至少 2 席。</span>
          </div>
          <ErrorNote error={err} />
        </div>
      </Section>
      {res ? (
        <Section title="会议记录" hint={res.note}>
          <div className="stack" data-testid="roundtable-out">
            <p className="lede" style={{ marginTop: 0 }}>{res.question}</p>
            {res.seats.map(s => (
              <div key={s.seat_id} className="panel" data-testid={`roundtable-answer-${s.seat_id}`}>
                <div className="row spread">
                  <h3 style={{ margin: 0 }}>{s.seat_name} <span className="note">{s.role_label}</span></h3>
                  <span className="note">{s.tokens ?? 0} tokens</span>
                </div>
                <p className="lede" style={{ margin: '6px 0' }}>{s.answer.answer}</p>
                {s.answer.kb_refs.length ? (
                  <details className="more"><summary>依据（{s.answer.kb_refs.length} 条）</summary>
                    <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>
                      {s.answer.kb_refs.map(r => <li key={r.kb_id}><strong>{r.book ?? '资料'}</strong> <code>{r.locator}</code></li>)}
                    </ul>
                  </details>
                ) : null}
                {s.answer.missing_inputs.length ? <p className="unknown">缺的数据：{s.answer.missing_inputs.join('、')}</p> : null}
                {s.answer.uncertainties.length ? <p className="note">保留：{s.answer.uncertainties.join('；')}</p> : null}
              </div>
            ))}
            <div className="panel">
              <h3 style={{ marginTop: 0 }}>各席的保留与缺口（不是投票结果）</h3>
              <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>
                {res.seats.map(s => <li key={s.seat_id}><strong>{s.seat_name}</strong>：
                  {s.answer.missing_inputs.length || s.answer.uncertainties.length
                    ? [...s.answer.missing_inputs, ...s.answer.uncertainties].join('；')
                    : '没有额外保留'}</li>)}
              </ul>
              <p className="note">本次共 {res.tokens} tokens。席位的结论并列呈现，系统不会替你合成一个「综合意见」。</p>
            </div>
          </div>
        </Section>
      ) : null}
    </div>
  );
}

export function SeatsPage({ id }: { id: string | null }) {
  const app = useApp();
  const reg = app.registry?.seats ?? [];
  const [fav, setFav] = useLocalState<string[]>('rt.seat_favorites.v1', []);
  const [read, setRead] = useLocalState<Record<string, boolean>>('rt.learning.read.v1', {});
  const [combo, setCombo] = useLocalState<string[]>('rt.seat_combo.v1', []);
  const [fixes, setFixes] = useLocalState<string[]>('rt.learning.source_fixes.v1', []);
  const [fixDraft, setFixDraft] = useState('');
  const [sort, setSort] = useState<'registry' | 'favorite'>('registry');
  const sorted = useMemo(() => [...reg].sort((a, b) => sort === 'favorite'
    ? (Number(fav.includes(b.seat_id)) - Number(fav.includes(a.seat_id))) || a.registry_order - b.registry_order
    : a.registry_order - b.registry_order), [reg, sort, fav]);
  if (id === 'roundtable') return <RoundtableView />;
  if (id && id.startsWith('deck_')) return <DeckView id={id} />;
  if (id) {
    const s = reg.find(x => x.seat_id === id);
    return s ? <SeatDetail s={s} /> : <Empty title="没有这个席位">{id}</Empty>;
  }
  const reviewable = (app.index?.cases ?? []).filter(c => c.state === 'succeeded' || c.state === 'partial').length;
  const decks = app.index?.decks ?? [];
  return (
    <>
      <div className="ws-head"><h1>席位库</h1><p>13 个方法各自的资料、能判断的范围与缺口。头像只是标签，不代表收益能力。</p>
</div>
      <div className="grid-2">
        <div className="stack">
          <div className="row">
            <span className="note">排序</span>
            <button className={`btn small ${sort === 'registry' ? '' : 'secondary'}`} onClick={() => setSort('registry')}>注册顺序</button>
            <button className={`btn small ${sort === 'favorite' ? '' : 'secondary'}`} onClick={() => setSort('favorite')}>收藏在前</button>
          </div>
          <div className="grid-cols" data-testid="seat-library">
            {sorted.map(s => {
              const cap = CAPS[s.seat_id];
              const unk = (cap?.b_level?.length ?? 0) + (cap?.c_level?.length ?? 0);
              return (
                <article key={s.seat_id} className="seatcard" data-seat-id={s.seat_id}>
                  <Avatar id={s.seat_id} name={s.name} />
                  <div style={{ minWidth: 0 }}>
                    <h3><a href={href('seats', s.seat_id)}>{s.name}</a></h3>
                    <div className="sub">{ROLE[s.role] ?? s.role}；{s.horizons.map(h => HOR[h] ?? h).join('、')}</div>
                    <p className="note" style={{ margin: '4px 0' }}>{cap?.job}</p>
                    {unk ? <div className="unknown" style={{ fontSize: 'var(--fs-1)' }}>本环境判不了的条件 {unk} 条（需分钟线或 Level2）</div> : null}
                    <div className="row" style={{ marginTop: 6 }}>
                      <button className="btn quiet small" aria-pressed={fav.includes(s.seat_id)}
                              onClick={() => setFav(fav.includes(s.seat_id) ? fav.filter(x => x !== s.seat_id) : [...fav, s.seat_id])}>
                        {fav.includes(s.seat_id) ? '已收藏' : '收藏'}</button>
                      <button className="btn quiet small" aria-pressed={combo.includes(s.seat_id)} disabled={!combo.includes(s.seat_id) && combo.length >= 4}
                              onClick={() => setCombo(combo.includes(s.seat_id) ? combo.filter(x => x !== s.seat_id) : [...combo, s.seat_id])}>
                        {combo.includes(s.seat_id) ? '移出组合' : '加入组合'}</button>
                      <label className="check" style={{ fontSize: 'var(--fs-1)' }}><input type="checkbox" checked={!!read[s.seat_id]}
                        onChange={e => setRead({ ...read, [s.seat_id]: e.target.checked })} />已读方法说明</label>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </div>
        <div className="stack">
          <Section title="任务组合" hint="3–4 席；建议互补，不叠加相关性高的方法">
            {combo.length ? <ol style={{ paddingLeft: 18 }}>{combo.map(c => <li key={c}>{CAPS[c]?.name ?? c}</li>)}</ol> : <p className="note">从左边的卡片「加入组合」。</p>}
            <p className="note">组合只是一份研究提纲：新建 Case 时第一个席位默认是主方法，可以再改。换组合不会改变任何已冻结的计划。</p>
            <button className="btn secondary small" disabled={combo.length < 1} onClick={() => go('cases', 'new')}>用组合去新建 Case</button>
          </Section>
          <Section title="复习卡组" hint="由已完成的研究、计划与复盘派生">
            {decks.length ? <ul className="timeline">{decks.map(d => <li key={d.deck_id}><a href={href('seats', d.deck_id)}>{d.title}</a> {shanghai(d.created_at)}</li>)}</ul>
              : <p className="note">在 Case、计划或复盘页点「生成复习卡组」。</p>}
          </Section>
          <Section title="学习记录" hint="只在本机；和交易次数、盈亏无关">
            <KV items={[['已读方法说明', `${Object.values(read).filter(Boolean).length} / 13`], ['完成的可复核案例', reviewable],
              ['纠正的来源错误', fixes.length]]} />
            <div className="row" style={{ marginTop: 8 }}>
              <input type="text" aria-label="记录一处来源错误" value={fixDraft} onChange={e => setFixDraft(e.target.value)} placeholder="记下一处资料出处的错误或疑点" />
              <button className="btn secondary small" disabled={!fixDraft.trim()} onClick={() => { setFixes([...fixes, fixDraft.trim()]); setFixDraft(''); }}>记录</button>
            </div>
            <LearningBackup />
          </Section>
        </div>
      </div>
    </>
  );
}
