import React from 'react';
import LatestValue from './components/LatestValue';
import HealthBadge from './components/HealthBadge';
import RefreshBadge from './components/RefreshBadge';

const App: React.FC = () => {
    return (
        <div className="min-h-screen bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 transition-colors duration-300 relative p-4">
            {/* Top right badges */}
            <div className="absolute top-4 right-4 flex items-center z-10">
                <RefreshBadge />
                <HealthBadge />
            </div>
            <div className="container mx-auto py-8 flex flex-col gap-4">
                <h1 className="text-3xl font-bold mb-4">Hello Irisgatan</h1>
                {/* LatestValue components in a single row */}
                <div className="flex flex-row gap-4 min-h-[20vh]">
                    <div className="flex-1">
                        <LatestValue
                            measurement="ngenic_node_sensor_measurement_value"
                            field="temperature_C"
                            filter={{node: "a84f4c8f-47c5-465d-878e-957c0affb60b"}}
                            title="Ngenic Inomhus"
                        />
                    </div>
                    <div className="flex-1">
                        <LatestValue
                            measurement="ngenic_node_sensor_measurement_value"
                            field="temperature_C"
                            filter={{node: "efc2897b-d9d3-41dd-81c6-b376d4bd4996"}}
                            title="Ngenic Utomhus"
                        />
                    </div>
                    <div className="flex-1">
                        <LatestValue
                            measurement="ngenic_node_sensor_measurement_value"
                            field="target_temperature_C"
                            filter={{node: "a84f4c8f-47c5-465d-878e-957c0affb60b"}}
                            title="Ngenic Mål"
                        />
                    </div>
                </div>
                <div className="flex flex-row gap-4 min-h-[20vh]">
                    <div className="flex-1">
                        <LatestValue
                            measurement="aqua_temp"
                            field="temp_incoming"
                            title="Pool Ingående"
                        />
                    </div>
                    <div className="flex-1">
                        <LatestValue
                            measurement="aqua_temp"
                            field="temp_outgoing"
                            title="Pool Utgående"
                        />
                    </div>
                    <div className="flex-1">
                        <LatestValue
                            measurement="aqua_temp"
                            field="temp_target"
                            title="Pool Måltemp"
                        />
                    </div>
                </div>
                {/* Add more grid items as needed */}
            </div>
        </div>
    );
};

export default App;