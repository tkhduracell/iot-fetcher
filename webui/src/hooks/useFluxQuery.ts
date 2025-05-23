import {useState, useEffect} from 'react';
import { InfluxDB } from '@influxdata/influxdb-client-browser';
import { queryReloadInterval, queryJitterInterval } from '../globals';

function useFluxQuery({ fluxQuery, reloadInterval = queryReloadInterval }: { fluxQuery: string, reloadInterval?: number }) {
  const url = '/influx';
  const token = 'hardcoded-token'; // Replace with your actual token
  const org = 'home';

  const [loading, setLoading] = useState(true);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [result, setResult] = useState<any[]>([]);

  useEffect(() => {
    let cancelled = false;
    let intervalId: NodeJS.Timeout | null = null;
    let timerId: NodeJS.Timeout | null = null;

    const client = new InfluxDB({ url, token });
    const queryApi = client.getQueryApi(org);

    const runQuery = () => {
      setLoading(true);
      setError(null);
      const rows: any[] = [];
      queryApi.queryRows(fluxQuery, {
        next(row, tableMeta) {
          if (!cancelled) rows.push(tableMeta.toObject(row));
        },
        error(e) {
          if (!cancelled) {
            setError(e instanceof Error ? e : new Error(String(e)));
            setLoading(false);
            setResult([]);
          }
        },
        complete() {
          if (!cancelled) {
            setResult(rows);
            setLoading(false);
            setInitialLoading(false);
          }
        },
      });
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
  }, [fluxQuery, reloadInterval]);

  return { initialLoading, loading, error, result };
}

export default useFluxQuery;
