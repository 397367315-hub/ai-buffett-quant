'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  Activity, AlertTriangle, BarChart3, BookOpenCheck, BrainCircuit,
  CalendarCheck, CheckCircle2, ChevronRight, CircleSlash2, Clock3,
  Crosshair, Database, Gauge, Layers3, ListChecks, Loader2, Menu,
  Radar, RefreshCw, Save, Search, ShieldAlert, Target, TrendingUp, X,
  type LucideIcon,
} from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';
import KlineChart, { type KlineRow } from '@/components/KlineChart';
import WildmanAccountPanel from '@/components/wildman/WildmanAccountPanel';
import WildmanMainlines from '@/components/wildman/WildmanMainlines';

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

const ROLE_ORDER = ['空间龙/总龙头', '核心中军', '先锋', '题材老龙', '穿越龙', '补涨龙', '中位股', '跟风', '杂毛/排除', '待确认'];
const CYCLE_STAGES = ['冰点', '启动/试错', '发酵/主升', '高潮', '退潮'];
const TOOLBOX_STRATEGIES = [
  {
    id: 'WM_CLASSIC_520',
    name: '520战法',
    horizon: '短线',
    holding_period: '2–5 个交易日',
    summary: '5 日线向上穿越 20 日线，寻找短线强势延续。',
    color: 'cyan',
  },
  {
    id: 'WM_CLASSIC_T',
    name: '老太太扶楼梯',
    horizon: '短线',
    holding_period: '日内 / 五日循环',
    summary: '只服务已有持仓的高抛低吸，不代表新建仓信号。',
    color: 'amber',
  },
  {
    id: 'WM_CLASSIC_75A',
    name: '圆弧底75A',
    horizon: '波段',
    holding_period: '数周至数月',
    summary: '75 日线拐头并突破结构，等待波段级别确认。',
    color: 'violet',
  },
] as const;

type ToolboxStrategy = (typeof TOOLBOX_STRATEGIES)[number] & AnyMap;
type ToolboxEvidence = AnyMap;
type ToolboxRow = AnyMap & { evidence?: ToolboxEvidence[]; risk_notes?: string[] };
type ToolboxScan = AnyMap & {
  status?: 'running' | 'completed' | 'failed';
  progress?: AnyMap;
  rows?: ToolboxRow[];
  strategy?: ToolboxStrategy;
};

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

function shanghaiToday(): string {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
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

function readableValue(value: unknown, fallback = '未提供'): string {
  if (value === true) return '是';
  if (value === false) return '否';
  if (value === null || value === undefined || String(value).trim() === '') return fallback;
  if (Array.isArray(value)) return value.length ? value.map((item) => readableValue(item, fallback)).join('、') : fallback;
  if (typeof value === 'object') {
    const object = value as AnyMap;
    const entries = Object.entries(object);
    return entries.length
      ? entries.map(([key, nested]) => `${coverageLabel(key)}：${readableValue(nested, fallback)}`).join('；')
      : fallback;
  }
  return String(value);
}

function evidenceTone(passed: unknown): string {
  return passed === true ? 'text-up' : passed === false ? 'text-down' : 'text-warn';
}

function mainlinePassedCount(row: AnyMap): number {
  const rawCount = row?.passed_count;
  if (rawCount !== null && rawCount !== undefined && String(rawCount).trim() !== '' && Number.isFinite(Number(rawCount))) return Math.max(0, Math.min(5, Number(rawCount)));
  const steps = Array.isArray(row?.mainline_five_steps) ? row.mainline_five_steps : [];
  return steps.filter((item: AnyMap) => item?.passed === true).length;
}

function cycleConfirmed(cycle: AnyMap): boolean {
  const quality = cycle?.data_quality;
  const qualityText = typeof quality === 'string'
    ? quality.toLowerCase()
    : String(quality?.status || quality?.state || '').toLowerCase();
  return cycle?.confirmed !== false
    && quality?.confirmed !== false
    && !['unknown', 'unavailable', 'insufficient', 'missing', 'stale', '待确认', '未知'].includes(qualityText);
}

function cycleStageIndex(cycle: AnyMap): number {
  if (!cycleConfirmed(cycle)) return -1;
  const value = String(cycle?.cycle || '').trim();
  return CYCLE_STAGES.findIndex((stage) => value === stage || value.includes(stage.split('/')[0]));
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

function DisclosureEvidence({ facts }: { facts: AnyMap }) {
  const coverage = facts.coverage || facts.risk_coverage || {};
  const evidence = Array.isArray(facts.risk_evidence) ? facts.risk_evidence : [];
  const announcements = evidence.filter((item: AnyMap) => item.kind === 'announcement');
  const risks = evidence.filter((item: AnyMap) => item.status === 'risk_hit');
  const title = facts.major_risk === true ? '命中风险，优先核查' : facts.major_risk === false ? '核查范围内未命中重大风险' : '核查范围尚不完整';
  return <Panel title="公告与财务核查" icon={ShieldAlert}>
    <div className="text-xs text-text">{title}</div>
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-secondary">
      <span>核查区间：{safe(coverage.checked_from)} 至 {safe(coverage.checked_to)}</span>
      <span>有效公告：{safe(coverage.announcement_records, '0')} 条</span>
      <span>来源：{Array.isArray(coverage.sources) ? coverage.sources.map(sourceText).join('、') || '尚无有效返回' : '尚无有效返回'}</span>
    </div>
    {coverage.note && <p className="mt-2 text-[11px] leading-5 text-text-secondary">{coverage.note}</p>}
    <div className="mt-3 divide-y divide-border">{[...risks, ...announcements.filter((item: AnyMap) => item.status !== 'risk_hit')].slice(0, 6).map((item: AnyMap, index: number) => <div key={index} className="py-2 text-xs leading-5">
      <div className="flex flex-wrap items-start justify-between gap-2"><span className={item.status === 'risk_hit' ? 'text-warn' : 'text-text'}>{safe(item.title || item.reason)}</span><time className="shrink-0 text-[10px] text-text-secondary">{safe(item.published_at || item.disclosed_at)}</time></div>
      {item.title && <p className="text-[11px] text-text-secondary">{item.reason}</p>}
      {typeof item.url === 'string' && /^https?:\/\//i.test(item.url) && <a className="text-[11px] text-accent" href={item.url} target="_blank" rel="noreferrer">查看原公告</a>}
    </div>)}</div>
    <p className="mt-2 text-[10px] leading-4 text-text-secondary">以已披露信息和本次核查窗口为准；未命中不等于公司不存在其他风险。</p>
  </Panel>;
}

function RuleRows({ rows }: { rows: AnyMap[] }) {
  return <div className="divide-y divide-border">{rows.length ? rows.map((row, index) => <div key={`${row.rule_id}-${index}`} className="grid gap-2 py-2.5 text-[11px] sm:grid-cols-[1fr_1fr_auto]">
    <div title={safe(row.rule_id, '规则编号')}><div className="text-text">{safe(row.rule_name, '规则证据')}</div><div className="mt-0.5 text-[9px] text-text-secondary">{row.source ? `来源：${readableValue(row.source)}` : '来源：核心周期数据'}</div></div>
    <div className="min-w-0 break-words text-text-secondary">要求：{readableValue(row.required)}<br />实际：{readableValue(row.actual)}</div>
    <span className={evidenceTone(row.passed)}>{row.passed === true ? '通过' : row.passed === false ? '未通过' : '待确认'}</span>
  </div>) : <div className="py-5 text-xs text-text-secondary">暂无规则证据</div>}</div>;
}

function CycleStages({ cycle, compact = false }: { cycle: AnyMap; compact?: boolean }) {
  const activeIndex = cycleStageIndex(cycle);
  const colors = [
    'border-sky-400/35 bg-sky-400/10 text-sky-200',
    'border-emerald-400/35 bg-emerald-400/10 text-emerald-200',
    'border-lime-400/35 bg-lime-400/10 text-lime-200',
    'border-amber-400/35 bg-amber-400/10 text-amber-200',
    'border-rose-400/35 bg-rose-400/10 text-rose-200',
  ];
  return <div>
    <div className={`grid gap-2 ${compact ? 'grid-cols-5' : 'grid-cols-1 sm:grid-cols-5'}`}>
      {CYCLE_STAGES.map((stage, index) => {
        const active = activeIndex === index;
        return <div key={stage} aria-current={active ? 'step' : undefined} className={`relative min-w-0 rounded border px-2 py-3 text-center transition ${colors[index]} ${active ? 'ring-2 ring-accent ring-offset-2 ring-offset-card shadow-[0_0_0_1px_rgba(76,141,255,0.35)]' : 'opacity-75'}`}>
          <span className="mx-auto grid h-6 w-6 place-items-center rounded-full border border-current font-mono text-[10px]">{index + 1}</span>
          <span className="mt-2 block break-words text-[11px] leading-4">{stage}</span>
          {active && <span className="mt-1 block text-[9px] font-medium text-accent">当前阶段</span>}
        </div>;
      })}
    </div>
    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-text-secondary">
      <span>{activeIndex >= 0 ? `当前：${CYCLE_STAGES[activeIndex]}` : '当前周期：未确认'}</span>
      {activeIndex < 0 && <span className="text-warn">周期数据质量不足，不代表已确认进入冰点</span>}
    </div>
  </div>;
}

function ToolboxEvidence({ rows }: { rows: ToolboxEvidence[] }) {
  return <div className="divide-y divide-border">
    {rows.length ? rows.map((item, index) => <div key={`${item.rule_id || item.rule_name || 'rule'}-${index}`} className="grid gap-2 py-2.5 text-[11px] sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
      <div className="min-w-0" title={safe(item.rule_id, '规则编号')}><div className="break-words text-text">{safe(item.rule_name, '规则证据')}</div><div className="mt-0.5 text-[9px] text-text-secondary">{item.source ? `来源：${readableValue(item.source)}` : '来源：策略扫描'}</div></div>
      <div className="min-w-0 break-words text-text-secondary"><div>要求：{readableValue(item.required)}</div><div className="mt-1">实际：{readableValue(item.actual)}</div></div>
      <span className={`whitespace-nowrap ${evidenceTone(item.passed)}`}>{item.passed === true ? '通过' : item.passed === false ? '未通过' : '待确认'}</span>
    </div>) : <div className="py-5 text-xs text-text-secondary">暂无规则证据</div>}
  </div>;
}

function toolboxStatusTone(status: unknown): string {
  if (status === '已确认') return 'border-up/35 bg-up/10 text-up';
  if (status === '风险排除') return 'border-down/35 bg-down/10 text-down';
  return 'border-warn/35 bg-warn/10 text-warn';
}

function sourceText(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => sourceText(item)).filter(Boolean).join(' + ');
  if (value && typeof value === 'object') {
    const object = value as AnyMap;
    return sourceText(object.source || object.provider || object.preferred_provider || object.name || object.label);
  }
  const text = String(value || '').trim();
  if (!text) return '后端未返回来源';
  if (text.includes('+')) return text.split('+').map(sourceText).join(' + ');
  const labels: Record<string, string> = {
    numcat: '猫爪 NumCat',
    numcat_screening: '猫爪·全市场筛选',
    numcat_stockbasic: '猫爪·证券目录',
    numcat_stk_factor_pro: '猫爪·日线因子',
    numcat_daily: '猫爪·日线行情',
    numcat_tick: '猫爪·实时快照',
    numcat_daily_auc: '猫爪·集合竞价',
    numcat_daily_auc_detail: '猫爪·集合竞价明细',
    numcat_level2: '猫爪·Level-2',
    numcat_finance_announcement: '猫爪·公司公告',
    numcat_finance_indicator: '猫爪·财务指标',
    financial_pit_snapshots: '已披露财务缓存',
    stock_daily_bars: '数据库·日线缓存',
    database_cache: '数据库缓存',
    cache: '缓存',
    unavailable: '暂不可用',
  };
  if (labels[text.toLowerCase()]) return labels[text.toLowerCase()];
  if (/numcat|猫爪/i.test(text)) return `猫爪 NumCat（${text}）`;
  if (/cache|缓存|database|数据库/i.test(text)) return text;
  return text;
}

const COVERAGE_LABELS: Record<string, string> = {
  universe: '市场总数',
  total: '市场总数',
  eligible: '可评估',
  evaluated: '已评估',
  scanned: '已扫描',
  excluded: '规则过滤',
  missing_history: '缺历史',
  stale_history: '过期',
  source: '实际来源',
  provider: '实际来源',
  errors: '错误',
  error: '错误',
  actual: '实际值', market_max: '市场最高连板', height: '连板数',
  stock_start_date: '个股启动日', leader_established_date: '同题材高板确立日',
  early_theme_start: '题材早期启动', first_limit_pioneer: '首板先锋',
  supplement_started_after_leader: '高板确立后启动',
  stock_height: '个股连板', leader_height: '同题材最高连板', ratio: '相对比例',
  first_divergence_support: '首次分歧承接', theme_linkage: '同题材跟随观察',
  independent_theme_leadership: '独立领涨证据', value: '数值',
  pioneer: '先锋', midcap: '中军', leader_premium: '前日龙头溢价',
  support_promotion: '助攻晋级', core_midcap_stable: '中军稳定',
  fund_return: '资金回流', reversal: '反包', supplement_started: '补涨启动',
  resists_market_drop: '相对市场抗跌', position_record: '已有持仓',
  sellable_shares: '可卖股数', as_of: '核对日期', current: '日期有效',
};

function coverageLabel(key: string): string {
  return COVERAGE_LABELS[key] || key.replace(/_/g, ' ');
}

function coverageValue(key: string, value: unknown): string {
  if (key === 'source' || key === 'provider' || /source|provider/i.test(key)) return sourceText(value);
  if (key === 'error' || key === 'errors' || /error/i.test(key)) {
    if (value === null || value === undefined || value === '' || value === 'none' || value === 'None') return '无';
    if (value && typeof value === 'object') {
      const entries = Object.entries(value as AnyMap);
      return entries.length ? entries.map(([childKey, childValue]) => `${coverageLabel(childKey)}：${coverageValue(childKey, childValue)}`).join('；') : '无';
    }
  }
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as AnyMap);
    return entries.length ? entries.map(([childKey, childValue]) => `${coverageLabel(childKey)}：${coverageValue(childKey, childValue)}`).join('；') : readableValue(value);
  }
  return readableValue(value, '未提供');
}

