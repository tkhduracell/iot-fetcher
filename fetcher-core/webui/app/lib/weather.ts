export interface MetTimeseriesEntry {
  time: string;
  data: {
    instant: { details: { air_temperature?: number } };
    next_1_hours?: { summary: { symbol_code: string } };
    next_6_hours?: { summary: { symbol_code: string } };
    next_12_hours?: { summary: { symbol_code: string } };
  };
}

export interface DailyForecast {
  date: string; // YYYY-MM-DD in the configured timezone
  symbolCode: string; // '' when no symbol is available for the day
  tempMax: number | null;
  tempMin: number | null;
}

const DEFAULT_TZ = 'Europe/Stockholm';

// 'YYYY-MM-DD' for the given ISO instant in `tz`. en-CA gives ISO-ordered parts.
function localDay(iso: string, tz: string): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: tz,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(iso));
}

// Local hour-of-day (0-23) for the given ISO instant in `tz`.
function localHour(iso: string, tz: string): number {
  const h = new Intl.DateTimeFormat('en-GB', {
    timeZone: tz,
    hour: '2-digit',
    hour12: false,
  }).format(new Date(iso));
  // en-GB can yield '24' for midnight in some engines; normalise to 0.
  return parseInt(h, 10) % 24;
}

function symbolOf(e: MetTimeseriesEntry): string | undefined {
  return (
    e.data.next_6_hours?.summary.symbol_code ??
    e.data.next_1_hours?.summary.symbol_code ??
    e.data.next_12_hours?.summary.symbol_code
  );
}

export function aggregateDailyForecast(
  timeseries: MetTimeseriesEntry[],
  opts: { timeZone?: string; days?: number } = {},
): DailyForecast[] {
  const tz = opts.timeZone ?? DEFAULT_TZ;
  const maxDays = opts.days ?? 7;

  // Group entries by local day, preserving first-seen day order.
  const byDay = new Map<string, MetTimeseriesEntry[]>();
  for (const e of timeseries) {
    const day = localDay(e.time, tz);
    const bucket = byDay.get(day);
    if (bucket) bucket.push(e);
    else byDay.set(day, [e]);
  }

  const days: DailyForecast[] = [];
  for (const [date, entries] of byDay) {
    if (days.length >= maxDays) break;

    const temps = entries
      .map(e => e.data.instant.details.air_temperature)
      .filter((t): t is number => typeof t === 'number');
    const tempMax = temps.length ? Math.round(Math.max(...temps)) : null;
    const tempMin = temps.length ? Math.round(Math.min(...temps)) : null;

    // Symbol from the entry nearest 12:00 local that actually has a symbol.
    const ranked = [...entries].sort(
      (a, b) =>
        Math.abs(localHour(a.time, tz) - 12) - Math.abs(localHour(b.time, tz) - 12),
    );
    let symbolCode = '';
    for (const e of ranked) {
      const s = symbolOf(e);
      if (s) {
        symbolCode = s;
        break;
      }
    }

    days.push({ date, symbolCode, tempMax, tempMin });
  }

  return days;
}
