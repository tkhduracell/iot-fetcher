import React, { useMemo } from 'react';
import usePromQLQuery from '../hooks/usePromQLQuery';
import { startOfDay, endOfDay, differenceInMinutes } from 'date-fns';

type Point = {
  _time: string;
  value: number;
}

const Wrapper = (props: { children: React.ReactNode }) => {
  return (
    <div className="px-1.5 py-1 rounded-md bg-blue-100 dark:bg-blue-900 shadow-sm ring-1 ring-blue-200 dark:ring-blue-800 flex flex-col gap-1 items-center justify-center">
      {props.children}
    </div>
  );
}

const EnergyPriceBar: React.FC = () => {
  const measurement = 'energy_price';
  const field = '100th_SEK_per_kWh';
  const filterTag = 'SE4';

  const start = startOfDay(new Date());
  const end = endOfDay(new Date());

  const promQuery = useMemo(() => {
    return `avg_over_time(${measurement}_${field}{area="${filterTag}"}[1h])`;
  }, [measurement, field, filterTag]);

  const { initialLoading, error, result } = usePromQLQuery({
    query: promQuery,
    type: 'range',
    start: start.toISOString(),
    end: end.toISOString(),
    step: '1h',
  });

  if (initialLoading && !error) return <Wrapper>Hämtar energipriser...</Wrapper>;
  if (error) return <Wrapper>Fel uppstod vid laddning: {error.message}</Wrapper>;

  const values = result.map(p => p.value).filter(v => v !== undefined && v !== null);
  if (values.length === 0) {
    return <Wrapper>Inga energipriser tillgängliga.</Wrapper>;
  }

  const getBucketColorClass = (value: number | null): string => {
    if (value === undefined || value === null) {
      return 'bg-gray-200 dark:bg-gray-700';
    }
    if (value < 100) {
      return 'bg-green-400 dark:bg-green-700';
    } else if (value <= 150) {
      return 'bg-yellow-400 dark:bg-yellow-700';
    } else {
      return 'bg-red-400 dark:bg-red-700';
    }
  };

  const startStr = start.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  const endStr = end.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });

  const dateStr = start.toLocaleDateString([], { weekday:'long', month: 'long', day: 'numeric' });

      return (
        <Wrapper>
          <div className="w-full flex flex-row justify-between text-sm md:text-base text-gray-600 dark:text-gray-300">
            <div>{startStr}</div>
            <div>{dateStr}</div>
            <div>{endStr}</div>
          </div>
          <div className="w-full flex gap-1 flex-wrap">
            {result.map((point: Point) => {
              const value = point.value;
              const colorClass = getBucketColorClass(value);
              const time = new Date(point._time);
              const diff = differenceInMinutes(time, Date.now());
              const isNow = ( diff < 59 &&  diff > 0 );
              return (
                <div className='flex flex-1 flex-col justify-end' key={point._time}>
                  <div className={
                    `${colorClass} rounded p-0 text-[10px] flex
                    text-center text-gray-600 justify-center items-center
                    dark:text-gray-300 ${isNow ? 'h-5' : 'h-1.5'}`}>
                      { isNow && `${value?.toFixed(0)}` }
                  </div>
                </div>
              );
            })}
          </div>

          <div className="w-full flex flex-row justify-between text-[10px] text-gray-600 dark:text-gray-300">
            <div className='bg-green-400 dark:bg-green-700 rounded px-1 py-0.5'>
              &lt; 100 öre
            </div>
            <div className='bg-yellow-400 dark:bg-yellow-700 rounded px-1 py-0.5'>
              100–150 öre
            </div>
            <div className='bg-red-400 dark:bg-red-700 rounded px-1 py-0.5'>
              &gt; 150 öre
            </div>
          </div>
        </Wrapper>
      );
};

export default EnergyPriceBar;
