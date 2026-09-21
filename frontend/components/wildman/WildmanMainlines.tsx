import { ArrowRight, CheckCircle2, CircleDashed, CircleX, ExternalLink, Radar } from 'lucide-react';
import StockKlineButton from '@/components/StockKlineButton';

type AnyMap = Record<string, any>;

const FIVE_STEPS = [
  { key: 'message_level', label: '消息级别', ruleId: 'WM_MAINLINE_STEP1', aliases: ['消息级别', '政策催化', '政策/消息催化', '消息催化'] },
  { key: 'first_day_move', label: '首日异动', ruleId: 'WM_MAINLINE_STEP2', aliases: ['首日异动', '首日爆发', '首板异动', '涨停潮'] },
  { key: 'capacity_midcap', label: '容量中军', ruleId: 'WM_MAINLINE_STEP3', aliases: ['容量中军', '核心中军', '中军'] },
  { key: 'old_leader_move', label: '老龙异动', ruleId: 'WM_MAINLINE_STEP4', aliases: ['老龙异动', '题材老龙', '老龙'] },
  { key: 'next_day_premium', label: '次日溢价验证', ruleId: 'WM_MAINLINE_STEP5', aliases: ['次日溢价验证', '次日溢价', '龙头溢价'] },
] as const;

const KEY_LABELS: Record<string, string> = {
  policy_catalyst: '政策催化', policy_level: '消息级别', news_level: '消息级别', catalyst: '催化因素',
  first_day_move: '首日异动', first_day_burst: '首日爆发', burst: '爆发强度', first_limit: '首板',
  limit_up_count: '涨停数量', max_limit_height: '空间高度', has_pioneer: '先锋', has_core_midcap: '核心中军',
  capacity_midcap: '容量中军', core_midcap: '核心中军', core_midcap_stable: '中军稳定', market_cap: '市值', amount: '成交额',
  height: '连板高度', pioneer: '先锋', midcap: '中军', early_theme_start: '题材早期启动', first_limit_pioneer: '首板先锋',
  supplement_started_after_leader: '龙头确立后启动', stock_height: '个股连板', leader_height: '龙头连板', ratio: '相对比例',
  first_divergence_support: '首次分歧承接', theme_linkage: '题材联动', independent_theme_leadership: '独立领涨',
  old_dragon_active: '老龙异动', old_leader_move: '老龙异动', old_dragon: '老龙', leader_premium: '龙头溢价',
  next_day_premium: '次日溢价', support_promotion: '助攻晋级', validation_date: '验证日期', trigger_date: '触发日期',
  fund_return: '资金回流', reversal: '反包', supplement_started: '补涨启动', resists_market_drop: '相对抗跌',
  source: '来源', provider: '数据源', published_at: '发布日期', actual: '实际', expected: '要求', reason: '原因',
  date: '日期', value: '数值', status: '状态',
};

const DISPLAY_ALIAS_GROUPS = [
  ['代码', 'symbol', 'code', 'stock_code', 'stockCode'],
  ['名称', 'name', 'stock_name', 'stockName'],
  ['交易日', 'trade_date'],
  ['验证日', 'validation_date'],
  ['市值', 'market_cap', 'market_cap_yuan'],
  ['成交额', 'turnover', 'amount'],
  ['成交量', 'volume'],
  ['涨跌幅', 'change_pct'],
  ['开盘', 'open'],
  ['前收', 'prev_close'],
  ['收盘', 'close'],
] as const;

function safe(value: unknown, fallback = '暂无数据'): string {
  const text = String(value ?? '').trim();
  return text || fallback;
}

