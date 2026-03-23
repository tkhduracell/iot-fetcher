'use client';

import React, { useMemo } from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

echarts.use([LineChart, GridComponent, CanvasRenderer]);

interface SparklineChartProps {
  data: { time: number; value: number }[];
  fixedMin?: number;
  fixedMax?: number;
}

const SparklineChart: React.FC<SparklineChartProps> = ({ data, fixedMin, fixedMax }) => {
  const [autoMin, autoMax] = useMemo(() => {
    let lo = Infinity, hi = -Infinity;
    for (const d of data) { lo = Math.min(lo, d.value); hi = Math.max(hi, d.value); }
    return [lo, hi];
  }, [data]);

  const dataMin = fixedMin ?? autoMin;
  const dataMax = fixedMax ?? autoMax;

  const option = useMemo(() => {
    const range = dataMax - dataMin;
    const autoPad = range > 0 ? range * 0.1 : 1;

    return {
      grid: { top: 0, right: 0, bottom: 0, left: 0 },
      xAxis: { type: 'time' as const, show: false },
      yAxis: {
        type: 'value' as const,
        show: false,
        min: fixedMin != null ? dataMin : dataMin - autoPad,
        max: fixedMax != null ? dataMax : dataMax + autoPad,
      },
      series: [{
        type: 'line',
        data: data.map(d => [d.time, d.value]),
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1.5, color: 'rgba(147, 197, 253, 0.5)' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(147, 197, 253, 0.3)' },
            { offset: 1, color: 'rgba(147, 197, 253, 0.05)' },
          ]),
        },
      }],
      animation: false,
    };
  }, [data, dataMin, dataMax, fixedMin, fixedMax]);

  if (data.length === 0) return null;

  return (
    <ReactEChartsCore
      echarts={echarts}
      option={option}
      style={{ width: '100%', height: '100%' }}
      opts={{ renderer: 'canvas' }}
      notMerge
    />
  );
};

export default SparklineChart;
