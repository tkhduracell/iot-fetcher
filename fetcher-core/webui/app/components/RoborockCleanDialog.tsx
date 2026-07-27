'use client';

import React, { useEffect, useRef, useState } from 'react';
import { RoborockTarget, RoborockTargets } from '../lib/roborock';
import { useRoborockStatus } from '../hooks/useRoborockStatus';

type Props = {
  targets: RoborockTargets;
  onClose: () => void;
};

// Vacuum states that mean "hasn't actually gone off to clean". If the
// vacuum is still in one of these ~15s after we asked it to start, the most
// likely reason is its Do Not Disturb window (22:00-08:00) silently
// refusing the request.
const NOT_MOVING_STATES = new Set(['docked', 'idle', 'charging']);
const START_CONFIRM_DELAY_MS = 15000;

const RoborockCleanDialog: React.FC<Props> = ({ targets, onClose }) => {
  const status = useRoborockStatus(true);
  const [pending, setPending] = useState<string | null>(null);
  const [requested, setRequested] = useState<string | null>(null);
  const [notStarted, setNotStarted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pendingCheck = useRef<{ at: number } | null>(null);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [onClose]);

  // A 200 from the trigger route only proves Home Assistant accepted the
  // call, not that the vacuum moved — check status a little later before
  // trusting it.
  useEffect(() => {
    if (!pendingCheck.current || !status) return;
    if (Date.now() - pendingCheck.current.at < START_CONFIRM_DELAY_MS) return;
    if (NOT_MOVING_STATES.has(status.state)) setNotStarted(true);
    pendingCheck.current = null;
  }, [status]);

  const start = async (target: RoborockTarget) => {
    setPending(target.entity_id);
    setError(null);
    setRequested(null);
    setNotStarted(false);
    pendingCheck.current = null;
    try {
      const resp = await fetch('/api/roborock/trigger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entity_id: target.entity_id }),
      });
      if (!resp.ok) throw new Error(`Could not start ${target.name}`);
      setRequested(target.name);
      pendingCheck.current = { at: Date.now() };
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    } finally {
      setPending(null);
    }
  };

  const dock = async () => {
    setError(null);
    setNotStarted(false);
    pendingCheck.current = null;
    try {
      const resp = await fetch('/api/roborock/dock', { method: 'POST' });
      if (!resp.ok) throw new Error('Could not send the vacuum back to its dock');
      setRequested('Return to dock');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    }
  };

  const renderTarget = (target: RoborockTarget, large: boolean) => (
    <button
      key={target.entity_id}
      onClick={() => start(target)}
      disabled={pending !== null}
      className={[
        'rounded-xl font-semibold text-white transition-colors disabled:opacity-50',
        large
          ? 'bg-green-700 hover:bg-green-600 px-6 py-6 text-xl'
          : 'bg-gray-700 hover:bg-gray-600 px-4 py-5 text-lg',
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
      <div className="flex-1 overflow-y-auto px-6 py-5">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-3">
          Whole floor
        </h3>
        <div className="grid grid-cols-2 gap-3 mb-8">
          {targets.floors.map(f => renderTarget(f, true))}
        </div>

        <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-3">
          Rooms
        </h3>
        <div className="grid grid-cols-3 gap-3">
          {targets.rooms.map(r => renderTarget(r, false))}
        </div>
      </div>

      {/* Status footer */}
      <div className="flex items-center justify-between gap-4 px-6 py-4 border-t border-gray-700">
        <div className="text-sm text-gray-300 flex items-center gap-3 min-w-0">
          {status ? (
            <>
              <span className="capitalize">{status.status}</span>
              {status.battery !== null && <span>{status.battery}%</span>}
              {status.room && <span className="truncate">in {status.room}</span>}
              {status.error && <span className="text-red-400">{status.error}</span>}
            </>
          ) : (
            <span className="text-gray-500">Loading status…</span>
          )}
          {requested && notStarted && (
            <span className="text-yellow-400 truncate">
              {requested} hasn&apos;t started — Do Not Disturb may be active
            </span>
          )}
          {requested && !notStarted && (
            <span className="text-green-400 truncate">{requested} requested</span>
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
