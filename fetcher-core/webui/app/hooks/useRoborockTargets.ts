'use client';

import { useCallback, useEffect, useState } from 'react';
import { RoborockTargets } from '../lib/roborock';

const EMPTY: RoborockTargets = { floors: [], rooms: [] };

// How often to quietly retry in the background while targets are empty and
// the last attempt failed (HA unreachable). This polls a home server 24/7,
// so keep it slow — self-healing, not hammering.
const RETRY_INTERVAL_MS = 30000;

/**
 * Fetches the labelled clean targets on mount, so the button knows whether to
 * render at all, and exposes refetch for when the dialog opens.
 *
 * A failed fetch never discards an already-known target set — the button
 * must not vanish because of a transient Home Assistant blip. When targets
 * are still empty and the last attempt failed, it retries quietly in the
 * background so the dashboard self-heals without a manual reload.
 */
export function useRoborockTargets() {
  const [targets, setTargets] = useState<RoborockTargets>(EMPTY);
  const [isLoading, setIsLoading] = useState(true);
  const [lastFetchFailed, setLastFetchFailed] = useState(false);

  const refetch = useCallback(async () => {
    try {
      const resp = await fetch('/api/roborock/targets', { cache: 'no-store' });
      if (!resp.ok) throw new Error(`targets returned ${resp.status}`);
      const data = await resp.json();
      setTargets(data);
      setLastFetchFailed(false);
    } catch (e) {
      // Transient failure: keep showing the last known targets so the
      // button doesn't disappear mid-use.
      console.error('Failed to load Roborock targets:', e);
      setLastFetchFailed(true);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  useEffect(() => {
    const isEmpty = targets.floors.length === 0 && targets.rooms.length === 0;
    if (!isEmpty || !lastFetchFailed) return;

    const timer = setInterval(refetch, RETRY_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [targets, lastFetchFailed, refetch]);

  return { targets, isLoading, refetch };
}
