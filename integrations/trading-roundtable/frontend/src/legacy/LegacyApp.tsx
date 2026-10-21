import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { demoData } from './data';
import type { Candidate, QuantPageData, SeatPick, StrategySeat } from './types';
import { FunnelDiagram } from './components/FunnelDiagram';
import { SeatCard, badgeText } from './components/SeatCard';
import { SeatAi } from './components/SeatAi';
import { LlmSettingsDialog } from '../components/LlmSettings';
import { CandidateBoard, CandidateDetail } from './components/CandidateBoard';
import { BacktestPanel } from './components/BacktestPanel';
import { PortfolioPanel } from './components/PortfolioPanel';
import { MarketGateBar } from './components/MarketGateBar';
import { Icon } from './components/Icons';
import { applyOrder, loadLedger, makeQuoteLookup, newLedger, positionsOf, saveLedger } from './portfolio';
import type { Ledger, Side } from './portfolio';
import { ApiError, ensureSession, errorText, get, newKey, post } from '../api/v1';
import { V1Island } from '../app/V1Island';
import { goClassic, setRouteBase, type ClassicPage } from '../lib/route';
import { assetUrl } from '../lib/assets';

const number = (v: number) => new Intl.NumberFormat('zh-CN').format(v);
const n = (v: number | null) => v === null ? '—' : number(v);
const STEPS_DELAY = 1030;

interface LegacyRefreshStatus {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  stage: string;
  history_days: number;
  error?: string | null;
  [key: string]: unknown;
}

interface LegacyRefreshEnvelope { code: number; data: LegacyRefreshStatus }

const LEGACY_REFRESH_JOB_KEY = 'rt.legacy_refresh_job.v1';

/** 席位抽屉里的一只票 → 候选详情抽屉的数据。
 *  规则用这个席位自己的口径；K 线与量价状态从同一份候选数据里取。 */
function toCandidate(pick: SeatPick, seatName: string, all?: Candidate[]): Candidate {
  const full = all?.find(c => c.code === pick.code);
  const others = (full?.seats ?? []).filter(s => s !== seatName);
  return {
    code: pick.code, name: pick.name, pool: pick.pool, poolLabel: pick.poolLabel,
    seat: seatName, seats: [seatName, ...others],
    role: full?.role ?? pick.role, stage: full?.stage ?? pick.stage,
    state10: full?.state10 ?? [], kline: full?.kline ?? [], flow: full?.flow ?? [],
    stats: full?.stats ?? null, rules: pick.rules ?? full?.rules ?? null,
  };
}

/** 抽屉列表里只放短标记，整句留在 title 和候选详情里，不然一行挤不下。 */
function shortFlag(flag: string): string {
  if (!flag || flag === '正常') return '';
  const atr = flag.match(/([\d.]+×ATR)/)?.[1];
  const chase = flag.includes('追高');
  if (chase && atr) return `追高 · ${atr}`;
  if (chase) return '追高';
  if (atr) return atr;
  if (flag.includes('止损过远')) return '止损过远';
  if (flag.includes('结构位过近')) return '结构位近';
  return '注意';
}

function emptyPickText(seat: StrategySeat): string {
  if (seat.version === 'trading-tuixue-v1') return '审查席不产生候选——它只对别的席位的出票做模式审查（四要件 + 四条纪律）。';
  if (!seat.configured) return '这个席位还没接入真实配置，暂时没有票。';
  if (seat.count === null) return '这个席位输出的是周期定位与角色标注，不直接给买入候选；买点要按技能逐条核。';
  if (seat.gate === 'NO_SIGNAL') return '今天没有触发信号，闸门没开。没有信号本身就是一个结论，不用硬凑票。';
  if (seat.gate === 'CLOSED') return '今天闸门关着，不出票。多数交易日只有一两个席位会开闸。';
  return '这个席位今天没有筛出符合条件的票。';
}

type Page = 'funnel' | 'seats' | 'candidates' | 'portfolio' | 'backtest' | 'environment' | 'cases' | 'roundtable' | 'commander';

declare global { interface Window { __DASHBOARD__?: QuantPageData } }

type Scope = 'all' | 'main';

interface DashboardResult { data: QuantPageData; universeSupported: boolean }

/** API 不可用时的真实空态：不携带演示 gate、报价、回测、池计数或股票候选。 */
const emptyData: QuantPageData = {
  source: 'unavailable', stages: [], seats: [], candidates: [],
};

