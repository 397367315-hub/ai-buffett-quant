import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { ApiError, currentSession, get, onSession, type SessionContext } from '../api/v1';
import type { HealthData, MarketSnapshot, SeatRegistry, WorkspaceIndex } from '../api/types';

/** 读一份数据：loading / error / data，reload() 重新读；读失败保留上一次成功的数据并给出错误。 */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: unknown; loading: boolean;
  reload: () => Promise<void> } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const reload = useCallback(async () => {
    const n = ++seq.current;
    setLoading(true);
    try {
      const v = await fn();
      if (n === seq.current) { setData(v); setError(null); }
    } catch (e) {
      if (n === seq.current) setError(e);
    } finally {
      if (n === seq.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { reload(); }, [reload]);
  return { data, error, loading, reload };
}

export interface AppState {
  session: SessionContext | null;
  health: HealthData | null;
  healthError: unknown;
  snapshot: MarketSnapshot | null;
  snapshotError: unknown;
  snapshotStale: boolean;
  registry: SeatRegistry | null;
  index: WorkspaceIndex | null;
  /** 连不上本地服务（会话建立失败）时给出原因；页面据此显示「没法建立会话」。 */
  bootError: unknown;
  refreshAll: () => Promise<void>;
  refreshIndex: () => Promise<void>;
}

export const AppCtx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const v = useContext(AppCtx);
  if (!v) throw new Error('AppCtx missing');
  return v;
}

export function useSession(): SessionContext | null {
  const [s, setS] = useState<SessionContext | null>(currentSession());
  useEffect(() => onSession(setS), []);
  return s;
}

/** 最新快照：先按「此刻最新」读；503 DATA_STALE（此刻不是最新或无法判断）时按 allow_stale 读出旧快照——
 *  返回的 metadata.freshness 是服务端读取时按当前日历重估的结果（stale 或 unknown），界面照这个显示，不自己猜。 */
export async function loadLatestSnapshot(): Promise<{ snap: MarketSnapshot | null; stale: boolean }> {
  try {
    return { snap: await get<MarketSnapshot>('/api/v1/snapshots/latest'), stale: false };
  } catch (e) {
    if (e instanceof ApiError && e.code === 'DATA_STALE') {
      const snap = await get<MarketSnapshot>('/api/v1/snapshots/latest?allow_stale=true');
      return { snap, stale: snap.metadata.freshness !== 'fresh' };
    }
    if (e instanceof ApiError && e.code === 'SNAPSHOT_NOT_FOUND') return { snap: null, stale: false };
    throw e;
  }
}

export function useLocalState<T>(key: string, init: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : init;
    } catch { return init; }
  });
  const set = useCallback((nv: T) => {
    setV(nv);
    try { window.localStorage.setItem(key, JSON.stringify(nv)); } catch { /* 存储不可用时只保留在内存 */ }
  }, [key]);
  return [v, set];
}

export const isReplay = (s: SessionContext | null) => s?.mode === 'replay';

const LAST_KEY = () => {
  const s = currentSession();
  return `rt.last_snapshot.${s?.mode ?? 'live'}.${s?.replay_id ?? 'none'}`;
};

/** 只记最近一次成功快照的「身份与日期」（不存行情内容），失联时用来说明「上次成功的数据是哪天的」。 */
export function rememberSnapshot(snap: MarketSnapshot) {
  try {
    const md = snap.metadata;
    window.sessionStorage.setItem(LAST_KEY(), JSON.stringify({ snapshot_id: snap.snapshot_id, quote_date: md.quote_date,
      captured_at: md.captured_at, generated_at: md.generated_at }));
  } catch { /* 存储不可用 */ }
}

export function lastKnownSnapshot(): { snapshot_id: string; quote_date: string | null; captured_at: string } | null {
  try { const v = window.sessionStorage.getItem(LAST_KEY()); return v ? JSON.parse(v) : null; } catch { return null; }
}
