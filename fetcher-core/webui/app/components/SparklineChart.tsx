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
}

const SparklineChart: React.FC<SparklineChartProps> = ({ data }) => {
  const option = useMemo(() => ({
    grid: { top: 0, right: 0, bottom: 0, left: 0 },
    xAxis: { type: 'time' as const, show: false },
    yAxis: { type: 'value' as const, show: false },
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
  }), [data]);

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
