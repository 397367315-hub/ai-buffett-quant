'use client';

import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import { apiFetch } from '@/lib/api';

export default function HeatmapPage() {
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const fetchAndRender = async () => {
      try {
        const res = await apiFetch<any>('/flow/concept/rank?limit=50');
        const rankings = res.data.rankings;

        if (chartRef.current && rankings.length > 0) {
          const chart = echarts.init(chartRef.current);

          const treemapData = rankings.slice(0, 30).map((item: any) => ({
            name: item.name,
            value: Math.abs(item.main_net_inflow),
            itemStyle: {
              color: item.main_net_inflow >= 0
                ? `rgba(38, 166, 154, ${Math.min(0.9, 0.3 + Math.abs(item.change_pct) * 0.05)})`
                : `rgba(239, 83, 80, ${Math.min(0.9, 0.3 + Math.abs(item.change_pct) * 0.05)})`,
            },
            changePct: item.change_pct,
            mainNetInflow: item.main_net_inflow,
          }));

          const option: echarts.EChartsOption = {
            tooltip: {
              backgroundColor: '#161B22',
              borderColor: '#30363D',
              textStyle: { color: '#E6EDF3', fontSize: 12 },
              formatter: (params: any) => {
                const d = params.data;
                const yi = (d.mainNetInflow / 1e8).toFixed(2);
                return `${d.name}<br/>主力净流入：${yi}亿<br/>涨跌幅：${d.changePct?.toFixed(2)}%`;
              },
            },
            series: [{
              type: 'treemap',
              width: '100%',
              height: '100%',
              roam: false,
              nodeClick: false,
              breadcrumb: { show: false },
              label: {
                show: true,
                formatter: (params: any) => {
                  const name = params.name;
                  const yi = (params.data.mainNetInflow / 1e8).toFixed(1);
                  return `{name|${name}}\n{value|${yi}亿}`;
                },
                rich: {
                  name: { color: '#E6EDF3', fontSize: 12, lineHeight: 18 },
                  value: { color: '#8B949E', fontSize: 11, lineHeight: 16 },
                },
              },
              upperLabel: { show: true, height: 20 },
              data: treemapData,
            }],
          };

          chart.setOption(option);
          const handleResize = () => chart.resize();
          window.addEventListener('resize', handleResize);
          return () => {
            window.removeEventListener('resize', handleResize);
            chart.dispose();
          };
        }
      } catch (err) {
        console.error('Failed to fetch heatmap data:', err);
      }
    };

    fetchAndRender();
  }, []);

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <h1 className="text-2xl font-bold text-text mb-2">板块轮动热力图</h1>
      <p className="text-text-secondary mb-6">矩形面积=资金规模 | 颜色深浅=涨跌幅 | 红=流入，绿/红=涨跌</p>
      <div className="bg-card border border-border rounded-lg p-4">
        <div ref={chartRef} style={{ height: '650px' }} />
      </div>
    </div>
  );
}
