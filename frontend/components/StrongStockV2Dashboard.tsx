'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  BarChart3,
  BookOpenCheck,
  BrainCircuit,
  CalendarDays,
  CheckCircle2,
  CircleAlert,
  Database,
  GitCompareArrows,
  Gauge,
  History,
  Layers3,
  LineChart,
  Loader2,
  Map as MapIcon,
  Play,
  RotateCcw,
  ShieldAlert,
  Target,
  TrendingUp,
} from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';

type AnyMap = Record<string, any>;

const STATUS_LABELS: Record<string, string> = {
  NOT_FOUND: '未形成',
  POSSIBLE: '疑似',
  FORMING: '构建中',
  CONFIRMED: '已确认',
  WEAKENING: '减弱',
  INVALID: '失效',
};

const STATUS_CLASS: Record<string, string> = {
  CONFIRMED: 'is-confirmed',
  FORMING: 'is-forming',
  POSSIBLE: 'is-possible',
  WEAKENING: 'is-weakening',
  INVALID: 'is-invalid',
};

const NAV_ITEMS = [
  ['总览', 'v2-overview'],
  ['风险总控', 'v2-risk'],
  ['量时空', 'v2-qts'],
  ['主力', 'v2-main-force'],
  ['均线归位', 'v2-ma'],
  ['A/B/C区', 'v2-zones'],
  ['三度', 'v2-three-degree'],
  ['大形态', 'v2-patterns'],
  ['暴涨之星', 'v2-stars'],
  ['盈利模式', 'v2-profit'],
  ['买卖雷达', 'v2-buy-sell'],
  ['三书互证', 'v2-consensus'],
  ['历史验证', 'v2-history'],
];

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function text(value: unknown, fallback = '有效样本不足'): string {
  const result = String(value ?? '').trim();
  return result || fallback;
}

function numberText(value: unknown, digits = 1): string {
  return finite(value) ? value.toFixed(digits) : '--';
}

function percent(value: unknown, digits = 1): string {
  return finite(value) ? `${value.toFixed(digits)}%` : '--';
}

function statusLabel(value: unknown): string {
  return STATUS_LABELS[String(value || '')] || '未形成';
}

function statusClass(value: unknown): string {
  return STATUS_CLASS[String(value || '')] || '';
}

function tone(value: unknown): string {
  const raw = String(value || '');
  if (/风险|失效|退出|空头|出货|承压|下行|弱/.test(raw)) return 'is-down';
  if (/警告|观察|构建|疑似|中|分歧|等待|未知/.test(raw)) return 'is-warn';
  if (/确认|增强|偏多|强|顺势|有效|通过|机会/.test(raw)) return 'is-up';
  return '';
}

function Evidence({ items, limit = 3 }: { items?: AnyMap[]; limit?: number }) {
  const rows = (items || []).slice(0, limit);
  if (!rows.length) return <span className="strong-v2-muted">暂无可核验证据</span>;
  return <ul className="strong-v2-evidence">{rows.map((item, index) => <li key={`${item.feature || item.text || index}-${index}`}><span>{text(item.text, '证据')}</span>{item.feature && <em>{item.feature}{item.value !== undefined ? ` · ${text(item.value)}` : ''}</em>}</li>)}</ul>;
}

function StatusPill({ value }: { value: unknown }) {
  return <span className={`strong-v2-status ${statusClass(value)}`}>{statusLabel(value)}</span>;
}

function V2Panel({ id, title, subtitle, icon: Icon, children, className = '', risk = false }: { id?: string; title: string; subtitle?: string; icon: any; children: React.ReactNode; className?: string; risk?: boolean }) {
  return <section id={id} className={`strong-v2-panel ${risk ? 'is-risk' : ''} ${className}`}><header className="strong-v2-panel-header"><div><h2><Icon size={14} />{title}</h2>{subtitle && <p>{subtitle}</p>}</div></header><div className="strong-v2-panel-body">{children}</div></section>;
}

function Metric({ label, value, detail, valueClass = '' }: { label: string; value: React.ReactNode; detail?: React.ReactNode; valueClass?: string }) {
  return <div className="strong-v2-metric"><span>{label}</span><b className={valueClass}>{value}</b>{detail && <small>{detail}</small>}</div>;
}

function SignalCard({ signal, compact = false }: { signal: AnyMap; compact?: boolean }) {
  return <article className={`strong-v2-signal-card ${compact ? 'is-compact' : ''}`}>
    <div className="strong-v2-signal-head"><div className="min-w-0"><b>{text(signal.name || signal.original_name)}</b><code>{text(signal.skill_id, '')}</code></div><StatusPill value={signal.status} /></div>
    {signal.subtype && signal.subtype !== signal.name && <div className="strong-v2-subtype">子型：{text(signal.subtype)}</div>}
    {!compact && <><p className="strong-v2-mechanism">{text(signal.mechanism || signal.forming_mechanism, '形成机理待补')}</p><Evidence items={signal.evidence} limit={2} />{signal.counter_evidence?.length > 0 && <div className="strong-v2-counter"><span>反证</span><Evidence items={signal.counter_evidence} limit={1} /></div>}<div className="strong-v2-confirm-grid"><div><span>下一步确认</span><p>{text(signal.next_confirmation?.[0], '等待后续价格与成交确认')}</p></div><div><span>失效条件</span><p>{text(signal.invalidation?.[0], '关键结构失守')}</p></div></div></>}
    {finite(signal.confidence) && <div className="strong-v2-signal-confidence"><span>工程置信度</span><b>{numberText(signal.confidence, 0)}%</b></div>}
  </article>;
}

