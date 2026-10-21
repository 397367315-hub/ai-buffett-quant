import { useMemo, useState } from 'react';
import { errorText, get, newKey, post, runJob, type Job } from '../api/v1';
import type { MarketSnapshot, Metric, Portfolio } from '../api/types';
import { Empty, ErrorNote, Field, JobProgress, Section, Tag } from '../components/ui';
import { LlmSettingsDialog } from '../components/LlmSettings';
import { useApp, useLoad, useLocalState } from '../lib/hooks';
import { changeRatio, formatDecimal, formatMinor, formatRatioPct, formatYi, ratioTone } from '../lib/money';
import { FRESHNESS_LABEL, QUALITY_LABEL, label, missingText } from '../lib/labels';
import { go, href } from '../lib/route';
import { downloadText, nowIso, shanghai } from '../lib/format';

interface MetricDef { metric_id: string; dimension: string; title: string; unit: string; formula: string;
  denominator_policy: string | null; includes_st: boolean | null; note: string }

const DIM_ORDER: [string, string][] = [
  ['index_trend', '指数趋势'], ['breadth', '市场宽度'], ['limit_ecology', '涨跌停与连板生态'],
  ['theme_diffusion', '题材扩散（行业代理）'], ['liquidity', '成交与流动性'], ['volatility', '波动与拥挤'],
  ['coverage', '候选池覆盖'],
];

const INDEX_NAMES: Record<string, string> = {
  '000001.SH': '上证指数', '399001.SZ': '深证成指', '399006.SZ': '创业板指', '000300.SH': '沪深300', '000852.SH': '中证1000',
};

export function metricDefs(snap: MarketSnapshot): Record<string, MetricDef> {
  const out: Record<string, MetricDef> = {};
  for (const e of snap.evidence) {
    if (e.field_path?.startsWith('metric_dictionary.') && e.value && typeof e.value === 'object') {
      const v = e.value as MetricDef;
      out[v.metric_id] = v;
    }
  }
  return out;
}

/** 带方向的比率（涨红跌绿）；覆盖率、绝对值类不着色。 */
const SIGNED = new Set(['index_pct_chg', 'index_ret_20', 'breadth_median_pct_chg', 'prev_limit_up_followthrough']);

/** 方向由指标含义决定，与数值正负无关：A 股习惯「涨/涨停红、跌/跌停绿」。
 *  上涨家数、上涨占比、涨停家数 → 红；下跌家数、跌停家数 → 绿。 */
const UP_METRICS = new Set(['breadth_up_count', 'breadth_up_ratio', 'limit_up_count']);
const DOWN_METRICS = new Set(['breadth_down_count', 'limit_down_count']);

export const BOARD_LABEL: Record<string, string> = {
  SH_MAIN: '沪主板', SZ_MAIN: '深主板', CHINEXT: '创业板', STAR: '科创板', BSE: '北交所', unknown: '板块未知',
};

export function formatMetric(m: Metric): { text: string; tone: string; title?: string } {
  if (m.value === null) return { text: '未提供', tone: 'unknown' };
  const semantic = UP_METRICS.has(m.metric_id) ? 'up' : DOWN_METRICS.has(m.metric_id) ? 'down' : '';
  switch (m.unit) {
    case 'ratio': return { text: formatRatioPct(m.value), tone: semantic || (SIGNED.has(m.metric_id) ? ratioTone(m.value) : '') };
    // 成交额按亿显示（与系统其它地方一致）；精确到分的「元」放在悬停提示里
    case 'money_minor': return { text: formatYi(m.value), tone: '', title: `${formatMinor(m.value)} 元` };
    case 'multiple': return { text: `${formatDecimal(m.value, 3)} 倍`, tone: '' };
    default: return { text: formatDecimal(m.value), tone: semantic };
  }
}

