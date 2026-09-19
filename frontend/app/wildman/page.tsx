'use client';

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  Activity, AlertTriangle, BarChart3, BookOpenCheck, BrainCircuit,
  CalendarCheck, CheckCircle2, ChevronRight, CircleSlash2, Clock3,
  Crosshair, Database, Gauge, Layers3, ListChecks, Loader2, Menu,
  Radar, RefreshCw, Save, ShieldAlert, Target, TrendingUp, X,
  type LucideIcon,
} from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';

type AnyMap = Record<string, any>;
type ViewKey = 'dashboard' | 'cycle' | 'mainline' | 'candidates' | 'setups' | 'intraday' | 'risk' | 'plan' | 'review' | 'classic';

const NAV: Array<{ key: ViewKey; label: string; icon: LucideIcon }> = [
  { key: 'dashboard', label: '野人哥驾驶舱', icon: Gauge },
  { key: 'cycle', label: '情绪周期', icon: Activity },
  { key: 'mainline', label: '主线雷达', icon: Radar },
  { key: 'candidates', label: '核心选股', icon: Target },
  { key: 'setups', label: '买点雷达', icon: Crosshair },
  { key: 'intraday', label: '盘口与主力阶段', icon: BarChart3 },
  { key: 'risk', label: '风险 / 仓位 / 卖点', icon: ShieldAlert },
  { key: 'plan', label: '盘前计划与预期', icon: CalendarCheck },
  { key: 'review', label: '复盘与纠错', icon: BookOpenCheck },
  { key: 'classic', label: '经典战法工具箱', icon: ListChecks },
];

const ROLE_ORDER = ['空间龙/总龙头', '核心中军', '先锋', '题材老龙', '穿越龙', '补涨龙', '中位股', '跟风', '杂毛/排除'];

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function safe(value: unknown, fallback = '--'): string {
  const text = String(value ?? '').trim();
  return text || fallback;
}

function pct(value: unknown): string {
  return finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '--';
}

function tone(value: unknown): string {
  return finite(value) && value > 0 ? 'text-up' : finite(value) && value < 0 ? 'text-down' : 'text-text-secondary';
}

function statusTone(value: unknown): string {
  const text = String(value || '');
  if (['模式条件成立', '核心主线确认', '良性', '超预期'].includes(text)) return 'border-up/30 bg-up/5 text-up';
  if (['风险否决', '退潮', '差/无承接', '不及预期', '主线退潮'].includes(text)) return 'border-down/30 bg-down/5 text-down';
  if (['等待确认', '高潮', '高质量候选', '主线走弱'].includes(text)) return 'border-warn/30 bg-warn/5 text-warn';
  return 'border-border bg-white/[0.02] text-text-secondary';
}

function Badge({ children, value }: { children: ReactNode; value?: unknown }) {
  return <span className={`inline-flex min-h-6 items-center rounded border px-2 text-[11px] ${statusTone(value ?? children)}`}>{children}</span>;
}

function Panel({ title, icon: Icon, meta, children, className = '' }: { title: string; icon: LucideIcon; meta?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`min-w-0 overflow-hidden rounded-md border border-border bg-card ${className}`}>
    <header className="flex min-h-12 items-center justify-between gap-3 border-b border-border px-4 py-2.5">
      <h2 className="flex min-w-0 items-center gap-2 text-sm font-semibold text-text"><Icon size={15} className="shrink-0 text-accent" />{title}</h2>
      {meta && <div className="shrink-0 text-[10px] text-text-secondary">{meta}</div>}
    </header>
    <div className="p-4">{children}</div>
  </section>;
}

function Fact({ label, value, detail }: { label: string; value: ReactNode; detail?: ReactNode }) {
  return <div className="min-w-0 border-b border-border py-2.5 last:border-0">
    <div className="flex min-w-0 items-center justify-between gap-3 text-xs"><span className="text-text-secondary">{label}</span><b className="text-right font-medium text-text">{value}</b></div>
    {detail && <div className="mt-1 text-[10px] leading-4 text-text-secondary">{detail}</div>}
  </div>;
}

