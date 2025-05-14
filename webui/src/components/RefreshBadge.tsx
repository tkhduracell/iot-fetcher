import React from 'react';
import { globals } from './globals';

const RefreshBadge: React.FC = () => {
    const refreshSec = Math.round(globals.queryReloadInterval / 1000);
    return (
        <div className="bg-blue-600 text-white px-3 py-1 rounded-full shadow text-sm font-semibold">
            Refresh: {refreshSec} seconds
        </div>
    );
};

export default RefreshBadge;
