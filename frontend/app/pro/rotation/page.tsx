'use client';

import { useEffect, useState } from 'react';
import { apiFetch, formatYi, formatYiShort, getChangeColor } from '@/lib/api';
import {
  TrendingUp, TrendingDown, Flame, BarChart3, HelpCircle, ArrowUp, ArrowDown, Zap, BrainCircuit, Loader2
} from 'lucide-react';

interface SectorItem {
  code: string;
  name: string;
  change_pct: number;
  main_net_inflow: number;
  super_large_inflow: number;
  large_inflow: number;
  up_count: number;
  down_count: number;
}

interface RotationData {
  sectors: SectorItem[];
  hot_inflow: SectorItem[];
  hot_outflow: SectorItem[];
  hot_gainers: SectorItem[];
  outflow_data_available?: boolean;
}

type AnalysisWindow = 'week' | 'two_weeks' | 'month' | 'quarter' | 'year';
interface RotationAnalysis {
  available: boolean;
  window: { id: AnalysisWindow; label: string; sessions: number };
  period: { start: string | null; end: string | null };
  coverage: { actual_sessions: number; requested_sessions: number; board_count: number; complete: boolean };
  analysis: {
    score: number; tone: string; headline: string; summary: string;
    aggregate_inflow: number; latest_breadth_pct: number; concentration_top3_pct: number;
    top_inflows: SectorItem[]; top_outflows: SectorItem[]; suggestions: string[]; risks: string[];
  };
  ai_narrative: string | null;
  ai_generated: boolean;
}

const WINDOWS: Array<{ id: AnalysisWindow; label: string }> = [
  { id: 'week', label: '一周' }, { id: 'two_weeks', label: '半个月' },
  { id: 'month', label: '一个月' }, { id: 'quarter', label: '一个季度' }, { id: 'year', label: '一年' },
];

