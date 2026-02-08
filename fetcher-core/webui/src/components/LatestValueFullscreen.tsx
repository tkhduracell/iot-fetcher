import React, { useMemo } from 'react';

import { ConfigValue } from '../types';
import useInfluxQLQuery from '../hooks/useInfluxQLQuery';
import { endOfYesterday, startOfYesterday } from 'date-fns';

interface LatestValueFullscreenProps extends ConfigValue {
  open: boolean;
  onClose: () => void;
  bucket?: string;
}

const LatestValueFullscreen: React.FC<LatestValueFullscreenProps> = ({
  open, onClose,
  bucket = "irisgatan",
  filter = {},
  measurement,
  title,
  field,
  window = "60m",
}) => {
  if (!open) return null;

  const influxQLQuery = useMemo(() => {
    const start = startOfYesterday();
    const end = endOfYesterday();

    // Build WHERE clause
    const whereClauses = [
      `time >= '${start.toISOString()}'`,
      `time < '${end.toISOString()}'`
    ];

    Object.entries(filter).forEach(([key, value]) => {
      whereClauses.push(`"${key}" = '${value}'`);
    });

    const whereClause = whereClauses.join(' AND ');

    return `SELECT LAST("${field}") AS value
            FROM "${measurement}"
            WHERE ${whereClause}
            GROUP BY time(${window})
            ORDER BY time DESC
            LIMIT 10`;
  }, [measurement, field, filter, window]);

  const { initialLoading, error, result } = useInfluxQLQuery({
    query: influxQLQuery,
    database: bucket
  });

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
            { result.map((item, idx) => <div key={idx}>{item.value}</div>) }
          </div>
        </div>
      </div>
    </div>
  );
};

export default LatestValueFullscreen;
