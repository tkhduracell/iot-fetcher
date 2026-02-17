import { useState, useEffect } from 'react';
import { queryJitterInterval } from '../lib/globals';
import { SonosZone } from '../lib/types';

type UseSonosQueryResult = {
  initialLoading: boolean;
  loading: boolean;
  error: Error | null;
  result: SonosZone[];
};

const useSonosQuery = (): UseSonosQueryResult & {
  updateZoneMute: (zoneUuid: string, muted: boolean) => void;
} => {
  const [initialLoading, setInitialLoading] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [result, setResult] = useState<SonosZone[]>([]);

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
      setError(err instanceof Error ? err : new Error('Unknown error'));
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
  }, []);

  return {
    initialLoading,
    loading,
    error,
    result,
    updateZoneMute,
  };
};

export default useSonosQuery;
