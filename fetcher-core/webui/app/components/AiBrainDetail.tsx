'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  type AgentDetail,
  type CycleTrace,
  type RoundTrace,
  type ToolCall,
  fetchAgent,
  fetchFact,
  fetchJournal,
  fetchTrace,
  formatAgo,
  formatClock,
  parseJournalLine,
  shortModel,
  truncate,
} from '../lib/aiBrain';
import useAiBrain from '../hooks/useAiBrain';
import { Card, Pill } from './AiBrainSupervisor';

type Tab = 'trace' | 'journal' | 'goals' | 'facts' | 'inbox';

const TABS: { id: Tab; label: string }[] = [
  { id: 'trace', label: 'Spår' },
  { id: 'journal', label: 'Journal' },
  { id: 'goals', label: 'Mål & Identitet' },
  { id: 'facts', label: 'Fakta' },
  { id: 'inbox', label: 'Inkorg' },
];

const DAY_CHOICES = [1, 3, 7, 30];

const Empty: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <p className="text-xs text-gray-600 dark:text-gray-400 italic">{children}</p>
);

const Mono: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <pre className="text-[11px] font-mono whitespace-pre-wrap break-words text-gray-900 dark:text-gray-100">
    {children}
  </pre>
);

// ------------------------------------------------------------------ trace

/** One `key: value` argument line. Long values are cut at 160 chars behind a
 *  "…visa mer" toggle — a single append_journal body used to be taller than
 *  the viewport, which is what made one cycle several screens. */
const ArgLine: React.FC<{ name: string; value: string }> = ({ name, value }) => {
  const [expanded, setExpanded] = useState(false);
  const { text, truncated } = truncate(value ?? '');

  return (
    <div className="text-[11px] text-gray-700 dark:text-gray-300 break-words">
      <span className="text-gray-500 dark:text-gray-500">{name}:</span>{' '}
      {expanded ? value : text}
      {truncated && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="ml-1 text-blue-700 dark:text-blue-300 hover:underline cursor-pointer"
        >
          {expanded ? 'visa mindre' : '…visa mer'}
        </button>
      )}
    </div>
  );
};

const CallView: React.FC<{ call: ToolCall }> = ({ call }) => (
  <div className="pl-2 border-l-2 border-blue-300 dark:border-blue-700">
    <div className="text-[11px] font-semibold text-blue-800 dark:text-blue-300 break-words">
      → {call.name}
    </div>
    {Object.entries(call.args ?? {}).map(([k, v]) => (
      <ArgLine key={k} name={k} value={String(v ?? '')} />
    ))}
  </div>
);

/** A round is collapsed to its tool names; the body only mounts when opened,
 *  so a 30-round cycle costs one line each until you ask for more. */
