import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import { PanelBuilder as GaugeBuilder } from '@grafana/grafana-foundation-sdk/gauge';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { INFLUXDB_DS, influxRawSql, influxSql } from '../datasource.ts';
import {
  greenThreshold, greenRedThresholds, paletteColor,
  legendBottom, tooltipMulti,
  overrideDisplayAndColor,
} from '../helpers.ts';

export function spaPanels(): cog.Builder<dashboard.Panel>[] {
  // Spabadet (timeseries) - value/min/max over last 24h
  const spaTs = new TimeseriesBuilder()
    .title('Spabadet')
    .datasource(INFLUXDB_DS)
    .unit('celsius')
    .min(0)
    .max(45)
    .lineInterpolation('smooth' as any)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .withTarget(
      influxRawSql('A', [
        `SELECT`,
        `  DATE_BIN(INTERVAL '$__interval', time) AS time,`,
        `  AVG("value") AS "value",`,
        `  AVG("min") AS "min",`,
        `  AVG("max") AS "max"`,
        `FROM "spa_temperature"`,
        `WHERE time >= $__timeFrom AND time <= $__timeTo`,
        `GROUP BY 1`,
        `ORDER BY 1`,
      ].join('\n')),
    )
    .gridPos({ h: 8, w: 12, x: 0, y: 38 });

  // Spabadet (stat) - latest temp values
  const spaStat = new StatBuilder()
    .title('Spabadet')
    .datasource(INFLUXDB_DS)
    .unit('celsius')
    .thresholds(greenThreshold())
    .overrides([
      overrideDisplayAndColor('value', 'Temperatur', 'purple'),
      overrideDisplayAndColor('min', 'Min', 'blue'),
      overrideDisplayAndColor('max', 'Max', 'red'),
    ])
    .withTarget(
      influxRawSql('A', [
        `SELECT`,
        `  DATE_BIN(INTERVAL '$__interval', time) AS time,`,
        `  AVG("value") AS "value",`,
        `  AVG("min") AS "min",`,
        `  AVG("max") AS "max"`,
        `FROM "spa_temperature"`,
        `WHERE time >= $__timeFrom AND time <= $__timeTo`,
        `GROUP BY 1`,
        `ORDER BY 1`,
      ].join('\n')),
    )
    .gridPos({ h: 8, w: 4, x: 12, y: 38 });

  // Spa Active? (gauge)
  const spaActive = new GaugeBuilder()
    .title('Spa Active?')
    .datasource(INFLUXDB_DS)
    .unit('bool_on_off')
    .thresholds(greenRedThresholds(80))
    .withTarget(
      influxSql('A', 'spa_mode', 'enabled', { agg: 'AVG' }),
    )
    .gridPos({ h: 8, w: 4, x: 16, y: 38 });

  // Spa Circulation (gauge)
  const spaCirculation = new GaugeBuilder()
    .title('Spa Circulation')
    .datasource(INFLUXDB_DS)
    .unit('bool_on_off')
    .thresholds(greenThreshold())
    .withTarget(
      influxSql('A', 'spa_circulation_pump', 'enabled', { agg: 'AVG' }),
    )
    .gridPos({ h: 8, w: 4, x: 20, y: 38 });

  return [spaTs, spaStat, spaActive, spaCirculation];
}
