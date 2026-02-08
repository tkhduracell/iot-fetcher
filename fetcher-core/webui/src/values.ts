import { Config } from "./types";

export const values: Config = [
    [
        { measurement: 'ngenic_node_sensor_measurement_value', field: 'temperature_C', 
         filter: {node: 'a84f4c8f-47c5-465d-878e-957c0affb60b'}, title: '🏠 Ngenic Inomhus', unit: '°C' },
        { measurement: 'ngenic_node_sensor_measurement_value', field: 'temperature_C', 
         filter: {node: 'efc2897b-d9d3-41dd-81c6-b376d4bd4996'}, title: '🏡 Ngenic Utomhus', unit: '°C' },
        { measurement: 'air_quality', field: 'aqi', title: '😶‍🌫️ Luftkvalitet', unit: 'AQI⁺', window: '60m', range: '-1h' },
    ],
    [
        { measurement: 'aqua_temp', field: 'temp_incoming', title: '➡️ Pool Ingående', unit: '°C' },
        { measurement: 'aqua_temp', field: 'temp_outgoing', title: '⬅️ Pool Utgående', unit: '°C' },
        { measurement: 'pool_iqpump_motordata', field: 'speed', title: '💦 Poolpump', unit: 'RPM' },
    ],
    [ 
        { measurement: 'tibber', field: 'accumulatedCost', title: 'Dygnskostnad', unit: 'Kr', decimals: 0 },
        { measurement: 'tibber', field: 'accumulatedConsumption', title: 'Dygnskonsumtion', unit: 'KWh', decimals: 0 },
        { measurement: 'sigenergy_battery', field: 'soc_percent', title: '🔋 Batteri SOC', unit: '%', decimals: 0, reload: 60000 },
    ],
    [
        { measurement: 'sigenergy_pv_power', field: 'power_kw', title: '☀️ Solceller Produktion', unit: 'kW', decimals: 1, reload: 10000 },
        { measurement: 'sigenergy_grid_power', field: 'net_power_kw', title: '⚡️ Nät Inköp', unit: 'kW', decimals: 1, reload: 10000 },
        { measurement: 'sigenergy_battery', field: 'power_to_battery_kw', title: '🪫 Batteri Urladdning', unit: 'kW', decimals: 1, reload: 10000 },
    ],
];
