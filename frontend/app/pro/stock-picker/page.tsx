'use client';

import { useEffect, useState } from 'react';
import {
  AlertTriangle,
  BrainCircuit,
  ChevronRight,
  CircleAlert,
  Database,
  Gauge,
  Play,
  RefreshCw,
  SearchCheck,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import { apiFetch, formatYi, getChangeColor } from '@/lib/api';

type SelectionMode = 'quick' | 'full';
type RiskProfile = 'conservative' | 'balanced' | 'aggressive';

interface PipelineAgent {
  id: string;
  name: string;
  skill: string;
  status: 'completed' | 'unavailable' | 'waiting' | 'not_configured';
  summary: string;
}

interface AgentReport {
  agent: string;
  skill: string;
  score: number;
  signal?: string;
  verdict?: string;
  confidence?: number;
  summary: string;
  evidence: string[];
  risks: string[];
  available?: boolean;
  sources?: SourceItem[];
  metrics?: Record<string, number | null>;
  plan?: {
    risk_level: string;
    daily_volatility_pct: number;
    max_drawdown_20d_pct: number;
    stop_loss_price: number | null;
    reference_target_price: number | null;
    max_research_position_pct: number;
  };
  debate?: {
    bull_score: number;
    bear_score: number;
    bull_points: string[];
    bear_points: string[];
    decisive_factor: string;
  };
}

interface SourceItem {
  source: string;
  scope?: string;
  title: string;
  published_at?: string | null;
  url: string;
  impact?: 'positive' | 'negative' | 'neutral';
  category?: string;
}

interface SectorOption {
  name: string;
  candidate_count: number;
}

interface Recommendation {
  rank: number;
  code: string;
  name: string;
  sector: string;
  price: number;
  change_pct: number;
  turnover: number;
  market_cap: number;
  selection_sources: string[];
  score: number;
  verdict: string;
  confidence: number;
  agents: {
    technical: AgentReport;
    fundamental: AgentReport;
    capital: AgentReport;
    risk: AgentReport;
    news: AgentReport;
    supervisor: AgentReport;
  };
}

interface SelectionResult {
  available: boolean;
  source: string;
  is_realtime: boolean;
  data_date: string | null;
  updated_at: string;
  mode: SelectionMode;
  risk_profile: RiskProfile;
  risk_profile_label: string;
  market_regime: {
    regime: string;
    confidence: number;
    bias: string;
    positive_days_10?: number;
    total_inflow_10d?: number;
  };
  candidate_summary: {
    live_candidates: number;
    market_candidates?: number;
    analyzed: number;
    selected: number;
  };
  sector_filter?: {
    value: string;
    label: string;
    matched_candidates: number;
    market_candidates: number;
  };
  macro_policy?: {
    available: boolean;
    updated_at: string;
    summary: string;
    international_items: SourceItem[];
    policy_items: SourceItem[];
    source_status: Record<string, string>;
    macro_adjustment: number;
    announcement_coverage: number;
    announcement_requested: number;
  };
  agent_pipeline: PipelineAgent[];
  recommendations: Recommendation[];
  message: string;
  disclaimer: string;
  run_id?: number | null;
  trace_available?: boolean;
}

const modes: Array<{ id: SelectionMode; label: string; detail: string }> = [
  { id: 'quick', label: '快速扫描', detail: '来源候选池 + 多维交叉验证' },
  { id: 'full', label: '深度扫描', detail: '扩大候选池并完整保留研究轨迹' },
];

const profiles: Array<{ id: RiskProfile; label: string }> = [
  { id: 'conservative', label: '稳健' },
  { id: 'balanced', label: '均衡' },
  { id: 'aggressive', label: '进取' },
];

const statusClass: Record<PipelineAgent['status'], string> = {
  completed: 'border-[#26A69A55] bg-[#26A69A18] text-down',
  unavailable: 'border-[#EF535055] bg-[#EF535018] text-up',
  waiting: 'border-[#D2992255] bg-[#D2992218] text-warn',
  not_configured: 'border-border bg-[#21262D] text-text-secondary',
};

function verdictClass(verdict: string): string {
  if (verdict === '优先研究') return 'bg-[#26A69A22] text-down border-[#26A69A44]';
  if (verdict === '持续跟踪') return 'bg-[#58A6FF22] text-accent border-[#58A6FF44]';
  return 'bg-[#D2992222] text-warn border-[#D2992244]';
}

function scoreClass(score: number): string {
  if (score >= 72) return 'text-down';
  if (score >= 58) return 'text-accent';
  return 'text-warn';
}

function formatTime(value: string): string {
  if (!value) return '--';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value));
}