function isObject(value: unknown): value is AnyMap {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function keyLabel(key: string): string {
  if (/[一-鿿]/.test(key)) return key;
  if (KEY_LABELS[key]) return KEY_LABELS[key];
  const spaced = key.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ').trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : '字段';
}

function formatNumber(value: number): string {
  const absolute = Math.abs(value);
  if (absolute >= 100000000) return `${(value / 100000000).toFixed(2).replace(/\.00$/, '')}亿`;
  if (absolute >= 10000) return `${(value / 10000).toFixed(2).replace(/\.00$/, '')}万`;
  if (Number.isInteger(value)) return String(value);
  const decimals = absolute < 1 ? 3 : 2;
  return value.toFixed(decimals).replace(/0+$/, '').replace(/\.$/, '');
}

function isDuplicateAlias(key: string, value: unknown, entries: Array<[string, unknown]>): boolean {
  const group = DISPLAY_ALIAS_GROUPS.find((aliases) => aliases.includes(key as never));
  if (!group || group[0] === key) return false;
  const chineseAlias = group[0];
  return entries.some(([entryKey, entryValue]) => entryKey === chineseAlias && String(entryValue) === String(value));
}

function readable(value: unknown, fallback = '暂无可核验数据', depth = 0): string {
  if (value === true) return '是';
  if (value === false) return '否';
  if (value === null || value === undefined || String(value).trim() === '') return fallback;
  if (typeof value === 'number') return Number.isFinite(value) ? formatNumber(value) : fallback;
  if (depth > 2) return String(value);
  if (Array.isArray(value)) return value.length ? value.map((item) => readable(item, fallback, depth + 1)).join('、') : fallback;
  if (isObject(value)) {
    const entries = Object.entries(value).filter(([key, nested]) => !['sources', 'source', 'reason', 'reasons'].includes(key) && !isDuplicateAlias(key, nested, Object.entries(value)));
    return entries.length ? entries.map(([key, nested]) => `${keyLabel(key)}：${readable(nested, fallback, depth + 1)}`).join('；') : fallback;
  }
  return String(value);
}

function passedValue(value: unknown): boolean | null {
  if (value === true || value === 'true' || value === 1) return true;
  if (value === false || value === 'false' || value === 0) return false;
  return null;
}

function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== 'string' || !/^https?:\/\/[^\s]+$/i.test(value)) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null;
  } catch {
    return null;
  }
}

function sourceLabel(value: unknown): string {
  if (isObject(value)) return sourceLabel(value.source || value.provider || value.name || value.title);
  const text = String(value || '').trim();
  if (!text) return '来源未返回';
  const labels: Record<string, string> = {
    unavailable: '数据源当前不可用', unknown: '数据源未说明', cache: '缓存数据', database_cache: '数据库缓存',
    numcat: '猫爪 NumCat', numcat_daily: '猫爪·日线行情', numcat_tick: '猫爪·实时快照',
  };
  return labels[text.toLowerCase()] || text;
}

function boardIndexText(value: unknown): string {
  if (!isObject(value)) return '暂无板块指数';
  const name = value.name || value['名称'] || value.label;
  const code = value.code || value['代码'] || value.symbol;
  const source = value.source ? ` · ${sourceLabel(value.source)}` : '';
  const label = [name, code].filter(Boolean).map(String).join(' · ');
  return label ? `${label}${source}` : '暂无板块指数';
}

function sourceItems(step: AnyMap, row: AnyMap): AnyMap[] {
  const sources = Array.isArray(step.sources) && step.sources.length ? step.sources : Array.isArray(row.sources) ? row.sources : [];
  return sources.filter(isObject);
}

function SourceLinks({ step, row }: { step: AnyMap; row: AnyMap }) {
  const items = sourceItems(step, row);
  if (!items.length) return <span>未取得可核验的数据来源</span>;
  return <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
    {items.map((item, index) => {
      const url = safeHttpUrl(item.url);
      const title = safe(item.title || item.source, '原始来源');
      const meta = [item.source, item.published_at].filter(Boolean).map(sourceLabel).join(' · ');
      return url
        ? <a key={`${url}-${index}`} href={url} target="_blank" rel="noreferrer" className="inline-flex max-w-full items-center gap-1 text-accent hover:text-text" title={url}><span className="truncate">{title}</span><ExternalLink size={11} className="shrink-0" /></a>
        : <span key={`${title}-${index}`} className="max-w-full break-words">{title}{meta ? ` · ${meta}` : ' · 链接不可用'}</span>;
    })}
  </span>;
}

function matchesStep(item: AnyMap, index: number): boolean {
  const identity = [item.key, item.step, item.step_key, item.step_name, item.rule_id, item.rule_name, item.name].filter(Boolean).join(' ');
  return String(item.rule_id || '').toUpperCase() === FIVE_STEPS[index].ruleId
    || FIVE_STEPS[index].aliases.some((alias) => identity.includes(alias))
    || new RegExp(`(?:step|步骤)[_-]?${index + 1}$`, 'i').test(String(item.key || item.step || item.step_key || ''));
}