async function readDashboard(scope: Scope = 'all'): Promise<DashboardResult> {
  // 单文件快照模式：没有服务时直接读内联数据
  if (typeof window !== 'undefined' && window.__DASHBOARD__) {
    const snap = window.__DASHBOARD__;
    if (Array.isArray(snap.stages) && Array.isArray(snap.seats)) {
      return { data: { ...snap, source: 'live' }, universeSupported: true };
    }
  }
  await ensureSession();                       // 旧接口同样需要本地会话（同源 cookie）
  let universeSupported = true;
  try {
    const payload = await get<QuantPageData>(`/api/dashboard?universe=${scope}`);
    if (!Array.isArray(payload.stages) || !Array.isArray(payload.seats)) throw new Error('接口缺少 stages 或 seats 数组');
    payload.candidates = Array.isArray(payload.candidates) ? payload.candidates : [];
    if (payload.stages.some(s => !Number.isFinite(s.count) || s.count < 0)) throw new Error('阶段计数格式异常');
    return { data: { ...payload, source: 'live' }, universeSupported };
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 400) throw error;
    const payload = await get<QuantPageData>('/api/dashboard');
    if (!Array.isArray(payload.stages) || !Array.isArray(payload.seats)) throw new Error('接口缺少 stages 或 seats 数组');
    payload.candidates = Array.isArray(payload.candidates) ? payload.candidates : [];
    universeSupported = false;
    return { data: { ...payload, source: 'live' }, universeSupported };
  }
}

function Dot({ tone = 'gold' }: { tone?: 'gold'|'blue'|'gray' }) {
  return <span className={`status-dot dot-${tone}`} aria-hidden="true"/>;
}

function formatDate(v?: string) {
  if (!v) return '暂未同步';
  const time = new Date(v);
  return Number.isNaN(time.getTime()) ? v : time.toLocaleString('zh-CN', { hour12: false });
}

