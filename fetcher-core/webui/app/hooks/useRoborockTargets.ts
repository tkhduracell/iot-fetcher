'use client';

import { useCallback, useEffect, useState } from 'react';
import { RoborockTargets } from '../lib/roborock';

const EMPTY: RoborockTargets = { floors: [], rooms: [] };

/**
 * Fetches the labelled clean targets on mount, so the button knows whether to
 * render at all, and exposes refetch for when the dialog opens.
 */
export function useRoborockTargets() {
  const [targets, setTargets] = useState<RoborockTargets>(EMPTY);
  const [isLoading, setIsLoading] = useState(true);

  const refetch = useCallback(async () => {
    try {
      const resp = await fetch('/api/roborock/targets', { cache: 'no-store' });
      if (!resp.ok) throw new Error(`targets returned ${resp.status}`);
      setTargets(await resp.json());
    } catch (e) {
      console.error('Failed to load Roborock targets:', e);
      setTargets(EMPTY);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  return { targets, isLoading, refetch };
}
