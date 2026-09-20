'use client';

import React, { useCallback, useMemo } from 'react';
import {
  type Loop,
  type LoopsResponse,
  type Status,
  fetchLoops,
  fetchStatus,
  naggingLoops,
} from '../lib/aiBrain';
import useAiBrain from '../hooks/useAiBrain';
import AiBrainLoopCard from './AiBrainLoopCard';
import {
  BackLink,
  EmptyState,
  MONO,
  Mono,
  SANS,
  SERIF,
  Section,
  WALL,
  WallShell,
  useServerClock,
} from './AiBrainWallTheme';

/** `/ai-brain/loops` — the answer to "den upprepar sig".
 *
 *  Every topic the brain has proposed more than once, with the full proposal
 *  history behind it. The screen's job is to make the *cost* of a repetition
 *  visible: a topic proposed four times and rejected four times has spent four
 *  cycles of a daily budget on nothing, and that is invisible anywhere else in
 *  the system.
 *
 *  Read density: this is a phone screen and a tablet held in the hand, not the
 *  wall. It scrolls, and the detail is the point. */

const POLL_MS = 60_000;

/** `fetchLoops` resolves `null` when `/api/loops` cannot be reached and
 *  `{loops: []}` when the brain simply has no proposals on disk. They look the
 *  same in a blank panel and mean opposite things, so they are separated here
 *  and worded differently below. */
type LoopsState = 'loading' | 'unreachable' | 'empty' | 'ready';

export function loopsState(
  data: LoopsResponse | null | undefined,
  initialLoading: boolean,
): LoopsState {
  if (data === null || data === undefined) return initialLoading ? 'loading' : 'unreachable';
  if ((data.loops ?? []).length === 0) return 'empty';
  return 'ready';
}

/** The whole screen in one line: how many laps have been spent, and how many of
 *  them ended in a no. */
export function totalsSentence(loops: Loop[]): string {
  const repeated = loops.filter((l) => (l.laps ?? 0) >= 2);
  if (repeated.length === 0) return 'Inget ämne har föreslagits mer än en gång.';
  const laps = repeated.reduce((sum, l) => sum + (l.laps ?? 0), 0);
  const rejected = repeated.reduce((sum, l) => sum + (l.rejected ?? 0), 0);
  const subject = repeated.length === 1 ? 'ett ämne' : `${repeated.length} ämnen`;
  const tail =
    rejected > 0
      ? ` ${rejected} av varven slutade i ett nej.`
      : ' Inget av varven har avslagits.';
  return `Hjärnan har kommit tillbaka till ${subject} ${laps} gånger.${tail}`;
}

const AiBrainLoops: React.FC = () => {
  const loopsFetcher = useCallback((signal: AbortSignal) => fetchLoops(signal), []);
  const statusFetcher = useCallback((signal: AbortSignal) => fetchStatus(signal), []);

  const loops = useAiBrain<LoopsResponse | null>(loopsFetcher, [], POLL_MS);
  const status = useAiBrain<Status>(statusFetcher, [], POLL_MS);
  const now = useServerClock(status.data?.now);

  const all = useMemo(() => loops.data?.loops ?? [], [loops.data]);
  // laps >= 2 only: a topic proposed once is not a loop, whatever the endpoint
  // groups. The singles are still reachable, in their own quiet list below.
  const repeated = useMemo(() => naggingLoops(all, 2), [all]);
  const singles = useMemo(
    () =>
      all
        .filter((l) => (l.laps ?? 0) < 2)
        .sort((a, b) => a.topic.localeCompare(b.topic, 'sv')),
    [all],
  );

  const state = loopsState(loops.data, loops.initialLoading);

  return (
    <WallShell
      density="read"
      title="Slingor"
      current="/ai-brain/loops"
      back={<BackLink />}
      footer={
        <p className="text-[12px] m-0" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
          /api/loops · grupperar varje förslag på ämne · vyn är skrivskyddad, beslut fattas med ✅ i
          Slack
        </p>
      }
    >
      {state === 'ready' && (
        <p
          className="text-[24px] sm:text-[28px] leading-[1.25] max-w-[46ch] m-0"
          style={{ fontFamily: SERIF, color: WALL.ink, fontWeight: 400 }}
        >
          {totalsSentence(all)}
        </p>
      )}

      <Section
        title="Tjatar om"
        accent={WALL.rose}
        show={state === 'ready' && repeated.length > 0}
        empty={
          state === 'loading' ? (
            <EmptyState why="hämtar /api/loops">Läser slingorna …</EmptyState>
          ) : state === 'unreachable' ? (
            <EmptyState why="/api/loops svarade inte — det här är inte samma sak som noll slingor, det är ingen kontakt">
              Vet inte om hjärnan upprepar sig.
            </EmptyState>
          ) : state === 'empty' ? (
            <EmptyState why="hjärnan har inga förslag på disk ännu — slingor byggs av förslagen som redan skrivits">
              Hjärnan har inte föreslagit någonting ännu.
            </EmptyState>
          ) : (
            <EmptyState why="varje ämne har föreslagits exakt en gång — två varv är golvet för att kallas slinga">
              Ingenting upprepas.
            </EmptyState>
          )
        }
      >
        <div className="flex flex-col">
          {repeated.map((loop) => (
            <AiBrainLoopCard key={loop.topic} loop={loop} now={now} />
          ))}
        </div>
      </Section>

      {/* Single-lap topics are not loops, but they are the whole proposal
          history the old page could show — kept reachable, kept quiet. */}
      <Section
        title="Föreslaget en gång"
        show={singles.length > 0}
        empty={
          state === 'ready' ? (
            <EmptyState why="varje ämne hjärnan tagit upp har tagits upp igen">
              Ingenting har bara sagts en gång.
            </EmptyState>
          ) : undefined
        }
      >
        <ul className="flex flex-wrap gap-x-4 gap-y-2 list-none m-0 p-0">
          {singles.map((l) => (
            <li key={l.topic} className="flex items-baseline gap-2 min-w-0">
              <Mono className="text-[12px]">
                <span style={{ color: WALL.inkFaint }}>1×</span>
              </Mono>
              <span
                className="text-[15px] truncate"
                style={{ fontFamily: SANS, color: WALL.inkDim }}
                title={l.topic}
              >
                {l.topic}
              </span>
            </li>
          ))}
        </ul>
      </Section>
    </WallShell>
  );
};

export default AiBrainLoops;
