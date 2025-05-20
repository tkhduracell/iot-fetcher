// Remove the values array, only export types
export type Config = readonly ConfigRow[];
export type ConfigRow = readonly ConfigValue[];
export type ConfigValue = {
  measurement: string;
  field: string;
  filter?: Record<string, any>;
  title: string;
  unit: string;
  decimals?: number;
};