/** 涨跌家数对比条（同花顺那种）：红涨、灰平、绿跌，宽度按家数占比；家数缺失时不画。 */
function BreadthBar({ up, flat, down }: { up: number; flat: number | null; down: number }) {
  const total = up + down + (flat ?? 0);
  if (!total) return null;
  const pct = (n: number) => `${(n / total) * 100}%`;
  return (
    <div className="breadth-bar" data-testid="breadth-bar" role="img"
         data-up={up} data-down={down} data-flat={flat ?? ''}
         aria-label={`上涨 ${up} 家${flat ? `，平盘 ${flat} 家` : ''}，下跌 ${down} 家`}>
      <div className="bb-head">
        <span className="rise">上涨 {up}</span>
        {flat ? <span className="flat">平 {flat}</span> : null}
        <span className="fall">下跌 {down}</span>
      </div>
      <div className="bb-track">
        <i className="bb-up" style={{ width: pct(up) }} title={`上涨 ${up} 家，占 ${pct(up)}`} />
        {flat ? <i className="bb-flat" style={{ width: pct(flat) }} title={`平盘 ${flat} 家，占 ${pct(flat)}`} /> : null}
        <i className="bb-down" style={{ width: pct(down) }} title={`下跌 ${down} 家，占 ${pct(down)}`} />
      </div>
    </div>
  );
}

function Denominators({ snap }: { snap: MarketSnapshot }) {
  const defs = useMemo(() => metricDefs(snap), [snap]);
  const val = (id: string) => snap.metrics.find(m => m.metric_id === id && m.symbol === null)?.value ?? null;
  const envPolicy = defs.breadth_member_count?.denominator_policy ?? 'environment_cn_a_v1';
  const candPolicy = defs.candidate_member_count?.denominator_policy ?? snap.metadata.universe_policy_id;
  return (
    <div className="panel" data-testid="denominators">
      <h3>两个分母，不能混用</h3>
      <div className="denoms">
        <div data-testid="market-denominator" data-universe-id={envPolicy} data-count={val('breadth_member_count') ?? ''}>
          <div className="note">环境宽度（看市场冷暖）</div>
          <div className="denom-n num">{val('breadth_member_count') ?? '未提供'}<small> 只</small></div>
          <div className="note">其中有报价 {val('breadth_quoted_count') ?? '未提供'} 只；<code>{envPolicy}</code>：沪深 A 股（含创业板、科创板），ST 计入并单列</div>
        </div>
        <div data-testid="candidate-denominator" data-universe-id={candPolicy} data-count={val('candidate_member_count') ?? ''}>
          <div className="note">候选股票池（只从这里挑研究对象）</div>
          <div className="denom-n num">{val('candidate_member_count') ?? '未提供'}<small> 只</small></div>
          <div className="note">其中有报价 {val('candidate_quoted_count') ?? '未提供'} 只；<code>{candPolicy}</code>：沪深主板，排除创业板、科创板、北交所、B 股和 ST</div>
        </div>
      </div>
      <p className="note">宽度、涨跌停、连板等指标按环境分母计算；候选数量按候选池计算，两者不是同一个全集。</p>
    </div>
  );
}