function SignalCollection({ title, signals, limit = 8 }: { title: string; signals?: AnyMap[]; limit?: number }) {
  const rows = signals || [];
  const active = rows.filter((item) => item.status !== 'NOT_FOUND');
  const visible = (active.length ? active : rows).slice(0, limit);
  return <div className="strong-v2-signal-collection"><div className="strong-v2-collection-heading"><span>{title}</span><b>{active.length} 个有效</b></div><div className="strong-v2-signal-grid">{visible.map((item) => <SignalCard key={`${item.skill_id}-${item.name}`} signal={item} compact />)}</div>{rows.length > visible.length && <details className="strong-v2-more"><summary>查看全部 {rows.length} 个技能结果</summary><div className="strong-v2-signal-grid">{rows.map((item) => <SignalCard key={`all-${item.skill_id}-${item.name}`} signal={item} />)}</div></details>}</div>;
}

function RiskPanel({ risk }: { risk: AnyMap }) {
  const signals = risk.signals || [];
  return <V2Panel id="v2-risk" title="风险总控" subtitle="认识风险比认识机会更重要 · 风险压力优先" icon={ShieldAlert} risk>
    <div className="strong-v2-risk-summary"><div><span>图表风险</span><strong className={tone(risk.overall_state)}>{numberText(risk.overall_score, 0)}<small>/100</small></strong><em>{text(risk.overall_state, '未知')}</em></div><div><span>处理优先级</span><b className={risk.priority === 'RISK' ? 'is-down' : 'is-warn'}>{risk.priority === 'RISK' ? '风险优先' : '继续观察'}</b><p>{text(risk.note)}</p></div></div>
    <div className="strong-v2-pressure-grid">{signals.map((signal: AnyMap) => <article key={signal.skill_id}><div><b>{text(signal.name)}</b><StatusPill value={signal.status} /></div><strong className={tone(signal.status)}>{numberText(signal.confidence, 0)}<small>/100</small></strong><Evidence items={signal.evidence} limit={2} /></article>)}</div>
    <div className="strong-v2-risk-columns"><div><h3>大势风险背景</h3><p>{text(risk.systemic_risk?.scope)}</p></div><div><h3>投资者纪律自检</h3><div className="strong-v2-chip-list">{(risk.investor_risk?.items || []).map((item: string) => <span key={item}>{item}</span>)}</div></div></div>
  </V2Panel>;
}

function historyDate(value: unknown): string {
  const raw = String(value || '');
  return raw.length >= 10 ? raw.slice(0, 10) : '--';
}

function historyScore(value: unknown): string {
  return finite(value) ? `${Math.round(value)}` : '--';
}

function HistoryPressureRow({ row }: { row: AnyMap }) {
  const items = [
    ['顶部', row.top, 'is-down'],
    ['趋势', row.trend, 'is-warn'],
    ['缺口', row.gap, 'is-warn'],
    ['暴跌起点', row.crash_origin, 'is-down'],
  ] as const;
  return <div className="strong-v2-pressure-history-row"><time>{historyDate(row.date)}</time><b>{historyScore(row.overall_score)}</b><div>{items.map(([label, value, className]) => <span key={label}><em>{label}</em><i className={className} style={{ width: `${Math.max(0, Math.min(100, finite(value) ? value : 0))}%` }} /><small>{historyScore(value)}</small></span>)}</div></div>;
}

function CaseMatch({ title, value, toneClass }: { title: string; value?: AnyMap | null; toneClass: string }) {
  return <article className={`strong-v2-case-match ${toneClass}`}><div><span>{title}</span><b>{value ? `${historyDate(value.start_date)} → ${historyDate(value.end_date)}` : '暂无已标注案例'}</b></div>{value && <><strong>{historyScore(value.similarity)}<small>/100</small></strong><p>{text(value.notes, '暂无案例说明')}</p><code>{text(value.skill_id, '技能未标注')}</code></>}</article>;
}

