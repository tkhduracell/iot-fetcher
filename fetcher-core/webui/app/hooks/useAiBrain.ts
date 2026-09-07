import { useCallback, useEffect, useRef, useState } from 'react';

/** Polls an ai-brain endpoint on a fixed interval.
 *
 *  Unlike `usePromQLQuery` this keeps the last good payload when a poll fails —
 *  a brief ai-brain restart should dim the page, not blank it — and skips the
 *  startup jitter, since a single dashboard makes one small request per tick.
 */
export function useAiBrain<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
  intervalMs: number = 10_000,
  enabled: boolean = true,
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [tick, setTick] = useState(0);

  // Held in a ref so a re-render with a fresh closure does not restart polling;
  // deps are the explicit re-run signal.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!enabled) {
      setInitialLoading(false);
      return;
    }

    let cancelled = false;
    const controller = new AbortController();

    const run = async () => {
      setLoading(true);
      try {
        const result = await fetcherRef.current(controller.signal);
        if (cancelled) return;
        setData(result);
        setError(null);
      } catch (e) {
        if (cancelled || controller.signal.aborted) return;
        // Keep `data` — the card stays readable behind the error banner.
        setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (!cancelled) {
          setLoading(false);
          setInitialLoading(false);
        }
      }
    };

    run();
    const intervalId = setInterval(run, intervalMs);

    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(intervalId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs, enabled, tick]);

  return { data, error, initialLoading, loading, refresh };
}

export default useAiBrain;
