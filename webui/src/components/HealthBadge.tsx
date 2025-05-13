import React, { useEffect, useState } from 'react';

const HealthBadge: React.FC = () => {
    const [health, setHealth] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        fetch('/influx/api/v2/health')
            .then(res => {
                if (!res.ok) throw new Error('Network response was not ok');
                return res.json();
            })
            .then(data => {
                setHealth(data);
                setLoading(false);
            })
            .catch(err => {
                setError(err.message);
                setLoading(false);
            });
    }, []);

    if (loading) return null;
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