function MarketMatrix({ snap }: { snap: MarketSnapshot }) {
  const defs = useMemo(() => metricDefs(snap), [snap]);
  const groups = useMemo(() => {
    const g: Record<string, Metric[]> = {};
    for (const m of snap.metrics) {
      const d = defs[m.metric_id]?.dimension ?? 'other';
      (g[d] ??= []).push(m);
    }
    return g;
  }, [snap, defs]);
  const idx = groups.index_trend ?? [];
  const idxAllMissing = idx.every(m => m.value === null);
  const cell = (sym: string, id: string) => {
    const m = idx.find(x => x.symbol === sym && x.metric_id === id);
    if (!m) return <td className="num unknown">--</td>;
    const f = formatMetric(m);
    return <td className={`num ${f.tone === 'up' ? 'rise' : f.tone === 'down' ? 'fall' : f.tone === 'unknown' ? 'unknown' : ''}`}
               data-metric={id} data-value={m.value ?? ''} title={f.title}>{f.text}</td>;
  };
  return (
    <div className="stack" data-testid="market-matrix">
      <div className="matrix">
        <div className="dim">
          <h3>指数趋势</h3>
          {idxAllMissing ? <p className="unknown" style={{ fontSize: 'var(--fs-1)' }}>未提供：这份快照没有指数日线</p> : (
            <table className="mini"><thead><tr><th>指数</th><th className="num">当日</th><th className="num">20 日</th><th className="num">对 20 日均线</th></tr></thead>
              <tbody>{Object.keys(INDEX_NAMES).map(sym => (
                <tr key={sym}><td>{INDEX_NAMES[sym]}</td>{cell(sym, 'index_pct_chg')}{cell(sym, 'index_ret_20')}{cell(sym, 'index_close_to_ma20')}</tr>
              ))}</tbody></table>
          )}
        </div>
        {DIM_ORDER.filter(([d]) => d !== 'index_trend').map(([dim, dimLabel]) => (
          <div className="dim" key={dim}>
            <h3>{dimLabel}</h3>
            {dim === 'breadth' ? (() => {
              const get = (id: string) => {
                const m = (groups.breadth ?? []).find(x => x.metric_id === id && x.symbol === null);
                return m && m.value !== null ? Number(m.value) : null;
              };
              const up = get('breadth_up_count');
              const down = get('breadth_down_count');
              const quoted = get('breadth_quoted_count');
              if (up === null || down === null || !Number.isFinite(up) || !Number.isFinite(down)) return null;
              const flat = quoted !== null && quoted >= up + down ? quoted - up - down : null;
              return <BreadthBar up={up} flat={flat} down={down} />;
            })() : null}
            <dl className="mrows">
              {(groups[dim] ?? []).map(m => {
                const d = defs[m.metric_id];
                const f = formatMetric(m);
                return (
                  <div key={`${m.metric_id}-${m.symbol ?? ''}`} data-metric={m.metric_id} title={d ? `${d.formula}${d.note ? `；${d.note}` : ''}` : undefined}>
                    <dt>{d?.title ?? m.metric_id}</dt>
                    <dd className={`num ${f.tone === 'up' ? 'rise' : f.tone === 'down' ? 'fall' : f.tone === 'unknown' ? 'unknown' : ''}`}
                        data-value={m.value ?? ''} title={f.title}>{f.text}</dd>
                  </div>
                );
              })}
            </dl>
          </div>
        ))}
      </div>
      <p className="note">矩阵只陈列事实与判据（鼠标停在指标上可看公式），不合成总分，也没有全市场「今天能买」的按钮；每个席位的市场门槛独立判断。
        涨红跌绿按 A 股习惯：上涨家数、上涨占比、涨停家数为红，下跌家数、跌停家数为绿，涨跌幅按正负着色；
        成交额以亿显示（1 亿 = 100,000,000 元），鼠标停在数字上可看精确到分的元值。
        缺数据的地方写「未提供」，不会当成 0。证据时间：{Array.from(new Set(snap.metrics.map(m => m.available_at).filter(Boolean))).map(t => shanghai(t)).join('、') || '--'}。</p>
    </div>
  );
}

