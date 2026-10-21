/**
 * hash 导航（无路由依赖）。两套外壳共用一份 hash：
 *
 *   上一版面（默认）： #/funnel、#/seats、#/candidates、#/portfolio、#/backtest、#/environment、#/cases/<id>
 *   实验功能（新工作台）： #/lab/<workspace>/<id>/<sub>
 *
 * 「环境概览」「案例研究」两页同时属于两套外壳：不带 lab 前缀时嵌在上一版外壳里，带 lab 前缀时在工作台里。
 * 其它 v1 地址（#/plans、#/training…）只有工作台有，裸地址会被规范成 #/lab/...。
 * href()/go() 使用当前外壳的 base（各自外壳渲染时用 setRouteBase 设置），保证链接留在自己的壳里。
 */
import { useEffect, useState } from 'react';

export type Workspace = 'environment' | 'cases' | 'seats' | 'plans' | 'portfolio' | 'training' | 'validation';

export const WORKSPACES: { id: Workspace; label: string; hint: string }[] = [
  { id: 'environment', label: '环境概览', hint: '数据状态、市场矩阵、看盘窗口' },
  { id: 'cases', label: '案例研究', hint: '一个问题、3–4 个席位、分歧与条件' },
  { id: 'seats', label: '席位库', hint: '13 个方法的能力、资料与缺口' },
  { id: 'plans', label: '事前计划', hint: '冻结、版本、触发与取消' },
  { id: 'portfolio', label: '模拟仓与日记', hint: '模拟执行、估值、日记、冷却' },
  { id: 'training', label: '历史训练', hint: '截点内研究、决定后推进' },
  { id: 'validation', label: '方法验证', hint: '候选研究、前瞻登记、旧资料' },
];

/** 上一版外壳自己的页面（含嵌进来的环境概览、案例研究）。 */
export const CLASSIC_PAGES = ['funnel', 'seats', 'candidates', 'portfolio', 'backtest', 'environment', 'cases', 'roundtable', 'commander'] as const;
export type ClassicPage = (typeof CLASSIC_PAGES)[number];

export type Shell = 'classic' | 'lab';

export interface Location { shell: Shell; ws: string; id: string | null; sub: string | null }

let ROUTE_BASE = '';

/** 当前外壳的地址前缀：上一版外壳 ''，工作台 'lab'。 */
export function setRouteBase(base: '' | 'lab') {
  ROUTE_BASE = base;
}

function parts(hash: string): string[] {
  return hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(s => {
    try { return decodeURIComponent(s); } catch { return s; }
  });
}

export function parseLocation(hash: string): Location & { canonical: string | null } {
  const seg = parts(hash);
  if (seg[0] === 'lab') {
    const ws = (WORKSPACES.find(w => w.id === seg[1])?.id ?? 'environment') as Workspace;
    return { shell: 'lab', ws, id: seg[2] ?? null, sub: seg[3] ?? null, canonical: null };
  }
  if (seg.length === 0) return { shell: 'classic', ws: 'funnel', id: null, sub: null, canonical: null };
  const first = seg[0];
  if ((CLASSIC_PAGES as readonly string[]).includes(first)) {
    return { shell: 'classic', ws: first, id: seg[1] ?? null, sub: seg[2] ?? null, canonical: null };
  }
  const ws = (WORKSPACES.find(w => w.id === first)?.id ?? 'environment') as Workspace;
  return { shell: 'lab', ws, id: seg[1] ?? null, sub: seg[2] ?? null,
           canonical: `#/lab/${ws}${seg[1] ? `/${seg[1]}` : ''}${seg[2] ? `/${seg[2]}` : ''}` };
}

export interface Route { ws: Workspace; id: string | null; sub: string | null }

/** 工作台自己的路由（只关心 v1 的 ws/id/sub）。 */
export function parseHash(h: string): Route {
  const loc = parseLocation(h);
  return { ws: loc.ws as Workspace, id: loc.id, sub: loc.sub };
}

export function href(ws: string, id?: string | null, sub?: string | null): string {
  const base = ROUTE_BASE ? `${ROUTE_BASE}/` : '';
  return `#/${base}${ws}${id ? `/${encodeURIComponent(id)}` : ''}${sub ? `/${encodeURIComponent(sub)}` : ''}`;
}

export function go(ws: Workspace, id?: string | null, sub?: string | null) {
  window.location.hash = href(ws, id, sub);
}

export function goClassic(page: ClassicPage, id?: string | null, sub?: string | null) {
  window.location.hash = `#/${page}${id ? `/${encodeURIComponent(id)}` : ''}${sub ? `/${encodeURIComponent(sub)}` : ''}`;
}

export function useLocation(): Location {
  const [loc, setLoc] = useState<Location>(() => {
    const p = parseLocation(window.location.hash);
    return { shell: p.shell, ws: p.ws, id: p.id, sub: p.sub };
  });
  useEffect(() => {
    const sync = () => {
      const p = parseLocation(window.location.hash);
      if (p.canonical) window.history.replaceState(null, '', p.canonical);
      setLoc({ shell: p.shell, ws: p.ws, id: p.id, sub: p.sub });
    };
    sync();
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);
  return loc;
}

export function useRoute(): Route {
  const loc = useLocation();
  return { ws: loc.ws as Workspace, id: loc.id, sub: loc.sub };
}
