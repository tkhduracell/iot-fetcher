import React from 'react';
import useFluxQuery from './useFluxQuery';
import { flux } from '@influxdata/influxdb-client-browser';

interface LatestValueProps {
  bucket?: string;
  measurement: string;
  field: string;
  filter?: Record<string, string>;
  title?: string;
  decimals?: number;
}

const LatestValue: React.FC<LatestValueProps> = ({
  bucket = "irisgatan",
  measurement,
  field,
  filter = {},
  title,
  decimals = 1,
}) => {
  filter = {
    '_measurement': measurement,
    '_field': field,
    ...filter
  }
  const filterQuery = Object.entries(filter)
    .map(([k,v]) => `|> filter(fn: (r) => r["${k}"] == "${v}") `)
    .join('\n    ')
  const fluxQuery = `from(bucket: "${bucket}")
    |> range(start: -15m)
    ${ filterQuery }
    |> aggregateWindow(every: 5m, fn: last, createEmpty: false)
    |> yield(name: "last")`;

  const { initalLoading, loading, error, result } = useFluxQuery({ fluxQuery: fluxQuery.toString() });
  const value = result.length > 0 ? result[0]._value : null;

  return (
    <div className="p-4 rounded-lg bg-blue-100 dark:bg-blue-900 shadow flex flex-col items-center justify-center h-full">
      <h2 className="text-lg md:text-xl font-semibold mb-2">{title || field}</h2>
      <div className="text-5xl md:text-6xl font-bold">
        {initalLoading && (
          <>...</>
        )}
        {!initalLoading && (
          <div className={
            (loading && !initalLoading ) ? 
              'text-gray-500 dark:text-gray-400' : 
              'text-blue-700 dark:text-blue-200'
            }>
            {value?.toFixed(decimals) }
          </div>
        )}
      </div>
      {error && <p className="text-red-500">Error: {String(error)}</p>}
    </div>
  );
};

export default LatestValue;
