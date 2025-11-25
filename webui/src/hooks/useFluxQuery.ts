import {useState, useEffect, useRef} from 'react';
import { InfluxDB } from '@influxdata/influxdb-client-browser';
import { queryReloadInterval, queryJitterInterval } from '../globals';

// Helper to detect if error is due to missing InfluxDB configuration
function isConfigurationError(error: Error | null): boolean {
  if (!error) return false;
  const message = error.message || String(error);
  return message.includes('Missing INFLUX_HOST or INFLUX_TOKEN') || 
         message.includes('500 INTERNAL SERVER ERROR');
}

function useFluxQuery({ fluxQuery, reloadInterval = queryReloadInterval }: { fluxQuery: string, reloadInterval?: number }) {
  const url = '/influx';
  const token = 'hardcoded-token'; // Replace with your actual token
  const org = 'home';

  const [loading, setLoading] = useState(true);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [result, setResult] = useState<any[]>([]);
  const [unavailable, setUnavailable] = useState(false);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    // Skip effect if already marked as unavailable
    if (unavailable) return;

    let cancelled = false;
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
            const err = e instanceof Error ? e : new Error(String(e));
            setError(err);
            setLoading(false);
            setInitialLoading(false);
            setResult([]);
            // If this is a configuration error, mark as unavailable and stop polling
            if (isConfigurationError(err)) {
              setUnavailable(true);
              if (intervalRef.current) {
                clearInterval(intervalRef.current);
                intervalRef.current = null;
              }
            }
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
      intervalRef.current = setInterval(runQuery, reloadInterval);
    }, jitter);

    return () => {
      cancelled = true;
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      if (timerId) clearTimeout(timerId);
    };
  }, [fluxQuery, reloadInterval, unavailable]);

  return { initialLoading, loading, error, result, unavailable };
}

export default useFluxQuery;
