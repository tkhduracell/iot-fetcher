import React from 'react';
import { flux, InfluxDB } from '@influxdata/influxdb-client-browser';
import { globals } from './globals';

function useFluxQuery({ fluxQuery }: { fluxQuery: string }) {
  const url = '/influx';
  const token = 'hardcoded-token'; // Replace with your actual token
  const org = 'home';

  const [loading, setLoading] = React.useState(true);
  const [initalLoading, setInitalLoading] = React.useState(true);
  const [error, setError] = React.useState<Error | null>(null);
  const [result, setResult] = React.useState<any[]>([]);

  React.useEffect(() => {
    let cancelled = false;
    let intervalId: NodeJS.Timeout | null = null;

    const runQuery = () => {
      setLoading(true);
      setError(null);
      const client = new InfluxDB({ url, token });
      const queryApi = client.getQueryApi(org);
      const rows: any[] = [];
      queryApi.queryRows(fluxQuery, {
        next(row, tableMeta) {
          if (!cancelled) rows.push(tableMeta.toObject(row));
        },
        error(e) {
          if (!cancelled) {
            setError(e instanceof Error ? e : new Error(String(e)));
            setLoading(false);
            setResult([])
          }
        },
        complete() {
          if (!cancelled) {
            setResult(rows);
            setLoading(false)
            setInitalLoading(false);
          }
        }
      });
    };

    runQuery();
    intervalId = setInterval(runQuery, globals.queryReloadInterval);

    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
    };
  }, [fluxQuery]);

  return { initalLoading, loading, error, result };
}

export default useFluxQuery;
