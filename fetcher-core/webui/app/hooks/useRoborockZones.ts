import { useState, useEffect } from 'react';
import { RoborockZone } from '../lib/types';

type UseRoborockZonesResult = {
  data: RoborockZone[] | undefined;
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
};

const CACHE_KEY = 'roborock-zones-cache';
const CACHE_DURATION = 1000 * 60 * 60; // 1 hour

const getCache = (): { data: RoborockZone[]; timestamp: number } | null => {
  try {
    const cached = localStorage.getItem(CACHE_KEY);
    if (!cached) return null;

    const parsed = JSON.parse(cached);
    const age = Date.now() - parsed.timestamp;

    if (age < CACHE_DURATION) {
      return parsed;
    }

    localStorage.removeItem(CACHE_KEY);
    return null;
  } catch {
    return null;
  }
};

const setCache = (data: RoborockZone[]) => {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({
      data,
      timestamp: Date.now(),
    }));
  } catch {
    // Ignore cache errors
  }
};

const fetchRoborockZones = async (): Promise<RoborockZone[]> => {
  const response = await fetch('/roborock/zones');

  if (!response.ok) {
    throw new Error('Failed to fetch Roborock zones');
  }

  return response.json();
};

export const useRoborockZones = (): UseRoborockZonesResult => {
  const [data, setData] = useState<RoborockZone[] | undefined>(undefined);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [shouldFetch, setShouldFetch] = useState(0);

  useEffect(() => {
    let isMounted = true;

    const loadZones = async () => {
      const cached = getCache();
      if (cached && isMounted) {
        setData(cached.data);
        setIsLoading(false);
        return;
      }

      setIsLoading(true);
      setError(null);

      try {
        const zones = await fetchRoborockZones();

        if (isMounted) {
          setData(zones);
          setCache(zones);
          setError(null);
        }
      } catch (err) {
        if (isMounted) {
          setError(err instanceof Error ? err : new Error('Unknown error'));
        }
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    };

    loadZones();

    return () => {
      isMounted = false;
    };
  }, [shouldFetch]);

  const refetch = () => {
    setShouldFetch(prev => prev + 1);
  };

  return { data, isLoading, error, refetch };
};
