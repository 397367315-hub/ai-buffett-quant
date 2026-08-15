'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  BarChart3,
  BrainCircuit,
  Building2,
  CheckCircle2,
  Clock3,
  Database,
  Gauge,
  History,
  Layers3,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
  Target,
  Trash2,
  TriangleAlert,
} from 'lucide-react';
import KlineChart, { KlineRow } from '@/components/KlineChart';
import { apiFetch, formatYi, friendlyApiError } from '@/lib/api';

type KlineCategory = 4 | 5 | 6 | 11;
type AnyMap = Record<string, any>;

interface KlineData {
  stock_code: string;
  stock_name: string;
  category: KlineCategory;
  category_label: string;
  rows: KlineRow[];
  count: number;
  available: boolean;
  source: string;
  data_date: string | null;
  is_realtime: boolean;
  warning: string | null;
}

interface QueryHistoryItem {
  code: string;
  name: string;
  asOf: string;
  dataDate: string | null;
  savedAt: string;
  profile: AnyMap;
  flowData: AnyMap[];
  kline: KlineData | null;
}

const QUERY_HISTORY_KEY = 'stock-decision-query-history-v1';
const QUERY_HISTORY_LIMIT = 8;

function readQueryHistory(): QueryHistoryItem[] {
  try {
    const raw = window.localStorage.getItem(QUERY_HISTORY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is QueryHistoryItem => Boolean(
      item && typeof item.code === 'string' && item.profile && typeof item.profile === 'object',
    )).slice(0, QUERY_HISTORY_LIMIT);
  } catch {
    return [];
  }
}

function writeQueryHistory(items: QueryHistoryItem[]): void {
  try {
    window.localStorage.setItem(QUERY_HISTORY_KEY, JSON.stringify(items.slice(0, QUERY_HISTORY_LIMIT)));
  } catch {
    // A private browsing session or a full quota must not break stock queries.
  }
}

const CATEGORY_OPTIONS: Array<{ value: KlineCategory; label: string }> = [
  { value: 4, label: '日K' },
  { value: 5, label: '周K' },
  { value: 6, label: '月K' },
  { value: 11, label: '60分钟' },
];

const DECISION_STYLES: Record<string, string> = {
  EXECUTE: 'border-up/50 bg-up/10 text-up',
  CAUTION: 'border-warn/50 bg-warn/10 text-warn',
  OBSERVE: 'border-accent/50 bg-accent/10 text-accent',
  AVOID: 'border-down/50 bg-down/10 text-down',
  NO_TRADE: 'border-border bg-[#21262D] text-text-secondary',
};

const AUDIT_LABELS: Record<string, string> = {
  observed: '已核验',
  cached_fallback: '缓存回退',
  source_retry_required: '数据源本次响应异常',
};

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function number(value: unknown, digits = 2, suffix = '', fallback = '有效样本不足'): string {
  return finite(value) ? `${value.toFixed(digits)}${suffix}` : fallback;
}

