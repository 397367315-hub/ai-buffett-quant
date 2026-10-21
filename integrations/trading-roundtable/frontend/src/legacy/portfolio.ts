/**
 * 模拟交易引擎 —— A 股规则 + 真实费率。
 *
 * 这是研究用的模拟盘：不接券商、不下单、不构成投资建议。
 * 价格取面板最近一次快照（refresh.py 每 5 分钟刷新一次），不是逐笔行情。
 *
 * 规则（按 2026 年 A 股现行口径）：
 *   佣金    成交额 × 0.025%，单笔最低 5 元，买卖都收
 *   印花税  成交额 × 0.05%，只在卖出时收（2023-08-28 起由千分之一减半）
 *   过户费  成交额 × 0.001%，买卖都收
 *   T+1     当日买入的股份当日不能卖
 *   手数    买入必须是 100 股的整数倍；卖出后剩余不足 100 股的部分必须一次卖掉
 *   涨跌停  主板 ±10%、创业板 ±20%、ST ±5%，按昨收计算
 */

export const FEES = {
  commissionRate: 0.00025,
  commissionMin: 5,
  stampRate: 0.0005,
  transferRate: 0.00001,
};

export const INITIAL_CASH = 1_000_000;
export const STORAGE_KEY = 'agi-quant-portfolio-v1';

import type { PoolKey, QuantPageData } from './types';

export type Side = 'buy' | 'sell';

export interface FeeBreakdown {
  commission: number;
  stamp: number;
  transfer: number;
  total: number;
}

export interface Trade {
  id: string;
  ts: string;
  code: string;
  name: string;
  side: Side;
  shares: number;
  price: number;
  amount: number;
  fee: FeeBreakdown;
  net: number;
  note?: string;
}

export interface WatchItem {
  code: string;
  name: string;
  pool: PoolKey;
  poolLabel: string;
  seat: string;
  addedAt: string;
}

export interface Ledger {
  version: 1;
  cash: number;
  initial: number;
  watch: WatchItem[];
  trades: Trade[];
}

export interface Position {
  code: string;
  name: string;
  shares: number;
  available: number;
  avgCost: number;
  costBase: number;
  last: number | null;
  prevClose: number | null;
  /** 没有报价时为 null —— 不拿成本冒充市值，也不补 0 */
  marketValue: number | null;
  pnl: number | null;
  pnlPct: number | null;
  todayPnl: number;
}

export const r2 = (v: number) => Math.round(v * 100) / 100;
export const money = (v: number) => new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);

export function newLedger(): Ledger {
  return { version: 1, cash: INITIAL_CASH, initial: INITIAL_CASH, watch: [], trades: [] };
}

export function calcFee(side: Side, amount: number): FeeBreakdown {
  if (!(amount > 0)) return { commission: 0, stamp: 0, transfer: 0, total: 0 };
  const commission = r2(Math.max(amount * FEES.commissionRate, FEES.commissionMin));
  const stamp = r2(side === 'sell' ? amount * FEES.stampRate : 0);
  const transfer = r2(amount * FEES.transferRate);
  return { commission, stamp, transfer, total: r2(commission + stamp + transfer) };
}

/** 涨跌停幅度：创业板/科创板 20%，ST 5%，其余主板 10%。 */
export function limitPct(code: string, name: string): number {
  if (/^(300|301|688|689)/.test(code)) return 0.2;
  if (/^(8|4|9)/.test(code)) return 0.3;
  if (/ST/i.test(name)) return 0.05;
  return 0.1;
}

export function limitPrices(prevClose: number, code: string, name: string) {
  const p = limitPct(code, name);
  return { up: r2(prevClose * (1 + p)), down: r2(prevClose * (1 - p)) };
}

/** 上海时区的日期，用来判 T+1。 */
export function shanghaiDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(d);
}

export function today(): string {
  return shanghaiDate(new Date().toISOString());
}

