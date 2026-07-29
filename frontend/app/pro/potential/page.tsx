'use client';

import { useEffect, useState } from 'react';
import { apiFetch, formatYi, getChangeColor } from '@/lib/api';
import { Search, Sparkles, TrendingUp, AlertTriangle, BarChart3, Layers } from 'lucide-react';

interface BoardInfo {
  code: string;
  name: string;
  category: string;
  stock_count: number;
}

interface StockItem {
  code: string;
  name: string;
  price: string;
  change_pct: string;
  turnover: string;
  pe: string;
  pb: string;
  roe: string;
  market_cap: string;
  volume_ratio: string;
  main_net_inflow: string;
  main_net_inflow_pct: string;
}

export default function PotentialStocksPage() {
  const [boards, setBoards] = useState<BoardInfo[]>([]);
  const [selectedBoard, setSelectedBoard] = useState<BoardInfo | null>(null);
  const [stocks, setStocks] = useState<StockItem[]>([]);
  const [analysis, setAnalysis] = useState<string>('');
  const [rawStocks, setRawStocks] = useState<StockItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');

  useEffect(() => {
    const fetchBoards = async () => {
      try {
        const res = await apiFetch<any>('/board/list');
        setBoards(res.data);
        if (res.data.length > 0) {
          setSelectedBoard(res.data[0]);
        }
      } catch (err) {
        console.error('Failed to fetch boards:', err);
      } finally {
        setLoading(false);
      }
    };
    fetchBoards();
  }, []);

  useEffect(() => {
    if (selectedBoard) {
      fetchStocks(selectedBoard.code);
    }
  }, [selectedBoard]);

  const fetchStocks = async (boardCode: string) => {
    setLoading(true);
    setAnalysis('');
    try {
      const res = await apiFetch<any>(`/board/stocks/${boardCode}?page_size=50`);
      setStocks(res.data.stocks || []);
    } catch (err) {
      console.error('Failed to fetch stocks:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleAnalyze = async () => {
    if (!selectedBoard) return;
    setAnalyzing(true);
    try {
      const res = await apiFetch<any>('/board/ai-analysis', {
        method: 'POST',
        body: JSON.stringify({
          board_code: selectedBoard.code,
          board_name: selectedBoard.name,
          top_n: 15,
        }),
      });
      setAnalysis(res.data.analysis);
      setRawStocks(res.data.raw_stocks || []);
    } catch (err) {
      console.error('Failed to analyze:', err);
    } finally {
      setAnalyzing(false);
    }
  };

  const sortedStocks = [...stocks].sort((a, b) => {
    const inflowA = parseFloat(a.main_net_inflow || '0');
    const inflowB = parseFloat(b.main_net_inflow || '0');
    return inflowB - inflowA;
  });

  const filteredStocks = searchTerm
    ? sortedStocks.filter(
        (s) =>
          s.name.includes(searchTerm) ||
          s.code.includes(searchTerm)
      )
    : sortedStocks;

  const filteredBoards = searchTerm && !selectedBoard
    ? boards.filter(b => b.name.includes(searchTerm) || b.code.includes(searchTerm))
    : boards;

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text flex items-center gap-2">
          <Sparkles size={22} className="text-warn" />
          板块潜力股分析
        </h1>
        <p className="text-text-secondary text-sm mt-1">
          深入每个概念板块，结合主力资金 + 估值指标 + AI量化评分，挖掘潜力标的
        </p>
      </div>

      {/* 板块选择器 */}
      <div className="bg-card border border-border rounded-lg p-4 mb-6">
        <div className="flex items-center gap-3 flex-wrap">
          <Search size={16} className="text-text-secondary" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="搜索板块..."
            className="bg-[#0D1117] border border-border rounded-md px-3 py-1.5 text-sm text-text placeholder:text-text-secondary focus:outline-none focus:border-accent w-40"
          />
          <div className="flex flex-wrap gap-1.5 max-h-32 overflow-y-auto">
            {filteredBoards.slice(0, 20).map((b) => (
              <button
                key={b.code}
                className={`px-2.5 py-1 text-xs rounded-full border transition-colors whitespace-nowrap ${
                  selectedBoard?.code === b.code
                    ? 'bg-accent border-accent text-white'
                    : 'border-border text-text-secondary hover:border-text-secondary hover:text-text'
                }`}
                onClick={() => { setSelectedBoard(b); setSearchTerm(''); }}
              >
                {b.name}
                <span className="ml-1 opacity-60">{b.stock_count}只</span>
              </button>
            ))}
          </div>
          <span className="text-xs text-text-secondary ml-auto">
            共{boards.length}个板块可选
          </span>
        </div>
      </div>

      {selectedBoard && (
        <>
          {/* AI 分析按钮 */}
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-lg font-bold text-text">
                <Layers size={16} className="inline text-accent mr-1" />
                {selectedBoard.name}
              </h2>
              <span className="text-xs text-text-secondary">
                {selectedBoard.category} · {stocks.length}只成分股
              </span>
            </div>
            <button
              onClick={handleAnalyze}
              disabled={analyzing || stocks.length === 0}
              className="flex items-center gap-1.5 px-4 py-2 bg-accent text-white text-sm rounded-md hover:opacity-90 disabled:opacity-50 transition-colors"
            >
              <Sparkles size={14} className={analyzing ? 'animate-pulse' : ''} />
              {analyzing ? 'AI分析中...' : '🤖 AI 量化分析'}
            </button>
          </div>

          {/* AI 分析结果 */}
          {analysis && (
            <div className="bg-card border border-accent rounded-lg p-6 mb-6">
              <h3 className="text-base font-bold text-text mb-3 flex items-center gap-2">
                <Sparkles size={16} className="text-warn" />
                AI 量化分析报告
              </h3>
              <div className="prose prose-invert max-w-none text-text leading-relaxed whitespace-pre-wrap text-sm">
                {analysis}
              </div>

              {/* 分析用到的原始数据 */}
              {rawStocks.length > 0 && (
                <div className="mt-4 pt-4 border-t border-border">
                  <h4 className="text-xs font-medium text-text-secondary mb-2">
                    分析数据源（按主力净流入排序TOP{rawStocks.length}）
                  </h4>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-text-secondary border-b border-border">
                          <th className="text-left py-1">股票</th>
                          <th className="text-right py-1">现价</th>
                          <th className="text-right py-1">涨跌</th>
                          <th className="text-right py-1">市盈率</th>
                          <th className="text-right py-1">换手</th>
                          <th className="text-right py-1">量比</th>
                          <th className="text-right py-1">主力净流入</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rawStocks.map((s) => (
                          <tr key={s.code} className="border-b border-border/30">
                            <td className="py-1">
                              <span className="font-medium">{s.name}</span>
                              <span className="text-text-secondary ml-1">{s.code}</span>
                            </td>
                            <td className="text-right font-mono">{s.price}</td>
                            <td className={`text-right ${getChangeColor(parseFloat(s.change_pct || '0'))}`}>
                              {s.change_pct}%
                            </td>
                            <td className="text-right">{s.pe || '--'}</td>
                            <td className="text-right">{s.turnover}%</td>
                            <td className="text-right">{s.volume_ratio || '--'}</td>
                            <td className={`text-right font-mono ${getChangeColor(parseFloat(s.main_net_inflow || '0'))}`}>
                              {(parseFloat(s.main_net_inflow || '0') / 1e8).toFixed(2)}亿
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* 成分股列表 */}
          {!loading && (
            <div className="bg-card border border-border rounded-lg overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-text-secondary border-b border-border bg-[#0D1117]">
                      <th className="text-left px-4 py-3 font-medium">股票</th>
                      <th className="text-right px-3 py-3 font-medium">现价</th>
                      <th className="text-right px-3 py-3 font-medium">涨跌幅</th>
                      <th className="text-right px-3 py-3 font-medium hidden md:table-cell">市盈率</th>
                      <th className="text-right px-3 py-3 font-medium hidden lg:table-cell">市净率</th>
                      <th className="text-right px-3 py-3 font-medium hidden lg:table-cell">净资产收益率</th>
                      <th className="text-right px-3 py-3 font-medium hidden md:table-cell">换手率</th>
                      <th className="text-right px-3 py-3 font-medium hidden lg:table-cell">量比</th>
                      <th className="text-right px-3 py-3 font-medium">主力净流入</th>
                      <th className="text-right px-3 py-3 font-medium hidden xl:table-cell">市值</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredStocks.map((s) => (
                      <tr key={s.code} className="border-b border-border/50 hover:bg-[#21262D] transition-colors">
                        <td className="px-4 py-2">
                          <div className="font-medium text-text text-xs">{s.name}</div>
                          <div className="text-xs text-text-secondary">{s.code}</div>
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-text text-xs">{s.price}</td>
                        <td className={`px-3 py-2 text-right font-mono text-xs font-bold ${getChangeColor(parseFloat(s.change_pct || '0'))}`}>
                          {parseFloat(s.change_pct || '0') > 0 ? '+' : ''}{s.change_pct}%
                        </td>
                        <td className="px-3 py-2 text-right text-text-secondary text-xs hidden md:table-cell">
                          {s.pe || '--'}
                        </td>
                        <td className="px-3 py-2 text-right text-text-secondary text-xs hidden lg:table-cell">
                          {s.pb || '--'}
                        </td>
                        <td className="px-3 py-2 text-right text-xs hidden lg:table-cell">
                          {s.roe ? (
                            <span className={parseFloat(s.roe) >= 15 ? 'text-up' : parseFloat(s.roe) >= 8 ? 'text-text-secondary' : 'text-down'}>
                              {s.roe}%
                            </span>
                          ) : '--'}
                        </td>
                        <td className="px-3 py-2 text-right text-text-secondary text-xs hidden md:table-cell">
                          {s.turnover}%
                        </td>
                        <td className="px-3 py-2 text-right text-xs hidden lg:table-cell">
                          {s.volume_ratio ? (
                            <span className={parseFloat(s.volume_ratio) > 1.5 ? 'text-up' : 'text-text-secondary'}>
                              {s.volume_ratio}
                            </span>
                          ) : '--'}
                        </td>
                        <td className={`px-3 py-2 text-right font-mono text-xs ${getChangeColor(parseFloat(s.main_net_inflow || '0'))}`}>
                          {(parseFloat(s.main_net_inflow || '0') / 1e8).toFixed(2)}亿
                        </td>
                        <td className="px-3 py-2 text-right text-text-secondary text-xs hidden xl:table-cell">
                          {(parseFloat(s.market_cap || '0') / 1e8).toFixed(0)}亿
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {loading && (
        <div className="text-center text-text-secondary py-12">
          <div className="animate-spin w-8 h-8 border-2 border-accent border-t-transparent rounded-full mx-auto mb-3" />
          加载中...
        </div>
      )}
    </div>
  );
}
