'use client';

import { useEffect, useRef, useState } from 'react';
import * as echarts from 'echarts';
import { ArrowRightLeft, Clock, Wallet } from 'lucide-react';
import { apiFetch, formatYi } from '@/lib/api';

interface NorthHistoryItem {
  date: string;
  deal_amount: number;
  net_inflow: number | null;
}

interface NorthSummary {
  total_deal_amount: number;
  latest_deal_amount: number | null;
  net_inflow_available: boolean;
  latest_inflow: number | null;
}

export default function NorthFlowPage() {
  const [history, setHistory] = useState<NorthHistoryItem[]>([]);
  const [summary, setSummary] = useState<NorthSummary | null>(null);
  const [available, setAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await apiFetch<any>('/flow/north/daily?days=30');
        setHistory(response.data.history || []);
        setSummary(response.data.summary || null);
        setAvailable(Boolean(response.data.available));
      } catch (error) {
        console.error('Failed to fetch northbound history:', error);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  useEffect(() => {
    if (!chartRef.current || history.length === 0) return;
    chartInstance.current?.dispose();
    const chart = echarts.init(chartRef.current);
    chartInstance.current = chart;
    chart.setOption({
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#161B22',
        borderColor: '#30363D',
        textStyle: { color: '#E6EDF3', fontSize: 12 },
        valueFormatter: (value: string | number) => `${Number(value).toFixed(2)} 亿`,
      },
      grid: { left: '3%', right: '4%', bottom: '3%', top: '34px', containLabel: true },
      xAxis: { type: 'category', data: history.map((item) => item.date), axisLabel: { color: '#8B949E', fontSize: 10 } },
      yAxis: { type: 'value', name: '亿元', nameTextStyle: { color: '#8B949E' }, axisLabel: { color: '#8B949E' }, splitLine: { lineStyle: { color: '#21262D' } } },
      series: [{
        name: '北向成交额',
        type: 'bar',
        data: history.map((item) => item.deal_amount / 1e8),
        itemStyle: { color: '#58A6FF' },
      }],
    });
    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.dispose();
    };
  }, [history]);

  if (loading) {
    return <div className="flex items-center justify-center h-96 text-text-secondary">数据加载中...</div>;
  }

  if (!available) {
    return <div className="flex items-center justify-center h-96 text-text-secondary">北向历史数据暂不可用</div>;
  }

  const recent = history.slice(-10).reverse();
  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text flex items-center gap-2"><ArrowRightLeft size={22} className="text-accent" />北向成交监控</h1>
        <p className="text-text-secondary text-sm mt-1">数据源：东方财富公开历史数据</p>
      </div>

      {summary && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="text-xs text-text-secondary mb-1">最新北向成交额</div>
            <div className="text-xl font-mono font-bold text-text">{summary.latest_deal_amount == null ? '--' : formatYi(summary.latest_deal_amount)}</div>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="text-xs text-text-secondary mb-1">近30日累计成交额</div>
            <div className="text-xl font-mono font-bold text-accent">{formatYi(summary.total_deal_amount || 0)}</div>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="text-xs text-text-secondary mb-1">北向净买入</div>
            <div className="text-xl font-mono font-bold text-text">{summary.net_inflow_available && summary.latest_inflow != null ? formatYi(summary.latest_inflow) : '未公开'}</div>
          </div>
        </div>
      )}

      <div className="bg-card border border-border rounded-lg p-6 mb-6">
        <h2 className="text-lg font-bold text-text mb-4">近30个交易日成交额</h2>
        <div ref={chartRef} className="h-[400px]" />
      </div>

      <div className="bg-card border border-border rounded-lg p-6">
        <h2 className="text-lg font-bold text-text mb-4 flex items-center gap-2"><Clock size={18} />近10日明细</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-text-secondary text-left border-b border-border"><th className="pb-2 font-medium">日期</th><th className="pb-2 font-medium text-right">北向成交额</th><th className="pb-2 font-medium text-right">北向净买入</th></tr></thead>
            <tbody>{recent.map((item) => <tr key={item.date} className="border-b border-border/50"><td className="py-2.5 font-medium">{item.date}</td><td className="py-2.5 text-right font-mono text-text">{formatYi(item.deal_amount)}</td><td className="py-2.5 text-right font-mono text-text-secondary">{item.net_inflow == null ? '--' : formatYi(item.net_inflow)}</td></tr>)}</tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
