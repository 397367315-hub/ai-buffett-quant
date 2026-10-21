/**
 * v1 客户端（02 §8 / §13.2）：本地会话 + 会话绑定 CSRF + 每个意图一个幂等键 + 202 任务轮询。
 *
 * - 会话 cookie 是 HttpOnly，脚本拿不到；CSRF token 只存在内存里（不进 URL、不进 localStorage）；
 * - 写请求一律 POST，带 X-CSRF-Token 与 Idempotency-Key；同一意图的重试复用同一个键；
 * - 202 只说明「已受理」，成功要等 GET /api/v1/jobs/{id} 到 succeeded/partial；
 * - 401（会话过期）→ 重新 bootstrap 一次再重试；其它错误原样抛出 ApiError（code/message/details）。
 */

export interface ErrorDetail { field: string; reason: string }

export class ApiError extends Error {
  status: number;
  code: string;
  details: ErrorDetail[];
  requestId: string | null;
  retryable: boolean;
  constructor(status: number, code: string, message: string, details: ErrorDetail[] = [], requestId: string | null = null,
              retryable = false) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
    this.retryable = retryable;
  }
}

export interface SessionContext {
  session_id: string;
  mode: 'live' | 'replay';
  replay_id: string | null;
  knowledge_cutoff_at: string | null;
  context_revision: number;
  csrf_token: string;
  created_at: string;
  expires_at: string;
}

export interface Job {
  job_id: string; kind: string; state: string; phase: string; completed_units: number; total_units: number | null;
  resource_type: string | null; resource_id: string | null; created_at: string; started_at: string | null;
  finished_at: string | null; attempt: number; error: { error: { code: string; message: string; details: ErrorDetail[] } } | null;
}

export interface JobAccepted { job: Job; status_url: string; resource_url: string | null }

let session: SessionContext | null = null;
let booting: Promise<SessionContext> | null = null;

const API_BASE = String(import.meta.env.VITE_ROUNDTABLE_API_BASE ?? '/api/v1/roundtable').replace(/\/$/, '');
const SESSION_KEY = 'roundtable.session.v1';

function storedSession(): Pick<SessionContext, 'session_id' | 'csrf_token' | 'expires_at'> | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null');
    return value && typeof value.session_id === 'string' ? value : null;
  } catch { return null; }
}

function saveSession(value: SessionContext) {
  setSession(value);
  try { sessionStorage.setItem(SESSION_KEY, JSON.stringify({ session_id: value.session_id, csrf_token: value.csrf_token, expires_at: value.expires_at })); } catch { /* private browsing */ }
}

function clearSession() {
  setSession(null);
  try { sessionStorage.removeItem(SESSION_KEY); } catch { /* private browsing */ }
}

function endpoint(path: string): string {
  const clean = path.startsWith('/') ? path : `/${path}`;
  // Vendor paths retain their semantic names while the gateway owns the
  // roundtable prefix: /api/v1/foo -> /v1/foo; /api/dashboard -> /dashboard.
  const withoutApi = clean.replace(/^\/api\//, '/');
  return `${API_BASE}${withoutApi}`;
}

function hostBearer(): string | null {
  try {
    const value = JSON.parse(localStorage.getItem('stockflow_session') || 'null');
    return typeof value?.token === 'string' && value.token ? value.token : null;
  } catch { return null; }
}

function shouldRenewSession(e: unknown): boolean {
  if (!(e instanceof ApiError)) return false;
  if (e.status === 401) return true;
  if (e.status !== 403) return false;
  return new Set(['SESSION_EXPIRED', 'ROUND_TABLE_SESSION_EXPIRED', 'CSRF_EXPIRED', 'CSRF_INVALID', 'CSRF_MISMATCH']).has(e.code);
}
const listeners = new Set<(s: SessionContext | null) => void>();

export function onSession(fn: (s: SessionContext | null) => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

function setSession(s: SessionContext | null) {
  session = s;
  listeners.forEach(fn => fn(s));
}

export function currentSession(): SessionContext | null { return session; }

export function newKey(intent: string): string {
  const rnd = (typeof crypto !== 'undefined' && 'randomUUID' in crypto)
    ? crypto.randomUUID().replace(/-/g, '')
    : Math.random().toString(36).slice(2) + Date.now().toString(36);
  return `${intent.replace(/[^a-z0-9_-]/gi, '').slice(0, 40)}-${rnd}`.slice(0, 120);
}

async function raw(method: string, path: string, body?: unknown, headers: Record<string, string> = {}) {
  const h: Record<string, string> = { Accept: 'application/json', ...headers };
  if (body !== undefined) h['Content-Type'] = 'application/json';
  const bearer = hostBearer();
  if (bearer && !h.Authorization) h.Authorization = `Bearer ${bearer}`;
  const active = session ?? storedSession();
  if (active?.session_id && !h['X-Roundtable-Session']) h['X-Roundtable-Session'] = active.session_id;
  let res: Response;
  try {
    res = await fetch(endpoint(path), { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body),
                              credentials: 'omit', cache: 'no-store' });
  } catch (e) {
    throw new ApiError(0, 'NETWORK_UNAVAILABLE', '连不上本地服务（请确认 panel/api.py 正在运行）');
  }
  const text = await res.text();
  let data: any = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = null; }
  }
  if (!res.ok) {
    const e = data?.error;
    const hostMessage = typeof data?.detail === 'string' ? data.detail : null;
    throw new ApiError(res.status, e?.code ?? `HTTP_${res.status}`, e?.message ?? hostMessage ?? `请求失败（HTTP ${res.status}）`,
                       e?.details ?? [], e?.request_id ?? null, !!e?.retryable);
  }
  return { status: res.status, data, headers: res.headers };
}