function RuleRows({ rows }: { rows: AnyMap[] }) {
  return <div className="divide-y divide-border">{rows.length ? rows.map((row, index) => <div key={`${row.rule_id}-${index}`} className="grid gap-2 py-2.5 text-[11px] sm:grid-cols-[1fr_1fr_auto]">
    <div><div className="text-text">{safe(row.rule_name)}</div><div className="mt-0.5 font-mono text-[9px] text-text-secondary">{safe(row.rule_id)}</div></div>
    <div className="min-w-0 break-words text-text-secondary">要求：{safe(row.required)}<br />实际：{typeof row.actual === 'object' ? JSON.stringify(row.actual) : safe(row.actual)}</div>
    <span className={row.passed === true ? 'text-up' : row.passed === false ? 'text-down' : 'text-warn'}>{row.passed === true ? '通过' : row.passed === false ? '未通过' : '待确认'}</span>
  </div>) : <div className="py-5 text-xs text-text-secondary">暂无规则证据</div>}</div>;
}

function CandidateCard({ row, onOpen }: { row: AnyMap; onOpen: (symbol: string) => void }) {
  const setup = row.setup || {};
  return <button type="button" onClick={() => onOpen(row.symbol)} className="block w-full rounded-md border border-border bg-bg p-3 text-left transition-colors hover:border-accent/50">
    <div className="flex min-w-0 items-start justify-between gap-3">
      <div className="min-w-0"><div className="flex items-center gap-2"><b className="truncate text-sm text-text">{safe(row.name)}</b><span className="font-mono text-[10px] text-text-secondary">{row.symbol}</span></div><div className="mt-1 truncate text-[10px] text-text-secondary">{safe(row.theme_name, '题材待归类')} · {safe(row.role?.role)}</div></div>
      <Badge value={row.candidate_status}>{row.candidate_status}</Badge>
    </div>
    <div className="mt-3 grid grid-cols-3 gap-2 border-y border-border py-2 text-[10px]">
      <div><span className="block text-text-secondary">模式</span><b className="mt-1 block truncate font-medium text-text">{safe(setup.name, '未形成')}</b></div>
      <div><span className="block text-text-secondary">盈亏比</span><b className="mt-1 block font-mono font-medium text-text">{finite(row.risk_reward) ? `${row.risk_reward}:1` : '待确认'}</b></div>
      <div><span className="block text-text-secondary">涨跌</span><b className={`mt-1 block font-mono font-medium ${tone(row.change_pct)}`}>{pct(row.change_pct)}</b></div>
    </div>
    <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-text-secondary"><span className="truncate">锚点：{safe(setup.anchor_type, '未形成')} {finite(setup.anchor_price) ? setup.anchor_price.toFixed(2) : ''}</span><ChevronRight size={13} /></div>
  </button>;
}

