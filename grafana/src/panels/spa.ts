import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import { PanelBuilder as GaugeBuilder } from '@grafana/grafana-foundation-sdk/gauge';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmMetric, vmExpr } from '../datasource.ts';
import {
  greenThreshold, greenRedThresholds, paletteColor,
  legendBottom, tooltipMulti,
  overrideDisplayAndColor, overrideDisplayName,
  SPAN_NULLS_MS,
} from '../helpers.ts';
import { spaCostExpr } from '../energyCost.ts';

export function spaPanels(): cog.Builder<dashboard.Panel>[] {
  // Spabadet (timeseries) - current temperature from HA climate entity
  const spaTs = new TimeseriesBuilder()
    .title('Spabadet')
    .datasource(VM_DS)
    .unit('celsius')
    .min(0)
    .max(45)
    .lineInterpolation('smooth' as any)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .insertNulls(SPAN_NULLS_MS)
    .overrides([
      overrideDisplayAndColor('current_temperature_value', 'Temperatur', 'light-green'),
      overrideDisplayAndColor('state_text', 'Temp Range', 'yellow'),
    ])
    .withTarget(vmMetric('A', 'spa_climate', 'current_temperature_value'))
    .withTarget(vmExpr('B', 'last_over_time(spa_temperature_range_state_text[$__interval])', 'state_text'))
    .gridPos({ h: 8, w: 12, x: 0, y: 69 });

  // Spabadet (stat) - latest temp
  const spaStat = new StatBuilder()
    .title('Spabadet')
    .datasource(VM_DS)
    .unit('celsius')
    .thresholds(greenThreshold())
    .overrides([
      overrideDisplayAndColor('current_temperature_value', 'Temperatur', 'purple'),
    ])
    .withTarget(vmMetric('A', 'spa_climate', 'current_temperature_value'))
    .gridPos({ h: 8, w: 4, x: 12, y: 69 });

  // Spa Circulation (gauge)
  const spaCirculation = new GaugeBuilder()
    .title('Spa Circulation')
    .datasource(VM_DS)
    .unit('bool_on_off')
    .thresholds(greenThreshold())
    .overrides([
      overrideDisplayName('circulation', 'Circulation'),
      overrideDisplayName('spa_pump_1_value', 'Jet 1'),
      overrideDisplayName('spa_pump_2_value', 'Jet 2'),
    ])
    .withTarget(vmExpr('A', 'last_over_time(spa_circulation_pump_value[$__interval])', 'circulation'))
    .withTarget(vmMetric('B', 'spa_pump_1', 'value'))
    .withTarget(vmMetric('C', 'spa_pump_2', 'value'))
    .gridPos({ h: 8, w: 8, x: 16, y: 69 });

  // Modelled spa cost. Power comes from HA state signals × calibrated wattages
  // (jets 2000 W, heater 3000 W, circ 80 W), priced with the same SE4 spot +
  // nätavgift + energiskatt + moms loading the pool panels use.
  const KOSTNAD_BESKRIVNING =
    'Modellerad elkostnad för spabadet: jetpumpar 2000 W, värmare 3000 W, ' +
    'cirkulationspump 80 W (uppmätt). Pris = spotpris (SE4) + nätavgift + ' +
    'energiskatt + moms 25%. Cirkulationspumpens värde inkluderar ozonaggregatet, ' +
    'som styrs tillsammans med pumpen.\n\n' +
    '**Värmaren är ännu inte med i beloppet nedan.** Signalen `spa_heater_on` finns ' +
    'inte i VictoriaMetrics förrän en Home Assistant-mallsensor skapats för hand. ' +
    'Värmaren står för ~83% av spabadets modellerade energi, så summan täcker just nu ' +
    'bara jetpumpar + cirkulationspump och underskattar den verkliga kostnaden kraftigt ' +
    '(grovt sett en sjättedel av den faktiska).';

  const spaCostLastMonth = new StatBuilder()
    .title('Spa kostnad senaste månaden')
    .description(KOSTNAD_BESKRIVNING)
    .datasource(VM_DS)
    .unit('currencySEK')
    .thresholds(greenThreshold())
    .withTarget(vmExpr(
      'A',
      spaCostExpr({ step: '15m', stepMinutes: 15, range: '$__range' }),
      '30 dagar',
    ))
    .timeFrom('30d')
    .gridPos({ h: 8, w: 8, x: 0, y: 77 });

  // 1h resampling (not 15m) keeps the 12-month point count well under VM's
  // per-series limit; the price series is 15-minutely, so hourly averaging is
  // more than adequate for monthly totals.
  const spaCostPerMonth = new TimeseriesBuilder()
    .title('Spa kostnad per månad')
    .description(
      KOSTNAD_BESKRIVNING +
      ' Månader före mätdatans början visas som tomma, inte som noll.'
    )
    .datasource(VM_DS)
    .unit('currencySEK')
    .drawStyle('bars' as any)
    .fillOpacity(100)
    .axisSoftMin(0)
    .interval('1M')
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .withTarget(vmExpr(
      'A',
      spaCostExpr({ step: '1h', stepMinutes: 60, range: '$__interval' }),
      'Spa',
    ))
    .timeFrom('12M')
    .gridPos({ h: 8, w: 8, x: 8, y: 77 });

  // avg_over_time × 24 rather than sum_over_time × sample-interval: robust to the
  // exporter's irregular ~60 s cadence.
  //
  // Asymmetric with the cost expressions during an exporter outage: avg_over_time
  // extrapolates the last observed duty cycle across the gap (overstating runtime),
  // while spaPowerWattsExpr's `sum(...) * W` collapses a sample-less window to 0 W
  // (understating cost). Neither is "more correct" — they just fail in opposite
  // directions when the exporter is down.
  const circRuntime = new StatBuilder()
    .title('Cirkulationspump drifttid 24h')
    .description('Antal timmar cirkulationspumpen varit igång det senaste dygnet.')
    .datasource(VM_DS)
    .unit('h')
    .thresholds(greenThreshold())
    .withTarget(vmExpr(
      'A',
      'sum(avg_over_time(spa_circulation_pump_value[24h])) * 24',
      'Drifttid',
    ))
    .gridPos({ h: 8, w: 8, x: 16, y: 77 });

  return [spaTs, spaStat, spaCirculation, spaCostLastMonth, spaCostPerMonth, circRuntime];
}
