import { useState, useEffect } from 'react';
import { queryReloadInterval, queryJitterInterval } from '../lib/globals';

export interface PromQLInstantParams {
  query: string;
  type?: 'instant';
  reloadInterval?: number;
}

export interface PromQLRangeParams {
  query: string;
  type: 'range';
  start: string;
  end: string;
  step: string;
  reloadInterval?: number;
}

export type PromQLQueryParams = PromQLInstantParams | PromQLRangeParams;

export interface PromQLRow {
  _time: string;
  value: number;
  [key: string]: any;
}

function usePromQLQuery(params: PromQLQueryParams) {
  const { query, reloadInterval = queryReloadInterval } = params;
  const type = params.type || 'instant';

  const [loading, setLoading] = useState(true);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [result, setResult] = useState<PromQLRow[]>([]);

  // Stable key for range params
  const rangeKey = type === 'range'
    ? `${(params as PromQLRangeParams).start}|${(params as PromQLRangeParams).end}|${(params as PromQLRangeParams).step}`
    : '';

  useEffect(() => {
    let cancelled = false;
    let intervalId: NodeJS.Timeout | null = null;
    let timerId: NodeJS.Timeout | null = null;

    const runQuery = async () => {
      setLoading(true);
      setError(null);

      try {
        let url: string;
        if (type === 'range') {
          const { start, end, step } = params as PromQLRangeParams;
          const qs = new URLSearchParams({ query, start, end, step });
          url = `/influx/api/v1/query_range?${qs}`;
        } else {
          const qs = new URLSearchParams({ query });
          url = `/influx/api/v1/query?${qs}`;
        }

        const response = await fetch(url, {
          headers: { 'Accept': 'application/json' },
        });

        if (!response.ok) {
          throw new Error(`Query failed: ${response.statusText}`);
        }

        const data = await response.json();
        const promResults = data?.data?.result ?? [];
        const rows: PromQLRow[] = [];

        if (type === 'range') {
          // Range query: result[].values = [[ts, val], ...]
          for (const series of promResults) {
            const labels = { ...series.metric };
            delete labels.__name__;
            for (const [ts, val] of series.values || []) {
              rows.push({
                ...labels,
                _time: new Date(ts * 1000).toISOString(),
                value: parseFloat(val),
              });
            }
          }
        } else {
          // Instant query: result[].value = [ts, val]
          for (const series of promResults) {
            const labels = { ...series.metric };
            delete labels.__name__;
            rows.push({
              ...labels,
              _time: new Date(series.value[0] * 1000).toISOString(),
              value: parseFloat(series.value[1]),
            });
          }
        }

        if (!cancelled) {
          setResult(rows);
          setLoading(false);
          setInitialLoading(false);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e : new Error(String(e)));
          setLoading(false);
          setResult([]);
        }
      }
    };

    const jitter = Math.random() * queryJitterInterval;
    timerId = setTimeout(() => {
      if (cancelled) return;
      runQuery();
      intervalId = setInterval(runQuery, reloadInterval);
    }, jitter);

    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
      if (timerId) clearTimeout(timerId);
    };
  }, [query, type, rangeKey, reloadInterval]);

  return { initialLoading, loading, error, result };
}

export default usePromQLQuery;
