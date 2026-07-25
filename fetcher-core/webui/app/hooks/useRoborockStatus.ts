'use client';

import { useEffect, useState } from 'react';
import { RoborockStatus } from '../lib/roborock';

const POLL_INTERVAL_MS = 5000;

/** Polls vacuum status while `enabled`, and stops as soon as it goes false. */
export function useRoborockStatus(enabled: boolean): RoborockStatus | null {
  const [status, setStatus] = useState<RoborockStatus | null>(null);

  useEffect(() => {
    if (!enabled) {
      setStatus(null);
      return;
    }

    let cancelled = false;

    const tick = async () => {
      try {
        const resp = await fetch('/api/roborock/status', { cache: 'no-store' });
        if (!resp.ok) return;
        const data = await resp.json();
        if (!cancelled) setStatus(data);
      } catch {
        // Transient failure: keep showing the last known status.
      }
    };

    tick();
    const timer = setInterval(tick, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enabled]);

  return status;
}