/** 当前是否在连续竞价时段（不含节假日判断）。 */
export function inTradingHours(now = new Date()): boolean {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', weekday: 'short', hour12: false,
  }).formatToParts(now);
  const get = (t: string) => parts.find(p => p.type === t)?.value ?? '';
  const wd = get('weekday');
  if (wd === 'Sat' || wd === 'Sun') return false;
  const mins = Number(get('hour')) * 60 + Number(get('minute'));
  return (mins >= 570 && mins <= 690) || (mins >= 780 && mins <= 900);
}

/** 取价：优先全市场快照，其次候选表的最后一根日线，都没有就返回 null。 */
export function makeQuoteLookup(data: QuantPageData) {
  return (code: string) => {
    const q = data.quotes?.[code];
    if (q) return { last: q[1], prevClose: q[2], name: q[0] };
    const c = data.candidates?.find(x => x.code === code);
    if (c && c.kline.length) {
      return {
        last: c.kline[c.kline.length - 1].c,
        prevClose: c.kline.length > 1 ? c.kline[c.kline.length - 2].c : null,
        name: c.name,
      };
    }
    return { last: null, prevClose: null, name: undefined as string | undefined };
  };
}

/** 按平均成本法从成交流水还原持仓。 */
export function positionsOf(ledger: Ledger, priceOf: (code: string) => { last: number | null; prevClose: number | null; name?: string }): Position[] {
  const day = today();
  const acc = new Map<string, { name: string; shares: number; costBase: number; todayBought: number; todaySold: number }>();
  for (const t of [...ledger.trades].sort((a, b) => a.ts.localeCompare(b.ts))) {
    const cur = acc.get(t.code) ?? { name: t.name, shares: 0, costBase: 0, todayBought: 0, todaySold: 0 };
    cur.name = t.name || cur.name;
    if (t.side === 'buy') {
      cur.shares += t.shares;
      cur.costBase += t.amount + t.fee.total;
      if (shanghaiDate(t.ts) === day) cur.todayBought += t.shares;
    } else {
      const avg = cur.shares > 0 ? cur.costBase / cur.shares : t.price;
      cur.costBase = Math.max(0, cur.costBase - avg * t.shares);
      cur.shares = Math.max(0, cur.shares - t.shares);
      if (shanghaiDate(t.ts) === day) cur.todaySold += t.shares;
    }
    acc.set(t.code, cur);
  }
  const out: Position[] = [];
  for (const [code, a] of acc) {
    if (a.shares <= 0) continue;
    const q = priceOf(code);
    const last = q.last;
    const avgCost = a.costBase / a.shares;
    // DATA-003：没有报价就是未知。成本不是市值，0 也不是。
    const marketValue = last != null ? last * a.shares : null;
    const pnl = last != null ? marketValue! - a.costBase : null;
    out.push({
      code, name: a.name, shares: a.shares,
      available: Math.max(0, a.shares - a.todayBought),
      avgCost, costBase: r2(a.costBase), last,
      prevClose: q.prevClose, marketValue: marketValue == null ? null : r2(marketValue),
      pnl: pnl == null ? null : r2(pnl),
      pnlPct: pnl != null && a.costBase > 0 ? r2(pnl / a.costBase * 100) : null,
      todayPnl: r2(last != null && q.prevClose != null ? (last - q.prevClose) * a.shares : 0),
    });
  }
  return out.sort((a, b) => (b.marketValue ?? -1) - (a.marketValue ?? -1));
}

export interface OrderRequest {
  code: string; name: string; side: Side; shares: number; price: number; note?: string;
}

