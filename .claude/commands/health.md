---
description: Check metrics freshness — queries VictoriaMetrics and reports stale/missing metrics
allowed-tools: Bash, Read
---

## Your Task

Check the freshness of all key metrics in VictoriaMetrics and report which ones have recent data and which are stale or missing.

### Step 1: Query each metric

Use `./scripts/vm-query.sh query` to check each metric with `last_over_time(...[15m])`.

**Metrics to check:**

| Metric name | Label filter | Description |
|---|---|---|
| `ngenic_node_sensor_measurement_value_temperature_C` | `node="a84f4c8f-47c5-465d-878e-957c0affb60b"` | Ngenic Inomhus |
| `ngenic_node_sensor_measurement_value_temperature_C` | `node="efc2897b-d9d3-41dd-81c6-b376d4bd4996"` | Ngenic Utomhus |
| `air_quality_aqi` | _(none)_ | Luftkvalitet |
| `aqua_temp_temp_incoming` | _(none)_ | Pool Ingaende |
| `aqua_temp_temp_outgoing` | _(none)_ | Pool Utgaende |
| `pool_iqpump_motordata_speed` | _(none)_ | Poolpump |
| `tibber_accumulatedCost` | _(none)_ | Dygnskostnad |
| `tibber_accumulatedConsumption` | _(none)_ | Dygnskonsumtion |
| `sigenergy_battery_soc_percent` | _(none)_ | Batteri SOC |
| `sigenergy_battery_power_to_battery_kw` | _(none)_ | Batteri Urladdning |
| `sigenergy_pv_power_power_kw` | _(none)_ | Solceller Produktion |
| `sigenergy_grid_power_net_power_kw` | _(none)_ | Nat Inkop |

**Query pattern** — run all in a single bash script for efficiency:

```bash
./scripts/vm-query.sh query 'last_over_time(METRIC_NAME{LABEL_FILTER}[15m])'
```

For each metric, extract from the JSON result:
- The **timestamp** from `.data.result[0].value[0]` (unix epoch)
- The **value** from `.data.result[0].value[1]`
- Calculate the **age** as `now - timestamp` in minutes

### Step 2: Report results

Present a summary table like this:

```
| Metric                        | Description          | Value    | Age   | Status  |
|-------------------------------|----------------------|----------|-------|---------|
| ngenic_..._temperature_C      | Ngenic Inomhus       | 22.3     | 2m    | OK      |
| air_quality_aqi               | Luftkvalitet         | -        | -     | MISSING |
| tibber_accumulatedCost        | Dygnskostnad         | 45.2     | 18m   | STALE   |
```

**Status rules:**
- **OK**: Data exists and age < 15 minutes
- **STALE**: Data exists but age >= 15 minutes
- **MISSING**: No data returned at all

At the end, print a one-line summary: `X/12 metrics OK, Y stale, Z missing`

If any metrics are STALE or MISSING, suggest checking the relevant fetcher container logs on rpi5.
