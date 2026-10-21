/**
 * 状态的中文标签：契约枚举原样传输，界面只做「翻译」，不新增枚举、不合并语义。
 * skip / unknown / negative / failed / not_run 必须是不同的字，不能都显示成「不开」或「0」。
 */

export interface SeatTriplet { run_state: string; applicability: string; verdict: string }

export function seatStatusLabel(t: SeatTriplet): string {
  if (t.run_state === 'queued') return '排队中';
  if (t.run_state === 'running') return '运行中';
  if (t.run_state === 'failed') return '运行失败（结论未知）';
  if (t.run_state === 'skipped' || t.applicability === 'inapplicable') return '不适用于此任务（未运行）';
  if (t.applicability === 'unknown' || t.verdict === 'unknown') return '待核：输入不足，判定不了';
  switch (t.verdict) {
    case 'conditions_met': return '条件满足';
    case 'conditions_failed': return '条件不满足';
    case 'watch': return '观察：A 级满足，B/C 级待核';
    default: return '待核';
  }
}

export function seatStatusTone(t: SeatTriplet): 'ok' | 'bad' | 'warn' | 'muted' | 'info' {
  if (t.run_state === 'failed') return 'bad';
  if (t.run_state === 'skipped' || t.applicability === 'inapplicable') return 'muted';
  if (t.applicability === 'unknown' || t.verdict === 'unknown') return 'warn';
  if (t.verdict === 'conditions_met') return 'ok';
  if (t.verdict === 'conditions_failed') return 'bad';
  return 'info';
}

export const CONDITION_LABEL: Record<string, string> = {
  met: '满足', failed: '不满足', unknown: '无法判断', inapplicable: '不适用',
};

export const FRESHNESS_LABEL: Record<string, string> = {
  fresh: '此刻最新', stale: '已过期', unknown: '新鲜度未知',
};

export const QUALITY_LABEL: Record<string, string> = {
  complete: '数据完整', partial: '部分缺失', unavailable: '不可用',
};

export const SOURCE_MODE_LABEL: Record<string, string> = {
  provider_api: '供应商接口', file_import: '只读归档', user_manual: '用户手工', fixture: '合成测试数据', mixed: '混合来源',
};

export const BAR_STATUS_LABEL: Record<string, string> = {
  final: '日线已收定', partial: '盘中未收定', unknown: '终结状态未确认',
};

export const ELIGIBILITY_LABEL: Record<string, string> = {
  eligible: '可用于模拟执行', ineligible: '不可执行', unknown: '执行资格未知（不能据此开仓）',
};

export const PLAN_STATUS_LABEL: Record<string, string> = { draft: '草稿', frozen: '已冻结' };

export const PURPOSE_LABEL: Record<string, string> = {
  research: '研究（仅观察）', simulation: '模拟执行', user_trade_record: '手工记录（未核验）',
};

export const JOB_STATE_LABEL: Record<string, string> = {
  queued: '排队中', running: '运行中', succeeded: '已完成', partial: '部分完成', failed: '失败', cancelled: '已取消',
};

export const ROLE_LABEL: Record<string, string> = {
  primary: '主方法', validator: '反证', discipline: '纪律', context: '环境背景',
};

export const ORIGIN_LABEL: Record<string, string> = {
  author_quote: '原书原文', editorial_summary: '编者提炼', engineering_policy: '系统工程规则', user_rule: '我的规则',
};

export const EVENT_LABEL: Record<string, string> = {
  opening_balance: '期初资金', cash_adjustment: '现金调整', paper_fill: '模拟成交', manual_trade_record: '手工成交记录',
  watch_added: '加入观察', watch_removed: '移出观察', note: '日记', correction: '更正',
};

export const LIFECYCLE_LABEL: Record<string, string> = {
  activated: '开始观察', trigger_observed: '观察到触发证据', cancelled: '已取消', expired: '已过期',
  execution_unfilled: '未成交', execution_filled: '已成交',
};

const MISSING: Record<string, string> = {
  trading_calendar: '覆盖今天的交易日历', market_snapshot: '行情快照', index_daily: '指数日线', limit_events: '涨停异动事件',
  price_limit_vendor: '供应商涨跌停价（已按规则推导代替）', st_status: '当日 ST 名单', stockbasic: '证券基础信息（板块按代码段判断）',
  suspend_status: '停复牌名单', valuation: '估值数据', final_daily_bars: '收定的日线', daily_bars: '当日日线', amount: '部分股票的成交额',
  volume: '部分股票的成交量', hourly_bars: '小时线', minute_bars: '分钟线', level2: 'Level2 / 逐笔', industry_map: '行业分类',
  limit_pool_session: '当日涨停池', dividend_history: '历史分红', decision_plan: '事前计划',
};

const FRESH_REASON: [string, string][] = [
  ['calendar_does_not_cover_today', '交易日历不覆盖今天'], ['calendar_unavailable', '交易日历不可用'],
  ['quote_date_unknown', '报价日未知'], ['expected_session_missing', '已有更新的交易日'], ['intraday_partial', '盘中，日线未收定'],
  ['latest_expected_completed_session', '是最近一个完整交易日'],
];

/** 新鲜度 + 一句原因（原因来自服务端 freshness_reason 的前缀，不在前端推断）。 */
export function freshnessText(f: string | undefined, reason: string | undefined): string {
  const base = label(FRESHNESS_LABEL, f);
  const r = FRESH_REASON.find(([k]) => (reason ?? '').startsWith(k))?.[1];
  return r ? `${base}：${r}` : base;
}

/** 缺失项的中文说明：snapshot:xxx / freshness:… / invalid_rows:N 等前缀都认得。 */
export function missingText(k: string): string {
  const key = k.replace(/^snapshot:/, '');
  if (key.startsWith('invalid_rows:')) return `${key.split(':')[1]} 行非法数据已拒收`;
  if (key.startsWith('freshness:')) return `新鲜度：${key.slice('freshness:'.length)}`;
  if (key.startsWith('quote:')) return `${key.slice(6)} 没有报价`;
  const base = key.split('(')[0];
  return MISSING[base] ? `${MISSING[base]}${key.includes('(') ? key.slice(key.indexOf('(')) : ''}` : key;
}

export function label(map: Record<string, string>, v: string | null | undefined): string {
  if (v === null || v === undefined) return '未知';
  return map[v] ?? v;
}
