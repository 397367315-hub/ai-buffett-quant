'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { AlertTriangle, ArrowUpDown, BrainCircuit, DollarSign, Loader2, RefreshCw, TrendingUp } from 'lucide-react';
import StockKlineButton from '@/components/StockKlineButton';

interface BlockTrade {
  code: string; name: string; date?: string; price: number; amount: number;
  premium: number; volume: number; buyer: string; seller: string;
}

interface BlockTradeData {
  trades: BlockTrade[];
  summary: { total: number; total_amount: number; premium_count: number };
  data_date?: string | null;
}

interface StockAnalysis {
  code: string; name: string; trade_count: number; latest_trade_date: string | null;
  total_amount: number; average_premium: number | null; latest_trade_price: number | null;
  latest_price: number | null; relative_to_latest_pct: number | null;
  facts: string[]; evidence: string[]; risks: string[]; conclusion: string;
  buyers: string[]; sellers: string[]; quote_source: string | null; quote_data_date: string | null;
}

interface BlockTradeAnalysis {
  available: boolean; headline: string; summary: string; stocks: StockAnalysis[];
  selected: StockAnalysis | null; data_date: string | null; quote_data_dates: string[];
  ai_narrative: string | null; ai_generated: boolean; updated_at: string;
}

const money = (value: number) => `${(value / 1e8).toFixed(2)}亿`;
const signed = (value: number | null | undefined) => value == null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;

