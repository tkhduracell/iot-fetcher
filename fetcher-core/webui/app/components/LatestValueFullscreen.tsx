import React, { useMemo } from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, DataZoomComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

import { ConfigValue } from '../lib/types';
import usePromQLQuery from '../hooks/usePromQLQuery';
import { subDays } from 'date-fns';

echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, CanvasRenderer]);

interface LatestValueFullscreenProps extends ConfigValue {
  open: boolean;
  onClose: () => void;
}

const LatestValueFullscreen: React.FC<LatestValueFullscreenProps> = ({
  open, onClose,
  filter = {},
  measurement,
  title,
  field,
  unit,
  sparklineMin,
  sparklineMax,
  window = '5m',
}) => {
  const now = useMemo(() => new Date(), []);
  const start = useMemo(() => subDays(now, 7), [now]);

  const promQuery = useMemo(() => {
    const metricName = `${measurement}_${field}`;
    const labelParts = Object.entries(filter).map(([k, v]) => `${k}="${v}"`);
    const selector = labelParts.length > 0 ? `{${labelParts.join(',')}}` : '';
    return `avg_over_time(${metricName}${selector}[15m])`;
  }, [measurement, field, filter]);

  const { initialLoading, error, result } = usePromQLQuery({
    query: promQuery,
    type: 'range',
    start: start.toISOString(),
    end: now.toISOString(),
    step: '15m',
    reloadInterval: 60000,
  });

  const chartOption = useMemo(() => {
    const seriesData = result.map(r => [new Date(r._time).getTime(), r.value]);

    return {
      grid: { top: 40, right: 40, bottom: 80, left: 60 },
      tooltip: {
        trigger: 'axis',
        formatter: (params: any) => {
          const p = params[0];
          if (!p) return '';
          const date = new Date(p.value[0]);
          const formatted = date.toLocaleString('sv-SE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
          return `${formatted}<br/><strong>${p.value[1]?.toFixed(1)} ${unit}</strong>`;
        },
      },
      xAxis: {
        type: 'time',
        axisLabel: {
          color: 'rgba(255,255,255,0.5)',
          fontSize: 11,
        },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.15)' } },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        min: sparklineMin,
        max: sparklineMax,
        axisLabel: {
          color: 'rgba(255,255,255,0.5)',
          fontSize: 11,
          formatter: (v: number) => `${v} ${unit}`,
        },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.08)' } },
      },
      dataZoom: [
        { type: 'inside', start: 0, end: 100 },
        {
          type: 'slider',
          bottom: 20,
          height: 24,
          borderColor: 'rgba(255,255,255,0.1)',
          backgroundColor: 'rgba(255,255,255,0.05)',
          fillerColor: 'rgba(147, 197, 253, 0.15)',
          textStyle: { color: 'rgba(255,255,255,0.5)', fontSize: 10 },
        },
      ],
      series: [{
        type: 'line',
        data: seriesData,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 2, color: 'rgba(147, 197, 253, 0.8)' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(147, 197, 253, 0.3)' },
            { offset: 1, color: 'rgba(147, 197, 253, 0.02)' },
          ]),
        },
      }],
      animation: false,
    };
  }, [result, unit, sparklineMin, sparklineMax]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85" onClick={onClose}>
      <div
        className="bg-gray-900 rounded-lg shadow-lg p-6 w-[90vw] h-[80vh] relative flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <button
          className="absolute top-3 right-4 text-4xl text-gray-400 hover:text-red-400 leading-none"
          onClick={onClose}
          aria-label="Close"
        >
          &times;
        </button>

        <h2 className="text-2xl font-semibold text-blue-100 mb-4">{title} — 7 days</h2>

        {initialLoading && !error && <div className="text-xl text-gray-400 m-auto">Loading...</div>}
        {error && <div className="text-xl text-red-400 m-auto">Error: {error.message}</div>}

        {!initialLoading && !error && (
          <div className="flex-1 min-h-0">
            <ReactEChartsCore
              echarts={echarts}
              option={chartOption}
              style={{ width: '100%', height: '100%' }}
              opts={{ renderer: 'canvas' }}
              notMerge
            />
          </div>
        )}
      </div>
    </div>
  );
};

export default LatestValueFullscreen;
