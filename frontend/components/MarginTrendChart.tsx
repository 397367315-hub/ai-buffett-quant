'use client';

import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';

interface MarginHistoryRow {
  trade_date: string;
  financing_balance?: number | null;
  financing_ratio?: number | null;
  financing_net_buy?: number | null;
  market_index_close?: number | null;
}

interface Props {
  rows: MarginHistoryRow[];
  height?: number;
  mode?: 'market' | 'stock';
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

export default function MarginTrendChart({ rows, height = 330, mode = 'market' }: Props) {
  const option = useMemo(() => {
    const dates = rows.map((row) => row.trade_date);
    const balances = rows.map((row) => finite(row.financing_balance) ? row.financing_balance! / 1e8 : null);
    const secondary = rows.map((row) => {
      const value = mode === 'market' ? row.market_index_close : row.financing_ratio;
      return finite(value) ? value : null;
    });
    const netBuys = rows.map((row) => finite(row.financing_net_buy) ? row.financing_net_buy! / 1e8 : null);
    return {
      animation: false,
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        backgroundColor: '#0D1117EE',
        borderColor: '#30363D',
        textStyle: { color: '#E6EDF3', fontSize: 11 },
      },
      legend: {
        top: 0,
        right: 8,
        itemWidth: 14,
        itemHeight: 7,
        textStyle: { color: '#8B949E', fontSize: 10 },
      },
      grid: [
        { left: 58, right: 58, top: 34, height: '58%' },
        { left: 58, right: 58, top: '76%', height: '12%' },
      ],
      xAxis: [
        {
          type: 'category', data: dates, boundaryGap: false,
          axisLine: { lineStyle: { color: '#30363D' } },
          axisLabel: { color: '#8B949E', fontSize: 9, hideOverlap: true },
          splitLine: { show: false },
        },
        {
          type: 'category', gridIndex: 1, data: dates, boundaryGap: true,
          axisLine: { lineStyle: { color: '#30363D' } },
          axisLabel: { show: false }, splitLine: { show: false },
        },
      ],
      yAxis: [
        {
          type: 'value', scale: true, name: '亿元', nameTextStyle: { color: '#667085', fontSize: 9 },
          axisLabel: { color: '#8B949E', fontSize: 9 },
          splitLine: { lineStyle: { color: '#21262D' } },
        },
        {
          type: 'value', scale: true,
          name: mode === 'market' ? '指数' : '%',
          nameTextStyle: { color: '#667085', fontSize: 9 },
          axisLabel: { color: '#8B949E', fontSize: 9 },
          splitLine: { show: false },
        },
        {
          type: 'value', gridIndex: 1,
          axisLabel: { color: '#667085', fontSize: 8 },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: rows.length > 80 ? 65 : 0, end: 100 },
        {
          type: 'slider', xAxisIndex: [0, 1], start: rows.length > 80 ? 65 : 0, end: 100,
          bottom: 0, height: 15, borderColor: '#30363D', backgroundColor: '#0D1117',
          fillerColor: '#1F6FEB33', handleStyle: { color: '#58A6FF' },
          textStyle: { color: '#667085', fontSize: 8 },
        },
      ],
      series: [
        {
          name: '融资余额', type: 'line', data: balances, symbol: 'none', smooth: false,
          lineStyle: { color: '#58A6FF', width: 1.6 }, areaStyle: { color: '#1F6FEB18' },
        },
        {
          name: mode === 'market' ? '上证指数' : '融资杠杆率', type: 'line', data: secondary,
          yAxisIndex: 1, symbol: 'none', smooth: false, lineStyle: { color: '#D29922', width: 1.2 },
        },
        {
          name: '融资净买入', type: 'bar', data: netBuys, xAxisIndex: 1, yAxisIndex: 2,
          barMaxWidth: 8, itemStyle: { color: (params: { value: number }) => params.value >= 0 ? '#EF5350' : '#26A69A' },
        },
      ],
    };
  }, [mode, rows]);

  if (!rows.length) {
    return <div className="grid h-[260px] place-items-center text-xs text-text-secondary">暂无两融历史趋势</div>;
  }
  return <ReactECharts option={option} notMerge lazyUpdate style={{ width: '100%', height }} />;
}
