# 7-Day Weather Row — Design

**Date:** 2026-07-06
**Component:** `fetcher-core/webui` (Next.js dashboard shown on iPad kiosk)

## Goal

Add a row of 7 boxes (one per day) showing the weather forecast, placed
directly **above** the `EnergyPriceBar`. Each box shows a pretty weather icon,
the daily high/low temperature, and a weekday label. Data comes from **YR / MET
Norway** (easiest free API — no key, JSON, returns a `symbol_code` per timestep
that maps 1:1 to an open-licensed icon set).

## Data source

- **Forecast API:** MET Norway *Locationforecast 2.0 compact*
  `https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat}&lon={lon}&altitude={alt}`
  - Requires an identifying `User-Agent` header (MET TOS). We send
    `iot-fetcher-weather/1.0 github.com/tkhduracell/iot-fetcher`.
  - Returns `properties.timeseries[]`, each with `time`, `data.instant.details.air_temperature`,
    and forecast-window summaries (`next_1_hours` / `next_6_hours` / `next_12_hours`)
    each carrying a `summary.symbol_code` (e.g. `partlycloudy_day`).
  - Verified: returns ~85 timesteps (hourly for ~2 days, then 6-hourly), covering
    well beyond 7 days.
- **Icons:** official MET icon set via jsDelivr CDN (chosen: fetch at runtime):
  `https://cdn.jsdelivr.net/gh/metno/weathericons/weather/svg/{symbol_code}.svg`
  - Verified 200 `image/svg+xml` for all sampled codes (clearsky_day/night,
    cloudy, rain, heavyrainandthunder, snow, sleet, fog, partlycloudy_night,
    lightrainshowers_day, …). The `{symbol_code}` from the API is used verbatim.

## Configuration (env)

Read server-side in the API route; consumed by the `iot-fetcher` container via
`fetcher-core/webui/.env` (existing `process.env.X || 'default'` convention).

| Var                | Default   | Meaning                          |
|--------------------|-----------|----------------------------------|
| `WEATHER_LAT`      | `55.571`  | Latitude (default: Kulladal, Malmö) |
| `WEATHER_LON`      | `12.997`  | Longitude                        |
| `WEATHER_ALTITUDE` | `19`      | Altitude in metres (MET accuracy); omit from URL if unset |

Defaults resolve to **Kulladal, Malmö** (YR location `2-2698885`), matching the
requested location.

## Component 1 — API route `app/api/weather/route.ts`

`GET` handler:

1. Read `WEATHER_LAT` / `WEATHER_LON` / `WEATHER_ALTITUDE` (with defaults).
2. `fetch` the Locationforecast compact URL with the `User-Agent` header and
   `next: { revalidate: 1800 }` (server cache ~30 min — respects MET TOS and
   means the hourly full-page reload hits cache, not MET).
3. On non-2xx, return `{ error }` with the upstream status.
4. **Aggregate** `timeseries[]` into up to 7 daily objects:
   - Group each timestep by its **Europe/Stockholm calendar day** (derive the
     local `YYYY-MM-DD` via `Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Stockholm' })`
     — no new dependency).
   - Take the first 7 distinct days starting from today (local).
   - `tempMax` / `tempMin` = max / min of `instant.details.air_temperature`
     across that day's timesteps (rounded to whole °C for display).
   - `symbolCode` = the symbol of the timestep **nearest 12:00 local** that day,
     preferring `next_6_hours` → `next_1_hours` → `next_12_hours`. (Midday gives
     the representative daytime icon.) If a day has no timestep with a symbol,
     fall back to the nearest available symbol that day.
   - The route returns only the ISO `date` (`YYYY-MM-DD`); the weekday label is
     formatted client-side in component 2.
5. Respond `{ days: DailyForecast[] }` where
   `DailyForecast = { date: string; symbolCode: string; tempMax: number; tempMin: number }`.

Edge cases: fewer than 7 usable days → return what exists. Missing temps for a
day → still return the day with whatever symbol is available and `null` temps;
component renders "–".

## Component 2 — `app/components/WeatherBar.tsx`

Client component, styled to match `EnergyPriceBar` (shared visual language):

- `Wrapper`: `rounded-md`, light/dark `bg`, `ring-1`, `shadow-sm` (same idiom as
  EnergyPriceBar's wrapper).
- Fetch `/api/weather` once on mount (`useEffect` + `fetch`); no polling — the
  page already reloads hourly via `useAutoReload`, and the route is cached.
- States (Swedish, matching EnergyPriceBar tone):
  - loading → "Hämtar väder…"
  - error → "Fel uppstod vid laddning: {msg}"
  - empty → "Ingen väderprognos tillgänglig."
- Success: a `flex flex-row gap-1` of up to 7 `flex-1` boxes. Each box:
  - **Weekday** label on top: first box = "Idag", otherwise Swedish short day
    (Mån/Tis/Ons/Tor/Fre/Lör/Sön) via
    `new Intl.DateTimeFormat('sv-SE', { weekday: 'short', timeZone: 'Europe/Stockholm' })`.
  - **Icon**: `<img>` with `src` = CDN URL for `symbolCode`, fixed size (e.g.
    `w-10 h-10`), `alt` = symbolCode. `loading="eager"`.
  - **Hi/Lo**: `{tempMax}° / {tempMin}°` (max emphasised, min muted), "–" when null.

## Component 3 — Wiring `app/page.tsx`

Insert `<WeatherBar />` immediately before `<EnergyPriceBar />` (page.tsx:53),
inside the existing `flex flex-col gap-1.5` container so spacing is consistent.

## Non-goals / YAGNI

- No storage in VictoriaMetrics (forecasts aren't timeseries metrics; icons
  don't fit VM).
- No per-hour detail, no click-through / fullscreen view for weather.
- No multi-location support (single configured location).
- No new npm dependencies (native `Intl` handles TZ + weekday formatting).

## Testing

- **Local (pre-merge):**
  - Unit: aggregation function — feed a captured Locationforecast JSON fixture,
    assert 7 days, correct hi/lo, midday symbol selection, TZ day-grouping.
  - Manual: run `npm run dev`, load dashboard, confirm the weather row renders
    above the energy bar with icons + temps + Swedish weekdays; confirm
    `/api/weather` returns cached JSON; test with `WEATHER_LAT/LON` overridden.
  - Verify error/empty states by pointing at a bad URL / forcing a fetch failure.
- **Post-merge E2E (rpi5):** set `WEATHER_*` in `fetcher-core/webui/.env` (or leave
  defaults for Kulladal), redeploy, confirm the iPad dashboard shows the row and
  it survives the hourly reload.
