import { describe, it, expect } from 'vitest';
import { aggregateDailyForecast, MetTimeseriesEntry } from './weather';

// Helper: build a minimal timeseries entry.
function entry(
  time: string,
  temp: number,
  next6?: string,
  next1?: string,
): MetTimeseriesEntry {
  return {
    time,
    data: {
      instant: { details: { air_temperature: temp } },
      ...(next6 ? { next_6_hours: { summary: { symbol_code: next6 } } } : {}),
      ...(next1 ? { next_1_hours: { summary: { symbol_code: next1 } } } : {}),
    },
  };
}

describe('aggregateDailyForecast', () => {
  it('groups timesteps by Europe/Stockholm calendar day (summer +02:00)', () => {
    // 2026-07-06T22:30Z is 2026-07-07 00:30 local, so it belongs to the 7th.
    const ts = [
      entry('2026-07-06T10:00:00Z', 20, 'clearsky_day'),
      entry('2026-07-06T22:30:00Z', 15, 'clearsky_night'),
    ];
    const days = aggregateDailyForecast(ts);
    expect(days.map(d => d.date)).toEqual(['2026-07-06', '2026-07-07']);
  });

  it('computes rounded daily max/min from instant air_temperature', () => {
    const ts = [
      entry('2026-07-06T06:00:00Z', 12.4, 'clearsky_day'),
      entry('2026-07-06T10:00:00Z', 21.6, 'clearsky_day'),
      entry('2026-07-06T16:00:00Z', 18.2, 'cloudy'),
    ];
    const [day] = aggregateDailyForecast(ts);
    expect(day.tempMax).toBe(22);
    expect(day.tempMin).toBe(12);
  });

  it('picks the symbol of the timestep nearest 12:00 local, preferring next_6_hours', () => {
    // Local noon in summer = 10:00Z. Middle entry wins.
    const ts = [
      entry('2026-07-06T06:00:00Z', 12, 'clearsky_day'),   // 08 local
      entry('2026-07-06T10:00:00Z', 20, 'rain', 'fog'),    // 12 local -> next_6 'rain'
      entry('2026-07-06T16:00:00Z', 18, 'cloudy'),         // 18 local
    ];
    const [day] = aggregateDailyForecast(ts);
    expect(day.symbolCode).toBe('rain');
  });

  it('falls back to next_1_hours when next_6_hours is absent on the midday step', () => {
    const ts = [
      entry('2026-07-06T10:00:00Z', 20, undefined, 'lightrain'), // 12 local, only next_1
    ];
    const [day] = aggregateDailyForecast(ts);
    expect(day.symbolCode).toBe('lightrain');
  });

  it('returns at most `days` days', () => {
    const ts = Array.from({ length: 10 }, (_, i) =>
      entry(`2026-07-${String(6 + i).padStart(2, '0')}T10:00:00Z`, 20, 'clearsky_day'),
    );
    expect(aggregateDailyForecast(ts, { days: 7 })).toHaveLength(7);
  });

  it('returns null temps when a day has no air_temperature', () => {
    const ts: MetTimeseriesEntry[] = [
      { time: '2026-07-06T10:00:00Z', data: { instant: { details: {} }, next_6_hours: { summary: { symbol_code: 'cloudy' } } } },
    ];
    const [day] = aggregateDailyForecast(ts);
    expect(day.tempMax).toBeNull();
    expect(day.tempMin).toBeNull();
    expect(day.symbolCode).toBe('cloudy');
  });
});