function HistoryWorkspace({ v2, symbol }: { v2: AnyMap; symbol: string }) {
  const [expanded, setExpanded] = useState(false);
  const [tab, setTab] = useState<'history' | 'cases' | 'replay' | 'backtest'>('history');
  const [history, setHistory] = useState<AnyMap | null>(null);
  const [cases, setCases] = useState<AnyMap | null>(null);
  const [replay, setReplay] = useState<AnyMap | null>(null);
  const [backtest, setBacktest] = useState<AnyMap | null>(null);
  const [replayDate, setReplayDate] = useState(historyDate(v2.trade_date) === '--' ? '' : historyDate(v2.trade_date));
  const [skillId, setSkillId] = useState('');
  const [horizons, setHorizons] = useState('1,3,5,10,20');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const skillOptions = useMemo(() => {
    const values = (v2.signals || [])
      .filter((item: AnyMap) => item?.skill_id && item?.status !== 'NOT_FOUND')
      .map((item: AnyMap) => ({ id: String(item.skill_id), name: text(item.name, item.skill_id) }));
    const unique = new Map<string, AnyMap>(values.map((item: AnyMap) => [item.id, item] as [string, AnyMap]));
    return Array.from(unique.values()).slice(0, 80);
  }, [v2.signals]);
  const selectedSkill = skillId || skillOptions[0]?.id || 'BXZX_009';

  useEffect(() => {
    // A new symbol/as-of snapshot must never display results calculated for the
    // previous symbol while the user is reviewing the learning workspace.
    setHistory(null);
    setCases(null);
    setReplay(null);
    setBacktest(null);
    setTab('history');
    setReplayDate(historyDate(v2.trade_date) === '--' ? '' : historyDate(v2.trade_date));
    setSkillId('');
    setBusy('');
    setError('');
  }, [symbol, v2.trade_date]);

  const run = async (kind: 'history' | 'cases' | 'replay' | 'backtest') => {
    setExpanded(true);
    setTab(kind);
    setBusy(kind);
    setError('');
    try {
      if (kind === 'history') {
        const response = await apiFetch<{ data: AnyMap }>(`/strong-stock-decision/v2/${symbol}/timeline?limit=80`, { timeoutMs: 60000 });
        setHistory(response.data);
      } else if (kind === 'cases') {
        const response = await apiFetch<{ data: AnyMap }>(`/strong-stock-decision/v2/${symbol}/wang-xing-kong`, { timeoutMs: 60000 });
        setCases(response.data);
      } else if (kind === 'replay') {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(replayDate)) throw new Error('请选择有效的回放日期');
        const response = await apiFetch<{ data: AnyMap }>('/strong-stock-decision/v2/internal/replay', { method: 'POST', body: JSON.stringify({ symbol, trade_date: replayDate }), timeoutMs: 90000 });
        setReplay(response.data);
      } else {
        const parsedHorizons = horizons.split(',').map((value) => Number(value.trim())).filter((value) => [1, 3, 5, 10, 20].includes(value));
        const response = await apiFetch<{ data: AnyMap }>('/strong-stock-decision/v2/internal/backtest', { method: 'POST', body: JSON.stringify({ symbol, skill_id: selectedSkill, horizons: parsedHorizons.length ? parsedHorizons : [1, 3, 5, 10, 20] }), timeoutMs: 180000 });
        setBacktest(response.data);
      }
    } catch (caught) {
      setError(friendlyApiError(caught, '历史验证请求失败'));
    } finally {
      setBusy('');
    }
  };

  const activeTabClass = (value: string) => tab === value ? 'is-active' : '';
  const historyPoints: AnyMap[] = history?.points || [];
  const pressureRows: AnyMap[] = history?.pressure_history || [];
  const mainForceRows: AnyMap[] = history?.main_force_history || [];
  const metrics = (backtest?.metrics || {}) as AnyMap;

  return <section id="v2-history" className="strong-v2-learning">
    <header className="strong-v2-learning-header"><div><h2><History size={14} />历史验证与学习</h2><p>P1 因果回放与案例对照 · P2 统计结果保持 Shadow，不改写 ACTION</p></div><button type="button" className="strong-v2-learning-toggle" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>{expanded ? '收起工作区' : '展开工作区'}<span>{expanded ? '−' : '+'}</span></button></header>
    {expanded && <div className="strong-v2-learning-body">
      <div className="strong-v2-learning-actions">
        <button type="button" onClick={() => void run('history')} disabled={Boolean(busy)}><MapIcon size={13} />{busy === 'history' ? '读取中…' : '历史轨迹'}</button>
        <button type="button" onClick={() => void run('cases')} disabled={Boolean(busy)}><GitCompareArrows size={13} />{busy === 'cases' ? '比对中…' : '案例对照'}</button>
        <label><CalendarDays size={13} /><span>回放日期</span><input type="date" value={replayDate} onChange={(event) => setReplayDate(event.target.value)} /></label>
        <button type="button" onClick={() => void run('replay')} disabled={Boolean(busy) || !replayDate}><RotateCcw size={13} />{busy === 'replay' ? '回放中…' : '运行回放'}</button>
        <label className="strong-v2-skill-select"><span>回测 Skill</span><select value={selectedSkill} onChange={(event) => setSkillId(event.target.value)}>{skillOptions.map((item: AnyMap) => <option key={item.id} value={item.id}>{item.id} · {item.name}</option>)}</select></label>
        <label><span>窗口</span><input className="strong-v2-horizon-input" value={horizons} onChange={(event) => setHorizons(event.target.value)} aria-label="回测窗口" /></label>
        <button type="button" onClick={() => void run('backtest')} disabled={Boolean(busy)}><Play size={13} />{busy === 'backtest' ? '计算中…' : '运行 Shadow 回测'}</button>
      </div>
      {error && <div className="strong-v2-learning-error"><CircleAlert size={14} />{error}</div>}
      <nav className="strong-v2-learning-tabs" aria-label="历史验证视图"><button className={activeTabClass('history')} type="button" onClick={() => { setTab('history'); if (!history) void run('history'); }}>轨迹与压力</button><button className={activeTabClass('cases')} type="button" onClick={() => { setTab('cases'); if (!cases) void run('cases'); }}>望星空案例</button><button className={activeTabClass('replay')} type="button" onClick={() => setTab('replay')}>指定日回放</button><button className={activeTabClass('backtest')} type="button" onClick={() => setTab('backtest')}>验证统计</button></nav>

      {tab === 'history' && <div className="strong-v2-learning-view">
        {!history && !busy && <div className="strong-v2-learning-empty">点击“历史轨迹”读取最近 80 个可复现交易截面。</div>}
        {busy === 'history' && <div className="strong-v2-learning-loading"><Loader2 size={16} className="animate-spin" />正在按日期重算状态链…</div>}
        {history && <><div className="strong-v2-history-summary"><Metric label="有效截面" value={history.point_count ?? 0} detail={`${historyDate(history.first_date)} → ${historyDate(history.last_date)}`} /><Metric label="数据状态" value={text(history.status)} /><Metric label="最新压力" value={historyScore(pressureRows[pressureRows.length - 1]?.overall_score)} detail="/100" /><Metric label="计算口径" value="因果截面" /></div><div className="strong-v2-history-columns"><div><h3>历史压力地图</h3><p className="strong-v2-inline-note">四类大压按当日可见数据记录，数值越高代表证据越集中。</p><div className="strong-v2-pressure-history">{pressureRows.slice(-14).map((row: AnyMap) => <HistoryPressureRow key={row.date} row={row} />)}</div></div><div><h3>主力证据时间线</h3><p className="strong-v2-inline-note">只描述量价与承接状态，不推断不可验证的参与者意图。</p><div className="strong-v2-main-history">{mainForceRows.slice(-12).reverse().map((row: AnyMap) => <div key={row.date}><time>{historyDate(row.date)}</time><b>{text(row.direction, '暂不明确')}</b><span>{text(row.stage, '样本不足')}</span><em>{text(row.continuity, '未知')}</em></div>)}</div></div></div><div className="strong-v2-evolution"><h3>状态演化</h3><div>{historyPoints.slice(-12).reverse().map((row: AnyMap) => <article key={row.date}><time>{historyDate(row.date)}</time><b>{text(row.zone, '未形成')}</b><span>{text(row.action, 'NO_TRADE')}</span><strong>{historyScore(row.risk_score)}<small>风险</small></strong></article>)}</div></div><p className="strong-v2-learning-method">{text(history.method)} {text(history.note)}</p></>}
      </div>}

      {tab === 'cases' && <div className="strong-v2-learning-view"><div className="strong-v2-case-summary"><Metric label="案例状态" value={text(cases?.status, '尚未读取')} /><Metric label="成功案例" value={cases?.success_cases?.length ?? '--'} /><Metric label="失败案例" value={cases?.failure_cases?.length ?? '--'} /><Metric label="形似案例" value={cases?.look_alike_cases?.length ?? '--'} /></div>{busy === 'cases' && <div className="strong-v2-learning-loading"><Loader2 size={16} className="animate-spin" />正在比较结构特征…</div>}{cases && <><div className="strong-v2-case-grid"><CaseMatch title="最相似成功案例" value={cases.closest_success} toneClass="is-success" /><CaseMatch title="最相似失败案例" value={cases.closest_failure} toneClass="is-failure" /></div><div className="strong-v2-why-different"><h3>相似与差异</h3><div><span><b>相似：</b>{(cases.why_similar || []).join('；') || '暂无已标注相似维度'}</span><span><b>差异：</b>{(cases.why_different || []).join('；') || '暂无已标注差异维度'}</span></div></div><p className="strong-v2-learning-method">{text(cases.note)}</p></>}</div>}

      {tab === 'replay' && <div className="strong-v2-learning-view"><div className="strong-v2-replay-head"><CalendarDays size={15} /><span>回放截面：{historyDate(replayDate)}</span><button type="button" onClick={() => void run('replay')} disabled={Boolean(busy) || !replayDate}><Play size={12} />执行</button></div>{busy === 'replay' && <div className="strong-v2-learning-loading"><Loader2 size={16} className="animate-spin" />正在重建该日可见信息…</div>}{replay && <div className="strong-v2-replay-grid"><Metric label="ACTION（Shadow）" value={text(replay.action, 'NO_TRADE')} /><Metric label="状态" value={text(replay.state_name)} /><Metric label="交易区" value={text(replay.zones?.zone)} /><Metric label="图表风险" value={`${historyScore(replay.risk?.overall_score)}/100`} /><div className="strong-v2-replay-explanation"><h3>当日判断</h3><p>{text(replay.explanation?.current_judgement)}</p><h3>当日可见证据</h3><p>{(replay.explanation?.evidence || []).slice(0, 8).join('；') || '暂无可核验信号'}</p><h3>回放边界</h3><p>{text(replay.data_quality?.note)} {text(replay.explanation?.limitations?.[0], '')}</p></div></div>}{!replay && !busy && <div className="strong-v2-learning-empty">选择交易日后运行回放；系统只使用该日及之前的数据。</div>}</div>}

      {tab === 'backtest' && <div className="strong-v2-learning-view"><div className="strong-v2-backtest-head"><div><h3>Shadow 验证统计</h3><p>当前 Skill：{selectedSkill} · {skillOptions.find((item: AnyMap) => item.id === selectedSkill)?.name || '技能'}</p></div>{backtest?.validation_gate && <span className="strong-v2-gate">{text(backtest.validation_gate.status)} · ACTION 不受影响</span>}</div>{busy === 'backtest' && <div className="strong-v2-learning-loading"><Loader2 size={16} className="animate-spin" />正在按截面计算前瞻结果，首次可能需要较长时间…</div>}{backtest && <><div className="strong-v2-backtest-table-wrap"><table className="strong-v2-backtest-table"><thead><tr><th>结果窗口</th><th>样本数</th><th>正收益占比</th><th>平均收益</th><th>MFE</th><th>MAE</th><th>单笔最大回撤</th><th>盈亏比</th></tr></thead><tbody>{Object.entries(metrics).map(([key, item]: [string, AnyMap]) => <tr key={key}><td>{key.replace('t_plus_', 'T+')}</td><td>{item.sample_size ?? '--'}</td><td>{finite(item.win_rate) ? `${item.win_rate.toFixed(1)}%` : '--'}</td><td className={tone(item.average_return)}>{finite(item.average_return) ? `${item.average_return.toFixed(2)}%` : '--'}</td><td>{finite(item.mfe) ? `${item.mfe.toFixed(2)}%` : '--'}</td><td className="is-down">{finite(item.mae) ? `${item.mae.toFixed(2)}%` : '--'}</td><td className="is-down">{finite(item.max_drawdown) ? `${item.max_drawdown.toFixed(2)}%` : '--'}</td><td>{finite(item.profit_loss_ratio) ? item.profit_loss_ratio.toFixed(2) : '--'}</td></tr>)}</tbody></table></div><div className="strong-v2-backtest-foot"><span>状态确认率：{finite(backtest.confirmation_rate) ? `${backtest.confirmation_rate.toFixed(1)}%` : '--'}</span><span>状态减弱/失效率：{finite(backtest.failure_rate) ? `${backtest.failure_rate.toFixed(1)}%` : '--'}</span>{backtest.false_breakout && <span>近似假突破率：{finite(backtest.false_breakout.rate) ? `${backtest.false_breakout.rate.toFixed(1)}%` : '--'}</span>}</div><p className="strong-v2-learning-method">{text(backtest.method)} {text(backtest.promotion)}</p></>}{!backtest && !busy && <div className="strong-v2-learning-empty">选择 Skill 和结果窗口后运行 Shadow 回测；统计结果不会改写当前 ACTION。</div>}</div>}
    </div>}
  </section>;
}