/** 下单前校验。返回中文错误信息，能下单返回 null。 */
export function checkOrder(ledger: Ledger, req: OrderRequest, pos: Position | undefined): string | null {
  const { side, shares, price } = req;
  if (!Number.isFinite(shares) || shares <= 0) return '股数要大于 0';
  if (!Number.isFinite(price) || price <= 0) return '没有可用的成交价';
  if (side === 'buy') {
    if (shares % 100 !== 0) return '买入必须是 100 股（1 手）的整数倍';
    const amount = shares * price;
    const total = amount + calcFee('buy', amount).total;
    if (total > ledger.cash + 1e-6) return `可用资金不足：需要 ${money(total)} 元`;
    return null;
  }
  if (!pos || pos.shares <= 0) return '没有持仓，卖不了';
  if (shares > pos.available) {
    return pos.available === 0
      ? 'T+1：今天买的股份今天不能卖，明天才能卖'
      : `最多可卖 ${pos.available} 股（今天还有 ${pos.shares - pos.available} 股是 T+1 锁定的）`;
  }
  const left = pos.shares - shares;
  if (left > 0 && left < 100) return `卖出后剩下 ${left} 股零股，A 股要求零股一次性卖掉`;
  return null;
}

/** 执行下单，返回新账本与这笔成交。不合法时返回 error，账本原样不动。 */
export function applyOrder(ledger: Ledger, req: OrderRequest, pos: Position | undefined): { ledger: Ledger; trade?: Trade; error?: string } {
  const error = checkOrder(ledger, req, pos);
  if (error) return { ledger, error };
  const amount = r2(req.shares * req.price);
  const fee = calcFee(req.side, amount);
  const net = req.side === 'buy' ? r2(-(amount + fee.total)) : r2(amount - fee.total);
  const trade: Trade = {
    id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
    ts: new Date().toISOString(),
    code: req.code, name: req.name, side: req.side,
    shares: req.shares, price: req.price, amount, fee, net,
    note: req.note,
  };
  return {
    ledger: { ...ledger, cash: r2(ledger.cash + net), trades: [...ledger.trades, trade] },
    trade,
  };
}

export function portfolioStats(ledger: Ledger, positions: Position[]) {
  // 只把「有报价」的部分算进市值与浮动盈亏；没报价的单独计数，不摊派
  const priced = positions.filter(p => p.marketValue != null);
  const marketValue = r2(priced.reduce((s, p) => s + (p.marketValue ?? 0), 0));
  const cost = r2(priced.reduce((s, p) => s + p.costBase, 0));
  const unpricedCost = r2(positions.filter(p => p.marketValue == null)
    .reduce((s, p) => s + p.costBase, 0));
  const unpricedCount = positions.length - priced.length;
  const total = r2(ledger.cash + marketValue);
  return {
    cash: ledger.cash,
    marketValue,
    cost,
    floating: r2(marketValue - cost),
    total,
    pnl: r2(total - ledger.initial),
    pnlPct: ledger.initial > 0 ? r2((total - ledger.initial) / ledger.initial * 100) : 0,
    unpricedCount,
    unpricedCost,
    /** 总资产是否完整：有没报价的票时，total 只是「已知部分」 */
    complete: unpricedCount === 0,
  };
}

/* ── 旧版个人池只存本机浏览器 ───────────────────────────── */

// 旧账本接口在 v1 已冻结为只读；实验功能里的模拟仓与日记才是服务端账本。
// 个人池继续沿用旧版的 localStorage，避免每次加入候选都触发必然失败的 POST。

function sane(l: unknown): l is Ledger {
  const x = l as Ledger;
  return !!x && typeof x === 'object' && typeof x.cash === 'number'
    && Array.isArray(x.watch) && Array.isArray(x.trades);
}

export async function loadLedger(): Promise<{ ledger: Ledger; backend: 'server' | 'local' }> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (sane(parsed)) return { ledger: parsed, backend: 'local' };
    }
  } catch { /* 隐私模式下 localStorage 可能不可用 */ }
  return { ledger: newLedger(), backend: 'local' };
}

export async function saveLedger(ledger: Ledger, _backend: 'server' | 'local'): Promise<'server' | 'local'> {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(ledger));
  } catch { /* 存不下就算了，界面会提示 */ }
  return 'local';
}