function asStepItem(value: unknown): AnyMap {
  if (isObject(value)) {
    const nested = isObject(value.evidence) ? value.evidence : Array.isArray(value.evidence) && isObject(value.evidence[0]) ? value.evidence[0] : {};
    return { ...nested, ...value };
  }
  return { passed: value, actual: value };
}

function normalizeSteps(row: AnyMap): { steps: AnyMap[]; modern: boolean } {
  const raw = row.mainline_five_steps;
  const modernEvidence = Array.isArray(row.evidence) && row.evidence.some((item: unknown) => isObject(item) && (Array.isArray(item.sources) || item.step_key || item.step_name));
  const modern = Array.isArray(raw) || isObject(raw) || Array.isArray(row.sources) || modernEvidence || row.mainline_version || row.mainline_contract_version;
  const values = Array.isArray(raw)
    ? raw
    : isObject(raw)
      ? Object.entries(raw.steps || raw).map(([key, value]) => isObject(value) ? { key, ...value } : { key, passed: value, actual: value })
      : modern && Array.isArray(row.evidence) ? row.evidence : [];
  const evidence = Array.isArray(row.evidence) ? row.evidence.filter(isObject) : [];
  return { modern: Boolean(modern), steps: FIVE_STEPS.map((definition, index) => {
    const item = values.find((candidate: unknown) => isObject(candidate) && matchesStep(candidate, index)) || values[index];
    const evidenceItem = evidence.find((candidate) => matchesStep(candidate, index));
    if (!item && !evidenceItem) return {};
    const rawItem = isObject(item) ? item : item === undefined ? {} : { passed: item, actual: item };
    return asStepItem({
      ...(evidenceItem || {}),
      ...rawItem,
      rule_id: rawItem.rule_id ?? evidenceItem?.rule_id ?? definition.ruleId,
      rule_name: rawItem.rule_name ?? evidenceItem?.rule_name ?? definition.label,
      expected: rawItem.expected ?? evidenceItem?.expected,
      required: rawItem.required ?? evidenceItem?.required,
      sources: Array.isArray(rawItem.sources) && rawItem.sources.length ? rawItem.sources : evidenceItem?.sources,
      reason: rawItem.reason ?? rawItem.reasons ?? evidenceItem?.reason ?? evidenceItem?.reasons,
    });
  }) };
}

function statusFor(passed: boolean | null): { label: string; className: string; barClassName: string; Icon: typeof CheckCircle2 } {
  if (passed === true) return { label: '已通过', className: 'text-up', barClassName: 'bg-up', Icon: CheckCircle2 };
  if (passed === false) return { label: '未通过', className: 'text-down', barClassName: 'bg-down', Icon: CircleX };
  return { label: '待核验', className: 'text-warn', barClassName: 'bg-warn', Icon: CircleDashed };
}

function listValues(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => isObject(item) ? readable(item.name || item.symbol || item.title, '未命名') : readable(item)).filter(Boolean);
}

function stockAlias(item: unknown, poolRole: string): AnyMap {
  if (!isObject(item)) return { name: item, pool_role: poolRole };
  return {
    ...item,
    symbol: item.symbol || item.code || item['代码'] || item.stock_code || item.stockCode,
    name: item.name || item['名称'] || item.stock_name || item.stockName,
    pool_role: poolRole,
  };
}

