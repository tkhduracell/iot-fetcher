import React from 'react';
import useLatestValueQuery, { LatestValueQueryParams } from '../hooks/useLatestValueQuery';
import { ConfigValue } from '../lib/types';

type LatestValueProps = ConfigValue & LatestValueQueryParams

const LatestValue: React.FC<LatestValueProps> = (props) => {
  const { initialLoading, loading, error, result } = useLatestValueQuery(props);
  const { decimals = 1, title, field, unit } = props;
  const value = result.length > 0 ? result[0]._value : null;

  return (
    <div className="p-1 rounded bg-blue-100 dark:bg-blue-900 shadow-sm ring-1 ring-blue-100 dark:ring-blue-800 hover:ring-blue-300/60 transition-colors flex flex-col items-center justify-center h-full">
      <h2 className="text-sm md:text-base font-medium mb-0 leading-tight text-blue-800 dark:text-blue-100/90 truncate text-center w-full">
        {title || field}
      </h2>
      <div className="flex items-baseline gap-0.5 leading-none">
        {(initialLoading || error) ? (
          <span className="text-4xl">...</span>
        ) : (
          <>
            <span className={
              `font-semibold ${loading ? 'text-gray-500 dark:text-gray-400' : 'text-blue-700 dark:text-blue-200'} text-5xl md:text-6xl tracking-[-0.01em]`
            }>
              {value?.toFixed(decimals).replace(/\.0$/, '')}
            </span>
            <span className="text-sm md:text-base text-blue-700/80 dark:text-blue-200/80">
              {unit}
            </span>
          </>
        )}
      </div>
      {error && <p className="text-red-500 text-[10px] mt-0.5">{String(error)}</p>}
    </div>
  );
};

export default LatestValue;
