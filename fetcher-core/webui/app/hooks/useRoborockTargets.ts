'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  RoborockTargets,
  RoborockTargetsResponse,
  shouldRetryTargets,
} from '../lib/roborock';

const EMPTY: RoborockTargets = { floors: [], rooms: [] };

// How often to quietly retry in the background while we have no usable targets.
// This polls a home server 24/7, so keep it slow — self-healing, not hammering.
const RETRY_INTERVAL_MS = 30000;

/**
 * Fetches the labelled clean targets on mount, so the button knows whether to
 * render at all, and exposes refetch for when the dialog opens.
 *
 * A failed fetch never discards an already-known target set — the button must
 * not vanish because of a transient Home Assistant blip. Retries continue in the
 * background until targets are known, which covers the reboot case where HA
 * answers successfully but has not registered its Roborock entities yet.
 */
export function useRoborockTargets() {
  const [targets, setTargets] = useState<RoborockTargets>(EMPTY);
  const [configured, setConfigured] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [lastFetchFailed, setLastFetchFailed] = useState(false);

  const refetch = useCallback(async () => {
    try {
      const resp = await fetch('/api/roborock/targets', { cache: 'no-store' });
      if (!resp.ok) throw new Error(`targets returned ${resp.status}`);
      const data: RoborockTargetsResponse = await resp.json();
      setTargets({ floors: data.floors ?? [], rooms: data.rooms ?? [] });
      setConfigured(Boolean(data.configured));
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

  const isEmpty = targets.floors.length === 0 && targets.rooms.length === 0;

  // Depend on the derived booleans rather than the `targets` object: a poll that
  // returns the same content still yields a new object, which would otherwise
  // tear down and rebuild the interval on every tick.
  useEffect(() => {
    if (!shouldRetryTargets({ configured, isEmpty, lastFetchFailed })) return;

    const timer = setInterval(refetch, RETRY_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [configured, isEmpty, lastFetchFailed, refetch]);

  return { targets, isEmpty, isLoading, refetch };
}
