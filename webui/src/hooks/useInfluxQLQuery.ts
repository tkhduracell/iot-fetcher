import { useState, useEffect } from 'react';
import { queryReloadInterval, queryJitterInterval } from '../globals';

export interface InfluxQLQueryParams {
  query: string;
  database?: string;
  reloadInterval?: number;
}

function useInfluxQLQuery({ query, database = 'irisgatan', reloadInterval = queryReloadInterval }: InfluxQLQueryParams) {
  const [loading, setLoading] = useState(true);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [result, setResult] = useState<any[]>([]);

  useEffect(() => {
    let cancelled = false;
    let intervalId: NodeJS.Timeout | null = null;
    let timerId: NodeJS.Timeout | null = null;

    const runQuery = async () => {
      setLoading(true);
      setError(null);

      try {
        const params = new URLSearchParams({
          db: database,
          q: query
        });

        const response = await fetch(`/influx/query?${params.toString()}`, {
          method: 'GET',
          headers: {
            'Accept': 'application/json'
          }
        });

        if (!response.ok) {
          throw new Error(`Query failed: ${response.statusText}`);
        }

        const data = await response.json();

        // Parse InfluxQL JSON response
        // Expected format: {"results": [{"series": [{"columns": [...], "values": [[...]]}]}]}
        const rows: any[] = [];

        if (data.results && data.results.length > 0) {
          const result = data.results[0];

          if (result.series) {
            result.series.forEach((series: any) => {
              const columns = series.columns || [];
              const values = series.values || [];
              const tags = series.tags || {};

              values.forEach((row: any[]) => {
                const obj: any = { ...tags };
                columns.forEach((col: string, idx: number) => {
                  if (col === 'time') {
                    obj._time = row[idx];
                  } else {
                    obj[col] = row[idx];
                  }
                });
                rows.push(obj);
              });
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
  }, [query, database, reloadInterval]);

  return { initialLoading, loading, error, result };
}

export default useInfluxQLQuery;
