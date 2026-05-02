import * as cog from '@grafana/grafana-foundation-sdk/cog';
import * as alerting from '@grafana/grafana-foundation-sdk/alerting';
import { reduceExpr, thresholdExpr } from './helpers.ts';

const INFLUX_DS_UID = 'aehviakqvk2dca';

const FLUX_QUERY = `from(bucket: "irisgatan")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "ngenic_node_sensor_measurement_value")
  |> filter(fn: (r) => r["_field"] == "humidity_relative_percent")
  |> filter(fn: (r) => r["node_type"] == "SENSOR")
  |> aggregateWindow(every: v.windowPeriod, fn: count, createEmpty: true)
  |> yield(name: "mean")`;

interface InfluxFluxQuery {
  refId: string;
  alias: string;
  datasource: { type: 'influxdb'; uid: string };
  groupBy: Array<{ params: string[]; type: string }>;
  instant: boolean;
  intervalMs: number;
  maxDataPoints: number;
  measurement: string;
  orderByTime: 'ASC' | 'DESC';
  policy: string;
  query: string;
  queryType: 'Flux';
  range: boolean;
  resultFormat: 'time_series';
  select: Array<Array<{ params: string[]; type: string }>>;
  tags: Array<{ key: string; operator: string; value: string }>;
  _implementsDataqueryVariant(): void;
}

class InfluxQueryShim implements cog.Builder<cog.Dataquery> {
  private readonly model: InfluxFluxQuery;
  constructor(model: InfluxFluxQuery) {
    this.model = model;
  }
  build(): cog.Dataquery {
    return this.model as unknown as cog.Dataquery;
  }
}

/**
 * Ngenic indoor humidity drop alert. Currently paused — preserved here as
 * code so a re-enable is a one-liner. Uses InfluxDB Flux because the source
 * data still flows through the legacy Influx pipeline rather than VM.
 */
export function ngenicLuftfuktighet(): alerting.RuleBuilder {
  const query: InfluxFluxQuery = {
    refId: 'A',
    alias: 'Fuktighet',
    datasource: { type: 'influxdb', uid: INFLUX_DS_UID },
    groupBy: [
      { params: ['$__interval'], type: 'time' },
      { params: ['linear'], type: 'fill' },
    ],
    instant: false,
    intervalMs: 3600000,
    maxDataPoints: 43200,
    measurement: 'ngenic_node_sensor_measurement_value',
    orderByTime: 'ASC',
    policy: 'default',
    query: FLUX_QUERY,
    queryType: 'Flux',
    range: true,
    resultFormat: 'time_series',
    select: [
      [
        { params: ['humidity_relative_percent'], type: 'field' },
        { params: [], type: 'mean' },
      ],
    ],
    tags: [{ key: 'node_type::tag', operator: '=', value: 'SENSOR' }],
    _implementsDataqueryVariant() {},
  };

  return new alerting.RuleBuilder('Ngenic Innegivare - Relativ Luftfuktighet')
    .uid('cf6onky43wq9sc')
    .ruleGroup('Irisgatan')
    .condition('C')
    .forDuration('30m')
    .noDataState('Alerting')
    .execErrState('Error')
    .isPaused(true)
    .annotations({ __dashboardUid__: 'aehvj7vxn6vi8f', __panelId__: '23' })
    .notificationSettings(new alerting.NotificationSettingsBuilder().receiver('Slack'))
    .withQuery(
      new alerting.QueryBuilder('A')
        .queryType('Flux')
        .relativeTimeRange({ from: 172800, to: 0 })
        .datasourceUid(INFLUX_DS_UID)
        .model(new InfluxQueryShim(query)),
    )
    .withQuery(reduceExpr('B', 'A', 'sum'))
    .withQuery(thresholdExpr('C', 'B', 'eq', 0));
}