export default function RotationPage() {
  const [data, setData] = useState<RotationData | null>(null);
  const [loading, setLoading] = useState(true);
  const [analysisWindow, setAnalysisWindow] = useState<AnalysisWindow>('week');
  const [analysis, setAnalysis] = useState<RotationAnalysis | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  const fetchData = async () => {
    try {
      const res = await apiFetch<any>('/flow/rotation');
      setData(res.data);
    } catch (err) {
      console.error('Failed to fetch rotation data:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, 60000);
    return () => clearInterval(timer);
  }, []);

  const runAnalysis = async (window: AnalysisWindow) => {
    setAnalysisWindow(window);
    setAnalysisLoading(true);
    setAnalysisError(null);
    try {
      const response = await apiFetch<{ data: RotationAnalysis }>('/flow/rotation/analysis', {
        method: 'POST', body: JSON.stringify({ window, use_ai: true }),
      });
      setAnalysis(response.data);
    } catch (caught) {
      setAnalysisError(caught instanceof Error ? caught.message : '板块资金分析暂时不可用');
    } finally {
      setAnalysisLoading(false);
    }
  };

  useEffect(() => {
    void runAnalysis('week');
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-text-secondary text-center">
          <div className="animate-spin w-8 h-8 border-2 border-accent border-t-transparent rounded-full mx-auto mb-3" />
          <span>数据加载中...</span>
        </div>
      </div>
    );
  }

  if (!data || !data.sectors || data.sectors.length === 0) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-text-secondary text-center">
          <BarChart3 size={24} className="mx-auto mb-2 opacity-50" />
          <span>暂无板块轮动数据</span>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text flex items-center gap-2">
          <Zap size={22} className="text-warn" />
          行业轮动追踪
        </h1>
        <p className="text-text-secondary text-sm mt-1">
          追踪主力资金在板块间的流向变化，发现资金轮动规律
        </p>
      </div>

      <section className="border border-border rounded-lg overflow-hidden mb-6">
        <div className="px-4 py-3 border-b border-border flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold text-text flex items-center gap-2"><BrainCircuit size={16} className="text-accent" />板块资金 AI 分析</h2>
          <span className="text-[11px] text-text-secondary">按缓存交易日聚合，实时行情不可用时仍可查看历史证据</span>
        </div>
        <div className="flex flex-wrap gap-1.5 p-3 border-b border-border">
          {WINDOWS.map((item) => <button key={item.id} type="button" onClick={() => runAnalysis(item.id)} disabled={analysisLoading} className={`px-3 py-1.5 rounded border text-xs ${analysisWindow === item.id ? 'border-accent bg-accent/15 text-accent' : 'border-border text-text-secondary hover:text-text'} disabled:opacity-60`}>{item.label}</button>)}
        </div>
        {analysisLoading && <div className="px-4 py-6 text-xs text-text-secondary flex items-center gap-2"><Loader2 size={15} className="animate-spin text-accent" />正在读取{WINDOWS.find((item) => item.id === analysisWindow)?.label}缓存并核对资金连续性...</div>}
        {analysisError && <div className="m-3 border border-down/50 bg-[#EF535018] rounded p-3 text-xs text-down">{analysisError}</div>}
        {analysis && !analysisLoading && <div className="p-4 space-y-3"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-sm font-semibold text-text">{analysis.analysis.headline}</div><div className="text-xs text-text-secondary mt-1">{analysis.analysis.summary}</div></div><div className="text-right"><div className="font-mono text-lg text-accent">{analysis.analysis.score.toFixed(1)}</div><div className="text-[11px] text-text-secondary">资金状态：{analysis.analysis.tone}</div></div></div><div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs"><div className="border border-border rounded p-2"><div className="text-text-secondary">累计净流入</div><div className="font-mono text-text mt-1">{formatYi(analysis.analysis.aggregate_inflow)}</div></div><div className="border border-border rounded p-2"><div className="text-text-secondary">最新流入广度</div><div className="font-mono text-text mt-1">{analysis.analysis.latest_breadth_pct.toFixed(1)}%</div></div><div className="border border-border rounded p-2"><div className="text-text-secondary">前3集中度</div><div className="font-mono text-text mt-1">{analysis.analysis.concentration_top3_pct.toFixed(1)}%</div></div><div className="border border-border rounded p-2"><div className="text-text-secondary">数据覆盖</div><div className="font-mono text-text mt-1">{analysis.coverage.actual_sessions}/{analysis.coverage.requested_sessions}日</div></div></div><div className="grid gap-3 md:grid-cols-3 text-xs"><div><div className="text-text-secondary mb-1">AI/规则结论</div><div className="text-text leading-5 whitespace-pre-line">{analysis.ai_narrative || '当前使用规则审计结论，AI服务不可用时不影响数据分析。'}</div></div><div><div className="text-text-secondary mb-1">观察建议</div><div className="space-y-1 text-up">{analysis.analysis.suggestions.map((item) => <div key={item}>{item}</div>)}</div></div><div><div className="text-text-secondary mb-1">风险</div><div className="space-y-1 text-warn">{analysis.analysis.risks.length ? analysis.analysis.risks.map((item) => <div key={item}>{item}</div>) : <div>暂未发现额外风险，但不能据此保证收益。</div>}</div></div></div></div>}
      </section>

      {/* 资金流入 TOP5 */}
      {data.hot_inflow && data.hot_inflow.length > 0 && (
        <div className="mb-6">
          <h3 className="text-lg font-bold text-up mb-3 flex items-center gap-2">
            <ArrowUp size={18} />
            🔥 资金流入 TOP5
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
            {data.hot_inflow.map((item, i) => (
              <div
                key={item.code}
                className="bg-card border border-border rounded-lg p-4 hover:border-[#EF535044] transition-colors"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-text-secondary font-mono">#{i + 1}</span>
                  <span className={`text-xs font-bold ${item.change_pct >= 0 ? 'text-up' : 'text-down'}`}>
                    {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                  </span>
                </div>
                <div className="text-sm font-bold text-text mb-1.5 truncate" title={item.name}>
                  {item.name}
                </div>
                <div className="text-base font-mono font-bold text-up">
                  {formatYiShort(item.main_net_inflow)}
                </div>
                <div className="text-xs text-text-secondary mt-1">
                  涨{item.up_count} / 跌{item.down_count}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 资金流出 TOP5 */}
      {data.hot_outflow && data.hot_outflow.length > 0 && (
        <div className="mb-6">
          <h3 className="text-lg font-bold text-down mb-3 flex items-center gap-2">
            <ArrowDown size={18} />
            🧊 资金流出 TOP5
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
            {data.hot_outflow.map((item, i) => (
              <div
                key={item.code}
                className="bg-card border border-[#26A69A44] rounded-lg p-4 hover:border-[#26A69A77] transition-colors"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-text-secondary font-mono">#{i + 1}</span>
                  <span className={`text-xs font-bold ${item.change_pct >= 0 ? 'text-up' : 'text-down'}`}>
                    {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                  </span>
                </div>
                <div className="text-sm font-bold text-text mb-1.5 truncate" title={item.name}>
                  {item.name}
                </div>
                <div className="text-base font-mono font-bold text-down">
                  {formatYiShort(item.main_net_inflow)}
                </div>
                <div className="text-xs text-text-secondary mt-1">
                  涨{item.up_count} / 跌{item.down_count}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      {(!data.hot_outflow || data.hot_outflow.length === 0 || data.outflow_data_available === false) && <div className="mb-6 border border-border rounded-lg px-4 py-3 text-xs text-text-secondary">当前快照没有核验到负净流入板块，暂不展示“资金流出 TOP5”，避免把流入较弱误说成资金流出。</div>}

      {/* 涨幅 TOP5 */}
      {data.hot_gainers && data.hot_gainers.length > 0 && (
        <div className="mb-6">
          <h3 className="text-lg font-bold text-warn mb-3 flex items-center gap-2">
            <Flame size={18} />
            📈 涨幅 TOP5
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
            {data.hot_gainers.map((item, i) => (
              <div
                key={item.code}
                className="bg-card border border-border rounded-lg p-4 hover:border-[#D2992244] transition-colors"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-text-secondary font-mono">#{i + 1}</span>
                  <span className={`text-xs font-bold ${item.change_pct >= 0 ? 'text-up' : 'text-down'}`}>
                    {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                  </span>
                </div>
                <div className="text-sm font-bold text-text mb-1.5 truncate" title={item.name}>
                  {item.name}
                </div>
                <div className={`text-base font-mono font-bold ${getChangeColor(item.main_net_inflow)}`}>
                  {formatYiShort(item.main_net_inflow)}
                </div>
                <div className="text-xs text-text-secondary mt-1">
                  涨{item.up_count} / 跌{item.down_count}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 全板块表格 */}
      <div className="bg-card border border-border rounded-lg overflow-hidden mb-6">
        <div className="px-6 py-4 border-b border-border">
          <h3 className="text-lg font-bold text-text">全板块资金流向</h3>
          <p className="text-xs text-text-secondary mt-0.5">
            共 {data.sectors.length} 个板块 · 按主力净流入排序
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-text-secondary border-b border-border bg-[#0D1117]">
                <th className="text-left px-4 py-3 font-medium">板块</th>
                <th className="text-right px-3 py-3 font-medium">涨跌幅</th>
                <th className="text-right px-3 py-3 font-medium">主力净流入</th>
                <th className="text-right px-3 py-3 font-medium hidden md:table-cell">超大单</th>
                <th className="text-right px-3 py-3 font-medium hidden md:table-cell">大单</th>
                <th className="text-right px-3 py-3 font-medium hidden sm:table-cell">涨跌家数</th>
              </tr>
            </thead>
            <tbody>
              {[...data.sectors]
                .sort((a, b) => b.main_net_inflow - a.main_net_inflow)
                .map((item) => (
                  <tr key={item.code} className="border-b border-border/50 hover:bg-[#21262D] transition-colors">
                    <td className="px-4 py-2.5">
                      <div className="font-medium text-text">{item.name}</div>
                      <div className="text-xs text-text-secondary">{item.code}</div>
                    </td>
                    <td className={`px-3 py-2.5 text-right font-mono ${getChangeColor(item.change_pct)}`}>
                      {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                    </td>
                    <td className={`px-3 py-2.5 text-right font-mono font-medium ${getChangeColor(item.main_net_inflow)}`}>
                      {formatYiShort(item.main_net_inflow)}
                    </td>
                    <td className={`px-3 py-2.5 text-right font-mono text-text-secondary hidden md:table-cell ${getChangeColor(item.super_large_inflow)}`}>
                      {formatYiShort(item.super_large_inflow)}
                    </td>
                    <td className={`px-3 py-2.5 text-right font-mono text-text-secondary hidden md:table-cell ${getChangeColor(item.large_inflow)}`}>
                      {formatYiShort(item.large_inflow)}
                    </td>
                    <td className="px-3 py-2.5 text-right text-text-secondary hidden sm:table-cell">
                      <span className="text-up">{item.up_count}</span>
                      <span className="mx-0.5">/</span>
                      <span className="text-down">{item.down_count}</span>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 小白解读 */}
      <div className="bg-card border border-border rounded-lg p-6">
        <h3 className="text-lg font-bold text-text mb-3 flex items-center gap-2">
          <HelpCircle size={18} className="text-accent" />
          小白解读
        </h3>
        <div className="text-sm text-text-secondary leading-relaxed space-y-2">
          <p>
            <strong className="text-text">行业轮动</strong>是指主力资金在不同行业板块之间
            <strong className="text-text">有规律地进出</strong>，形成板块"你方唱罢我登场"的格局。
          </p>
          <p>
            <span className="text-up">资金流入板块</span>：主力正在建仓或加仓的板块，短期可能继续走强。
            关注是否有<span className="text-warn">持续性</span>（连续多日流入）。
          </p>
          <p>
            <span className="text-down">资金流出板块</span>：主力正在减仓的板块，短期注意回避。
            可能是获利了结，也可能是调仓换股。
          </p>
          <p>
            <span className="text-accent">涨幅板块 vs 资金板块</span>：
            有时候涨幅最好的板块不是资金流入最多的，这可能是游资炒作而非主力建仓，
            需要区分<strong className="text-text">"真金白银"</strong>和<strong className="text-warn">"虚火"</strong>。
          </p>
          <p className="text-xs text-text-secondary mt-2 pt-2 border-t border-border">
            💡 提示：关注资金持续流入的方向，结合板块涨幅和技术形态综合判断。
          </p>
        </div>
      </div>
    </div>
  );
}
