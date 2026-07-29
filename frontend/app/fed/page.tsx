'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { TrendingUp, TrendingDown, Minus, DollarSign, RefreshCw, ChevronRight } from 'lucide-react';

interface FedDecision {
  date: string;
  rate: string;
  change: string;
  direction: string;
  decision: string;
  reason: string;
}

interface SectorImpact {
  sector: string;
  reason: string;
  impact: number;
}

export default function FedAnalysisPage() {
  const [history, setHistory] = useState<FedDecision[]>([]);
  const [sectorImpact, setSectorImpact] = useState<any>(null);
  const [selectedDate, setSelectedDate] = useState<string>('');
  const [analysis, setAnalysis] = useState<string>('');
  const [analyzing, setAnalyzing] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [histRes, impactRes] = await Promise.all([
          apiFetch<any>('/fed/history'),
          apiFetch<any>('/fed/sector-impact'),
        ]);
        setHistory(histRes.data.history);
        setSectorImpact(impactRes.data);
        if (histRes.data.history.length > 0) {
          setSelectedDate(histRes.data.history[0].date);
        }
      } catch (err) {
        console.error('Failed to fetch fed data:', err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  const selectedDecision = history.find((d) => d.date === selectedDate);

  const handleAnalyze = async () => {
    if (!selectedDate) return;
    setAnalyzing(true);
    try {
      const res = await apiFetch<any>('/fed/analysis', {
        method: 'POST',
        body: JSON.stringify({ date: selectedDate }),
      });
      setAnalysis(res.data.analysis);
    } catch (err) {
      console.error('Failed to get analysis:', err);
    } finally {
      setAnalyzing(false);
    }
  };

  const getDirectionBadge = (direction: string) => {
    switch (direction) {
      case '加息':
        return { bg: 'bg-[#26A69A22]', text: 'text-down', icon: TrendingUp, label: '加息' };
      case '降息':
        return { bg: 'bg-[#EF535022]', text: 'text-up', icon: TrendingDown, label: '降息' };
      default:
        return { bg: 'bg-[#21262D]', text: 'text-text-secondary', icon: Minus, label: '维持' };
    }
  };

  const getImpactColor = (impact: number) => {
    if (impact >= 3) return 'text-up';
    if (impact >= 1) return 'text-up/70';
    if (impact <= -3) return 'text-down';
    if (impact <= -1) return 'text-down/70';
    return 'text-text-secondary';
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="animate-spin w-8 h-8 border-2 border-accent border-t-transparent rounded-full" />
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text flex items-center gap-2">
          <DollarSign size={22} className="text-warn" />
          美联储利率分析
        </h1>
        <p className="text-text-secondary text-sm mt-1">
          追踪美联储加息/降息周期，分析对A股各板块的影响
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 左侧：利率历史时间线 */}
        <div className="lg:col-span-1">
          <div className="bg-card border border-border rounded-lg p-4 sticky top-20">
            <h3 className="text-sm font-bold text-text mb-3">利率决策时间线</h3>
            <div className="space-y-0">
              {history.map((d, i) => {
                const badge = getDirectionBadge(d.direction);
                const Icon = badge.icon;
                const isSelected = d.date === selectedDate;
                return (
                  <button
                    key={d.date}
                    className={`w-full text-left flex items-start gap-3 py-2.5 px-2 -mx-2 rounded transition-colors ${
                      isSelected ? 'bg-[#1F6FEB11]' : 'hover:bg-[#21262D]'
                    } ${i < history.length - 1 ? 'border-l-2 ml-[11px] pl-4' : 'border-l-2 border-transparent ml-[11px] pl-4'}`}
                    style={{
                      borderLeftColor: i < history.length - 1
                        ? isSelected ? '#58A6FF' : '#30363D'
                        : 'transparent'
                    }}
                    onClick={() => { setSelectedDate(d.date); setAnalysis(''); }}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs text-text font-medium">{d.date}</span>
                        <span className={`flex items-center gap-0.5 text-xs px-1.5 py-0.5 rounded ${badge.bg} ${badge.text}`}>
                          <Icon size={10} />
                          {badge.label}
                        </span>
                      </div>
                      <div className="text-xs text-text-secondary mt-0.5">
                        利率: {d.rate}
                        {d.change !== '0' && (
                          <span className={d.direction === '降息' ? 'text-up ml-1' : 'text-down ml-1'}>
                            {d.change}
                          </span>
                        )}
                      </div>
                    </div>
                    {isSelected && <ChevronRight size={14} className="text-accent mt-1 shrink-0" />}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* 右侧：详情和板块影响分析 */}
        <div className="lg:col-span-2 space-y-6">
          {selectedDecision && (
            <>
              {/* 决策详情 */}
              <div className="bg-card border border-border rounded-lg p-6">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h2 className="text-lg font-bold text-text">{selectedDecision.date} 美联储决议</h2>
                    <p className="text-sm text-text-secondary mt-0.5">{selectedDecision.decision}</p>
                  </div>
                  <span className={`px-3 py-1.5 rounded-lg text-sm font-bold ${
                    selectedDecision.direction === '加息' ? 'bg-[#26A69A22] text-down' :
                    selectedDecision.direction === '降息' ? 'bg-[#EF535022] text-up' :
                    'bg-[#21262D] text-text-secondary'
                  }`}>
                    {selectedDecision.direction}
                    {selectedDecision.change !== '0' && ` ${selectedDecision.change}`}
                  </span>
                </div>

                <div className="grid grid-cols-3 gap-4 mb-4">
                  <div className="bg-[#0D1117] rounded-lg p-3">
                    <div className="text-xs text-text-secondary">利率区间</div>
                    <div className="text-lg font-mono font-bold text-text">{selectedDecision.rate}</div>
                  </div>
                  <div className="bg-[#0D1117] rounded-lg p-3">
                    <div className="text-xs text-text-secondary">变动幅度</div>
                    <div className={`text-lg font-mono font-bold ${selectedDecision.direction === '降息' ? 'text-up' : selectedDecision.direction === '加息' ? 'text-down' : 'text-text-secondary'}`}>
                      {selectedDecision.change === '0' ? '不变' : selectedDecision.change}
                    </div>
                  </div>
                  <div className="bg-[#0D1117] rounded-lg p-3">
                    <div className="text-xs text-text-secondary">决策背景</div>
                    <div className="text-sm text-text">{selectedDecision.reason}</div>
                  </div>
                </div>

                <button
                  onClick={handleAnalyze}
                  disabled={analyzing}
                  className="flex items-center gap-1.5 px-4 py-2 bg-accent text-white text-sm rounded-md hover:opacity-90 disabled:opacity-50 transition-colors"
                >
                  <RefreshCw size={14} className={analyzing ? 'animate-spin' : ''} />
                  {analyzing ? 'AI分析中...' : '🤖 AI 深度分析影响'}
                </button>
              </div>

              {/* 板块影响矩阵 */}
              {sectorImpact && selectedDecision.direction !== '维持' && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  {/* 利好板块 */}
                  <div className="bg-card border border-border rounded-lg p-6">
                    <h3 className="text-base font-bold text-up mb-4 flex items-center gap-2">
                      📈 利好板块
                    </h3>
                    <div className="space-y-3">
                      {(sectorImpact[selectedDecision.direction]?.positive || []).map((item: SectorImpact) => (
                        <div key={item.sector} className="bg-[#0D1117] rounded-lg p-3 border-l-2 border-up">
                          <div className="flex items-center justify-between mb-1">
                            <span className="text-sm font-medium text-text">{item.sector}</span>
                            <span className="text-xs text-up">{'+'.repeat(Math.abs(item.impact))}</span>
                          </div>
                          <p className="text-xs text-text-secondary">{item.reason}</p>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* 利空板块 */}
                  <div className="bg-card border border-border rounded-lg p-6">
                    <h3 className="text-base font-bold text-down mb-4 flex items-center gap-2">
                      📉 利空板块
                    </h3>
                    <div className="space-y-3">
                      {(sectorImpact[selectedDecision.direction]?.negative || []).map((item: SectorImpact) => (
                        <div key={item.sector} className="bg-[#0D1117] rounded-lg p-3 border-l-2 border-down">
                          <div className="flex items-center justify-between mb-1">
                            <span className="text-sm font-medium text-text">{item.sector}</span>
                            <span className="text-xs text-down">{'↓'.repeat(Math.abs(item.impact))}</span>
                          </div>
                          <p className="text-xs text-text-secondary">{item.reason}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {sectorImpact && selectedDecision.direction === '维持' && (
                <div className="bg-card border border-border rounded-lg p-6 text-center">
                  <Minus size={32} className="text-text-secondary mx-auto mb-2" />
                  <p className="text-text-secondary text-sm">{sectorImpact['维持']?.note}</p>
                </div>
              )}

              {/* AI 分析结果 */}
              {analysis && (
                <div className="bg-card border border-accent rounded-lg p-6">
                  <div className="prose prose-invert max-w-none text-text leading-relaxed whitespace-pre-wrap text-sm">
                    {analysis}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
