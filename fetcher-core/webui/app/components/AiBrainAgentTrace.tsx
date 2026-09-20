'use client';

import React, { useCallback, useState } from 'react';
import {
  type CycleTrace,
  type RoundTrace,
  type ToolCall,
  type ToolResult,
  fetchTrace,
  formatAgo,
  formatClock,
  shortModel,
  statusTone,
  truncate,
} from '../lib/aiBrain';
import useAiBrain from '../hooks/useAiBrain';
import { EmptyState, MONO, Pill, SANS, SERIF, WALL, toneColor } from './AiBrainWallTheme';

/** "Spår" — one cycle, round by round, in the wall's language.
 *
 *  This is the debugging surface: read up close, dense, every identifier in
 *  mono. It replaces the trace tab of the old blue-card `AiBrainDetail` and
 *  keeps everything that tab could show — rounds, the model's text, tool calls
 *  with their arguments, tool results, the status, the model and the summary.
 *
 *  Three states are all real and all named honestly: no cycle has ever run
 *  (`trace === null`), one is running right now (`in_progress`), and one has
 *  finished with a status. A running cycle is not an empty one. */

const POLL_MS = 10_000;

/** A body of machine text — a model's round, a tool result, a fact. */
export const Pre: React.FC<{ children: React.ReactNode; className?: string }> = ({
  children,
  className = '',
}) => (
  <pre
    className={`text-[12px] leading-[1.55] whitespace-pre-wrap break-words m-0 ${className}`}
    style={{ fontFamily: MONO, color: WALL.ink }}
  >
    {children}
  </pre>
);

/** One `key: value` argument. Long values are cut at 160 characters behind a
 *  toggle: a single `append_journal` body is taller than the viewport, which
 *  is what used to make one cycle several screens. */
const ArgLine: React.FC<{ name: string; value: string }> = ({ name, value }) => {
  const [expanded, setExpanded] = useState(false);
  const { text, truncated } = truncate(value ?? '');

  return (
    <div
      className="text-[12px] leading-[1.5] break-words"
      style={{ fontFamily: MONO, color: WALL.inkDim }}
    >
      <span style={{ color: WALL.inkFaint }}>{name}:</span> {expanded ? value : text}
      {truncated && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="ml-2 cursor-pointer bg-transparent border-0 p-0 underline"
          style={{ fontFamily: SANS, color: WALL.amber }}
        >
          {expanded ? 'mindre' : 'mer'}
        </button>
      )}
    </div>
  );
};

export const CallView: React.FC<{ call: ToolCall }> = ({ call }) => (
  <div className="pl-3 flex flex-col gap-[1px]" style={{ borderLeft: `2px solid ${WALL.amber}` }}>
    <div className="text-[12px] break-words" style={{ fontFamily: MONO, color: WALL.amber }}>
      → {call.name}
    </div>
    {Object.entries(call.args ?? {}).map(([k, v]) => (
      <ArgLine key={k} name={k} value={String(v ?? '')} />
    ))}
  </div>
);

export const ResultView: React.FC<{ result: ToolResult }> = ({ result }) => (
  <div className="pl-3 flex flex-col gap-[1px]" style={{ borderLeft: `2px solid ${WALL.sage}` }}>
    <div className="text-[12px] break-words" style={{ fontFamily: MONO, color: WALL.sage }}>
      ← {result.name}
    </div>
    <div className="overflow-x-auto rounded px-2 py-1" style={{ background: WALL.raised }}>
      <Pre>{result.result_preview}</Pre>
    </div>
  </div>
);

/** A round collapses to its tool names; the body only mounts when opened, so a
 *  30-round cycle costs one line each until you ask for more. */
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
      className="rounded px-3 py-2 [&[open]>summary]:mb-2"
      style={{ background: WALL.raised }}
    >
      <summary
        className="cursor-pointer select-none text-[12px] break-words"
        style={{ fontFamily: MONO, color: WALL.inkFaint }}
      >
        <span style={{ color: WALL.ink }}>Runda {index + 1}</span>
        <span className="tabular-nums"> · {formatClock(round.at)}</span>
        {names && <span> · {names}</span>}
        {!names && <span> · ingen verktygsanvändning</span>}
      </summary>

      <div className="flex flex-col gap-2 min-w-0">
        {round.text && <Pre className="opacity-90">{round.text}</Pre>}
        {calls.map((call, j) => (
          <CallView key={`${call.name}-${j}`} call={call} />
        ))}
        {results.map((res, j) => (
          <ResultView key={`${res.name}-${j}`} result={res} />
        ))}
      </div>
    </details>
  );
};

