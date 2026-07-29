import { useMemo } from 'react';
import usePromQLQuery from './usePromQLQuery';

interface UseHistoryQueryParams {
  measurement: string;
  field: string;
  filter?: Record<string, string>;
  expr?: string;
  sparkline: string; // e.g. "24h", "12h", "1h"
}

function useHistoryQuery({ measurement, field, filter = {}, expr, sparkline }: UseHistoryQueryParams) {
  const enabled = Boolean(sparkline);

  const labelParts = Object.entries(filter).map(([k, v]) => `${k}="${v}"`);
  const selector = labelParts.length > 0 ? `{${labelParts.join(',')}}` : '';
  const inner = expr ?? `${measurement}_${field}${selector}`;

  const query = enabled ? `avg(avg_over_time(${inner}[5m]))` : 'up';

  const { start, end, step } = useMemo(() => {
    if (!enabled) return { start: new Date().toISOString(), end: new Date().toISOString(), step: '5m' };
    const now = new Date();
    const match = sparkline.match(/^(\d+)([hmd])$/);
    if (!match) return { start: now.toISOString(), end: now.toISOString(), step: '5m' };
    const amount = parseInt(match[1], 10);
    const unit = match[2];
    const ms = unit === 'h' ? amount * 3600000 : unit === 'd' ? amount * 86400000 : amount * 60000;
    const startDate = new Date(now.getTime() - ms);
    return {
      start: startDate.toISOString(),
      end: now.toISOString(),
      step: '5m',
    };
  }, [sparkline, enabled]);

  const { result } = usePromQLQuery({
    query,
    type: 'range',
    start,
    end,
    step,
    reloadInterval: 300000, // 5 minutes
  });

  const data = useMemo(() => {
    if (!enabled) return [];
    return result.map(row => ({
      time: new Date(row._time).getTime(),
      value: row.value,
    }));
  }, [result, enabled]);

  return data;
}

export default useHistoryQuery;
