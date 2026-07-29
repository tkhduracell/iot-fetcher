# 7-Day Weather Row Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a row of 7 daily weather boxes (icon + hi/lo + weekday) above the energy price bar on the iPad dashboard, sourced from YR / MET Norway.

**Architecture:** A pure aggregation function turns the MET Locationforecast timeseries into 7 daily summaries. A cached Next.js API route (`/api/weather`) fetches MET server-side and calls that function. A client `WeatherBar` component renders the boxes and is wired into the dashboard page above `<EnergyPriceBar />`.

**Tech Stack:** Next.js 15 (App Router), React 18, TypeScript, Tailwind. New devDep: `vitest` (for the pure aggregation unit only). No runtime deps added. Weather icons fetched at runtime from jsDelivr CDN.

## Global Constraints

- All work is under `fetcher-core/webui/`. Run commands from that directory.
- Location is env-configurable, read server-side only: `WEATHER_LAT` (default `55.571`), `WEATHER_LON` (default `12.997`), `WEATHER_ALTITUDE` (default `19`). Defaults = Kulladal, Malmö.
- MET API requires an identifying `User-Agent`: `iot-fetcher-weather/1.0 github.com/tkhduracell/iot-fetcher`.
- Forecast API: `https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat}&lon={lon}&altitude={alt}`.
- Icon CDN: `https://cdn.jsdelivr.net/gh/metno/weathericons/weather/svg/{symbolCode}.svg`.
- Timezone for day-grouping and weekday labels: `Europe/Stockholm`. Use native `Intl` — do NOT add a date-lib dependency.
- UI copy is Swedish, matching `EnergyPriceBar.tsx`.
- Follow existing conventions: `process.env.X || 'default'` in routes (see `app/api/sonos/[...path]/route.ts`), Tailwind wrapper idiom from `EnergyPriceBar.tsx`.

---

## File Structure

- Create `app/lib/weather.ts` — types + `aggregateDailyForecast()` pure function.
- Create `app/lib/weather.test.ts` — vitest unit tests for the aggregation.
- Create `app/api/weather/route.ts` — cached GET route: env → MET fetch → aggregate → JSON.
- Create `app/components/WeatherBar.tsx` — client component rendering the 7 boxes.
- Modify `app/page.tsx` — insert `<WeatherBar />` above `<EnergyPriceBar />`.
- Modify `package.json` — add `vitest` devDep + `test` script.

---

## Task 1: Aggregation function + tests

**Files:**
- Create: `fetcher-core/webui/app/lib/weather.ts`
- Test: `fetcher-core/webui/app/lib/weather.test.ts`
- Modify: `fetcher-core/webui/package.json` (add `vitest` devDep + `test` script)

**Interfaces:**
- Consumes: nothing (pure, stdlib only).
- Produces:
  - `interface MetTimeseriesEntry { time: string; data: { instant: { details: { air_temperature?: number } }; next_1_hours?: { summary: { symbol_code: string } }; next_6_hours?: { summary: { symbol_code: string } }; next_12_hours?: { summary: { symbol_code: string } } } }`
  - `interface DailyForecast { date: string; symbolCode: string; tempMax: number | null; tempMin: number | null }`
  - `function aggregateDailyForecast(timeseries: MetTimeseriesEntry[], opts?: { timeZone?: string; days?: number }): DailyForecast[]`

- [ ] **Step 1: Add vitest devDep and test script**

Run:
```bash
cd fetcher-core/webui
npm install --save-dev vitest@^2
```
Then edit `package.json` `scripts` to add (keep existing entries):
```json
    "test": "vitest run"
```

- [ ] **Step 2: Write the failing test**

Create `app/lib/weather.test.ts`:
```ts
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
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — `Cannot find module './weather'` / `aggregateDailyForecast is not a function`.

- [ ] **Step 4: Write the implementation**

Create `app/lib/weather.ts`:
```ts
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
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npm test`
Expected: PASS — 6 passing tests.

- [ ] **Step 6: Typecheck**

Run: `npm run typecheck`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add fetcher-core/webui/app/lib/weather.ts fetcher-core/webui/app/lib/weather.test.ts fetcher-core/webui/package.json fetcher-core/webui/package-lock.json
git commit -m "feat(webui): add MET forecast daily-aggregation helper"
```

---

## Task 2: `/api/weather` route

**Files:**
- Create: `fetcher-core/webui/app/api/weather/route.ts`

**Interfaces:**
- Consumes: `aggregateDailyForecast`, `MetTimeseriesEntry`, `DailyForecast` from `../../lib/weather`.
- Produces: `GET` returns `{ days: DailyForecast[] }` on success, or `{ error: string }` with a non-200 status on failure.

- [ ] **Step 1: Write the route**

