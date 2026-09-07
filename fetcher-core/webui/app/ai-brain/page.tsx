'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import RefreshBadge from '../components/RefreshBadge';
import AiBrainSupervisor, { Card } from '../components/AiBrainSupervisor';
import AiBrainAgentCard from '../components/AiBrainAgentCard';
import AiBrainDetail from '../components/AiBrainDetail';
import useAiBrain from '../hooks/useAiBrain';
import { fetchAgents, fetchStatus } from '../lib/aiBrain';

const POLL_MS = 10_000;

const Skeleton: React.FC = () => (
  <div className="flex flex-col gap-1.5">
    {[0, 1, 2].map((i) => (
      <div
        key={i}
        className="h-24 rounded-md bg-blue-100 dark:bg-blue-900 ring-1 ring-blue-200 dark:ring-blue-800 animate-pulse"
      />
    ))}
  </div>
);

const ErrorCard: React.FC<{ message: string; stale: boolean }> = ({ message, stale }) => (
  <div className="px-3 py-2 rounded-md bg-red-100 dark:bg-red-900 ring-1 ring-red-300 dark:ring-red-800 text-sm text-red-900 dark:text-red-100">
    Kunde inte nå ai-brain: {message}
    {stale && <span className="block text-xs opacity-80">Visar senast kända värden.</span>}
  </div>
);

export default function AiBrainPage() {
  const [selected, setSelected] = useState<string | null>(null);
  // Second-resolution clock so the "nästa om …" countdowns move between polls.
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  // Every timestamp on this page (last_cycle_at, next_wake_at, blocked_until)
  // comes from ai-brain's clock. A browser minutes off would render a cycle
  // that just ran as "5 min sedan", or a wake that already happened as still
  // pending — so the ticker runs on the server's clock, offset by whatever the
  // last /api/status said the difference was. Held in a ref: it must not
  // restart the interval, and the ticker below picks it up on its next tick.
  const offsetRef = useRef(0);

  const statusFetcher = useCallback((signal: AbortSignal) => fetchStatus(signal), []);
  const agentsFetcher = useCallback((signal: AbortSignal) => fetchAgents(signal), []);

  const status = useAiBrain(statusFetcher, [], POLL_MS);
  const agents = useAiBrain(agentsFetcher, [], POLL_MS);

  const serverNow = status.data?.now;
  useEffect(() => {
    if (typeof serverNow !== 'number' || !Number.isFinite(serverNow)) return;
    offsetRef.current = serverNow - Date.now() / 1000;
    setNow(Date.now() / 1000 + offsetRef.current);
  }, [serverNow]);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000 + offsetRef.current), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (agents.data) setUpdatedAt(new Date());
  }, [agents.data]);

  // The payload is cast, not validated — default the list so a partial
  // response degrades to "inga agenter" instead of crashing the render.
  const agentList = agents.data?.agents ?? [];

  // Default to the first agent (the brain) once the list arrives.
  useEffect(() => {
    if (selected === null && agentList.length > 0) {
      setSelected(agentList[0].name);
    }
  }, [agentList, selected]);

  const error = status.error ?? agents.error;
  const loading = status.initialLoading || agents.initialLoading;

  return (
    <div className="min-h-screen bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 transition-colors duration-300 relative p-1">
      <div className="flex items-center flex-wrap gap-2 mx-1 my-2">
        <Link
          href="/"
          aria-label="Tillbaka till översikten"
          className="w-9 h-9 rounded-full bg-blue-600 hover:bg-blue-700 text-white shadow flex items-center justify-center transition-colors"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </Link>
        <h1 className="text-3xl font-semibold tracking-tight">🧠 AI-hjärna</h1>
        <div className="flex grow gap-1.5 justify-end items-center">
          {updatedAt && (
            <span className="text-xs text-gray-600 dark:text-gray-400 tabular-nums">
              uppdaterad {updatedAt.toLocaleTimeString('sv-SE')}
            </span>
          )}
          <RefreshBadge />
        </div>
      </div>

      <div className="w-full py-0 flex flex-col gap-1.5">
        {error && <ErrorCard message={error.message} stale={Boolean(status.data || agents.data)} />}

        {loading && !status.data && !agents.data ? (
          <Skeleton />
        ) : (
          <>
            {status.data && <AiBrainSupervisor status={status.data} />}

            {agents.data && agentList.length === 0 && (
              <Card>
                <p className="text-xs text-gray-700 dark:text-gray-300">Inga agenter körs.</p>
              </Card>
            )}

            {agentList.length > 0 && (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
                {agentList.map((agent) => (
                  <AiBrainAgentCard
                    key={agent.name}
                    agent={agent}
                    now={now}
                    selected={agent.name === selected}
                    onSelect={setSelected}
                  />
                ))}
              </div>
            )}

            {selected && <AiBrainDetail agent={selected} now={now} />}
          </>
        )}
      </div>
    </div>
  );
}