function stockPool(row: AnyMap): AnyMap[] {
  const seen = new Set<string>();
  const pools: AnyMap[] = [
    ...(Array.isArray(row.core_stocks) ? row.core_stocks.map((item: unknown) => stockAlias(item, '核心中军')) : []),
    ...(Array.isArray(row.leader_stocks) ? row.leader_stocks.map((item: unknown) => stockAlias(item, '龙头/老龙')) : []),
  ];
  return pools.filter((item) => {
    const identity = String(item.symbol || item.code || item.name || '');
    if (!identity || seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

function formatThresholds(thresholds: AnyMap): string[] {
  if (!isObject(thresholds)) return [];
  const lines: string[] = [];
  const groups = Array.isArray(thresholds.step1_message_groups) ? thresholds.step1_message_groups.join('、') : '';
  if (groups) lines.push(`消息级别：${groups}`);
  const firstBoardWindow = thresholds.step2_firstboard_window;
  const firstBoardCount = thresholds.step2_firstboard_count;
  const lookback = thresholds.step2_board_breakout_lookback;
  const volumeMultiple = thresholds.step2_volume_multiple;
  if (firstBoardWindow || firstBoardCount || lookback || volumeMultiple) {
    const details = [
      firstBoardCount !== undefined ? `首板数量≥${firstBoardCount}只` : '',
      firstBoardWindow !== undefined ? `时间窗${firstBoardWindow}` : '',
      lookback !== undefined ? `板指突破前${lookback}日高点` : '',
      volumeMultiple !== undefined ? `量≥前5日均量${volumeMultiple}倍` : '',
    ].filter(Boolean);
    lines.push(`首日异动：${details.join('；')}`);
  }
  const marketCap = Number(thresholds.step3_market_cap_yuan);
  const coreVolumeMultiple = thresholds.step3_volume_multiple ?? thresholds.step3_volume_ratio;
  if (Number.isFinite(marketCap) || coreVolumeMultiple !== undefined) {
    const capText = Number.isFinite(marketCap) ? `市值≥${(marketCap / 100000000).toFixed(0)}亿元` : '市值门槛未提供';
    const trendText = '趋势：收盘>MA20；替代强势口径：涨跌幅≥5%且强阳线';
    const volumeText = coreVolumeMultiple !== undefined ? `量≥前5日均量${coreVolumeMultiple}倍` : '';
    lines.push(`容量中军：${[capText, trendText, volumeText].filter(Boolean).join('；')}`);
  }
  const priorBoards = thresholds.step4_prior_wave_boards;
  const interruptedDays = thresholds.step4_interrupted_observed_days;
  const restartSessions = thresholds.step4_restart_sessions;
  const lowPositionMultiple = thresholds.step4_low_position_multiple;
  const oldVolumeMultiple = thresholds.step4_volume_multiple ?? thresholds.step4_volume_ratio;
  if ([priorBoards, interruptedDays, restartSessions, lowPositionMultiple, oldVolumeMultiple].some((value) => value !== undefined)) {
    const details = [
      priorBoards !== undefined ? `前一轮≥${priorBoards}板` : '',
      interruptedDays !== undefined ? `中断观测≥${interruptedDays}天` : '',
      restartSessions !== undefined ? `重启窗口：触发日前${restartSessions}个交易日至当天` : '',
      lowPositionMultiple !== undefined ? `低位价格≤前20日最低×${lowPositionMultiple}` : '',
      oldVolumeMultiple !== undefined ? `量≥前5日均量${oldVolumeMultiple}倍` : '',
    ].filter(Boolean);
    lines.push(`老龙异动：${details.join('；')}`);
  }
  const premium = thresholds.step5_leader_open_premium_pct ?? thresholds.step5_leader_premium_pct;
  const survival = thresholds.step5_firstboard_survival ?? thresholds.step5_survival_rate;
  if (premium !== undefined || survival !== undefined) {
    const survivalText = Number.isFinite(Number(survival)) ? `${Number(survival) * 100}%` : safe(survival, '未提供');
    lines.push(`次日溢价验证：龙头开盘溢价≥${premium !== undefined ? premium : '未提供'}%；昨日首板存活率≥${survivalText}`);
  }
  return lines;
}

function progressCount(row: AnyMap, steps: AnyMap[]): number {
  const rawCount = row.passed_count;
  if (rawCount !== null && rawCount !== undefined && String(rawCount).trim() !== '' && Number.isFinite(Number(rawCount))) return Math.max(0, Math.min(5, Number(rawCount)));
  return steps.reduce((count, step) => count + (passedValue(step.passed) === true ? 1 : 0), 0);
}

function MainlineCard({ row }: { row: AnyMap }) {
  const { steps, modern } = normalizeSteps(row);
  const passedCount = progressCount(row, steps);
  const confirmed = row.confirmed === true || (row.confirmed === undefined && row.state === '核心主线确认');
  const failed = listValues(row.failed);
  const missing = listValues(row.missing);
  const failedText = failed.length ? failed.join('、') : '';
  const missingText = missing.length ? missing.join('、') : '';
  const firstPending = steps.findIndex((step) => passedValue(step.passed) === null);
  const nextStep = safe(row.next_step, failed.length ? `修正并复核：${failedText}` : missing.length ? `补齐证据：${missingText}` : firstPending >= 0 ? `核验${FIVE_STEPS[firstPending].label}` : '等待下一交易日验证');
  const themeName = safe(row.theme_name || row.industry_name || row.topic_name || row.name, '未命名主线候选');
  const stocks = stockPool(row);
  const rawState = safe(row.state, '待确认');
  const state = confirmed ? '核心主线确认' : rawState === '核心主线确认' ? '主线候选' : rawState;
  const boardIndex = isObject(row.board_index) ? row.board_index : null;
  const thresholdLines = formatThresholds(row.thresholds);
  const thresholdDisclosure = safe(row.thresholds?.disclosure, '以上为工程判定口径，用于可复核筛选，不声称是原文逐字阈值。');
  return <article className="min-w-0 overflow-hidden rounded-md border border-border bg-card">
    <header className="border-b border-border px-3 py-3 sm:px-4">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 items-start gap-2"><Radar size={15} className="mt-0.5 shrink-0 text-accent" /><div className="min-w-0"><h3 className="break-words text-sm font-semibold text-text">{themeName}</h3></div></div>
        <span className={`shrink-0 rounded border px-2 py-1 text-xs ${confirmed ? 'border-up/35 bg-up/10 text-up' : 'border-warn/35 bg-warn/10 text-warn'}`}>{state}</span>
      </div>
      <div className="mt-3 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-secondary">
        <span className="font-mono text-text">五步 {passedCount}/5</span>
        <span>触发日：{safe(row.trigger_date, '待确认')}</span>
        <span>验证日：{safe(row.validation_date, '待确认')}</span>
        <span>板块指数：{boardIndexText(boardIndex)}</span>
      </div>
      <div className="mt-2 grid grid-cols-5 gap-1" aria-label={`五步进度 ${passedCount}/5`}>
        {steps.map((step, index) => { const status = statusFor(passedValue(step.passed)); return <div key={FIVE_STEPS[index].key} className={`h-1 rounded-full ${status.barClassName}`} title={`${index + 1} ${FIVE_STEPS[index].label} · ${status.label}`} />; })}
      </div>
    </header>

    <div className="p-3 sm:p-4">
      {!modern && <div className="mb-3 border border-warn/30 bg-warn/5 px-2.5 py-2 text-xs leading-5 text-warn">当前返回仍是旧版主线缓存，未沿用旧规则解释；刷新后读取新版五步证据。</div>}
      <div className="divide-y divide-border border-y border-border">
        {FIVE_STEPS.map((definition, index) => {
          const step = steps[index];
          const passed = passedValue(step.passed);
          const status = statusFor(passed);
          const Icon = status.Icon;
          const expected = step.expected ?? step.required;
          const actual = step.actual ?? step.value;
          const reason = step.reason || (passed === null ? (modern ? '证据不完整，暂不能核验' : '旧版缓存未纳入新版核验') : '');
          return <div key={definition.key} className="grid min-w-0 gap-2 py-3 sm:grid-cols-[150px_minmax(0,1fr)_auto] sm:items-start">
            <div className="flex min-w-0 items-start gap-2"><span className="grid h-5 w-5 shrink-0 place-items-center rounded-full border border-border font-mono text-[10px] text-text-secondary">{index + 1}</span><div className="min-w-0"><div className="break-words text-xs font-medium text-text">{definition.label}</div><div className="mt-0.5 break-words text-[12px] leading-5 text-text-secondary">要求：{readable(expected, '本步骤口径未提供')}</div></div></div>
            <div className="min-w-0 break-words text-[12px] leading-5 text-text-secondary"><div><span className="text-text-secondary">实际：</span><span className="text-text">{readable(actual)}</span></div>{reason && <div className="mt-0.5 text-warn">{readable(reason)}</div>}<div className="mt-1 flex min-w-0 items-start gap-1 text-[12px] leading-5"><span className="shrink-0">来源：</span><SourceLinks step={step} row={row} /></div></div>
            <div className={`flex items-center gap-1 text-xs ${status.className}`}><Icon size={13} className="shrink-0" />{status.label}</div>
          </div>;
        })}
      </div>

      <div className="mt-3 grid min-w-0 gap-2 text-xs leading-5 sm:grid-cols-2">
        <div className="min-w-0"><span className="text-text-secondary">下一步核验：</span><span className="break-words text-text">{nextStep}</span></div>
        <div className="min-w-0"><span className="text-text-secondary">资格说明：</span><span className="break-words text-text-secondary">{safe(row.qualification_note, confirmed ? '五步证据已满足，仍以实际交易日和来源为准。' : '当前仅为主线候选，五步证据尚未全部满足。')}</span></div>
      </div>
      {(failedText || missingText) && <div className="mt-2 flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-xs leading-5"><span className="text-down">未通过项：{failedText || '无'}</span><span className="text-warn">待补证：{missingText || '无'}</span></div>}

      <details className="mt-3 border-t border-border pt-3 text-xs leading-5">
        <summary className="cursor-pointer text-accent">量化口径补充</summary>
        {thresholdLines.length ? <div className="mt-2 space-y-1 text-text-secondary">{thresholdLines.map((line) => <div key={line}>{line}</div>)}</div> : <div className="mt-2 text-text-secondary">行级量化口径未提供，不能用通用规则文本代替。</div>}
        <div className="mt-2 border-t border-border pt-2 text-text-secondary">{thresholdDisclosure}</div>
      </details>

      <div className="mt-3 border-t border-border pt-3">
        <div className="flex items-center justify-between gap-2 text-xs text-text-secondary"><span className="font-medium text-text">候选股池</span><span>{stocks.length ? `${stocks.length}只` : '待返回'}</span></div>
        {stocks.length ? <div className="mt-2 divide-y divide-border border-y border-border">{stocks.map((stock, index) => { const code = String(stock.symbol || stock.code || '').trim(); const name = safe(stock.name, code); return <div key={`${code || name}-${index}`} className="flex min-w-0 flex-wrap items-center justify-between gap-2 py-2.5 text-xs"><div className="flex min-w-0 items-center gap-2"><span className="shrink-0 rounded border border-border px-1.5 py-0.5 text-xs text-text-secondary">{safe(stock.pool_role)}</span>{code ? <StockKlineButton code={code} name={name} className="min-w-0 truncate text-text hover:text-accent" title="查看K线"><span className="truncate">{name}</span><span className="ml-2 font-mono text-text-secondary">{code}</span></StockKlineButton> : <span className="truncate text-text">{name}</span>}</div><span className="shrink-0 text-text-secondary">{stock.role || stock.position || (stock.change_pct !== undefined && stock.change_pct !== null ? `涨跌 ${readable(stock.change_pct)}` : '待核验')}</span></div>; })}</div> : <div className="mt-2 border-y border-border py-2 text-xs text-text-secondary">核心与龙头候选尚未返回，不能用空列表替代确认。</div>}
      </div>
    </div>
  </article>;
}

export default function WildmanMainlines({ rows }: { rows: AnyMap[] }) {
  const mainlines = Array.isArray(rows) ? rows : [];
  return <div className="space-y-4">
    <div className="border-l-2 border-accent bg-card px-3 py-2.5 text-xs leading-5 text-text-secondary"><div className="flex items-start gap-2"><ArrowRight size={14} className="mt-1 shrink-0 text-accent" /><span><b className="font-medium text-text">一个行业/板块/题材候选池 · 严格五步核验：</b>政策/消息催化 → 首日异动 → 容量中军 → 老龙异动 → 次日溢价验证。五步未齐不称为核心主线。</span></div></div>
    {mainlines.length ? mainlines.map((row, index) => <MainlineCard key={row.theme_id || row.theme_name || index} row={row} />) : <div className="border border-border bg-card px-3 py-8 text-center text-xs text-text-secondary">当前没有可核验的行业/板块/题材候选。</div>}
  </div>;
}