function CoverageNote({ note }: { note: unknown }) {
  if (!note) return null;
  if (typeof note !== 'object') {
    const text = String(note);
    const parts = [
      text.includes('无历史不计为无匹配') ? '缺失或过期历史不会被解释为无匹配' : '',
      text.includes('当前没有PersonalPoolItem可卖底仓') ? '当前无可卖底仓，T 不生成新买入建议' : '',
      text.includes('扫描进行中') ? '全市场扫描进行中' : '',
      text.includes('扫描失败') ? '扫描失败，未将失败数据解释为无匹配' : '',
    ].filter(Boolean);
    return parts.length ? <>{parts.map((part, index) => <span key={part}>{part}{index < parts.length - 1 ? ' · ' : ''}</span>)}</> : null;
  }
  const entries = Object.entries(note as AnyMap);
  return <>{entries.map(([key, value], index) => <span key={key}><b className="font-normal text-text">{coverageLabel(key)}</b>：{coverageValue(key, value)}{index < entries.length - 1 ? ' · ' : ''}</span>)}</>;
}

function ToolboxSourceAudit({ scan }: { scan: ToolboxScan | null }) {
  const quality = scan?.data_quality || {};
  const sourceStatus = scan?.source_status || {};
  const dataSources = (scan?.data_sources || quality.data_sources || sourceStatus.data_sources) as AnyMap;
  const universe = dataSources?.universe || {};
  const history = dataSources?.history || {};
  const providers = history.providers || {};
  const providerDetails = Object.entries(providers).map(([provider, count]) => `${sourceText(provider)} ${readableValue(count, '0')}只`);
  const sourceDetails = dataSources && Object.keys(dataSources).length
    ? [`市场：${sourceText(universe.provider || universe.source || sourceStatus.preferred_provider || sourceStatus.source)}${finite(universe.count) ? ` ${universe.count}只` : ''}`, providerDetails.length ? `历史：${providerDetails.join(' + ')}` : '历史：后端未返回来源']
    : [sourceText(scan?.source || quality.source || sourceStatus.preferred_provider || sourceStatus.source || sourceStatus)];
  const sourceTime = history.latest_source_updated_at || scan?.source_time || scan?.source_updated_at || scan?.trade_date || quality.source_time || sourceStatus.data_date;
  const coverage = scan?.coverage_pct ?? quality.coverage_pct ?? scan?.coverage?.pct;
  const fallback = scan?.fallback_used || scan?.fallback_source || quality.fallback_used;
  const errors = Object.entries(history.numcat_errors || {}).filter(([, count]) => Number(count) > 0).map(([error, count]) => `${error} ${count}`);
  return <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border pt-3 text-[10px] leading-4 text-text-secondary" title="来源、时间和覆盖率均来自后端实际返回"><span className="inline-flex items-center gap-1"><Database size={12} />数据源：<b className="font-normal text-text">{sourceDetails.join('；')}</b></span><span>数据时间：<b className="font-mono font-normal text-text">{safe(sourceTime, '后端未返回时间')}</b></span>{finite(coverage) && <span>覆盖：<b className="font-mono font-normal text-text">{coverage.toFixed(1)}%</b></span>}{errors.length > 0 && <span className="text-warn">NumCat错误：{errors.join('、')}</span>}{fallback && <span className="text-warn">备用源：{sourceText(fallback)}</span>}</div>;
}

