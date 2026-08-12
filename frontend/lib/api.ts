import { clearAuthSession, readAuthSession } from '@/lib/authSession';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const TRANSIENT_NETWORK_ERRORS = new Set([
  'Load failed',
  'Failed to fetch',
  'NetworkError when attempting to fetch resource.',
]);

/** Keep browser-specific fetch errors out of user-facing module messages. */
export function friendlyApiError(caught: unknown, fallback = '请求失败，请稍后重试'): string {
  const message = caught instanceof Error ? caught.message : String(caught || '');
  if (!message || TRANSIENT_NETWORK_ERRORS.has(message)) {
    return '后端连接暂时中断，请稍后重试。';
  }
  return message || fallback;
}

export function isTransientApiError(caught: unknown): boolean {
  const message = caught instanceof Error ? caught.message : String(caught || '');
  return TRANSIENT_NETWORK_ERRORS.has(message) || /连接暂时中断|网络|超时/i.test(message);
}

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}/api/v1${path}`;
  const headers = new Headers(options?.headers);
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const session = readAuthSession();
  if (session && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${session.token}`);
  }
  let res: Response;
  try {
    res = await fetch(url, {
      ...options,
      headers,
    });
  } catch (caught) {
    throw new Error(friendlyApiError(caught));
  }
  let payload: any = null;
  try {
    payload = await res.json();
  } catch {
    // Non-JSON proxy failures still receive the HTTP status below.
  }
  if (!res.ok) {
    if (res.status === 401) {
      clearAuthSession();
      if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
        window.location.assign('/login');
      }
    }
    const detail = typeof payload?.detail === 'string'
      ? payload.detail
      : Array.isArray(payload?.detail)
        ? payload.detail.map((item: any) => item?.msg).filter(Boolean).join('；')
        : payload?.message;
    throw new Error(detail || `请求失败：${res.status} ${res.statusText}`);
  }
  return payload as T;
}

export function formatYi(value: number): string {
  const yi = value / 1e8;
  const sign = value >= 0 ? '+' : '';
  return `${sign}${yi.toFixed(2)}亿`;
}

export function formatYiShort(value: number): string {
  const yi = value / 1e8;
  return `${yi.toFixed(1)}亿`;
}

export function getChangeColor(value: number): string {
  if (value > 0) return 'text-[#EF5350]';
  if (value < 0) return 'text-[#26A69A]';
  return 'text-[#8B949E]';
}

export function getBgColor(value: number): string {
  if (value > 0) return '#EF5350';
  if (value < 0) return '#26A69A';
  return '#8B949E';
}
