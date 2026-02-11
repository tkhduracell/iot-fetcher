import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmMetric, vmExpr } from '../datasource.ts';
import {
  thresholds, greenThreshold, paletteColor,
  legendBottom, tooltipSingle, tooltipMulti,
  overrideDisplayAndColor, overrideDisplayName,
  SPAN_NULLS_MS,
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
    .datasource(VM_DS)
    .unit('percent')
    .min(0)
    .max(100)
    .interval('1m')
    .colorScheme(paletteColor())
    .thresholds(energyThresholds())
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .spanNulls(SPAN_NULLS_MS)
    .withTarget(vmMetric('A', 'sigenergy_battery', 'soc_percent'))
    .gridPos({ h: 8, w: 9, x: 0, y: 46 });

  // ☀️ Solceller effekt (stat)
  const solar = new StatBuilder()
    .title('☀️ Solceller effekt')
    .datasource(VM_DS)
    .unit('kwatt')
    .min(0)
    .max(3.2)
    .thresholds(greenThreshold())
    .withTarget(
      vmMetric('A', 'sigenergy_pv_power', 'power_kw', {
        where: `"string" = 'total'`,
      }),
    )
    .gridPos({ h: 8, w: 4, x: 9, y: 46 });

  // 🪫 Urladdning (stat)
  const discharge = new StatBuilder()
    .title('🪫 Urladdning')
    .datasource(VM_DS)
    .unit('kwatt')
    .min(0)
    .max(10)
    .thresholds(greenThreshold())
    .withTarget(vmMetric('A', 'sigenergy_battery', 'power_to_battery_kw'))
    .gridPos({ h: 8, w: 4, x: 13, y: 46 });

  // ⚡️ Energiförbrukning - simple (timeseries, mean+max)
  const energySimple = new TimeseriesBuilder()
    .title('⚡️ Energiförbrukning')
    .datasource(VM_DS)
    .unit('watt')
    .interval('1h')
    .colorScheme(paletteColor())
    .thresholds(energyThresholds())
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .spanNulls(SPAN_NULLS_MS)
    .withTarget(vmMetric('DjUv', 'tibber', 'power'))
    .withTarget(vmMetric('B', 'tibber', 'power', { agg: 'MAX' }))
    .gridPos({ h: 8, w: 7, x: 17, y: 46 });

  // ⚡️ Energiförbrukning - detailed (timeseries, 4 queries)
  const energyDetailed = new TimeseriesBuilder()
    .title('⚡️ Energiförbrukning')
    .datasource(VM_DS)
    .unit('watt')
    .colorScheme(paletteColor())
    .thresholds(energyThresholds())
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .spanNulls(SPAN_NULLS_MS)
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
    .withTarget(vmMetric('Net', 'tibber', 'power'))
    .withTarget(
      vmExpr(
        'Battery',
        'avg_over_time(sigenergy_battery_power_to_battery_kw[$__interval]) * 1000',
        'power_to_battery_kw',
      ),
    )
    .withTarget(
      vmExpr(
        'Price',
        'last_over_time(energy_price_SEK_per_kWh[$__interval]) + 0.307 + 0.54875',
        'SEK_per_kWh',
      ),
    )
    .withTarget(
      vmExpr(
        'A',
        'avg_over_time(sigenergy_pv_power_power_kw{string="total"}[$__interval]) * 1000',
        'power_kw',
      ),
    )
    .gridPos({ h: 8, w: 9, x: 0, y: 54 });

  // ⚡️ Energiförbrukning (stat)
  const energyStat = new StatBuilder()
    .title('⚡️ Energiförbrukning')
    .datasource(VM_DS)
    .unit('kwatth')
    .thresholds(energyThresholds())
    .withTarget(vmMetric('A', 'tibber', 'accumulatedConsumption', { agg: 'LAST_VALUE' }))
    .gridPos({ h: 8, w: 3, x: 9, y: 54 });

  // ⚡️ Energiförbrukning per fas (timeseries)
  const energyPhases = new TimeseriesBuilder()
    .title('⚡️ Energiförbrukning per fas')
    .datasource(VM_DS)
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
    .spanNulls(SPAN_NULLS_MS)
    .withTarget(vmExpr('A', 'avg_over_time(tibber_powerL1[$__interval])', 'powerL1'))
    .withTarget(vmExpr('B', 'avg_over_time(tibber_powerL2[$__interval])', 'powerL2'))
    .withTarget(vmExpr('C', 'avg_over_time(tibber_powerL3[$__interval])', 'powerL3'))
    .gridPos({ h: 8, w: 12, x: 12, y: 54 });

  // Dygnskostnad 30d (bar chart)
  const dailyCost30d = new TimeseriesBuilder()
    .title('Dygnskostnad')
    .datasource(VM_DS)
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
    .spanNulls(SPAN_NULLS_MS)
    .withTarget(vmMetric('A', 'tibber', 'accumulatedCost', { agg: 'MAX' }))
    .timeFrom('30d/d')
    .gridPos({ h: 6, w: 7, x: 0, y: 62 });

  // Dygnsförbrukning 30d (bar chart)
  const dailyConsumption30d = new TimeseriesBuilder()
    .title('Dygnsförbrukning')
    .datasource(VM_DS)
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
    .spanNulls(SPAN_NULLS_MS)
    .withTarget(vmMetric('A', 'tibber', 'accumulatedConsumption', { agg: 'MAX' }))
    .timeFrom('30d/d')
    .gridPos({ h: 6, w: 9, x: 7, y: 62 });

  // Veckopris (timeseries, stepAfter, 7d)
  const weeklyPrice = new TimeseriesBuilder()
    .title('Veckopris')
    .datasource(VM_DS)
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
    .spanNulls(SPAN_NULLS_MS)
    .withTarget(
      vmMetric('A', 'energy_price', 'SEK_per_kWh', {
        agg: 'LAST_VALUE',
        where: `"area" = 'SE4'`,
      }),
    )
    .timeFrom('7d/d')
    .gridPos({ h: 6, w: 8, x: 16, y: 62 });

  // Dygnskostnad 1d (timeseries)
  const dailyCost1d = new TimeseriesBuilder()
    .title('Dygnskostnad')
    .datasource(VM_DS)
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
    .spanNulls(SPAN_NULLS_MS)
    .withTarget(vmMetric('A', 'tibber', 'accumulatedCost'))
    .timeFrom('1d/d')
    .gridPos({ h: 8, w: 7, x: 0, y: 68 });

  // Dygnsförbrukning 1d (timeseries)
  const dailyConsumption1d = new TimeseriesBuilder()
    .title('Dygnsförbrukning')
    .datasource(VM_DS)
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
    .spanNulls(SPAN_NULLS_MS)
    .withTarget(vmMetric('A', 'tibber', 'accumulatedConsumption'))
    .timeFrom('1d/d')
    .gridPos({ h: 8, w: 9, x: 7, y: 68 });

  // Dygnspris (timeseries, stepAfter, 1d)
  const dailyPrice = new TimeseriesBuilder()
    .title('Dygnspris')
    .datasource(VM_DS)
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
    .spanNulls(SPAN_NULLS_MS)
    .withTarget(
      vmMetric('A', 'energy_price', 'SEK_per_kWh', {
        agg: 'LAST_VALUE',
        where: `"area" = 'SE4'`,
      }),
    )
    .timeFrom('1d/d')
    .gridPos({ h: 8, w: 8, x: 16, y: 68 });

  return [
    battery, solar, discharge, energySimple,
    energyDetailed, energyStat, energyPhases,
    dailyCost30d, dailyConsumption30d, weeklyPrice,
    dailyCost1d, dailyConsumption1d, dailyPrice,
  ];
}
