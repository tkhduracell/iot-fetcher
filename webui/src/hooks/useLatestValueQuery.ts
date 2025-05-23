import { useMemo } from 'react';
import useFluxQuery from './useFluxQuery';

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
  const fullFilter = useMemo(() => ({
    '_measurement': measurement,
    '_field': field,
    ...filter
  }), [measurement, field, filter]);

  const filterQuery = useMemo(() => Object.entries(fullFilter)
    .map(([k,v]) => `|> filter(fn: (r) => r["${k}"] == "${v}") `)
    .join('\n    '), [fullFilter]);

  const fluxQuery = useMemo(() => `from(bucket: "${bucket}")
    |> range(start: ${range})
    ${filterQuery}
    |> aggregateWindow(every: ${window}, fn: last, createEmpty: false)
    |> yield(name: "last")`, [bucket, range, filterQuery, window]);

  return useFluxQuery({ fluxQuery: fluxQuery.toString(), reloadInterval: reload });
}

export default useLatestValueQuery;