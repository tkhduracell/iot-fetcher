import React from 'react';
import useLatestValueQuery, { LatestValueQueryParams } from '../hooks/useLatestValueQuery';
import useHistoryQuery from '../hooks/useHistoryQuery';
import { ConfigValue } from '../lib/types';
import SparklineChart from './SparklineChart';

function valueSizeClass(colCount: number) {
  if (colCount <= 1) return 'text-7xl md:text-8xl';
  if (colCount === 2) return 'text-6xl md:text-7xl';
  if (colCount === 3) return 'text-5xl md:text-6xl';
  return 'text-5xl md:text-6xl';
}

function titleSizeClass(colCount: number) {
  if (colCount <= 1) return 'text-base md:text-lg';
  if (colCount <= 3) return 'text-sm md:text-base';
  return 'text-sm md:text-base';
}

type LatestValueProps = ConfigValue & LatestValueQueryParams & { colCount?: number }

const LatestValue: React.FC<LatestValueProps> = (props) => {
  const { initialLoading, loading, error, result } = useLatestValueQuery(props);
  const { decimals = 1, title, field, unit, sparkline, sparklineMin, sparklineMax, colCount = 3 } = props;
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
        <div className="absolute inset-0 z-0 pointer-events-none" aria-hidden="true">
          <SparklineChart data={historyData} fixedMin={sparklineMin} fixedMax={sparklineMax} />
        </div>
      )}
      <h2 className={`${titleSizeClass(colCount)} font-medium mb-0 leading-tight text-blue-800 dark:text-blue-100/90 truncate text-center w-full relative z-10`}>
        {title || field}
      </h2>
      <div className="flex items-baseline gap-0.5 leading-none relative z-10">
        {(initialLoading || error) ? (
          <span className="text-4xl">...</span>
        ) : (
          <>
            <span className={
              `font-semibold ${loading ? 'text-gray-500 dark:text-gray-400' : 'text-blue-700 dark:text-blue-200'} ${valueSizeClass(colCount)} tracking-[-0.01em]`
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