export function InstrumentTable({ snap, limit = 40, testid = 'instrument-table' }: { snap: MarketSnapshot; limit?: number;
  testid?: string }) {
  const [q, setQ] = useState('');
  const bars = useMemo(() => {
    const m: Record<string, (typeof snap.daily_bars)[number]> = {};
    snap.daily_bars.forEach(b => { if (b.session_date === snap.metadata.session_date) m[b.symbol] = b; });
    return m;
  }, [snap]);
  const rows = snap.instruments.filter(i => !q || i.symbol.includes(q.toUpperCase()) || i.name.includes(q));
  return (
    <div className="stack">
      <Field label="查找股票" hint={`共 ${snap.instruments.length} 只；只显示前 ${limit} 条匹配`}>
        <input type="text" value={q} onChange={e => setQ(e.target.value)} placeholder="代码或名称，例如 600000 或 浦发" />
      </Field>
      <div className="table-wrap">
        <table data-testid={testid}>
          <thead><tr><th>代码</th><th>名称</th><th className="num">最新价</th><th className="num">涨跌</th><th className="num">昨收</th>
            <th className="num">成交额（亿）</th><th>状态</th><th>日线</th></tr></thead>
          <tbody>
            {rows.slice(0, limit).map(i => {
              const r = changeRatio(i.last_price_decimal, i.prev_close_decimal);
              const tone = ratioTone(r);
              const b = bars[i.symbol];
              return (
                <tr key={i.symbol} data-symbol={i.symbol}>
                  <td className="num">{i.symbol}</td>
                  <td>{i.name}</td>
                  <td className="num" data-field="last_price" data-value={i.last_price_decimal ?? ''}>{formatDecimal(i.last_price_decimal)}</td>
                  <td className={`num ${tone === 'up' ? 'rise' : tone === 'down' ? 'fall' : tone === 'flat' ? 'flat' : 'unknown'}`}
                      data-field="change_ratio" data-value={r ?? ''}>{r === null ? '--' : formatRatioPct(r)}</td>
                  <td className="num">{formatDecimal(i.prev_close_decimal)}</td>
                  <td className="num" data-field="amount" data-value={b?.amount_minor ?? ''}
                      title={b?.amount_minor ? `${formatMinor(b.amount_minor)} 元` : undefined}>
                    {b?.amount_minor ? formatYi(b.amount_minor) : '--'}</td>
                  <td>{i.suspended === null ? <Tag tone="warn">停牌状态未知</Tag> : i.suspended ? <Tag tone="bad">停牌</Tag>
                    : i.is_st ? <Tag tone="muted">ST</Tag> : <span>{BOARD_LABEL[i.board] ?? i.board}</span>}</td>
                  <td>{b ? b.bar_status === 'final' ? '已收定' : b.bar_status === 'partial' ? '盘中未收定' : '终结未确认' : '无日线'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="note">「--」表示数据缺失，不是 0；涨红跌绿。最新价是该快照的价格，不是逐笔实时。</p>
    </div>
  );
}

interface ViewWindow { id: string; name: string; time: string; purpose: string; minutes: number }

function WatchWindows({ portfolios }: { portfolios: { portfolio_id: string; name: string; mode: string }[] }) {
  const [windows, setWindows] = useLocalState<ViewWindow[]>('rt.view_windows.v1', []);
  const [draft, setDraft] = useState<ViewWindow>({ id: '', name: '', time: '', purpose: '', minutes: 15 });
  const [active, setActive] = useState<ViewWindow | null>(null);
  const [decision, setDecision] = useState('继续等待');
  const [text, setText] = useState('');
  const [pid, setPid] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const save = async () => {
    setErr(null); setMsg(null);
    try {
      const p = await get<Portfolio>(`/api/v1/portfolios/${pid}`);
      const note = JSON.stringify({ schema: 'diary_v1', kind: 'window_decision', window: active?.name ?? '临时看盘',
                                    decision, text });
      await post('/api/v1/journal/events', {
        portfolio_id: pid, event_type: 'note', mode: p.mode === 'paper' ? 'paper' : 'user_reported', effective_at: nowIso(),
        symbol: null, side: null, shares: null, price_decimal: null, net_cash_delta_minor: null, snapshot_id: null,
        plan_id: null, plan_version: null, corrects_event_id: null, correction_action: null, corrected_event_type: null,
        note, replay_id: null, expected_portfolio_revision: p.revision, evidence_ids: [], reported_fees: null,
      }, newKey('window-decision'));
      setMsg('已记下本次决定'); setText(''); setActive(null);
    } catch (e) { setErr(e); }
  };
  return (
    <div className="stack">
      {windows.length ? (
        <ul className="timeline">{windows.map(w => (
          <li key={w.id}>
            <strong>{w.time} {w.name}</strong>（约 {w.minutes} 分钟）：{w.purpose || '未写目的'}
            {' '}<button className="btn quiet small" onClick={() => setActive(w)}>开始本次看盘</button>
            <button className="btn quiet small" onClick={() => setWindows(windows.filter(x => x.id !== w.id))}>删除</button>
          </li>
        ))}</ul>
      ) : <p className="note">还没有设定看盘窗口。窗口只在应用打开时提示，不注册后台任务、不发系统通知；次数和时间由你定，错过只记事实。</p>}
      <details className="more">
        <summary>添加看盘窗口</summary>
        <div className="form-grid" style={{ marginTop: 8 }}>
          <Field label="名称"><input type="text" value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} placeholder="开盘后半小时" /></Field>
          <Field label="时间（上海）"><input type="text" value={draft.time} onChange={e => setDraft({ ...draft, time: e.target.value })} placeholder="10:00" /></Field>
          <Field label="目的"><input type="text" value={draft.purpose} onChange={e => setDraft({ ...draft, purpose: e.target.value })} placeholder="复核已冻结计划" /></Field>
          <Field label="预计分钟"><input type="number" min={1} value={draft.minutes} onChange={e => setDraft({ ...draft, minutes: Number(e.target.value) || 15 })} /></Field>
        </div>
        <button className="btn secondary small" style={{ marginTop: 8 }} disabled={!draft.name || !draft.time}
                onClick={() => { setWindows([...windows, { ...draft, id: `${Date.now()}` }]); setDraft({ id: '', name: '', time: '', purpose: '', minutes: 15 }); }}>
          保存窗口</button>
      </details>
      {active || windows.length === 0 ? (
        <div className="panel">
          <h3>{active ? `本次看盘：${active.name}` : '临时看盘'}</h3>
          <ol className="note" style={{ paddingLeft: 18 }}>
            <li>先看顶部：数据截至哪天、是否过期。</li>
            <li>浏览矩阵里与上次相比的变化。</li>
            <li>只挑一个问题去研究，或者复核一份已冻结的计划。</li>
            <li>记下简短决定，然后结束。</li>
          </ol>
          <div className="form-grid">
            <Field label="决定">
              <select value={decision} onChange={e => setDecision(e.target.value)}>
                {['继续等待', '准备条件', '模拟执行', '仅记录'].map(x => <option key={x}>{x}</option>)}
              </select>
            </Field>
            <Field label="记到哪个组合的日记">
              <select value={pid} onChange={e => setPid(e.target.value)}>
                <option value="">选择组合</option>
                {portfolios.map(p => <option key={p.portfolio_id} value={p.portfolio_id}>{p.name || p.portfolio_id}</option>)}
              </select>
            </Field>
          </div>
          <Field label="一句话"><textarea value={text} onChange={e => setText(e.target.value)} placeholder="例如：主线不清，继续等待；明早复核计划 A" /></Field>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn" disabled={!pid || !text.trim()} onClick={save}>保存决定</button>
            <a className="btn secondary" href={href('cases')}>去研究一个问题</a>
            {active ? <button className="btn quiet" onClick={() => setActive(null)}>结束窗口</button> : null}
          </div>
          {msg ? <p className="note" role="status">{msg}</p> : null}
          <ErrorNote error={err} />
        </div>
      ) : null}
    </div>
  );
}

/** 个人约束（不是市场证据）：各组合是否在冷却中。 */
function MyConstraints() {
  const app = useApp();
  const pfs = app.index?.portfolios ?? [];
  const st = useLoad(async () => {
    const out: { name: string; active: boolean; reason: string }[] = [];
    for (const p of pfs.slice(0, 10)) {
      const ev = await get<{ items: { event_id: string; event_type: string; related_control_event_id: string | null;
        reason: string; scheduled_end_at: string | null }[] }>(`/api/v1/portfolios/${p.portfolio_id}/controls/events`);
      let active: { event_id: string; reason: string; scheduled_end_at: string | null } | null = null;
      for (const e of ev.items) {
        if (e.event_type === 'enabled') active = e;
        else if (active && e.related_control_event_id === active.event_id) active = null;
      }
      if (active?.scheduled_end_at && Date.parse(active.scheduled_end_at) <= Date.now()) active = null;
      out.push({ name: p.name || p.portfolio_id, active: !!active, reason: active?.reason ?? '' });
    }
    return out;
  }, [pfs.map(p => p.portfolio_id).join(',')]);
  const on = (st.data ?? []).filter(x => x.active);
  return (
    <div className="panel" data-testid="my-constraints">
      <h3>我的约束</h3>
      {on.length ? on.map(x => <p key={x.name} style={{ margin: 0, fontSize: 'var(--fs-1)' }}><Tag tone="warn">冷却中</Tag> {x.name}：{x.reason}</p>)
        : <p className="note" style={{ margin: 0 }}>没有生效中的冷却。</p>}
      <p className="note">这是你给自己定的条件，不是「市场弱」的证据。</p>
    </div>
  );
}

interface PmItem { label: string; value: string; metric_id: string | null; tone: string; note: string; available: boolean }
interface PmSection { id: string; title: string; note: string; items: PmItem[] }
interface PostMarketReport {
  schema_version: string; snapshot_id: string; session_date: string | null; generated_at: string;
  generator: { kind: string; model_called: boolean; note: string };
  data_quality: { quality: string; freshness: string; source_mode: string };
  summary: { headline: string; tone: string };
  sections: PmSection[];
  watchpoints: string[]; risks: string[]; gaps: { item: string; reason: string; impact: string }[];
  markdown: string;
}

/** 盘后复盘（规则生成，不调用模型）：每个数字带 metric_id，缺的写「未提供」。 */
function PostMarketSection({ snap }: { snap: MarketSnapshot }) {
  const rep = useLoad(() => get<PostMarketReport>(`/api/v1/snapshots/${snap.snapshot_id}/postmarket`), [snap.snapshot_id]);
  if (rep.error) return <ErrorNote error={rep.error} title="读取复盘失败" />;
  const r = rep.data;
  if (!r) return <p className="note">生成复盘中…</p>;
  const tone = r.summary.tone;
  return (
    <div className="stack" data-testid="postmarket">
      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h3 style={{ margin: 0 }}>{r.session_date} 盘后复盘</h3>
          <span className="note">规则生成 · 未调用模型 · 生成于 {shanghai(r.generated_at)}</span>
        </div>
        <p className="lede" style={{ margin: '8px 0' }}>{r.summary.headline}</p>
        <div className="row">
          <Tag tone={tone === 'broad_strong' ? 'ok' : tone === 'broad_weak' ? 'bad' : 'muted'}>
            {{ broad_strong: '宽度偏强', broad_weak: '宽度偏弱', neutral: '宽度中性' }[tone] ?? tone}</Tag>
          <Tag tone={r.data_quality.quality === 'complete' ? 'ok' : 'warn'}>数据质量 {r.data_quality.quality}</Tag>
          <Tag tone={r.data_quality.freshness === 'fresh' ? 'ok' : 'warn'}>新鲜度 {r.data_quality.freshness}</Tag>
          <button className="btn quiet small" onClick={() => downloadText(`${r.session_date ?? 'session'}-盘后复盘.md`, r.markdown)}
                  data-testid="postmarket-download">下载 Markdown</button>
        </div>
      </div>
      {r.sections.filter(s_ => s_.id === 'index').map(sec => (
        <div className="panel" key={sec.id} data-testid={`pm-${sec.id}`}>
          <h3>{sec.title}{sec.note ? <span className="note">（{sec.note}）</span> : null}</h3>
          <div className="grid-cols">
            {sec.items.map(it => (
              <div key={it.label}>
                <div className="note">{it.label}</div>
                <div className={`num ${it.tone === 'up' ? 'rise' : it.tone === 'down' ? 'fall' : ''}`}
                     style={{ fontSize: 'var(--fs-3)', fontWeight: 600 }}
                     title={it.metric_id ? `来源 ${it.metric_id}` : undefined}>{it.value}</div>
                {it.note ? <div className="note">{it.note}</div> : null}
              </div>
            ))}
          </div>
        </div>
      ))}
      <div className="grid-2">
        {r.sections.filter(s_ => s_.id !== 'index').map(sec => (
          <div className="panel" key={sec.id} data-testid={`pm-${sec.id}`}>
            <h3>{sec.title}</h3>
            {sec.note ? <p className="note" style={{ marginTop: 0 }}>{sec.note}</p> : null}
            <dl className="mrows">
              {sec.items.map(it => (
                <div key={it.label}>
                  <dt>{it.label}{it.note ? <span className="note">（{it.note}）</span> : null}</dt>
                  <dd className={`num ${it.tone === 'up' ? 'rise' : it.tone === 'down' ? 'fall' : it.tone === 'flat' ? 'flat' : ''}`}
                      title={it.metric_id ? `来源 ${it.metric_id}` : undefined}>{it.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
      <div className="grid-2">
        <div className="panel" data-testid="pm-watch"><h3>明日观察点</h3>
          <ul style={{ paddingLeft: 18 }}>{r.watchpoints.map(x => <li key={x}>{x}</li>)}</ul></div>
        <div className="panel" data-testid="pm-risks"><h3>主要风险</h3>
          {r.risks.length ? <ul style={{ paddingLeft: 18 }}>{r.risks.map(x => <li key={x}>{x}</li>)}</ul>
            : <p className="note">今天没有触发规则级的风险项。</p>}</div>
      </div>
      <InterpretPanel snap={snap} />
      <details className="more" data-testid="pm-gaps">
        <summary>未接入的数据（{r.gaps.length} 项）</summary>
        <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>
          {r.gaps.map(g_ => <li key={g_.item}><strong>{g_.item}</strong>：{g_.reason}（{g_.impact}）</li>)}
        </ul>
      </details>
    </div>
  );
}

interface InterpretOut {
  status: string; used_search: boolean; tokens: number | null;
  interpretation: { commentary: string; highlights: string[]; watch_next: string[]; web_refs: { title: string; url: string }[]; missing_inputs: string[] };
  search_hits: { title: string; url: string }[]; audit: { model_run_id: string };
}

/** 让模型解读这份复盘（默认关闭）：数字必须原样来自报告，网页引用必须来自本次检索，否则后端拒收。 */
function InterpretPanel({ snap }: { snap: MarketSnapshot }) {
  const st = useLoad(() => get<{ enabled: boolean; label?: string; model: string; reason: string; search_available?: boolean;
    profile_name?: string }>('/api/v1/llm/status'), []);
  const [search, setSearch] = useState(false);
  const [out, setOut] = useState<InterpretOut | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [cfgOpen, setCfgOpen] = useState(false);
  const run = async () => {
    setBusy(true); setErr(null); setOut(null);
    try {
      const { data } = await post<InterpretOut>('/api/v1/reports/postmarket/interpret',
        { snapshot_id: snap.snapshot_id, enable_search: search }, newKey('pm-interpret'));
      setOut(data);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (
    <div className="panel" data-testid="pm-interpret">
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h3 style={{ margin: 0 }}>模型解读</h3>
        <span className="row"><button className="btn quiet small" onClick={() => setCfgOpen(true)}
                                     data-testid="open-llm-settings">模型设置</button>
          <span className="note">可选 · 默认关闭 · 每次点击才调用一次</span></span>
      </div>
      {st.data && !st.data.enabled ? (
        <p className="note" data-testid="pm-llm-disabled">模型未启用（{st.data.reason}）。点右上「模型设置」选一份存档、把密钥填进去就行
          （也可以继续用环境变量 <code>DEEPSEEK_API_KEY</code>）；默认关闭，每次点击才调用一次。</p>
      ) : (
        <>
          <label className="check"><input type="checkbox" checked={search} disabled={st.data?.search_available === false}
                                          onChange={e => setSearch(e.target.checked)} />
            允许联网补缺（两融/北向/ETF/外围等未接入项，结果标注为未核验）
            {st.data?.search_available === false ? <span className="note">当前模型供应商不支持搜索</span> : null}</label>
          <div className="row">
            <button className="btn" disabled={busy} onClick={run} data-testid="pm-interpret-run">
              {busy ? '在写…（会调用一次 DeepSeek）' : '让模型解读这份复盘'}</button>
            {st.data ? <span className="note">模型{st.data.profile_name ? `「${st.data.profile_name}」` : ''}
              {st.data.label ? ` ${st.data.label} · ` : ' '}{st.data.model}</span> : null}
          </div>
        </>
      )}
      <ErrorNote error={err} />
      <LlmSettingsDialog open={cfgOpen} onClose={() => setCfgOpen(false)} onSaved={() => st.reload()} />
      {out ? (
        <div className="stack" data-testid="pm-interpret-out">
          <p className="lede" style={{ margin: 0 }}>{out.interpretation.commentary}</p>
          {out.interpretation.highlights.length ? <ul style={{ paddingLeft: 18 }}>{out.interpretation.highlights.map(x => <li key={x}>{x}</li>)}</ul> : null}
          {out.interpretation.watch_next.length ? <div><strong>明天看这些</strong><ul style={{ paddingLeft: 18 }}>{out.interpretation.watch_next.map(x => <li key={x}>{x}</li>)}</ul></div> : null}
          {out.interpretation.web_refs.length ? (
            <details className="more" open><summary>网页来源（未核验，{out.interpretation.web_refs.length} 条）</summary>
              <ul style={{ paddingLeft: 18, fontSize: 'var(--fs-1)' }}>{out.interpretation.web_refs.map(r => <li key={r.url}><a href={r.url} target="_blank" rel="noreferrer">{r.title || r.url}</a></li>)}</ul>
            </details>
          ) : null}
          {out.interpretation.missing_inputs.length ? <p className="unknown">仍缺：{out.interpretation.missing_inputs.join('、')}</p> : null}
          <p className="note">审计 {out.audit.model_run_id} · {out.tokens ?? 0} tokens{out.used_search ? ' · 用了联网检索' : ''}</p>
        </div>
      ) : null}
    </div>
  );
}

/** 空库时的第二条路：用服务端预配置的只读历史日线归档（最近一个归档日）建一份快照——明确标「本机历史归档」，不冒充今天的行情。 */
function ArchiveImport() {
  const app = useApp();
  const arch = app.index?.archive as { coverage_end: string | null; session_count: number | null } | null | undefined;
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (!arch?.coverage_end) return null;
  const day = arch.coverage_end;
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      await runJob('/api/v1/snapshots', { session_date: day, source_mode: 'file_import', symbols: [], allow_stale: true },
                   newKey('archive-snapshot'), setJob);
      await app.refreshAll();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (
    <div className="stack" style={{ marginTop: 10 }}>
      <div className="row">
        <button className="btn secondary" disabled={busy} onClick={run} data-testid="archive-import">
          {busy ? '正在用归档建快照…' : `用本机历史归档（${day}）`}</button>
        <JobProgress job={busy ? job : null} />
      </div>
      <p className="note" style={{ margin: 0 }}>归档是以前下载好的日线（共 {arch.session_count ?? '--'} 个交易日），只能看历史，不是今天的行情；
        状态条会写「本机历史归档」，新鲜度会如实显示落后。</p>
      <ErrorNote error={err} />
    </div>
  );
}

export function EnvironmentPage() {
  const app = useApp();
  const snap = app.snapshot;
  const h = app.health;
  return (
    <>
      <div className="ws-head">
        <h1>环境概览</h1>
        <p>先确认数据是哪一天的、是否完整，再决定今天研究什么。这里不给买卖建议。</p>
      </div>
      <div className="toprow">
        <Section title="数据就绪">
          {h ? (
            <div className="panel" data-testid="readiness" data-status={h.status}>
              <div className="row">
                <Tag tone={h.status === 'ready' ? 'ok' : h.status === 'degraded' ? 'warn' : 'bad'}>
                  {h.status === 'ready' ? '业务就绪' : h.status === 'degraded' ? '可以查看，但有缺口' : '数据不可用'}</Tag>
              </div>
              <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 'var(--fs-1)' }}>
                <li>最新快照：{label(QUALITY_LABEL, h.quality)}，{label(FRESHNESS_LABEL, h.freshness)}</li>
                <li>最近完整交易日：{h.latest_completed_session_date ?? '暂不知道（交易日历还没覆盖今天；点「采集最新快照」会先从数据源取日历）'}</li>
              </ul>
              {h.missing_inputs.length ? <details className="more"><summary>缺 {h.missing_inputs.length} 项</summary>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 'var(--fs-1)' }}>{h.missing_inputs.map(m => <li key={m} className="unknown">{missingText(m)}</li>)}</ul>
              </details> : null}
              <p className="note">服务能打开不等于数据可用。已启用：{h.enabled_phases.join('、')}；实时提醒（P4）默认关闭。</p>
            </div>
          ) : <ErrorNote error={app.healthError} title="健康检查失败" />}
        </Section>
        {snap ? <Section title="股票池"><Denominators snap={snap} /></Section> : null}
        <Section title="下一步">
          <div className="stack">
            <MyConstraints />
            <button className="btn" onClick={() => go('cases', 'new')} disabled={!snap}>研究一个问题</button>
            <a className="btn secondary" href={href('plans')}>复核已冻结的计划</a>
            <a className="btn secondary" href={href('portfolio')}>写一笔日记</a>
          </div>
        </Section>
      </div>
      <Section title="市场矩阵" hint={snap ? `快照 ${snap.metadata.quote_date ?? '--'}` : undefined}>
        {snap ? <MarketMatrix snap={snap} /> : app.snapshotError ? (
          <div className="alert" role="alert" data-testid="no-data">数据不可用：{errorText(app.snapshotError)}。
            不会自动切换到演示数据；修好数据源后在顶部重新采集。</div>
        ) : <Empty title="还没有任何快照">
          <p style={{ margin: '6px 0 0' }}>点顶部「采集最新快照」从数据源取一次（需要本机的 meoz 和网络）。
            数据源暂时连不上时，可以先用本机历史归档看看界面。</p>
          <ArchiveImport />
        </Empty>}
      </Section>
      <Section title="盘后复盘" hint={snap ? '规则生成，数字可回查；缺的数据单列' : undefined}>
        {snap ? <PostMarketSection snap={snap} /> : <p className="note">需要一份快照：点顶部「采集最新快照」，或用本机历史归档。</p>}
      </Section>
      <div className="grid-2" style={{ marginTop: 22 }}>
        {snap ? (
          <Section title="行情表" hint="同一快照，所有席位读的都是这份数据">
            <InstrumentTable snap={snap} limit={15} />
          </Section>
        ) : <div />}
        <Section title="固定看盘窗口">
          <WatchWindows portfolios={app.index?.portfolios ?? []} />
        </Section>
      </div>
    </>
  );
}