function ToolboxDetail({ row, detail, loading, onClose, onRefresh }: { row: ToolboxRow | null; detail: AnyMap | null; loading: boolean; onClose: () => void; onRefresh: () => void }) {
  if (!row && !loading) return null;
  const item = detail || row || {};
  const strategy = item.strategy || {};
  const bars = Array.isArray(item.bars) ? item.bars as KlineRow[] : [];
  const latestBar = item.bars?.[item.bars.length - 1] || {};
  return <div className="fixed inset-0 z-[80] flex justify-end bg-black/60" onClick={onClose}>
    <aside className="h-full w-full max-w-[840px] overflow-y-auto border-l border-border bg-bg shadow-2xl" onClick={(event) => event.stopPropagation()}>
      <header className="sticky top-0 z-10 flex min-h-14 items-center justify-between gap-3 border-b border-border bg-card/95 px-4 backdrop-blur">
        <div className="min-w-0"><h2 className="truncate text-sm font-semibold text-text">{safe(item.name)} · {safe(item.symbol)}</h2><p className="mt-0.5 truncate text-[10px] text-text-secondary">工具箱详情 · {safe(strategy.horizon, '时间窗口待确认')} · 数据日 {safe(item.trade_date)}</p></div>
        <div className="flex shrink-0 items-center gap-1"><button type="button" onClick={onRefresh} className="grid h-8 w-8 place-items-center rounded border border-border text-text-secondary hover:text-accent" title="刷新工具箱详情"><RefreshCw size={14} /></button><button type="button" onClick={onClose} className="grid h-8 w-8 place-items-center rounded border border-border text-text-secondary hover:text-text" title="关闭详情"><X size={15} /></button></div>
      </header>
      {loading ? <div className="grid h-64 place-items-center text-xs text-text-secondary"><Loader2 size={20} className="animate-spin text-accent" /></div> : <div className="space-y-4 p-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4"><div className="rounded border border-border bg-card p-3"><span className="text-[10px] text-text-secondary">状态</span><b className={`mt-1 block text-xs ${toolboxStatusTone(item.status).split(' ').at(-1)}`}>{safe(item.status)}</b></div><div className="rounded border border-border bg-card p-3"><span className="text-[10px] text-text-secondary">当前价</span><b className={`mt-1 block font-mono text-xs ${tone(item.change_pct)}`}>{finite(item.price) ? item.price.toFixed(2) : '--'} <span className="text-[10px]">{pct(item.change_pct)}</span></b></div><div className="rounded border border-border bg-card p-3"><span className="text-[10px] text-text-secondary">锚点 / 目标</span><b className="mt-1 block font-mono text-xs text-text">{finite(item.anchor_price) ? item.anchor_price.toFixed(2) : '--'} / {finite(item.target_price) ? item.target_price.toFixed(2) : '--'}</b></div><div className="rounded border border-border bg-card p-3"><span className="text-[10px] text-text-secondary">信号日</span><b className="mt-1 block text-xs text-text">{safe(item.signal_date)}</b></div></div>
        <Panel title="时间窗口与策略边界" icon={Clock3}><div className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2"><Fact label="策略" value={safe(strategy.name, safe(item.horizon))} /><Fact label="持有周期" value={safe(item.holding_period, safe(item.strategy?.holding_period))} /><Fact label="信号原因" value={safe(item.reason)} /><Fact label="风险说明" value={safe(strategy.risk_note, '以策略规则和实际持仓为准')} /></div></Panel>
        {bars.length > 0 && <Panel title="K 线与均线" icon={BarChart3} meta={`${bars.length} 个交易日`}><div className="mb-3 flex flex-wrap gap-3 text-[10px] text-text-secondary"><span>MA5 <b className="font-mono text-text">{finite(latestBar.ma5) ? latestBar.ma5.toFixed(2) : '--'}</b></span><span>MA20 <b className="font-mono text-text">{finite(latestBar.ma20) ? latestBar.ma20.toFixed(2) : '--'}</b></span>{strategy.id === 'WM_CLASSIC_75A' && <span>MA75 <b className="font-mono text-text">{finite(latestBar.ma75) ? latestBar.ma75.toFixed(2) : '--'}</b></span>}</div><KlineChart rows={bars} height={350} showMovingAverages movingAveragePeriods={strategy.id === 'WM_CLASSIC_75A' ? [5, 20, 75] : [5, 20]} /></Panel>}
        <Panel title="规则证据" icon={ListChecks}><ToolboxEvidence rows={item.evidence || []} /></Panel>
        {strategy.id === 'WM_CLASSIC_T' && <DisclosureEvidence facts={item} />}
        <div className="grid gap-4 md:grid-cols-2"><Panel title="防守" icon={ShieldAlert}><div className="space-y-2 text-xs leading-5 text-text-secondary">{(item.risk_notes || [strategy.risk_note]).filter(Boolean).map((note: string, index: number) => <div key={`${note}-${index}`} className="border-l-2 border-warn/70 pl-3">{note}</div>)}</div></Panel><Panel title="策略规则" icon={Target}><div className="space-y-3 text-xs leading-5"><div><span className="text-text-secondary">入场条件</span>{(strategy.entry_rules || []).map((rule: string, index: number) => <div key={`${rule}-${index}`} className="mt-1 text-text">{rule}</div>)}</div><div><span className="text-text-secondary">退出条件</span>{(strategy.exit_rules || []).map((rule: string, index: number) => <div key={`${rule}-${index}`} className="mt-1 text-text">{rule}</div>)}</div></div></Panel></div>
      </div>}
    </aside>
  </div>;
}

