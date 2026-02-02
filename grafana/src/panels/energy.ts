import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { INFLUXDB_DS, influxSql, influxRawSql } from '../datasource.ts';
import {
  thresholds, greenThreshold, paletteColor,
  legendBottom, tooltipSingle, tooltipMulti,
  overrideDisplayAndColor, overrideDisplayName,
} from '../helpers.ts';

const energyThresholds = () => thresholds([
  { color: 'green', value: null },
  { color: '#EAB839', value: 12000 },
  { color: 'red', value: 17250 },
]);

export function energyPanels(): cog.Builder<dashboard.Panel>[] {
  // 🔋 Sigstore batterinivå (timeseries)
  const battery = new TimeseriesBuilder()
    .title('🔋 Sigstore batterinivå')
    .datasource(INFLUXDB_DS)
    .unit('percent')
    .min(0)
    .max(100)
    .interval('1m')
    .colorScheme(paletteColor())
    .thresholds(energyThresholds())
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .withTarget(influxSql('A', 'sigenergy_battery', 'soc_percent'))
    .gridPos({ h: 8, w: 9, x: 0, y: 47 });

  // ☀️ Solceller effekt (stat)
  const solar = new StatBuilder()
    .title('☀️ Solceller effekt')
    .datasource(INFLUXDB_DS)
    .unit('kwatt')
    .min(0)
    .max(3.2)
    .thresholds(greenThreshold())
    .withTarget(
      influxSql('A', 'sigenergy_pv_power', 'power_kw', {
        where: `"string" = 'total'`,
      }),
    )
    .gridPos({ h: 8, w: 4, x: 9, y: 47 });

  // 🪫 Urladdning (stat)
  const discharge = new StatBuilder()
    .title('🪫 Urladdning')
    .datasource(INFLUXDB_DS)
    .unit('kwatt')
    .min(0)
    .max(10)
    .thresholds(greenThreshold())
    .withTarget(influxSql('A', 'sigenergy_battery', 'power_to_battery_kw'))
    .gridPos({ h: 8, w: 4, x: 13, y: 47 });

  // ⚡️ Energiförbrukning - simple (timeseries, mean+max)
  const energySimple = new TimeseriesBuilder()
    .title('⚡️ Energiförbrukning')
    .datasource(INFLUXDB_DS)
    .unit('watt')
    .interval('1h')
    .colorScheme(paletteColor())
    .thresholds(energyThresholds())
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .withTarget(influxSql('DjUv', 'tibber', 'power'))
    .withTarget(influxSql('B', 'tibber', 'power', { agg: 'MAX' }))
    .gridPos({ h: 8, w: 7, x: 17, y: 47 });

  // ⚡️ Energiförbrukning - detailed (timeseries, 4 queries)
  const energyDetailed = new TimeseriesBuilder()
    .title('⚡️ Energiförbrukning')
    .datasource(INFLUXDB_DS)
    .unit('watt')
    .colorScheme(paletteColor())
    .thresholds(energyThresholds())
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .overrides([
      overrideDisplayName('power', 'Inköp'),
      overrideDisplayAndColor('power_to_battery_kw', 'Batteri', 'blue'),
      {
        matcher: { id: 'byName', options: 'SEK_per_kWh' },
        properties: [
          { id: 'custom.axisPlacement', value: 'right' },
          { id: 'unit', value: 'currencySEK' },
          { id: 'displayName', value: 'Elpris (inkl nät&skatt)' },
          { id: 'color', value: { fixedColor: 'light-purple', mode: 'fixed' } },
          { id: 'custom.axisSoftMin', value: 0 },
          { id: 'custom.lineInterpolation', value: 'stepAfter' },
          { id: 'custom.fillOpacity', value: 3 },
          { id: 'custom.axisSoftMax', value: 3 },
        ],
      },
      overrideDisplayAndColor('power_kw', 'Solceller', 'yellow'),
    ])
    .withTarget(influxSql('Net', 'tibber', 'power'))
    .withTarget(
      influxRawSql('Battery', [
        `SELECT`,
        `  DATE_BIN(INTERVAL '$__interval', time) AS time,`,
        `  AVG("power_to_battery_kw") * 1000 AS "power_to_battery_kw"`,
        `FROM "sigenergy_battery"`,
        `WHERE time >= $__timeFrom AND time <= $__timeTo`,
        `GROUP BY 1`,
        `ORDER BY 1`,
      ].join('\n')),
    )
    .withTarget(
      influxRawSql('Price', [
        `SELECT`,
        `  DATE_BIN(INTERVAL '$__interval', time) AS time,`,
        `  LAST_VALUE("SEK_per_kWh") + 0.307 + 0.54875 AS "SEK_per_kWh"`,
        `FROM "energy_price"`,
        `WHERE time >= $__timeFrom AND time <= $__timeTo`,
        `GROUP BY 1`,
        `ORDER BY 1`,
      ].join('\n')),
    )
    .withTarget(
      influxRawSql('A', [
        `SELECT`,
        `  DATE_BIN(INTERVAL '$__interval', time) AS time,`,
        `  AVG("power_kw") * 1000 AS "power_kw"`,
        `FROM "sigenergy_pv_power"`,
        `WHERE time >= $__timeFrom AND time <= $__timeTo`,
        `  AND "string" = 'total'`,
        `GROUP BY 1`,
        `ORDER BY 1`,
      ].join('\n')),
    )
    .gridPos({ h: 8, w: 9, x: 0, y: 55 });

  // ⚡️ Energiförbrukning (stat)
  const energyStat = new StatBuilder()
    .title('⚡️ Energiförbrukning')
    .datasource(INFLUXDB_DS)
    .unit('kwatth')
    .thresholds(energyThresholds())
    .withTarget(influxSql('A', 'tibber', 'accumulatedConsumption', { agg: 'LAST_VALUE' }))
    .gridPos({ h: 8, w: 3, x: 9, y: 55 });

  // ⚡️ Energiförbrukning per fas (timeseries)
  const energyPhases = new TimeseriesBuilder()
    .title('⚡️ Energiförbrukning per fas')
    .datasource(INFLUXDB_DS)
    .unit('watt')
    .colorScheme(paletteColor())
    .thresholds(thresholds([
      { color: 'green', value: null },
      { color: '#EAB839', value: 4600 },
      { color: 'super-light-red', value: 5750 },
      { color: 'red', value: 6900 },
    ]))
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .withTarget(
      influxRawSql('A', [
        `SELECT`,
        `  DATE_BIN(INTERVAL '$__interval', time) AS time,`,
        `  AVG("powerL1") AS "powerL1",`,
        `  AVG("powerL2") AS "powerL2",`,
        `  AVG("powerL3") AS "powerL3"`,
        `FROM "tibber"`,
        `WHERE time >= $__timeFrom AND time <= $__timeTo`,
        `GROUP BY 1`,
        `ORDER BY 1`,
      ].join('\n')),
    )
    .gridPos({ h: 8, w: 12, x: 12, y: 55 });

  // Dygnskostnad 30d (bar chart)
  const dailyCost30d = new TimeseriesBuilder()
    .title('Dygnskostnad')
    .datasource(INFLUXDB_DS)
    .unit('currencySEK')
    .drawStyle('bars' as any)
    .fillOpacity(100)
    .axisSoftMin(0)
    .axisSoftMax(75)
    .interval('1d')
    .colorScheme(paletteColor())
    .thresholds(thresholds([
      { color: 'green', value: null },
      { color: '#EAB839', value: 50 },
      { color: 'orange', value: 60 },
      { color: 'red', value: 70 },
    ]))
    .legend(legendBottom(false))
    .tooltip(tooltipMulti())
    .withTarget(influxSql('A', 'tibber', 'accumulatedCost', { agg: 'MAX' }))
    .timeFrom('30d/d')
    .gridPos({ h: 6, w: 7, x: 0, y: 63 });

  // Dygnsförbrukning 30d (bar chart)
  const dailyConsumption30d = new TimeseriesBuilder()
    .title('Dygnsförbrukning')
    .datasource(INFLUXDB_DS)
    .unit('kwatth')
    .drawStyle('bars' as any)
    .fillOpacity(100)
    .axisSoftMin(0)
    .interval('1d')
    .colorScheme(paletteColor())
    .thresholds(thresholds([
      { color: 'green', value: null },
      { color: '#EAB839', value: 30 },
      { color: 'orange', value: 40 },
      { color: 'red', value: 50 },
    ]))
    .legend(legendBottom(false))
    .tooltip(tooltipSingle())
    .withTarget(influxSql('A', 'tibber', 'accumulatedConsumption', { agg: 'MAX' }))
    .timeFrom('30d/d')
    .gridPos({ h: 6, w: 9, x: 7, y: 63 });

  // Veckopris (timeseries, stepAfter, 7d)
  const weeklyPrice = new TimeseriesBuilder()
    .title('Veckopris')
    .datasource(INFLUXDB_DS)
    .unit('currencySEK')
    .axisSoftMin(0)
    .axisSoftMax(0.8)
    .lineInterpolation('stepAfter' as any)
    .interval('15m')
    .colorScheme(paletteColor())
    .thresholds(thresholds([
      { color: 'green', value: null },
      { color: '#EAB839', value: 0.4 },
      { color: 'orange', value: 0.6 },
      { color: 'red', value: 0.9 },
    ]))
    .legend(legendBottom(false))
    .tooltip(tooltipMulti())
    .queryCachingTTL(600000)
    .withTarget(
      influxSql('A', 'energy_price', 'SEK_per_kWh', {
        agg: 'LAST_VALUE',
        where: `"area" = 'SE4'`,
      }),
    )
    .timeFrom('7d/d')
    .gridPos({ h: 6, w: 8, x: 16, y: 63 });

  // Dygnskostnad 1d (timeseries)
  const dailyCost1d = new TimeseriesBuilder()
    .title('Dygnskostnad')
    .datasource(INFLUXDB_DS)
    .unit('currencySEK')
    .axisSoftMin(0)
    .axisSoftMax(75)
    .colorScheme(paletteColor())
    .thresholds(thresholds([
      { color: 'green', value: null },
      { color: '#EAB839', value: 50 },
      { color: 'orange', value: 60 },
      { color: 'red', value: 70 },
    ]))
    .legend(legendBottom(false))
    .tooltip(tooltipMulti())
    .withTarget(influxSql('A', 'tibber', 'accumulatedCost'))
    .timeFrom('1d/d')
    .gridPos({ h: 8, w: 7, x: 0, y: 69 });

  // Dygnsförbrukning 1d (timeseries)
  const dailyConsumption1d = new TimeseriesBuilder()
    .title('Dygnsförbrukning')
    .datasource(INFLUXDB_DS)
    .unit('kwatth')
    .axisSoftMin(0)
    .axisSoftMax(75)
    .colorScheme(paletteColor())
    .thresholds(thresholds([
      { color: 'green', value: null },
      { color: '#EAB839', value: 30 },
      { color: 'orange', value: 40 },
      { color: 'red', value: 50 },
    ]))
    .legend(legendBottom(false))
    .tooltip(tooltipMulti())
    .withTarget(influxSql('A', 'tibber', 'accumulatedConsumption'))
    .timeFrom('1d/d')
    .gridPos({ h: 8, w: 9, x: 7, y: 69 });

  // Dygnspris (timeseries, stepAfter, 1d)
  const dailyPrice = new TimeseriesBuilder()
    .title('Dygnspris')
    .datasource(INFLUXDB_DS)
    .unit('currencySEK')
    .axisSoftMin(0)
    .axisSoftMax(1)
    .lineInterpolation('stepAfter' as any)
    .interval('15m')
    .colorScheme(paletteColor())
    .thresholds(thresholds([
      { color: 'green', value: null },
      { color: '#EAB839', value: 70 },
      { color: 'orange', value: 120 },
      { color: 'red', value: 170 },
    ]))
    .legend(legendBottom(false))
    .tooltip(tooltipMulti())
    .withTarget(
      influxSql('A', 'energy_price', 'SEK_per_kWh', {
        agg: 'LAST_VALUE',
        where: `"area" = 'SE4'`,
      }),
    )
    .timeFrom('1d/d')
    .gridPos({ h: 8, w: 8, x: 16, y: 69 });

  return [
    battery, solar, discharge, energySimple,
    energyDetailed, energyStat, energyPhases,
    dailyCost30d, dailyConsumption30d, weeklyPrice,
    dailyCost1d, dailyConsumption1d, dailyPrice,
  ];
}
