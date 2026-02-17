import { useMemo } from 'react';
import usePromQLQuery from './usePromQLQuery';

export interface LatestValueQueryParams {
  bucket?: string;
  measurement: string;
  field: string;
  filter?: {[key: string]: string};
  window?: string;
  range?: string;
  reload?: number;
}

function useLatestValueQuery({ measurement, field, filter = {}, window = "5m", range = "-15m", reload }: LatestValueQueryParams) {
  const promQuery = useMemo(() => {
    const metricName = `${measurement}_${field}`;
    const labelParts = Object.entries(filter).map(([k, v]) => `${k}="${v}"`);
    const selector = labelParts.length > 0 ? `{${labelParts.join(',')}}` : '';

    // Parse range string like "-15m" → "15m"
    const lookback = range.replace(/^-/, '') || window;

    return `last_over_time(${metricName}${selector}[${lookback}])`;
  }, [measurement, field, filter, window, range]);

  const { initialLoading, loading, error, result } = usePromQLQuery({
    query: promQuery,
    reloadInterval: reload,
  });

  const transformedResult = useMemo(() => {
    return result.map(row => ({
      _measurement: measurement,
      _field: field,
      _time: row._time,
      _value: row.value,
      ...filter
    }));
  }, [result, measurement, field, filter]);

  return { initialLoading, loading, error, result: transformedResult };
}

export default useLatestValueQuery;
