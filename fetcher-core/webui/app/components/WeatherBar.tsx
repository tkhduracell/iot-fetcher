import React, { useEffect, useState } from 'react';
import { DailyForecast } from '../lib/weather';

const ICON_BASE =
  'https://cdn.jsdelivr.net/gh/metno/weathericons/weather/svg';

const Wrapper = (props: { children: React.ReactNode }) => (
  <div className="px-1.5 py-1 rounded-md bg-sky-100 dark:bg-sky-900 shadow-sm ring-1 ring-sky-200 dark:ring-sky-800 flex items-center justify-center">
    {props.children}
  </div>
);

// Swedish short weekday for a YYYY-MM-DD date; anchored to noon local so the
// date can't drift across the timezone boundary.
function weekdayLabel(date: string): string {
  const d = new Date(`${date}T12:00:00`);
  return new Intl.DateTimeFormat('sv-SE', {
    weekday: 'short',
    timeZone: 'Europe/Stockholm',
  }).format(d);
}

const WeatherBar: React.FC = () => {
  const [days, setDays] = useState<DailyForecast[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/weather')
      .then(async res => {
        const body = await res.json();
        if (!res.ok) throw new Error(body?.error || `HTTP ${res.status}`);
        return body.days as DailyForecast[];
      })
      .then(d => {
        if (!cancelled) setDays(d);
      })
      .catch(e => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'okänt fel');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <Wrapper>Fel uppstod vid laddning: {error}</Wrapper>;
  if (!days) return <Wrapper>Hämtar väder…</Wrapper>;
  if (days.length === 0) return <Wrapper>Ingen väderprognos tillgänglig.</Wrapper>;

  return (
    <Wrapper>
      <div className="w-full flex flex-row gap-1">
        {days.map((day, idx) => (
          <div
            key={day.date}
            className="flex-1 flex flex-col items-center justify-between gap-0.5 rounded bg-sky-200/50 dark:bg-sky-800/40 py-1"
          >
            <div className="text-xs font-semibold capitalize text-gray-700 dark:text-gray-200">
              {idx === 0 ? 'Idag' : weekdayLabel(day.date)}
            </div>
            {day.symbolCode ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={`${ICON_BASE}/${day.symbolCode}.svg`}
                alt={day.symbolCode}
                className="w-10 h-10"
                loading="eager"
              />
            ) : (
              <div className="w-10 h-10" />
            )}
            <div className="text-xs text-gray-700 dark:text-gray-200">
              <span className="font-semibold">
                {day.tempMax != null ? `${day.tempMax}°` : '–'}
              </span>
              <span className="mx-0.5 text-gray-400">/</span>
              <span className="text-gray-500 dark:text-gray-400">
                {day.tempMin != null ? `${day.tempMin}°` : '–'}
              </span>
            </div>
          </div>
        ))}
      </div>
    </Wrapper>
  );
};

export default WeatherBar;
