'use client';

import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';

export interface KlineRow {
  date: string;
  open: number | null;
  close: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
  amount?: number | null;
  change_pct?: number | null;
}

interface Props {
  rows: KlineRow[];
  height?: number | string;
  showMovingAverages?: boolean;
  /** V2 research overlays; they are descriptive chart annotations only. */
  annotations?: Array<Record<string, any>>;
  showAnnotations?: boolean;
}

function finite(value: number | null | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function hasCompletePrice(row: KlineRow): boolean {
  return [row.open, row.close, row.high, row.low].every(
    (value) => typeof value === 'number' && Number.isFinite(value) && value > 0,
  );
}

function movingAverage(rows: KlineRow[], window: number): Array<number | null> {
  return rows.map((_row, index) => {
    if (index + 1 < window) return null;
    const closes = rows.slice(index + 1 - window, index + 1).map((row) => row.close);
    const numericCloses = closes.filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
    if (numericCloses.length !== window) return null;
    return numericCloses.reduce((total, value) => total + value, 0) / window;
  });
}

export default function KlineChart({ rows, height = 360, showMovingAverages = false, annotations = [], showAnnotations = false }: Props) {
  const chartRows = useMemo(() => rows.filter(hasCompletePrice), [rows]);
  const option = useMemo(() => {
    const dates = chartRows.map((row) => row.date);
    const candles = chartRows.map((row) => [
      finite(row.open),
      finite(row.close),
      finite(row.low),
      finite(row.high),
    ]);
    const volumes = chartRows.map((row, index) => ({
      value: finite(row.volume),
      itemStyle: { color: finite(row.close) >= finite(row.open) ? '#EF5350' : '#26A69A' },
      itemIndex: index,
    }));
    const movingAverageSeries = showMovingAverages
      ? [
          { name: 'MA5', data: movingAverage(chartRows, 5), color: '#f0c44b' },
          { name: 'MA10', data: movingAverage(chartRows, 10), color: '#37d9b0' },
          { name: 'MA20', data: movingAverage(chartRows, 20), color: '#d36fff' },
          { name: 'MA30', data: movingAverage(chartRows, 30), color: '#7eaefb' },
          { name: 'MA60', data: movingAverage(chartRows, 60), color: '#39c66e' },
        ].map((line) => ({
          name: line.name,
          type: 'line',
          data: line.data,
          symbol: 'none',
          showSymbol: false,
          connectNulls: false,
          smooth: false,
          z: 3,
          lineStyle: { width: 1.15, color: line.color },
          emphasis: { disabled: true },
        }))
      : [];
    const start = chartRows.length > 80 ? Math.max(0, 100 - (80 / chartRows.length) * 100) : 0;
    const overlayLines = showAnnotations
      ? annotations
          .map((item) => ({
            name: typeof item.type === 'string' ? item.type : '研究标注',
            yAxis: typeof item.key_price === 'number' ? item.key_price : typeof item.upper_boundary === 'number' ? item.upper_boundary : null,
          }))
          .filter((item) => typeof item.yAxis === 'number')
          .slice(0, 20)
      : [];
    const overlayAreas = showAnnotations
      ? annotations
          .filter((item) => typeof item.upper_boundary === 'number' && typeof item.lower_boundary === 'number')
          .map((item) => [
            { name: typeof item.type === 'string' ? item.type : '结构区', xAxis: dates[0], yAxis: item.upper_boundary },
            { xAxis: dates[dates.length - 1], yAxis: item.lower_boundary },
          ])
          .slice(0, 5)
      : [];

    return {
      animation: false,
      backgroundColor: 'transparent',
      axisPointer: { link: [{ xAxisIndex: 'all' }], label: { backgroundColor: '#30363D' } },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        borderColor: '#30363D',
        backgroundColor: '#0D1117EE',
        textStyle: { color: '#C9D1D9', fontSize: 11 },
      },
      grid: [
        { left: 58, right: 18, top: 20, height: '60%' },
        { left: 58, right: 18, top: '73%', height: '16%' },
      ],
      xAxis: [
        {
          type: 'category',
          data: dates,
          boundaryGap: true,
          axisLine: { lineStyle: { color: '#30363D' } },
          axisLabel: { color: '#8B949E', fontSize: 10, hideOverlap: true },
          splitLine: { show: false },
          min: 'dataMin',
          max: 'dataMax',
        },
        {
          type: 'category',
          gridIndex: 1,
          data: dates,
          boundaryGap: true,
          axisLine: { lineStyle: { color: '#30363D' } },
          axisLabel: { show: false },
          splitLine: { show: false },
          min: 'dataMin',
          max: 'dataMax',
        },
      ],
      yAxis: [
        {
          scale: true,
          axisLine: { show: false },
          axisLabel: { color: '#8B949E', fontSize: 10 },
          splitLine: { lineStyle: { color: '#21262D' } },
        },
        {
          scale: true,
          gridIndex: 1,
          axisLine: { show: false },
          axisLabel: {
            color: '#8B949E',
            fontSize: 9,
            formatter: (value: number) => value >= 1e8 ? `${(value / 1e8).toFixed(1)}亿` : `${(value / 1e4).toFixed(0)}万`,
          },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start, end: 100 },
        {
          type: 'slider',
          xAxisIndex: [0, 1],
          start,
          end: 100,
          bottom: 4,
          height: 18,
          borderColor: '#30363D',
          backgroundColor: '#0D1117',
          fillerColor: '#1F6FEB33',
          handleStyle: { color: '#58A6FF' },
          textStyle: { color: '#8B949E', fontSize: 9 },
        },
      ],
        series: [
        {
          name: 'K线',
          type: 'candlestick',
          data: candles,
          itemStyle: {
            color: '#EF5350',
            color0: '#26A69A',
            borderColor: '#EF5350',
            borderColor0: '#26A69A',
          },
          markLine: overlayLines.length ? {
            symbol: ['none', 'none'],
            silent: true,
            precision: 2,
            lineStyle: { width: 0.8, type: 'dashed', color: '#667085', opacity: 0.6 },
            label: { show: true, color: '#9AA4B2', fontSize: 8, formatter: (params: any) => params?.name || '' },
            data: overlayLines,
          } : undefined,
          markArea: overlayAreas.length ? {
            silent: true,
            itemStyle: { color: 'rgba(76,141,255,0.035)' },
            label: { show: false },
            data: overlayAreas,
          } : undefined,
        },
        ...movingAverageSeries,
        {
          name: '成交量',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volumes,
          barMaxWidth: 12,
        },
      ],
    };
  }, [annotations, chartRows, showAnnotations, showMovingAverages]);

  if (chartRows.length === 0) {
    return <div className="grid h-[260px] place-items-center text-xs text-text-secondary">暂无可核验K线</div>;
  }

  return (
    <div className="w-full overflow-hidden" style={{ height }}>
      <ReactECharts option={option} notMerge lazyUpdate style={{ width: '100%', height: '100%' }} />
    </div>
  );
}
