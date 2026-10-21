import { errorText } from '../api/v1';
import { StatusBand } from '../components/StatusBand';
import { AppCtx } from '../lib/hooks';
import { useAppState } from '../lib/appData';
import { setRouteBase } from '../lib/route';
import { EnvironmentPage } from '../pages/EnvironmentPage';
import { CasesPage } from '../pages/CasesPage';
import { RoundtableView } from '../pages/SeatsPage';
import { CommanderPage } from '../pages/CommanderPage';

/**
 * 把新版的两页（环境概览、案例研究）嵌进上一版外壳。
 * 链接基址是上一版壳（不带 lab 前缀）：页内指向计划/训练/验证的链接会被路由转到「实验功能」工作台。
 */
export function V1Island({ page, id, sub }: { page: 'environment' | 'cases' | 'roundtable' | 'commander'; id: string | null; sub: string | null }) {
  setRouteBase('');
  const app = useAppState();
  return (
    <AppCtx.Provider value={app}>
      <div className="v1-shell v1-embed" data-testid="v1-island" data-v1-page={page}>
        <StatusBand />
        {app.bootError ? (
          <main className="workspace"><div className="alert" role="alert">没能建立本地会话：{errorText(app.bootError)}</div></main>
        ) : (
          <main className="workspace">
            {page === 'environment' ? <EnvironmentPage />
              : page === 'roundtable' ? <RoundtableView />
              : page === 'commander' ? <CommanderPage />
              : <CasesPage id={id} sub={sub} />}
          </main>
        )}
      </div>
    </AppCtx.Provider>
  );
}
