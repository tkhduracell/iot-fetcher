import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as TableBuilder } from '@grafana/grafana-foundation-sdk/table';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { INFLUXDB_DS, influxRawSql } from '../datasource.ts';
import { greenRedThresholds, paletteColor, legendBottom, tooltipSingle } from '../helpers.ts';

export function sonosPanels(): cog.Builder<dashboard.Panel>[] {
  // Högtalare 🔊 - volume over time
  const speakers = new TimeseriesBuilder()
    .title('Högtalare 🔊')
    .datasource(INFLUXDB_DS)
    .unit('percent')
    .min(0)
    .max(100)
    .colorScheme(paletteColor())
    .thresholds(greenRedThresholds(80))
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .withTarget(
      influxRawSql('A', [
        `SELECT`,
        `  DATE_BIN(INTERVAL '$__interval', time) AS time,`,
        `  room_name,`,
        `  AVG("volume") AS "volume"`,
        `FROM "sonos_playback"`,
        `WHERE time >= $__timeFrom AND time <= $__timeTo`,
        `GROUP BY 1, room_name`,
        `ORDER BY 1`,
      ].join('\n')),
    )
    .gridPos({ h: 6, w: 10, x: 0, y: 1 });

  // Now Playing 🎧 - distinct track info
  const nowPlaying = new TableBuilder()
    .title('Now Playing 🎧')
    .datasource(INFLUXDB_DS)
    .thresholds(greenRedThresholds())
    .withTarget(
      influxRawSql('A', [
        `SELECT DISTINCT "track_info" AS "Track"`,
        `FROM "sonos_playback"`,
        `WHERE time >= $__timeFrom AND time <= $__timeTo`,
        `  AND "track_info" != 'Unknown'`,
        `ORDER BY time DESC`,
      ].join('\n')).resultFormat('table'),
    )
    .gridPos({ h: 6, w: 10, x: 10, y: 1 });

  return [speakers, nowPlaying];
}
