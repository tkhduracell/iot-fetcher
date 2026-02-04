import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmExpr } from '../datasource.ts';
import { greenRedThresholds, paletteColor, legendBottom, tooltipSingle, SPAN_NULLS_MS } from '../helpers.ts';

export function sonosPanels(): cog.Builder<dashboard.Panel>[] {
  // Högtalare 🔊 - volume over time, grouped by room_name label
  const speakers = new TimeseriesBuilder()
    .title('Högtalare 🔊')
    .datasource(VM_DS)
    .unit('percent')
    .min(0)
    .max(100)
    .colorScheme(paletteColor())
    .thresholds(greenRedThresholds(80))
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .spanNulls(SPAN_NULLS_MS)
    .withTarget(
      vmExpr('A', 'avg_over_time(sonos_playback_volume[$__interval])', '{{room_name}}'),
    )
    .gridPos({ h: 6, w: 20, x: 0, y: 1 });

  return [speakers];
}
