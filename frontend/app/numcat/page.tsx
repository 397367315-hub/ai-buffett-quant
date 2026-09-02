'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CalendarDays,
  Check,
  ChevronRight,
  Database,
  FileText,
  Gauge,
  History,
  Layers3,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Table2,
  Wallet,
} from 'lucide-react';
import { apiFetch, friendlyApiError } from '@/lib/api';

type AnyMap = Record<string, any>;

interface CatalogItem {
  apiname: string;
  group: string;
  typed_provider: boolean;
  generic_query: boolean;
  realtime: boolean;
  cache_ttl_seconds: number;
}

interface NumCatStatus {
  configured: boolean;
  official_catalog_version: string;
  official_apiname_count: number;
  typed_provider_count: number;
  typed_provider_missing: string[];
  persistent_raw_storage: boolean;
  gateway: AnyMap;
}

interface QueryPayload {
  apiname: string;
  source: string;
  fetched_at: string;
  cache_policy: string;
  persistent_raw_storage: boolean;
  row_count: number;
  fields: string[] | null;
  data: AnyMap | AnyMap[] | unknown;
  consumer?: string;
}

interface BundleSection {
  available?: boolean;
  rows?: AnyMap[];
  count?: number;
  error?: string;
  source?: string;
}

interface ResearchBundle {
  symbols: string[];
  trade_date: string | null;
  updated_at: string;
  available: boolean;
  partial: boolean;
  errors: string[];
  sections: Record<string, BundleSection>;
}

const GROUP_LABELS: Record<string, string> = {
  all: '全部能力',
  '交易日历': '交易日历',
  '实时快照': '实时行情',
  'Tick历史与竞价边界': '竞价与Tick',
  '集合竞价': '集合竞价',
  '竞价增强': '竞价增强',
  '板块基础': '板块基础',
  '板块盘中': '板块盘中',
  '多周期行情': '周期行情',
  '交易约束': '交易约束',
  '复权': '复权因子',
  '异动历史': '异动历史',
  '交易监管': '监管风控',
  '异动预测': '异动预测',
  '新股': '新股数据',
  '龙虎榜': '龙虎榜',
  '互联互通': '互联互通',
  '两融': '两融数据',
  '财务PIT': '财务 PIT',
  '公司问答': '公司问答',
};

const QUICK_MODULES = [
  { label: '交易日历', description: '交易日与历史口径', icon: CalendarDays, api: 'tradecal', params: '{\n  "recentdays": 10\n}' },
  { label: '板块周期强度', description: '行业、概念和周期板块', icon: Layers3, api: 'themedaily_jx', params: '{\n  "recentdays": 20\n}' },
  { label: '板块资金迁徙', description: '板块主力买卖和分钟变化', icon: Wallet, api: 'themefundflow_jx', params: '{\n  "recentdays": 20\n}' },
  { label: '板块竞价', description: '竞价爆量与异动', icon: Activity, api: 'theme_auc_kp', params: '{\n  "recentdays": 5\n}' },
  { label: '板块异动原因', description: '事件和因果证据', icon: FileText, api: 'theme_reason', params: '{\n  "recentdays": 10\n}' },
  { label: '竞价确认', description: '09:15-09:25 价格量能', icon: Gauge, api: 'daily_auc_detail', params: '{\n  "recentdays": 5\n}' },
  { label: 'Level-2 逐笔', description: '按标的和时间查询', icon: BarChart3, api: 'level2_trade_history', params: '{\n  "symbols": ["600519"],\n  "limit": 100\n}' },
  { label: '监管异动', description: '异动历史与风控证据', icon: ShieldCheck, api: 'limit_event_v2_history', params: '{\n  "recentdays": 30\n}' },
  { label: '财务 PIT', description: '公告时点财务指标', icon: Table2, api: 'finance_indicator', params: '{\n  "symbols": ["600519"],\n  "version": "latest"\n}' },
  { label: '公告新闻', description: '公司公告和实时资讯', icon: FileText, api: 'finance_announcement', params: '{\n  "symbols": ["600519"],\n  "limit": 50\n}' },
  { label: '互联互通', description: '北向、南向资金', icon: Activity, api: 'northbound_flow', params: '{\n  "recentdays": 20\n}' },
  { label: '新股与交易约束', description: '新股、ST、停牌、涨跌停', icon: AlertTriangle, api: 'new_share', params: '{\n  "recentdays": 20\n}' },
] as const;

