import React from 'react';
import useLatestValueQuery, { LatestValueQueryParams } from '../hooks/useLatestValueQuery';
import useHistoryQuery from '../hooks/useHistoryQuery';
import { ConfigValue } from '../lib/types';
import SparklineChart from './SparklineChart';

type LatestValueProps = ConfigValue & LatestValueQueryParams

const LatestValue: React.FC<LatestValueProps> = (props) => {
  const { initialLoading, loading, error, result } = useLatestValueQuery(props);
  const { decimals = 1, title, field, unit, sparkline } = props;
  const value = result.length > 0 ? result[0]._value : null;

  const historyData = useHistoryQuery({
    measurement: props.measurement,
    field: props.field,
    filter: props.filter as Record<string, string>,
    sparkline: sparkline || '',
  });

  return (
    <div className="p-1 rounded bg-blue-100 dark:bg-blue-900 shadow-sm ring-1 ring-blue-100 dark:ring-blue-800 hover:ring-blue-300/60 transition-colors flex flex-col items-center justify-center h-full relative overflow-hidden">
      {sparkline && historyData.length > 0 && (
        <div className="absolute inset-0 z-0">
          <SparklineChart data={historyData} />
        </div>
      )}
      <h2 className="text-sm md:text-base font-medium mb-0 leading-tight text-blue-800 dark:text-blue-100/90 truncate text-center w-full relative z-10">
        {title || field}
      </h2>
      <div className="flex items-baseline gap-0.5 leading-none relative z-10">
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
      {error && <p className="text-red-500 text-[10px] mt-0.5 relative z-10">{String(error)}</p>}
    </div>
  );
};

export default LatestValue;
