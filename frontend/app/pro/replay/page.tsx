'use client';

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  BarChart3,
  BrainCircuit,
  CalendarDays,
  CheckCircle2,
  Clock3,
  Database,
  Flame,
  History,
  Layers3,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  TrendingDown,
  TrendingUp,
  Wallet,
  X,
  type LucideIcon,
} from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';

type AnyMap = Record<string, any>;

interface ReplaySection {
  available?: boolean;
  rows?: AnyMap[];
  count?: number;
  summary?: AnyMap;
  source?: string;
  data_date?: string | null;
  updated_at?: string | null;
  is_realtime?: boolean;
  cache_hit?: boolean;
  quality?: string;
  error?: string | null;
}

interface ReplayPayload {
  available?: boolean;
  requested_date?: string | null;
  trade_date?: string | null;
  date_adjusted?: boolean;
  date_adjustment_note?: string | null;
  updated_at?: string | null;
  is_realtime?: boolean;
  cache_hit?: boolean;
  source?: string;
  emotion?: AnyMap;
  sections?: Record<string, ReplaySection>;
  history?: { rows?: AnyMap[]; count?: number; source?: string; data_start?: string | null; data_end?: string | null; formula_note?: string };
  available_dates?: DateEntry[];
  quality?: AnyMap;
}

interface DateEntry {
  date: string;
  source?: string;
  coverage?: number;
  emotion_snapshot?: boolean;
  market_sentiment?: boolean;
  fund_flow?: boolean;
  daily_bars?: boolean;
}

interface AnalysisResult {
  trade_date?: string | null;
  interpretation?: string;
  ai_generated?: boolean;
  data_cutoff_time?: string | null;
  sources?: string[];
  quality?: AnyMap;
  policy?: string;
}

type DetailTab = 'limit_up' | 'limit_down' | 'failed_limit' | 'yesterday_limit';
type TopicTab = 'strong_sectors' | 'topic_rotation' | 'topic_auction' | 'fengkou' | 'hot_search' | 'reasons';

const DETAIL_TABS: Array<{ key: DetailTab; label: string }> = [
  { key: 'limit_up', label: '涨停明细' },
  { key: 'limit_down', label: '跌停明细' },
  { key: 'failed_limit', label: '炸板明细' },
  { key: 'yesterday_limit', label: '昨日涨停' },
];