const BUNDLE_LABELS: Record<string, string> = {
  trade_calendar: '交易日历',
  security_basic: '证券主数据',
  tick: '实时快照',
  auction: '集合竞价',
  last_auction_tick: '竞价末笔 Tick',
  auction_limit_buy: '竞价委买',
  auction_one_price: '竞价一字封单',
  daily: '日线与因子',
  price_limit: '涨跌停价',
  st: 'ST 状态',
  suspend: '停牌状态',
  limit_events: '异动历史',
  finance_indicator: '财务指标',
  finance_income_statement: '利润表',
  finance_cash_flow: '现金流量表',
  finance_forecast: '业绩预告',
  finance_disclosure_date: '披露日期',
  finance_dividend: '分红数据',
};

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function jsonText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '--';
  if (typeof value === 'object') {
    try { return JSON.stringify(value, null, 0); } catch { return String(value); }
  }
  return String(value);
}

function compactNumber(value: unknown): string {
  if (!finite(value)) return jsonText(value);
  const absolute = Math.abs(value);
  if (absolute >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (absolute >= 1e4) return `${(value / 1e4).toFixed(2)}万`;
  return Number.isInteger(value) ? String(value) : value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
}

function rowsFromData(data: unknown): AnyMap[] {
  if (Array.isArray(data)) return data.filter((item): item is AnyMap => Boolean(item && typeof item === 'object' && !Array.isArray(item)));
  if (!data || typeof data !== 'object') return [];
  const object = data as AnyMap;
  const fields = Array.isArray(object.fields) ? object.fields.map(String) : [];
  const items = object.items ?? object.rows ?? object.data;
  if (!Array.isArray(items)) return [];
  return items.map((item: unknown) => {
    if (item && typeof item === 'object' && !Array.isArray(item)) return item as AnyMap;
    if (Array.isArray(item) && fields.length) return Object.fromEntries(fields.map((field, index) => [field, item[index] ?? null]));
    return null;
  }).filter(Boolean) as AnyMap[];
}

function columnsFor(rows: AnyMap[]): string[] {
  const columns: string[] = [];
  for (const row of rows.slice(0, 100)) {
    for (const key of Object.keys(row)) {
      if (!columns.includes(key)) columns.push(key);
      if (columns.length >= 16) return columns;
    }
  }
  return columns;
}

function sourceLabel(status: NumCatStatus | null): string {
  if (!status?.configured) return '未配置猫爪服务端密钥';
  return '猫爪官方接口';
}

function Stat({ label, value, detail, tone = 'text-text' }: { label: string; value: string; detail?: string; tone?: string }) {
  return <div className="min-w-0 border-l-2 border-accent/70 bg-[#151D27] px-3 py-3">
    <div className="truncate text-[10px] text-text-secondary">{label}</div>
    <div className={`mt-1 truncate font-mono text-lg font-semibold ${tone}`} title={value}>{value}</div>
    {detail && <div className="mt-1 truncate text-[10px] text-text-secondary" title={detail}>{detail}</div>}
  </div>;
}

function MetaLine({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-text-secondary">{children}</div>;
}

function Panel({ title, icon: Icon, children, action }: { title: string; icon: typeof Database; children: React.ReactNode; action?: React.ReactNode }) {
  return <section className="min-w-0 overflow-hidden rounded-md border border-border bg-card">
    <header className="flex min-w-0 items-center justify-between gap-3 border-b border-border px-4 py-3">
      <h2 className="flex min-w-0 items-center gap-2 text-sm font-semibold text-text"><Icon size={15} className="shrink-0 text-accent" />{title}</h2>
      {action}
    </header>
    {children}
  </section>;
}

export default function NumCatPage() {
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [status, setStatus] = useState<NumCatStatus | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState('');
  const [group, setGroup] = useState('all');
  const [search, setSearch] = useState('');
  const [realtimeOnly, setRealtimeOnly] = useState(false);
  const [apiName, setApiName] = useState('themedaily_jx');
  const [paramsText, setParamsText] = useState('{\n  "recentdays": 20\n}');
  const [fieldsText, setFieldsText] = useState('');
  const [refreshQuery, setRefreshQuery] = useState(false);
  const [queryLoading, setQueryLoading] = useState(false);
  const [queryError, setQueryError] = useState('');
  const [queryResult, setQueryResult] = useState<QueryPayload | null>(null);
  const [symbolsText, setSymbolsText] = useState('600519,000333');
  const [bundleDate, setBundleDate] = useState('');
  const [bundleFinance, setBundleFinance] = useState(true);
  const [bundleRegulatory, setBundleRegulatory] = useState(true);
  const [bundleMicrostructure, setBundleMicrostructure] = useState(true);
  const [bundleLoading, setBundleLoading] = useState(false);
  const [bundleError, setBundleError] = useState('');
  const [bundle, setBundle] = useState<ResearchBundle | null>(null);
  const queryRef = useRef<HTMLElement | null>(null);

  const loadHub = useCallback(async () => {
    setCatalogLoading(true);
    setCatalogError('');
    try {
      const [statusResponse, catalogResponse] = await Promise.all([
        apiFetch<{ data: NumCatStatus }>('/numcat/status', { timeoutMs: 20000 }),
        apiFetch<{ data: { items: CatalogItem[] } }>('/numcat/catalog', { timeoutMs: 20000 }),
      ]);
      setStatus(statusResponse.data);
      setCatalog(catalogResponse.data.items || []);
    } catch (caught) {
      setCatalogError(friendlyApiError(caught, '猫爪数据中枢暂时无法读取'));
    } finally {
      setCatalogLoading(false);
    }
  }, []);

  useEffect(() => { void loadHub(); }, [loadHub]);

  const groups = useMemo(() => {
    const values = [...new Set(catalog.map((item) => item.group).filter(Boolean))];
    return ['all', ...values];
  }, [catalog]);

  const filteredCatalog = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return catalog.filter((item) => {
      const matchesGroup = group === 'all' || item.group === group;
      const matchesRealtime = !realtimeOnly || item.realtime;
      const matchesSearch = !keyword || item.apiname.toLowerCase().includes(keyword) || item.group.toLowerCase().includes(keyword);
      return matchesGroup && matchesRealtime && matchesSearch;
    });
  }, [catalog, group, realtimeOnly, search]);

  const selectedCatalogItem = catalog.find((item) => item.apiname === apiName);
  const resultRows = useMemo(() => rowsFromData(queryResult?.data), [queryResult]);
  const resultColumns = useMemo(() => columnsFor(resultRows), [resultRows]);
  const cacheBytes = Number(status?.gateway?.cache_bytes || 0);
  const cacheMaxBytes = Number(status?.gateway?.cache_max_bytes || 16 * 1024 * 1024);
  const cachePercent = Math.min(100, Math.max(0, cacheBytes / Math.max(cacheMaxBytes, 1) * 100));

  const applyModule = (module: typeof QUICK_MODULES[number]) => {
    setApiName(module.api);
    setParamsText(module.params);
    setFieldsText('');
    setQueryError('');
    window.requestAnimationFrame(() => queryRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  };

  const runQuery = async () => {
    let params: AnyMap = {};
    if (paramsText.trim()) {
      try {
        const parsed = JSON.parse(paramsText);
        if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('参数必须是JSON对象');
        params = parsed;
      } catch (caught) {
        setQueryError(caught instanceof Error ? caught.message : '参数JSON格式不正确');
        return;
      }
    }
    setQueryLoading(true);
    setQueryError('');
    try {
      const body: AnyMap = { apiname: apiName, params, refresh: refreshQuery };
      if (fieldsText.trim()) body.fields = fieldsText.trim();
      const response = await apiFetch<{ data: QueryPayload }>('/numcat/query', {
        method: 'POST', body: JSON.stringify(body), timeoutMs: 60000,
      });
      setQueryResult(response.data);
    } catch (caught) {
      setQueryError(friendlyApiError(caught, '接口查询失败，请检查参数或服务端配置'));
    } finally {
      setQueryLoading(false);
    }
  };

  const runBundle = async () => {
    const symbols = symbolsText.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean);
    if (!symbols.length) { setBundleError('至少输入一只股票代码'); return; }
    if (symbols.length > 20) { setBundleError('研究包一次最多查询20只股票'); return; }
    setBundleLoading(true);
    setBundleError('');
    try {
      const body = {
        symbols,
        tradedate: bundleDate || undefined,
        include_finance: bundleFinance,
        include_regulatory: bundleRegulatory,
        include_microstructure: bundleMicrostructure,
      };
      const response = await apiFetch<{ data: ResearchBundle }>('/numcat/research-bundle', {
        method: 'POST', body: JSON.stringify(body), timeoutMs: 90000,
      });
      setBundle(response.data);
    } catch (caught) {
      setBundleError(friendlyApiError(caught, '研究包查询失败，请稍后重试'));
    } finally {
      setBundleLoading(false);
    }
  };

  return <div className="min-h-screen bg-bg text-text">
    <div className="mx-auto max-w-[1680px] space-y-4 p-3 sm:p-5">
      <header className="flex min-w-0 flex-wrap items-start justify-between gap-4 border-b border-border pb-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[11px] font-mono text-accent"><Database size={14} />DATA HUB / NUMCAT</div>
          <h1 className="mt-2 text-xl font-semibold tracking-normal text-text sm:text-2xl">猫爪数据中枢</h1>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-text-secondary">统一调度行情、板块、竞价、Level-2、财务 PIT、监管和资金接口，服务现有选股、量化、研究与决策模块。</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 text-[10px] ${status?.configured ? 'border-up/30 text-up' : 'border-warn/30 text-warn'}`}><span className={`h-1.5 w-1.5 rounded-full ${status?.configured ? 'bg-up' : 'bg-warn'}`} />{status?.configured ? '服务已配置' : '等待服务配置'}</span>
          <button type="button" onClick={() => void loadHub()} disabled={catalogLoading} className="command-button"><RefreshCw size={13} className={catalogLoading ? 'animate-spin' : ''} />刷新状态</button>
        </div>
      </header>

      {catalogError && <div className="flex items-start gap-2 rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-xs text-warn"><AlertTriangle size={14} className="mt-0.5 shrink-0" />{catalogError}</div>}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        <Stat label="官方接口" value={String(status?.official_apiname_count ?? (catalog.length || '--'))} detail={`版本 ${status?.official_catalog_version || '--'}`} />
        <Stat label="类型化接入" value={String(status?.typed_provider_count ?? '--')} detail="已有业务模块直接使用" tone="text-up" />
        <Stat label="通用白名单" value={String(catalog.length || '--')} detail="其余接口按需调用" />
        <Stat label="网关请求" value={String(status?.gateway?.usage?.requests ?? 0)} detail={`错误 ${status?.gateway?.usage?.errors ?? 0}`} />
        <Stat label="内存缓存" value={`${cacheBytes >= 1024 * 1024 ? (cacheBytes / 1024 / 1024).toFixed(1) : (cacheBytes / 1024).toFixed(0)} MB`} detail={`${cachePercent.toFixed(0)}% / ${(cacheMaxBytes / 1024 / 1024).toFixed(0)} MB`} />
        <Stat label="原始数据落库" value="关闭" detail="短缓存，进程内受限" tone="text-accent" />
      </div>

      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-4">
          <Panel title="缺失页面能力整合" icon={Layers3} action={<span className="text-[10px] text-text-secondary">快捷进入统一查询</span>}>
            <div className="grid grid-cols-1 gap-px bg-border sm:grid-cols-2 xl:grid-cols-3">
              {QUICK_MODULES.map((module) => {
                const Icon = module.icon;
                const isSelected = apiName === module.api;
                return <button key={module.api} type="button" onClick={() => applyModule(module)} className={`flex min-w-0 items-center gap-3 bg-card px-3 py-3 text-left transition-colors hover:bg-[#18212C] ${isSelected ? 'border-l-2 border-accent bg-[#18212C]' : 'border-l-2 border-transparent'}`}>
                  <Icon size={16} className="shrink-0 text-accent" />
                  <span className="min-w-0"><b className="block truncate text-xs font-medium text-text">{module.label}</b><small className="mt-1 block truncate text-[10px] text-text-secondary">{module.description}</small></span>
                  <ChevronRight size={13} className="ml-auto shrink-0 text-text-secondary" />
                </button>;
              })}
            </div>
            <div className="border-t border-border px-4 py-3"><MetaLine><Check size={12} className="text-up" />已有板块强弱、两融、龙虎榜、研究、量化等页面继续使用现有业务接口</MetaLine><MetaLine><Check size={12} className="text-up" />没有独立页面的官方能力在此集中查询，不额外建立数据仓库</MetaLine></div>
          </Panel>

          <section ref={queryRef} className="scroll-mt-16">
            <Panel title="官方接口查询" icon={SlidersHorizontal} action={<span className="font-mono text-[10px] text-text-secondary">{selectedCatalogItem?.apiname || apiName}</span>}>
              <div className="grid min-w-0 gap-4 p-4 xl:grid-cols-[250px_minmax(0,1fr)]">
                <div className="min-w-0 space-y-3">
                  <label className="block text-[11px] text-text-secondary">选择接口<select value={apiName} onChange={(event) => { setApiName(event.target.value); setQueryError(''); }} className="input mt-1 w-full"><option value="">请选择</option>{catalog.map((item) => <option key={item.apiname} value={item.apiname}>{item.apiname} · {GROUP_LABELS[item.group] || item.group}</option>)}</select></label>
                  {selectedCatalogItem && <div className="space-y-2 border-l-2 border-accent/60 bg-[#151D27] px-3 py-3 text-[10px] text-text-secondary"><div className="flex items-center justify-between gap-2"><span>接入方式</span><b className={selectedCatalogItem.typed_provider ? 'text-up' : 'text-accent'}>{selectedCatalogItem.typed_provider ? '业务已接入' : '通用白名单'}</b></div><div className="flex items-center justify-between gap-2"><span>数据特性</span><b className={selectedCatalogItem.realtime ? 'text-up' : 'text-text'}>{selectedCatalogItem.realtime ? '实时/盘中' : '历史/收盘'}</b></div><div className="flex items-center justify-between gap-2"><span>默认缓存</span><b className="font-mono text-text">{selectedCatalogItem.cache_ttl_seconds}s</b></div></div>}
                  <label className="flex cursor-pointer items-center gap-2 text-[11px] text-text-secondary"><input type="checkbox" checked={refreshQuery} onChange={(event) => setRefreshQuery(event.target.checked)} />强制刷新上游</label>
                  <p className="text-[10px] leading-5 text-text-secondary">接口名来自官方 0.0.481 白名单。查询参数会在后端进行字段、数量和 48KB 大小限制。</p>
                </div>
                <div className="min-w-0 space-y-3">
                  <label className="block text-[11px] text-text-secondary">params JSON<textarea value={paramsText} onChange={(event) => setParamsText(event.target.value)} className="input mt-1 min-h-32 resize-y font-mono text-[11px] leading-5" spellCheck={false} /></label>
                  <label className="block text-[11px] text-text-secondary">fields（可选，逗号分隔）<input value={fieldsText} onChange={(event) => setFieldsText(event.target.value)} className="input mt-1 font-mono text-[11px]" placeholder="留空使用接口默认字段" /></label>
                  <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-[10px] text-text-secondary">响应最多在页面展示前100行，原始响应不持久化。</span><button type="button" onClick={() => void runQuery()} disabled={queryLoading || !apiName} className="command-button command-button-primary"><Search size={13} />{queryLoading ? '查询中' : '查询接口'}</button></div>
                </div>
              </div>
              {queryError && <div className="border-t border-border px-4 py-3 text-xs text-warn">{queryError}</div>}
              {queryResult && <QueryResult result={queryResult} rows={resultRows} columns={resultColumns} />}
            </Panel>
          </section>

          <Panel title="多标的研究包" icon={Sparkles} action={<span className="text-[10px] text-text-secondary">最多20只 · 分区返回</span>}>
            <div className="space-y-4 p-4">
              <div className="grid min-w-0 gap-3 md:grid-cols-[minmax(0,1fr)_150px_auto] md:items-end">
                <label className="block text-[11px] text-text-secondary">股票代码（逗号、空格或换行分隔）<input value={symbolsText} onChange={(event) => setSymbolsText(event.target.value)} className="input mt-1 font-mono text-xs" placeholder="600519, 000333" /></label>
                <label className="block text-[11px] text-text-secondary">指定交易日<input type="date" value={bundleDate} onChange={(event) => setBundleDate(event.target.value)} className="input mt-1 text-xs" /></label>
                <button type="button" onClick={() => void runBundle()} disabled={bundleLoading} className="command-button command-button-primary"><Sparkles size={13} />{bundleLoading ? '研究中' : '生成研究包'}</button>
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-2 text-[11px] text-text-secondary"><label className="flex items-center gap-2"><input type="checkbox" checked={bundleFinance} onChange={(event) => setBundleFinance(event.target.checked)} />财务 PIT</label><label className="flex items-center gap-2"><input type="checkbox" checked={bundleRegulatory} onChange={(event) => setBundleRegulatory(event.target.checked)} />交易约束与监管</label><label className="flex items-center gap-2"><input type="checkbox" checked={bundleMicrostructure} onChange={(event) => setBundleMicrostructure(event.target.checked)} />竞价与微结构</label></div>
              {bundleError && <div className="text-xs text-warn">{bundleError}</div>}
              {bundleLoading && <div className="flex items-center gap-2 border border-border bg-[#151D27] px-3 py-3 text-xs text-text-secondary"><Loader2 size={14} className="animate-spin text-accent" />并行读取行情、竞价、约束、异动和财务分区，部分接口失败不会丢失已完成结果。</div>}
              {bundle && <BundleResult bundle={bundle} />}
              {!bundle && !bundleLoading && <div className="border border-dashed border-border px-4 py-6 text-center text-xs text-text-secondary">输入股票代码后生成一份可追溯的个股研究包。</div>}
            </div>
          </Panel>
        </div>

        <aside className="min-w-0 space-y-4">
          <Panel title="接口目录" icon={Database} action={<span className="font-mono text-[10px] text-text-secondary">{filteredCatalog.length}/{catalog.length}</span>}>
            <div className="space-y-3 p-3">
              <div className="relative"><Search size={14} className="absolute left-2.5 top-2.5 text-text-secondary" /><input value={search} onChange={(event) => setSearch(event.target.value)} className="input w-full pl-8 text-xs" placeholder="搜索接口或分类" /></div>
              <div className="flex gap-1 overflow-x-auto pb-1"><button type="button" onClick={() => setRealtimeOnly(false)} className={`shrink-0 rounded border px-2 py-1 text-[10px] ${!realtimeOnly ? 'border-accent/50 bg-accent/10 text-accent' : 'border-border text-text-secondary'}`}>全部</button><button type="button" onClick={() => setRealtimeOnly(true)} className={`shrink-0 rounded border px-2 py-1 text-[10px] ${realtimeOnly ? 'border-up/50 bg-up/10 text-up' : 'border-border text-text-secondary'}`}>实时</button></div>
              <div className="flex max-h-28 flex-wrap content-start gap-1 overflow-y-auto">{groups.map((item) => <button type="button" key={item} onClick={() => setGroup(item)} className={`rounded border px-2 py-1 text-[10px] ${group === item ? 'border-accent/50 bg-accent/10 text-accent' : 'border-border text-text-secondary hover:text-text'}`}>{GROUP_LABELS[item] || item}</button>)}</div>
              <div className="max-h-[500px] overflow-y-auto border-y border-border">{catalogLoading ? <div className="flex items-center justify-center gap-2 py-8 text-xs text-text-secondary"><Loader2 size={14} className="animate-spin" />读取接口目录</div> : filteredCatalog.length ? filteredCatalog.map((item) => <button type="button" key={item.apiname} onClick={() => { setApiName(item.apiname); setQueryError(''); }} className={`flex w-full min-w-0 items-center gap-2 border-b border-border/70 px-2 py-2 text-left last:border-0 ${apiName === item.apiname ? 'bg-accent/10' : 'hover:bg-[#18212C]'}`}><span className={`h-1.5 w-1.5 shrink-0 rounded-full ${item.realtime ? 'bg-up' : 'bg-[#667085]'}`} /><span className="min-w-0 flex-1 truncate font-mono text-[10px] text-text">{item.apiname}</span><span className={`shrink-0 text-[9px] ${item.typed_provider ? 'text-up' : 'text-text-secondary'}`}>{item.typed_provider ? '已接入' : '通用'}</span></button>) : <div className="py-8 text-center text-xs text-text-secondary">没有匹配接口</div>}</div>
            </div>
          </Panel>

          <Panel title="数据治理状态" icon={ShieldCheck}>
            <div className="space-y-3 p-4 text-[11px]">
              <div className="flex items-start gap-2"><Check size={13} className="mt-0.5 shrink-0 text-up" /><span className="text-text-secondary">API Key 只在后端环境变量中使用，前端不会接收。</span></div>
              <div className="flex items-start gap-2"><Check size={13} className="mt-0.5 shrink-0 text-up" /><span className="text-text-secondary">猫爪原始响应只进入有条目数和字节上限的进程内短缓存。</span></div>
              <div className="flex items-start gap-2"><Check size={13} className="mt-0.5 shrink-0 text-up" /><span className="text-text-secondary">业务模块仍按来源、数据日期和实时性标记使用结果。</span></div>
              <div className="border-t border-border pt-3"><div className="mb-1 flex justify-between gap-2 text-[10px] text-text-secondary"><span>缓存占用</span><span className="font-mono">{cachePercent.toFixed(0)}%</span></div><div className="h-1.5 overflow-hidden rounded-full bg-[#26313D]"><span className="block h-full rounded-full bg-accent transition-[width]" style={{ width: `${cachePercent}%` }} /></div></div>
              <MetaLine><History size={12} />缓存条目 {status?.gateway?.cache_entries ?? 0} · 命中 {status?.gateway?.usage?.cache_hits ?? 0}</MetaLine>
            </div>
          </Panel>

          <Panel title="类型化接入待补" icon={Table2} action={<span className="text-[10px] text-text-secondary">仍可通用查询</span>}>
            <div className="p-4"><div className="flex flex-wrap gap-1.5">{(status?.typed_provider_missing || []).map((item) => <span key={item} className="rounded border border-border px-1.5 py-1 font-mono text-[10px] text-text-secondary">{item}</span>)}</div><p className="mt-3 text-[10px] leading-5 text-text-secondary">“待补”表示尚未写成独立业务 DTO，不代表接口不可用。先通过本页官方通用查询使用，避免为每个接口都增加数据库表和长期存储。</p></div>
          </Panel>
        </aside>
      </div>
    </div>
  </div>;
}

