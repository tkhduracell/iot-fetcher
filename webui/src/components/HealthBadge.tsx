import React, { useEffect, useState } from 'react';

// Helper to detect if error is due to missing InfluxDB configuration
function isConfigurationError(errorText: string): boolean {
  return errorText.includes('Missing INFLUX_HOST or INFLUX_TOKEN') || 
         errorText.includes('500');
}

const HealthBadge: React.FC = () => {
    const [health, setHealth] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [unavailable, setUnavailable] = useState(false);

    useEffect(() => {
        fetch('/influx/api/v2/health')
            .then(async res => {
                if (!res.ok) {
                    const text = await res.text();
                    if (isConfigurationError(text)) {
                        setUnavailable(true);
                        setLoading(false);
                        return null;
                    }
                    throw new Error('Network response was not ok');
                }
                return res.json();
            })
            .then(data => {
                if (data) {
                    setHealth(data);
                    setLoading(false);
                }
            })
            .catch(err => {
                setError(err.message);
                setLoading(false);
            });
    }, []);

    if (loading) return null;
    if (unavailable) return (
        <div className="bg-gray-500 text-white px-3 py-1 rounded-full shadow text-sm font-semibold ml-2">Demo Mode</div>
    );
    if (error || !health) return (
        <div className="bg-red-600 text-white px-3 py-1 rounded-full shadow text-sm font-semibold ml-2">Health: Error</div>
    );
    return (
        <div className={`px-3 py-1 rounded-full shadow text-sm font-semibold ml-2 ${health.status === 'pass' ? 'bg-green-600 text-white' : 'bg-yellow-600 text-white'}`}>
            {health.status === 'pass' ? 'Healthy' : health.status} {health.version}
        </div>
    );
};

export default HealthBadge;