function ReviewMetrics({ metrics }: { metrics: AnyMap }) {
  const cards = [
    ['样本数', metrics.sample_count ?? metrics.total],
    ['期望值', finite(metrics.expectancy) ? pct(metrics.expectancy) : metrics.expectancy],
    ['最大连亏', metrics.max_consecutive_losses],
    ['模式外亏损占比', finite(metrics.outside_loss_share) ? `${metrics.outside_loss_share}%` : metrics.outside_loss_share],
    ['模式外占比', finite(metrics.mode_outside_ratio) ? `${metrics.mode_outside_ratio}%` : metrics.mode_outside_ratio],
    ['模式内胜率', finite(metrics.inside_win_rate) ? `${metrics.inside_win_rate}%` : metrics.inside_win_rate],
    ['盈亏比', metrics.profit_loss_ratio],
  ].filter(([, value]) => value !== undefined && value !== null && value !== '');
  const perSetup = Array.isArray(metrics.per_setup) ? metrics.per_setup : metrics.per_setup && typeof metrics.per_setup === 'object' ? Object.entries(metrics.per_setup).map(([name, value]) => ({ name, ...(value as AnyMap) })) : [];
  return <div>
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{cards.map(([label, value]) => <div key={String(label)} className="rounded border border-border bg-bg p-3"><span className="text-[10px] text-text-secondary">{label}</span><b className="mt-1 block break-words font-mono text-sm text-text">{readableValue(value, '--')}</b></div>)}</div>
    {perSetup.length > 0 && <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[540px] text-[10px]"><thead className="text-text-secondary"><tr><th className="px-2 py-2 text-left font-medium">策略</th><th className="px-2 py-2 text-right font-medium">样本</th><th className="px-2 py-2 text-right font-medium">胜率</th><th className="px-2 py-2 text-right font-medium">期望值</th><th className="px-2 py-2 text-right font-medium">盈亏比</th></tr></thead><tbody>{perSetup.map((item: AnyMap, index: number) => <tr key={`${item.name || item.setup_type || 'setup'}-${index}`} className="border-t border-border"><td className="px-2 py-2 text-text">{safe(item.name || item.setup_name || item.setup_type)}</td><td className="px-2 py-2 text-right font-mono text-text-secondary">{readableValue(item.sample_count ?? item.count, '--')}</td><td className="px-2 py-2 text-right font-mono text-text-secondary">{finite(item.win_rate) ? `${item.win_rate}%` : readableValue(item.win_rate, '--')}</td><td className="px-2 py-2 text-right font-mono text-text-secondary">{finite(item.expectancy) ? pct(item.expectancy) : readableValue(item.expectancy, '--')}</td><td className="px-2 py-2 text-right font-mono text-text-secondary">{readableValue(item.profit_loss_ratio, '--')}</td></tr>)}</tbody></table></div>}
  </div>;
}

function LeaderSupplementTable() {
  const rows = [
    ['启动时间', '题材最早首板中走出', '真龙打出高度后，低位启动'],
    ['板块带动性', '有：其他股票跟涨或跟跌', '无或弱：依赖真龙带动'],
    ['高度预期', '情绪上限，通常是最高板', '通常不超过真龙50%～70%连板高度'],
    ['分歧承接', '首次分歧有承接', '承接弱，容易快速下跌'],
    ['退潮表现', '横盘震荡或 A 字，视周期而定', '快速下跌，反抽弱，风险更高'],
  ];
  return <details className="rounded-md border border-border bg-card"><summary className="cursor-pointer list-none px-4 py-3 text-xs font-semibold text-text"><span className="mr-2 text-accent">角色辨析</span>真龙与补涨的五个观察维度</summary><div className="border-t border-border px-3 pb-3"><div className="mt-3 overflow-x-auto"><table className="w-full min-w-[680px] text-[11px]"><thead className="text-text-secondary"><tr><th className="w-[120px] px-2 py-2 text-left font-medium">维度</th><th className="w-1/2 px-2 py-2 text-left font-medium text-accent">真龙</th><th className="w-1/2 px-2 py-2 text-left font-medium text-warn">补涨</th></tr></thead><tbody>{rows.map(([dimension, leader, supplement]) => <tr key={dimension} className="border-t border-border align-top"><th className="px-2 py-2.5 text-left font-medium text-text">{dimension}</th><td className="px-2 py-2.5 leading-5 text-text-secondary">{leader}</td><td className="px-2 py-2.5 leading-5 text-text-secondary">{supplement}</td></tr>)}</tbody></table></div><p className="mt-2 text-[10px] leading-4 text-text-secondary">以上是识别框架，不是对未来高度或收益的承诺；以当前周期和逐股事实为准。</p></div></details>;
}

function RoleEvidence({ role, facts }: { role: AnyMap; facts: AnyMap }) {
  const evidence = Array.isArray(role?.evidence) ? role.evidence.filter((item: unknown): item is AnyMap => Boolean(item && typeof item === 'object')) : [];
  if (!evidence.length && !role?.confidence_source) return null;
  const basis = role.fact_basis || facts.fact_basis || {};
  const dates = Array.isArray(basis.observed_dates) ? basis.observed_dates : [];
  const proxy = facts.leadership_proxy_evidence || {};
  return <Panel title="角色事实" icon={Target} meta="规则核查">
    {dates.length > 0 && <p className="mb-3 text-[11px] leading-5 text-text-secondary">历史核查：{dates[0]} 至 {dates[dates.length - 1]}，共 {dates.length} 个交易日。{facts.industry_theme_proxy ? '当前使用行业归类。' : '按对应交易日的猫爪题材成分归类。'}</p>}
    {facts.leadership_proxy === true && <div className="mb-3 border-l-2 border-accent pl-3 text-[11px] leading-5 text-text-secondary">多日领涨观察成立：{proxy.lead_day_count} 日处于同题材连板领先位置，随后出现 {proxy.follower_count} 只跟随股。这是历史关联证据，不能单独认证真龙或证明因果带动。</div>}
    <RuleRows rows={evidence} />
  </Panel>;
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
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4"><div className="rounded border border-border bg-card p-3"><span className="text-[10px] text-text-secondary">周期</span><b className="mt-1 block text-xs text-text">{row.cycle?.cycle}</b></div><div className="rounded border border-border bg-card p-3"><span className="text-[10px] text-text-secondary">角色</span><b className="mt-1 block text-xs text-text">{row.role?.role}</b></div><div className="rounded border border-border bg-card p-3"><span className="text-[10px] text-text-secondary">节点</span><b className="mt-1 block text-xs text-text">{row.setup?.name}</b></div><div className="rounded border border-border bg-card p-3"><span className="text-[10px] text-text-secondary">结论</span><b className="mt-1 block text-xs text-text">{row.candidate_status}</b></div></div>
        <RoleEvidence role={row.role || {}} facts={row.stock_facts || {}} />
        <DisclosureEvidence facts={row.stock_facts || {}} />
        {row.account_context?.source && <div className="border-l-2 border-accent pl-3 text-xs leading-5 text-text-secondary">账户口径：{row.account_context.source}。日期 {safe(row.account_context.as_of)}；连续已结算亏损 {row.account_context.consecutive_stops} 笔。{row.account_context.current ? '仓位参考已结合账户数据。' : '账户日期与研究日不一致，未套用回撤。'}扫描持续开放。</div>}
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
  const [reviewForm, setReviewForm] = useState({ symbol: '', setup_type: 'FIRST_DIVERGENCE', pnl_pct: '', mode_inside: true, completed: false, exit_date: '', review_text: '' });
  const [toolboxStrategyId, setToolboxStrategyId] = useState<string>(TOOLBOX_STRATEGIES[0].id);
  const [toolboxScan, setToolboxScan] = useState<ToolboxScan | null>(null);
  const [toolboxLoading, setToolboxLoading] = useState(false);
  const [toolboxError, setToolboxError] = useState('');
  const [toolboxSearch, setToolboxSearch] = useState('');
  const [toolboxStatusFilter, setToolboxStatusFilter] = useState<'all' | '已确认' | '等待确认' | '风险排除'>('all');
  const [toolboxPage, setToolboxPage] = useState(1);
  const [toolboxSelected, setToolboxSelected] = useState<ToolboxRow | null>(null);
  const [toolboxDetail, setToolboxDetail] = useState<AnyMap | null>(null);
  const [toolboxDetailLoading, setToolboxDetailLoading] = useState(false);
  const toolboxRunRef = useRef(0);
  const toolboxAbortRef = useRef<AbortController | null>(null);
  const toolboxDetailAbortRef = useRef<AbortController | null>(null);
  const dashboardAbortRef = useRef<AbortController | null>(null);
  const candidateAbortRef = useRef<AbortController | null>(null);

  const tradeDate = useMemo(() => {
    if (payload?.trade_date) return String(payload.trade_date).slice(0, 10);
    return shanghaiToday();
  }, [payload?.trade_date]);

  const loadToolboxScan = useCallback(async (refresh = false) => {
    const strategyId = toolboxStrategyId;
    toolboxAbortRef.current?.abort();
    const controller = new AbortController();
    toolboxAbortRef.current = controller;
    const runId = toolboxRunRef.current + 1;
    toolboxRunRef.current = runId;
    setToolboxLoading(true);
    setToolboxError('');
    setToolboxPage(1);
    setToolboxScan(null);

    try {
      let nextRefresh = refresh;
      while (true) {
        const params = new URLSearchParams({ date: tradeDate, refresh: String(nextRefresh), exclude_star_market: String(excludeStar), exclude_gem: String(excludeGem) });
        const response = await apiFetch<{ code: number; data: ToolboxScan }>(`/wildman/toolbox/${encodeURIComponent(strategyId)}/scan?${params}`, { timeoutMs: 60000, signal: controller.signal, cache: 'no-store' });
        if (runId !== toolboxRunRef.current || controller.signal.aborted) return;
        setToolboxScan(response.data);
        if (response.data.status !== 'running') {
          if (response.data.status === 'failed') setToolboxError(safe(response.data.error, '策略扫描失败，请重试'));
          return;
        }
        nextRefresh = false;
        await new Promise<void>((resolve, reject) => {
          let timer = 0;
          const onAbort = () => { window.clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
          timer = window.setTimeout(() => { controller.signal.removeEventListener('abort', onAbort); resolve(); }, 2500);
          controller.signal.addEventListener('abort', onAbort, { once: true });
        });
      }
    } catch (caught) {
      if (runId !== toolboxRunRef.current || controller.signal.aborted || (caught instanceof Error && caught.name === 'AbortError')) return;
      setToolboxError(friendlyApiError(caught, '工具箱扫描暂时不可用'));
    } finally {
      if (runId === toolboxRunRef.current) setToolboxLoading(false);
    }
  }, [excludeGem, excludeStar, toolboxStrategyId, tradeDate]);

  useEffect(() => {
    if (view === 'classic') void loadToolboxScan(false);
    return () => { toolboxAbortRef.current?.abort(); };
  }, [loadToolboxScan, view]);

  const loadToolboxDetail = useCallback(async (row: ToolboxRow, refresh = false) => {
    toolboxDetailAbortRef.current?.abort();
    const controller = new AbortController();
    toolboxDetailAbortRef.current = controller;
    setToolboxSelected(row);
    setToolboxDetail(null);
    setToolboxDetailLoading(true);
    try {
      const params = new URLSearchParams({ date: tradeDate });
      if (refresh) params.set('refresh', 'true');
      const response = await apiFetch<{ data: AnyMap }>(`/wildman/toolbox/${encodeURIComponent(toolboxStrategyId)}/stocks/${encodeURIComponent(row.symbol)}?${params}`, { timeoutMs: 60000, signal: controller.signal, cache: 'no-store' });
      if (!controller.signal.aborted) setToolboxDetail(response.data);
    } catch (caught) {
      if (!controller.signal.aborted) setToolboxError(friendlyApiError(caught, '工具箱股票详情暂时不可用'));
    } finally {
      if (!controller.signal.aborted) setToolboxDetailLoading(false);
    }
  }, [toolboxStrategyId, tradeDate]);

  const load = useCallback(async (refresh = false) => {
    dashboardAbortRef.current?.abort();
    const controller = new AbortController();
    dashboardAbortRef.current = controller;
    setLoading(true); if (refresh) setRefreshing(true); setError('');
    try {
      const params = new URLSearchParams({ refresh: String(refresh), exclude_star_market: String(excludeStar), exclude_gem: String(excludeGem) });
      const response = await apiFetch<{ data: AnyMap }>(`/wildman/dashboard?${params}`, { timeoutMs: 120000, signal: controller.signal });
      if (!controller.signal.aborted) setPayload(response.data);
    } catch (caught) { if (!controller.signal.aborted) setError(friendlyApiError(caught, '野人哥交易决策模块暂时不可用')); }
    finally { if (!controller.signal.aborted) { setLoading(false); setRefreshing(false); } }
  }, [excludeGem, excludeStar]);

  useEffect(() => { void load(false); return () => dashboardAbortRef.current?.abort(); }, [load]);
  useEffect(() => () => { candidateAbortRef.current?.abort(); toolboxDetailAbortRef.current?.abort(); }, []);

  const loadDetail = useCallback(async (symbol: string, refresh = false) => {
    candidateAbortRef.current?.abort();
    const controller = new AbortController();
    candidateAbortRef.current = controller;
    setDetailLoading(true); setSelected((current) => current?.symbol === symbol ? current : null);
    try {
      const response = await apiFetch<{ data: AnyMap }>(`/wildman/candidates/${encodeURIComponent(symbol)}?refresh=${refresh}`, { timeoutMs: 120000, signal: controller.signal });
      if (!controller.signal.aborted) setSelected(response.data);
    } catch (caught) { if (!controller.signal.aborted) { setError(friendlyApiError(caught, '候选详情暂时不可用')); setSelected(null); } }
    finally { if (!controller.signal.aborted) setDetailLoading(false); }
  }, []);

  useEffect(() => {
    if (!selected?.level2?.pending || detailLoading) return;
    const controller = new AbortController();
    const symbol = selected.symbol;
    const date = selected.trade_date;
    const timer = window.setTimeout(async () => {
      try {
        const params = new URLSearchParams({ date, refresh: 'false' });
        const response = await apiFetch<{ data: AnyMap }>(`/wildman/candidates/${encodeURIComponent(symbol)}?${params}`, { timeoutMs: 45000, signal: controller.signal, cache: 'no-store' });
        if (!controller.signal.aborted) setSelected((current) => current?.symbol === symbol && current?.trade_date === date ? response.data : current);
      } catch (caught) {
        if (!controller.signal.aborted) setError(friendlyApiError(caught, '盘口同步查询暂时失败，可重新打开详情查看'));
      }
    }, 10000);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [selected, detailLoading]);

  const loadReview = useCallback(async () => {
    try { const response = await apiFetch<{ data: AnyMap }>(`/wildman/review/${reviewPeriod}`, { timeoutMs: 45000 }); setReview(response.data); }
    catch (caught) { setError(friendlyApiError(caught, '复盘记录暂时不可用')); }
  }, [reviewPeriod]);

  useEffect(() => { if (view === 'review') void loadReview(); }, [loadReview, view]);

  const saveReview = async () => {
    setSaving(true);
    try {
      await apiFetch('/wildman/manual-review', { method: 'POST', body: JSON.stringify({ ...reviewForm, entry_date: tradeDate, exit_date: reviewForm.completed ? (reviewForm.exit_date || tradeDate) : null, pnl_pct: reviewForm.completed && reviewForm.pnl_pct !== '' ? Number(reviewForm.pnl_pct) : null }) });
      setReviewForm({ symbol: '', setup_type: 'FIRST_DIVERGENCE', pnl_pct: '', mode_inside: true, completed: false, exit_date: '', review_text: '' });
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
  const selectedToolboxStrategy = TOOLBOX_STRATEGIES.find((item) => item.id === toolboxStrategyId) || TOOLBOX_STRATEGIES[0];
  const toolboxRows = useMemo(() => {
    const query = toolboxSearch.trim().toLowerCase();
    return (toolboxScan?.rows || []).filter((row: ToolboxRow) => {
      const matchesStatus = toolboxStatusFilter === 'all' || row.status === toolboxStatusFilter;
      const searchable = [row.symbol, row.name, row.theme_name, row.reason].map((value) => String(value || '').toLowerCase()).join(' ');
      return matchesStatus && (!query || searchable.includes(query));
    });
  }, [toolboxScan, toolboxSearch, toolboxStatusFilter]);
  const toolboxPageSize = 12;
  const toolboxPageCount = Math.max(1, Math.ceil(toolboxRows.length / toolboxPageSize));
  const toolboxPageRows = toolboxRows.slice((toolboxPage - 1) * toolboxPageSize, toolboxPage * toolboxPageSize);

  const renderCandidates = (rows: AnyMap[]) => <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">{rows.length ? rows.map((row) => <CandidateCard key={`${row.symbol}-${row.setup?.type}`} row={row} onOpen={(symbol) => void loadDetail(symbol)} />) : <div className="py-8 text-xs text-text-secondary">当前没有满足该组规则的候选</div>}</div>;

  const renderToolbox = () => {
    const progress = toolboxScan?.progress || {};
    const total = finite(progress.total) ? progress.total : 0;
    const scanned = finite(progress.scanned) ? progress.scanned : 0;
    const eligible = finite(progress.eligible) ? progress.eligible : total;
    const evaluated = finite(progress.evaluated) ? progress.evaluated : Math.max(0, scanned - (progress.missing_history || 0) - (progress.stale_history || 0));
    const excluded = finite(progress.excluded) ? progress.excluded : 0;
    const missingHistory = finite(progress.missing_history) ? progress.missing_history : 0;
    const staleHistory = finite(progress.stale_history) ? progress.stale_history : 0;
    const progressDenominator = eligible > 0 ? eligible : total;
    const progressNumerator = toolboxScan?.status === 'completed' ? progressDenominator : Math.min(scanned, progressDenominator);
    const scanPercent = progressDenominator > 0 ? Math.min(100, Math.round((progressNumerator / progressDenominator) * 100)) : 0;
    const confirmedCount = (toolboxScan?.rows || []).filter((row: ToolboxRow) => row.status === '已确认').length;
    const waitingCount = (toolboxScan?.rows || []).filter((row: ToolboxRow) => row.status === '等待确认').length;
    const excludedCount = (toolboxScan?.rows || []).filter((row: ToolboxRow) => row.status === '风险排除').length;
    return <div className="space-y-4">
      {toolboxStrategyId === 'WM_CLASSIC_T' && <details className="border-b border-border pb-3"><summary className="cursor-pointer text-xs text-accent">个人底仓与可卖数量核对</summary><WildmanAccountPanel className="mt-3" /></details>}
      <section className="border-b border-border pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="text-base font-semibold text-text">经典战法工具箱</h2><span className="rounded border border-accent/30 bg-accent/10 px-2 py-1 text-[10px] text-accent">全市场扫描</span></div><p className="mt-1 max-w-3xl text-xs leading-5 text-text-secondary">选择策略，读取当前交易日的全市场候选。结果只代表规则状态，T+1 工具不代表新建仓信号。</p></div><button type="button" onClick={() => void loadToolboxScan(true)} disabled={toolboxLoading} className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded border border-border px-2.5 text-[11px] text-text-secondary hover:border-accent hover:text-accent disabled:opacity-50" title="仅本次扫描强制刷新缓存"><RefreshCw size={13} className={toolboxLoading ? 'animate-spin' : ''} />刷新扫描</button></div>
        <div className="mt-4 grid gap-3 lg:grid-cols-3">{TOOLBOX_STRATEGIES.map((strategy) => { const active = strategy.id === toolboxStrategyId; const color = strategy.color === 'cyan' ? 'border-cyan-400/50 bg-cyan-400/10' : strategy.color === 'amber' ? 'border-amber-400/50 bg-amber-400/10' : 'border-violet-400/50 bg-violet-400/10'; return <button key={strategy.id} type="button" aria-pressed={active} onClick={() => { setToolboxStrategyId(strategy.id); setToolboxStatusFilter('all'); setToolboxSearch(''); setToolboxPage(1); }} className={`min-w-0 rounded-md border p-4 text-left transition ${active ? `${color} ring-1 ring-accent` : 'border-border bg-card hover:border-accent/50'}`}><div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="truncate text-sm font-semibold text-text">{strategy.name}</div><div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-text-secondary"><span className="rounded border border-current/30 px-1.5 py-0.5">{strategy.horizon}</span><span>{strategy.holding_period}</span></div></div><ChevronRight size={15} className={active ? 'shrink-0 text-accent' : 'shrink-0 text-text-secondary'} /></div><p className="mt-3 min-h-10 text-xs leading-5 text-text-secondary">{strategy.summary}</p>{strategy.id === 'WM_CLASSIC_T' && <p className="mt-2 text-[10px] leading-4 text-amber-200">前提：已有持仓 · T+1 · 不产生新买入信号</p>}{strategy.id === 'WM_CLASSIC_520' && <p className="mt-2 text-[10px] leading-4 text-cyan-200">参考目标：2-5% · 持有不超过 5 个交易日</p>}{strategy.id === 'WM_CLASSIC_75A' && <p className="mt-2 text-[10px] leading-4 text-violet-200">参考节奏：波段持有数周至数月</p>}</button>; })}</div>
      </section>
      <Panel title={`${selectedToolboxStrategy.name} · 扫描结果`} icon={ListChecks} meta={<span className={toolboxScan?.status === 'completed' ? 'text-up' : toolboxScan?.status === 'failed' ? 'text-down' : 'text-warn'}>{toolboxScan?.status === 'completed' ? '扫描完成' : toolboxScan?.status === 'failed' ? '扫描失败' : toolboxLoading ? '扫描中' : '等待扫描'}</span>}>
        {toolboxError && <div className="mb-4 flex items-start gap-2 rounded border border-down/30 bg-down/5 p-3 text-xs text-down"><AlertTriangle size={14} className="mt-0.5 shrink-0" /><span className="min-w-0 break-words">{toolboxError}</span><button type="button" onClick={() => void loadToolboxScan(true)} className="ml-auto shrink-0 text-down underline">重试</button></div>}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4"><div className="rounded border border-border bg-bg p-3"><div className="text-[10px] text-text-secondary">市场总数</div><b className="mt-1 block font-mono text-sm text-text">{total.toLocaleString()} 只</b></div><div className="rounded border border-border bg-bg p-3"><div className="text-[10px] text-text-secondary">规则过滤</div><b className="mt-1 block font-mono text-sm text-text">{excluded.toLocaleString()}</b></div><div className="rounded border border-border bg-bg p-3"><div className="text-[10px] text-text-secondary">已评估</div><b className="mt-1 block font-mono text-sm text-text">{evaluated.toLocaleString()}</b></div><div className="rounded border border-border bg-bg p-3"><div className="text-[10px] text-text-secondary">缺历史 / 过期</div><b className="mt-1 block font-mono text-sm text-warn">{missingHistory.toLocaleString()} / {staleHistory.toLocaleString()}</b></div></div>
        <div className="mt-2 grid gap-2 sm:grid-cols-3"><div className="rounded border border-border bg-bg px-3 py-2 text-[10px] text-text-secondary">已确认 <b className="ml-1 font-mono font-normal text-up">{confirmedCount}</b></div><div className="rounded border border-border bg-bg px-3 py-2 text-[10px] text-text-secondary">等待确认 <b className="ml-1 font-mono font-normal text-warn">{waitingCount}</b></div><div className="rounded border border-border bg-bg px-3 py-2 text-[10px] text-text-secondary">风险排除 <b className="ml-1 font-mono font-normal text-down">{excludedCount}</b></div></div>
        {(toolboxLoading || toolboxScan?.status === 'running') && <div className="mt-4"><div className="flex items-center justify-between gap-3 text-[10px] text-text-secondary"><span className="flex items-center gap-1.5"><Loader2 size={12} className="animate-spin text-accent" />正在扫描全市场</span><span className="font-mono">{progressNumerator.toLocaleString()} / {progressDenominator.toLocaleString()} · {scanPercent}%</span></div><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-border"><div className="h-full rounded-full bg-accent transition-all" style={{ width: `${scanPercent}%` }} /></div></div>}
        <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-[10px] leading-4 text-text-secondary"><span className="inline-flex flex-wrap"><CoverageNote note={toolboxScan?.coverage_note} /></span><span>历史不足/未返回 {missingHistory}</span><span>过期 {staleHistory}</span>{toolboxScan?.cache_hit && <span className="text-accent">命中缓存</span>}</div>
        {toolboxScan && toolboxScan.coverage_gaps?.length > 0 && <details className="mt-3 border-t border-border pt-3 text-xs"><summary className="cursor-pointer text-text-secondary">查看未参与计算的股票与原因（{toolboxScan.coverage_gaps.length}只）</summary><div className="mt-2 max-h-60 overflow-y-auto divide-y divide-border">{toolboxScan.coverage_gaps.map((gap: AnyMap) => <div key={gap.symbol} className="flex flex-wrap justify-between gap-2 py-2"><span>{gap.name} <span className="font-mono text-text-secondary">{gap.symbol}</span></span><span className="text-text-secondary">{gap.reason}{finite(gap.history_rows) ? ` · ${gap.history_rows}/${gap.required_rows}根日线` : ''}</span></div>)}</div></details>}
        <ToolboxSourceAudit scan={toolboxScan} />
        <div className="mt-4 flex flex-col gap-3 border-y border-border py-3 md:flex-row md:items-center"><label className="flex min-w-0 flex-1 items-center gap-2 rounded border border-border bg-bg px-3"><Search size={14} className="shrink-0 text-text-secondary" /><input value={toolboxSearch} onChange={(event) => { setToolboxSearch(event.target.value); setToolboxPage(1); }} placeholder="搜索股票代码、名称、题材" className="h-9 min-w-0 flex-1 bg-transparent text-xs text-text outline-none placeholder:text-text-secondary" /></label><div className="flex flex-wrap items-center gap-2 text-[10px]"><label className="flex items-center gap-1.5 text-text-secondary"><input type="checkbox" checked={excludeStar} onChange={(event) => { setExcludeStar(event.target.checked); setToolboxPage(1); }} />排除科创</label><label className="flex items-center gap-1.5 text-text-secondary"><input type="checkbox" checked={excludeGem} onChange={(event) => { setExcludeGem(event.target.checked); setToolboxPage(1); }} />排除创业</label></div></div>
        <div className="flex flex-wrap items-center gap-2"><span className="mr-1 text-[10px] text-text-secondary">结果</span>{([['all', '全部'], ['已确认', `已确认 ${confirmedCount}`], ['等待确认', `等待确认 ${waitingCount}`], ['风险排除', `风险排除 ${excludedCount}`]] as const).map(([value, label]) => <button key={value} type="button" onClick={() => { setToolboxStatusFilter(value); setToolboxPage(1); }} className={`rounded border px-2.5 py-1.5 text-[10px] ${toolboxStatusFilter === value ? 'border-accent/60 bg-accent/10 text-accent' : 'border-border text-text-secondary hover:text-text'}`}>{label}</button>)}</div>
        {toolboxPageRows.length ? <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[760px] text-xs"><thead className="text-[10px] text-text-secondary"><tr><th className="px-2 py-2 text-left font-medium">股票</th><th className="px-2 py-2 text-left font-medium">题材</th><th className="px-2 py-2 text-right font-medium">价格</th><th className="px-2 py-2 text-right font-medium">涨跌</th><th className="px-2 py-2 text-left font-medium">状态</th><th className="px-2 py-2 text-left font-medium">时间窗口</th><th className="px-2 py-2 text-left font-medium">原因</th><th className="px-2 py-2 text-right font-medium">详情</th></tr></thead><tbody>{toolboxPageRows.map((row) => <tr key={`${row.symbol}-${row.signal_date || ''}`} className="border-t border-border hover:bg-white/[0.025]"><td className="px-2 py-3"><button type="button" onClick={() => void loadToolboxDetail(row)} className="text-left hover:text-accent"><div className="font-medium text-text">{safe(row.name)}</div><div className="mt-0.5 font-mono text-[10px] text-text-secondary">{safe(row.symbol)}</div></button></td><td className="max-w-[150px] px-2 py-3 text-text-secondary"><span className="block truncate">{safe(row.theme_name, '题材待归类')}</span></td><td className={`px-2 py-3 text-right font-mono ${tone(row.change_pct)}`}>{finite(row.price) ? row.price.toFixed(2) : '--'}</td><td className={`px-2 py-3 text-right font-mono ${tone(row.change_pct)}`}>{pct(row.change_pct)}</td><td className="px-2 py-3"><span className={`inline-flex rounded border px-2 py-1 text-[10px] ${toolboxStatusTone(row.status)}`}>{safe(row.status)}</span></td><td className="px-2 py-3 text-text-secondary">{safe(row.horizon, selectedToolboxStrategy.horizon)}<div className="mt-1 text-[10px]">{safe(row.holding_period, selectedToolboxStrategy.holding_period)}</div></td><td className="max-w-[220px] px-2 py-3 text-text-secondary"><span className="block truncate" title={safe(row.reason)}>{safe(row.reason)}</span></td><td className="px-2 py-3 text-right"><button type="button" onClick={() => void loadToolboxDetail(row)} className="inline-flex h-7 w-7 items-center justify-center rounded border border-border text-text-secondary hover:border-accent hover:text-accent" title="查看规则证据与K线"><ChevronRight size={14} /></button></td></tr>)}</tbody></table></div> : <div className="grid min-h-40 place-items-center py-8 text-center text-xs text-text-secondary">{toolboxLoading ? '扫描进行中，结果会持续更新' : toolboxScan?.status === 'completed' ? '没有符合当前筛选的结果' : '选择策略后开始扫描'}</div>}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3 text-[10px] text-text-secondary"><span>显示 {toolboxRows.length ? (toolboxPage - 1) * toolboxPageSize + 1 : 0}-{Math.min(toolboxPage * toolboxPageSize, toolboxRows.length)} / {toolboxRows.length}</span><div className="flex items-center gap-2"><button type="button" disabled={toolboxPage <= 1} onClick={() => setToolboxPage((page) => page - 1)} className="rounded border border-border px-2.5 py-1.5 disabled:opacity-40">上一页</button><span className="font-mono">{toolboxPage} / {toolboxPageCount}</span><button type="button" disabled={toolboxPage >= toolboxPageCount} onClick={() => setToolboxPage((page) => page + 1)} className="rounded border border-border px-2.5 py-1.5 disabled:opacity-40">下一页</button></div></div>
      </Panel>
    </div>;
  };

  const renderView = () => {
    if (!payload) return null;
    if (view === 'dashboard') return <div className="space-y-4"><div className="grid gap-4 xl:grid-cols-[1.1fr_1fr_1fr]"><Panel title="今天能不能做" icon={Gauge} meta={payload.trade_date}><div className="flex items-center justify-between gap-3"><div><div className="text-[10px] text-text-secondary">当前周期</div><div className="mt-1 text-xl font-semibold text-text">{cycle.cycle}</div><div className="mt-1 text-xs text-text-secondary">{cycle.cycle_node}</div></div><div className="text-right"><div className="text-[10px] text-text-secondary">仓位参考</div><b className="mt-1 block font-mono text-lg text-accent">{cycle.position_range}</b></div></div><div className="mt-4 grid grid-cols-2 gap-3"><div className="border-l-2 border-up pl-3"><div className="text-[10px] text-text-secondary">允许</div>{(cycle.allowed_actions || []).map((item: string) => <div key={item} className="mt-1 text-xs text-text">{item}</div>)}</div><div className="border-l-2 border-down pl-3"><div className="text-[10px] text-text-secondary">禁止</div>{(cycle.forbidden_actions || []).map((item: string) => <div key={item} className="mt-1 text-xs text-text">{item}</div>)}</div></div></Panel><Panel title="主线候选" icon={Radar} meta={`${payload.mainlines?.length || 0}个候选`}>{(payload.mainlines || []).slice(0, 6).map((row: AnyMap) => { const confirmed = row.confirmed === true || (row.confirmed === undefined && row.state === '核心主线确认'); const count = mainlinePassedCount(row); return <div key={row.theme_id} className="flex min-w-0 items-center justify-between gap-3 border-b border-border py-2.5 last:border-0"><div className="min-w-0"><div className="break-words text-xs text-text">{row.theme_name}</div><div className="mt-1 text-[10px] text-text-secondary">五步 {count}/5 · 触发 {safe(row.trigger_date)}</div></div><span className={`shrink-0 rounded border px-2 py-1 text-[10px] ${confirmed ? 'border-up/30 bg-up/5 text-up' : 'border-warn/30 bg-warn/5 text-warn'}`}>{confirmed ? '核心主线确认' : '主线候选'}</span></div>; })}</Panel><Panel title="风险与数据" icon={ShieldAlert}><Fact label="风险否决" value={`${payload.reject_pool?.length || 0}只`} /><Fact label="模式内候选" value={`${focus.length}只`} /><Fact label="数据日期" value={payload.source_status?.data_date || payload.trade_date} /><Fact label="数据源" value={sourceText(payload.source_status?.preferred_provider || payload.source_status?.source)} detail={Array.isArray(payload.source_status?.daily_sources) ? `日级：${payload.source_status.daily_sources.map((item: unknown) => sourceText(item)).join('、')}` : '按后端实际返回来源展示'} /><Fact label="主线归类" value={safe(payload.source_status?.theme_basis, '行业归类代理')} detail="行业归类不能直接等同跨行业概念真主线，需结合题材证据核验" /><Fact label="Level-2" value="按需核验" detail="打开候选详情后按股读取实际provider、逐笔委托与十档盘口" /><Fact label="规则版本" value={payload.rule_version} /></Panel></div><Panel title="今日重点候选" icon={Target} meta="按角色与节点排序">{renderCandidates(focus.slice(0, 9))}</Panel></div>;
    if (view === 'cycle') return <div className="grid gap-4 xl:grid-cols-[1fr_1.2fr]"><Panel title="情绪周期状态机" icon={Activity}><CycleStages cycle={cycle} /><div className="mt-4 text-xs leading-6 text-text-secondary">冰点试错 → 启动确认加 → 主升做分歧 → 高潮减 → 退潮空仓</div></Panel><Panel title="周期证据" icon={ListChecks}><RuleRows rows={cycle.evidence || []} /></Panel></div>;
    if (view === 'mainline') return <WildmanMainlines rows={payload.mainlines || []} />;
    if (view === 'candidates') return <div className="space-y-5"><LeaderSupplementTable />{roleGroups.map((group) => <Panel key={group.role} title={group.role} icon={Target} meta={`${group.rows.length}只`}>{renderCandidates(group.rows)}</Panel>)}</div>;
    if (view === 'setups') return <div className="space-y-4">{['FIRST_DIVERGENCE', 'WEAK_TO_STRONG', 'SWING_PULLBACK', 'SWING_BREAKOUT', 'B_POINT', 'LIMIT_PULLBACK'].map((type) => { const rows = candidates.filter((row: AnyMap) => row.setup?.type === type); return rows.length ? <Panel key={type} title={safe(rows[0]?.setup?.name)} icon={Crosshair} meta={`${rows.length}只`}>{renderCandidates(rows)}</Panel> : null; })}<Panel title="等待模式形成" icon={Clock3}>{renderCandidates(candidates.filter((row: AnyMap) => row.setup?.type === 'NONE').slice(0, 12))}</Panel></div>;
    if (view === 'intraday') return <div className="grid gap-4 xl:grid-cols-[1fr_1fr]"><Panel title="盘口候选" icon={BarChart3}>{renderCandidates(focus.slice(0, 8))}</Panel><Panel title="猫爪 Level-2 接入状态" icon={Database}><Fact label="数据类型" value="逐笔成交 / 逐笔委托 / 十档盘口" /><Fact label="调用范围" value="候选详情按股按日" /><Fact label="承接指标" value="买方吸收 / OBI / 补单" /><Fact label="风险指标" value="疑似派发 / 异常挂撤单" /><Fact label="存储策略" value="复用现有缓存" detail="本模块只保存规则结论和证据摘要" /></Panel></div>;
    if (view === 'risk') return <div className="space-y-4"><WildmanAccountPanel /><div className="grid gap-4 xl:grid-cols-2"><Panel title="今日排除池" icon={CircleSlash2} meta={`${payload.reject_pool?.length || 0}只`}>{renderCandidates((payload.reject_pool || []).slice(0, 16))}</Panel><Panel title="风险核查条件" icon={ShieldAlert}>{['退潮期与鱼尾行情', '过度一致、后排跟风', '假突破或无承接', '跌破防守锚点', '模式外、无止损或盈亏比不足', '重大公告或财务风险命中', '公告核查不完整，继续观察', '连续亏损提醒，扫描持续开放'].map((item) => <div key={item} className="border-b border-border py-2.5 text-xs text-text last:border-0">{item}</div>)}</Panel></div></div>;
    if (view === 'plan') return <div className="grid gap-4 xl:grid-cols-[1fr_1fr]"><Panel title="盘前计划" icon={CalendarCheck}><Fact label="市场阶段" value={`${cycle.cycle} · ${cycle.cycle_node}`} /><Fact label="核心主线" value={(payload.mainlines || []).filter((row: AnyMap) => ['核心主线确认', '高质量候选'].includes(row.state)).slice(0, 4).map((row: AnyMap) => row.theme_name).join(' / ') || '等待确认'} /><Fact label="允许模式" value={(cycle.allowed_actions || []).join(' / ')} /><Fact label="禁止模式" value={(cycle.forbidden_actions || []).join(' / ')} /><Fact label="仓位" value={cycle.position_range} /></Panel><Panel title="三套预期剧本" icon={BrainCircuit}><Fact label="超预期" value="持有或等待模式内盘口确认" detail="昨日分歧，竞价高开且带量上攻" /><Fact label="符合预期" value="观察锚点和分时承接" detail="正常溢价或平开换手" /><Fact label="不及预期" value="竞价或开盘优先处理" detail="该强不强、竞价萎缩或无承接；绝不补仓" /></Panel><Panel title="重点盯盘" icon={Target} className="xl:col-span-2">{renderCandidates(focus.slice(0, 10))}</Panel></div>;
    if (view === 'review') return <div className="grid gap-4 xl:grid-cols-[.8fr_1.2fr]"><Panel title="记录交易复盘" icon={Save}><div className="space-y-3"><input value={reviewForm.symbol} onChange={(event) => setReviewForm({ ...reviewForm, symbol: event.target.value.replace(/\D/g, '').slice(0, 6) })} placeholder="股票代码" className="h-9 w-full rounded border border-border bg-bg px-3 text-xs text-text outline-none focus:border-accent" /><select value={reviewForm.setup_type} onChange={(event) => setReviewForm({ ...reviewForm, setup_type: event.target.value })} className="h-9 w-full rounded border border-border bg-bg px-3 text-xs text-text"><option value="FIRST_DIVERGENCE">主升首分歧</option><option value="WEAK_TO_STRONG">弱转强</option><option value="SWING_PULLBACK">波段回踩</option><option value="B_POINT">B点</option><option value="LIMIT_PULLBACK">涨停缩量回踩</option><option value="NONE">模式外</option></select><input value={reviewForm.pnl_pct} onChange={(event) => setReviewForm({ ...reviewForm, pnl_pct: event.target.value })} placeholder="已实现盈亏百分比，例如 -3.5" inputMode="decimal" disabled={!reviewForm.completed} className="h-9 w-full rounded border border-border bg-bg px-3 text-xs text-text outline-none focus:border-accent disabled:opacity-50" /><label className="flex items-center gap-2 text-xs text-text-secondary"><input type="checkbox" checked={reviewForm.mode_inside} onChange={(event) => setReviewForm({ ...reviewForm, mode_inside: event.target.checked })} />模式内交易</label><label className="flex items-center gap-2 text-xs text-text-secondary"><input type="checkbox" checked={reviewForm.completed} onChange={(event) => setReviewForm({ ...reviewForm, completed: event.target.checked })} />已完成，纳入已实现统计</label>{reviewForm.completed && <input type="date" value={reviewForm.exit_date} onChange={(event) => setReviewForm({ ...reviewForm, exit_date: event.target.value })} className="h-9 w-full rounded border border-border bg-bg px-3 text-xs text-text outline-none focus:border-accent" aria-label="退出日期" />}<textarea value={reviewForm.review_text} onChange={(event) => setReviewForm({ ...reviewForm, review_text: event.target.value })} placeholder="预判、实际、执行与纠错" className="min-h-24 w-full resize-y rounded border border-border bg-bg p-3 text-xs text-text outline-none focus:border-accent" /><button type="button" disabled={saving || reviewForm.symbol.length !== 6} onClick={() => void saveReview()} className="inline-flex h-9 w-full items-center justify-center gap-2 rounded bg-accent px-4 text-xs font-medium text-white disabled:opacity-50">{saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}保存复盘</button></div></Panel><div className="space-y-4"><Panel title="复盘统计" icon={TrendingUp} meta={<div className="flex gap-1">{(['daily', 'weekly', 'monthly'] as const).map((item) => <button key={item} onClick={() => setReviewPeriod(item)} className={`rounded px-2 py-1 ${reviewPeriod === item ? 'bg-accent/15 text-accent' : 'text-text-secondary'}`}>{item === 'daily' ? '日' : item === 'weekly' ? '周' : '月'}</button>)}</div>}><ReviewMetrics metrics={reviewMetrics} /></Panel><Panel title="交易日志" icon={BookOpenCheck}>{(review?.trades || []).length ? (review?.trades || []).map((row: AnyMap) => <div key={row.trade_id} className="grid gap-2 border-b border-border py-2.5 text-xs last:border-0 sm:grid-cols-[1fr_auto]"><div><b className="font-medium text-text">{row.stock_name || row.symbol} · {row.symbol}</b><p className="mt-1 text-[10px] text-text-secondary">{row.setup_type} · {row.entry_date} · {row.mode_inside ? '模式内' : '模式外'}{row.exit_date ? ` · 退出 ${row.exit_date}` : ''}</p></div><b className={tone(row.pnl_pct)}>{pct(row.pnl_pct)}</b></div>) : <div className="py-5 text-xs text-text-secondary">当前周期还没有交易复盘记录</div>}</Panel></div></div>;
    return renderToolbox();
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
        <header className="sticky top-10 z-30 flex min-h-14 items-center justify-between gap-3 border-b border-border bg-bg/95 px-3 backdrop-blur md:px-5"><div className="flex min-w-0 items-center gap-2"><button onClick={() => setMobileNav(true)} className="grid h-8 w-8 shrink-0 place-items-center rounded border border-border text-text-secondary lg:hidden" title="打开模块导航"><Menu size={15} /></button><div className="min-w-0"><h1 className="truncate text-sm font-semibold text-text">{activeTitle}</h1><p className="mt-0.5 hidden truncate text-xs text-text-secondary sm:block">市场周期 → 主线 → 地位 → 节点 → 盘口 → 风险 → 仓位 → 复盘</p></div></div><div className="flex shrink-0 items-center gap-2"><label className="hidden items-center gap-1.5 text-[10px] text-text-secondary sm:flex"><input type="checkbox" checked={excludeStar} onChange={(event) => setExcludeStar(event.target.checked)} />排除科创</label><label className="hidden items-center gap-1.5 text-[10px] text-text-secondary sm:flex"><input type="checkbox" checked={excludeGem} onChange={(event) => setExcludeGem(event.target.checked)} />排除创业</label><button onClick={() => void load(true)} disabled={loading} className="inline-flex h-8 items-center gap-1.5 rounded border border-border px-2.5 text-[11px] text-text-secondary hover:border-accent hover:text-accent disabled:opacity-50"><RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} /><span className="hidden sm:inline">刷新规则快照</span></button></div></header>
        <div className="p-3 md:p-5">{error && <div className="mb-4 flex items-start gap-2 rounded border border-down/30 bg-down/5 p-3 text-xs text-down"><AlertTriangle size={14} className="mt-0.5 shrink-0" /><span>{error}</span></div>}{loading && !payload ? <div className="grid h-72 place-items-center text-xs text-text-secondary"><div className="text-center"><Loader2 size={22} className="mx-auto animate-spin text-accent" /><p className="mt-3">正在构建周期、主线与候选解释链</p></div></div> : renderView()}</div>
      </main>
    </div>
    <CandidateDetail row={selected} loading={detailLoading} onClose={() => { candidateAbortRef.current?.abort(); setDetailLoading(false); setSelected(null); }} onRefresh={() => selected && void loadDetail(selected.symbol, true)} />
    <ToolboxDetail row={toolboxSelected} detail={toolboxDetail} loading={toolboxDetailLoading} onClose={() => { toolboxDetailAbortRef.current?.abort(); setToolboxDetailLoading(false); setToolboxSelected(null); setToolboxDetail(null); }} onRefresh={() => toolboxSelected && void loadToolboxDetail(toolboxSelected, true)} />
  </div>;
}
