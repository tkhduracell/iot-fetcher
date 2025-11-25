import { useState, useEffect } from 'react';
import { queryJitterInterval } from '../globals';
import { SonosZone } from '../types';

// Helper to detect if error is due to missing configuration
function isConfigurationError(error: Error | null): boolean {
  if (!error) return false;
  const message = error.message || String(error);
  return message.includes('Missing SONOS_HOST') || 
         message.includes('status: 500') ||
         message.includes('status: 502');
}

type UseSonosQueryResult = {
  initialLoading: boolean;
  loading: boolean;
  error: Error | null;
  result: SonosZone[];
  unavailable: boolean;
};

const useSonosQuery = (): UseSonosQueryResult & { 
  updateZoneMute: (zoneUuid: string, muted: boolean) => void;
} => {
  const [initialLoading, setInitialLoading] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [result, setResult] = useState<SonosZone[]>([]);
  const [unavailable, setUnavailable] = useState(false);

  const updateZoneMute = (zoneUuid: string, muted: boolean) => {
    setResult(prevResult => 
      prevResult.map(zone => 
        zone.uuid === zoneUuid
          ? {
              ...zone,
              coordinator: {
                ...zone.coordinator,
                state: {
                  ...zone.coordinator.state,
                  mute: muted
                }
              },
              members: zone.members.map(member => 
                member.uuid === zoneUuid
                  ? {
                      ...member,
                      state: {
                        ...member.state,
                        mute: muted
                      }
                    }
                  : member
              )
            }
          : zone
      )
    );
  };

  const fetchSonosZones = async () => {
    // Don't fetch if already marked as unavailable
    if (unavailable) return;
    
    try {
      setLoading(true);
      const response = await fetch('/sonos/zones');
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      const data: SonosZone[] = await response.json();
      setResult(data);
      setError(null);
    } catch (err) {
      const error = err instanceof Error ? err : new Error('Unknown error');
      setError(error);
      if (isConfigurationError(error)) {
        setUnavailable(true);
      }
    } finally {
      setLoading(false);
      setInitialLoading(false);
    }
  };

  useEffect(() => {
    fetchSonosZones();

    const jitter = Math.random() * queryJitterInterval;
    const interval = setInterval(() => {
      fetchSonosZones();
    }, 5000 + jitter);

    return () => clearInterval(interval);
  }, [unavailable]);

  return {
    initialLoading,
    loading,
    error,
    result,
    unavailable,
    updateZoneMute,
  };
};

export default useSonosQuery;