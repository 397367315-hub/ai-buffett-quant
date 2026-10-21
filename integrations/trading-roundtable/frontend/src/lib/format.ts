/** 时间展示：存储是 UTC，界面一律显示上海时间（+08:00，无夏令时），并写明是哪种时间。 */

export function shanghai(iso: string | null | undefined, withSeconds = false): string {
  if (!iso) return '--';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const d = new Date(t + 8 * 3600 * 1000);
  const p = (n: number) => String(n).padStart(2, '0');
  const s = `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
  return withSeconds ? `${s}:${p(d.getUTCSeconds())}` : s;
}

export function shortId(id: string | null | undefined, n = 6): string {
  if (!id) return '--';
  const tail = id.split('_').pop() ?? id;
  return tail.length > n ? tail.slice(-n) : tail;
}

export function shortHash(h: string | null | undefined): string {
  return h ? `${h.slice(0, 8)}…` : '--';
}

/** 现在（上海时间）对应的 ISO UTC 字符串，秒精度——只作表单默认值，业务时钟以服务端为准。 */
export function nowIso(): string {
  return new Date(Math.floor(Date.now() / 1000) * 1000).toISOString().replace('.000Z', 'Z');
}

export function plusDaysIso(days: number): string {
  return new Date(Math.floor(Date.now() / 1000) * 1000 + days * 86400 * 1000).toISOString().replace('.000Z', 'Z');
}

export function dateOnly(iso: string | null | undefined): string {
  return iso ? shanghai(iso).slice(0, 10) : '--';
}

export function downloadJson(name: string, obj: unknown) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function downloadText(name: string, text: string) {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export async function sha256Hex(text: string): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
}

/** canonical-json-v1 的浏览器实现（只用于导入组合哈希）：键排序、无空白、UTF-8。 */
export function canonicalJson(v: unknown): string {
  if (v === null || typeof v !== 'object') return JSON.stringify(v);
  if (Array.isArray(v)) return `[${v.map(canonicalJson).join(',')}]`;
  const o = v as Record<string, unknown>;
  return `{${Object.keys(o).sort().map(k => `${JSON.stringify(k)}:${canonicalJson(o[k])}`).join(',')}}`;
}
