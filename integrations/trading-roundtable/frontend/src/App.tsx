/**
 * 外壳路由：默认进入上一版面（五个原有页面 + 环境概览 + 案例研究）；
 * 地址带 #/lab/... 时进入「实验功能」——新版七工作区工作台。
 * 裸的 v1 地址（#/plans、#/training…）由 route.ts 规范成 #/lab/...。
 */
import { useEffect } from 'react';
import { ensureSession } from './api/v1';
import { useLocation } from './lib/route';
import Workbench from './app/Workbench';
import { LegacyApp } from './legacy/LegacyApp';

export default function App() {
  const loc = useLocation();
  // 旧页面的 /api/dashboard 与旧账本接口都需要本地会话：进入哪个壳都先领一次（幂等）
  useEffect(() => { void ensureSession().catch(() => undefined); }, [loc.shell]);

  if (loc.shell === 'lab') {
    return <div className="v1-shell" data-shell="lab"><Workbench /></div>;
  }
  return <div className="legacy-shell" data-shell="classic"><LegacyApp loc={loc as never} /></div>;
}