export default function StrongStockV2Dashboard({ v2, symbol, onRefresh }: { v2: AnyMap; symbol?: string; onRefresh?: () => void }) {
  const risk = v2?.risk || {};
  const qts = v2?.quantity_time_space || {};
  const mainForce = v2?.main_force || {};
  const ma = v2?.moving_average || {};
  const zones = v2?.zones || {};
  const degree = v2?.three_degree || {};
  const theme = v2?.theme || {};
  const character = v2?.stock_character || {};
  const stacking = v2?.stacking || {};
  const buy = v2?.buy_point || {};
  const sell = v2?.sell || {};
  const consensus = v2?.consensus || {};
  const allSignals: AnyMap[] = v2?.signals || [];
  const patterns: AnyMap[] = v2?.big_patterns || [];
  const stars: AnyMap[] = v2?.stars || [];
  const profits: AnyMap[] = v2?.profit_patterns || [];
  const activePattern = patterns.find((item) => item.status !== 'NOT_FOUND');
  const activeStar = stars.find((item) => item.status !== 'NOT_FOUND');
  const activeProfit = profits.find((item) => item.status !== 'NOT_FOUND');
  const bookState = [
    { book: '猎取强势股', state: `${text(zones.zone, '未形成明确交易区')} · ${text(mainForce.intent, '暂不判断')}`, detail: `均线：${text(ma.stage, '数据不足')}`, icon: Target },
    { book: '暴涨大形态', state: text(activePattern?.name, '未形成明确大形态'), detail: `生命周期：${text(activePattern?.lifecycle, 'NOT_FOUND')}`, icon: Layers3 },
    { book: '暴涨之星', state: text(activeStar?.name, '未形成明确星线'), detail: `状态：${statusLabel(activeStar?.status)}`, icon: TrendingUp },
  ];

  if (v2?.status === 'UNAVAILABLE') return <section className="strong-v2-unavailable"><CircleAlert size={18} /><span>V2 Shadow分析层暂时不可用，旧版决策结果仍可查看。{v2.error_type ? `（${v2.error_type}）` : ''}</span></section>;

  return <section className="strong-v2-shell" aria-label="强势股交易决策系统 V2.0">
    <header className="strong-v2-header"><div><div className="strong-v2-eyebrow">THREE-BOOK DECISION TERMINAL · POINT-IN-TIME RESEARCH</div><h1>三书完整强势股交易决策系统 <b>V2.0</b></h1><p>风险 → 量时空 → 主力 → 量价 → 均线 → A/B/C → 三度 → 大形态 → 暴涨之星 → 互证</p></div><div className="strong-v2-header-actions"><span className="strong-v2-mode">{text(v2.mode, 'SHADOW')}</span><button type="button" onClick={onRefresh} title="刷新分析"><Database size={13} />刷新</button></div></header>
    <nav className="strong-v2-local-nav" aria-label="V2模块导航">{NAV_ITEMS.map(([label, id]) => <a key={id} href={`#${id}`}>{label}</a>)}</nav>

    <div id="v2-overview" className="strong-v2-overview-grid">
      <V2Panel title="当前三书状态" subtitle="并行状态机 · 明确风险不被机会平均抵消" icon={BookOpenCheck} className="strong-v2-book-panel"><div className="strong-v2-book-state-list">{bookState.map(({ book, state, detail, icon: Icon }) => <div key={book}><Icon size={15} /><div><b>{book}</b><strong>{state}</strong><small>{detail}</small></div></div>)}</div><div className="strong-v2-action-row"><span>ACTION（沿用旧版，V2不改写）</span><strong className={tone(v2.action)}>{text(v2.action, 'NO_TRADE')}</strong><p>{text(v2.consensus?.note, 'Shadow层仅用于研究与验证。')}</p></div></V2Panel>
      <V2Panel title="决策冲突矩阵" subtitle="明确风险 > 明确机会" icon={CircleAlert} risk={consensus.dominant_side === 'RISK'}><div className="strong-v2-consensus-main"><strong className={consensus.dominant_side === 'RISK' ? 'is-down' : 'is-up'}>{text(consensus.level, '冲突')}</strong><span>{consensus.status === 'CONFLICT_FOUND' ? '发现显式冲突' : '当前无显式冲突'}</span></div><div className="strong-v2-consensus-books"><Metric label="猎取强势股" value={text(consensus.hunter?.state)} /><Metric label="暴涨大形态" value={text(consensus.big_pattern?.state)} /><Metric label="暴涨之星" value={text(consensus.star?.state)} /></div>{(consensus.conflicts || []).length ? <div className="strong-v2-conflict-list">{consensus.conflicts.map((item: AnyMap) => <div key={item.code}><AlertTriangle size={13} /><span>{text(item.text)}</span></div>)}</div> : <div className="strong-v2-ok-line"><CheckCircle2 size={13} />暂无需要升级的冲突条件</div>}</V2Panel>
    </div>

    <div className="strong-v2-grid strong-v2-grid-2"><RiskPanel risk={risk} /><V2Panel id="v2-qts" title="量时空" subtitle="量 × 时间 × 空间 · 机会和压力分别呈现" icon={Gauge}><div className="strong-v2-qts-grid"><div><Metric label="时间" value={text(qts.time?.state)} valueClass={tone(qts.time?.state)} detail={qts.time?.evidence?.[0]?.text} /><Metric label="空间" value={text(qts.space?.state)} valueClass={tone(qts.space?.state)} detail={qts.space?.evidence?.[0]?.text} /><Metric label="量" value={text(qts.quantity?.state)} valueClass={tone(qts.quantity?.state)} detail={qts.quantity?.evidence?.[0]?.text} /></div><div className="strong-v2-dual-score"><div><span>机会</span><strong className="is-up">{numberText(qts.opportunity, 0)}</strong></div><div><span>压力</span><strong className="is-down">{numberText(qts.risk, 0)}</strong></div></div></div><div className="strong-v2-risk-source"><span>风险来源</span>{(qts.risk_sources || []).length ? qts.risk_sources.map((item: string) => <em key={item}>{item}</em>) : <small>当前没有额外压力名称</small>}</div></V2Panel></div>

    <div className="strong-v2-grid strong-v2-grid-2"><V2Panel id="v2-main-force" title="主力身影与拉升意图" subtitle="只描述可观察的成交、价格和承接证据" icon={TrendingUp}><div className="strong-v2-main-force-top"><div><span>主力身影</span><strong className={tone(mainForce.presence)}>{text(mainForce.presence || mainForce.state)}</strong></div><div><span>方向</span><strong className={tone(mainForce.direction)}>{text(mainForce.direction)}</strong></div><div><span>阶段</span><strong>{text(mainForce.stage)}</strong></div><div><span>持续性</span><strong>{text(mainForce.continuity)}</strong></div></div><div className="strong-v2-intent"><span>程序化主力逻辑</span><b className={tone(mainForce.intent)}>{text(mainForce.intent)}</b>{mainForce.intent_subtype && <em>子型：{text(mainForce.intent_subtype)}</em>}</div><div className="strong-v2-path">{(mainForce.behavior_path || []).map((item: AnyMap, index: number) => <div key={item.step}><span>{index + 1}</span><b>{text(item.step)}</b><em className={item.status === 'OBSERVED' ? 'is-up' : 'is-warn'}>{item.status === 'OBSERVED' ? '已观察' : '待验证'}</em>{index < (mainForce.behavior_path || []).length - 1 && <ArrowRight size={11} />}</div>)}</div><Evidence items={mainForce.evidence} limit={4} /></V2Panel><V2Panel id="v2-ma" title="均线归位" subtitle="排列 → 角度 → 距离；五阶段独立展示" icon={LineChart}><div className="strong-v2-ma-stage"><strong className={tone(ma.stage)}>{text(ma.stage)}</strong><span>{text(ma.slope_state)}</span><em>距离 {percent(ma.distance_pct)}</em></div><div className="strong-v2-ma-values">{Object.entries(ma.values || {}).map(([key, value]) => <div key={key}><span>{key.toUpperCase()}</span><b>{numberText(value, 2)}</b></div>)}</div><div className="strong-v2-ma-evolution">{(ma.evolution || []).slice(-7).map((item: AnyMap, index: number) => <span key={`${item.date || index}-${item.stage}`}>{text(item.stage)}</span>)}</div><p className="strong-v2-note">反向退化：{text(ma.reverse_degradation)}</p><SignalCollection title="均线五阶段技能" signals={ma.signals} limit={5} /></V2Panel></div>

    <div className="strong-v2-grid strong-v2-grid-2"><V2Panel id="v2-zones" title="A / B / C 交易区几何" subtitle="区域状态、上下沿、攻击线和成本线" icon={Target} risk={zones.zone === '风险C区'}><div className="strong-v2-zone-hero"><strong className={tone(zones.zone)}>{text(zones.zone)}</strong><span>{text(zones.stage)}</span></div><div className="strong-v2-zone-metrics"><Metric label="区域上沿" value={numberText(zones.upper, 2)} /><Metric label="区域下沿" value={numberText(zones.lower, 2)} /><Metric label="短期攻击线" value={numberText(zones.short_attack_line, 2)} /><Metric label="中长期成本线" value={numberText(zones.mid_long_cost_line, 2)} /><Metric label="小A点" value={numberText(zones.small_a_point, 2)} /><Metric label="失效价格" value={numberText(zones.invalidation_price, 2)} /></div><div className="strong-v2-zone-reasons">{(zones.reasons || []).map((item: string) => <span key={item}>{item}</span>)}</div><SignalCollection title="区域状态" signals={zones.signals} limit={3} /></V2Panel><V2Panel id="v2-three-degree" title="三度行大道" subtitle="厚度、力度、速度不合并成一个总分" icon={BarChart3}><div className="strong-v2-degree-grid">{(['thickness', 'strength', 'speed'] as const).map((key) => { const item = degree[key] || {}; const label = key === 'thickness' ? '厚度' : key === 'strength' ? '力度' : '速度'; return <div key={key}><span>{label}</span><strong className={tone(item.state)}>{numberText(item.value, 0)}</strong><b>{text(item.state)}</b><em>{text(item.change)}</em><small>{(item.evidence || []).join(' · ')}</small></div>; })}</div><div className="strong-v2-shadow-note">三度模式：{text(v2.empirical_layer?.action_impact, 'DISABLED_UNTIL_VALIDATED')} · 样本 {numberText(v2.empirical_layer?.sample_size, 0)}</div></V2Panel></div>

    <div className="strong-v2-grid strong-v2-grid-2"><V2Panel id="v2-patterns" title="暴涨大形态" subtitle="统一生命周期：种子、构建、成熟、测试、突破、确认、失败" icon={Layers3}><div className="strong-v2-feature-summary"><div><span>主形态</span><strong>{text(activePattern?.name)}</strong></div><div><span>子型/生命周期</span><strong>{text(activePattern?.subtype)} · {text(activePattern?.lifecycle)}</strong></div><div><span>关键价格</span><strong>{numberText(activePattern?.chart_annotations?.[0]?.key_price, 2)}</strong></div></div><SignalCollection title="形态技能库" signals={patterns} limit={7} /></V2Panel><V2Panel id="v2-stars" title="暴涨之星" subtitle="前置趋势 + 位置 + 量 + 均线 + 主力 + 后续确认" icon={TrendingUp}><div className="strong-v2-feature-summary"><div><span>当前星线</span><strong>{text(activeStar?.name)}</strong></div><div><span>状态</span><strong className={tone(activeStar?.status)}>{statusLabel(activeStar?.status)}</strong></div><div><span>上下文</span><strong>{text(v2.stars?.[0]?.metrics?.position120 ? `位置 ${numberText(v2.stars[0].metrics.position120, 0)}` : '等待上下文')}</strong></div></div><SignalCollection title="星线技能库" signals={stars} limit={7} /></V2Panel></div>

    <div className="strong-v2-grid strong-v2-grid-2"><V2Panel id="v2-profit" title="经典盈利模式与个股股性" subtitle="模式识别不等于交易指令；股性属于工程观察层" icon={BookOpenCheck}><div className="strong-v2-feature-summary"><div><span>当前模式</span><strong>{text(activeProfit?.name)}</strong></div><div><span>模式状态</span><strong className={tone(activeProfit?.status)}>{statusLabel(activeProfit?.status)}</strong></div><div><span>股性标签</span><strong>{text(character.engineering_label)}</strong></div></div><SignalCollection title="盈利模式技能" signals={profits} limit={6} /><div className="strong-v2-character"><span>历史样本</span><b>{numberText(character.historical_samples, 0)}</b><span>波动</span><b>{numberText(character.features?.volatility, 2)}</b><span>强势后延续</span><b>{percent(character.features?.up_day_continuation_rate)}</b></div></V2Panel><V2Panel title="量能体叠加路径" subtitle="基础层 → 图表层 → 题材层 → 买点 → 风险冲突" icon={Database}><div className="strong-v2-stack-path">{(stacking.path || []).map((stage: AnyMap) => <div key={stage.stage}><h3>{text(stage.stage)}</h3><div>{(stage.nodes || []).map((node: AnyMap) => <span key={node.name} className={node.status === 'RISK' ? 'is-risk' : node.status === 'PASS' ? 'is-pass' : 'is-watch'}>{node.name}<small>{node.status === 'PASS' ? '通过' : node.status === 'RISK' ? '风险' : '观察'}</small></span>)}</div></div>)}</div><div className="strong-v2-stack-level"><span>当前叠加状态</span><strong className={tone(stacking.level)}>{text(stacking.level)}</strong><p>{text(stacking.note)}</p></div></V2Panel></div>

    <div id="v2-buy-sell" className="strong-v2-grid strong-v2-grid-2"><V2Panel title="题材互证与买点等级" subtitle="题材类型、热点等级与四级买点分开显示" icon={BrainCircuit}><div className="strong-v2-theme-grid"><Metric label="题材" value={text(theme.theme_name)} /><Metric label="题材类型" value={text(theme.theme_type)} valueClass={tone(theme.theme_type)} /><Metric label="热点等级" value={text(theme.hotspot_level)} valueClass={tone(theme.hotspot_level)} /><Metric label="题材阶段" value={text(theme.theme_stage)} /></div><Evidence items={theme.evidence} limit={3} /><div className="strong-v2-buy-levels">{(buy.levels || []).map((item: AnyMap) => <div key={item.name} className={item.status === 'CONFIRMED' ? 'is-active' : ''}><b>{item.name}</b><StatusPill value={item.status} /><small>{item.status === 'CONFIRMED' ? (item.matched || []).join(' · ') : (item.missing || []).slice(0, 2).join(' · ')}</small></div>)}</div><p className="strong-v2-note">当前分类：<b className={buy.is_imagined ? 'is-warn' : 'is-up'}>{text(buy.level)}</b>。{text(buy.note)}</p></V2Panel><V2Panel title="卖出风险雷达" subtitle="明显遇顶不等于已经见顶；C区优先" icon={ShieldAlert} risk={sell.risk_priority === 'RISK'}><div className="strong-v2-sell-grid"><Metric label="明显见顶" value={text(sell.obvious_top?.state)} valueClass={tone(sell.obvious_top?.state)} /><Metric label="明显遇顶" value={text(sell.meet_top?.state)} valueClass={tone(sell.meet_top?.state)} /><Metric label="C区卖出" value={text(sell.c_zone?.state)} valueClass={tone(sell.c_zone?.state)} /><Metric label="经典现顶" value={text(sell.classic_top?.state)} valueClass={tone(sell.classic_top?.state)} /></div><div className="strong-v2-matched-list">{(sell.classic_top?.matched || []).map((item: string) => <span key={item}>{item}</span>)}</div><SignalCollection title="卖出技能" signals={sell.signals} limit={3} /></V2Panel></div>

    <V2Panel id="v2-consensus" title="三书互证、AI解释与审计" subtitle="结论来自哪些层、还缺什么、什么条件会推翻" icon={BookOpenCheck}><div className="strong-v2-bottom-grid"><div><div className="strong-v2-consensus-banner"><strong className={consensus.dominant_side === 'RISK' ? 'is-down' : 'is-up'}>{text(consensus.level)}</strong><span>主导侧：{text(consensus.dominant_side)}</span></div><div className="strong-v2-conflict-list">{(consensus.conflicts || []).map((item: AnyMap) => <div key={item.code}><AlertTriangle size={13} /><span>{item.text}</span></div>)}</div><div className="strong-v2-explanation"><h3>当前判断</h3><p>{text(v2.explanation?.current_judgement)}</p><h3>主要矛盾</h3><p>{text(v2.explanation?.main_contradiction)}</p><h3>为什么不是其他形态</h3><ul className="strong-v2-why-not">{(v2.explanation?.why_not || ['未满足其他形态的完整前置条件；单一K线或单一指标不会升级为确认。']).slice(0, 5).map((item: string) => <li key={item}>{item}</li>)}</ul><h3>下一步</h3><Evidence items={(v2.explanation?.next_step || []).map((item: string) => ({ text: item }))} limit={4} /></div></div><div className="strong-v2-audit"><Metric label="数据质量" value={text(v2.data_quality?.status)} /><Metric label="日线样本" value={numberText(v2.data_quality?.bar_count, 0)} /><Metric label="价格口径" value={text(v2.data_quality?.price_basis)} /><Metric label="V2信号数" value={numberText(allSignals.length, 0)} /><h3>知识层分布</h3><div className="strong-v2-layer-counts">{Object.entries(v2.explanation?.data_basis || {}).map(([key, value]) => <span key={key}>{key}<b>{text(value)}</b></span>)}</div><p className="strong-v2-note">{text(v2.data_quality?.note)}</p></div></div></V2Panel>
    {<HistoryWorkspace v2={v2} symbol={symbol || v2.symbol || ''} />}
    <footer className="strong-v2-footer"><span>V2.0 · {text(v2.engine_version)} · Shadow分析层</span><span>工程置信度不是收益概率；主力意图只按可观察证据描述。</span><span>历史/缺失字段不会被默认值伪装。</span></footer>
  </section>;
}
