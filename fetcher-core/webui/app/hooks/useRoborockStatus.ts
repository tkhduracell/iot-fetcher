'use client';

import { useEffect, useState } from 'react';
import { RoborockStatus } from '../lib/roborock';

const POLL_INTERVAL_MS = 5000;

export type RoborockStatusFeed = {
  status: RoborockStatus | null;
  /**
   * True once a poll has failed and no poll has succeeded since. The last known
   * `status` is deliberately kept on screen, so without this flag a frozen
   * reading is indistinguishable from a live one — which is exactly what
   * happens when the Roborock integration itself is down.
   */
  stale: boolean;
};

/** Polls vacuum status while `enabled`, and stops as soon as it goes false. */
export function useRoborockStatus(enabled: boolean): RoborockStatusFeed {
  const [status, setStatus] = useState<RoborockStatus | null>(null);
  const [stale, setStale] = useState(false);

  useEffect(() => {
    if (!enabled) {
      setStatus(null);
      setStale(false);
      return;
    }

    let cancelled = false;

    const tick = async () => {
      try {
        const resp = await fetch('/api/roborock/status', { cache: 'no-store' });
        if (!resp.ok) {
          if (!cancelled) setStale(true);
          return;
        }
        const data = await resp.json();
        if (!cancelled) {
          setStatus(data);
          setStale(false);
        }
      } catch {
        // Transient failure: keep showing the last known status, but mark it.
        if (!cancelled) setStale(true);
      }
    };

    tick();
    const timer = setInterval(tick, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enabled]);

  return { status, stale };
}
