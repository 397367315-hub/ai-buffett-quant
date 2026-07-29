'use client';

import { useEffect, useState, useRef } from 'react';
import { apiFetch, formatYi, getChangeColor } from '@/lib/api';
import * as echarts from 'echarts';
import { TrendingUp, TrendingDown, Clock, Wallet, ArrowRightLeft, HelpCircle } from 'lucide-react';

interface NorthHistoryItem {
  date: string;
  balance: number;
  hold_balance: number;
  net_inflow: number;
  sh_net_inflow: number;
  sz_net_inflow: number;
}

interface NorthSummary {
  total_inflow: number;
  consecutive_inflow_days: number;
  consecutive_outflow_days: number;
  trend: string;
  latest_inflow: number;
}

export default function NorthFlowPage() {
  const [history, setHistory] = useState<NorthHistoryItem[]>([]);
  const [summary, setSummary] = useState<NorthSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const res = await apiFetch<any>('/flow/north/daily?days=30');
        setHistory(res.data.history || []);
        setSummary(res.data.summary || null);
      } catch (err) {
        console.error('Failed to fetch north flow data:', err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  useEffect(() => {
    if (!chartRef.current || history.length === 0) return;

    if (chartInstance.current) {
      chartInstance.current.dispose();
    }

    const chart = echarts.init(chartRef.current);
    chartInstance.current = chart;

    const dates = history.map((d) => d.date);
    const inflows = history.map((d) => (d.net_inflow / 1e8).toFixed(2));
    const shInflows = history.map((d) => (d.sh_net_inflow / 1e8).toFixed(2));
    const szInflows = history.map((d) => (d.sz_net_inflow / 1e8).toFixed(2));

    const option: echarts.EChartsOption = {
      tooltip: {
        backgroundColor: '#161B22',
        borderColor: '#30363D',
        textStyle: { color: '#E6EDF3', fontSize: 12 },
        trigger: 'axis',
        formatter: (params: any) => {
          if (!params || params.length === 0) return '';
          const date = params[0].axisValue;
          let html = `<div style="font-weight:bold;margin-bottom:4px">${date}</div>`;
          params.forEach((p: any) => {
            const v = parseFloat(p.value);
            const color = v >= 0 ? '#EF5350' : '#26A69A';
            html += `<div style="display:flex;justify-content:space-between;gap:12px"><span>${p.marker} ${p.seriesName}</span><span style="color:${color}">${v >= 0 ? '+' : ''}${v}亿</span></div>`;
          });
          return html;
        },
      },
      legend: {
        top: 0,
        textStyle: { color: '#8B949E', fontSize: 12 },
        data: ['净流入', '沪股通', '深股通'],
      },
      grid: { left: '3%', right: '4%', bottom: '3%', top: '40px', containLabel: true },
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: '#30363D' } },
        axisLabel: { color: '#8B949E', fontSize: 10, rotate: dates.length > 15 ? 45 : 0 },
      },
      yAxis: {
        type: 'value',
        name: '亿元',
        nameTextStyle: { color: '#8B949E', fontSize: 11 },
        axisLine: { lineStyle: { color: '#30363D' } },
        axisLabel: { color: '#8B949E', fontSize: 10 },
        splitLine: { lineStyle: { color: '#21262D' } },
      },
      series: [
        {
          name: '净流入',
          type: 'bar',
          data: inflows,
          itemStyle: {
            color: (params: any) => parseFloat(params.value) >= 0 ? '#EF5350' : '#26A69A',
            borderRadius: [2, 2, 0, 0],
          },
        },
        {
          name: '沪股通',
          type: 'line',
          data: shInflows,
          smooth: true,
          lineStyle: { color: '#58A6FF', width: 1.5 },
          itemStyle: { color: '#58A6FF' },
          symbol: 'none',
        },
        {
          name: '深股通',
          type: 'line',
          data: szInflows,
          smooth: true,
          lineStyle: { color: '#D29922', width: 1.5 },
          itemStyle: { color: '#D29922' },
          symbol: 'none',
        },
      ],
    };

    chart.setOption(option);
    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.dispose();
    };
  }, [history]);

  const last10 = history.slice(-10).reverse();

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

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text flex items-center gap-2">
          <ArrowRightLeft size={22} className="text-accent" />
          北向资金监控
        </h1>
        <p className="text-text-secondary text-sm mt-1">
          追踪沪港通/深港通资金流向，外资动向一目了然
        </p>
      </div>

      {/* 摘要卡片 */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="text-xs text-text-secondary mb-1">今日净流入</div>
            <div className={`text-xl font-mono font-bold ${getChangeColor(summary.latest_inflow)}`}>
              {formatYi(summary.latest_inflow)}
            </div>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="text-xs text-text-secondary mb-1">连续流入天数</div>
            <div className="text-xl font-mono font-bold text-up flex items-center gap-1.5">
              <TrendingUp size={18} />
              {summary.consecutive_inflow_days}天
            </div>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="text-xs text-text-secondary mb-1">累计余额</div>
            <div className="text-xl font-mono font-bold text-text">
              {history.length > 0 ? ((history[history.length - 1]?.hold_balance || 0) / 1e8).toFixed(0) : '--'}亿
            </div>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="text-xs text-text-secondary mb-1">趋势方向</div>
            <div className={`text-xl font-bold ${summary.trend.includes('流入') ? 'text-up' : summary.trend.includes('流出') ? 'text-down' : 'text-warn'}`}>
              {summary.trend}
            </div>
          </div>
        </div>
      )}

      {/* ECharts 折线图 */}
      {history.length > 0 && (
        <div className="bg-card border border-border rounded-lg p-6 mb-6">
          <h3 className="text-lg font-bold text-text mb-4">净流入趋势（近30日）</h3>
          <div ref={chartRef} style={{ height: '400px' }} />
        </div>
      )}

      {/* 近10日数据表格 */}
      {history.length > 0 && (
        <div className="bg-card border border-border rounded-lg p-6 mb-6">
          <h3 className="text-lg font-bold text-text mb-4 flex items-center gap-2">
            <Clock size={18} className="text-text-secondary" />
            近10日明细
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-text-secondary text-left border-b border-border">
                  <th className="pb-2 font-medium">日期</th>
                  <th className="pb-2 font-medium text-right">净流入</th>
                  <th className="pb-2 font-medium text-right">沪股通</th>
                  <th className="pb-2 font-medium text-right">深股通</th>
                  <th className="pb-2 font-medium text-right">余额</th>
                </tr>
              </thead>
              <tbody>
                {last10.map((item) => (
                  <tr key={item.date} className="border-b border-border/50">
                    <td className="py-2.5 font-medium">{item.date}</td>
                    <td className={`py-2.5 text-right font-mono ${getChangeColor(item.net_inflow)}`}>
                      {formatYi(item.net_inflow)}
                    </td>
                    <td className={`py-2.5 text-right font-mono ${getChangeColor(item.sh_net_inflow)}`}>
                      {formatYi(item.sh_net_inflow)}
                    </td>
                    <td className={`py-2.5 text-right font-mono ${getChangeColor(item.sz_net_inflow)}`}>
                      {formatYi(item.sz_net_inflow)}
                    </td>
                    <td className="py-2.5 text-right font-mono text-text-secondary">
                      {(item.balance / 1e8).toFixed(0)}亿
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 小白解读 */}
      <div className="bg-card border border-border rounded-lg p-6">
        <h3 className="text-lg font-bold text-text mb-3 flex items-center gap-2">
          <HelpCircle size={18} className="text-accent" />
          小白解读
        </h3>
        <div className="text-sm text-text-secondary leading-relaxed space-y-2">
          <p>
            <strong className="text-text">北向资金</strong>是指通过沪港通和深港通从香港市场流入A股的资金，
            代表了<strong className="text-text">外资（国际资本）</strong>对A股市场的态度和操作方向。
          </p>
          <p>
            <span className="text-up">净流入</span>意味着外资在买入A股，说明看好中国市场；
            <span className="text-down">净流出</span>则相反，可能是避险或获利了结。
          </p>
          <p>
            北向资金被称为<strong className="text-warn">"聪明钱"</strong>，因为它们往往能提前嗅到市场机会。
            连续多日流入通常预示着行情回暖，而大额连续流出则需要警惕。
          </p>
          <p className="text-xs text-text-secondary mt-2 pt-2 border-t border-border">
            💡 提示：北向资金只是参考指标之一，投资决策还需结合基本面和技术面综合判断。
          </p>
        </div>
      </div>
    </div>
  );
}
