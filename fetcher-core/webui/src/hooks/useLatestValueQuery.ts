import { useMemo } from 'react';
import useInfluxQLQuery from './useInfluxQLQuery';

export interface LatestValueQueryParams {
  bucket?: string;
  measurement: string;
  field: string;
  filter?: {[key: string]: string};
  window?: string;
  range?: string;
  reload?: number;
}

function useLatestValueQuery({ bucket = "irisgatan", measurement, field, filter = {}, window = "5m", range = "-15m", reload }: LatestValueQueryParams) {

  const influxQLQuery = useMemo(() => {
    // Build WHERE clause
    const whereClauses = [`time > now() ${range}`];

    Object.entries(filter).forEach(([key, value]) => {
      whereClauses.push(`"${key}" = '${value}'`);
    });

    const whereClause = whereClauses.join(' AND ');

    // Convert window format (5m -> 5m, 1h -> 1h, etc.)
    // InfluxQL uses same format as Flux for time durations

    // Build InfluxQL query: get last value within time windows
    return `SELECT LAST("${field}") AS value, "${field}" AS _value
            FROM "${measurement}"
            WHERE ${whereClause}
            GROUP BY time(${window})
            ORDER BY time DESC
            LIMIT 1`;
  }, [measurement, field, filter, window, range]);

  const { initialLoading, loading, error, result } = useInfluxQLQuery({
    query: influxQLQuery,
    database: bucket,
    reloadInterval: reload
  });

  // Transform result to match Flux format for backward compatibility
  const transformedResult = useMemo(() => {
    return result.map(row => ({
      _measurement: measurement,
      _field: field,
      _time: row._time,
      _value: row._value ?? row.value,
      ...filter
    }));
  }, [result, measurement, field, filter]);

  return { initialLoading, loading, error, result: transformedResult };
}

export default useLatestValueQuery;