export function LegacyApp({ loc }: { loc: { ws: string; id: string | null; sub: string | null } }) {
  setRouteBase('');                            // 上一版壳里的链接不带 lab 前缀
  const page = (loc.ws || 'funnel') as Page;
  const setPage = (p: Page) => { if (p !== page) goClassic(p as ClassicPage); };
  const [scopeSupported, setScopeSupported] = useState(true);
  const [scope, setScope] = useState<Scope>(() => {
    try { return localStorage.getItem('rt.scope.v1') === 'main' ? 'main' : 'all'; } catch { return 'all'; }
  });
  const [scopeBusy, setScopeBusy] = useState(false);
  const [data, setData] = useState<QuantPageData>(emptyData);
  const [dataError, setDataError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshStatus, setRefreshStatus] = useState<LegacyRefreshStatus | null>(null);
  const refreshPollRef = useRef(false);
  const mountedRef = useRef(true);
  const [selected, setSelected] = useState(0);
  const [visible, setVisible] = useState(1);
  const [playing, setPlaying] = useState(true);
  const [paused, setPaused] = useState(false);
  const [drawerSeat, setDrawerSeat] = useState<StrategySeat | null>(null);
  const [cfgOpen, setCfgOpen] = useState(false);
  const [openCandidate, setOpenCandidate] = useState<Candidate | null>(null);
  const [ledger, setLedger] = useState<Ledger>(newLedger);
  const [pfBackend, setPfBackend] = useState<'server' | 'local'>('local');
  const [pfSaving, setPfSaving] = useState(false);
  const [animatingSeat, setAnimatingSeat] = useState<string | null>(null);
  const clearSeatAnimation = useRef<number | null>(null);

  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false; }; }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setDataError(null);
    try {
      const r = await readDashboard(scope);
      setData(r.data);
      setScopeSupported(r.universeSupported);
    } catch (err) {
      setDataError(err instanceof Error ? err.message : '读取数据失败');
      if (import.meta.env.VITE_ROUNDTABLE_ALLOW_DEMO === '1') {
        setData(demoData); // 仅显式本地演示模式允许回退
      } else {
        setData(emptyData);
      }
    } finally {
      setRefreshing(false);
    }
  }, [scope]);

  const pollLegacyRefresh = useCallback(async (jobId: string) => {
    if (refreshPollRef.current) return;
    refreshPollRef.current = true;
    if (mountedRef.current) setRefreshing(true);
    try {
      for (;;) {
        const envelope = await get<LegacyRefreshEnvelope>('/api/host/refresh/status');
        const status = envelope.data;
        if (!status || status.job_id !== jobId) {
          throw new Error('刷新任务状态缺少匹配的 job_id');
        }
        if (mountedRef.current) setRefreshStatus(status);
        if (status.status === 'completed') {
          try { localStorage.removeItem(LEGACY_REFRESH_JOB_KEY); } catch { /* storage unavailable */ }
          await refresh();
          return;
        }
        if (status.status === 'failed') {
          try { localStorage.removeItem(LEGACY_REFRESH_JOB_KEY); } catch { /* storage unavailable */ }
          if (mountedRef.current) setDataError(status.error || '刷新任务失败');
          return;
        }
        await new Promise(resolve => window.setTimeout(resolve, 1000));
        if (!mountedRef.current) return;
      }
    } catch (error) {
      // Keep the job id so a later page visit can resume status polling.
      if (mountedRef.current) setDataError(errorText(error));
    } finally {
      refreshPollRef.current = false;
      if (mountedRef.current) setRefreshing(false);
    }
  }, [refresh]);

  const startLegacyRefresh = useCallback(async () => {
    if (refreshPollRef.current) return;
    setDataError(null);
    setRefreshing(true);
    try {
      const { data: envelope } = await post<LegacyRefreshEnvelope>('/api/host/refresh', { history_days: 130 }, newKey('legacy-refresh'));
      const status = envelope.data;
      if (!status?.job_id) throw new Error('刷新接口没有返回 job_id');
      try { localStorage.setItem(LEGACY_REFRESH_JOB_KEY, status.job_id); } catch { /* storage unavailable */ }
      setRefreshStatus(status);
      await pollLegacyRefresh(status.job_id);
    } catch (error) {
      setRefreshing(false);
      setDataError(errorText(error));
    }
  }, [pollLegacyRefresh]);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    let jobId: string | null = null;
    try { jobId = localStorage.getItem(LEGACY_REFRESH_JOB_KEY); } catch { /* storage unavailable */ }
    if (jobId) void pollLegacyRefresh(jobId);
  }, [pollLegacyRefresh]);

  /** 标的范围开关（上一版的「仅主板 / 含创业板」）：两个口径各自由生成端算好，切换是换一份数据，不在前端硬算。 */
  const switchScope = async (next: Scope) => {
    if (next === scope || scopeBusy) return;
    setScopeBusy(true);
    setScope(next);
    try { localStorage.setItem('rt.scope.v1', next); } catch { /* 存不下就只记在内存 */ }
    try {
      const r = await readDashboard(next);
      setData(r.data);
      setScopeSupported(r.universeSupported);
      if (!r.universeSupported && next !== 'all') setScope('all');   // 旧服务没有第二份数据，退回含创业板
      setDataError(null);
    } catch (err) {
      setDataError(err instanceof Error ? err.message : '切换标的范围失败');
    } finally {
      setScopeBusy(false);
    }
  };

  useEffect(() => {
    if (page !== 'funnel' || !playing || paused || visible >= data.stages.length) return;
    const timer = window.setTimeout(() => {
      setVisible(v => {
        const next = Math.min(v + 1, data.stages.length);
        setSelected(next - 1);
        if (next >= data.stages.length) setPlaying(false);
        return next;
      });
    }, STEPS_DELAY);
    return () => window.clearTimeout(timer);
  }, [page, playing, paused, visible, data.stages.length]);

  useEffect(() => () => {
    if (clearSeatAnimation.current !== null) window.clearTimeout(clearSeatAnimation.current);
  }, []);

  useEffect(() => {
    if (!drawerSeat && !openCandidate) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (openCandidate) setOpenCandidate(null);   // 先关候选详情，席位抽屉留着
      else setDrawerSeat(null);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [drawerSeat, openCandidate]);

  // ── 个人池：账本读写与下单 ──────────────────────────────
  useEffect(() => {
    let alive = true;
    void (async () => {
      const { ledger: l, backend } = await loadLedger();
      if (!alive) return;
      setLedger(l); setPfBackend(backend);
    })();
    return () => { alive = false; };
  }, []);

  const persist = useCallback(async (next: Ledger) => {
    setLedger(next);
    setPfSaving(true);
    try {
      const b = await saveLedger(next, pfBackend);
      if (b !== pfBackend) setPfBackend(b);
    } finally { setPfSaving(false); }
  }, [pfBackend]);

  const inPool = useCallback((code: string) => ledger.watch.some(w => w.code === code), [ledger]);

  const addToPool = useCallback((c: Candidate) => {
    if (ledger.watch.some(w => w.code === c.code)) return;
    void persist({
      ...ledger,
      watch: [...ledger.watch, {
        code: c.code, name: c.name, pool: c.pool, poolLabel: c.poolLabel,
        seat: c.seat, addedAt: new Date().toISOString(),
      }],
    });
  }, [ledger, persist]);

  const removeWatch = useCallback((code: string) => {
    void persist({ ...ledger, watch: ledger.watch.filter(w => w.code !== code) });
  }, [ledger, persist]);

  const placeOrder = useCallback((req: { code: string; name: string; side: Side; shares: number; price: number; note?: string }): string | null => {
    const pos = positionsOf(ledger, makeQuoteLookup(data)).find(p => p.code === req.code);
    const { ledger: next, error } = applyOrder(ledger, req, pos);
    if (error) return error;
    void persist(next);
    return null;
  }, [data, ledger, persist]);

  const resetPool = useCallback(() => { void persist(newLedger()); }, [persist]);

  const stages = data.stages;
  const finalCount = stages.length ? stages[stages.length - 1].count : 0;
  const coverageRate = stages[0]?.count ? (100 * finalCount / stages[0].count).toFixed(2) : '—';
  const stage = stages[selected] || stages[0];
  const availableSeats = data.seats.filter(s => s.configured).length;
  const openSeats = data.seats.filter(s => s.gate === 'OPEN').length;
  const runningSeats = data.seats.filter(s => s.runState === 'SUCCESS').length;
  const currentRemoved = selected > 0 ? Math.max(0, stages[selected-1].count - stage.count) : null;
  const stageRate = selected > 0 && stages[selected - 1].count > 0
    ? (stage.count / stages[selected - 1].count * 100).toFixed(1) + '%' : '100%';
  const trendData = useMemo(() => [21, 33, 27, 48, 40, 66, 58, 73, 62, 83, 72, 91], []);

  const chooseStage = (i: number) => {
    setSelected(i);
    setVisible(v => Math.max(v, i + 1));
    setPlaying(false);
    setPaused(false);
  };

  const replay = () => {
    setVisible(1);
    setSelected(0);
    setPaused(false);
    setPlaying(true);
  };

  const previewSeat = (seat: StrategySeat) => {
    if (!seat.configured) return;
    if (clearSeatAnimation.current !== null) window.clearTimeout(clearSeatAnimation.current);
    setAnimatingSeat(seat.id);
    clearSeatAnimation.current = window.setTimeout(() => setAnimatingSeat(null), 2000);
  };

  return (
    <div className="shell">
      <aside className="rail" aria-label="侧边导航">
        <div className="rail-symbol"><span className="mark-bars"><i/><i/><i/></span></div>
        <div className="rail-nav">
          <button title="智能漏斗" aria-label="智能漏斗" className={`rail-button ${page === 'funnel' ? 'is-current':''}`} onClick={() => setPage('funnel')}><Icon name="layers" size={21}/></button>
          <button title="策略席位" aria-label="策略席位" className={`rail-button ${page === 'seats' ? 'is-current':''}`} onClick={() => setPage('seats')}><Icon name="users" size={21}/></button>
          <span className="rail-divider" />
          <button title="候选池" aria-label="候选池" className={`rail-button ${page === 'candidates' ? 'is-current':''}`} onClick={() => setPage('candidates')}><Icon name="target" size={21}/></button>
          <button title="个人池" aria-label="个人池" className={`rail-button ${page === 'portfolio' ? 'is-current':''}`} onClick={() => setPage('portfolio')}><Icon name="wallet" size={21}/></button>
          <button title="回测验证" aria-label="回测验证" className={`rail-button ${page === 'backtest' ? 'is-current':''}`} onClick={() => setPage('backtest')}><Icon name="shield" size={21}/></button>
          <span className="rail-divider" />
          <button title="环境概览" aria-label="环境概览" className={`rail-button ${page === 'environment' ? 'is-current':''}`} onClick={() => setPage('environment')} data-testid="rail-environment"><Icon name="chart" size={21}/></button>
          <button title="案例研究" aria-label="案例研究" className={`rail-button ${page === 'cases' ? 'is-current':''}`} onClick={() => setPage('cases')} data-testid="rail-cases"><Icon name="spark" size={21}/></button>
        </div>
        <div className="rail-foot">
          <button className="rail-button rail-lab" title="实验功能（新版工作台：事前计划、模拟仓与日记、历史训练、方法验证）"
                  aria-label="实验功能" data-testid="rail-lab" onClick={() => { window.location.hash = '#/lab/environment'; }}>
            <Icon name="flask" size={19}/>
          </button>
          <span className="rail-bottom">Q<span>·</span>V1</span>
        </div>
      </aside>

      <main className="main">
        <header className="app-header">
          <div className="wordmark"><span className="wordmark-icon"><span/><span/><span/></span><div><b>AGI <em>QUANT</em></b><small>INTELLIGENT MARKET SYSTEM</small></div></div>
          <div className="header-right">
            <span className="market-tag"><Dot tone={data.source === 'live' ? 'blue' : 'gold'}/>{data.source === 'live' ? '已接入真实数据' : data.source === 'demo' ? '界面演示模式' : '数据不可用'}</span>
            {data.dataStatus?.quote_date && <span className="data-stamp" title={data.dataStatus.freshness_reason}>报价 {data.dataStatus.quote_date} · 生成 {(data.dataStatus.generated_at || '').slice(5,10).replace('T',' ')}</span>}
            <button className="icon-button refresh-button" aria-label="刷新数据" title="刷新数据" onClick={() => void startLegacyRefresh()} disabled={refreshing}><span className={refreshing ? 'spinning' : ''}><Icon name="refresh" size={17}/></span></button>
            {refreshStatus && (refreshing || refreshStatus.status === 'queued' || refreshStatus.status === 'running')
              ? <span className="data-stamp" role="status" data-testid="legacy-refresh-status">{refreshStatus.stage || '正在同步'} · {refreshStatus.history_days} 日</span>
              : null}
            <div className="header-avatar">A</div>
          </div>
        </header>

        <div className="content">
          <div className="breadcrumb"><span>策略工作台</span><Icon name="chevron" size={13}/><strong>{page === 'funnel' ? '智能选股漏斗' : page === 'seats' ? '独立策略席位' : page === 'candidates' ? '今日候选池' : page === 'portfolio' ? '我的个人池' : page === 'backtest' ? '回测验证' : page === 'environment' ? '环境概览' : page === 'roundtable' ? '圆桌' : page === 'commander' ? '总指挥' : '案例研究'}</strong><span className="breadcrumb-right">AGI / TRADING INTELLIGENCE <b>2026</b></span></div>
          {data.marketGate && <MarketGateBar gate={data.marketGate}/>}
          {dataError && <div className="data-warning" role="status"><Icon name="info" size={15}/>真实接口连接失败：{dataError}。当前无可用数据，等待同步。</div>}
          {refreshStatus?.status === 'failed' && !dataError
            ? <div className="data-warning" role="status" data-testid="legacy-refresh-error"><Icon name="info" size={15}/>刷新失败：{refreshStatus.error || '服务端没有提供失败原因'}。</div>
            : null}
          {!scopeSupported && <div className="data-warning" role="status" data-testid="scope-stale"><Icon name="info" size={15}/>
            后台还是改动前启动的旧版本（它不认识「标的范围」参数）。数据已按旧接口正常读取，{data.seats.length} 席都在；
            要切换「仅主板 / 含创业板」，请重启服务：关掉终端窗口，重新双击「打开交易圆桌.command」。</div>}

          {page === 'environment' ? (
            <V1Island page="environment" id={null} sub={null}/>
          ) : page === 'roundtable' ? (
            <V1Island page="roundtable" id={null} sub={null}/>
          ) : page === 'commander' ? (
            <V1Island page="commander" id={null} sub={null}/>
          ) : page === 'cases' ? (
            <V1Island page="cases" id={loc.id} sub={loc.sub}/>
          ) : page === 'candidates' ? (
            <CandidateBoard candidates={data.candidates ?? []} onAddToPool={addToPool} inPool={inPool}/>
          ) : page === 'portfolio' ? (
            <PortfolioPanel data={data} ledger={ledger} backend={pfBackend} saving={pfSaving}
              onOrder={placeOrder} onRemoveWatch={removeWatch} onReset={resetPool}/>
          ) : page === 'backtest' ? (
            data.backtest ? <BacktestPanel data={data.backtest}/> : <div className="seat-notice"><Icon name="info" size={18}/><span>接口未返回回测数据。</span></div>
          ) : page === 'funnel' ? (
            <>
              <section className="heading-row">
                <div><div className="eyebrow"><span>01</span> / INTELLIGENT FUNNEL</div><h1>全市场<span className="accent">智能筛选漏斗</span></h1><p className="section-summary">从海量股票中层层提纯，把每一次筛选都变成看得见的决策过程。</p></div>
                <div className="heading-controls"><div className="scope-switch" role="group" aria-label="标的范围" data-testid="scope-switch" data-supported={scopeSupported ? 'true' : 'false'}>
                  <button className={`scope-btn ${scope === 'main' ? 'on' : ''}`} aria-pressed={scope === 'main'}
                          disabled={scopeBusy || !scopeSupported} onClick={() => void switchScope('main')} data-testid="scope-main"
                          title="只沪深主板：排除创业板、科创板、北交所、B 股">仅主板</button>
                  <button className={`scope-btn ${scope === 'all' ? 'on' : ''}`} aria-pressed={scope === 'all'}
                          disabled={scopeBusy || !scopeSupported} onClick={() => void switchScope('all')} data-testid="scope-all"
                          title="沪深主板＋创业板：科创板、北交所、B 股仍不在样本内">含创业板</button>
                </div><button className="outline-btn" onClick={replay}><Icon name="refresh" size={15}/>重播筛选</button><button className="solid-btn" onClick={() => { setPlaying(v => v || visible < stages.length); setPaused(p => !p); }} disabled={!playing && visible >= stages.length}><Icon name={paused ? 'play' : 'pause'} size={15}/>{paused ? '继续动画' : '暂停动画'}</button></div>
              </section>

              <section className="metrics metrics-funnel" aria-label="筛选汇总">
                <div className="metric"><div className="metric-label"><span className="metric-icon"><Icon name="database" size={17}/></span> 初始股票池</div><div className="metric-bottom"><strong>{n(stages[0]?.count ?? 0)}</strong><small>只股票</small></div><div className="metric-foot">{data.universe?.label ?? '沪深主板 + 创业板'}</div></div>
                <div className="metric"><div className="metric-label"><span className="metric-icon orange"><Icon name="layers" size={17}/></span> 已筛选层级</div><div className="metric-bottom"><strong>{String(visible).padStart(2,'0')}<i> / {String(stages.length).padStart(2,'0')}</i></strong></div><div className="metric-foot">五层递进式条件过滤</div></div>
                <div className="metric"><div className="metric-label"><span className="metric-icon green"><Icon name="target" size={17}/></span> 最终候选池</div><div className="metric-bottom"><strong className="metric-highlight">{n(finalCount)}</strong><small>只股票</small></div><div className="metric-foot">跨席位去重 · 保留归属</div></div>
                <div className="metric metric-last"><div className="metric-label"><span className="metric-icon purple"><Icon name="activity" size={17}/></span> 最终保留比例</div><div className="metric-bottom"><strong>{coverageRate}<i>%</i></strong></div><div className="sparkline">{trendData.map((p,i) => <span key={i} style={{ height: `${p}%` }}/>)}</div></div>
              </section>

              <section className="funnel-grid">
                <div className="visual-panel">
                  <div className="visual-panel-head"><div><span className="panel-kicker">LIVE VISUALIZATION</span><h2>五层策略过滤引擎</h2></div><div className="pulse-caption"><Dot tone="blue"/>{playing && !paused ? '正在演示过滤' : paused ? '已暂停' : '已完成 / 待命'}</div></div>
                  <FunnelDiagram stages={stages} selected={selected} visible={visible} playing={playing} paused={paused} onSelect={chooseStage}/>
                  <div className="visual-result"><div className="result-icon"><Icon name="target" size={26}/></div><div><small>FINAL CANDIDATE POOL</small><strong>最终精选 <span>{n(finalCount)}</span> 只标的</strong></div><button onClick={() => setPage('seats')}>查看席位分析 <Icon name="arrow" size={16}/></button></div>
                </div>

                <div className="right-stack">
                  <div className="stage-explorer"><div className="panel-heading"><div><span className="panel-kicker dark">FILTER PIPELINE</span><h2>筛选执行链路</h2></div><span className="panel-counter">{stages.length} STAGES</span></div>
                    <div className="stage-steps">{stages.map((s,i) => <button key={s.id} className={`step ${selected === i ? 'is-selected' : ''} ${i < visible ? 'is-done' : ''}`} onClick={() => chooseStage(i)}>
                      <span className="step-number">{String(i+1).padStart(2,'0')}</span><span className="step-main"><strong>{s.title}</strong><small>{s.shortTitle}</small></span><span className="step-right"><b>{n(s.count)}</b><Icon name="chevron" size={14}/></span></button>)}</div>
                  </div>
                  <div className="insight-card">
                    <div className="insight-header"><span className="insight-id">{stage?.id ?? '—'} / LAYER INSIGHT</span><span className="insight-indicator"><Icon name="spark" size={15}/> 筛选规则</span></div>
                    <h3>{stage?.title}</h3>
                    <p>{stage?.logic}</p>
                    <div className="insight-duo"><div><small>本层保留</small><strong>{n(stage?.count ?? 0)}<span> 只</span></strong></div><div><small>{selected === 0 ? '样本基准' : '本层剔除'}</small><strong>{currentRemoved === null ? '—' : n(currentRemoved)}<span>{currentRemoved === null ? '' : ' 只'}</span></strong></div></div>
                    <div className="rule-label">筛选判定条件 <span>PASS RATE {stageRate}</span></div>
                    <div className="rule-list">{stage?.conditions.map((rule, i) => <div className="rule-item" key={i}><span className="rule-check"><Icon name="check" size={12}/></span><span>{rule}</span></div>)}</div>
                    <div className="insight-note"><Icon name="info" size={16}/><span>点击漏斗任意层或上方步骤，可展开对应的真实筛选规则。</span></div>
                  </div>
                  <div className="disclosure"><Icon name="shield" size={16}/><span>本页面{data.source === 'demo' ? '使用原截图数值演示布局与动画' : data.source === 'unavailable' ? '等待真实接口同步数据' : '展示接口返回的统计结果'}；不构成买卖建议。</span></div>
                </div>
              </section>
              <div className="bottom-path"><span className="bottom-path-tag">FLOW</span>市场样本 <Icon name="arrow" size={14}/> 基础合规 <Icon name="arrow" size={14}/> 席位覆盖 <Icon name="arrow" size={14}/> 环境过滤 <Icon name="arrow" size={14}/> 去重研究候选 <span className="end-chip"><Icon name="check" size={12}/> 完成</span></div>
            </>
          ) : (
            <>
              <section className="heading-row seat-heading"><div><div className="eyebrow"><span>03</span> / STRATEGY AGENTS</div><h1>独立策略<span className="accent">交易席位</span></h1><p className="section-summary">十三位独立策略代理，各自观察市场、运行规则、输出候选。席位之间不投票、不折中。</p></div><div className="heading-controls"><button className="solid-btn" onClick={() => setPage('commander')} data-testid="open-commander"><Icon name="spark" size={15}/> 总指挥</button><button className="solid-btn" onClick={() => setPage('roundtable')} data-testid="open-roundtable"><Icon name="users" size={15}/> 圆桌：几席一起看</button><button className="outline-btn" onClick={() => setCfgOpen(true)} data-testid="seats-model-settings"><Icon name="target" size={15}/> 模型设置</button><button className="outline-btn" onClick={() => setPage('funnel')}><Icon name="layers" size={15}/> 返回选股漏斗</button><button className="solid-btn" onClick={() => void startLegacyRefresh()} disabled={refreshing}><Icon name="refresh" size={15}/>{refreshing ? '正在同步' : '同步最新数据'}</button></div></section>
              <section className="metrics metrics-seats"><div className="metric"><div className="metric-label"><span className="metric-icon"><Icon name="users" size={17}/></span> 策略席位</div><div className="metric-bottom"><strong>{data.seats.length.toString().padStart(2,'0')}</strong><small>个</small></div><div className="metric-foot">每席位对应独立策略</div></div><div className="metric"><div className="metric-label"><span className="metric-icon green"><Icon name="check" size={17}/></span> 已配置席位</div><div className="metric-bottom"><strong>{availableSeats.toString().padStart(2,'0')}</strong><small>个</small></div><div className="metric-foot">其余待接入真实配置</div></div><div className="metric"><div className="metric-label"><span className="metric-icon orange"><Icon name="activity" size={17}/></span> 今日已运行</div><div className="metric-bottom"><strong>{runningSeats.toString().padStart(2,'0')}</strong><small>个</small></div><div className="metric-foot">本次刷新全部跑完的席位</div></div><div className="metric metric-last"><div className="metric-label"><span className="metric-icon purple"><Icon name="target" size={17}/></span> 当前允许开仓</div><div className="metric-bottom"><strong>{openSeats.toString().padStart(2,'0')}</strong><small>个席位</small></div><div className="metric-foot">不代表候选股票买卖建议</div></div></section>
              <div className="seats-toolbar"><div><Dot tone="blue"/><strong>独立席位工作台</strong><span>人物状态与策略结果分离展示</span></div><span className="seat-toolbar-note">{data.source === 'demo' ? 'DEMO DATA / 示例数值' : data.source === 'unavailable' ? 'WAITING FOR DATA / 待同步' : `UPDATED ${formatDate(data.asOf)}`}</span></div>
              <div className="seats-grid">{data.seats.map((s,i) => <SeatCard key={s.id} seat={s} index={i} onSelect={() => setDrawerSeat(s)} animating={animatingSeat === s.id}/>)}</div>
              <div className="seat-notice"><Icon name="info" size={18}/><span>十三个席位各自独立运行，人物形象为角色插画，不对应真实人物。席位判断由各自的策略脚本产出，界面只负责展示。</span></div>
            </>
          )}
          <footer className="footer"><span>© AGI QUANT · STRATEGY RESEARCH INTERFACE</span><span>DESIGNED FOR SIGNAL CLARITY <b>—</b> NOT INVESTMENT ADVICE</span></footer>
        </div>
      </main>

      {drawerSeat && <div className="drawer-scrim" onMouseDown={e => { if (e.target === e.currentTarget) setDrawerSeat(null); }}>
        <section className="seat-drawer" role="dialog" aria-modal="true" aria-label={`${drawerSeat.name}席位详情`}>
          <div className="drawer-top"><span>STRATEGY AGENT / DETAIL</span><button aria-label="关闭详情" className="icon-button" onClick={() => setDrawerSeat(null)}><Icon name="close" size={20}/></button></div>
            <div className="drawer-profile"><div className="drawer-portrait">{drawerSeat.portrait ? <img src={assetUrl(drawerSeat.portrait)} alt=""/> : <Icon name="users" size={40}/>}</div><div><span className="drawer-sublabel">独立策略席位</span><h2>{drawerSeat.name}</h2><span className={`seat-status status-${drawerSeat.gate.toLowerCase()}`}>{badgeText[drawerSeat.gate]}</span></div></div>
          <div className="drawer-section"><label>策略定位</label><p>{drawerSeat.subtitle || '待接入真实席位配置'}</p></div>
          <div className="drawer-section"><label>策略规则及判断</label><p>{drawerSeat.reasoning}</p></div>
          <div className="drawer-grid"><div><label>席位候选</label><strong>{n(drawerSeat.count)} <small>只</small></strong></div><div><label>分析运行状态</label><strong className="drawer-status">{drawerSeat.runState === 'SUCCESS' ? '已完成' : drawerSeat.runState === 'RUNNING' ? '运行中' : drawerSeat.runState === 'ERROR' ? '运行异常' : '未运行'}</strong></div></div>
          <div className="drawer-section seat-picks"><label>本席位筛出的票{drawerSeat.picks && drawerSeat.picks.length > 0 && <span className="picks-count">{drawerSeat.picks.length} 只</span>}</label>
            {drawerSeat.picks && drawerSeat.picks.length > 0 ? <>
              {drawerSeat.gate !== 'OPEN' && <p className="picks-gated"><Icon name="info" size={14}/><span>闸门现在是「{badgeText[drawerSeat.gate]}」：下面是脚本仍筛出的备选，按纪律不该动手。</span></p>}
              <div className="pick-list">
                {drawerSeat.picks.map(p => (
                  <button className="pick-row" key={p.code} title={p.risk_flag || undefined}
                    onClick={() => setOpenCandidate(toCandidate(p, drawerSeat.name, data.candidates))}>
                    <span className="pick-name">{p.name}<small>{p.code}</small></span>
                    <span className="pick-price">{p.stop != null ? <>止损 <b>{p.stop}</b>（−{p.risk_pct?.toFixed(2)}%）</> : '无价位'}</span>
                    <span className={`pick-flag${shortFlag(p.risk_flag) ? ' is-warn' : ''}`}>{shortFlag(p.risk_flag)}</span>
                    <Icon name="chevron" size={13}/>
                  </button>
                ))}
              </div>
              {drawerSeat.count != null && drawerSeat.count > drawerSeat.picks.length &&
                <p className="picks-note">共 {drawerSeat.count} 只，这里列前 {drawerSeat.picks.length} 只。</p>}
            </> : <p className="picks-empty">{emptyPickText(drawerSeat)}</p>}
          </div>
          <div className="drawer-section"><label>AI 大脑（这一席自己的模型、自己的书）</label>
            <SeatAi seat={drawerSeat} snapshotId={null} onOpenSettings={() => setCfgOpen(true)} />
            <div className="seat-ai-round">
              <button className="outline-btn mini" data-testid="seat-ai-roundtable"
                      onClick={() => {
                        let picks: string[] = [];
                        try { picks = JSON.parse(sessionStorage.getItem('rt.roundtable.picks') || '[]'); } catch { picks = []; }
                        if (!Array.isArray(picks)) picks = [];
                        if (drawerSeat.version?.startsWith('trading-') && !picks.includes(drawerSeat.version)) picks.push(drawerSeat.version);
                        sessionStorage.setItem('rt.roundtable.picks', JSON.stringify(picks.slice(-4)));
                        setDrawerSeat(null);
                        setPage('roundtable');
                      }}>
                <Icon name="users" size={14}/> 带上这一席去圆桌（和其他席位共同探讨）
              </button>
            </div>
          </div>
          <div className="drawer-section"><label>策略关键词</label><div className="drawer-tags">{drawerSeat.keywords.map(k => <span key={k}>{k}</span>)}</div></div>
          <div className="drawer-section"><label>策略版本</label><code>{drawerSeat.version}</code><p className="drawer-update">更新：{formatDate(drawerSeat.updatedAt || data.asOf)}</p></div>
          <div className="drawer-end"><button className="outline-btn" onClick={() => previewSeat(drawerSeat)} disabled={!drawerSeat.configured}>{animatingSeat === drawerSeat.id ? '人物正在演示…' : '预览人物工作动效'}</button><p>动效仅作 UI 预览，不重新运行交易策略，也不生成交易信号。</p></div>
        </section>
      </div>}
      {openCandidate && <CandidateDetail c={openCandidate} onClose={() => setOpenCandidate(null)}
        onAddToPool={addToPool} inPool={inPool}/>}
      <div className="v1-shell">
        <LlmSettingsDialog open={cfgOpen} onClose={() => setCfgOpen(false)} />
      </div>
    </div>
  );
}