function sourceLabel(source: string): string {
  if (source === 'fund_flow') return '资金流';
  if (source === 'volume') return '量比';
  if (source === 'momentum') return '动量';
  if (source === 'ftshare_market') return 'FTShare 行情';
  return source;
}

function marketSourceLabel(source: string): string {
  if (source === 'eastmoney') return '东方财富';
  if (source === 'ftshare_mcp') return 'FTShare MCP';
  return source;
}

function sourceImpactClass(impact?: SourceItem['impact']): string {
  if (impact === 'positive') return 'text-down';
  if (impact === 'negative') return 'text-up';
  return 'text-text-secondary';
}

function sourceDate(value?: string | null): string {
  return value ? value.slice(0, 10) : '日期未披露';
}

export default function StockPickerPage() {
  const [mode, setMode] = useState<SelectionMode>('quick');
  const [riskProfile, setRiskProfile] = useState<RiskProfile>('balanced');
  const [topN, setTopN] = useState(5);
  const [sector, setSector] = useState('');
  const [sectors, setSectors] = useState<SectorOption[]>([]);
  const [sectorsLoading, setSectorsLoading] = useState(true);
  const [result, setResult] = useState<SelectionResult | null>(null);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    const loadSectors = async () => {
      try {
        const res = await apiFetch<{ code: number; data: { sectors: SectorOption[] } }>('/stock-selection/sectors');
        if (active) setSectors(res.data.sectors || []);
      } catch (err) {
        console.error('Failed to load stock-selection sectors:', err);
      } finally {
        if (active) setSectorsLoading(false);
      }
    };

    loadSectors();
    return () => { active = false; };
  }, []);

  const runSelection = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch<{ code: number; data: SelectionResult }>('/stock-selection/run', {
        method: 'POST',
        body: JSON.stringify({ mode, risk_profile: riskProfile, top_n: topN, sector: sector || undefined }),
      });
      setResult(res.data);
      setSelectedCode(res.data.recommendations[0]?.code ?? null);
    } catch (err) {
      console.error('Failed to run stock selection:', err);
      setError('实时选股暂时无法完成，请稍后重试。');
    } finally {
      setLoading(false);
    }
  };

  const recommendations = result?.recommendations || [];
  const selected = recommendations.find((item) => item.code === selectedCode) || recommendations[0];
  const macroSources = result?.macro_policy
    ? [...result.macro_policy.international_items, ...result.macro_policy.policy_items]
    : [];
  const agentPanels: Array<{ key: keyof Recommendation['agents']; icon: typeof Gauge }> = [
    { key: 'technical', icon: TrendingUp },
    { key: 'fundamental', icon: Target },
    { key: 'capital', icon: Database },
    { key: 'risk', icon: ShieldCheck },
    { key: 'news', icon: BrainCircuit },
  ];

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 overflow-x-hidden">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-text flex items-center gap-2">
            <BrainCircuit size={22} className="text-warn" />
            智能选股 Agent
          </h1>
          <p className="text-text-secondary text-sm mt-1">来源候选池 · 多空交叉验证 · 风险约束</p>
        </div>
        {result && (
          <div className="text-right text-xs text-text-secondary leading-5">
            <div className="flex items-center justify-end gap-1.5">
              <span className={`inline-block h-2 w-2 rounded-full ${result.is_realtime ? 'bg-down' : 'bg-warn'}`} />
              {result.is_realtime ? '盘中实时行情' : result.data_date ? `最近交易快照 · 数据日期 ${result.data_date}` : '来源快照 · 未提供日期'}
            </div>
            <div>{marketSourceLabel(result.source)} · 本次运行 {formatTime(result.updated_at)}</div>
          </div>
        )}
      </div>

      <section className="border border-border bg-card rounded-lg p-4 mb-6">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-4 items-end">
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_auto_auto_auto] gap-4 items-end">
            <div>
              <div className="text-xs text-text-secondary mb-1.5">运行模式</div>
              <div className="grid grid-cols-2 gap-1 rounded-md bg-[#0D1117] p-1" role="group" aria-label="运行模式">
                {modes.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setMode(item.id)}
                    className={`min-h-12 px-3 text-left rounded transition-colors ${mode === item.id ? 'bg-[#1F6FEB33] text-text' : 'text-text-secondary hover:text-text'}`}
                  >
                    <div className="text-sm font-medium">{item.label}</div>
                    <div className="text-xs mt-0.5 opacity-80 leading-4">{item.detail}</div>
                  </button>
                ))}
              </div>
            </div>
            <div>
              <div className="text-xs text-text-secondary mb-1.5">风险偏好</div>
              <div className="flex rounded-md bg-[#0D1117] p-1" role="group" aria-label="风险偏好">
                {profiles.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setRiskProfile(item.id)}
                    className={`px-3 py-2 text-sm rounded transition-colors ${riskProfile === item.id ? 'bg-[#1F6FEB33] text-accent' : 'text-text-secondary hover:text-text'}`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
            <label className="block">
              <span className="block text-xs text-text-secondary mb-1.5">行业板块</span>
              <select
                value={sector}
                onChange={(event) => setSector(event.target.value)}
                className="w-full min-w-32 bg-[#0D1117] border border-border rounded-md px-3 py-2 text-sm text-text focus:outline-none focus:border-accent"
                aria-label="按行业板块筛选"
              >
                <option value="">全部行业</option>
                {sectors.map((item) => (
                  <option key={item.name} value={item.name}>{item.name} ({item.candidate_count})</option>
                ))}
              </select>
              {sectorsLoading && <span className="block text-xs text-text-secondary mt-1">正在加载行业</span>}
            </label>
            <label className="block">
              <span className="block text-xs text-text-secondary mb-1.5">入选数量</span>
              <select
                value={topN}
                onChange={(event) => setTopN(Number(event.target.value))}
                className="w-full min-w-24 bg-[#0D1117] border border-border rounded-md px-3 py-2 text-sm text-text focus:outline-none focus:border-accent"
              >
                {[3, 5, 8, 10].map((value) => <option key={value} value={value}>{value} 只</option>)}
              </select>
            </label>
          </div>
          <button
            type="button"
            onClick={runSelection}
            disabled={loading}
            className="min-h-10 px-4 py-2 bg-accent text-white text-sm rounded-md hover:opacity-90 disabled:opacity-50 transition-colors flex items-center justify-center gap-1.5"
          >
            {loading ? <RefreshCw size={15} className="animate-spin" /> : <Play size={15} />}
            {loading ? 'Agent 分析中...' : '开始实时选股'}
          </button>
        </div>
      </section>

      {error && (
        <div className="border border-[#EF535055] bg-[#EF535018] rounded-lg px-4 py-3 text-sm text-up flex items-center gap-2 mb-6">
          <CircleAlert size={16} /> {error}
        </div>
      )}

      {loading && (
        <div className="py-16 text-center text-text-secondary">
          <div className="animate-spin w-8 h-8 border-2 border-accent border-t-transparent rounded-full mx-auto mb-3" />
          正在汇集实时行情与 Agent 结论...
        </div>
      )}

      {!loading && result && (
        <>
          <section className="mb-6 border-b border-border pb-5">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="border border-border bg-card rounded-lg p-3">
                <div className="text-xs text-text-secondary">市场环境</div>
                <div className="mt-1 text-lg font-bold text-text">{result.market_regime.regime}</div>
                <div className="text-xs text-text-secondary mt-1">置信度 {(result.market_regime.confidence * 100).toFixed(0)}%</div>
              </div>
              <div className="border border-border bg-card rounded-lg p-3">
                <div className="text-xs text-text-secondary">{result.sector_filter?.label || '全部行业'}候选</div>
                <div className="mt-1 text-lg font-mono font-bold text-text">{result.candidate_summary.live_candidates}</div>
                <div className="text-xs text-text-secondary mt-1">全市场 {result.sector_filter?.market_candidates ?? result.candidate_summary.market_candidates ?? result.candidate_summary.live_candidates} 只</div>
              </div>
              <div className="border border-border bg-card rounded-lg p-3">
                <div className="text-xs text-text-secondary">完成研究</div>
                <div className="mt-1 text-lg font-mono font-bold text-text">{result.candidate_summary.analyzed}</div>
                <div className="text-xs text-text-secondary mt-1">保留分项证据</div>
              </div>
              <div className="border border-border bg-card rounded-lg p-3">
                <div className="text-xs text-text-secondary">风险框架</div>
                <div className="mt-1 text-lg font-bold text-text">{result.risk_profile_label}</div>
                <div className="text-xs text-text-secondary mt-1">研究仓位有上限</div>
              </div>
            </div>
          </section>

          <section className="mb-6">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles size={16} className="text-warn" />
              <h2 className="text-base font-bold text-text">Agent 流水线</h2>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-3">
              {result.agent_pipeline.map((agent) => (
                <article key={agent.id} className="border border-border bg-card rounded-lg p-3 min-h-28">
                  <div className="flex items-start justify-between gap-2">
                    <div className="text-sm font-medium text-text">{agent.name}</div>
                    <span className={`shrink-0 border rounded px-1.5 py-0.5 text-xs ${statusClass[agent.status]}`}>
                      {agent.status === 'completed' ? '已完成' : agent.status === 'not_configured' ? '未接入' : agent.status === 'waiting' ? '等待数据' : '不可用'}
                    </span>
                  </div>
                  <div className="text-xs text-accent mt-2">{agent.skill}</div>
                  <p className="text-xs text-text-secondary leading-5 mt-1.5 break-words">{agent.summary}</p>
                </article>
              ))}
            </div>
          </section>

          {!result.available ? (
            <section className="border border-[#D2992255] bg-[#D2992218] rounded-lg px-4 py-4 text-sm text-warn flex items-start gap-2">
              <AlertTriangle size={17} className="shrink-0 mt-0.5" />
              <div>{result.message}</div>
            </section>
          ) : (
            <>
              <section className="mb-6">
                <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
                  <div>
                    <h2 className="text-base font-bold text-text flex items-center gap-2"><SearchCheck size={16} className="text-accent" />研究优先级</h2>
                    <p className="text-xs text-text-secondary mt-1">{result.sector_filter?.label || '全部行业'} · {result.message}</p>
                  </div>
                  {result.trace_available && <span className="text-xs text-text-secondary">运行记录 #{result.run_id}</span>}
                </div>
                <div className="border border-border bg-card rounded-lg overflow-hidden">
                  <div className="overflow-x-auto">
                    <table className="w-full table-fixed sm:table-auto text-sm">
                      <thead>
                        <tr className="border-b border-border bg-[#0D1117] text-text-secondary text-xs">
                          <th className="w-[40%] sm:w-auto text-left px-2 sm:px-4 py-3 font-medium">排名 / 标的</th>
                          <th className="w-[17%] sm:w-auto text-right px-1 sm:px-3 py-3 font-medium whitespace-nowrap">现价</th>
                          <th className="w-[17%] sm:w-auto text-right px-1 sm:px-3 py-3 font-medium whitespace-nowrap">涨跌幅</th>
                          <th className="w-[16%] sm:w-auto text-right px-1 sm:px-3 py-3 font-medium whitespace-nowrap">综合分</th>
                          <th className="hidden sm:table-cell text-right px-3 py-3 font-medium">技术</th>
                          <th className="hidden sm:table-cell text-right px-3 py-3 font-medium">资金</th>
                          <th className="hidden sm:table-cell text-right px-3 py-3 font-medium">政策</th>
                          <th className="hidden sm:table-cell text-right px-3 py-3 font-medium">风险</th>
                          <th className="hidden sm:table-cell text-left px-3 py-3 font-medium">裁决</th>
                          <th className="w-[10%] sm:w-12 px-1 sm:px-2 py-3"><span className="sr-only">查看</span></th>
                        </tr>
                      </thead>
                      <tbody>
                        {recommendations.map((stock) => {
                          const active = selected?.code === stock.code;
                          return (
                            <tr key={stock.code} className={`border-b border-border/50 transition-colors ${active ? 'bg-[#1F6FEB12]' : 'hover:bg-[#21262D]'}`}>
                              <td className="px-2 sm:px-4 py-3">
                                <div className="flex items-center gap-1.5 sm:gap-2 min-w-0">
                                  <span className="w-4 sm:w-5 shrink-0 text-xs font-mono text-text-secondary">{stock.rank}</span>
                                  <div className="min-w-0">
                                    <div className="font-medium text-text">{stock.name}</div>
                                    <div className="text-xs text-text-secondary leading-4 break-words">{stock.code} · {stock.sector || '行业未标注'} · {stock.selection_sources.map(sourceLabel).join(' / ')}</div>
                                  </div>
                                </div>
                              </td>
                              <td className="px-1 sm:px-3 py-3 text-right font-mono text-xs sm:text-sm text-text whitespace-nowrap">{stock.price.toFixed(2)}</td>
                              <td className={`px-1 sm:px-3 py-3 text-right font-mono text-xs sm:text-sm font-medium whitespace-nowrap ${getChangeColor(stock.change_pct)}`}>{stock.change_pct >= 0 ? '+' : ''}{stock.change_pct.toFixed(2)}%</td>
                              <td className={`px-1 sm:px-3 py-3 text-right font-mono text-xs sm:text-sm font-bold whitespace-nowrap ${scoreClass(stock.score)}`}>{stock.score.toFixed(1)}</td>
                              <td className="hidden sm:table-cell px-3 py-3 text-right font-mono text-text-secondary">{stock.agents.technical.score.toFixed(0)}</td>
                              <td className="hidden sm:table-cell px-3 py-3 text-right font-mono text-text-secondary">{stock.agents.capital.score.toFixed(0)}</td>
                              <td className={`hidden sm:table-cell px-3 py-3 text-right font-mono ${scoreClass(stock.agents.news.score)}`}>{stock.agents.news.score.toFixed(0)}</td>
                              <td className="hidden sm:table-cell px-3 py-3 text-right font-mono text-text-secondary">{stock.agents.risk.score.toFixed(0)}</td>
                              <td className="hidden sm:table-cell px-3 py-3"><span className={`inline-flex border rounded px-2 py-1 text-xs whitespace-nowrap ${verdictClass(stock.verdict)}`}>{stock.verdict}</span></td>
                              <td className="px-1 sm:px-2 py-3 text-right">
                                <button type="button" onClick={() => setSelectedCode(stock.code)} aria-label={`查看${stock.name}的研究轨迹`} title="查看研究轨迹" className="p-1 text-text-secondary hover:text-accent transition-colors">
                                  <ChevronRight size={18} />
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              </section>

              {selected && (
                <section className="border-t border-border pt-6">
                  <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
                    <div>
                      <h2 className="text-lg font-bold text-text flex items-center gap-2">
                        <Target size={18} className="text-warn" /> {selected.name}
                        <span className="text-sm font-mono text-text-secondary">{selected.code}</span>
                      </h2>
                      <p className="text-xs text-text-secondary mt-1">{selected.sector || '行业未标注'} · 置信度 {selected.confidence.toFixed(0)}% · 现价 {selected.price.toFixed(2)} · 换手率 {selected.turnover.toFixed(2)}%</p>
                    </div>
                    <span className={`border rounded px-2.5 py-1 text-sm ${verdictClass(selected.verdict)}`}>{selected.verdict}</span>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-3 mb-5">
                    {agentPanels.map(({ key, icon: Icon }) => {
                      const agent = selected.agents[key];
                      return (
                        <article key={key} className="border border-border bg-card rounded-lg p-4">
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex items-center gap-2 text-sm font-medium text-text"><Icon size={15} className="text-accent" />{agent.agent}</div>
                            <span className={`font-mono font-bold ${scoreClass(agent.score)}`}>{agent.score.toFixed(0)}</span>
                          </div>
                          <p className="text-xs text-text-secondary mt-3 leading-5 min-h-10">{agent.summary}</p>
                          <div className="mt-3 space-y-1.5">
                            {agent.evidence.slice(0, 2).map((item) => <div key={item} className="text-xs text-down leading-4">{item}</div>)}
                            {agent.risks.slice(0, 2).map((item) => <div key={item} className="text-xs text-warn leading-4">{item}</div>)}
                          </div>
                          {agent.sources && agent.sources.length > 0 && (
                            <div className="mt-3 pt-3 border-t border-border space-y-1.5">
                              {agent.sources.slice(0, 2).map((source) => (
                                <a
                                  key={`${source.source}-${source.url}`}
                                  href={source.url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className={`block text-xs leading-4 break-words hover:text-accent transition-colors ${sourceImpactClass(source.impact)}`}
                                >
                                  {source.source} · {source.title}
                                </a>
                              ))}
                            </div>
                          )}
                        </article>
                      );
                    })}
                  </div>

                  {result.macro_policy && (
                    <section className="border border-border bg-card rounded-lg p-4 mb-5">
                      <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
                        <div>
                          <h3 className="text-sm font-bold text-text flex items-center gap-2"><BrainCircuit size={16} className="text-accent" />宏观与国内政策来源</h3>
                          <p className="text-xs text-text-secondary mt-1">{result.macro_policy.summary}</p>
                        </div>
                        <div className="text-xs text-text-secondary">公告覆盖 {result.macro_policy.announcement_coverage}/{result.macro_policy.announcement_requested} 只候选</div>
                      </div>
                      {macroSources.length > 0 ? (
                        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                          {macroSources.slice(0, 9).map((source) => (
                            <a
                              key={`${source.source}-${source.url}`}
                              href={source.url}
                              target="_blank"
                              rel="noreferrer"
                              className="border border-border rounded-md p-3 hover:border-accent transition-colors min-w-0"
                            >
                              <div className="flex items-center justify-between gap-2 text-xs">
                                <span className="text-accent min-w-0 break-words">{source.source}</span>
                                <span className="text-text-secondary shrink-0">{sourceDate(source.published_at)}</span>
                              </div>
                              <p className={`text-xs leading-5 mt-2 break-words ${sourceImpactClass(source.impact)}`}>{source.title}</p>
                            </a>
                          ))}
                        </div>
                      ) : (
                        <div className="text-xs text-text-secondary py-2">本轮未取得可展示的宏观或政策原始链接。</div>
                      )}
                    </section>
                  )}

                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-5">
                    <article className="border border-border bg-card rounded-lg p-4">
                      <div className="flex items-center gap-2 text-sm font-bold text-text mb-3"><TrendingUp size={16} className="text-down" />多空辩论</div>
                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <div className="text-xs text-down mb-2">看多 {selected.agents.supervisor.debate?.bull_score.toFixed(0) ?? '--'}</div>
                          <div className="space-y-2">{selected.agents.supervisor.debate?.bull_points.map((item) => <p key={item} className="text-xs text-text-secondary leading-5">{item}</p>)}</div>
                        </div>
                        <div className="border-l border-border pl-4">
                          <div className="text-xs text-warn mb-2">看空 {selected.agents.supervisor.debate?.bear_score.toFixed(0) ?? '--'}</div>
                          <div className="space-y-2">{selected.agents.supervisor.debate?.bear_points.map((item) => <p key={item} className="text-xs text-text-secondary leading-5">{item}</p>)}</div>
                        </div>
                      </div>
                      <div className="mt-4 pt-3 border-t border-border text-xs text-text-secondary">裁决因素：{selected.agents.supervisor.debate?.decisive_factor || '--'}</div>
                    </article>

                    <article className="border border-border bg-card rounded-lg p-4">
                      <div className="flex items-center gap-2 text-sm font-bold text-text mb-3"><ShieldCheck size={16} className="text-warn" />风险计划</div>
                      <div className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                        <div><div className="text-xs text-text-secondary">风险等级</div><div className="font-medium text-text mt-1">{selected.agents.risk.plan?.risk_level || '--'}</div></div>
                        <div><div className="text-xs text-text-secondary">研究仓位上限</div><div className="font-mono font-medium text-text mt-1">{selected.agents.risk.plan?.max_research_position_pct ?? '--'}%</div></div>
                        <div><div className="text-xs text-text-secondary">止损参考价</div><div className="font-mono font-medium text-warn mt-1">{selected.agents.risk.plan?.stop_loss_price?.toFixed(2) ?? '--'}</div></div>
                        <div><div className="text-xs text-text-secondary">目标参考价</div><div className="font-mono font-medium text-down mt-1">{selected.agents.risk.plan?.reference_target_price?.toFixed(2) ?? '--'}</div></div>
                        <div><div className="text-xs text-text-secondary">20日波动率</div><div className="font-mono text-text mt-1">{selected.agents.risk.plan?.daily_volatility_pct?.toFixed(2) ?? '--'}%</div></div>
                        <div><div className="text-xs text-text-secondary">20日最大回撤</div><div className="font-mono text-text mt-1">{selected.agents.risk.plan?.max_drawdown_20d_pct?.toFixed(1) ?? '--'}%</div></div>
                      </div>
                    </article>
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-text-secondary border-t border-border pt-4">
                    {selected.agents.capital.metrics?.main_net_inflow == null
                      ? <span>主力资金数据未提供</span>
                      : <span>主力净流入 {formatYi(selected.agents.capital.metrics.main_net_inflow)}</span>}
                    <span>日线样本 {selected.agents.technical.metrics?.history_points ?? 0} 条</span>
                    <span>{result.disclaimer}</span>
                  </div>
                </section>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
