/** 会话 + 健康 + 最新快照 + 席位注册表 + 工作区索引：两套外壳共用的数据层（只在用到 v1 的地方挂载）。 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ensureSession, get } from '../api/v1';
import type { HealthData, MarketSnapshot, SeatRegistry, WorkspaceIndex } from '../api/types';
import { type AppState, loadLatestSnapshot, rememberSnapshot, useSession } from './hooks';

export function useAppState(): AppState {
  const session = useSession();
  const [health, setHealth] = useState<HealthData | null>(null);
  const [healthError, setHealthError] = useState<unknown>(null);
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null);
  const [snapshotError, setSnapshotError] = useState<unknown>(null);
  const [snapshotStale, setSnapshotStale] = useState(false);
  const [registry, setRegistry] = useState<SeatRegistry | null>(null);
  const [index, setIndex] = useState<WorkspaceIndex | null>(null);
  const [bootError, setBootError] = useState<unknown>(null);

  const refreshIndex = useCallback(async () => {
    try { setIndex(await get<WorkspaceIndex>('/api/v1/workspace/index')); } catch { /* 索引失败不阻断页面 */ }
  }, []);

  const refreshAll = useCallback(async () => {
    try {
      await ensureSession();
      setBootError(null);
    } catch (e) {
      // 连不上服务：明确显示「数据不可用」，保留上一次成功快照的日期作参考，绝不切到演示数据
      setBootError(e); setSnapshotError(e); setHealthError(e);
      return;
    }
    await Promise.all([
      get<HealthData>('/api/v1/health/data').then(h => { setHealth(h); setHealthError(null); }, e => setHealthError(e)),
      // 读取失败时保留上一次成功的快照（显示原日期与错误），不切换演示数据
      loadLatestSnapshot().then(r => {
        setSnapshot(r.snap); setSnapshotStale(r.stale); setSnapshotError(null);
        if (r.snap) rememberSnapshot(r.snap);
      }, e => setSnapshotError(e)),
      get<SeatRegistry>('/api/v1/seats/registry').then(setRegistry, () => undefined),
      refreshIndex(),
    ]);
  }, [refreshIndex]);

  useEffect(() => { void refreshAll(); }, [refreshAll, session?.context_revision, session?.mode]);

  return useMemo(() => ({ session, health, healthError, snapshot, snapshotError, snapshotStale, registry,
                          index, bootError, refreshAll, refreshIndex }),
    [session, health, healthError, snapshot, snapshotError, snapshotStale, registry, index, bootError, refreshAll, refreshIndex]);
}
