import { clearAuthSession, readAuthSession } from '@/lib/authSession';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const TRANSIENT_NETWORK_ERRORS = new Set([
  'Load failed',
  'Failed to fetch',
  'NetworkError when attempting to fetch resource.',
]);
const RETRYABLE_STATUS_CODES = new Set([502, 503, 504]);
const READ_RETRY_DELAYS_MS = [400, 900];

function errorMessage(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught || '');
}

function isAbortError(caught: unknown, signal?: AbortSignal | null): boolean {
  return Boolean(signal?.aborted) || (caught instanceof Error && caught.name === 'AbortError');
}

function isRetryableNetworkError(caught: unknown): boolean {
  const message = errorMessage(caught);
  return TRANSIENT_NETWORK_ERRORS.has(message)
    || (caught instanceof TypeError && /load failed|failed to fetch|networkerror|fetch/i.test(message));
}

function abortReason(signal?: AbortSignal | null): unknown {
  return signal?.reason || new DOMException('The operation was aborted.', 'AbortError');
}

async function waitForRetry(delayMs: number, signal?: AbortSignal | null): Promise<void> {
  if (signal?.aborted) throw abortReason(signal);
  await new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, delayMs);
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(abortReason(signal));
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/** Keep browser-specific fetch errors out of user-facing module messages. */
export function friendlyApiError(caught: unknown, fallback = '请求失败，请稍后重试'): string {
  const message = errorMessage(caught);
  if (!message || TRANSIENT_NETWORK_ERRORS.has(message)) {
    return '后端连接暂时中断，请稍后重试。';
  }
  return message || fallback;
}

export function isTransientApiError(caught: unknown): boolean {
  const message = errorMessage(caught);
  return TRANSIENT_NETWORK_ERRORS.has(message) || /连接暂时中断|网络|超时/i.test(message);
}

export type ApiFetchOptions = RequestInit & {
  /** Total wall-clock budget, including transient read retries. */
  timeoutMs?: number;
};

export async function apiFetch<T>(path: string, options?: ApiFetchOptions): Promise<T> {
  const url = `${API_BASE}/api/v1${path}`;
  const { timeoutMs: configuredTimeoutMs, ...requestOptions } = options || {};
  const method = String(requestOptions.method || 'GET').toUpperCase();
  const canRetry = method === 'GET' || method === 'HEAD';
  const timeoutMs = Math.max(1000, configuredTimeoutMs ?? (canRetry ? 30000 : 45000));
  const headers = new Headers(requestOptions.headers);
  if (requestOptions.body != null && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const session = readAuthSession();
  if (session && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${session.token}`);
  }
  const requestController = new AbortController();
  let timedOut = false;
  const timeoutHandle = window.setTimeout(() => {
    timedOut = true;
    requestController.abort();
  }, timeoutMs);
  const forwardAbort = () => requestController.abort(requestOptions.signal?.reason);
  requestOptions.signal?.addEventListener('abort', forwardAbort, { once: true });

  try {
    let res: Response | null = null;
    for (let attempt = 0; attempt <= READ_RETRY_DELAYS_MS.length; attempt += 1) {
      try {
        res = await fetch(url, {
          ...requestOptions,
          method,
          headers,
          signal: requestController.signal,
        });
      } catch (caught) {
        if (timedOut) throw new Error(`请求超时（${Math.ceil(timeoutMs / 1000)}秒），请稍后重试`);
        if (isAbortError(caught, requestOptions.signal)) throw caught;
        if (!canRetry || !isRetryableNetworkError(caught) || attempt >= READ_RETRY_DELAYS_MS.length) {
          throw new Error(friendlyApiError(caught));
        }
        await waitForRetry(READ_RETRY_DELAYS_MS[attempt], requestController.signal);
        continue;
      }
      if (!canRetry || !RETRYABLE_STATUS_CODES.has(res.status) || attempt >= READ_RETRY_DELAYS_MS.length) {
        break;
      }
      await res.body?.cancel().catch(() => undefined);
      await waitForRetry(READ_RETRY_DELAYS_MS[attempt], requestController.signal);
    }
    if (!res) throw new Error('后端连接暂时中断，请稍后重试。');
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
  } finally {
    window.clearTimeout(timeoutHandle);
    requestOptions.signal?.removeEventListener('abort', forwardAbort);
  }
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