export default function BlockTradePage() {
  const [data, setData] = useState<BlockTradeData | null>(null);
  const [analysis, setAnalysis] = useState<BlockTradeAnalysis | null>(null);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchData = async () => {
    setLoading(true);
    try {
      const res = await apiFetch<{ data: BlockTradeData }>('/block-trade/list');
      setData(res.data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '大宗交易数据读取失败');
    } finally {
      setLoading(false);
    }
  };

  const runAnalysis = async (code?: string | null) => {
    setAnalyzing(true);
    setError(null);
    try {
      const response = await apiFetch<{ data: BlockTradeAnalysis }>('/block-trade/analysis', {
        method: 'POST', body: JSON.stringify({ code: code || undefined, use_ai: true }),
      });
      setAnalysis(response.data);
      setSelectedCode(response.data.selected?.code || code || null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '大宗交易分析失败');
    } finally {
      setAnalyzing(false);
    }
  };

  useEffect(() => {
    void fetchData();
    void runAnalysis();
  }, []);

  const selected = analysis?.selected;
  return <div className="max-w-7xl mx-auto px-4 py-6">
    <header className="mb-6 flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-2xl font-bold text-text flex items-center gap-2"><ArrowUpDown size={22} className="text-warn" />大宗交易监控</h1><p className="text-text-secondary text-sm mt-1">成交明细与最新/缓存行情交叉核验</p></div><button type="button" onClick={() => runAnalysis(selectedCode)} disabled={analyzing} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-xs text-text-secondary hover:border-accent hover:text-text disabled:opacity-60"><RefreshCw size={14} className={analyzing ? 'animate-spin' : ''} />重新分析</button></header>

    {error && <div className="mb-4 flex gap-2 rounded-md border border-down/50 bg-[#EF535018] p-3 text-xs text-down"><AlertTriangle size={14} className="shrink-0" />{error}</div>}
    {loading ? <div className="py-16 text-center text-text-secondary"><Loader2 size={28} className="mx-auto animate-spin text-accent" /><div className="mt-3 text-sm">正在读取大宗交易明细</div></div> : data ? <>
      <section className="grid grid-cols-1 md:grid-cols-3 border border-border rounded-lg divide-y md:divide-y-0 md:divide-x divide-border mb-6"><Metric icon={ArrowUpDown} label="交易笔数" value={`${data.summary.total}笔`} /><Metric icon={DollarSign} label="总成交额" value={money(data.summary.total_amount)} accent="text-accent" /><Metric icon={TrendingUp} label="溢价交易数" value={`${data.summary.premium_count}笔`} accent="text-up" /></section>

      <section className="border border-border rounded-lg overflow-hidden mb-6"><div className="px-4 py-3 border-b border-border flex flex-wrap items-center gap-2"><h2 className="text-sm font-semibold text-text flex items-center gap-2"><BrainCircuit size={16} className="text-accent" />AI交叉分析</h2><span className="text-[11px] text-text-secondary">大宗数据日 {analysis?.data_date || data.data_date || '--'} · 行情日 {analysis?.quote_data_dates.join('、') || '--'}</span></div>{analyzing && !analysis ? <div className="p-6 flex items-center gap-2 text-xs text-text-secondary"><Loader2 size={15} className="animate-spin text-accent" />正在核对成交价、最新价和买卖方席位...</div> : analysis ? <div className="p-4 space-y-4"><div><div className="text-sm font-semibold text-text">{analysis.headline}</div><div className="mt-1 text-xs leading-5 text-text-secondary">{analysis.summary}</div>{analysis.ai_narrative && <div className="mt-2 border-l-2 border-accent pl-3 whitespace-pre-line text-xs leading-5 text-text">{analysis.ai_narrative}</div>}</div>{selected && <div className="border-t border-border pt-3"><div className="flex flex-wrap items-center justify-between gap-2"><StockKlineButton code={selected.code} name={selected.name} className="text-sm font-semibold text-text">{selected.name} <span className="font-mono text-text-secondary">{selected.code}</span></StockKlineButton><span className="rounded border border-warn/50 bg-[#D2992218] px-2 py-0.5 text-[11px] text-warn">{selected.conclusion}</span></div><div className="mt-3 grid gap-3 md:grid-cols-3 text-xs"><AnalysisColumn label="可核验事实" items={selected.facts} /><AnalysisColumn label="支持信号" items={selected.evidence} className="text-up" /><AnalysisColumn label="风险与缺口" items={selected.risks.length ? selected.risks : ['未发现额外数据风险，但大宗交易不能单独预测涨跌。']} className="text-warn" /></div></div>}</div> : <div className="p-5 text-xs text-text-secondary">当前没有形成可验证的分析结果。</div>}</section>

      <section className="border border-border rounded-lg overflow-hidden"><div className="overflow-x-auto"><table className="w-full min-w-[980px] text-sm"><thead><tr className="text-text-secondary border-b border-border bg-[#0D1117]"><th className="text-left px-4 py-3 font-medium">股票名称/代码</th><th className="text-right px-3 py-3 font-medium">成交价</th><th className="text-right px-3 py-3 font-medium">成交额</th><th className="text-right px-3 py-3 font-medium">溢价率</th><th className="text-right px-3 py-3 font-medium">成交量</th><th className="text-left px-3 py-3 font-medium">买方</th><th className="text-left px-3 py-3 font-medium">卖方</th></tr></thead><tbody>{data.trades.map((trade, index) => <tr key={`${trade.code}-${index}`} onClick={() => void runAnalysis(trade.code)} className={`border-b border-border/50 cursor-pointer transition-colors ${selectedCode === trade.code ? 'bg-[#1F6FEB12]' : 'hover:bg-[#21262D]'}`}><td className="px-4 py-2.5"><StockKlineButton code={trade.code} name={trade.name} className="font-medium text-text">{trade.name}</StockKlineButton><div className="text-xs text-text-secondary">{trade.code} · {trade.date || '--'}</div></td><td className="px-3 py-2.5 text-right font-mono text-text">{trade.price}</td><td className="px-3 py-2.5 text-right font-mono text-text-secondary">{money(trade.amount)}</td><td className={`px-3 py-2.5 text-right font-mono font-bold ${trade.premium > 0 ? 'text-up' : trade.premium < 0 ? 'text-down' : 'text-text-secondary'}`}>{signed(trade.premium)}</td><td className="px-3 py-2.5 text-right text-text-secondary">{(trade.volume / 1e4).toFixed(1)}万股</td><td className="px-3 py-2.5 text-text-secondary text-xs">{trade.buyer || '--'}</td><td className="px-3 py-2.5 text-text-secondary text-xs">{trade.seller || '--'}</td></tr>)}</tbody></table></div><div className="border-t border-border px-4 py-2 text-[11px] text-text-secondary">点击股票可单独核对。溢价代表协议成交价格高于参考市价，不等于后续股价必涨；折价也需结合减持、流动性和后续承接判断。</div></section>
    </> : <div className="border border-warn/50 rounded-lg p-6 text-center text-sm text-warn">暂无大宗交易数据。</div>}
  </div>;
}

function Metric({ icon: Icon, label, value, accent = 'text-text' }: { icon: typeof ArrowUpDown; label: string; value: string; accent?: string }) {
  return <div className="p-4"><div className="flex items-center gap-2 text-xs text-text-secondary"><Icon size={14} />{label}</div><div className={`mt-1 font-mono text-xl font-bold ${accent}`}>{value}</div></div>;
}

function AnalysisColumn({ label, items, className = 'text-text' }: { label: string; items: string[]; className?: string }) {
  return <div><div className="mb-1 text-text-secondary">{label}</div><div className={`space-y-1 leading-5 ${className}`}>{items.slice(0, 5).map((item) => <div key={item}>{item}</div>)}</div></div>;
}