function CandidateDetail({ row, loading, onClose, onRefresh }: { row: AnyMap | null; loading: boolean; onClose: () => void; onRefresh: () => void }) {
  if (!row && !loading) return null;
  return <div className="fixed inset-0 z-[80] flex justify-end bg-black/55" onClick={onClose}>
    <aside className="h-full w-full max-w-[760px] overflow-y-auto border-l border-border bg-bg shadow-2xl" onClick={(event) => event.stopPropagation()}>
      <header className="sticky top-0 z-10 flex min-h-14 items-center justify-between gap-3 border-b border-border bg-card/95 px-4 backdrop-blur">
        <div className="min-w-0"><h2 className="truncate text-sm font-semibold text-text">{row ? `${safe(row.name)} · ${safe(row.symbol)}` : '读取候选详情'}</h2><p className="mt-0.5 text-[10px] text-text-secondary">Strict Wildman · 规则可追溯</p></div>
        <div className="flex items-center gap-1"><button type="button" onClick={onRefresh} className="grid h-8 w-8 place-items-center rounded border border-border text-text-secondary hover:text-accent" title="刷新日线与猫爪Level-2"><RefreshCw size={14} /></button><button type="button" onClick={onClose} className="grid h-8 w-8 place-items-center rounded border border-border text-text-secondary hover:text-text" title="关闭"><X size={15} /></button></div>
      </header>
      {loading ? <div className="grid h-64 place-items-center text-xs text-text-secondary"><Loader2 size={20} className="animate-spin text-accent" /></div> : row && <div className="space-y-4 p-4">
        <div className="grid gap-3 sm:grid-cols-4"><div className="rounded border border-border bg-card p-3"><span className="text-[10px] text-text-secondary">周期</span><b className="mt-1 block text-xs text-text">{row.cycle?.cycle}</b></div><div className="rounded border border-border bg-card p-3"><span className="text-[10px] text-text-secondary">角色</span><b className="mt-1 block text-xs text-text">{row.role?.role}</b></div><div className="rounded border border-border bg-card p-3"><span className="text-[10px] text-text-secondary">节点</span><b className="mt-1 block text-xs text-text">{row.setup?.name}</b></div><div className="rounded border border-border bg-card p-3"><span className="text-[10px] text-text-secondary">结论</span><b className="mt-1 block text-xs text-text">{row.candidate_status}</b></div></div>
        <Panel title="规则解释链" icon={Layers3}>{(row.explain_chain || []).map((item: AnyMap, index: number) => <div key={`${item.stage}-${index}`} className="grid grid-cols-[72px_1fr] gap-3 border-b border-border py-2.5 text-xs last:border-0"><span className="text-text-secondary">{item.stage}</span><div><b className="font-medium text-text">{item.result}</b><p className="mt-1 text-[10px] text-text-secondary">{item.detail}</p></div></div>)}</Panel>
        <div className="grid gap-4 md:grid-cols-2"><Panel title="防守与仓位" icon={ShieldAlert}><Fact label="仓位动作" value={`${safe(row.position?.mode)} · ${safe(row.position?.range)}`} detail={row.position?.reason} /><Fact label="防守锚点" value={`${safe(row.exit_plan?.anchor_type)} ${finite(row.exit_plan?.anchor_price) ? row.exit_plan.anchor_price.toFixed(2) : ''}`} /><Fact label="价格止损" value={finite(row.exit_plan?.price_stop) ? row.exit_plan.price_stop.toFixed(2) : row.exit_plan?.max_loss_rule} /><Fact label="逻辑失效" value={row.exit_plan?.logic_stop} /></Panel><Panel title="猫爪 Level-2 盘口" icon={Database} meta={row.level2?.pending ? '同步中' : row.level2?.available ? '已接入' : '等待样本'}><Fact label="承接结论" value={<Badge value={row.support?.state}>{safe(row.support?.state)}</Badge>} detail={row.support?.note} /><Fact label="盘口失衡 OBI" value={row.level2?.summary?.obi?.label || '等待样本'} detail={finite(row.level2?.summary?.obi?.value) ? row.level2.summary.obi.value.toFixed(2) : undefined} /><Fact label="买方吸收" value={row.level2?.summary?.absorption?.buy?.label || '等待样本'} /><Fact label="买盘补单" value={row.level2?.summary?.replenishment?.bid?.label || '等待样本'} /><Fact label="疑似派发风险" value={row.level2?.summary?.distribution?.label || '等待样本'} /></Panel></div>
        <Panel title="完整规则依据" icon={ListChecks}><RuleRows rows={[...(row.passed_rules || []), ...(row.waiting_rules || []), ...(row.failed_rules || [])]} /></Panel>
      </div>}
    </aside>
  </div>;
}

