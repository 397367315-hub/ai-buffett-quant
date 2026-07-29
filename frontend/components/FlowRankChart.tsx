'use client';

import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import { FlowRankItem } from '@/lib/types';

interface Props {
  data: FlowRankItem[];
}

export default function FlowRankChart({ data }: Props) {
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!chartRef.current || data.length === 0) return;

    const chart = echarts.init(chartRef.current);
    const sorted = [...data].sort((a, b) => b.main_net_inflow - a.main_net_inflow);
    const top10 = sorted.slice(0, 10).reverse();
    const bottom10 = sorted.slice(-10);
    const displayData = [...bottom10, ...top10];

    const option: echarts.EChartsOption = {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: '#161B22',
        borderColor: '#30363D',
        textStyle: { color: '#E6EDF3', fontSize: 12 },
        formatter: (params: any) => {
          const p = params[0];
          const valueYi = (p.value / 1e8).toFixed(2);
          const color = p.value >= 0 ? '#EF5350' : '#26A69A';
          return `<span style="color:${color};font-weight:bold">${p.name}</span><br/>
                  主力净流入：<b>${valueYi}亿</b><br/>
                  涨跌幅：${(p.data as any).changePct?.toFixed(2) ?? '--'}%`;
        },
      },
      grid: { left: '3%', right: '8%', top: '3%', bottom: '3%', containLabel: true },
      xAxis: {
        type: 'value',
        axisLabel: { formatter: (v: number) => (v / 1e8).toFixed(0) + '亿', color: '#8B949E', fontSize: 10 },
        splitLine: { lineStyle: { color: '#21262D' } },
      },
      yAxis: {
        type: 'category',
        data: displayData.map((d) => d.name),
        axisLabel: { color: '#E6EDF3', fontSize: 11 },
        axisLine: { lineStyle: { color: '#30363D' } },
      },
      series: [{
        type: 'bar',
        data: displayData.map((d) => ({
          value: d.main_net_inflow,
          changePct: d.change_pct,
          itemStyle: {
            color: d.main_net_inflow >= 0 ? '#EF5350' : '#26A69A',
            borderRadius: d.main_net_inflow >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4],
          },
        })),
        label: {
          show: true,
          position: 'right',
          formatter: (params: any) => (params.value / 1e8).toFixed(1) + '亿',
          color: '#8B949E',
          fontSize: 10,
        },
        barMaxWidth: 28,
      }],
    };

    chart.setOption(option);
    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.dispose();
    };
  }, [data]);

  return <div ref={chartRef} style={{ height: '550px' }} />;
}
