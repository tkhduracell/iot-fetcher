import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';

const INFLUXDB_DS_UID = process.env.INFLUXDB_DS_UID ?? 'influxdb3';

export const INFLUXDB_DS: dashboard.DataSourceRef = {
  type: 'influxdb',
  uid: INFLUXDB_DS_UID,
};

interface InfluxDbQuery {
  refId: string;
  datasource: dashboard.DataSourceRef;
  rawSql: string;
  resultFormat: string;
  dataset?: string;
  table?: string;
  _implementsDataqueryVariant(): void;
}

export class InfluxDbQueryBuilder implements cog.Builder<cog.Dataquery> {
  private readonly internal: InfluxDbQuery;

  constructor(refId: string) {
    this.internal = {
      refId,
      datasource: INFLUXDB_DS,
      rawSql: '',
      resultFormat: 'time_series',
      _implementsDataqueryVariant() {},
    };
  }

  sql(rawSql: string): this {
    this.internal.rawSql = rawSql;
    return this;
  }

  datasource(ds: dashboard.DataSourceRef): this {
    this.internal.datasource = ds;
    return this;
  }

  resultFormat(format: string): this {
    this.internal.resultFormat = format;
    return this;
  }

  table(table: string): this {
    this.internal.table = table;
    return this;
  }

  build(): cog.Dataquery {
    return this.internal as unknown as cog.Dataquery;
  }
}

/** Shorthand to create a time-bucketed SQL query for a single field */
export function influxSql(
  refId: string,
  measurement: string,
  field: string,
  opts: {
    agg?: 'AVG' | 'MAX' | 'LAST_VALUE' | 'MEDIAN';
    where?: string;
    expr?: string;
  } = {},
): InfluxDbQueryBuilder {
  const agg = opts.agg ?? 'AVG';
  const col = opts.expr ?? `"${field}"`;
  const alias = `"${field}"`;
  const where = opts.where ? ` AND ${opts.where}` : '';
  const sql = [
    `SELECT`,
    `  DATE_BIN(INTERVAL '$__interval', time) AS time,`,
    `  ${agg}(${col}) AS ${alias}`,
    `FROM "${measurement}"`,
    `WHERE time >= $__timeFrom AND time <= $__timeTo${where}`,
    `GROUP BY 1`,
    `ORDER BY 1`,
  ].join('\n');
  return new InfluxDbQueryBuilder(refId).sql(sql);
}

/** Raw SQL query without aggregation helpers */
export function influxRawSql(refId: string, sql: string): InfluxDbQueryBuilder {
  return new InfluxDbQueryBuilder(refId).sql(sql);
}
