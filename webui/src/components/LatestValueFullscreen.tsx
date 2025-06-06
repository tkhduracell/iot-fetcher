import React from 'react';

import { ConfigValue } from '../types';
import useFluxQuery from '../hooks/useFluxQuery';
import { endOfYesterday, startOfYesterday } from 'date-fns';

interface LatestValueFullscreenProps extends ConfigValue {
  open: boolean;
  onClose: () => void;
  bucket?: string;
}

const LatestValueFullscreen: React.FC<LatestValueFullscreenProps> = ({ 
  open, onClose, 
  bucket = "irisgatan",
  filter,
  measurement,
  title,
  field,
  window = "60m",
}) => {
  if (!open) return null;

  filter = {
      '_measurement': measurement,
      '_field': field,
      ...filter
  }

  const filterQuery = Object.entries(filter)
    .map(([k,v]) => `|> filter(fn: (r) => r["${k}"] == "${v}") `)
    .join('\n    ')
  
  const start = startOfYesterday();
  const end = endOfYesterday();
  const fluxQuery = `from(bucket: "${bucket}")
    |> range(start: ${start.toISOString()}, stop: ${end.toISOString()})
    ${ filterQuery }
    |> aggregateWindow(every: ${window}, fn: last, createEmpty: false)
    |> yield(name: "last")`;

  const { initialLoading, error, result } = useFluxQuery({ fluxQuery: fluxQuery.toString() });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-80">
      <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-8 w-full h-full relative flex flex-col items-center">
        <button
          className="absolute top-4 right-4 text-6xl text-gray-700 dark:text-gray-200 hover:text-red-500"
          onClick={onClose}
          aria-label="Close"
        >
          &times;
        </button>

        { initialLoading && !error && <div className="text-2xl">Loading...</div> }
        { error && <div className="text-2xl">Error: {error.message}</div> }

        <div className="flex flex-col gap-4 items-center h-full align-items-center text-gray-600 dark:text-gray-300">
          <div className="text-6xl">{ title }</div>
          <div className="text-2xl font-bold">
            { measurement }
            .
            { field }
            </div>
          <div className="text-6xl">
            { result.map((item) => <div>{item._value}</div>) }
          </div>
        </div>
      </div>
    </div>
  );
};

export default LatestValueFullscreen;