const RoundView: React.FC<{ round: RoundTrace; index: number; defaultOpen: boolean }> = ({
  round,
  index,
  defaultOpen,
}) => {
  const calls = round.tool_calls ?? [];
  const results = round.tool_results ?? [];
  const names = calls.map((c) => c.name).join(', ');

  return (
    <details
      open={defaultOpen}
      className="rounded bg-blue-50 dark:bg-blue-950 px-2 py-1.5 [&[open]>summary]:mb-1"
    >
      <summary className="cursor-pointer select-none text-[11px] text-gray-600 dark:text-gray-400 break-words">
        <span className="font-semibold text-gray-900 dark:text-gray-100">Runda {index + 1}</span>
        <span className="tabular-nums"> · {formatClock(round.at)}</span>
        {names && <span> · {names}</span>}
      </summary>

      <div className="flex flex-col gap-1">
        {round.text && <Mono>{round.text}</Mono>}
        {calls.map((call, j) => (
          <CallView key={j} call={call} />
        ))}
        {results.length > 0 && (
          <details className="pl-2 border-l-2 border-green-300 dark:border-green-700">
            <summary className="cursor-pointer select-none text-[11px] font-semibold text-green-800 dark:text-green-300">
              resultat ({results.length})
            </summary>
            <div className="flex flex-col gap-1 mt-1">
              {results.map((res, j) => (
                <div key={j}>
                  <div className="text-[11px] text-gray-600 dark:text-gray-400 break-words">
                    ← {res.name}
                  </div>
                  <div className="rounded bg-blue-100 dark:bg-blue-900 px-1.5 py-1 overflow-x-auto">
                    <Mono>{res.result_preview}</Mono>
                  </div>
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </details>
  );
};

const TraceView: React.FC<{ agent: string; seed: CycleTrace | null }> = ({ agent, seed }) => {
  const fetcher = useCallback((signal: AbortSignal) => fetchTrace(agent, signal), [agent]);
  const { data, error, initialLoading } = useAiBrain(fetcher, [agent], 10_000);

  // /api/agents/{name} already carried the trace, so the tab renders on the
  // first paint; the 10 s poll then keeps a running cycle moving.
  const trace: CycleTrace | null = data?.trace ?? seed;

  if (!trace && initialLoading) return <Empty>Hämtar spår…</Empty>;
  if (!trace && error) return <Empty>Kunde inte hämta spåret: {error.message}</Empty>;
  if (!trace) return <Empty>Ingen cykel har körts än.</Empty>;

  const running = trace.finished_at === null;
  // The payload is cast, not validated, so an older ai-brain omitting a list
  // would crash the render rather than degrade.
  const rounds = trace.rounds ?? [];

  const head = [
    running ? null : (trace.status ?? 'klar'),
    trace.model ? shortModel(trace.model) : 'ingen modell',
    trace.finished_at !== null
      ? `${formatClock(trace.started_at)}–${formatClock(trace.finished_at)}`
      : formatClock(trace.started_at),
    `${rounds.length} rundor`,
  ].filter(Boolean);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-1.5 flex-wrap text-xs text-gray-700 dark:text-gray-300">
        {running && (
          <Pill className="bg-blue-600 dark:bg-blue-700 text-white animate-pulse">pågår…</Pill>
        )}
        <span className="tabular-nums break-words">{head.join(' · ')}</span>
      </div>

      {trace.summary && (
        <blockquote className="border-l-2 border-blue-400 dark:border-blue-600 pl-2 text-xs text-gray-900 dark:text-gray-100 break-words whitespace-pre-wrap">
          {trace.summary}
        </blockquote>
      )}

      {rounds.length === 0 ? (
        <Empty>Inga rundor i den här cykeln.</Empty>
      ) : (
        <div className="flex flex-col gap-1">
          {rounds.map((round, i) => (
            // Keyed by index *and* round count so a poll that appends a round
            // does not carry the previous last round's open state onto it.
            <RoundView
              key={`${rounds.length}-${i}`}
              round={round}
              index={i}
              defaultOpen={i === rounds.length - 1}
            />
          ))}
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------- journal

const JournalView: React.FC<{ agent: string }> = ({ agent }) => {
  const [days, setDays] = useState(3);
  const fetcher = useCallback(
    (signal: AbortSignal) => fetchJournal(agent, days, signal),
    [agent, days],
  );
  const { data, error, initialLoading } = useAiBrain(fetcher, [agent, days], 60_000);

  return (
    <div className="flex flex-col gap-2">
      <div
        role="group"
        aria-label="Antal dagar"
        className="inline-flex self-start rounded-md overflow-hidden ring-1 ring-blue-300 dark:ring-blue-700"
      >
        {DAY_CHOICES.map((d) => (
          <button
            key={d}
            type="button"
            aria-pressed={d === days}
            onClick={() => setDays(d)}
            className={`px-2.5 py-0.5 text-xs font-semibold cursor-pointer transition-colors border-r last:border-r-0 border-blue-300 dark:border-blue-700 ${
              d === days
                ? 'bg-blue-600 dark:bg-blue-700 text-white'
                : 'bg-blue-100 dark:bg-blue-900 text-gray-900 dark:text-gray-100 hover:bg-blue-200 dark:hover:bg-blue-800'
            }`}
          >
            {d} d
          </button>
        ))}
      </div>

      {initialLoading && <Empty>Hämtar journal…</Empty>}
      {error && !data && <Empty>Kunde inte hämta journalen: {error.message}</Empty>}

      {data && (data.entries ?? []).length === 0 && <Empty>Inga journalrader i perioden.</Empty>}

      {(data?.entries ?? []).map((entry) => (
        <div key={entry.date} className="flex flex-col gap-0.5">
          <div className="text-xs font-semibold text-gray-600 dark:text-gray-400 tabular-nums">
            {entry.date}
          </div>
          <div className="rounded bg-blue-50 dark:bg-blue-950 px-2 py-1.5 flex flex-col gap-0.5">
            {(entry.lines ?? []).map((line, i) => {
              const { time, text } = parseJournalLine(line);
              return (
                <div key={i} className="flex gap-2">
                  <span className="text-xs text-gray-500 dark:text-gray-500 tabular-nums flex-shrink-0">
                    {time ?? ''}
                  </span>
                  <span className="text-xs text-gray-900 dark:text-gray-100 break-words min-w-0">
                    {text}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
};

// ------------------------------------------------------------------ facts

const FactsView: React.FC<{ agent: string; factNames: string[] }> = ({ agent, factNames }) => {
  const [open, setOpen] = useState<string | null>(null);
  const [body, setBody] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Two facts opened in quick succession race: without these guards the slower
  // response wins and paints its body under the other fact's heading.
  const openRef = useRef<string | null>(null);
  const mountedRef = useRef(true);
  const inFlightRef = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      mountedRef.current = false;
      inFlightRef.current?.abort();
    },
    [],
  );

  const show = async (name: string) => {
    inFlightRef.current?.abort();
    if (open === name) {
      openRef.current = null;
      setOpen(null);
      return;
    }
    openRef.current = name;
    setOpen(name);
    setBody(null);
    setError(null);

    const controller = new AbortController();
    inFlightRef.current = controller;
    try {
      const result = await fetchFact(agent, name, controller.signal);
      if (!mountedRef.current || openRef.current !== name) return;
      setBody(result.body);
    } catch (e) {
      if (!mountedRef.current || openRef.current !== name || controller.signal.aborted) return;
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  if (factNames.length === 0) return <Empty>Agenten har inga fakta ännu.</Empty>;

  return (
    <div className="flex flex-col gap-1">
      {factNames.map((name) => (
        <div key={name} className="flex flex-col gap-1">
          <button
            type="button"
            onClick={() => show(name)}
            className="text-left text-xs font-semibold text-blue-800 dark:text-blue-300 hover:underline cursor-pointer break-words"
          >
            {open === name ? '▾' : '▸'} {name}
          </button>
          {open === name && (
            <div className="rounded bg-blue-50 dark:bg-blue-950 px-2 py-1.5 overflow-x-auto">
              {error ? (
                <Empty>Kunde inte hämta faktan: {error}</Empty>
              ) : body === null ? (
                <Empty>Hämtar…</Empty>
              ) : (
                <Mono>{body}</Mono>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

// ------------------------------------------------------------------ panel

const AiBrainDetail: React.FC<{ agent: string; now: number }> = ({ agent, now }) => {
  const [tab, setTab] = useState<Tab>('trace');
  const fetcher = useCallback((signal: AbortSignal) => fetchAgent(agent, signal), [agent]);
  const { data, error, initialLoading } = useAiBrain(fetcher, [agent], 30_000);

  const detail: AgentDetail | null = data;

  const renderBody = () => {
    if (!detail) return null;
    switch (tab) {
      case 'trace':
        return <TraceView agent={agent} seed={detail.trace ?? null} />;
      case 'journal':
        return <JournalView agent={agent} />;
      case 'goals':
        return (
          <div className="flex flex-col gap-2">
            <div>
              <div className="text-[10px] uppercase tracking-wide text-gray-600 dark:text-gray-400">
                Identitet
              </div>
              {detail.identity ? <Mono>{detail.identity}</Mono> : <Empty>Ingen identitet.</Empty>}
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-gray-600 dark:text-gray-400">
                Mål
              </div>
              {detail.goals ? <Mono>{detail.goals}</Mono> : <Empty>Inga mål.</Empty>}
            </div>
          </div>
        );
      case 'facts':
        return <FactsView agent={agent} factNames={detail.fact_names ?? []} />;
      case 'inbox':
        return (detail.notes ?? []).length === 0 ? (
          <Empty>Inkorgen är tom.</Empty>
        ) : (
          <div className="flex flex-col gap-1.5">
            {(detail.notes ?? []).map((note) => (
              <div key={note.file} className="rounded bg-blue-50 dark:bg-blue-950 px-2 py-1.5">
                <div className="flex items-center gap-2 text-[11px] text-gray-600 dark:text-gray-400">
                  <span className="font-semibold text-gray-900 dark:text-gray-100 truncate">
                    {note.sender}
                  </span>
                  <span className="tabular-nums shrink-0">{note.created}</span>
                </div>
                <Mono>{note.body}</Mono>
              </div>
            ))}
          </div>
        );
    }
  };

  return (
    <Card className="min-w-0">
      {/* Sticky so the tabs stay reachable while a long trace scrolls: at the
          very top on phones (single column), just under the page padding once
          the two-column layout puts the panel beside the list. */}
      <div className="sticky top-0 lg:top-2 z-10 -mx-2 sm:-mx-3 -mt-2 sm:-mt-3 px-2 sm:px-3 pt-2 sm:pt-3 pb-1.5 bg-blue-100 dark:bg-blue-900 rounded-t-md flex flex-col gap-1.5">
        <div className="flex items-center gap-2 min-w-0">
          <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">
            {agent}
          </h2>
          {detail?.is_brain && (
            <Pill className="bg-purple-600 text-white shrink-0">hjärna</Pill>
          )}
          {detail && (
            <span className="ml-auto text-xs text-gray-700 dark:text-gray-300 tabular-nums shrink-0">
              {formatAgo(detail.last_cycle_at, now)}
            </span>
          )}
        </div>

        <div className="flex gap-1 flex-wrap border-b border-blue-200 dark:border-blue-800 pb-1.5">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              aria-pressed={t.id === tab}
              onClick={() => setTab(t.id)}
              className={`px-2 py-0.5 rounded text-xs font-semibold cursor-pointer transition-colors ${
                t.id === tab
                  ? 'bg-blue-600 dark:bg-blue-700 text-white'
                  : 'text-gray-700 dark:text-gray-300 hover:bg-blue-200 dark:hover:bg-blue-800'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {initialLoading && <Empty>Hämtar agenten…</Empty>}
      {error && !detail && <Empty>Kunde inte hämta agenten: {error.message}</Empty>}
      {renderBody()}
    </Card>
  );
};

export default AiBrainDetail;
