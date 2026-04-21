import { Config } from "./types";

export const values: Config = [
    [
        { measurement: 'ngenic_node_sensor_measurement_value', field: 'temperature_C',
         filter: {node: 'a84f4c8f-47c5-465d-878e-957c0affb60b'}, title: '🏠 Ngenic Inomhus', unit: '°C', sparkline: '24h', sparklineMin: 18, sparklineMax: 24 },
        { measurement: 'ngenic_node_sensor_measurement_value', field: 'temperature_C',
         filter: {node: 'efc2897b-d9d3-41dd-81c6-b376d4bd4996'}, title: '🏡 Ngenic Utomhus', unit: '°C', sparkline: '24h', sparklineMin: -10, sparklineMax: 30 },
        { measurement: 'air_quality', field: 'aqi', title: '😶‍🌫️ Luftkvalitet', unit: 'AQI⁺', window: '60m', range: '-1h', sparkline: '24h', sparklineMin: 0, sparklineMax: 150 },
    ],
    [
        { measurement: 'spa_climate', field: 'current_temperature_value', title: '🛁 Spa Temperatur', unit: '°C', sparkline: '24h', sparklineMin: 0, sparklineMax: 45 },
        { measurement: 'pool_temperature', field: 'value', title: '🏊 Pool Temperatur', unit: '°C', sparkline: '24h', sparklineMin: 0, sparklineMax: 30 },
        { measurement: 'pool_iqpump_motordata', field: 'speed', title: '💦 Poolpump', unit: 'RPM', sparkline: '24h', sparklineMin: 0, sparklineMax: 3000 },
    ],
    [
        { measurement: 'tibber', field: 'accumulatedCost', title: 'Dygnskostnad', unit: 'Kr', decimals: 0, sparkline: '24h', sparklineMin: 0 },
        { measurement: 'tibber', field: 'accumulatedConsumption', title: 'Dygnskonsumtion', unit: 'KWh', decimals: 0, sparkline: '24h', sparklineMin: 0 },
        { measurement: 'ha_volvo_xc40_battery', field: 'value', title: '🚗 XC40 Batteri', unit: '%', decimals: 0, reload: 60000, sparkline: '24h', sparklineMin: 0, sparklineMax: 100 },
        { measurement: 'ha_volvo_xc40_charging_power', field: 'value', title: '🔌 XC40 Laddning', unit: 'kW', decimals: 1, reload: 60000, sparkline: '24h', sparklineMin: 0 },
    ],
    [
        { measurement: 'sigenergy_pv_power', field: 'power_kw', title: '☀️ Solceller Produktion', unit: 'kW', decimals: 1, reload: 10000, sparkline: '24h', sparklineMin: 0, sparklineMax: 3 },
        { measurement: 'sigenergy_grid_power', field: 'net_power_kw', title: '⚡️ Nät Inköp', unit: 'kW', decimals: 1, reload: 10000, sparkline: '24h', sparklineMin: -5, sparklineMax: 10 },
        { measurement: 'sigenergy_battery', field: 'soc_percent', title: '🔋 Batteri SOC', unit: '%', decimals: 0, reload: 60000, sparkline: '24h', sparklineMin: 0, sparklineMax: 100 },
        { measurement: 'sigenergy_battery', field: 'power_from_battery_kw',
         expr: 'clamp_min(sigenergy_battery_power_from_battery_kw, 0)',
         title: '🪫 Batteri Urladdning', unit: 'kW', decimals: 1, reload: 10000, sparkline: '24h', sparklineMin: 0, sparklineMax: 10 },
        { measurement: 'sigenergy_battery', field: 'power_from_battery_kw',
         expr: 'clamp_min(-sigenergy_battery_power_from_battery_kw, 0)',
         title: '🔋 Batteri Laddning', unit: 'kW', decimals: 1, reload: 10000, sparkline: '24h', sparklineMin: 0, sparklineMax: 10 },
    ],
];
