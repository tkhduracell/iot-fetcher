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
