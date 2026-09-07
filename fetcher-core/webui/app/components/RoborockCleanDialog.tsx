'use client';

import React, { useEffect, useState } from 'react';
import {
  RoborockTarget,
  RoborockTargets,
  StartOutcome,
  classifyStart,
  START_CONFIRM_DELAY_MS,
} from '../lib/roborock';
import { useRoborockStatus } from '../hooks/useRoborockStatus';

type Props = {
  targets: RoborockTargets;
  onClose: () => void;
};

const RoborockCleanDialog: React.FC<Props> = ({ targets, onClose }) => {
  const { status, stale } = useRoborockStatus(true);
  const [pending, setPending] = useState<string | null>(null);
  const [requested, setRequested] = useState<string | null>(null);
  const [requestedAt, setRequestedAt] = useState<number | null>(null);
  const [outcome, setOutcome] = useState<StartOutcome>('pending');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [onClose]);

  // A 200 from the trigger route only proves Home Assistant accepted the call,
  // not that the vacuum moved — confirm against status a little later.
  //
  // This runs on its own timer rather than keying off `status`. When the
  // Roborock integration fails, the status route 503s and the hook stops
  // publishing new objects, so a status-keyed effect would never re-run and the
  // warning would be suppressed in exactly the case it exists for.
  useEffect(() => {
    if (requestedAt === null) return;

    const evaluate = () =>
      setOutcome(
        classifyStart({ requestedAt, now: Date.now(), state: status?.state, stale })
      );

    evaluate();
    const timer = setInterval(evaluate, 1000);
    return () => clearInterval(timer);
  }, [requestedAt, status, stale]);

  const start = async (target: RoborockTarget) => {
    setPending(target.entity_id);
    setError(null);
    setRequested(null);
    setRequestedAt(null);
    setOutcome('pending');
    try {
      const resp = await fetch('/api/roborock/trigger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entity_id: target.entity_id }),
      });
      if (!resp.ok) throw new Error(`Could not start ${target.name}`);
      setRequested(target.name);
      setRequestedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    } finally {
      setPending(null);
    }
  };

  const dock = async () => {
    setError(null);
    setRequestedAt(null);
    setOutcome('pending');
    try {
      const resp = await fetch('/api/roborock/dock', { method: 'POST' });
      if (!resp.ok) throw new Error('Could not send the vacuum back to its dock');
      setRequested('Return to dock');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    }
  };

  const hasTargets = targets.floors.length > 0 || targets.rooms.length > 0;

  const renderTarget = (target: RoborockTarget, large: boolean) => (
    <button
      key={target.entity_id}
      onClick={() => start(target)}
      disabled={pending !== null}
      className={[
        'rounded-xl font-semibold text-white transition-colors disabled:opacity-50 min-w-0 break-words',
        large
          ? 'bg-green-700 hover:bg-green-600 px-4 py-6 text-lg sm:px-6 sm:text-xl'
          : 'bg-gray-700 hover:bg-gray-600 px-3 py-5 text-base sm:px-4 sm:text-lg',
      ].join(' ')}
    >
      {pending === target.entity_id ? 'Starting…' : target.name}
    </button>
  );

  return (
    <div className="fixed inset-0 z-50 bg-gray-900/95 backdrop-blur-sm flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
        <h2 className="text-xl font-semibold text-white">Clean</h2>
        <button
          onClick={onClose}
          className="w-10 h-10 rounded-full bg-gray-700 hover:bg-gray-600 text-white flex items-center justify-center transition-colors"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Targets */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden px-6 py-5">
        {hasTargets ? (
          <>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-3">
              Whole floor
            </h3>
            <div className="grid grid-cols-2 gap-3 mb-8">
              {targets.floors.map(f => renderTarget(f, true))}
            </div>

            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-3">
              Rooms
            </h3>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {targets.rooms.map(r => renderTarget(r, false))}
            </div>
          </>
        ) : (
          <div className="h-full flex flex-col items-center justify-center text-center gap-2">
            <span className="text-gray-300">No cleaning targets available</span>
            <span className="text-sm text-gray-500 max-w-md">
              Home Assistant reported no labelled automations. If it is still starting
              up this resolves on its own — the list refreshes automatically.
            </span>
          </div>
        )}
      </div>

      {/* Status footer */}
      <div className="flex items-center justify-between gap-4 px-6 py-4 border-t border-gray-700">
        <div className="text-sm text-gray-300 flex items-center gap-3 min-w-0">
          {status ? (
            <>
              <span className={stale ? 'capitalize text-gray-500' : 'capitalize'}>
                {status.status}
              </span>
              {status.battery !== null && (
                <span className={stale ? 'text-gray-500' : undefined}>{status.battery}%</span>
              )}
              {status.room && <span className="truncate">in {status.room}</span>}
              {status.error && <span className="text-red-400">{status.error}</span>}
              {stale && <span className="text-yellow-400 truncate">status not updating</span>}
            </>
          ) : stale ? (
            <span className="text-yellow-400">Status unavailable</span>
          ) : (
            <span className="text-gray-500">Loading status…</span>
          )}
          {requested && outcome === 'not-started' && (
            <span className="text-yellow-400 truncate">
              {requested} hasn&apos;t started — check Home Assistant, or Do Not Disturb may be
              active
            </span>
          )}
          {requested && outcome === 'unknown' && (
            <span className="text-yellow-400 truncate">
              {requested} requested — can&apos;t confirm it started
            </span>
          )}
          {requested && outcome !== 'not-started' && outcome !== 'unknown' && (
            <span className="text-green-400 truncate">
              {outcome === 'started' ? `${requested} started` : `${requested} requested`}
            </span>
          )}
          {error && <span className="text-red-400 truncate">{error}</span>}
        </div>
        <button
          onClick={dock}
          className="shrink-0 px-4 py-2 rounded-full bg-gray-700 hover:bg-gray-600 text-white text-sm font-semibold transition-colors"
        >
          Return to dock
        </button>
      </div>
    </div>
  );
};

export default RoborockCleanDialog;
