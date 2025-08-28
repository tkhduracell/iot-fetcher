import React, { useMemo } from 'react';
import useFluxQuery from '../hooks/useFluxQuery';
import { startOfDay, endOfDay, differenceInMinutes } from 'date-fns';

type Point = {
  _measurement: string;
  _field: string;
  _time: string;
  _value: number | null;
}

const Wrapper = (props: { children: React.ReactNode }) => {
  return (
    <div className="px-1.5 py-1 rounded-md bg-blue-100 dark:bg-blue-900 shadow-sm ring-1 ring-blue-200 dark:ring-blue-800 flex flex-col gap-1 items-center justify-center min-h-20">
      {props.children}
    </div>
  );
}

const EnergyPriceBar: React.FC = () => {
  const bucket = 'irisgatan';
  const measurement = 'energy_price';
  const field = '100th_SEK_per_kWh';
  const filter = { area: 'SE4' };

  const start = startOfDay(new Date());
  const end = endOfDay(new Date());

  const fluxQuery = useMemo(() => {
    const filterQuery = Object.entries(filter)
      .map(([k, v]) => `|> filter(fn: (r) => r["${k}"] == "${v}")`)
      .join('\n    ');

    return `from(bucket: "${bucket}")
      |> range(start: ${start.toISOString()}, stop: ${end.toISOString()})
      |> filter(fn: (r) => r["_measurement"] == "${measurement}" and r["_field"] == "${field}")
      ${filterQuery}
      |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
      |> yield(name: "mean")
    `;
  }, [bucket, measurement, field, filter, start, end]);

  const { initialLoading, error, result } = useFluxQuery({ fluxQuery });

  if (initialLoading && !error) return <Wrapper>Hämtar energipriser...</Wrapper>;
  if (error) return <Wrapper>Fel uppstod vid laddning: {error.message}</Wrapper>;

  const values = result.map(p => p._value).filter(v => v !== undefined && v !== null) as number[];
  if (values.length === 0) {
    return <Wrapper>Inga energipriser tillgängliga.</Wrapper>;
  }

  const minValue = Math.min(...values, 0);
  const maxValue = Math.max(...values, 30);
  const range = maxValue - minValue;
  const bucketSize = range / 3;

  const getBucketColorClass = (value: number | null): string => {
    if (value === undefined || value === null) {
      return 'bg-gray-200 dark:bg-gray-700';
    }
    if (value <= minValue + bucketSize) {
      return 'bg-green-400 dark:bg-green-700';
    } else if (value <= minValue + 2 * bucketSize) {
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
              const colorClass = getBucketColorClass(point._value);
              const time = new Date(point._time);
              const diff = differenceInMinutes(time, Date.now());
              const isNow = ( diff < 59 &&  diff > 0 );
              return (
                <div className='flex flex-1 flex-col justify-end' key={[point._measurement, point._field, point._time].join('|')}>
                  <div className={
                    `${colorClass} rounded p-0 text-[10px] flex
                    text-center text-gray-600 justify-center items-center
                    dark:text-gray-300 ${isNow ? 'h-5' : 'h-1.5'}`}>
                      { isNow && `${point._value?.toFixed(0)}` }
                  </div>
                </div>
              );
            })}
          </div>

          <div className="w-full flex flex-row justify-between text-[10px] text-gray-600 dark:text-gray-300">
            <div className='bg-green-400 dark:bg-green-700 rounded px-1 py-0.5'>
              Min {minValue.toFixed(0)} öre
            </div>
            <div className='bg-yellow-400 dark:bg-yellow-700 rounded px-1 py-0.5'>
              &lt; {(minValue + 1 * bucketSize).toFixed(0)} öre 
              &lt; {(minValue + 2 * bucketSize).toFixed(0)} öre &lt; 
            </div>
            <div className='bg-red-400 dark:bg-red-700 rounded px-1 py-0.5'>
              Max {(maxValue).toFixed(0)} öre
            </div>
          </div>
        </Wrapper>
      );
};

export default EnergyPriceBar;