function signed(value: unknown, digits = 2, suffix = '%', fallback = '有效样本不足'): string {
  if (!finite(value)) return fallback;
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}${suffix}`;
}

function yi(value: unknown, fallback = '股本披露未覆盖'): string {
  return finite(value) ? `${(value / 1e8).toFixed(2)}亿` : fallback;
}

function text(value: unknown, fallback = '公司未公开披露'): string {
  const displayValue = value && typeof value === 'object'
    ? (value as AnyMap).label ?? (value as AnyMap).name ?? (value as AnyMap).code
    : value;
  const normalized = String(displayValue ?? '').trim();
  return normalized || fallback;
}

function shares(value: unknown): string {
  if (!finite(value)) return '公开行情未提供';
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)}亿股`;
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(2)}万股`;
  return `${value.toFixed(0)}股`;
}

function tone(value: unknown): string {
  return finite(value) && value > 0 ? 'text-up' : finite(value) && value < 0 ? 'text-down' : 'text-text';
}

function SectionTitle({ icon: Icon, title, meta }: { icon: any; title: string; meta?: string }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-text"><Icon size={16} className="text-accent" />{title}</h2>
      {meta && <span className="text-[10px] text-text-secondary">{meta}</span>}
    </div>
  );
}

function Metric({ label, value, detail, valueClass = 'text-text' }: { label: string; value: string; detail?: string; valueClass?: string }) {
  return (
    <div className="min-w-0 border-l border-border pl-3">
      <div className="text-[10px] text-text-secondary">{label}</div>
      <div className={`mt-1 break-words text-sm font-semibold ${valueClass}`}>{value}</div>
      {detail && <div className="mt-1 break-words text-[10px] leading-4 text-text-secondary">{detail}</div>}
    </div>
  );
}

function MetricGrid({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-2 gap-x-3 gap-y-5 sm:grid-cols-3 lg:grid-cols-6">{children}</div>;
}

function EmptyVerified({ children }: { children: React.ReactNode }) {
  return <div className="border-y border-border py-6 text-center text-xs text-text-secondary">{children}</div>;
}

export default function StockPage() {
  const [stockCode, setStockCode] = useState('');
  const [asOf, setAsOf] = useState('');
  const [profile, setProfile] = useState<AnyMap | null>(null);
  const [flowData, setFlowData] = useState<AnyMap[]>([]);
  const [kline, setKline] = useState<KlineData | null>(null);
  const [category, setCategory] = useState<KlineCategory>(4);
  const [hasSearched, setHasSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [klineLoading, setKlineLoading] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiNarrative, setAiNarrative] = useState('');
  const [error, setError] = useState('');
  const [queryHistory, setQueryHistory] = useState<QueryHistoryItem[]>([]);
  const [loadedFromHistory, setLoadedFromHistory] = useState(false);
  const requestIdRef = useRef(0);
  const activeControllerRef = useRef<AbortController | null>(null);

  const isCurrentRequest = useCallback((requestId: number) => requestIdRef.current === requestId, []);

  const saveQuerySnapshot = useCallback((
    nextProfile: AnyMap,
    nextFlowData: AnyMap[],
    nextKline: KlineData | null,
    code: string,
    requestedAsOf: string,
  ) => {
    setQueryHistory((current) => {
      const previous = current.find((entry) => entry.code === code && entry.asOf === requestedAsOf);
      const item: QueryHistoryItem = {
        code,
        name: String(nextProfile.company?.stock_name || code),
        asOf: requestedAsOf,
        dataDate: nextProfile.meta?.data_date || null,
        savedAt: new Date().toISOString(),
        profile: nextProfile,
        flowData: nextFlowData.length ? nextFlowData.slice(-30) : previous?.flowData || [],
        kline: nextKline
          ? { ...nextKline, rows: nextKline.rows.slice(-120) }
          : previous?.kline || null,
      };
      const next = [item, ...current.filter((entry) => !(entry.code === code && entry.asOf === requestedAsOf))].slice(0, QUERY_HISTORY_LIMIT);
      writeQueryHistory(next);
      return next;
    });
  }, []);

  useEffect(() => {
    setQueryHistory(readQueryHistory());
  }, []);

  useEffect(() => () => {
    requestIdRef.current += 1;
    activeControllerRef.current?.abort();
  }, []);

  useEffect(() => {
    if (!loading) return;
    const timer = window.setInterval(() => {
      setProgress((current) => current >= 94 ? current : Math.min(94, current + (current < 45 ? 7 : current < 75 ? 4 : 1)));
    }, 550);
    return () => window.clearInterval(timer);
  }, [loading]);

  const loadKline = useCallback(async (code: string, nextCategory: KlineCategory, requestedAsOf = asOf, signal?: AbortSignal) => {
    setKlineLoading(true);
    try {
      const offset = nextCategory === 6 ? 120 : nextCategory === 5 ? 160 : 120;
      const query = new URLSearchParams({ code, category: String(nextCategory), offset: String(offset) });
      if (requestedAsOf) query.set('as_of', requestedAsOf);
      const response = await apiFetch<{ data: KlineData }>(`/kline?${query}`, {
        signal,
        timeoutMs: 20000,
      });
      setKline(response.data);
    } catch {
      if (!signal?.aborted) setKline(null);
    } finally {
      setKlineLoading(false);
    }
  }, [asOf]);

  const handleSearch = useCallback(async (codeInput?: string, force = false, asOfInput?: string, fromHistory = false) => {
    const code = String(codeInput ?? stockCode).trim();
    if (!code) return;
    const requestedAsOf = asOfInput ?? asOf;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    activeControllerRef.current?.abort();
    const controller = new AbortController();
    activeControllerRef.current = controller;
    setStockCode(code);
    if (asOfInput !== undefined) setAsOf(asOfInput);
    setLoading(true);
    setProgress(8);
    setError('');
    setAiNarrative('');
    setLoadedFromHistory(fromHistory);
    setCategory(4);
    const profileQuery = new URLSearchParams();
    if (requestedAsOf) profileQuery.set('as_of', requestedAsOf);
    if (force) profileQuery.set('refresh', 'true');
    const klineQuery = new URLSearchParams({ code, category: '4', offset: '120' });
    if (requestedAsOf) klineQuery.set('as_of', requestedAsOf);

    const profileTask = apiFetch<{ data: AnyMap }>(
      `/stocks/${encodeURIComponent(code)}/decision-profile${profileQuery.size ? `?${profileQuery}` : ''}`,
      { signal: controller.signal, timeoutMs: 70000 },
    );
    const flowTask = apiFetch<AnyMap>(`/flow/stock/${encodeURIComponent(code)}`, {
      signal: controller.signal,
      timeoutMs: 20000,
    });
    const klineTask = apiFetch<{ data: KlineData }>(`/kline?${klineQuery}`, {
      signal: controller.signal,
      timeoutMs: 20000,
    });

    // The profile is the primary result. Auxiliary endpoints update as they
    // finish and cannot hold the progress bar at an artificial 92% forever.
    void flowTask.then((result) => {
      if (!isCurrentRequest(requestId)) return;
      const nextFlowData = result.data.flow_data || [];
      setFlowData(nextFlowData);
      setProgress((current) => Math.max(current, 58));
    }).catch(() => {
      if (isCurrentRequest(requestId) && !controller.signal.aborted && !fromHistory) setFlowData([]);
    });
    void klineTask.then((result) => {
      if (!isCurrentRequest(requestId)) return;
      setKline(result.data);
      setProgress((current) => Math.max(current, 68));
    }).catch(() => {
      if (isCurrentRequest(requestId) && !controller.signal.aborted && !fromHistory) setKline(null);
    });

    try {
      const result = await profileTask;
      if (!isCurrentRequest(requestId)) return;
      setProfile(result.data);
      setHasSearched(true);
      setProgress(100);
      setError('');
      saveQuerySnapshot(result.data, [], null, code, requestedAsOf);
    } catch (caught) {
      if (!isCurrentRequest(requestId) || controller.signal.aborted) return;
      setError(friendlyApiError(caught, '个股决策画像生成失败'));
      setHasSearched(true);
      setProgress(100);
    } finally {
      if (isCurrentRequest(requestId)) {
        setLoading(false);
      }
    }

    // Enrich the just-saved history item when the two lighter requests finish.
    void Promise.allSettled([flowTask, klineTask]).then(([flowResult, klineResult]) => {
      if (!isCurrentRequest(requestId)) return;
      const currentProfile = profileTask;
      void currentProfile.then((result) => {
        if (!isCurrentRequest(requestId)) return;
        const nextFlowData = flowResult.status === 'fulfilled' ? flowResult.value.data.flow_data || [] : [];
        const nextKline = klineResult.status === 'fulfilled' ? klineResult.value.data : null;
        saveQuerySnapshot(result.data, nextFlowData, nextKline, code, requestedAsOf);
      }).catch(() => undefined);
      if (isCurrentRequest(requestId)) activeControllerRef.current = null;
    });
  }, [asOf, isCurrentRequest, saveQuerySnapshot, stockCode]);

  const openHistory = useCallback((item: QueryHistoryItem) => {
    requestIdRef.current += 1;
    activeControllerRef.current?.abort();
    activeControllerRef.current = null;
    setStockCode(item.code);
    setAsOf(item.asOf);
    setProfile(item.profile);
    setFlowData(item.flowData || []);
    setKline(item.kline || null);
    setCategory(4);
    setHasSearched(true);
    setLoadedFromHistory(true);
    setLoading(false);
    setProgress(0);
    setError('');
  }, []);

  const refreshHistoryItem = useCallback((item: QueryHistoryItem) => {
    setProfile(item.profile);
    setFlowData(item.flowData || []);
    setKline(item.kline || null);
    setAsOf('');
    void handleSearch(item.code, true, '', true);
  }, [handleSearch]);

  const clearQueryHistory = useCallback(() => {
    window.localStorage.removeItem(QUERY_HISTORY_KEY);
    setQueryHistory([]);
  }, []);

  const deleteQueryHistoryItem = useCallback((code: string, requestedAsOf: string) => {
    setQueryHistory((current) => {
      const next = current.filter((item) => !(item.code === code && item.asOf === requestedAsOf));
      writeQueryHistory(next);
      return next;
    });
  }, []);

  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get('code')?.trim();
    if (code) void handleSearch(code);
    // URL initialization runs once; form actions use current state afterwards.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const switchCategory = async (nextCategory: KlineCategory) => {
    if (!stockCode.trim() || nextCategory === category) return;
    setCategory(nextCategory);
    await loadKline(stockCode.trim(), nextCategory);
  };

  const explainDecision = async () => {
    if (!profile || aiLoading) return;
    setAiLoading(true);
    try {
      const response = await apiFetch<{ data: AnyMap }>(`/ai/stocks/${encodeURIComponent(stockCode)}/decision`, {
        method: 'POST',
        body: JSON.stringify({ as_of: asOf || undefined }),
      });
      setAiNarrative(String(response.data.narrative || '结构化解释已完成。'));
    } catch (caught) {
      setAiNarrative(friendlyApiError(caught, 'AI解释本次未完成'));
    } finally {
      setAiLoading(false);
    }
  };

  const company = profile?.company || {};
  const fundamentals = profile?.fundamentals || {};
  const fm = fundamentals.metrics || {};
  const valuation = profile?.valuation || {};
  const capital = profile?.capital_impact || {};
  const attribution = profile?.attribution || {};
  const sectorRole = profile?.sector_role || {};
  const dependency = profile?.sector_dependency || {};
  const emotion = profile?.emotion || {};
  const catalysts = profile?.catalysts || {};
  const expectation = profile?.expectation_gap || {};
  const risk = profile?.risk_reward || {};
  const strategy = profile?.strategy_fit || {};
  const decision = profile?.decision || {};
  const meta = profile?.meta || {};
  const audit = profile?.data_audit || {};
  const market = profile?.market_context || {};

  const stateStyle = DECISION_STYLES[decision.state] || DECISION_STYLES.OBSERVE;
  const loadingLabel = loadedFromHistory
    ? '历史快照已打开，后台核验最新数据'
    : progress < 35 ? '核验公司、财务与行情' : progress < 65 ? '计算板块、Alpha与资金冲击' : progress < 90 ? '生成风险收益与策略适配' : '整理证据与决策状态';
  const auditSources = useMemo(() => audit.sources || [], [audit.sources]);
  const expectationCovered = expectation.availability === 'covered' && (expectation.analyst_count || 0) > 0;
  const maxDecisionDate = useMemo(() => new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10), []);

  return (
    <main className="mx-auto max-w-7xl px-3 py-5 sm:px-4 sm:py-6">
      <header className="mb-5 border-b border-border pb-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-xl font-bold text-text"><BrainCircuit size={21} className="text-accent" />个股决策画像</h1>
            <p className="mt-1 text-xs text-text-secondary">事实、归因、风险与策略窗口统一核验</p>
          </div>
          {profile && <div className="text-right text-[10px] leading-4 text-text-secondary">契约 {meta.contract_version}<br />数据日 {meta.data_date} · {meta.is_realtime ? '交易时段实时' : '最近完整交易日/历史快照'}</div>}
        </div>
      </header>

      <section className="border-y border-border py-4">
        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_170px_auto]">
          <input
            type="text"
            value={stockCode}
            onChange={(event) => setStockCode(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') void handleSearch(); }}
            placeholder="输入6位股票代码，如 600519"
            className="h-10 min-w-0 rounded-md border border-border bg-bg px-3 text-sm text-text placeholder:text-text-secondary focus:border-accent focus:outline-none"
          />
          <label className="relative block min-w-0">
            <Clock3 size={14} className="pointer-events-none absolute left-3 top-3 text-text-secondary" />
            <input
              type="date"
              value={asOf}
              onChange={(event) => setAsOf(event.target.value)}
              max={maxDecisionDate}
              aria-label="历史决策日期"
              className="h-10 w-full min-w-0 rounded-md border border-border bg-bg pl-9 pr-2 text-xs text-text focus:border-accent focus:outline-none"
            />
          </label>
          <div className="grid grid-cols-[1fr_40px] gap-2 md:flex">
            <button
              type="button"
              onClick={() => void handleSearch()}
              disabled={loading || !stockCode.trim()}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-accent px-5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {loading ? <RefreshCw size={15} className="animate-spin" /> : <Search size={15} />}{loading ? '核验中' : '生成画像'}
            </button>
            <button
              type="button"
              title="强制刷新公开数据"
              aria-label="强制刷新公开数据"
              onClick={() => void handleSearch(undefined, true)}
              disabled={loading || !stockCode.trim()}
              className="grid h-10 w-10 place-items-center rounded-md border border-border bg-card text-text-secondary hover:text-text disabled:opacity-50"
            ><RefreshCw size={15} /></button>
          </div>
        </div>
        {asOf && <div className="mt-2 text-[10px] text-text-secondary">历史模式会严格使用该日期之前已公告的财报与行情。</div>}
        {queryHistory.length > 0 && (
          <div className="mt-4 border-t border-border pt-3">
            <div className="mb-2 flex items-center justify-between gap-3">
              <div className="flex items-center gap-1.5 text-[10px] font-medium text-text-secondary"><History size={13} />最近查询</div>
              <button
                type="button"
                onClick={clearQueryHistory}
                title="清空最近查询"
                aria-label="清空最近查询"
                className="grid h-7 w-7 place-items-center rounded border border-border text-text-secondary hover:text-text"
              ><Trash2 size={13} /></button>
            </div>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {queryHistory.map((item) => (
                <div key={`${item.code}-${item.asOf || 'latest'}`} className="relative min-w-[190px] shrink-0 rounded border border-border bg-card hover:border-accent/60">
                  <button
                    type="button"
                    onClick={() => openHistory(item)}
                    className="block w-full px-3 py-2 pr-[70px] text-left"
                    title="打开当时保存的查询画像"
                  >
                    <div className="flex items-center gap-2 text-xs"><span className="font-medium text-text">{item.name}</span><span className="font-mono text-text-secondary">{item.code}</span></div>
                    <div className="mt-1 text-[10px] text-text-secondary">数据日 {item.dataDate || '最近交易日'} · {item.asOf || '最新'}</div>
                  </button>
                  <button
                    type="button"
                    onClick={() => refreshHistoryItem(item)}
                    title={`刷新 ${item.name} 最新分析`}
                    aria-label={`刷新 ${item.name} 最新分析`}
                    className="absolute right-9 top-1.5 grid h-7 w-7 place-items-center rounded text-text-secondary hover:bg-bg hover:text-accent"
                  ><RefreshCw size={12} /></button>
                  <button
                    type="button"
                    onClick={() => deleteQueryHistoryItem(item.code, item.asOf)}
                    title={`删除 ${item.name} 查询记录`}
                    aria-label={`删除 ${item.name} 查询记录`}
                    className="absolute right-1.5 top-1.5 grid h-7 w-7 place-items-center rounded text-text-secondary hover:bg-bg hover:text-down"
                  ><Trash2 size={12} /></button>
                </div>
              ))}
            </div>
          </div>
        )}
        {loading && (
          <div className="mt-4">
            <div className="mb-1.5 flex justify-between text-[10px] text-text-secondary"><span>{loadingLabel}</span><span>{progress}%</span></div>
            <div className="h-1.5 overflow-hidden rounded-sm bg-[#21262D]"><div className="h-full bg-accent transition-all duration-500" style={{ width: `${progress}%` }} /></div>
          </div>
        )}
        {error && <div className="mt-3 flex items-start gap-2 text-xs text-down"><TriangleAlert size={14} className="mt-0.5 shrink-0" />{error}</div>}
      </section>

      {profile && (
        <>
          <section className="border-b border-border bg-card px-3 py-5 sm:px-4">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-xl font-bold text-text">{text(company.stock_name, stockCode)}</h2>
                  <span className="font-mono text-xs text-text-secondary">{company.stock_code}</span>
                  <span className={`rounded-sm border px-2 py-0.5 text-xs font-semibold ${stateStyle}`}>{decision.label || '观察'}</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-secondary">
                  <span>{text(company.sub_industry, '行业分类按最近披露')}</span>
                  <span>{text(profile.state_matrix?.label, '条件验证型')}</span>
                  <span>{meta.scope}</span>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-5 lg:min-w-[360px]">
                <Metric label="最新价" value={number(company.current_price)} />
                <Metric label="Alpha评分" value={number(attribution.individual_alpha_score, 1)} />
                <Metric label="风险收益比" value={number(risk.risk_reward_ratio, 2)} />
              </div>
            </div>
          </section>

          <section className="border-b border-border py-5">
            <SectionTitle icon={Target} title="当前决策" meta="结构化规则锁定，AI不能修改" />
            <div className={`border-l-2 px-4 py-3 ${stateStyle}`}>
              <div className="text-sm font-semibold">{decision.label || '观察'} · {market.final_action || 'observe'}</div>
              <div className="mt-2 grid gap-2 text-xs leading-5 md:grid-cols-2">
                {(decision.reasons || []).map((item: string) => <div key={item}>依据：{item}</div>)}
              </div>
            </div>
            <div className="mt-4 grid gap-5 lg:grid-cols-2">
              <div>
                <div className="mb-2 text-xs font-medium text-text">失效条件</div>
                <ul className="space-y-1.5 text-xs leading-5 text-text-secondary">{(decision.invalidation_conditions || []).map((item: string) => <li key={item}>• {item}</li>)}</ul>
              </div>
              <div>
                <div className="mb-2 text-xs font-medium text-text">市场上层约束</div>
                <div className="space-y-1.5 text-xs leading-5 text-text-secondary">
                  <div>主要矛盾：{text(market.principal_contradiction, '按市场工作台最近快照核验')}</div>
                  <div>主导方面：{text(market.dominant_aspect, '多空力量按客观数据核验')}</div>
                  <div>阶段：{text(market.stage, '阶段状态按最近交易日确认')}</div>
                </div>
              </div>
            </div>
            <div className="mt-4 flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-start">
              <button type="button" onClick={() => void explainDecision()} disabled={aiLoading} className="inline-flex h-9 shrink-0 items-center justify-center gap-2 rounded-md border border-accent/50 bg-accent/10 px-4 text-xs font-medium text-accent hover:bg-accent/20 disabled:opacity-50">
                {aiLoading ? <RefreshCw size={14} className="animate-spin" /> : <Sparkles size={14} />}{aiLoading ? 'AI解析中' : 'AI解释当前决策'}
              </button>
              {aiNarrative && <div className="whitespace-pre-wrap text-xs leading-5 text-text-secondary">{aiNarrative}</div>}
            </div>
          </section>

          <section className="border-b border-border py-5">
            <SectionTitle icon={Building2} title="公司本体" meta={company.source} />
            <MetricGrid>
              <Metric label="总市值" value={yi(company.total_market_cap)} />
              <Metric label="流通市值" value={yi(company.circulating_market_cap)} />
              <Metric label="自由流通市值" value={yi(company.free_float_market_cap)} />
              <Metric label="上市日期" value={text(company.listing_date)} />
              <Metric label="实际控制人" value={text(company.actual_controller)} />
              <Metric label="板块基准" value={text(company.sector_benchmark?.name)} detail={company.sector_benchmark?.code} />
            </MetricGrid>
            <div className="mt-3 text-[10px] text-text-secondary">自由流通口径：{company.free_float_method || 'F10自由流通股本'}</div>
            <div className="mt-5 grid gap-5 border-t border-border pt-4 lg:grid-cols-2">
              <div><div className="text-[10px] text-text-secondary">主营业务</div><p className="mt-1 text-xs leading-5 text-text">{text(company.main_business, '公司简介按最近公开披露')}</p></div>
              <div><div className="text-[10px] text-text-secondary">核心产品</div><p className="mt-1 text-xs leading-5 text-text">{(company.core_products || []).join('、') || '公司未在当前主营构成中单列产品名称'}</p></div>
            </div>
            <div className="mt-3 text-[10px] leading-4 text-text-secondary">核心客户：{company.core_customers?.disclosure_status || '公司未公开披露客户名称，系统不依据传闻补造'}</div>
          </section>

          <section className="border-b border-border py-5">
            <SectionTitle icon={Gauge} title="盈利与质量" meta={`报告期 ${fundamentals.report_date || '最近已披露'} · 公告日 ${fundamentals.disclosed_at || '已核验'}`} />
            <MetricGrid>
              <Metric label="盈利状态" value={text(fundamentals.earnings_state, '按财报核验')} />
              <Metric label="盈利质量" value={text(fundamentals.earnings_quality, '按现金流核验')} detail={`评分 ${number(fundamentals.earnings_quality_score, 1)}`} />
              <Metric label="营收同比" value={signed(fm.revenue_growth_pct)} valueClass={tone(fm.revenue_growth_pct)} />
              <Metric label="净利润同比" value={signed(fm.net_profit_growth_pct)} valueClass={tone(fm.net_profit_growth_pct)} />
              <Metric label="扣非利润同比" value={signed(fm.deducted_profit_growth_pct)} valueClass={tone(fm.deducted_profit_growth_pct)} />
              <Metric label="经营现金流/利润" value={number(fm.operating_cashflow_to_profit, 3)} />
              {finite(fm.roe_pct) && <Metric label="ROE" value={number(fm.roe_pct, 2, '%')} />}
              {finite(fm.gross_margin_pct) && <Metric label="毛利率" value={number(fm.gross_margin_pct, 2, '%')} />}
              {finite(fm.net_margin_ttm_pct) && <Metric label="净利率TTM" value={number(fm.net_margin_ttm_pct, 2, '%')} />}
              {finite(fm.debt_ratio_pct) && <Metric label="资产负债率" value={number(fm.debt_ratio_pct, 2, '%')} />}
              {finite(fm.accounts_receivable_yoy_pct) && <Metric label="应收同比" value={signed(fm.accounts_receivable_yoy_pct)} valueClass={tone(fm.accounts_receivable_yoy_pct)} />}
              {finite(fm.inventory_yoy_pct) && <Metric label="存货同比" value={signed(fm.inventory_yoy_pct)} valueClass={tone(fm.inventory_yoy_pct)} />}
            </MetricGrid>
            <div className="mt-4 border-t border-border pt-3 text-xs leading-5 text-text-secondary">{fundamentals.operating_vs_non_recurring} · 持续性 {fundamentals.earnings_sustainability || '按后续财报验证'}</div>
          </section>

          <section className="border-b border-border py-5">
            <div className="grid gap-7 lg:grid-cols-2 lg:divide-x lg:divide-border">
              <div>
                <SectionTitle icon={BarChart3} title="估值位置" meta={valuation.data_date} />
                <MetricGrid>
                  <Metric label="PE TTM" value={valuation.pe_applicable === false ? '亏损期' : number(valuation.current_pe_ttm)} detail={valuation.pe_applicable === false ? `原始PE ${number(valuation.current_pe_ttm)}` : undefined} />
                  {finite(valuation.current_pb) && <Metric label="PB" value={number(valuation.current_pb)} />}
                  {finite(valuation.pe_percentile_3y) && <Metric label="三年PE分位" value={number(valuation.pe_percentile_3y, 1, '%')} />}
                  {finite(valuation.industry_pe_percentile) && <Metric label="行业PE分位" value={number(valuation.industry_pe_percentile, 1, '%')} />}
                  {finite(valuation.peg_proxy) && <Metric label="PEG代理" value={number(valuation.peg_proxy, 3)} />}
                  <Metric label="估值状态" value={text(valuation.state, '按历史区间核验')} />
                  {valuation.is_cyclical && <Metric label="周期属性" value={text(valuation.cyclical_sector_label)} detail={`阶段置信度 ${number(valuation.cycle_confidence, 1, '%')}`} />}
                  {valuation.is_cyclical && <Metric label="周期阶段" value={text(valuation.cycle_phase_label)} />}
                  {valuation.is_cyclical && finite(valuation.normalized_pe) && <Metric label="标准化PE" value={number(valuation.normalized_pe)} detail="历史正利润中位数口径" />}
                  {valuation.is_cyclical && finite(valuation.profit_cycle_percentile) && <Metric label="利润周期分位" value={number(valuation.profit_cycle_percentile, 1, '%')} />}
                  {valuation.is_cyclical && finite(valuation.margin_cycle_percentile) && <Metric label="毛利率周期分位" value={number(valuation.margin_cycle_percentile, 1, '%')} />}
                  {valuation.is_cyclical && <Metric label="PE反向风险" value={valuation.pe_inversion_risk ? '已触发' : '未触发'} valueClass={valuation.pe_inversion_risk ? 'text-down' : 'text-up'} />}
                </MetricGrid>
                <div className="mt-3 text-[10px] text-text-secondary">{valuation.pe_resolution} · 三年正PE样本 {valuation.pe_history_samples || 0} 条 · 行业样本 {valuation.industry_positive_pe_samples || 0} 只</div>
                {valuation.is_cyclical && (
                  <div className={`mt-3 border-l-2 px-3 py-2 text-[10px] leading-4 ${valuation.pe_inversion_risk ? 'border-down bg-down/10 text-down' : 'border-accent bg-accent/10 text-text-secondary'}`}>
                    <div>{valuation.valuation_method} · {valuation.pb_roe_signal}</div>
                    {(valuation.cycle_evidence || []).length > 0 && <div className="mt-1">依据：{valuation.cycle_evidence.join('；')}</div>}
                    {(valuation.cycle_warnings || []).length > 0 && <div className="mt-1">提示：{valuation.cycle_warnings.join('；')}</div>}
                  </div>
                )}
              </div>
              <div className="lg:pl-7">
                <SectionTitle icon={ShieldAlert} title="风险收益" meta="20日边界 + ATR情景" />
                <MetricGrid>
                  <Metric label="潜在上行" value={number(risk.potential_upside_pct, 2, '%')} />
                  <Metric label="潜在下行" value={number(risk.potential_downside_pct, 2, '%')} />
                  <Metric label="RR" value={number(risk.risk_reward_ratio, 2)} />
                  <Metric label="支撑" value={number(risk.support)} />
                  <Metric label="阻力" value={number(risk.resistance)} />
                  <Metric label="60日最大回撤" value={number(risk.max_drawdown_60d_pct, 2, '%')} />
                </MetricGrid>
                <div className="mt-3 text-[10px] text-text-secondary">估值风险 {risk.valuation_risk}（{risk.valuation_risk_reason || '历史分位口径'}） · 拥挤风险 {risk.crowding_risk} · 牛/基准/熊 {number(risk.scenarios?.bull)} / {number(risk.scenarios?.base)} / {number(risk.scenarios?.bear)}</div>
              </div>
            </div>
          </section>

          <section className="border-b border-border py-5">
            <SectionTitle icon={Activity} title="资金冲击与上涨归因" meta={capital.source} />
            <div className="grid gap-6 xl:grid-cols-[0.8fr_1.2fr]">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-xs">
                  <thead className="text-text-secondary"><tr><th className="py-2 text-left font-medium">窗口</th><th className="py-2 text-right font-medium">观察数</th><th className="py-2 text-right font-medium">主力净流入</th><th className="py-2 text-right font-medium">冲击率</th></tr></thead>
                  <tbody>{(capital.windows || []).map((row: AnyMap) => <tr key={row.days} className="border-t border-border"><td className="py-2.5 text-text">{row.days}日</td><td className="py-2.5 text-right text-text-secondary">{row.observations}</td><td className={`py-2.5 text-right font-mono ${tone(row.main_net_inflow)}`}>{finite(row.main_net_inflow) ? formatYi(row.main_net_inflow) : '窗口内无交易记录'}</td><td className="py-2.5 text-right font-mono text-text">{number(row.impact_ratio_pct, 4, '%', '窗口内无交易记录')}</td></tr>)}</tbody>
                </table>
                <div className="mt-3 text-[10px] text-text-secondary">近5日正流入 {capital.positive_days_5d ?? 0} 日 · {capital.persistence}</div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] text-xs">
                  <thead className="text-text-secondary"><tr><th className="py-2 text-left font-medium">窗口</th><th className="py-2 text-right font-medium">个股</th><th className="py-2 text-right font-medium">大盘</th><th className="py-2 text-right font-medium">板块</th><th className="py-2 text-right font-medium">市场贡献</th><th className="py-2 text-right font-medium">板块贡献</th><th className="py-2 text-right font-medium">个股Alpha</th></tr></thead>
                  <tbody>{(attribution.windows || []).map((row: AnyMap) => <tr key={row.days} className="border-t border-border"><td className="py-2.5 text-text">{row.days}日</td>{['stock_return_pct', 'market_return_pct', 'sector_return_pct', 'market_contribution_pct', 'sector_contribution_pct', 'individual_alpha_pct'].map((key) => <td key={key} className={`py-2.5 text-right font-mono ${tone(row[key])}`}>{signed(row[key])}</td>)}</tr>)}</tbody>
                </table>
                <div className="mt-3 text-[10px] text-text-secondary">OLS样本 {attribution.sample_count || 0} · 市场Beta {number(attribution.market_beta, 4)} · 板块Beta {number(attribution.sector_beta, 4)} · 资金Beta {number(attribution.fund_flow_beta, 4)}</div>
              </div>
            </div>
          </section>

          <section className="border-b border-border py-5">
            <SectionTitle icon={Layers3} title="板块角色与个股情绪" />
            <div className="grid gap-6 md:grid-cols-3 md:divide-x md:divide-border">
              <div>
                <div className="text-xs font-medium text-text">{sectorRole.sector} · {sectorRole.role}</div>
                <div className="mt-3 space-y-1.5 text-xs text-text-secondary"><div>角色迁移：{sectorRole.role_migration}</div><div>涨幅排名：{number(sectorRole.change_rank, 0, '', '板块样本不足')} / {number(sectorRole.member_count, 0, '', '板块样本不足')}</div><div>资金排名：{number(sectorRole.fund_flow_rank, 0, '', '板块样本不足')} / {number(sectorRole.member_count, 0, '', '板块样本不足')}</div><div>市值排名：{number(sectorRole.market_cap_rank, 0, '', '板块样本不足')} / {number(sectorRole.member_count, 0, '', '板块样本不足')}</div></div>
              </div>
              <div className="md:pl-6">
                <div className="text-xs font-medium text-text">板块依赖 {dependency.dependency_level}</div>
                <div className="mt-3 space-y-1.5 text-xs text-text-secondary"><div>依赖度：{number(dependency.dependency_score, 1)}</div><div>60日相关：{number(dependency.correlation_60d, 4)}</div><div>独立性：{dependency.independence_level}</div><div>退潮韧性：{dependency.sector_retreat_resilience}</div><div>基准：{dependency.benchmark?.name} {dependency.benchmark?.code}</div></div>
              </div>
              <div className="md:pl-6">
                <div className="text-xs font-medium text-text">情绪 {emotion.level} · {emotion.trend}</div>
                <div className="mt-3 space-y-1.5 text-xs text-text-secondary"><div>情绪分：{number(emotion.score, 1)}</div><div>速度：{signed(emotion.velocity, 1, '')}</div><div>加速度：{signed(emotion.acceleration, 1, '')}</div><div>量比：{number(emotion.observations?.volume_ratio)}</div><div>距20日高点：{signed(emotion.observations?.drawdown_from_20d_high_pct)}</div></div>
              </div>
            </div>
          </section>

          <section className="border-b border-border py-5">
            <div className="grid gap-7 lg:grid-cols-2 lg:divide-x lg:divide-border">
              <div>
                <SectionTitle icon={Sparkles} title="催化剂" meta={`最高等级 ${catalysts.highest_grade}`} />
                {(catalysts.items || []).length ? <div className="divide-y divide-border">{catalysts.items.slice(0, 6).map((item: AnyMap, index: number) => <div key={`${item.title}-${index}`} className="py-2.5"><div className="flex items-start justify-between gap-3"><div className="text-xs leading-5 text-text">{item.title}</div><span className={`shrink-0 text-[10px] ${item.direction === 'positive' ? 'text-up' : item.direction === 'negative' ? 'text-down' : 'text-text-secondary'}`}>{item.grade}级 · {item.direction}</span></div><div className="mt-1 text-[10px] text-text-secondary">{item.published_at || '官方发布日按源核验'} · {item.realisation}</div></div>)}</div> : <div className="text-xs text-text-secondary">{catalysts.resolution}</div>}
              </div>
              <div className="lg:pl-7">
                <SectionTitle icon={Target} title="预期差" meta="当前一致预期快照不进入历史回测" />
                <MetricGrid>
                  <Metric label="状态" value={text(expectation.state, '按当前一致预期观察')} />
                  {finite(expectation.analyst_count) && <Metric label="分析师数" value={number(expectation.analyst_count, 0)} />}
                  {expectationCovered && finite(expectation.expected_eps_growth_pct) && <Metric label="预期EPS增速" value={number(expectation.expected_eps_growth_pct, 2, '%')} />}
                  {finite(expectation.latest_actual_profit_growth_pct) && <Metric label="实际利润增速" value={number(expectation.latest_actual_profit_growth_pct, 2, '%')} />}
                  {expectationCovered && finite(expectation.expectation_gap_proxy_pct) && <Metric label="预期差代理" value={number(expectation.expectation_gap_proxy_pct, 2, '%')} />}
                  {expectationCovered && finite(expectation.target_price_range?.[0]) && finite(expectation.target_price_range?.[1]) && <Metric label="目标价区间" value={`${number(expectation.target_price_range[0])} - ${number(expectation.target_price_range[1])}`} />}
                </MetricGrid>
                <div className="mt-4 text-[10px] leading-4 text-text-secondary">{expectation.warning}</div>
              </div>
            </div>
          </section>

          <section className="border-b border-border py-5">
            <SectionTitle icon={CheckCircle2} title="策略适配" meta="量比主阈值 > 1.2" />
            <div className="grid gap-6 lg:grid-cols-[0.7fr_1.3fr]">
              <MetricGrid>
                <Metric label="长线" value={strategy.long_term?.fit || '按基本面核验'} detail={`评分 ${number(strategy.long_term?.score, 1)}`} />
                <Metric label="趋势" value={strategy.trend?.fit || '按趋势核验'} detail={`评分 ${number(strategy.trend?.score, 1)}`} />
                <Metric label="14:55" value={strategy.tail_1455?.fit || '当前窗口不适用'} detail={strategy.tail_1455?.phase} />
                <Metric label="竞价确认" value={strategy.auction_confirmation?.fit || '当前窗口不适用'} />
              </MetricGrid>
              <div className="grid gap-2 sm:grid-cols-2">{(strategy.tail_1455?.conditions || []).map((item: AnyMap) => <div key={item.key} className="flex min-h-9 items-center justify-between gap-3 border-b border-border px-1 py-2 text-xs"><span className="text-text-secondary">{item.label}</span><span className={item.passed ? 'text-up' : 'text-warn'}>{item.passed ? '通过' : '未通过'}</span></div>)}</div>
            </div>
          </section>

          {kline && (
            <section className="border-b border-border py-5">
              <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <SectionTitle icon={BarChart3} title={`${kline.stock_name || stockCode} K线`} meta={`${kline.data_date || '最近交易日'} · ${kline.source} · ${kline.is_realtime ? '实时' : '历史/缓存'}`} />
                <div className="inline-flex h-8 self-start overflow-hidden rounded-md border border-border">{CATEGORY_OPTIONS.map((item) => <button type="button" key={item.value} onClick={() => void switchCategory(item.value)} disabled={klineLoading} className={`border-r border-border px-3 text-xs last:border-r-0 ${category === item.value ? 'bg-accent text-white' : 'bg-bg text-text-secondary hover:text-text'} disabled:opacity-50`}>{item.label}</button>)}</div>
              </div>
              <div className="overflow-hidden rounded-md border border-border bg-card">{klineLoading ? <div className="grid h-[360px] place-items-center text-xs text-text-secondary"><RefreshCw size={19} className="animate-spin text-accent" /></div> : <KlineChart rows={kline.rows} height={390} />}</div>
              {kline.warning && <div className="mt-2 text-[10px] text-warn">{kline.warning}</div>}
            </section>
          )}

          <section className="border-b border-border py-5">
            <SectionTitle icon={Database} title="数据审计" meta={`公开源覆盖 ${number(audit.public_source_coverage_pct, 1, '%')}`} />
            {audit.refresh_warning && <div className="mb-3 border-l-2 border-warn bg-warn/10 px-3 py-2 text-xs leading-5 text-warn">{audit.refresh_warning}</div>}
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-xs"><thead className="text-text-secondary"><tr><th className="py-2 text-left font-medium">数据项</th><th className="py-2 text-left font-medium">状态</th><th className="py-2 text-left font-medium">数据日</th><th className="py-2 text-left font-medium">来源</th><th className="py-2 text-left font-medium">核验说明</th></tr></thead><tbody>{auditSources.map((item: AnyMap) => <tr key={item.key} className="border-t border-border"><td className="py-2.5 text-text">{item.label}</td><td className={`py-2.5 ${item.status === 'observed' ? 'text-up' : item.status === 'cached_fallback' ? 'text-accent' : 'text-warn'}`}>{AUDIT_LABELS[item.status] || '已审计'}</td><td className="py-2.5 text-text-secondary">{item.data_date || '按最近披露'}</td><td className="py-2.5 text-text-secondary">{item.source}</td><td className="py-2.5 text-text-secondary">{item.detail}</td></tr>)}</tbody></table>
            </div>
            <div className="mt-4 grid gap-3 text-[10px] leading-4 text-text-secondary md:grid-cols-2"><div>{(audit.legally_not_disclosed || []).join('；')}</div><div>{(audit.not_applicable_now || []).join('；')}</div></div>
          </section>
        </>
      )}

      {flowData.length > 0 && (
        <section className="border-b border-border py-5">
          <SectionTitle icon={Activity} title="近期资金流原始记录" />
          <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-xs"><thead className="text-text-secondary"><tr><th className="py-2 text-left font-medium">日期</th><th className="py-2 text-right font-medium">主力净流入</th><th className="py-2 text-right font-medium">超大单</th><th className="py-2 text-right font-medium">大单</th><th className="py-2 text-right font-medium">中单</th><th className="py-2 text-right font-medium">小单</th></tr></thead><tbody>{flowData.map((row) => <tr key={row.date} className="border-t border-border"><td className="py-2.5 text-text">{row.date}</td><td className={`py-2.5 text-right font-mono ${tone(row.main_net_inflow)}`}>{formatYi(row.main_net_inflow)}</td><td className="py-2.5 text-right font-mono text-text-secondary">{formatYi(row.super_large_net_inflow)}</td><td className="py-2.5 text-right font-mono text-text-secondary">{formatYi(row.large_net_inflow)}</td><td className="py-2.5 text-right font-mono text-text-secondary">{formatYi(row.medium_net_inflow)}</td><td className="py-2.5 text-right font-mono text-text-secondary">{formatYi(row.small_net_inflow)}</td></tr>)}</tbody></table></div>
        </section>
      )}

      {kline?.rows && kline.rows.length > 0 && (
        <section className="py-5">
          <SectionTitle icon={BarChart3} title={`${kline.category_label}明细`} meta={`最近 ${Math.min(kline.rows.length, 30)} 条`} />
          <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-xs"><thead className="text-text-secondary"><tr><th className="py-2 text-left font-medium">日期</th><th className="py-2 text-right font-medium">开盘</th><th className="py-2 text-right font-medium">收盘</th><th className="py-2 text-right font-medium">最高</th><th className="py-2 text-right font-medium">最低</th><th className="py-2 text-right font-medium">涨跌幅</th><th className="py-2 text-right font-medium">成交量</th></tr></thead><tbody>{kline.rows.slice(-30).reverse().map((row) => <tr key={row.date} className="border-t border-border"><td className="py-2.5 text-text">{row.date}</td><td className="py-2.5 text-right font-mono">{number(row.open)}</td><td className="py-2.5 text-right font-mono">{number(row.close)}</td><td className="py-2.5 text-right font-mono text-text-secondary">{number(row.high)}</td><td className="py-2.5 text-right font-mono text-text-secondary">{number(row.low)}</td><td className={`py-2.5 text-right font-mono ${tone(row.change_pct)}`}>{row.change_pct == null ? '基期前无可比收盘' : signed(row.change_pct)}</td><td className="py-2.5 text-right font-mono text-text-secondary">{shares(row.volume)}</td></tr>)}</tbody></table></div>
        </section>
      )}

      {hasSearched && !loading && !profile && !error && <EmptyVerified>该股票的公开数据本次未形成完整核验快照。</EmptyVerified>}
    </main>
  );
}