export default function WildmanPage() {
  const [view, setView] = useState<ViewKey>('dashboard');
  const [mobileNav, setMobileNav] = useState(false);
  const [payload, setPayload] = useState<AnyMap | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [excludeStar, setExcludeStar] = useState(true);
  const [excludeGem, setExcludeGem] = useState(true);
  const [selected, setSelected] = useState<AnyMap | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [reviewPeriod, setReviewPeriod] = useState<'daily' | 'weekly' | 'monthly'>('daily');
  const [review, setReview] = useState<AnyMap | null>(null);
  const [saving, setSaving] = useState(false);
  const [reviewForm, setReviewForm] = useState({ symbol: '', setup_type: 'FIRST_DIVERGENCE', pnl_pct: '', mode_inside: true, review_text: '' });

  const load = useCallback(async (refresh = false) => {
    setLoading(true); if (refresh) setRefreshing(true); setError('');
    try {
      const params = new URLSearchParams({ refresh: String(refresh), exclude_star_market: String(excludeStar), exclude_gem: String(excludeGem) });
      const response = await apiFetch<{ data: AnyMap }>(`/wildman/dashboard?${params}`, { timeoutMs: 60000 });
      setPayload(response.data);
    } catch (caught) { setError(friendlyApiError(caught, '野人哥交易决策模块暂时不可用')); }
    finally { setLoading(false); setRefreshing(false); }
  }, [excludeGem, excludeStar]);

  useEffect(() => { void load(false); }, [load]);

  const loadDetail = useCallback(async (symbol: string, refresh = false) => {
    setDetailLoading(true); setSelected((current) => current?.symbol === symbol ? current : null);
    try {
      const response = await apiFetch<{ data: AnyMap }>(`/wildman/candidates/${encodeURIComponent(symbol)}?refresh=${refresh}`, { timeoutMs: 60000 });
      setSelected(response.data);
    } catch (caught) { setError(friendlyApiError(caught, '候选详情暂时不可用')); setSelected(null); }
    finally { setDetailLoading(false); }
  }, []);

  const loadReview = useCallback(async () => {
    try { const response = await apiFetch<{ data: AnyMap }>(`/wildman/review/${reviewPeriod}`, { timeoutMs: 45000 }); setReview(response.data); }
    catch (caught) { setError(friendlyApiError(caught, '复盘记录暂时不可用')); }
  }, [reviewPeriod]);

  useEffect(() => { if (view === 'review') void loadReview(); }, [loadReview, view]);

  const saveReview = async () => {
    setSaving(true);
    try {
      await apiFetch('/wildman/manual-review', { method: 'POST', body: JSON.stringify({ ...reviewForm, entry_date: new Date().toISOString().slice(0, 10), pnl_pct: reviewForm.pnl_pct === '' ? null : Number(reviewForm.pnl_pct) }) });
      setReviewForm({ symbol: '', setup_type: 'FIRST_DIVERGENCE', pnl_pct: '', mode_inside: true, review_text: '' });
      await loadReview();
    } catch (caught) { setError(friendlyApiError(caught, '复盘保存失败')); }
    finally { setSaving(false); }
  };

  const cycle = payload?.cycle || {};
  const candidates = payload?.candidates || [];
  const focus = candidates.filter((row: AnyMap) => ['模式条件成立', '等待确认'].includes(row.candidate_status));
  const reviewMetrics = review?.metrics || {};
  const activeTitle = NAV.find((item) => item.key === view)?.label;
  const roleGroups = useMemo(() => ROLE_ORDER.map((role) => ({ role, rows: candidates.filter((row: AnyMap) => row.role?.role === role) })).filter((group) => group.rows.length), [candidates]);

  const renderCandidates = (rows: AnyMap[]) => <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">{rows.length ? rows.map((row) => <CandidateCard key={`${row.symbol}-${row.setup?.type}`} row={row} onOpen={(symbol) => void loadDetail(symbol)} />) : <div className="py-8 text-xs text-text-secondary">当前没有满足该组规则的候选</div>}</div>;

  const renderView = () => {
    if (!payload) return null;
    if (view === 'dashboard') return <div className="space-y-4"><div className="grid gap-4 xl:grid-cols-[1.1fr_1fr_1fr]"><Panel title="今天能不能做" icon={Gauge} meta={payload.trade_date}><div className="flex items-center justify-between gap-3"><div><div className="text-[10px] text-text-secondary">当前周期</div><div className="mt-1 text-xl font-semibold text-text">{cycle.cycle}</div><div className="mt-1 text-xs text-text-secondary">{cycle.cycle_node}</div></div><div className="text-right"><div className="text-[10px] text-text-secondary">仓位参考</div><b className="mt-1 block font-mono text-lg text-accent">{cycle.position_range}</b></div></div><div className="mt-4 grid grid-cols-2 gap-3"><div className="border-l-2 border-up pl-3"><div className="text-[10px] text-text-secondary">允许</div>{(cycle.allowed_actions || []).map((item: string) => <div key={item} className="mt-1 text-xs text-text">{item}</div>)}</div><div className="border-l-2 border-down pl-3"><div className="text-[10px] text-text-secondary">禁止</div>{(cycle.forbidden_actions || []).map((item: string) => <div key={item} className="mt-1 text-xs text-text">{item}</div>)}</div></div></Panel><Panel title="核心主线" icon={Radar} meta={`${payload.mainlines?.length || 0}个题材`}>{(payload.mainlines || []).slice(0, 6).map((row: AnyMap) => <div key={row.theme_id} className="flex items-center justify-between gap-3 border-b border-border py-2.5 last:border-0"><div className="min-w-0"><div className="truncate text-xs text-text">{row.theme_name}</div><div className="mt-1 text-[10px] text-text-secondary">涨停 {row.limit_up_count} · 高度 {row.max_limit_height}</div></div><Badge value={row.state}>{row.state}</Badge></div>)}</Panel><Panel title="风险与数据" icon={ShieldAlert}><Fact label="风险否决" value={`${payload.reject_pool?.length || 0}只`} /><Fact label="模式内候选" value={`${focus.length}只`} /><Fact label="数据日期" value={payload.source_status?.data_date || payload.trade_date} /><Fact label="Level-2" value="猫爪按股接入" detail="候选详情按股票和交易日读取逐笔、委托与十档盘口" /><Fact label="规则版本" value={payload.rule_version} /></Panel></div><Panel title="今日重点候选" icon={Target} meta="按角色与节点排序">{renderCandidates(focus.slice(0, 9))}</Panel></div>;
    if (view === 'cycle') return <div className="grid gap-4 xl:grid-cols-[1fr_1.2fr]"><Panel title="情绪周期状态机" icon={Activity}><div className="grid grid-cols-5 gap-1">{['冰点', '启动/试错', '发酵/主升', '高潮', '退潮'].map((item) => <div key={item} className={`grid min-h-20 place-items-center rounded border px-2 text-center text-[11px] ${cycle.cycle === item ? statusTone(item) : 'border-border text-text-secondary'}`}>{item}</div>)}</div><div className="mt-4 text-xs leading-6 text-text-secondary">冰点试错 → 启动确认加 → 主升做分歧 → 高潮减 → 退潮空仓</div></Panel><Panel title="周期证据" icon={ListChecks}><RuleRows rows={cycle.evidence || []} /></Panel></div>;
    if (view === 'mainline') return <div className="space-y-4">{(payload.mainlines || []).map((row: AnyMap) => <Panel key={row.theme_id} title={row.theme_name} icon={Radar} meta={<Badge value={row.state}>{row.state}</Badge>}><div className="grid gap-4 lg:grid-cols-[180px_1fr]"><div><Fact label="涨停数量" value={row.limit_up_count} /><Fact label="空间高度" value={`${row.max_limit_height}板`} /><Fact label="缺失确认" value={row.missing?.length ? row.missing.join('、') : '无'} /></div><RuleRows rows={row.evidence || []} /></div></Panel>)}</div>;
    if (view === 'candidates') return <div className="space-y-5">{roleGroups.map((group) => <Panel key={group.role} title={group.role} icon={Target} meta={`${group.rows.length}只`}>{renderCandidates(group.rows)}</Panel>)}</div>;
    if (view === 'setups') return <div className="space-y-4">{['FIRST_DIVERGENCE', 'WEAK_TO_STRONG', 'SWING_PULLBACK', 'SWING_BREAKOUT', 'B_POINT', 'LIMIT_PULLBACK'].map((type) => { const rows = candidates.filter((row: AnyMap) => row.setup?.type === type); return rows.length ? <Panel key={type} title={safe(rows[0]?.setup?.name)} icon={Crosshair} meta={`${rows.length}只`}>{renderCandidates(rows)}</Panel> : null; })}<Panel title="等待模式形成" icon={Clock3}>{renderCandidates(candidates.filter((row: AnyMap) => row.setup?.type === 'NONE').slice(0, 12))}</Panel></div>;
    if (view === 'intraday') return <div className="grid gap-4 xl:grid-cols-[1fr_1fr]"><Panel title="盘口候选" icon={BarChart3}>{renderCandidates(focus.slice(0, 8))}</Panel><Panel title="猫爪 Level-2 接入状态" icon={Database}><Fact label="数据类型" value="逐笔成交 / 逐笔委托 / 十档盘口" /><Fact label="调用范围" value="候选详情按股按日" /><Fact label="承接指标" value="买方吸收 / OBI / 补单" /><Fact label="风险指标" value="疑似派发 / 异常挂撤单" /><Fact label="存储策略" value="复用现有缓存" detail="本模块只保存规则结论和证据摘要" /></Panel></div>;
    if (view === 'risk') return <div className="grid gap-4 xl:grid-cols-2"><Panel title="今日排除池" icon={CircleSlash2} meta={`${payload.reject_pool?.length || 0}只`}>{renderCandidates((payload.reject_pool || []).slice(0, 16))}</Panel><Panel title="风险 Gate" icon={ShieldAlert}>{['RETREAT_MARKET 退潮期', 'FISH_TAIL 鱼尾行情', 'OVER_CONSENSUS 过度一致', 'FOLLOWER 后排跟风', 'FAKE_BREAKOUT 假突破', 'NO_SUPPORT 无承接', 'ANCHOR_BROKEN 破锚', 'MODE_OUTSIDE 模式外', 'NO_STOP 无止损', 'RISK_REWARD_FAIL 盈亏比不足'].map((item) => <div key={item} className="border-b border-border py-2.5 text-xs text-text last:border-0">{item}</div>)}</Panel></div>;
    if (view === 'plan') return <div className="grid gap-4 xl:grid-cols-[1fr_1fr]"><Panel title="盘前计划" icon={CalendarCheck}><Fact label="市场阶段" value={`${cycle.cycle} · ${cycle.cycle_node}`} /><Fact label="核心主线" value={(payload.mainlines || []).filter((row: AnyMap) => ['核心主线确认', '高质量候选'].includes(row.state)).slice(0, 4).map((row: AnyMap) => row.theme_name).join(' / ') || '等待确认'} /><Fact label="允许模式" value={(cycle.allowed_actions || []).join(' / ')} /><Fact label="禁止模式" value={(cycle.forbidden_actions || []).join(' / ')} /><Fact label="仓位" value={cycle.position_range} /></Panel><Panel title="三套预期剧本" icon={BrainCircuit}><Fact label="超预期" value="持有或等待模式内盘口确认" detail="昨日分歧，竞价高开且带量上攻" /><Fact label="符合预期" value="观察锚点和分时承接" detail="正常溢价或平开换手" /><Fact label="不及预期" value="竞价或开盘优先处理" detail="该强不强、竞价萎缩或无承接；绝不补仓" /></Panel><Panel title="重点盯盘" icon={Target} className="xl:col-span-2">{renderCandidates(focus.slice(0, 10))}</Panel></div>;
    if (view === 'review') return <div className="grid gap-4 xl:grid-cols-[.8fr_1.2fr]"><Panel title="记录交易复盘" icon={Save}><div className="space-y-3"><input value={reviewForm.symbol} onChange={(event) => setReviewForm({ ...reviewForm, symbol: event.target.value.replace(/\D/g, '').slice(0, 6) })} placeholder="股票代码" className="h-9 w-full rounded border border-border bg-bg px-3 text-xs text-text outline-none focus:border-accent" /><select value={reviewForm.setup_type} onChange={(event) => setReviewForm({ ...reviewForm, setup_type: event.target.value })} className="h-9 w-full rounded border border-border bg-bg px-3 text-xs text-text"><option value="FIRST_DIVERGENCE">主升首分歧</option><option value="WEAK_TO_STRONG">弱转强</option><option value="SWING_PULLBACK">波段回踩</option><option value="B_POINT">B点</option><option value="LIMIT_PULLBACK">涨停缩量回踩</option><option value="NONE">模式外</option></select><input value={reviewForm.pnl_pct} onChange={(event) => setReviewForm({ ...reviewForm, pnl_pct: event.target.value })} placeholder="盈亏百分比，例如 -3.5" inputMode="decimal" className="h-9 w-full rounded border border-border bg-bg px-3 text-xs text-text outline-none focus:border-accent" /><label className="flex items-center gap-2 text-xs text-text-secondary"><input type="checkbox" checked={reviewForm.mode_inside} onChange={(event) => setReviewForm({ ...reviewForm, mode_inside: event.target.checked })} />模式内交易</label><textarea value={reviewForm.review_text} onChange={(event) => setReviewForm({ ...reviewForm, review_text: event.target.value })} placeholder="预判、实际、执行与纠错" className="min-h-24 w-full resize-y rounded border border-border bg-bg p-3 text-xs text-text outline-none focus:border-accent" /><button type="button" disabled={saving || reviewForm.symbol.length !== 6} onClick={() => void saveReview()} className="inline-flex h-9 w-full items-center justify-center gap-2 rounded bg-accent px-4 text-xs font-medium text-white disabled:opacity-50">{saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}保存复盘</button></div></Panel><div className="space-y-4"><Panel title="复盘统计" icon={TrendingUp} meta={<div className="flex gap-1">{(['daily', 'weekly', 'monthly'] as const).map((item) => <button key={item} onClick={() => setReviewPeriod(item)} className={`rounded px-2 py-1 ${reviewPeriod === item ? 'bg-accent/15 text-accent' : 'text-text-secondary'}`}>{item === 'daily' ? '日' : item === 'weekly' ? '周' : '月'}</button>)}</div>}><div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{[['交易', reviewMetrics.total], ['模式外占比', finite(reviewMetrics.mode_outside_ratio) ? `${reviewMetrics.mode_outside_ratio}%` : '--'], ['模式内胜率', finite(reviewMetrics.inside_win_rate) ? `${reviewMetrics.inside_win_rate}%` : '--'], ['盈亏比', reviewMetrics.profit_loss_ratio ?? '--']].map(([label, value]) => <div key={String(label)} className="rounded border border-border bg-bg p-3"><span className="text-[10px] text-text-secondary">{label}</span><b className="mt-1 block font-mono text-sm text-text">{value}</b></div>)}</div></Panel><Panel title="交易日志" icon={BookOpenCheck}>{(review?.trades || []).length ? (review?.trades || []).map((row: AnyMap) => <div key={row.trade_id} className="grid gap-2 border-b border-border py-2.5 text-xs last:border-0 sm:grid-cols-[1fr_auto]"><div><b className="font-medium text-text">{row.stock_name || row.symbol} · {row.symbol}</b><p className="mt-1 text-[10px] text-text-secondary">{row.setup_type} · {row.entry_date} · {row.mode_inside ? '模式内' : '模式外'}</p></div><b className={tone(row.pnl_pct)}>{pct(row.pnl_pct)}</b></div>) : <div className="py-5 text-xs text-text-secondary">当前周期还没有交易复盘记录</div>}</Panel></div></div>;
    return <div className="grid gap-4 lg:grid-cols-3">{(payload.classic_toolbox || []).map((row: AnyMap) => <Panel key={row.id} title={row.name} icon={ListChecks} meta={row.status}><Fact label="规则编号" value={row.id} />{row.id === 'WM_CLASSIC_520' && <><Fact label="买点" value="5日线上穿20日线" detail="20日线必须走平或向上，上穿放量" /><Fact label="退出" value="破5日减半，破20日清仓" /></>}{row.id === 'WM_CLASSIC_T' && <><Fact label="触发" value="连续下跌约5日或跌幅约10%" /><Fact label="风险" value="左侧交易" detail="单边下跌或流动性枯竭时可能越做T亏得越多" /></>}{row.id === 'WM_CLASSIC_75A' && <><Fact label="确认" value="75日线拐头 + 放量突破颈线" /><Fact label="防守" value="圆弧底最低或75日线" /></>}</Panel>)}</div>;
  };

  return <div className="min-h-[calc(100vh-96px)] bg-bg text-text">
    <div className="mx-auto grid min-h-[calc(100vh-96px)] max-w-[1920px] lg:grid-cols-[188px_minmax(0,1fr)]">
      <aside className={`${mobileNav ? 'fixed inset-y-0 left-0 z-[70] w-[250px]' : 'hidden'} border-r border-border bg-[#0E131A] p-3 lg:sticky lg:top-10 lg:block lg:h-[calc(100vh-40px)] lg:w-auto lg:overflow-y-auto`}>
        <div className="flex items-center justify-between border-b border-border px-2 pb-4"><div><div className="text-sm font-semibold text-text">野人哥交易决策</div><div className="mt-1 font-mono text-[9px] text-text-secondary">STRICT WILDMAN V1.0</div></div>{mobileNav && <button onClick={() => setMobileNav(false)} className="p-2 text-text-secondary"><X size={16} /></button>}</div>
        <nav className="mt-3 space-y-1">{NAV.map((item) => { const Icon = item.icon; return <button key={item.key} onClick={() => { setView(item.key); setMobileNav(false); }} className={`flex min-h-9 w-full items-center gap-2 rounded px-2.5 text-left text-[11px] ${view === item.key ? 'bg-accent/10 text-accent' : 'text-text-secondary hover:bg-white/[0.03] hover:text-text'}`}><Icon size={14} />{item.label}</button>; })}</nav>
        <div className="mt-5 border-t border-border px-2 pt-4"><div className="text-[9px] text-text-secondary">核心原则</div><div className="mt-2 text-xs leading-5 text-text">保住本金是第一原则<br />慢就是快</div></div>
      </aside>
      {mobileNav && <button className="fixed inset-0 z-[60] bg-black/50 lg:hidden" onClick={() => setMobileNav(false)} aria-label="关闭导航遮罩" />}
      <main className="min-w-0">
        <header className="sticky top-10 z-30 flex min-h-14 items-center justify-between gap-3 border-b border-border bg-bg/95 px-3 backdrop-blur md:px-5"><div className="flex min-w-0 items-center gap-2"><button onClick={() => setMobileNav(true)} className="grid h-8 w-8 shrink-0 place-items-center rounded border border-border text-text-secondary lg:hidden" title="打开模块导航"><Menu size={15} /></button><div className="min-w-0"><h1 className="truncate text-sm font-semibold text-text">{activeTitle}</h1><p className="mt-0.5 truncate text-[9px] text-text-secondary">市场周期 → 主线 → 地位 → 节点 → 盘口 → 风险 → 仓位 → 复盘</p></div></div><div className="flex shrink-0 items-center gap-2"><label className="hidden items-center gap-1.5 text-[10px] text-text-secondary sm:flex"><input type="checkbox" checked={excludeStar} onChange={(event) => setExcludeStar(event.target.checked)} />排除科创</label><label className="hidden items-center gap-1.5 text-[10px] text-text-secondary sm:flex"><input type="checkbox" checked={excludeGem} onChange={(event) => setExcludeGem(event.target.checked)} />排除创业</label><button onClick={() => void load(true)} disabled={loading} className="inline-flex h-8 items-center gap-1.5 rounded border border-border px-2.5 text-[11px] text-text-secondary hover:border-accent hover:text-accent disabled:opacity-50"><RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} /><span className="hidden sm:inline">刷新规则快照</span></button></div></header>
        <div className="p-3 md:p-5">{error && <div className="mb-4 flex items-start gap-2 rounded border border-down/30 bg-down/5 p-3 text-xs text-down"><AlertTriangle size={14} className="mt-0.5 shrink-0" /><span>{error}</span></div>}{loading && !payload ? <div className="grid h-72 place-items-center text-xs text-text-secondary"><div className="text-center"><Loader2 size={22} className="mx-auto animate-spin text-accent" /><p className="mt-3">正在构建周期、主线与候选解释链</p></div></div> : renderView()}</div>
      </main>
    </div>
    <CandidateDetail row={selected} loading={detailLoading} onClose={() => setSelected(null)} onRefresh={() => selected && void loadDetail(selected.symbol, true)} />
  </div>;
}
