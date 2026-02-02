import * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';

export function thresholds(
  steps: Array<{ color: string; value: number | null }>,
  mode: dashboard.ThresholdsMode = dashboard.ThresholdsMode.Absolute,
): cog.Builder<dashboard.ThresholdsConfig> {
  return {
    build(): dashboard.ThresholdsConfig {
      return {
        mode,
        steps: steps.map((s) => ({ color: s.color, value: s.value })),
      };
    },
  };
}

export function greenRedThresholds(redAt = 80): cog.Builder<dashboard.ThresholdsConfig> {
  return thresholds([
    { color: 'green', value: null },
    { color: 'red', value: redAt },
  ]);
}

export function greenThreshold(): cog.Builder<dashboard.ThresholdsConfig> {
  return thresholds([{ color: 'green', value: null }]);
}

export function fixedColor(color: string): cog.Builder<dashboard.FieldColor> {
  return {
    build(): dashboard.FieldColor {
      return { fixedColor: color, mode: dashboard.FieldColorModeId.Fixed };
    },
  };
}

export function paletteColor(): cog.Builder<dashboard.FieldColor> {
  return {
    build(): dashboard.FieldColor {
      return { mode: dashboard.FieldColorModeId.PaletteClassic };
    },
  };
}

export function legendBottom(show = true): cog.Builder<{ calcs: string[]; displayMode: string; placement: string; showLegend: boolean }> {
  return {
    build() {
      return {
        calcs: [],
        displayMode: 'list',
        placement: 'bottom',
        showLegend: show,
      };
    },
  };
}

export function tooltipSingle(): cog.Builder<{ mode: string; sort: string; hideZeros: boolean }> {
  return {
    build() {
      return { mode: 'single', sort: 'none', hideZeros: false };
    },
  };
}

export function tooltipMulti(): cog.Builder<{ mode: string; sort: string; hideZeros: boolean }> {
  return {
    build() {
      return { mode: 'multi', sort: 'none', hideZeros: false };
    },
  };
}

export function overrideDisplayName(fieldName: string, displayName: string): {
  matcher: dashboard.MatcherConfig;
  properties: dashboard.DynamicConfigValue[];
} {
  return {
    matcher: { id: 'byName', options: fieldName },
    properties: [{ id: 'displayName', value: displayName }],
  };
}

export function overrideColor(fieldName: string, color: string): {
  matcher: dashboard.MatcherConfig;
  properties: dashboard.DynamicConfigValue[];
} {
  return {
    matcher: { id: 'byName', options: fieldName },
    properties: [
      { id: 'color', value: { fixedColor: color, mode: 'fixed' } },
    ],
  };
}

export function overrideDisplayAndColor(
  fieldName: string,
  displayName: string,
  color: string,
): {
  matcher: dashboard.MatcherConfig;
  properties: dashboard.DynamicConfigValue[];
} {
  return {
    matcher: { id: 'byName', options: fieldName },
    properties: [
      { id: 'displayName', value: displayName },
      { id: 'color', value: { fixedColor: color, mode: 'fixed' } },
    ],
  };
}