export async function bootstrap(): Promise<SessionContext> {
  if (booting) return booting;
  booting = (async () => {
    const r = await raw('POST', '/api/v1/session/bootstrap', {});
    saveSession(r.data as SessionContext);
    return r.data as SessionContext;
  })();
  try { return await booting; } finally { booting = null; }
}

/** 先沿用浏览器里已有的会话（刷新页面不能把人从训练上下文里踢回真实盘）；没有或已过期（401）才新建。 */
export async function ensureSession(): Promise<SessionContext> {
  if (session && Date.parse(session.expires_at) > Date.now() + 5000) return session;
  const saved = storedSession();
  if (saved && Date.parse(saved.expires_at) > Date.now() + 5000) {
    try {
      const r = await raw('GET', '/api/v1/session/context');
      saveSession(r.data as SessionContext);
      return r.data as SessionContext;
    } catch { clearSession(); }
  }
  try {
    const r = await raw('GET', '/api/v1/session/context');
    saveSession(r.data as SessionContext);
    return r.data as SessionContext;
  } catch (e) {
    if (shouldRenewSession(e)) {
      clearSession();
      return bootstrap();
    }
    throw e;
  }
}

export async function refreshContext(): Promise<SessionContext> {
  const r = await get<SessionContext>('/api/v1/session/context');
  saveSession(r);
  return r;
}

export async function get<T>(path: string): Promise<T> {
  await ensureSession();
  try {
    return (await raw('GET', path)).data as T;
  } catch (e) {
    if (shouldRenewSession(e)) {
      clearSession();
      await bootstrap();
      return (await raw('GET', path)).data as T;
    }
    throw e;
  }
}

export async function post<T>(path: string, body: unknown, key: string): Promise<{ status: number; data: T }> {
  const s = await ensureSession();
  const send = (csrf: string) => raw('POST', path, body, { 'X-CSRF-Token': csrf, 'Idempotency-Key': key });
  try {
    const r = await send(s.csrf_token);
    return { status: r.status, data: r.data as T };
  } catch (e) {
    if (shouldRenewSession(e)) {
      clearSession();
      const s2 = await bootstrap();
      const r = await send(s2.csrf_token);
      return { status: r.status, data: r.data as T };
    }
    throw e;
  }
}

/** 202 任务：轮询到终态。onProgress 用于显示阶段与进度；超时只停止轮询，不判定任务失败。 */
export async function waitJob(jobId: string, onProgress?: (j: Job) => void, timeoutMs = 15 * 60 * 1000): Promise<Job> {
  const t0 = Date.now();
  let delay = 400;
  for (;;) {
    const j = await get<Job>(`/api/v1/jobs/${jobId}`);
    onProgress?.(j);
    if (['succeeded', 'partial', 'failed', 'cancelled'].includes(j.state)) return j;
    if (Date.now() - t0 > timeoutMs) throw new ApiError(0, 'POLL_TIMEOUT', '任务仍在运行：稍后在任务状态里查看结果');
    await new Promise(r => setTimeout(r, delay));
    delay = Math.min(2000, Math.round(delay * 1.5));
  }
}

/** 提交异步任务并等到终态；返回终态任务。失败任务抛 ApiError（带任务错误码）。 */
export async function runJob(path: string, body: unknown, key: string, onProgress?: (j: Job) => void): Promise<Job> {
  const { data } = await post<JobAccepted>(path, body, key);
  onProgress?.(data.job);
  const j = await waitJob(data.job.job_id, onProgress);
  if (j.state === 'failed') {
    const e = j.error?.error;
    throw new ApiError(0, e?.code ?? 'JOB_FAILED', e?.message ?? '任务失败', e?.details ?? []);
  }
  return j;
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const d = e.details.slice(0, 4).map(x => `${x.field}：${x.reason}`).join('；');
    return `${e.message}${d ? `（${d}）` : ''}`;
  }
  return e instanceof Error ? e.message : String(e);
}
