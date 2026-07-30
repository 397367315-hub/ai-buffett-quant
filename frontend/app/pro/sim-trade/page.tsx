'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch, formatYi, getChangeColor } from '@/lib/api';
import { Play, RotateCcw, Bot, Target, Shield, PieChart, FileText, Activity, BarChart3 } from 'lucide-react';

interface StrategyInfo {
  name: string;
  description: string;
  total_value: number;
  total_pnl: number;
  total_pnl_pct: number;
  trade_count: number;
  account_id?: number;
}

interface RiskData {
  total_return?: number; sharpe_ratio?: number; max_drawdown?: number;
  win_rate?: number; profit_loss_ratio?: number; error?: string;
}

interface AttributionData {
  total_trades?: number; winning_trades?: number; losing_trades?: number;
  sector_attribution?: Record<string, { pnl: number; count: number }>;
  error?: string;
}

export default function SimTradePage() {
  const [account, setAccount] = useState<any>(null);
  const [strategies, setStrategies] = useState<Record<string, StrategyInfo>>({});
  const [riskMetrics, setRiskMetrics] = useState<RiskData | null>(null);
  const [attribution, setAttribution] = useState<AttributionData | null>(null);
  const [trades, setTrades] = useState<any[]>([]);
  const [quantScores, setQuantScores] = useState<any[]>([]);
  const [aiReport, setAiReport] = useState('');
  const [loading, setLoading] = useState(true);
  const [executing, setExecuting] = useState(false);
  const [activeStrategy, setActiveStrategy] = useState('balanced');
  const [execResult, setExecResult] = useState<any>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [accRes, stratsRes, tradesRes, quantRes, reportRes] = await Promise.all([
        apiFetch<any>('/sim/account'),
        apiFetch<any>('/sim/compare'),
        apiFetch<any>('/sim/trades?days=30'),
        apiFetch<any>('/quant/score-board'),
        apiFetch<any>('/sim/ai-daily-report'),
      ]);
      setAccount(accRes?.data || { exists: false });
      setStrategies(stratsRes?.data || {});
      setTrades(tradesRes?.data || []);
      setQuantScores(quantRes?.data?.stocks || []);
      setAiReport(reportRes?.data?.report || '');
    } catch (err) { console.error(err); }
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
    const aid = strategies[activeStrategy]?.account_id || 1;
    fetchRisk(aid);
  }, [activeStrategy, strategies, fetchRisk]);

  const handleExecute = async () => {
    setExecuting(true); setExecResult(null);
    try {
      const res = await apiFetch<any>('/sim/execute-trading', {
        method: 'POST', body: JSON.stringify({ dry_run: false, all_strategies: true }),
      });
      setExecResult(res.data);
      await fetchAll();
      await fetchRisk(strategies[activeStrategy]?.account_id || 1);
    } catch(err) { console.error(err); }
    setExecuting(false);
  };

  if (loading) {
    return <div className="flex items-center justify-center h-96"><div className="animate-spin w-8 h-8 border-2 border-accent border-t-transparent rounded-full" /></div>;
  }

  const strategyKeys = Object.keys(strategies);
  const colorMap: Record<string, string> = { balanced: '#58A6FF', aggressive: '#EF5350', conservative: '#26A69A' };

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-text flex items-center gap-2"><Bot size={22} className="text-warn" /> AI 量化交易中心</h1>
          <p className="text-text-secondary text-sm mt-1">多策略并行 · 风控仪表盘 · 收益归因</p>
        </div>
        <div className="flex gap-2">
          <button onClick={handleExecute} disabled={executing} className="flex items-center gap-1.5 px-4 py-2 bg-accent text-white text-sm rounded-md hover:opacity-90 disabled:opacity-50">
            <Play size={14} className={executing ? 'animate-pulse' : ''} />{executing ? '交易中...' : '🤖 三策略执行'}
          </button>
          <button onClick={async () => { for (const aid of [1,2,3]) { try { await apiFetch<any>('/sim/reset',{method:'POST'}); } catch {} } await fetchAll(); }}
            className="flex items-center gap-1.5 px-3 py-2 border border-border text-text-secondary text-sm rounded-md hover:border-down hover:text-down">
            <RotateCcw size={14} />重置
          </button>
        </div>
      </div>

      {execResult && (
        <div className={`${execResult.status === 'unavailable' ? 'bg-[#D2992222] border-[#D2992244] text-warn' : 'bg-[#26A69A22] border-[#26A69A44] text-up'} border rounded-lg p-3 mb-4 text-sm`}>
          {execResult.status === 'unavailable' ? '实时数据暂不可用，本次未执行交易' : '三策略交易执行完成'}
        </div>
      )}

      {/* 三策略卡片 */}
      {strategyKeys.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          {Object.entries(strategies).map(([key, s]) => (
            <button key={key}
              className={`bg-card border rounded-lg p-4 text-left transition-all ${activeStrategy === key ? 'border-accent ring-1 ring-accent' : 'border-border hover:border-accent/50'}`}
              onClick={() => setActiveStrategy(key)}>
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-bold text-text">{s.name}</span>
                <span className="text-xs px-1.5 py-0.5 rounded" style={{backgroundColor: colorMap[key]+'22', color: colorMap[key]}}>
                  {key === 'balanced' ? '均衡' : key === 'aggressive' ? '激进' : '保守'}
                </span>
              </div>
              <div className="text-xs text-text-secondary mb-2">{s.description}</div>
              <div className="grid grid-cols-3 gap-2 text-xs">
                <div><span className="text-text-secondary">资产</span><div className="text-text font-mono">{(s.total_value/1e4).toFixed(1)}万</div></div>
                <div><span className="text-text-secondary">收益</span><div className={`font-mono ${getChangeColor(s.total_pnl)}`}>{s.total_pnl>=0?'+':''}{(s.total_pnl/1e4).toFixed(2)}万</div></div>
                <div><span className="text-text-secondary">交易</span><div className="text-text font-mono">{s.trade_count}笔</div></div>
              </div>
              {/* CSS条形图：收益率对比 */}
              <div className="mt-3 pt-3 border-t border-border/50">
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="text-text-secondary">收益率</span>
                  <span className={`font-mono ${getChangeColor(s.total_pnl_pct)}`}>{s.total_pnl_pct>=0?'+':''}{s.total_pnl_pct?.toFixed(2)}%</span>
                </div>
                <div className="w-full bg-[#30363D] h-2 rounded-full overflow-hidden">
                  <div className="h-full rounded-full transition-all" style={{
                    width: `${Math.min(Math.abs(s.total_pnl_pct||0)*5, 100)}%`,
                    backgroundColor: colorMap[key],
                    marginLeft: (s.total_pnl_pct||0) < 0 ? 'auto' : '0'
                  }} />
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      {/* 三策略收益对比一览 */}
      {strategyKeys.length > 1 && (
        <div className="bg-card border border-border rounded-lg p-5 mb-6">
          <h3 className="text-sm font-bold text-text mb-3 flex items-center gap-2"><Activity size={14} className="text-accent" /> 策略收益对比</h3>
          <div className="space-y-3">
            {Object.entries(strategies).map(([key, s]) => {
              const maxAbs = Math.max(...Object.values(strategies).map(x => Math.abs(x.total_pnl_pct || 0)), 1);
              const barWidth = Math.abs(s.total_pnl_pct || 0) / maxAbs * 100;
              return (
                <div key={key}>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="font-medium text-text">{s.name}</span>
                    <span className={`font-mono ${getChangeColor(s.total_pnl_pct)}`}>{s.total_pnl_pct>=0?'+':''}{s.total_pnl_pct?.toFixed(2)}%</span>
                  </div>
                  <div className="w-full bg-[#30363D] h-2.5 rounded-full overflow-hidden relative">
                    <div className="absolute left-1/2 top-0 bottom-0 w-px bg-border/50 z-10" />
                    <div className="h-full rounded-full transition-all absolute" style={{
                      width: `${barWidth}%`,
                      backgroundColor: colorMap[key],
                      [s.total_pnl_pct >= 0 ? 'left' : 'right']: '50%',
                    }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 风控 + 归因 + 日报 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
        <div className="bg-card border border-border rounded-lg p-5">
          <h3 className="text-sm font-bold text-text mb-3 flex items-center gap-2"><Shield size={14} className="text-warn" /> 风控仪表盘</h3>
          {riskMetrics && !riskMetrics.error ? (
            <div className="space-y-1.5 text-xs">
              {[
                {l:'总收益率',v:`${(riskMetrics.total_return||0)>=0?'+':''}${(riskMetrics.total_return||0)}%`,c:getChangeColor(riskMetrics.total_return||0)},
                {l:'夏普比率',v:(riskMetrics.sharpe_ratio||0).toFixed(2),c:(riskMetrics.sharpe_ratio||0)>1?'text-up':'text-text'},
                {l:'最大回撤',v:`${(riskMetrics.max_drawdown||0)}%`,c:(riskMetrics.max_drawdown||0)<10?'text-up':'text-down'},
                {l:'胜率',v:`${riskMetrics.win_rate||0}%`,c:(riskMetrics.win_rate||0)>50?'text-up':'text-down'},
                {l:'盈亏比',v:(riskMetrics.profit_loss_ratio||0).toFixed(1),c:(riskMetrics.profit_loss_ratio||0)>1.5?'text-up':'text-text'},
              ].map(item => (
                <div key={item.l} className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-text-secondary">{item.l}</span><span className={`font-mono font-bold ${item.c}`}>{item.v}</span>
                </div>
              ))}
            </div>
          ) : <div className="text-text-secondary text-xs py-4 text-center">暂无足够数据，执行一次交易后生成</div>}
        </div>

        <div className="bg-card border border-border rounded-lg p-5">
          <h3 className="text-sm font-bold text-text mb-3 flex items-center gap-2"><PieChart size={14} className="text-accent" /> 收益归因</h3>
          {attribution && !attribution.error && attribution.total_trades ? (
            <div className="space-y-2 text-xs">
              <div className="flex justify-between"><span className="text-text-secondary">已平仓</span><span className="text-text">{attribution.total_trades}笔</span></div>
              <div className="text-text-secondary mb-1">板块贡献:</div>
              {Object.entries(attribution.sector_attribution||{}).slice(0,5).map(([sector, data]) => (
                <div key={sector} className="flex justify-between py-0.5"><span className="text-text">{sector}</span><span className={`font-mono ${getChangeColor(data.pnl)}`}>{data.pnl>=0?'+':''}{(data.pnl/1e4).toFixed(2)}万</span></div>
              ))}
            </div>
          ) : <div className="text-text-secondary text-xs py-4 text-center">暂无已平仓交易</div>}
        </div>

        <div className="bg-card border border-border rounded-lg p-5">
          <h3 className="text-sm font-bold text-text mb-3 flex items-center gap-2"><FileText size={14} className="text-up" /> AI 交易日报</h3>
          {aiReport ? (
            <div className="text-xs text-text-secondary whitespace-pre-wrap leading-relaxed">{aiReport}</div>
          ) : <div className="text-text-secondary text-xs py-4 text-center">执行交易后生成</div>}
        </div>
      </div>

      {/* 持仓 */}
      {account?.positions?.length > 0 && (
        <div className="bg-card border border-border rounded-lg p-5 mb-6">
          <h3 className="text-sm font-bold text-text mb-3 flex items-center gap-2"><Target size={14} className="text-up" /> 当前持仓</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
            {account.positions.map((p: any) => (
              <div key={p.stock_code} className="bg-[#0D1117] border border-border rounded p-2.5 text-xs">
                <div className="flex justify-between mb-1"><span className="font-medium text-text">{p.stock_name}</span><span className={getChangeColor(p.pnl_pct)}>{p.pnl_pct>=0?'+':''}{p.pnl_pct?.toFixed(2)}%</span></div>
                <div className="flex gap-3 text-text-secondary"><span>{p.shares}股</span><span>¥{p.avg_cost?.toFixed(2)}</span><span>¥{p.current_price?.toFixed(2)}</span></div>
                <div className="mt-1.5 w-full bg-[#30363D] h-1 rounded-full overflow-hidden"><div className={`h-full rounded-full ${p.pnl_pct>=0?'bg-up':'bg-down'}`} style={{width:`${Math.min(Math.abs(p.pnl_pct||0)*3,100)}%`}}/></div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 量化评分 */}
      {quantScores.length > 0 && (
        <div className="bg-card border border-accent rounded-lg p-5 mb-6">
          <h3 className="text-sm font-bold text-text mb-3 flex items-center gap-2"><BarChart3 size={14} className="text-accent" /> 多因子量化评分</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs min-w-[900px]">
              <thead><tr className="text-text-secondary border-b border-border">
                <th className="text-left py-2 px-1.5">#</th><th className="text-left py-2 px-1.5">股票</th><th className="text-right py-2 px-1.5">综合</th><th className="text-center py-2 px-1.5">评级</th>
                <th className="text-right py-2 px-1.5">主力资金</th><th className="text-right py-2 px-1.5">趋势动量</th><th className="text-right py-2 px-1.5">估值水平</th><th className="text-right py-2 px-1.5">交易活跃</th>
                <th className="text-right py-2 px-1.5">涨跌</th><th className="text-right py-2 px-1.5">市盈率</th>
              </tr></thead>
              <tbody>
                {quantScores.map((s: any, i: number) => {
                  const fd = s.factor_detail || {};
                  const gc: Record<string,string>={S:'bg-[#D2992222] text-warn',A:'bg-[#26A69A22] text-up',B:'bg-[#58A6FF22] text-accent',C:'bg-[#21262D] text-text-secondary',D:'bg-[#EF535022] text-down'};
                  return (<tr key={s.code} className="border-b border-border/30 hover:bg-[#21262D]">
                    <td className="py-1.5 px-1.5 text-text-secondary">{i+1}</td>
                    <td className="py-1.5 px-1.5"><div className="font-medium text-text">{s.name}</div><div className="text-text-secondary text-xs">{s.code}</div></td>
                    <td className="py-1.5 px-1.5 text-right"><span className={`font-mono font-bold ${s.grade==='S'?'text-warn':s.grade==='A'?'text-up':'text-text'}`}>{s.quant_score}</span></td>
                    <td className="py-1.5 px-1.5 text-center"><span className={`px-1.5 py-0.5 rounded text-xs font-bold ${gc[s.grade]||''}`}>{s.grade}</span></td>
                    <td className="py-1.5 px-1.5 text-right font-mono text-text-secondary">{fd.fund_flow?.score||'-'}<div className="text-xs" style={{fontSize:'9px'}}>{fd.fund_flow?.raw>0?'买':'卖'}{Math.abs(fd.fund_flow?.raw||0).toFixed(1)}亿</div></td>
                    <td className="py-1.5 px-1.5 text-right font-mono text-text-secondary">{fd.momentum?.score||'-'}</td>
                    <td className="py-1.5 px-1.5 text-right font-mono text-text-secondary">{fd.valuation?.score||'-'}</td>
                    <td className="py-1.5 px-1.5 text-right font-mono text-text-secondary">{fd.liquidity?.score||'-'}</td>
                    <td className={`py-1.5 px-1.5 text-right font-mono ${getChangeColor(parseFloat(s.change_pct||'0'))}`}>{s.change_pct}%</td>
                    <td className="py-1.5 px-1.5 text-right text-text-secondary">{s.pe||'-'}</td>
                  </tr>);
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 交易日志 */}
      {trades.length > 0 && (
        <div className="bg-card border border-border rounded-lg p-5">
          <h3 className="text-sm font-bold text-text mb-3 flex items-center gap-2"><Activity size={14} className="text-warn" /> 最近交易日志</h3>
          <div className="space-y-1.5 max-h-60 overflow-y-auto">
            {trades.slice(0,15).map((t: any, i: number) => (
              <div key={i} className="flex items-center gap-2 py-1.5 border-b border-border/30 text-xs">
                <span className={`px-1.5 py-0.5 rounded font-bold ${t.trade_type==='buy'?'bg-[#26A69A22] text-up':'bg-[#EF535022] text-down'}`}>{t.trade_type==='buy'?'买':'卖'}</span>
                <span className="font-medium text-text">{t.stock_name}</span>
                <span className="text-text-secondary">{t.shares}股 @ ¥{t.price?.toFixed(2)}</span>
                {t.pnl ? <span className={`font-mono ${getChangeColor(t.pnl)}`}>{t.pnl>=0?'+':''}{(t.pnl/1e4).toFixed(2)}万</span>:null}
                <span className="text-text-secondary ml-auto text-xs">{t.trade_date}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {!account?.exists && (
        <div className="bg-card border border-border rounded-lg p-12 text-center">
          <Bot size={48} className="text-text-secondary mx-auto mb-4" />
          <h3 className="text-lg font-bold text-text mb-2">AI模拟账户未初始化</h3>
          <p className="text-text-secondary text-sm mb-6">点击「🤖 三策略执行」启动AI量化交易</p>
        </div>
      )}
    </div>
  );
}
