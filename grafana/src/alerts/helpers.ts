import * as cog from '@grafana/grafana-foundation-sdk/cog';
import * as alerting from '@grafana/grafana-foundation-sdk/alerting';

const EXPR_DS_UID = '__expr__';

interface ExprModel {
  refId: string;
  type: 'reduce' | 'threshold';
  datasource: { type: '__expr__'; uid: '__expr__' };
  expression: string;
  conditions: ExprCondition[];
  intervalMs: 1000;
  maxDataPoints: 43200;
  reducer?: string;
  _implementsDataqueryVariant(): void;
}

interface ExprCondition {
  evaluator: { params: number[]; type: ThresholdType | 'gt' };
  operator: { type: 'and' };
  query: { params: string[] };
  reducer: { params: never[]; type: ReducerFn };
  type: 'query';
}

export type ReducerFn = 'last' | 'mean' | 'sum' | 'min' | 'max' | 'count';
export type ThresholdType = 'gt' | 'lt' | 'eq' | 'within_range' | 'outside_range';

class ExprQueryBuilder implements cog.Builder<cog.Dataquery> {
  private readonly model: ExprModel;
  constructor(model: ExprModel) {
    this.model = model;
  }
  build(): cog.Dataquery {
    return this.model as unknown as cog.Dataquery;
  }
}

/**
 * Reduce expression: collapses the time series from `inputRefId` to a single
 * value using `reducer`. Mirrors the "Reduce" node in the Grafana alert UI.
 *
 * Grafana's pipeline alert model exposes two reducer fields on the reduce
 * node: a top-level `reducer` (the one the runtime actually uses) and a
 * legacy inner `conditions[].reducer.type` left over from the single-rule
 * UI. They're usually identical but the UI doesn't always sync them. Pass
 * `innerReducer` only when reproducing an existing alert that has them
 * out-of-step (e.g. "Energi per fas" exports `reducer=mean` but
 * `conditions[].reducer=last`); otherwise leave it default.
 */
export function reduceExpr(
  refId: string,
  inputRefId: string,
  reducer: ReducerFn,
  innerReducer: ReducerFn = reducer,
): alerting.QueryBuilder {
  const model: ExprModel = {
    refId,
    type: 'reduce',
    datasource: { type: '__expr__', uid: '__expr__' },
    expression: inputRefId,
    reducer,
    conditions: [
      {
        evaluator: { params: [], type: 'gt' },
        operator: { type: 'and' },
        query: { params: [] },
        reducer: { params: [], type: innerReducer },
        type: 'query',
      },
    ],
    intervalMs: 1000,
    maxDataPoints: 43200,
    _implementsDataqueryVariant() {},
  };
  return new alerting.QueryBuilder(refId)
    .queryType('expression')
    .datasourceUid(EXPR_DS_UID)
    .relativeTimeRange({ from: 0, to: 0 })
    .model(new ExprQueryBuilder(model));
}

/**
 * Threshold expression: emits 1 when `inputRefId`'s scalar value matches the
 * threshold, otherwise 0. Mirrors the "Threshold" node in the alert UI.
 */
export function thresholdExpr(
  refId: string,
  inputRefId: string,
  type: ThresholdType,
  ...params: number[]
): alerting.QueryBuilder {
  const model: ExprModel = {
    refId,
    type: 'threshold',
    datasource: { type: '__expr__', uid: '__expr__' },
    expression: inputRefId,
    conditions: [
      {
        evaluator: { params, type },
        operator: { type: 'and' },
        query: { params: [] },
        reducer: { params: [], type: 'last' },
        type: 'query',
      },
    ],
    intervalMs: 1000,
    maxDataPoints: 43200,
    _implementsDataqueryVariant() {},
  };
  return new alerting.QueryBuilder(refId)
    .queryType('expression')
    .datasourceUid(EXPR_DS_UID)
    .relativeTimeRange({ from: 0, to: 0 })
    .model(new ExprQueryBuilder(model));
}
