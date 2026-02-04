import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';

const VM_DS_UID = process.env.VM_DS_UID ?? 'cfc7gnph2ojr4d';

export const VM_DS: dashboard.DataSourceRef = {
  type: 'victoriametrics-metrics-datasource',
  uid: VM_DS_UID,
};

interface VMQuery {
  refId: string;
  datasource: dashboard.DataSourceRef;
  expr: string;
  legendFormat?: string;
  range: boolean;
  instant: boolean;
  _implementsDataqueryVariant(): void;
}

export class VMQueryBuilder implements cog.Builder<cog.Dataquery> {
  private readonly internal: VMQuery;

  constructor(refId: string) {
    this.internal = {
      refId,
      datasource: VM_DS,
      expr: '',
      range: true,
      instant: false,
      _implementsDataqueryVariant() {},
    };
  }

  expr(expression: string): this {
    this.internal.expr = expression;
    return this;
  }

  legendFormat(format: string): this {
    this.internal.legendFormat = format;
    return this;
  }

  instant(value = true): this {
    this.internal.instant = value;
    this.internal.range = !value;
    return this;
  }

  build(): cog.Dataquery {
    return this.internal as unknown as cog.Dataquery;
  }
}

type Agg = 'AVG' | 'MAX' | 'LAST_VALUE' | 'MEDIAN';

const AGG_FN: Record<Agg, string> = {
  AVG: 'avg_over_time',
  MAX: 'max_over_time',
  LAST_VALUE: 'last_over_time',
  MEDIAN: 'mad_over_time', // fallback; see quantile below
};

/** Build a MetricsQL metric name from measurement + field (InfluxDB line-protocol convention) */
function metricName(measurement: string, field: string): string {
  return `${measurement}_${field}`;
}

/** Convert SQL-style WHERE conditions to PromQL label selectors */
function labelSelector(where?: string): string {
  if (!where) return '';
  const conditions = where
    .split(/\s+AND\s+/i)
    .map((c) => {
      const m = c.trim().match(/^"?(\w+)"?\s*=\s*'([^']+)'$/);
      return m ? `${m[1]}="${m[2]}"` : '';
    })
    .filter(Boolean);
  return conditions.length > 0 ? `{${conditions.join(', ')}}` : '';
}

/**
 * Shorthand to create a MetricsQL query for a single metric.
 *
 * Metric name is derived as `measurement_field` (InfluxDB line-protocol naming).
 * legendFormat defaults to the field name so existing panel overrides (byName)
 * continue to match.
 */
export function vmMetric(
  refId: string,
  measurement: string,
  field: string,
  opts: {
    agg?: Agg;
    where?: string;
    expr?: string;
  } = {},
): VMQueryBuilder {
  const agg = opts.agg ?? 'AVG';
  const metric = opts.expr ?? metricName(measurement, field);
  const labels = labelSelector(opts.where);

  let expression: string;
  if (agg === 'MEDIAN') {
    expression = `quantile_over_time(0.5, ${metric}${labels}[$__interval])`;
  } else {
    expression = `${AGG_FN[agg]}(${metric}${labels}[$__interval])`;
  }

  return new VMQueryBuilder(refId).expr(expression).legendFormat(field);
}

/** Raw MetricsQL expression */
export function vmExpr(refId: string, expression: string, legend?: string): VMQueryBuilder {
  const b = new VMQueryBuilder(refId).expr(expression);
  if (legend !== undefined) b.legendFormat(legend);
  return b;
}
