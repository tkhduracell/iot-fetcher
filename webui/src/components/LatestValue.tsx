import React from 'react';
import useLatestValueQuery, { LatestValueQueryParams } from '../hooks/useLatestValueQuery';
import { ConfigValue } from '../types';

type LatestValueProps = ConfigValue & LatestValueQueryParams

const LatestValue: React.FC<LatestValueProps> = (props) => {
  const { initialLoading, loading, error, result } = useLatestValueQuery(props);
  const { decimals = 1, title, field, unit } = props;
  const value: number = result.length > 0 ? result[0]._value : null;

  return (
    <div className="p-4 rounded-lg bg-blue-100 dark:bg-blue-900 shadow flex flex-col items-center justify-center h-full">
      <h2 className="text-lg md:text-xl font-semibold mb-2">{title || field}</h2>
      <div className="text-5xl md:text-6xl font-bold">
        {(initialLoading || error) ? (
          <>...</>
        ) : (
          <div className={
            loading ? 'text-gray-500 dark:text-gray-400' : 'text-blue-700 dark:text-blue-200'
            }>
            {value?.toFixed(decimals).replace(/\.0$/, '')} { unit }
          </div>
        )}
      </div>
      {error && <p className="text-red-500">{String(error)}</p>}
    </div>
  );
};

export default LatestValue;
