'use client';

import { useEffect, useState, useRef, useCallback } from 'react';
import { apiFetch, formatYi, getChangeColor } from '@/lib/api';
import { Play, RotateCcw, TrendingUp, TrendingDown, BarChart3, Bot, Zap, Target, Shield, PieChart, Activity, FileText } from 'lucide-react';

let echartsModule: any = null;
async function getEcharts() {
  if (!echartsModule) {
    echartsModule = await import('echarts');
  }
  return echartsModule;
}

interface StrategyInfo {
  name: string;
  account_id: number;
  description: string;
  total_value: number;
  total_pnl: number;
  total_pnl_pct: number;
  trade_count: number;
  positions_count: number;
  daily_data: Array<{ date: string; total_value: number; cumulative_pnl: number }>;
}

interface RiskData {
  total_return: number;
  annual_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  win_rate: number;
  calmar_ratio: number;
  profit_loss_ratio: number;
  error?: string;
}

interface AttributionData {
  total_pnl: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  sector_attribution: Record<string, { pnl: number; count: number; win_rate: number }>;
  hold_period_attribution: Record<string, { pnl: number; count: number }>;
  avg_win: number;
  avg_loss: number;
  error?: string;
}

interface BenchmarkData {
  date: string;
  close: number;
}

export default function SimTradePage() {
  const [account, setAccount] = useState<any>(null);
  const [strategies, setStrategies] = useState<Record<string, StrategyInfo>>({});
  const [riskMetrics, setRiskMetrics] = useState<RiskData | null>(null);
  const [attribution, setAttribution] = useState<AttributionData | null>(null);
  const [benchmark, setBenchmark] = useState<BenchmarkData[]>([]);
  const [trades, setTrades] = useState<any[]>([]);
  const [dailySummary, setDailySummary] = useState<any[]>([]);
  const [quantScores, setQuantScores] = useState<any[]>([]);
  const [aiReport, setAiReport] = useState('');
  const [loading, setLoading] = useState(true);
  const [executing, setExecuting] = useState(false);
  const [activeStrategy, setActiveStrategy] = useState('balanced');
  const [metricsAccountId, setMetricsAccountId] = useState(1);

  const chartRef = useRef<HTMLDivElement>(null);
  const compareRef = useRef<HTMLDivElement>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [accRes, stratsRes, benchRes, tradesRes, summaryRes, quantRes, reportRes] = await Promise.all([
        apiFetch<any>('/sim/account'),
        apiFetch<any>('/sim/compare'),
        apiFetch<any>('/sim/benchmark?days=30'),
        apiFetch<any>('/sim/trades?days=30'),
        apiFetch<any>('/sim/daily-summary?days=30'),
        apiFetch<any>('/quant/score-board'),
        apiFetch<any>('/sim/ai-daily-report'),
      ]);
      setAccount(accRes.data);
      setStrategies(stratsRes.data || {});
      setBenchmark(benchRes.data || []);
      setTrades(tradesRes.data || []);
      setDailySummary(summaryRes.data || []);
      setQuantScores(quantRes.data?.stocks || []);
      setAiReport(reportRes.data?.report || '');
    } catch (err) {
      console.error('Failed to fetch:', err);
    }
    setLoading(false);
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const fetchRisk = useCallback(async (aid: number) => {
    try {
      const [riskRes, attrRes] = await Promise.all([
        apiFetch<any>(`/sim/risk-metrics/${aid}`),
        apiFetch<any>(`/sim/attribution/${aid}`),
      ]);
      setRiskMetrics(riskRes.data);
      setAttribution(attrRes.data);
    } catch {}
  }, []);

  useEffect(() => {
    fetchRisk(metricsAccountId);
  }, [metricsAccountId, fetchRisk]);

  // 多策略对比图表
  useEffect(() => {
    if (!compareRef.current || Object.keys(strategies).length === 0) return;
    let disposed = false;
    let chartInstance: any = null;

    (async () => {
      try {
        const echarts = await getEcharts();
        if (disposed || !compareRef.current) return;
        chartInstance = echarts.init(compareRef.current);

    const series: any[] = [];
    const colors: Record<string, string> = { balanced: '#58A6FF', aggressive: '#26A69A', conservative: '#EF5350' };

    Object.entries(strategies).forEach(([key, s]) => {
      if (!s.daily_data || s.daily_data.length === 0) return;
      series.push({
        name: s.name,
        type: 'line',
        data: s.daily_data.map(d => (d.total_value / 10000).toFixed(1)),
        smooth: true,
        symbol: 'none',
        lineStyle: { color: colors[key] || '#888', width: 2 },
      });
    });

    if (benchmark.length > 0) {
      const initValue = benchmark[0]?.close || 3200;
      series.push({
        name: '上证指数(归一化)',
        type: 'line',
        data: benchmark.map(b => ((b.close / initValue) * 100).toFixed(1)),
        smooth: true,
        symbol: 'none',
        lineStyle: { color: '#8B949E', width: 1.5, type: 'dashed' },
      });
    }

    const allDates = new Set<string>();
    Object.values(strategies).forEach(s => s.daily_data?.forEach(d => allDates.add(d.date)));
    benchmark.forEach(b => allDates.add(b.date));

    chartInstance.setOption({
      tooltip: { trigger: 'axis', backgroundColor: '#161B22', borderColor: '#30363D', textStyle: { color: '#E6EDF3', fontSize: 11 } },
      legend: { data: series.map(s => s.name), textStyle: { color: '#8B949E', fontSize: 10 }, top: 0 },
      grid: { left: '8%', right: '5%', top: '15%', bottom: '5%' },
      xAxis: { type: 'category', data: Array.from(allDates).sort(), axisLabel: { color: '#8B949E', fontSize: 9, rotate: 45 } },
      yAxis: { type: 'value', name: '万元', axisLabel: { color: '#8B949E', fontSize: 10 }, splitLine: { lineStyle: { color: '#21262D' } } },
      series,
    });
    const h = () => chartInstance.resize();
    window.addEventListener('resize', h);
    return () => { window.removeEventListener('resize', h); chartInstance.dispose(); };
      } catch(e) { console.error('Chart error:', e); }
    })();
    return () => { disposed = true; chartInstance?.dispose(); };
  }, [strategies, benchmark]);

  // 单策略收益图
  useEffect(() => {
    if (!chartRef.current || dailySummary.length === 0) return;
    let disposed = false;
    let chartInstance: any = null;

    (async () => {
      try {
        const echarts = await getEcharts();
        if (disposed || !chartRef.current) return;
        chartInstance = echarts.init(chartRef.current);
    const dates = dailySummary.map((d: any) => d.date);
    const values = dailySummary.map((d: any) => (d.total_value / 1e4).toFixed(1));
    chartInstance.setOption({
      tooltip: { trigger: 'axis', backgroundColor: '#161B22', borderColor: '#30363D', textStyle: { color: '#E6EDF3', fontSize: 11 } },
      legend: { data: ['总资产(万)'], textStyle: { color: '#8B949E', fontSize: 10 }, top: 0 },
      grid: { left: '8%', right: '5%', top: '15%', bottom: '5%' },
      xAxis: { type: 'category', data: dates, axisLabel: { color: '#8B949E', fontSize: 9, rotate: 45 } },
      yAxis: { type: 'value', name: '万元', axisLabel: { color: '#8B949E', fontSize: 10 }, splitLine: { lineStyle: { color: '#21262D' } } },
      series: [{ name: '总资产(万)', type: 'line', data: values, smooth: true, lineStyle: { color: '#58A6FF', width: 2 }, itemStyle: { color: '#58A6FF' }, symbol: 'none' }],
    });
    const h = () => chartInstance.resize();
    window.addEventListener('resize', h);
    return () => { window.removeEventListener('resize', h); chartInstance.dispose(); };
      } catch(e) { console.error('Chart error:', e); }
    })();
    return () => { disposed = true; chartInstance?.dispose(); };
  }, [dailySummary]);

  const handleExecute = async () => {
    setExecuting(true);
    try {
      await apiFetch<any>('/sim/execute-trading', { method: 'POST', body: JSON.stringify({ dry_run: false, all_strategies: true }) });
      await fetchAll();
      await fetchRisk(metricsAccountId);
    } catch (err) { console.error(err); }
    setExecuting(false);
  };

  const handleReset = async () => {
    if (!confirm('确定重置所有策略账户？')) return;
    for (const aid of [1, 2, 3]) {
      try { await apiFetch<any>('/sim/reset', { method: 'POST' }); } catch {}
    }
    await fetchAll();
  };

  if (loading) {
    return <div className="flex items-center justify-center h-96"><div className="animate-spin w-8 h-8 border-2 border-accent border-t-transparent rounded-full" /></div>;
  }

  const strategyKeys = Object.keys(strategies);

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-text flex items-center gap-2">
            <Bot size={22} className="text-warn" /> AI 量化交易中心
          </h1>
          <p className="text-text-secondary text-sm mt-1">多策略并行 · 风控仪表盘 · 收益归因 · 大盘基准对比</p>
        </div>
        <div className="flex gap-2">
          <button onClick={handleExecute} disabled={executing} className="flex items-center gap-1.5 px-4 py-2 bg-accent text-white text-sm rounded-md hover:opacity-90 disabled:opacity-50">
            <Play size={14} className={executing ? 'animate-pulse' : ''} /> {executing ? '交易中...' : '🤖 三策略同时执行'}
          </button>
          <button onClick={handleReset} className="flex items-center gap-1.5 px-3 py-2 border border-border text-text-secondary text-sm rounded-md hover:border-down hover:text-down">
            <RotateCcw size={14} /> 重置
          </button>
        </div>
      </div>

      {/* 策略摘要卡片 */}
      {strategyKeys.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          {Object.entries(strategies).map(([key, s]) => {
            const colorMap: Record<string, string> = { balanced: '#58A6FF', aggressive: '#26A69A', conservative: '#EF5350' };
            const c = colorMap[key] || '#888';
            return (
              <button
                key={key}
                className={`bg-card border rounded-lg p-4 text-left transition-all ${activeStrategy === key ? 'border-' + (key === 'aggressive' ? 'down' : key === 'conservative' ? 'up' : 'accent') + ' ring-1 ring-' + (key === 'aggressive' ? 'down' : key === 'conservative' ? 'up' : 'accent') : 'border-border hover:border-' + (key === 'aggressive' ? '[#26A69A]' : key === 'conservative' ? '[#EF5350]' : '[#58A6FF]')}`}
                onClick={() => { setActiveStrategy(key); setMetricsAccountId(s.account_id); }}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-bold text-text">{s.name}</span>
                  <span className="text-xs px-1.5 py-0.5 rounded" style={{ backgroundColor: c + '22', color: c }}>
                    {key === 'balanced' ? '均衡' : key === 'aggressive' ? '激进' : '保守'}
                  </span>
                </div>
                <div className="text-xs text-text-secondary mb-2">{s.description}</div>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div><span className="text-text-secondary">资产</span><div className="text-text font-mono">{(s.total_value / 1e4).toFixed(1)}万</div></div>
                  <div><span className="text-text-secondary">收益</span><div className={`font-mono ${getChangeColor(s.total_pnl)}`}>{s.total_pnl >= 0 ? '+' : ''}{(s.total_pnl / 1e4).toFixed(2)}万</div></div>
                  <div><span className="text-text-secondary">交易</span><div className="text-text font-mono">{s.trade_count}笔</div></div>
                </div>
              </button>
            );
          })}
        </div>
      )}

      {/* 多策略对比曲线 */}
      {strategyKeys.length > 0 && (
        <div className="bg-card border border-border rounded-lg p-6 mb-6">
          <h3 className="text-base font-bold text-text mb-4 flex items-center gap-2">
            <Activity size={16} className="text-accent" /> 三策略 vs 大盘基准
          </h3>
          <div ref={compareRef} style={{ height: '350px' }} />
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 风控仪表盘 */}
        <div className="bg-card border border-border rounded-lg p-5">
          <h3 className="text-sm font-bold text-text mb-3 flex items-center gap-2">
            <Shield size={14} className="text-warn" /> 风控仪表盘
          </h3>
          {riskMetrics && !riskMetrics.error ? (
            <div className="space-y-2 text-xs">
              {[
                { label: '总收益率', value: `${riskMetrics.total_return >= 0 ? '+' : ''}${riskMetrics.total_return}%`, color: getChangeColor(riskMetrics.total_return) },
                { label: '年化收益', value: `${riskMetrics.annual_return >= 0 ? '+' : ''}${riskMetrics.annual_return}%`, color: getChangeColor(riskMetrics.annual_return) },
                { label: '夏普比率', value: riskMetrics.sharpe_ratio.toFixed(2), color: riskMetrics.sharpe_ratio > 1 ? 'text-up' : riskMetrics.sharpe_ratio > 0 ? 'text-text' : 'text-down' },
                { label: '最大回撤', value: `${riskMetrics.max_drawdown}%`, color: riskMetrics.max_drawdown < 10 ? 'text-up' : 'text-down' },
                { label: 'Calmar', value: riskMetrics.calmar_ratio.toFixed(2), color: riskMetrics.calmar_ratio > 1 ? 'text-up' : 'text-text' },
                { label: '胜率', value: `${riskMetrics.win_rate}%`, color: riskMetrics.win_rate > 50 ? 'text-up' : 'text-down' },
                { label: '盈亏比', value: riskMetrics.profit_loss_ratio.toFixed(1), color: riskMetrics.profit_loss_ratio > 1.5 ? 'text-up' : 'text-text' },
              ].map(item => (
                <div key={item.label} className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-text-secondary">{item.label}</span>
                  <span className={`font-mono font-bold ${item.color}`}>{item.value}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-text-secondary text-xs py-4 text-center">暂无足够交易数据</div>
          )}
        </div>

        {/* 收益归因 */}
        <div className="bg-card border border-border rounded-lg p-5">
          <h3 className="text-sm font-bold text-text mb-3 flex items-center gap-2">
            <PieChart size={14} className="text-accent" /> 收益归因
          </h3>
          {attribution && !attribution.error ? (
            <div className="space-y-3 text-xs">
              <div className="flex justify-between">
                <span className="text-text-secondary">已平仓交易</span>
                <span className="text-text">{attribution.total_trades}笔 (盈{attribution.winning_trades}/亏{attribution.losing_trades})</span>
              </div>
              <div>
                <div className="text-text-secondary mb-1">板块贡献</div>
                {Object.entries(attribution.sector_attribution || {}).slice(0, 4).map(([sector, data]) => (
                  <div key={sector} className="flex justify-between py-0.5">
                    <span className="text-text">{sector}</span>
                    <span className={`font-mono ${getChangeColor(data.pnl)}`}>{data.pnl >= 0 ? '+' : ''}{(data.pnl / 1e4).toFixed(2)}万</span>
                  </div>
                ))}
              </div>
              <div>
                <div className="text-text-secondary mb-1">持仓周期</div>
                {Object.entries(attribution.hold_period_attribution || {}).map(([period, data]) => (
                  <div key={period} className="flex justify-between py-0.5">
                    <span className="text-text">{period}</span>
                    <span className={`font-mono ${getChangeColor(data.pnl)}`}>{data.pnl >= 0 ? '+' : ''}{(data.pnl / 1e4).toFixed(2)}万</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="text-text-secondary text-xs py-4 text-center">暂无已平仓交易数据</div>
          )}
        </div>

        {/* AI 交易日报 */}
        <div className="bg-card border border-border rounded-lg p-5">
          <h3 className="text-sm font-bold text-text mb-3 flex items-center gap-2">
            <FileText size={14} className="text-up" /> AI 交易日报
          </h3>
          {aiReport ? (
            <div className="text-xs text-text-secondary whitespace-pre-wrap leading-relaxed prose prose-invert max-w-none">
              {aiReport}
            </div>
          ) : (
            <div className="text-text-secondary text-xs py-4 text-center">今日暂无报告，执行交易后生成</div>
          )}
        </div>
      </div>

      {/* 当前持仓 - 全宽 */}
      <div className="bg-card border border-border rounded-lg p-5 mt-6">
          <h3 className="text-sm font-bold text-text mb-3 flex items-center gap-2">
            <Target size={14} className="text-up" /> 当前持仓 ({activeStrategy === 'balanced' ? '均衡型' : activeStrategy === 'aggressive' ? '激进型' : '保守型'})
          </h3>
          {account?.positions?.length > 0 ? (
            <div className="space-y-2 max-h-80 overflow-y-auto">
              {account.positions.map((p: any) => (
                <div key={p.stock_code} className="bg-[#0D1117] border border-border rounded p-2.5 text-xs">
                  <div className="flex justify-between mb-1">
                    <span className="font-medium text-text">{p.stock_name}</span>
                    <span className={getChangeColor(p.pnl_pct)}>{p.pnl_pct >= 0 ? '+' : ''}{p.pnl_pct?.toFixed(2)}%</span>
                  </div>
                  <div className="flex gap-3 text-text-secondary">
                    <span>{p.shares}股</span><span>成本¥{p.avg_cost?.toFixed(2)}</span><span>现价¥{p.current_price?.toFixed(2)}</span>
                  </div>
                  <div className="mt-1.5 w-full bg-[#30363D] h-1 rounded-full overflow-hidden">
                    <div className={`h-full rounded-full ${p.pnl_pct >= 0 ? 'bg-up' : 'bg-down'}`} style={{ width: `${Math.min(Math.abs(p.pnl_pct || 0) * 3, 100)}%` }} />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-text-secondary text-xs py-8 text-center">该策略暂无持仓</div>
          )}
        </div>

      {/* 量化评分详情 - 全宽 */}
      {quantScores.length > 0 && (
        <div className="bg-card border border-accent rounded-lg p-5 mt-6">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-bold text-text flex items-center gap-2">
              <BarChart3 size={14} className="text-accent" />
              多因子量化评分明细
              <span className="text-xs text-text-secondary font-normal">
                （资金{quantScores[0]?.weights?.fund_flow ? `${(quantScores[0].weights.fund_flow*100).toFixed(0)}%` : '30%'} + 动量{quantScores[0]?.weights?.momentum ? `${(quantScores[0].weights.momentum*100).toFixed(0)}%` : '20%'} + 估值{quantScores[0]?.weights?.valuation ? `${(quantScores[0].weights.valuation*100).toFixed(0)}%` : '18%'} + 流动{quantScores[0]?.weights?.liquidity ? `${(quantScores[0].weights.liquidity*100).toFixed(0)}%` : '15%'} + 板块{quantScores[0]?.weights?.sector_strength ? `${(quantScores[0].weights.sector_strength*100).toFixed(0)}%` : '12%'}）
                {quantScores[0]?.regime && <span className="ml-2 px-1.5 py-0.5 rounded bg-[#D2992222] text-warn">当前: {quantScores[0].regime}</span>}
              </span>
            </h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs min-w-[1000px]">
                <thead>
                  <tr className="text-text-secondary border-b border-border">
                    <th className="text-left py-2 px-1.5">#</th>
                    <th className="text-left py-2 px-1.5">股票</th>
                    <th className="text-right py-2 px-1.5">综合</th>
                    <th className="text-center py-2 px-1.5">评级</th>
                    <th className="text-right py-2 px-1.5">主力资金</th>
                    <th className="text-right py-2 px-1.5">趋势动量</th>
                    <th className="text-right py-2 px-1.5">估值水平</th>
                    <th className="text-right py-2 px-1.5">交易活跃</th>
                    <th className="text-right py-2 px-1.5">板块强度</th>
                    <th className="text-right py-2 px-1.5 hidden sm:table-cell">涨跌</th>
                    <th className="text-right py-2 px-1.5 hidden lg:table-cell">市盈率</th>
                    <th className="text-right py-2 px-1.5 hidden lg:table-cell">风险配比</th>
                  </tr>
                </thead>
                <tbody>
                  {quantScores.map((s: any, i: number) => {
                    const gradeColors: Record<string, string> = {
                      S: 'bg-[#D2992222] text-warn border border-[#D2992244]',
                      A: 'bg-[#EF535022] text-up border border-[#EF535044]',
                      B: 'bg-[#58A6FF22] text-accent border border-[#58A6FF44]',
                      C: 'bg-[#21262D] text-text-secondary border border-border',
                      D: 'bg-[#26A69A22] text-down border border-[#26A69A44]',
                    };
                    const fd = s.factor_detail || {};
                    const rp = s.risk_parity;
                    return (
                      <tr key={s.code} className="border-b border-border/30 hover:bg-[#21262D]">
                        <td className="py-1.5 px-1.5 text-text-secondary">{i + 1}</td>
                        <td className="py-1.5 px-1.5">
                          <div className="font-medium text-text">{s.name}</div>
                          <div className="text-text-secondary text-xs">{s.code}</div>
                        </td>
                        <td className="py-1.5 px-1.5 text-right">
                          <span className={`font-mono font-bold text-sm ${s.grade === 'S' ? 'text-warn' : s.grade === 'A' ? 'text-up' : 'text-text'}`}>{s.quant_score}</span>
                        </td>
                        <td className="py-1.5 px-1.5 text-center">
                          <span className={`px-1.5 py-0.5 rounded text-xs font-bold ${gradeColors[s.grade] || ''}`}>{s.grade}</span>
                        </td>
                        <td className="py-1.5 px-1.5 text-right font-mono text-text-secondary">
                          {fd.fund_flow?.score || '-'}
                          <div className="text-xs text-text-secondary" style={{fontSize:'9px'}}>主力{fd.fund_flow?.raw > 0 ? '买' : '卖'}{Math.abs(fd.fund_flow?.raw || 0).toFixed(1)}亿</div>
                        </td>
                        <td className="py-1.5 px-1.5 text-right font-mono text-text-secondary">
                          {fd.momentum?.score || '-'}
                          <div className="text-xs" style={{fontSize:'9px'}}>涨幅{fd.momentum?.change_score || '-'}分 量比{fd.momentum?.volume_score || '-'}分</div>
                        </td>
                        <td className="py-1.5 px-1.5 text-right font-mono text-text-secondary">
                          {fd.valuation?.score || '-'}
                          <div className="text-xs" style={{fontSize:'9px'}}>市盈率{fd.valuation?.pe_score || '-'}分 盈利{fd.valuation?.roe_score || '-'}分</div>
                        </td>
                        <td className="py-1.5 px-1.5 text-right font-mono text-text-secondary">
                          {fd.liquidity?.score || '-'}
                          <div className="text-xs" style={{fontSize:'9px'}}>换手{s.turnover}%</div>
                        </td>
                        <td className="py-1.5 px-1.5 text-right font-mono text-text-secondary">
                          {fd.sector_strength?.score || '-'}
                        </td>
                        <td className={`py-1.5 px-1.5 text-right font-mono hidden sm:table-cell ${getChangeColor(parseFloat(s.change_pct || '0'))}`}>
                          {s.change_pct}%
                        </td>
                        <td className="py-1.5 px-1.5 text-right text-text-secondary hidden lg:table-cell">
                          {s.pe || '-'}
                        </td>
                        <td className="py-1.5 px-1.5 text-right hidden lg:table-cell">
                          {rp ? (
                            <div className="text-xs">
                              <div className="text-text-secondary">权重{rp.weight}%</div>
                              <div className="text-text-secondary">波动{rp.volatility}</div>
                            </div>
                          ) : '-'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="mt-3 text-xs text-text-secondary flex flex-wrap gap-x-4 gap-y-1">
              <span>S≥80 强烈推荐</span><span>A≥70 推荐</span><span>B≥60 关注</span><span>C≥50 中性</span><span>D&lt;50 回避</span>
              {quantScores[0]?.regime && (
                <span className="ml-auto">市场状态: {quantScores[0].regime} | 权重已动态调整</span>
              )}
            </div>
          </div>
        )}

      {/* 最近交易记录 */}
      <div className="bg-card border border-border rounded-lg p-5 mt-6">
        <h3 className="text-sm font-bold text-text mb-3 flex items-center gap-2">
          <Zap size={14} className="text-warn" /> 最近交易日志
        </h3>
        {trades.length === 0 ? (
          <div className="text-text-secondary text-xs py-8 text-center">暂无交易</div>
        ) : (
          <div className="space-y-1.5 max-h-60 overflow-y-auto">
            {trades.slice(0, 15).map((t: any, i: number) => (
              <div key={i} className="flex items-center gap-2 py-1.5 border-b border-border/30 text-xs">
                <span className={`px-1.5 py-0.5 rounded font-bold ${t.trade_type === 'buy' ? 'bg-[#EF535022] text-up' : 'bg-[#26A69A22] text-down'}`}>{t.trade_type === 'buy' ? '买' : '卖'}</span>
                <span className="font-medium text-text">{t.stock_name}</span>
                <span className="text-text-secondary">{t.shares}股 @ ¥{t.price?.toFixed(2)}</span>
                {t.pnl ? <span className={`ml-auto font-mono ${getChangeColor(t.pnl)}`}>{t.pnl >= 0 ? '+' : ''}{(t.pnl / 1e4).toFixed(2)}万</span> : null}
                <span className="text-text-secondary text-xs">{t.trade_date}</span>
                {t.ai_reason && <span className="text-text-secondary truncate hidden lg:inline max-w-[200px]">💡 {t.ai_reason}</span>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
