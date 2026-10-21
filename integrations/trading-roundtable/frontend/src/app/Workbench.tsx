import { errorText } from '../api/v1';
import { StatusBand } from '../components/StatusBand';
import { AppCtx } from '../lib/hooks';
import { useAppState } from '../lib/appData';
import { WORKSPACES, href, setRouteBase, useRoute } from '../lib/route';
import { shanghai } from '../lib/format';
import { EnvironmentPage } from '../pages/EnvironmentPage';
import { CasesPage } from '../pages/CasesPage';
import { SeatsPage } from '../pages/SeatsPage';
import { PlansPage } from '../pages/PlansPage';
import { PortfolioPage } from '../pages/PortfolioPage';
import { TrainingPage } from '../pages/TrainingPage';
import { ValidationPage } from '../pages/ValidationPage';

/** 实验功能：新版七工作区工作台（上一版外壳侧栏底部的「实验」进入，地址 #/lab/...）。 */
export default function Workbench() {
  setRouteBase('lab');          // 工作台里的链接一律带 lab 前缀，留在本壳
  const route = useRoute();
  const app = useAppState();
  const session = app.session;
  const replay = session?.mode === 'replay';
  const executionEnabled = !!app.snapshot && !app.snapshotStale && !replay && app.snapshot.metadata.freshness === 'fresh';

  return (
    <AppCtx.Provider value={app}>
      <div data-testid="app-root" data-execution-enabled={executionEnabled ? 'true' : 'false'}
           data-session-mode={session?.mode ?? 'none'}>
        <StatusBand />
        <nav className="wsnav" aria-label="工作区">
          <a className="wsnav-home" href="#/funnel" data-testid="lab-home" title="回到上一版主界面（智能漏斗 / 策略席位 / 候选池）">← 返回主界面</a>
          {WORKSPACES.map(w => (
            <a key={w.id} href={href(w.id)} aria-current={route.ws === w.id ? 'page' : undefined} title={w.hint}>{w.label}</a>
          ))}
        </nav>
        {replay ? (
          <div className="alert warn" role="status" style={{ margin: '12px 24px 0', maxWidth: 'none' }} data-testid="replay-banner">
            历史训练中：只显示 {shanghai(session?.knowledge_cutoff_at)}（上海时间）之前已经可知的数据；真实盘的计划、组合与旧页面在训练结束前不可见。
            {' '}<a href={href('training', session?.replay_id)}>回到训练</a>
          </div>
        ) : null}
        {app.bootError ? (
          <main className="workspace">
            <div className="alert" role="alert">没能建立本地会话：{errorText(app.bootError)}。请确认本机服务正在运行（python3 panel/api.py）。</div>
          </main>
        ) : (
          <main className="workspace" id="main">
            {route.ws === 'environment' && <EnvironmentPage />}
            {route.ws === 'cases' && <CasesPage id={route.id} sub={route.sub} />}
            {route.ws === 'seats' && <SeatsPage id={route.id} />}
            {route.ws === 'plans' && <PlansPage id={route.id} sub={route.sub} />}
            {route.ws === 'portfolio' && <PortfolioPage id={route.id} />}
            {route.ws === 'training' && <TrainingPage id={route.id} />}
            {route.ws === 'validation' && <ValidationPage id={route.id} />}
          </main>
        )}
      </div>
    </AppCtx.Provider>
  );
}