const TOPIC_TABS: Array<{ key: TopicTab; label: string }> = [
  { key: 'strong_sectors', label: '强势板块' },
  { key: 'topic_rotation', label: '五日轮动' },
  { key: 'topic_auction', label: '板块竞价' },
  { key: 'fengkou', label: '最强风口' },
  { key: 'hot_search', label: '实时热搜' },
  { key: 'reasons', label: '板块原因' },
];

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function numberValue(value: unknown): number | null {
  if (finite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function integer(value: unknown): number | null {
  const parsed = numberValue(value);
  return parsed == null ? null : Math.round(parsed);
}

function numberText(value: unknown, digits = 1): string {
  const parsed = numberValue(value);
  return parsed == null ? '--' : parsed.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function countText(value: unknown): string {
  const parsed = integer(value);
  return parsed == null ? '--' : parsed.toLocaleString('zh-CN');
}

function percentText(value: unknown, digits = 2): string {
  const parsed = numberValue(value);
  if (parsed == null) return '--';
  return `${parsed > 0 ? '+' : ''}${parsed.toFixed(digits)}%`;
}

function moneyText(value: unknown, signed = true): string {
  const parsed = numberValue(value);
  if (parsed == null) return '--';
  const absolute = Math.abs(parsed);
  const body = absolute >= 1e8
    ? `${(absolute / 1e8).toFixed(2)}亿`
    : absolute >= 1e4
      ? `${(absolute / 1e4).toFixed(1)}万`
      : absolute.toFixed(0);
  if (!signed) return `${parsed < 0 ? '-' : ''}${body}`;
  return `${parsed > 0 ? '+' : parsed < 0 ? '-' : ''}${body}`;
}

function tone(value: unknown): string {
  const parsed = numberValue(value);
  if (parsed == null || parsed === 0) return 'text-text-secondary';
  // A-share convention: red is up/inflow, green is down/outflow.
  return parsed > 0 ? 'text-up' : 'text-down';
}

function toneBorder(value: unknown): string {
  const parsed = numberValue(value);
  if (parsed == null || parsed === 0) return 'border-border';
  return parsed > 0 ? 'border-up/35' : 'border-down/35';
}

function cleanText(value: unknown): string {
  return String(value ?? '')
    .replace(/```[\s\S]*?```/g, '')
    .replace(/\*\*|__|^#{1,6}\s*/gm, '')
    .replace(/^\s*[-*_]{3,}\s*$/gm, '')
    .replace(/[ \t]+\n/g, '\n')
    .trim();
}

function sourceLabel(source: string | undefined): string {
  const value = String(source || '');
  if (!value) return '暂无来源';
  const labels: string[] = [];
  if (value.includes('user_imported_csv')) labels.push('用户 CSV');
  if (value.includes('numcat')) labels.push('猫爪接口');
  if (value.includes('eastmoney')) labels.push('东方财富公开接口');
  if (value.includes('database_fund_flow')) labels.push('数据库资金快照');
  else if (value.includes('database')) labels.push('数据库缓存');
  if (value.includes('stock_daily_bars')) labels.push('日线推导');
  if (value.includes('daily_emotion_aggregate')) labels.push('市场情绪汇总');
  return labels.length ? labels.join(' + ') : value;
}

function dateText(value: unknown): string {
  const text = String(value || '');
  return text.length >= 10 ? text.slice(0, 10) : text || '--';
}

function timeText(value: unknown): string {
  const text = String(value || '');
  if (!text) return '--';
  return text.includes('T') ? text.slice(0, 16).replace('T', ' ') : text.slice(0, 16);
}

function displayRowName(row: AnyMap): string {
  return String(row.name || row.theme_name || row.sector || row.code || row.symbol || '未命名');
}

function rowCode(row: AnyMap): string {
  return String(row.code || row.symbol || '').replace(/\.(SH|SZ|BJ)$/i, '').slice(0, 12);
}

function sectionRows(section: ReplaySection | undefined): AnyMap[] {
  return Array.isArray(section?.rows) ? section?.rows || [] : [];
}

function SectionMeta({ section, compact = false }: { section?: ReplaySection; compact?: boolean }) {
  if (!section) return null;
  const status = section.is_realtime ? '实时' : section.cache_hit ? '缓存' : '收盘/历史';
  return (
    <span className={`inline-flex min-w-0 items-center gap-1 text-[9px] text-text-secondary ${compact ? '' : 'mt-1'}`} title={`${sourceLabel(section.source)} · 数据日 ${section.data_date || '--'}`}>
      <Database size={10} className="shrink-0" />
      <span className="truncate">{sourceLabel(section.source)} · {status} · {section.data_date || '--'}</span>
    </span>
  );
}

function Panel({
  title,
  icon: Icon,
  subtitle,
  action,
  children,
  className = '',
  id,
}: {
  title: string;
  icon: LucideIcon;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section id={id} className={`min-w-0 overflow-hidden rounded-md border border-border bg-card ${className}`}>
      <header className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-3 sm:px-4">
        <div className="flex min-w-0 items-center gap-2">
          <Icon size={15} className="shrink-0 text-accent" />
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-text">{title}</h2>
            {subtitle && <p className="mt-0.5 truncate text-[10px] text-text-secondary">{subtitle}</p>}
          </div>
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}

function Metric({ label, value, hint, className = 'text-text', valueTitle }: { label: string; value: string; hint?: string; className?: string; valueTitle?: string }) {
  return (
    <div className="min-w-0 border-b border-border/70 bg-card px-3 py-3 last:border-b-0 sm:px-4">
      <div className="truncate text-[10px] text-text-secondary">{label}</div>
      <div className={`mt-1 truncate font-mono text-lg font-medium ${className}`} title={valueTitle || value}>{value}</div>
      <div className="mt-1 min-h-4 truncate text-[9px] text-text-secondary" title={hint}>{hint || ' '}</div>
    </div>
  );
}

function ThinBar({ value, color = 'bg-accent' }: { value: unknown; color?: string }) {
  const parsed = Math.max(0, Math.min(100, numberValue(value) || 0));
  return <div className="h-1 overflow-hidden rounded-sm bg-border"><span className={`block h-full rounded-sm ${color}`} style={{ width: `${parsed}%` }} /></div>;
}

function EmptyDetail({ section, label = '该截面没有返回明细行' }: { section?: ReplaySection; label?: string }) {
  const summary = section?.summary || {};
  const hasSummary = Object.values(summary).some((item) => item !== null && item !== undefined && item !== '');
  return (
    <div className="px-3 py-5 text-xs text-text-secondary sm:px-4">
      <div className="flex items-center gap-2"><Database size={13} className="text-warn" />{hasSummary ? `${label}，汇总指标仍可用` : label}</div>
      {section?.error && <div className="mt-1 text-[10px] text-warn">接口状态：{cleanText(section.error)}</div>}
      <SectionMeta section={section} />
    </div>
  );
}

function SourceNotice({ payload }: { payload: ReplayPayload }) {
  const quality = payload.quality || {};
  const unavailable = Array.isArray(quality.unavailable_sections) ? quality.unavailable_sections : [];
  const errors = Array.isArray(quality.errors) ? quality.errors : [];
  return (
    <div className="flex min-w-0 flex-wrap items-start justify-between gap-2 border-b border-accent/25 bg-accent/5 px-3 py-2.5 text-[10px] leading-4 text-text-secondary sm:px-4">
      <div className="flex min-w-0 items-start gap-2"><CheckCircle2 size={13} className="mt-0.5 shrink-0 text-accent" /><span className="min-w-0">数据日 {payload.trade_date || '--'} · {payload.is_realtime ? '交易时段实时截面' : payload.cache_hit ? '规范化缓存截面' : '最近收盘/历史截面'} · 来源 {sourceLabel(payload.source)}</span></div>
      <div className="flex shrink-0 items-center gap-2 font-mono text-[9px] text-text-secondary"><span>覆盖 {numberText(quality.coverage_pct, 1)}%</span><span>{quality.available_sections ?? '--'}/{quality.section_count ?? '--'} 模块</span></div>
      {(Boolean(payload.date_adjusted) || unavailable.length > 0 || errors.length > 0) && <div className="basis-full border-t border-accent/15 pt-2 text-warn">{payload.date_adjustment_note || ''}{unavailable.length ? ` 明细未返回：${unavailable.slice(0, 4).join('、')}` : ''}{errors.length ? ` 状态：${errors[0]}` : ''}</div>}
    </div>
  );
}

function MarketHeadline({ emotion, section }: { emotion: AnyMap; section?: ReplaySection }) {
  const environment = String(emotion.market_environment || '数据不足');
  const moneyScore = numberValue(emotion.money_effect_score);
  const riskScore = numberValue(emotion.risk_release_score);
  const breadth = numberValue(emotion.breadth_pct);
  const environmentTone = environment.includes('进攻') || environment.includes('修复') ? 'text-up' : environment.includes('风险') || environment.includes('退潮') ? 'text-down' : 'text-warn';
  return (
    <Panel title="市场事实与情绪判断" icon={Activity} subtitle="涨跌宽度、成交、涨跌停与接力效率的同日截面" id="market-facts">
      <div className="grid gap-px bg-border sm:grid-cols-[minmax(190px,1.05fr)_minmax(0,1.95fr)]">
        <div className="min-w-0 bg-card p-4 sm:p-5">
          <div className="text-[10px] text-text-secondary">市场环境</div>
          <div className={`mt-2 break-words text-xl font-semibold leading-tight ${environmentTone}`}>{environment}</div>
          <div className="mt-3 flex items-center justify-between text-[10px] text-text-secondary"><span>宽度占比</span><b className="font-mono text-text">{percentText(breadth, 1)}</b></div>
          <ThinBar value={breadth} color={breadth != null && breadth >= 50 ? 'bg-up' : 'bg-down'} />
          <div className="mt-4 grid grid-cols-2 gap-2">
            <div className="border border-border bg-bg px-2.5 py-2"><div className="text-[9px] text-text-secondary">赚钱效应</div><b className="mt-1 block font-mono text-base text-up">{numberText(moneyScore, 1)}</b></div>
            <div className="border border-border bg-bg px-2.5 py-2"><div className="text-[9px] text-text-secondary">风险释放</div><b className="mt-1 block font-mono text-base text-down">{numberText(riskScore, 1)}</b></div>
          </div>
          <SectionMeta section={section} />
        </div>
        <div className="grid grid-cols-2 gap-px bg-border md:grid-cols-4">
          <Metric label="上涨家数" value={`${countText(emotion.up_count)} 只`} hint={`涨超7% ${countText(emotion.up_7pct_count)}只`} className="text-up" />
          <Metric label="下跌家数" value={`${countText(emotion.down_count)} 只`} hint={`跌超7% ${countText(emotion.down_7pct_count)}只`} className="text-down" />
          <Metric label="平盘 / 总数" value={`${countText(emotion.flat_count)} / ${countText(emotion.stock_count)}`} hint="市场宽度分母" />
          <Metric label="涨停 / 跌停" value={`${countText(emotion.limit_up_count)} / ${countText(emotion.limit_down_count)}`} hint={`曾涨停 ${countText(emotion.touched_limit_up_count)}只`} className="text-text" />
          <Metric label="炸板 / 炸板率" value={`${countText(emotion.failed_limit_count)} / ${percentText(emotion.failed_limit_rate, 1)}`} hint="封板质量" className="text-warn" />
          <Metric label="最高连板" value={`${countText(emotion.max_streak_height)} 板`} hint={`二板及以上 ${countText(emotion.second_board_or_higher_count)}只`} className="text-warn" />
          <Metric label="三市成交额" value={moneyText(emotion.market_amount, false)} hint={`较前日 ${moneyText(emotion.market_amount_change)}`} className={tone(emotion.market_amount_change)} />
          <Metric label="主力净额" value={moneyText(emotion.main_net_inflow)} hint={`竞价 ${moneyText(emotion.auction_main_net_inflow)}`} className={tone(emotion.main_net_inflow)} />
        </div>
      </div>
    </Panel>
  );
}

function AuctionPanel({ section }: { section?: ReplaySection }) {
  const summary = section?.summary || {};
  const rows = sectionRows(section);
  return (
    <Panel title="竞价与封单快照" icon={Clock3} subtitle="9:20 / 9:25 边界及竞价主力行为" action={<SectionMeta section={section} compact />}>
      <div className="grid grid-cols-2 gap-px bg-border sm:grid-cols-3">
        <Metric label="竞价主力净额" value={moneyText(summary.auction_main_net_inflow)} className={tone(summary.auction_main_net_inflow)} />
        <Metric label="涨停委买额" value={moneyText(summary.limit_up_order_amount)} hint={`家数 ${countText(summary.limit_up_order_count)}`} className={tone(summary.limit_up_order_amount)} />
        <Metric label="隔夜封单额" value={moneyText(summary.overnight_order_amount)} className={tone(summary.overnight_order_amount)} />
        <Metric label="9:20封单额" value={moneyText(summary.order_amount_0920)} hint={`涨停委买 ${moneyText(summary.limit_up_order_amount_0920)}`} className={tone(summary.order_amount_0920)} />
        <Metric label="9:25封单额" value={moneyText(summary.order_amount_0925)} hint={`家数 ${countText(summary.order_count_0925)}`} className={tone(summary.order_amount_0925)} />
        <Metric label="一字板" value={`${countText(summary.one_price_count)} 只`} />
      </div>
      {rows.length ? <div className="replay-table-wrap"><table className="replay-table"><thead><tr><th>代码</th><th>股票</th><th>竞价涨幅</th><th>竞价额</th><th>竞价量</th><th>未匹配</th><th>时间</th></tr></thead><tbody>{rows.slice(0, 20).map((row, index) => <tr key={`${rowCode(row)}-${index}`}><td className="font-mono text-text-secondary">{rowCode(row) || '--'}</td><td className="max-w-[150px] truncate text-text" title={displayRowName(row)}>{displayRowName(row)}</td><td className={tone(row.change_pct)}>{percentText(row.change_pct, 2)}</td><td className="font-mono">{moneyText(row.auction_amount ?? row.amount)}</td><td className="font-mono">{countText(row.auction_volume)}</td><td className={tone(row.unmatched_volume)}>{countText(row.unmatched_volume)}</td><td>{row.first_time || row.last_time || '--'}</td></tr>)}</tbody></table></div> : <EmptyDetail section={section} label="竞价明细未返回" />}
    </Panel>
  );
}

function LimitDetailPanel({ sections, emotion }: { sections: Record<string, ReplaySection>; emotion: AnyMap }) {
  const [tab, setTab] = useState<DetailTab>('limit_up');
  const section = sections[tab];
  const rows = sectionRows(section);
  const count = tab === 'limit_up' ? emotion.limit_up_count : tab === 'limit_down' ? emotion.limit_down_count : tab === 'failed_limit' ? emotion.failed_limit_count : emotion.yesterday_limit_up_count;
  return (
    <Panel title="涨跌停与炸板明细" icon={Flame} subtitle="聚合数量与股票级明细分开标识，避免把汇总当作明细" action={<span className="font-mono text-[10px] text-text-secondary">汇总 {countText(count)} 只</span>}>
      <div className="flex gap-1 overflow-x-auto border-b border-border px-3 py-2">{DETAIL_TABS.map((item) => <button key={item.key} type="button" onClick={() => setTab(item.key)} className={`shrink-0 rounded border px-2.5 py-1.5 text-[10px] ${tab === item.key ? 'border-accent/60 bg-accent/10 text-accent' : 'border-transparent text-text-secondary hover:border-border hover:text-text'}`}>{item.label}<span className="ml-1 font-mono">{countText(item.key === 'limit_up' ? emotion.limit_up_count : item.key === 'limit_down' ? emotion.limit_down_count : item.key === 'failed_limit' ? emotion.failed_limit_count : emotion.yesterday_limit_up_count)}</span></button>)}</div>
      {rows.length ? <div className="replay-table-wrap"><table className="replay-table"><thead><tr><th>代码</th><th>股票</th><th>涨跌幅</th><th>连板</th><th>成交额</th><th>封单额</th><th>首次/最后</th><th>原因</th></tr></thead><tbody>{rows.slice(0, 80).map((row, index) => <tr key={`${rowCode(row)}-${index}`}><td className="font-mono text-text-secondary">{rowCode(row) || '--'}</td><td className="max-w-[150px] truncate text-text" title={displayRowName(row)}>{displayRowName(row)}</td><td className={tone(row.change_pct)}>{percentText(row.change_pct, 2)}</td><td className="font-mono text-warn">{countText(row.continuous_days)}</td><td className="font-mono">{moneyText(row.amount, false)}</td><td className="font-mono">{moneyText(row.seal_amount, false)}</td><td>{row.first_time || row.last_time || '--'}</td><td className="max-w-[240px] truncate" title={row.reason}>{row.reason || '--'}</td></tr>)}</tbody></table></div> : <EmptyDetail section={section} label={`${DETAIL_TABS.find((item) => item.key === tab)?.label || '明细'}未返回`} />}
    </Panel>
  );
}

function StreakLadder({ section }: { section?: ReplaySection }) {
  const rows = sectionRows(section);
  return (
    <Panel title="连板天梯" icon={Layers3} subtitle="按连板高度观察接力结构" action={<SectionMeta section={section} compact />}>
      {rows.length ? <div className="divide-y divide-border">{rows.map((row, index) => <div key={`${row.height}-${index}`} className="grid min-w-0 grid-cols-[48px_48px_minmax(0,1fr)_90px] items-center gap-2 px-3 py-2.5 text-[10px] sm:grid-cols-[58px_58px_minmax(0,1fr)_110px] sm:px-4"><span className="font-mono text-base text-warn">{row.height === '4+' ? '4+' : `${row.height || '--'}板`}</span><span className="font-mono text-text">{countText(row.count)}只</span><span className="min-w-0 truncate text-text-secondary" title={Array.isArray(row.stocks) ? row.stocks.join('、') : ''}>{Array.isArray(row.stocks) && row.stocks.length ? row.stocks.slice(0, 5).join('、') : '仅有高度汇总，未返回股票明细'}</span><span className="text-right font-mono text-text-secondary">{moneyText(row.total_seal_amount ?? row.total_amount, false)}</span></div>)}</div> : <EmptyDetail section={section} label="连板天梯未返回" />}
    </Panel>
  );
}

function TopicTable({ tab, section }: { tab: TopicTab; section?: ReplaySection }) {
  const rows = sectionRows(section);
  if (!rows.length) return <EmptyDetail section={section} label={`${TOPIC_TABS.find((item) => item.key === tab)?.label || '题材'}明细未返回`} />;
  if (tab === 'reasons') return <div className="divide-y divide-border">{rows.slice(0, 24).map((row, index) => <div key={`${rowCode(row)}-${index}`} className="min-w-0 px-3 py-2.5 sm:px-4"><div className="flex min-w-0 items-center gap-2"><span className="font-mono text-[9px] text-text-secondary">{rowCode(row) || '--'}</span><span className="truncate text-xs text-text">{displayRowName(row)}</span><span className="ml-auto shrink-0 text-[9px] text-warn">{row.reason_source || '原因'}</span></div><p className="mt-1 line-clamp-2 text-[10px] leading-4 text-text-secondary">{cleanText(row.reason) || '未返回公开原因说明'}</p></div>)}</div>;
  return <div className="replay-table-wrap"><table className="replay-table"><thead><tr>{tab === 'topic_rotation' ? <><th>板块</th><th>观察日数</th><th>累计净额</th><th>最新净额</th><th>变化</th><th>状态</th></> : tab === 'topic_auction' ? <><th>板块</th><th>排名</th><th>竞价放量</th><th>异常额</th><th>主力净额</th></> : <><th>排名</th><th>板块/股票</th><th>涨跌幅</th><th>强度</th><th>主力净额</th><th>说明</th></>}</tr></thead><tbody>{rows.slice(0, 30).map((row, index) => <tr key={`${rowCode(row)}-${index}`}>
    {tab === 'topic_rotation' ? <><td className="max-w-[140px] truncate text-text" title={displayRowName(row)}>{displayRowName(row)}</td><td className="font-mono">{countText(row.sessions)}</td><td className={tone(row.total_main_net_inflow)}>{moneyText(row.total_main_net_inflow)}</td><td className={tone(row.latest_main_net_inflow)}>{moneyText(row.latest_main_net_inflow)}</td><td className={tone(row.flow_acceleration)}>{moneyText(row.flow_acceleration)}</td><td><span className={row.state === '强化' ? 'text-up' : row.state === '转弱' ? 'text-down' : 'text-warn'}>{row.state || '--'}</span></td></> : tab === 'topic_auction' ? <><td className="max-w-[150px] truncate text-text">{displayRowName(row)}</td><td className="font-mono">{countText(row.rank)}</td><td className="font-mono">{numberText(row.bid_volume_burst, 1)}</td><td className="font-mono">{moneyText(row.abnormal_amount, false)}</td><td className={tone(row.main_net_inflow)}>{moneyText(row.main_net_inflow)}</td></> : <><td className="font-mono text-text-secondary">{countText(row.rank || index + 1)}</td><td className="max-w-[150px] truncate text-text" title={displayRowName(row)}>{displayRowName(row)}</td><td className={tone(row.change_pct)}>{percentText(row.change_pct, 2)}</td><td className="font-mono text-accent">{numberText(row.strength_score ?? row.strength, 1)}</td><td className={tone(row.main_net_inflow)}>{moneyText(row.main_net_inflow)}</td><td className="max-w-[180px] truncate" title={row.reason}>{row.reason || row.sector || row.selected_themes || '--'}</td></>}
  </tr>)}</tbody></table></div>;
}

function TopicPanel({ sections }: { sections: Record<string, ReplaySection> }) {
  const [tab, setTab] = useState<TopicTab>('strong_sectors');
  const sectionKey: Record<TopicTab, string> = { strong_sectors: 'strong_sectors', topic_rotation: 'topic_rotation', topic_auction: 'topic_auction', fengkou: 'strongest_fengkou', hot_search: 'hot_search', reasons: 'limit_reasons' };
  const section = sections[sectionKey[tab]];
  return (
    <Panel title="板块、题材与资金方向" icon={BarChart3} subtitle="当日强弱、五日轮动、竞价风口和公开原因统一查看" action={<SectionMeta section={section} compact />} id="topics">
      <div className="flex gap-1 overflow-x-auto border-b border-border px-3 py-2">{TOPIC_TABS.map((item) => <button key={item.key} type="button" onClick={() => setTab(item.key)} className={`shrink-0 rounded border px-2.5 py-1.5 text-[10px] ${tab === item.key ? 'border-accent/60 bg-accent/10 text-accent' : 'border-transparent text-text-secondary hover:border-border hover:text-text'}`}>{item.label}</button>)}</div>
      <TopicTable tab={tab} section={section} />
    </Panel>
  );
}

function EventPanel({ section, title, icon }: { section?: ReplaySection; title: string; icon: typeof Activity }) {
  const rows = sectionRows(section);
  return (
    <Panel title={title} icon={icon} subtitle="异动与公开事件按所选交易日筛选" action={<SectionMeta section={section} compact />}>
      {rows.length ? <div className="divide-y divide-border">{rows.slice(0, 30).map((row, index) => <div key={`${rowCode(row)}-${index}`} className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-2 px-3 py-2.5 sm:px-4"><span className={`mt-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded border text-[9px] ${String(row.status).includes('高') ? 'border-down/45 text-down' : 'border-warn/45 text-warn'}`}>{row.status || row.event_type?.slice(0, 1) || '·'}</span><div className="min-w-0"><div className="truncate text-xs text-text">{displayRowName(row)} {rowCode(row) && <span className="font-mono text-[9px] text-text-secondary">{rowCode(row)}</span>}</div><div className="mt-1 line-clamp-2 text-[10px] leading-4 text-text-secondary">{cleanText(row.description || row.reason || row.event_type) || '公开描述未返回'}</div></div><span className="whitespace-nowrap font-mono text-[9px] text-text-secondary">{row.event_time || '--'}</span></div>)}</div> : <EmptyDetail section={section} label={`${title}未返回`} />}
    </Panel>
  );
}

function HistoryChart({ rows, selectedDate }: { rows: AnyMap[]; selectedDate?: string | null }) {
  const ordered = [...rows].sort((a, b) => String(a.date || a.trade_date).localeCompare(String(b.date || b.trade_date)));
  if (!ordered.length) return <div className="grid min-h-40 place-items-center text-xs text-text-secondary">暂无连续历史样本</div>;
  return (
    <div className="overflow-x-auto px-3 pb-3 pt-4 sm:px-4">
      <div className="flex min-w-[620px] items-end gap-1.5" style={{ height: 190 }}>
        {ordered.map((row, index) => {
          const date = String(row.date || row.trade_date || '');
          const moneyEffect = Math.max(2, Math.min(100, numberValue(row.money_effect_score) || 0));
          const risk = Math.max(2, Math.min(100, numberValue(row.risk_release_score) || 0));
          const active = date === selectedDate;
          return <div key={`${date}-${index}`} className={`flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1 rounded-sm px-0.5 ${active ? 'bg-accent/10' : ''}`} title={`${date}\n赚钱效应 ${numberText(row.money_effect_score)} · 风险释放 ${numberText(row.risk_release_score)}`}><div className="flex h-[145px] w-full items-end justify-center gap-0.5"><span className="block w-[42%] rounded-t-sm bg-up/75" style={{ height: `${moneyEffect}%` }} /><span className="block w-[42%] rounded-t-sm bg-down/75" style={{ height: `${risk}%` }} /></div><span className="w-full truncate text-center font-mono text-[8px] text-text-secondary">{date.slice(5)}</span></div>;
        })}
      </div>
      <div className="mt-3 flex items-center gap-4 text-[9px] text-text-secondary"><span className="inline-flex items-center gap-1"><i className="h-2 w-2 bg-up" />赚钱效应</span><span className="inline-flex items-center gap-1"><i className="h-2 w-2 bg-down" />风险释放</span><span className="ml-auto">颜色遵循A股涨跌显示约定</span></div>
    </div>
  );
}

function LadderHistory({ rows }: { rows: AnyMap[] }) {
  const recent = rows.slice(-12);
  return <div className="grid grid-cols-2 gap-px bg-border sm:grid-cols-3 lg:grid-cols-4">{recent.map((row, index) => <div key={`${row.date || row.trade_date}-${index}`} className="min-w-0 bg-card px-3 py-2.5"><div className="font-mono text-[9px] text-text-secondary">{dateText(row.date || row.trade_date)}</div><div className="mt-1 flex items-end justify-between gap-2"><span className="font-mono text-sm text-text">{numberText(row.money_effect_score, 1)}</span><span className={tone((numberValue(row.money_effect_score) || 0) - (numberValue(row.risk_release_score) || 0))}>{String(row.market_environment || '--')}</span></div><div className="mt-1 text-[9px] text-text-secondary">宽度 {percentText(row.breadth_pct, 1)} · 连板 {countText(row.max_streak_height)}板</div></div>)}</div>;
}

function AnalysisPanel({ analysis, loading, onAnalyze }: { analysis: AnalysisResult | null; loading: boolean; onAnalyze: () => void }) {
  return (
    <Panel title="AI复盘解读" icon={BrainCircuit} subtitle="只解释所选截面的结构化事实，不替换原始数值" action={<button type="button" onClick={onAnalyze} disabled={loading} className="inline-flex h-7 items-center gap-1.5 rounded border border-accent/45 px-2.5 text-[10px] text-accent hover:bg-accent/10 disabled:cursor-wait disabled:opacity-60">{loading ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}{loading ? '分析中' : analysis ? '重新分析' : '生成解读'}</button>}>
      {analysis ? <div className="p-3 sm:p-4"><div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[9px] text-text-secondary"><span className="inline-flex items-center gap-1"><BrainCircuit size={11} className="text-accent" />{analysis.ai_generated ? 'AI生成' : '规则审计'}</span><span>数据日 {analysis.trade_date || '--'}</span><span>截点 {timeText(analysis.data_cutoff_time)}</span></div><div className="whitespace-pre-line break-words text-xs leading-6 text-text">{cleanText(analysis.interpretation) || '暂无可展示解读'}</div>{analysis.policy && <div className="mt-4 border-t border-border pt-3 text-[9px] leading-4 text-text-secondary">边界：{cleanText(analysis.policy)}</div>}</div> : <div className="flex min-h-28 flex-col items-center justify-center gap-2 px-4 text-center text-xs text-text-secondary"><BrainCircuit size={22} className="text-accent/70" /><span>点击“生成解读”，查看当前交易日的市场事实、接力结构和次日观察条件。</span></div>}
    </Panel>
  );
}

function AppLoading({ text }: { text: string }) {
  return <main className="grid min-h-[70vh] place-items-center bg-bg px-4"><div className="flex items-center gap-2 text-sm text-text-secondary"><Loader2 size={18} className="animate-spin text-accent" />{text}</div></main>;
}

export default function ReplayPage() {
  const [dates, setDates] = useState<DateEntry[]>([]);
  const [selectedDate, setSelectedDate] = useState('');
  const [payload, setPayload] = useState<ReplayPayload | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [error, setError] = useState('');

  const loadWorkspace = useCallback(async (date?: string, refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams({ history_days: '40' });
      if (date) params.set('date', date);
      if (refresh) params.set('refresh', 'true');
      const response = await apiFetch<{ code: number; data: ReplayPayload }>(`/replay/workspace?${params.toString()}`, { timeoutMs: 60000, cache: 'no-store' });
      if (!response.data) throw new Error('复盘接口返回为空');
      setPayload(response.data);
      const actual = response.data.trade_date || date || '';
      if (actual) setSelectedDate(actual);
      if (response.data.available_dates?.length) setDates(response.data.available_dates);
      setAnalysis(null);
    } catch (caught) {
      setError(friendlyApiError(caught, '复盘数据暂时无法读取'));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  const loadDates = useCallback(async () => {
    try {
      const response = await apiFetch<{ code: number; data: { dates?: DateEntry[]; latest?: string | null } }>('/replay/dates?limit=240', { timeoutMs: 30000, cache: 'no-store' });
      const next = response.data?.dates || [];
      setDates(next);
      return response.data?.latest || next[0]?.date || '';
    } catch (caught) {
      setError(friendlyApiError(caught, '可用复盘日期读取失败'));
      return '';
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const latest = await loadDates();
      if (!cancelled) await loadWorkspace(latest || undefined);
    })();
    return () => { cancelled = true; };
  }, [loadDates, loadWorkspace]);

  const dateIndex = useMemo(() => dates.findIndex((item) => item.date === selectedDate), [dates, selectedDate]);
  const emotion = payload?.emotion || {};
  const sections = payload?.sections || {};
  const historyRows = payload?.history?.rows || [];

  const chooseDate = (value: string) => {
    if (!value || value === selectedDate) return;
    setSelectedDate(value);
    void loadWorkspace(value);
  };

  const stepDate = (direction: 'older' | 'newer') => {
    if (dateIndex < 0) return;
    const nextIndex = direction === 'older' ? dateIndex + 1 : dateIndex - 1;
    if (nextIndex >= 0 && nextIndex < dates.length) chooseDate(dates[nextIndex].date);
  };

  const runAnalysis = async () => {
    setAnalysisLoading(true);
    setError('');
    try {
      const response = await apiFetch<{ code: number; data: AnalysisResult }>('/replay/analysis', { method: 'POST', body: JSON.stringify({ date: payload?.trade_date || selectedDate || undefined, use_ai: true, history_days: 40 }), timeoutMs: 90000 });
      setAnalysis(response.data);
    } catch (caught) {
      setError(friendlyApiError(caught, 'AI复盘解读暂时无法生成'));
    } finally {
      setAnalysisLoading(false);
    }
  };

  if (loading && !payload) return <AppLoading text="正在读取历史情绪快照与复盘模块" />;

  return (
    <main className="min-h-screen bg-bg text-text">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex max-w-[1580px] flex-wrap items-center justify-between gap-3 px-3 py-4 sm:px-5">
          <div className="min-w-0"><div className="flex items-center gap-2"><History size={20} className="shrink-0 text-accent" /><h1 className="truncate text-lg font-semibold sm:text-xl">情绪复盘工作台</h1><span className="rounded border border-accent/35 px-1.5 py-0.5 font-mono text-[9px] text-accent">REPLAY</span></div><p className="mt-1 max-w-2xl text-[11px] leading-5 text-text-secondary">把每日市场宽度、竞价、涨跌停、连板、板块资金和历史情绪放在同一个交易日截面复盘。</p></div>
          <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
            <label className="flex min-w-0 flex-1 items-center gap-1.5 text-[10px] text-text-secondary sm:flex-none"><CalendarDays size={13} className="shrink-0" /><span className="sr-only">选择交易日</span><select value={selectedDate} onChange={(event) => chooseDate(event.target.value)} className="h-9 min-w-0 flex-1 rounded border border-border bg-bg px-2 text-xs text-text outline-none focus:border-accent sm:w-[150px]"><option value="">选择交易日</option>{dates.map((item) => <option key={item.date} value={item.date}>{item.date} · {item.coverage ?? 0}/4</option>)}</select></label>
            <button type="button" onClick={() => stepDate('older')} disabled={dateIndex < 0 || dateIndex >= dates.length - 1 || loading} className="inline-flex h-9 w-9 items-center justify-center rounded border border-border text-text-secondary hover:border-accent hover:text-accent disabled:opacity-40" title="更早一个交易日" aria-label="更早一个交易日"><ArrowLeft size={14} /></button>
            <button type="button" onClick={() => stepDate('newer')} disabled={dateIndex <= 0 || loading} className="inline-flex h-9 w-9 items-center justify-center rounded border border-border text-text-secondary hover:border-accent hover:text-accent disabled:opacity-40" title="更新一个交易日" aria-label="更新一个交易日"><ArrowRight size={14} /></button>
            <button type="button" onClick={() => void loadWorkspace(payload?.trade_date || selectedDate || undefined, true)} disabled={refreshing} className="inline-flex h-9 items-center gap-1.5 rounded border border-border px-3 text-xs text-text-secondary hover:border-accent hover:text-accent disabled:cursor-wait disabled:opacity-55" title="刷新当前截面"><RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />刷新</button>
          </div>
        </div>
      </header>

      {error && <div className="mx-auto flex max-w-[1580px] items-start gap-2 border-b border-down/30 bg-down/5 px-3 py-2.5 text-xs text-down sm:px-5"><AlertTriangle size={14} className="mt-0.5 shrink-0" /><span className="min-w-0 flex-1">{error}</span><button type="button" onClick={() => setError('')} aria-label="关闭提示"><X size={14} /></button></div>}

      {!payload ? <div className="mx-auto max-w-[1580px] px-3 py-10 text-center text-sm text-text-secondary sm:px-5">当前没有可核验的复盘截面</div> : <div className="mx-auto max-w-[1580px] px-3 py-4 sm:px-5 sm:py-5">
        <SourceNotice payload={payload} />

        <div className="mt-4 grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(360px,.9fr)]">
          <MarketHeadline emotion={emotion} section={sections.market_summary} />
          <AuctionPanel section={sections.auction_limit || sections.auction_grab} />
        </div>

        <div className="mt-4 grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(340px,.8fr)]">
          <LimitDetailPanel sections={sections} emotion={emotion} />
          <StreakLadder section={sections.streak_ladder} />
        </div>

        <div className="mt-4 grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,.75fr)]">
          <TopicPanel sections={sections} />
          <div className="grid min-w-0 gap-4"><EventPanel section={sections.anomaly} title="异动雷达" icon={Activity} /><EventPanel section={sections.radar} title="事件与监管观察" icon={ShieldAlert} /></div>
        </div>

        <div className="mt-4 grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,.65fr)]">
          <Panel title="历史情绪曲线" icon={TrendingUp} subtitle={`${payload.history?.data_start || '--'} 至 ${payload.history?.data_end || '--'} · ${payload.history?.count || 0} 个交易日`} action={<span className="text-[9px] text-text-secondary">透明规则派生分数</span>} id="history"><HistoryChart rows={historyRows} selectedDate={payload.trade_date} /><div className="border-t border-border"><LadderHistory rows={historyRows} /></div></Panel>
          <AnalysisPanel analysis={analysis} loading={analysisLoading} onAnalyze={() => void runAnalysis()} />
        </div>

        <div className="mt-4 grid min-w-0 gap-4 md:grid-cols-3">
          <Panel title="市场接力质量" icon={ArrowUp} subtitle="连板与封板效率"><div className="grid grid-cols-2 gap-px bg-border"><Metric label="一进二成功率" value={percentText(emotion.promotion_rate_1_to_2, 1)} hint={`昨日涨停 ${countText(emotion.yesterday_limit_up_count)}只`} className="text-warn" /><Metric label="连板晋级率" value={percentText(emotion.promotion_rate, 1)} hint={`二板以上 ${percentText(emotion.promotion_rate_2_plus, 1)}`} className="text-warn" /><Metric label="涨停委买家数" value={`${countText(emotion.limit_up_order_count)} 家`} hint={`一字板 ${countText(emotion.one_price_limit_up_count)}只`} /><Metric label="大幅回撤" value={`${countText(emotion.deep_retrace_count)} 只`} hint="情绪负反馈观察" className="text-down" /></div></Panel>
          <Panel title="成交与量能预估" icon={Wallet} subtitle="实际成交额与盘中预测"><div className="grid grid-cols-2 gap-px bg-border"><Metric label="实际成交额" value={moneyText(emotion.market_amount, false)} className="text-text" /><Metric label="预测成交额" value={moneyText(emotion.market_amount_forecast, false)} hint={`预计环比 ${percentText(emotion.market_amount_forecast_change_pct, 1)}`} className="text-text" /><Metric label="预测差值" value={moneyText(emotion.market_amount_forecast_change)} className={tone(emotion.market_amount_forecast_change)} /><Metric label="成交额环比" value={moneyText(emotion.market_amount_change)} className={tone(emotion.market_amount_change)} /></div></Panel>
          <Panel title="数据治理状态" icon={Database} subtitle="来源和存储边界"><div className="space-y-2 px-3 py-3 text-[10px] leading-4 text-text-secondary sm:px-4"><div className="flex justify-between gap-3"><span>规范化版本</span><b className="font-mono text-text">{payload.quality?.version || '--'}</b></div><div className="flex justify-between gap-3"><span>猫爪已配置</span><b className={payload.quality?.provider_configured ? 'text-up' : 'text-warn'}>{payload.quality?.provider_configured ? '是' : '否，使用缓存/CSV'}</b></div><div className="flex justify-between gap-3"><span>原始响应入库</span><b className="text-up">否</b></div><div className="border-t border-border pt-2">{cleanText(payload.quality?.storage_policy || '仅保存有限的日级规范化统计与排行，不保存猫爪原始响应。')}</div></div></Panel>
        </div>

        <footer className="mt-5 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3 text-[9px] leading-4 text-text-secondary"><span>复盘截面：{payload.trade_date || '--'} · 更新 {timeText(payload.updated_at)} · 请求日期 {payload.requested_date || payload.trade_date || '--'}</span><span>{payload.history?.formula_note || '历史分数仅用于结构比较，不代表未来收益。'}</span></footer>
      </div>}
    </main>
  );
}
