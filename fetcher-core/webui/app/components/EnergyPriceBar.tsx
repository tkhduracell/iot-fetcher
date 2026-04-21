import React, { useEffect, useMemo, useState } from 'react';
import usePromQLQuery from '../hooks/usePromQLQuery';
import { startOfDay, addDays, addHours } from 'date-fns';

type Point = {
  _time: string;
  value: number;
}

const HOUR_MS = 3_600_000;

const Wrapper = (props: { children: React.ReactNode }) => {
  return (
    <div className="px-1.5 py-1 rounded-md bg-blue-100 dark:bg-blue-900 shadow-sm ring-1 ring-blue-200 dark:ring-blue-800 flex flex-col gap-1 items-center justify-center">
      {props.children}
    </div>
  );
}

const getBucketColorClass = (value: number | null | undefined): string => {
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

const EnergyPriceBar: React.FC = () => {
  const measurement = 'energy_price';
  const field = '100th_SEK_per_kWh';
  const filterTag = 'SE4';

  const now = new Date();
  const todayStart = startOfDay(now);
  const tomorrowStart = startOfDay(addDays(now, 1));
  const dayAfterStart = startOfDay(addDays(now, 2));

  const [view, setView] = useState<'today' | 'tomorrow'>('today');

  const promQuery = useMemo(() => {
    return `avg_over_time(${measurement}_${field}{area="${filterTag}"}[1h])`;
  }, [measurement, field, filterTag]);

  // Each sample at time T aggregates `[T-1h, T]`, so a sample at dayStart+1h
  // covers hour 0 of that day and a sample at nextDayStart covers hour 23.
  // Starting the range at dayStart+1h (not dayStart) keeps the 1h window
  // strictly inside the day — so a tomorrow query returns no rows until
  // tomorrow's prices are actually published instead of picking up today's
  // last hour as a phantom midnight sample.
  const todayQuery = usePromQLQuery({
    query: promQuery,
    type: 'range',
    start: addHours(todayStart, 1).toISOString(),
    end: tomorrowStart.toISOString(),
    step: '1h',
  });
  const tomorrowQuery = usePromQLQuery({
    query: promQuery,
    type: 'range',
    start: addHours(tomorrowStart, 1).toISOString(),
    end: dayAfterStart.toISOString(),
    step: '1h',
  });

  const todayPoints = useMemo(
    () => todayQuery.result.filter(p => p.value != null && !Number.isNaN(p.value)),
    [todayQuery.result],
  );
  const tomorrowPoints = useMemo(
    () => tomorrowQuery.result.filter(p => p.value != null && !Number.isNaN(p.value)),
    [tomorrowQuery.result],
  );

  const hasTomorrow = tomorrowPoints.length > 0;

  useEffect(() => {
    if (view === 'tomorrow' && !hasTomorrow) {
      setView('today');
    }
  }, [view, hasTomorrow]);

  const initialLoading = todayQuery.initialLoading || tomorrowQuery.initialLoading;
  const error = todayQuery.error || tomorrowQuery.error;

  if (initialLoading && !error) return <Wrapper>Hämtar energipriser...</Wrapper>;
  if (error) return <Wrapper>Fel uppstod vid laddning: {error.message}</Wrapper>;

  if (todayPoints.length === 0 && tomorrowPoints.length === 0) {
    return <Wrapper>Inga energipriser tillgängliga.</Wrapper>;
  }

  const currentHour = now.getHours();

  const renderCells = (points: Point[], dayStart: Date, highlightNow: boolean) => {
    const byHour: (Point | undefined)[] = new Array(24).fill(undefined);
    const dayStartMs = dayStart.getTime();
    for (const p of points) {
      // Sample at T aggregates hour [T-1h, T), so its hour-of-day index is
      // (T - dayStart)/1h - 1.
      const hour = Math.round((new Date(p._time).getTime() - dayStartMs) / HOUR_MS) - 1;
      if (hour >= 0 && hour < 24) byHour[hour] = p;
    }

    return (
      <div className="w-1/2 flex gap-1">
        {byHour.map((point, hour) => {
          const value = point?.value;
          const colorClass = getBucketColorClass(value ?? null);
          const isNow = highlightNow && hour === currentHour && point !== undefined;
          return (
            <div className='flex flex-1 flex-col justify-end' key={hour}>
              <div className={
                `${colorClass} rounded p-0 flex h-8
                text-center text-gray-600 justify-center items-center
                dark:text-gray-300 ${isNow ? 'text-sm font-semibold ring-inset ring-2 ring-gray-500' : 'text-[10px]'}`}>
                  {value != null ? value.toFixed(0) : ''}
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <Wrapper>
      <div className="w-full flex gap-1 items-stretch">
        <div className="relative overflow-hidden flex-1">
          <div
            className="flex w-[200%] transition-transform duration-300 ease-out"
            style={{ transform: view === 'today' ? 'translateX(0)' : 'translateX(-50%)' }}
          >
            {renderCells(todayPoints, todayStart, true)}
            {renderCells(tomorrowPoints, tomorrowStart, false)}
          </div>
        </div>
        {hasTomorrow && (
          <button
            type="button"
            onClick={() => setView(view === 'today' ? 'tomorrow' : 'today')}
            aria-label={view === 'today' ? 'Visa morgondagens priser' : 'Tillbaka till dagens priser'}
            className="h-8 px-2 flex items-center justify-center rounded bg-blue-200 dark:bg-blue-800 hover:bg-blue-300 dark:hover:bg-blue-700 text-gray-700 dark:text-gray-200 text-sm font-semibold shadow-sm shrink-0 cursor-pointer transition-colors duration-200"
          >
            {view === 'today' ? '»' : '«'}
          </button>
        )}
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
