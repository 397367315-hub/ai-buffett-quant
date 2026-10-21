/**
 * 金额与十进制（02 §3）：*_minor 是整数分的十进制字符串，只用 BigInt 处理，永远不转成 JS number；
 * 价格、比率是 Decimal 字符串，只做字符串层面的格式化，不做浮点运算。
 */

export type MinorString = string;

const MINOR_RE = /^-?(0|[1-9][0-9]*)$/;
const DEC_RE = /^-?(0|[1-9][0-9]*)(\.[0-9]+)?$/;

/** 解析 API 金额：非法或缺失 → null（不当作 0）。 */
export function parseMinor(v: unknown): bigint | null {
  if (typeof v !== 'string' || !MINOR_RE.test(v)) return null;
  return BigInt(v);
}

/** 分 → 「1,234.56」（千分位只加在整数部分）。null → '--'。 */
export function formatMinor(v: MinorString | null | undefined, opts: { sign?: boolean } = {}): string {
  const b = parseMinor(v);
  if (b === null) return '--';
  const neg = b < 0n;
  const abs = neg ? -b : b;
  const yuan = abs / 100n;
  const fen = abs % 100n;
  const intPart = yuan.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const s = `${intPart}.${fen.toString().padStart(2, '0')}`;
  if (neg) return `-${s}`;
  return opts.sign && b > 0n ? `+${s}` : s;
}

/** 分 → 亿（1 亿 = 100,000,000 元 = 10^10 分），两位小数，用于成交额这类「和系统一致」的市场量。
 * 先按 0.01 亿四舍五入再拆整数/小数，避免大数经过浮点；需要精确值时调用方另附原始「元」。 */
export function formatYi(v: MinorString | null | undefined): string {
  const b = parseMinor(v);
  if (b === null) return '--';
  const neg = b < 0n;
  const abs = neg ? -b : b;
  const base = 10_000_000_000n;                 // 1 亿 = 10^10 分
  const hundredths = (abs * 100n + base / 2n) / base;   // 以 0.01 亿为单位，四舍五入
  const whole = hundredths / 100n;
  const dec = (hundredths % 100n).toString().padStart(2, '0');
  return `${neg ? '-' : ''}${whole.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${dec} 亿`;
}

/** 两个金额相加（都必须已知；任何一个未知 → null）。 */
export function addMinor(...vs: (MinorString | null | undefined)[]): MinorString | null {
  let acc = 0n;
  for (const v of vs) {
    const b = parseMinor(v);
    if (b === null) return null;
    acc += b;
  }
  return acc.toString();
}

/** 元（用户输入的十进制字符串）→ 分字符串；超过两位小数或非法 → null。 */
export function yuanToMinor(input: string): MinorString | null {
  const t = input.trim().replace(/,/g, '');
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return null;
  const [i, f = ''] = t.split('.');
  return (BigInt(i) * 100n + BigInt((f + '00').slice(0, 2))).toString();
}

/** Decimal 字符串原样展示（只去掉多余的尾随 0）；null → '--'。 */
export function formatDecimal(v: string | null | undefined, places?: number): string {
  if (v === null || v === undefined || !DEC_RE.test(v)) return '--';
  if (places === undefined) return v;
  const neg = v.startsWith('-');
  const [i, f = ''] = (neg ? v.slice(1) : v).split('.');
  const padded = (f + '0'.repeat(places)).slice(0, places);
  return `${neg ? '-' : ''}${i}${places ? '.' + padded : ''}`;
}

/** 比率字符串（0.0125）→「1.25%」，纯字符串移位，不经过浮点。null → '--'。 */
export function formatRatioPct(v: string | null | undefined, places = 2): string {
  if (v === null || v === undefined || !DEC_RE.test(v)) return '--';
  const neg = v.startsWith('-');
  const [i, f = ''] = (neg ? v.slice(1) : v).split('.');
  const digits = (i + (f + '00').slice(0, 2)).replace(/^0+(?=\d)/, '');
  const rest = f.slice(2);
  let s = digits + (places ? '.' + (rest + '0'.repeat(places)).slice(0, places) : '');
  if (/^0(\.0*)?$/.test(s)) return `${s}%`;
  return `${neg ? '-' : ''}${s}%`;
}

/** 比率的正负号（用于涨红跌绿配色）：'up' | 'down' | 'flat' | 'unknown'。 */
export function ratioTone(v: string | null | undefined): 'up' | 'down' | 'flat' | 'unknown' {
  if (v === null || v === undefined || !DEC_RE.test(v)) return 'unknown';
  if (/^-?0(\.0+)?$/.test(v)) return 'flat';
  return v.startsWith('-') ? 'down' : 'up';
}

/** 价格变化比率：(close / prev) - 1，用 BigInt 定点（12 位）计算，返回去尾 0 的 Decimal 字符串。 */
export function changeRatio(close: string | null | undefined, prev: string | null | undefined): string | null {
  if (!close || !prev || !DEC_RE.test(close) || !DEC_RE.test(prev)) return null;
  const scale = (s: string) => { const [i, f = ''] = s.split('.'); return { n: BigInt(i + f), d: f.length }; };
  const c = scale(close), p = scale(prev);
  if (p.n === 0n) return null;
  const P = 12;
  // close/prev = (c.n / 10^c.d) / (p.n / 10^p.d)
  const num = c.n * 10n ** BigInt(P + p.d);
  const den = p.n * 10n ** BigInt(c.d);
  let q = num / den;
  const r = num % den;
  if (r * 2n >= den) q += 1n;            // half-up（正数）
  const one = 10n ** BigInt(P);
  const diff = q - one;
  const neg = diff < 0n;
  const a = neg ? -diff : diff;
  const ip = a / one;
  let fp = (a % one).toString().padStart(P, '0').replace(/0+$/, '');
  const s = fp ? `${ip}.${fp}` : `${ip}`;
  return neg && s !== '0' ? `-${s}` : s;
}
