import { Config } from "./types";

export const values: Config = [
    [
        { measurement: 'ngenic_node_sensor_measurement_value', field: 'temperature_C', 
         filter: {node: 'a84f4c8f-47c5-465d-878e-957c0affb60b'}, title: 'Ngenic Inomhus', unit: '°C' },
        { measurement: 'ngenic_node_sensor_measurement_value', field: 'temperature_C', 
         filter: {node: 'efc2897b-d9d3-41dd-81c6-b376d4bd4996'}, title: 'Ngenic Utomhus', unit: '°C' },
        { measurement: 'ngenic_node_sensor_measurement_value', field: 'target_temperature_C', 
         filter: {node: 'a84f4c8f-47c5-465d-878e-957c0affb60b'}, title: 'Ngenic Mål', unit: '°C' },
    ],
    [
        { measurement: 'aqua_temp', field: 'temp_incoming', title: 'Pool Ingående', unit: '°C' },
        { measurement: 'aqua_temp', field: 'temp_outgoing', title: 'Pool Utgående', unit: '°C' },
        { measurement: 'aqua_temp', field: 'temp_target', title: 'Pool Måltemp', unit: '°C' },
    ],
    [ 
        { measurement: 'tibber', field: 'accumulatedCost', title: 'Dygnskostnad', unit: 'Kr', decimals: 0 },
        { measurement: 'tibber', field: 'accumulatedConsumption', title: 'Dygnskonsumtion', unit: 'KWh', decimals: 0 },
        { measurement: 'tibber', field: 'power', title: 'Effekt', unit: 'W', decimals: 0, reload: 10000 }
    ]
];