/** The trace tab. `seed` is the trace `/api/agents/{name}` already carried, so
 *  the first paint has content; the 10 s poll then keeps a running cycle
 *  moving without the whole detail payload coming along. */
const AiBrainAgentTrace: React.FC<{
  agent: string;
  seed: CycleTrace | null;
  now: number;
}> = ({ agent, seed, now }) => {
  const fetcher = useCallback((signal: AbortSignal) => fetchTrace(agent, signal), [agent]);
  const { data, error, initialLoading } = useAiBrain(fetcher, [agent], POLL_MS);

  const trace: CycleTrace | null = data?.trace ?? seed;

  if (!trace && initialLoading) {
    return <EmptyState why="hämtar från /api/agents/…/trace">Läser spåret…</EmptyState>;
  }
  if (!trace && error) {
    return <EmptyState why={error.message}>Kunde inte hämta spåret.</EmptyState>;
  }
  if (!trace) {
    return (
      <EmptyState why="loopen har inte vaknat sedan hjärnan startade — spåret skrivs vid första cykeln">
        Ingen cykel har körts än.
      </EmptyState>
    );
  }

  // `in_progress` is the served flag; `finished_at === null` is the same thing
  // said by an older ai-brain, so both are honoured.
  const running = trace.in_progress || trace.finished_at === null;
  // The payload is cast, not validated: an older ai-brain omitting a list must
  // degrade to "inga rundor", not crash the render.
  const rounds = trace.rounds ?? [];
  const tone = running ? 'busy' : statusTone(trace.status);

  const head = [
    trace.model ? shortModel(trace.model) : 'ingen modell vald',
    trace.finished_at !== null
      ? `${formatClock(trace.started_at)}–${formatClock(trace.finished_at)}`
      : `start ${formatClock(trace.started_at)}`,
    `${rounds.length} ${rounds.length === 1 ? 'runda' : 'rundor'}`,
    formatAgo(trace.started_at, now),
  ];

  return (
    <div className="flex flex-col gap-3 min-w-0">
      <div className="flex items-center gap-2 flex-wrap">
        <Pill tone={tone}>{running ? 'pågår' : (trace.status ?? 'klar')}</Pill>
        <span
          className="text-[12px] tabular-nums break-words"
          style={{ fontFamily: MONO, color: WALL.inkFaint }}
        >
          {head.join(' · ')}
        </span>
      </div>

      {/* The model's own words about the cycle. Shown verbatim here even when
          it is status text — on the wall that would be a lie about what the
          house understands, but this screen is exactly where a failure message
          belongs. */}
      {trace.summary ? (
        <blockquote
          className="m-0 pl-3 text-[18px] leading-[1.4] break-words whitespace-pre-wrap"
          style={{ fontFamily: SERIF, color: WALL.ink, borderLeft: `2px solid ${toneColor(tone)}` }}
        >
          {trace.summary}
        </blockquote>
      ) : (
        <EmptyState
          why={running ? 'sammanfattningen skrivs när cykeln avslutas' : 'cykeln skrev ingen'}
        >
          Ingen sammanfattning.
        </EmptyState>
      )}

      {rounds.length === 0 ? (
        <EmptyState
          why={running ? 'första rundan har inte returnerat än' : 'cykeln avslutades utan rundor'}
        >
          Inga rundor i den här cykeln.
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-2 min-w-0">
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

      {error && (
        <p className="text-[12px] m-0" style={{ fontFamily: MONO, color: WALL.rose }}>
          senast kända spår · {error.message}
        </p>
      )}
    </div>
  );
};

export default AiBrainAgentTrace;