function QueryResult({ result, rows, columns }: { result: QueryPayload; rows: AnyMap[]; columns: string[] }) {
  return <div className="border-t border-border">
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3"><MetaLine><span className="text-text">{result.apiname}</span><span>来源 {result.source}</span><span>抓取 {new Date(result.fetched_at).toLocaleString('zh-CN')}</span><span>记录 {result.row_count}</span><span className="text-accent">内存短缓存</span></MetaLine></div>
    {rows.length ? <div className="overflow-x-auto"><table className="w-full min-w-[720px] border-collapse text-[11px]"><thead><tr className="border-y border-border bg-[#151D27] text-left text-[10px] text-text-secondary">{columns.map((column) => <th key={column} className="whitespace-nowrap px-3 py-2 font-normal">{column}</th>)}</tr></thead><tbody>{rows.slice(0, 100).map((row, index) => <tr key={`${index}-${String(row.symbol || row.code || '')}`} className="border-b border-border/70 align-top hover:bg-[#18212C]">{columns.map((column) => <td key={column} className="max-w-[220px] px-3 py-2 font-mono text-text-secondary" title={jsonText(row[column])}>{compactNumber(row[column])}</td>)}</tr>)}</tbody></table></div> : <pre className="max-h-80 overflow-auto border-t border-border bg-[#0E131A] p-4 font-mono text-[10px] leading-5 text-text-secondary">{JSON.stringify(result.data, null, 2)}</pre>}
    {rows.length > 100 && <div className="px-4 py-2 text-[10px] text-text-secondary">页面仅展示前100行，完整响应不会写入数据库。</div>}
  </div>;
}

