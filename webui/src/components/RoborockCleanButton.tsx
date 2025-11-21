import React, { useState, useRef, useEffect } from 'react';
import { useRoborockZones } from '../hooks/useRoborockZones';
import { RoborockZone } from '../types';

const RoborockCleanButton: React.FC = () => {
  const [isOpen, setIsOpen] = useState(false);
  const [isCleaning, setIsCleaning] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const { data: zones, isLoading, error } = useRoborockZones();

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  const handleClean = async (zone: RoborockZone) => {
    setIsCleaning(true);
    setIsOpen(false);

    try {
      const response = await fetch(
        `/roborock/${zone.device_id}/${zone.map_flag}/${zone.zone_id}/clean`,
        { method: 'POST' }
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || 'Failed to start cleaning');
      }

      const result = await response.json();
      console.log('Clean started:', result);

      // Optional: Show success notification
      // You could add a toast/notification system here
    } catch (err) {
      console.error('Failed to start cleaning:', err);
      alert(`Failed to start cleaning: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setIsCleaning(false);
    }
  };

  // Group zones by map
  const zonesByMap = zones?.reduce((acc, zone) => {
    if (!acc[zone.map_name]) {
      acc[zone.map_name] = [];
    }
    acc[zone.map_name].push(zone);
    return acc;
  }, {} as Record<string, RoborockZone[]>);

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        disabled={isCleaning || isLoading}
        title="Start Roborock cleaning"
        className="bg-green-600 hover:bg-green-700 disabled:bg-gray-400 text-white px-3 py-1 rounded-full shadow text-sm font-semibold cursor-pointer transition-colors"
      >
        {isCleaning ? 'Starting...' : 'Clean'}
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-2 w-64 bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 z-50 max-h-96 overflow-y-auto">
          {isLoading && (
            <div className="p-4 text-center text-gray-600 dark:text-gray-400">
              Loading zones...
            </div>
          )}

          {error && (
            <div className="p-4 text-center text-red-600 dark:text-red-400">
              Failed to load zones
            </div>
          )}

          {zones && zones.length === 0 && (
            <div className="p-4 text-center text-gray-600 dark:text-gray-400">
              No zones configured
            </div>
          )}

          {zonesByMap && Object.keys(zonesByMap).map((mapName) => (
            <div key={mapName} className="border-b border-gray-200 dark:border-gray-700 last:border-b-0">
              <div className="px-4 py-2 bg-gray-50 dark:bg-gray-750 font-semibold text-xs text-gray-700 dark:text-gray-300 uppercase tracking-wide">
                {mapName}
              </div>
              {zonesByMap[mapName].map((zone) => (
                <button
                  key={`${zone.device_id}-${zone.zone_id}`}
                  onClick={() => handleClean(zone)}
                  className="w-full text-left px-4 py-3 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors flex items-center justify-between group"
                >
                  <div className="flex flex-col">
                    <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      {zone.zone_name}
                    </span>
                    <span className="text-xs text-gray-500 dark:text-gray-400">
                      Segment {zone.segment_id}
                    </span>
                  </div>
                  <svg
                    className="w-4 h-4 text-gray-400 group-hover:text-green-600 dark:group-hover:text-green-400 transition-colors"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M9 5l7 7-7 7"
                    />
                  </svg>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default RoborockCleanButton;