Create `app/api/weather/route.ts`:
```ts
import { NextResponse } from 'next/server';
import { aggregateDailyForecast, MetTimeseriesEntry } from '../../lib/weather';

const USER_AGENT = 'iot-fetcher-weather/1.0 github.com/tkhduracell/iot-fetcher';

export async function GET() {
  const lat = process.env.WEATHER_LAT || '55.571';
  const lon = process.env.WEATHER_LON || '12.997';
  const altitude = process.env.WEATHER_ALTITUDE || '19';

  const params = new URLSearchParams({ lat, lon });
  if (altitude) params.set('altitude', altitude);
  const url = `https://api.met.no/weatherapi/locationforecast/2.0/compact?${params.toString()}`;

  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': USER_AGENT },
      // Cache server-side ~30 min: respects MET TOS and survives the hourly
      // full-page reload without re-hitting MET.
      next: { revalidate: 1800 },
    });

    if (!res.ok) {
      return NextResponse.json(
        { error: `MET request failed: ${res.status}` },
        { status: 502 },
      );
    }

    const body = await res.json();
    const timeseries: MetTimeseriesEntry[] = body?.properties?.timeseries ?? [];
    const days = aggregateDailyForecast(timeseries, { days: 7 });
    return NextResponse.json({ days });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'unknown error';
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
```

- [ ] **Step 2: Typecheck**

Run: `npm run typecheck`
Expected: no errors.

- [ ] **Step 3: Manually verify the route against the dev server**

Run (in one terminal): `npm run dev`
Then (in another):
```bash
curl -s http://localhost:3000/api/weather | python3 -m json.tool | head -40
```
Expected: JSON `{ "days": [ { "date": "YYYY-MM-DD", "symbolCode": "...", "tempMax": N, "tempMin": N }, ... ] }` with 7 entries, dates starting today, plausible temps, non-empty symbolCodes.

Also verify env override:
```bash
WEATHER_LAT=57.7089 WEATHER_LON=11.9746 WEATHER_ALTITUDE=12 npm run dev
# then curl again — dates/temps should reflect Göteborg
```
Stop the dev server when done (Ctrl-C).

- [ ] **Step 4: Commit**

```bash
git add fetcher-core/webui/app/api/weather/route.ts
git commit -m "feat(webui): add cached /api/weather MET forecast route"
```

---

## Task 3: `WeatherBar` component

**Files:**
- Create: `fetcher-core/webui/app/components/WeatherBar.tsx`

**Interfaces:**
- Consumes: `DailyForecast` type from `../lib/weather`; the `/api/weather` route from Task 2.
- Produces: default-exported React component `WeatherBar` (no props).

- [ ] **Step 1: Write the component**

Create `app/components/WeatherBar.tsx`:
```tsx
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
```

- [ ] **Step 2: Typecheck**

Run: `npm run typecheck`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add fetcher-core/webui/app/components/WeatherBar.tsx
git commit -m "feat(webui): add WeatherBar 7-day forecast component"
```

---

## Task 4: Wire into the dashboard

**Files:**
- Modify: `fetcher-core/webui/app/page.tsx`

**Interfaces:**
- Consumes: `WeatherBar` default export from `./components/WeatherBar`.
- Produces: nothing (final wiring).

- [ ] **Step 1: Import WeatherBar**

In `app/page.tsx`, add alongside the other component imports (after the `EnergyPriceBar` import on line 11):
```tsx
import WeatherBar from './components/WeatherBar';
```

- [ ] **Step 2: Render it above the energy bar**

In the JSX, change:
```tsx
        <Grid values={values} onOpen={openFullscreen} />
        <EnergyPriceBar />
```
to:
```tsx
        <Grid values={values} onOpen={openFullscreen} />
        <WeatherBar />
        <EnergyPriceBar />
```

- [ ] **Step 3: Typecheck and build**

Run:
```bash
npm run typecheck
npm run build
```
Expected: typecheck clean; build succeeds (an ESLint warning is acceptable, an error is not).

- [ ] **Step 4: Manual browser verification**

Run: `npm run dev`, open `http://localhost:3000`.
Expected: a row of 7 boxes sits directly above the energy price bar. Each box shows a Swedish weekday (first = "Idag"), a MET weather icon, and `max° / min°`. Icons load from the CDN. Resize toward iPad width — boxes stay evenly sized and readable.

- [ ] **Step 5: Commit**

```bash
git add fetcher-core/webui/app/page.tsx
git commit -m "feat(webui): show 7-day weather row above energy price bar"
```

---

## Post-merge E2E (rpi5)

- Optionally set `WEATHER_LAT` / `WEATHER_LON` / `WEATHER_ALTITUDE` in `fetcher-core/webui/.env` (defaults already = Kulladal). Build the image (`make build`, push to registry — not on rpi5) and redeploy with both compose files. Confirm the iPad dashboard shows the weather row and that it survives the hourly auto-reload.
```