function BundleResult({ bundle }: { bundle: ResearchBundle }) {
  const entries = Object.entries(bundle.sections || {});
  return <div className="space-y-3">
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border border-border bg-[#151D27] px-3 py-3"><MetaLine><span className="font-mono text-text">{bundle.symbols.join(', ')}</span><span>更新 {new Date(bundle.updated_at).toLocaleString('zh-CN')}</span><span className={bundle.partial ? 'text-warn' : 'text-up'}>{bundle.partial ? `部分完成 · ${bundle.errors.length}个分区异常` : '全部分区完成'}</span></MetaLine></div>
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">{entries.map(([key, section]) => <div key={key} className={`min-w-0 border-l-2 px-3 py-2 ${section.available ? 'border-up bg-up/5' : 'border-warn bg-warn/5'}`}><div className="truncate text-[10px] text-text-secondary">{BUNDLE_LABELS[key] || key}</div><div className={`mt-1 font-mono text-sm ${section.available ? 'text-up' : 'text-warn'}`}>{section.available ? `${section.count ?? section.rows?.length ?? 0} 条` : '不可用'}</div>{section.error && <div className="mt-1 truncate text-[9px] text-warn" title={section.error}>{section.error}</div>}</div>)}</div>
    <div className="divide-y divide-border border-y border-border">{entries.map(([key, section]) => <details key={key} className="group"><summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2.5 hover:bg-[#18212C]"><span className="text-xs text-text">{BUNDLE_LABELS[key] || key}</span><span className="flex items-center gap-2 text-[10px] text-text-secondary">{section.available ? `${section.count ?? section.rows?.length ?? 0} 条` : '无可用数据'}<ChevronRight size={13} className="transition-transform group-open:rotate-90" /></span></summary>{section.available && section.rows?.length ? <div className="overflow-x-auto border-t border-border bg-[#0E131A]"><table className="w-full min-w-[580px] text-[10px]"><tbody>{section.rows.slice(0, 5).map((row, index) => <tr key={index} className="border-b border-border/70 last:border-0">{Object.entries(row).slice(0, 8).map(([field, value]) => <td key={field} className="px-3 py-2 align-top"><div className="text-[9px] text-text-secondary">{field}</div><div className="mt-1 max-w-[160px] truncate font-mono text-text" title={jsonText(value)}>{compactNumber(value)}</div></td>)}</tr>)}</tbody></table>{(section.count || 0) > 5 && <div className="px-3 py-2 text-[10px] text-text-secondary">仅展示前5行，单接口可在上方查询区查看。</div>}</div> : <div className="border-t border-border px-3 py-3 text-[10px] text-text-secondary">{section.error || '当前没有返回记录，未用零值替代。'}</div>}</details>)}</div>
  </div>;
}
