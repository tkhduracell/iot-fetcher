'use client';

import React from 'react';
import {
  type Loop,
  type Proposal,
  createdSeconds,
  executorName,
  formatAgo,
  groupProposals,
  proposalSentence,
} from '../lib/aiBrain';
import { BeliefRow, MONO, SANS, SERIF, SlackMirrorButtons, WALL } from './AiBrainWallTheme';

/** The three action blocks of the wall: what waits on a Slack ✅, what was
 *  actually carried out, and what the brain keeps coming back to.
 *
 *  The editorial rules — which statuses count, what a proposal's one line is,
 *  which repeats collapse, what counts as a loop — all live in `lib/aiBrain`
 *  so they are testable without a DOM. This file is layout. */

export const PendingProposals: React.FC<{
  proposals: Proposal[];
  now: number;
  /** The inert Slack pair beside each row.
   *
   *  Off on the wall: a column is ~300px wide at 1024 and three rows of
   *  buttons cost more vertical space than the proposals themselves, which is
   *  how the page stopped fitting its screen. The wall carries one pair in the
   *  section heading instead — the affordance is still there and still says
   *  Slack, once rather than three times. */
  buttons?: boolean;
}> = ({ proposals, now, buttons = true }) => (
  <ul className="flex flex-col gap-3 list-none m-0 p-0">
    {proposals.map((p) => (
      <li key={p.id} className="flex items-start justify-between gap-3 min-w-0">
        <div className="min-w-0 flex flex-col gap-[2px]">
          <p
            className="text-[19px] leading-[1.32] m-0"
            title={p.reason || undefined}
            style={{
              fontFamily: SERIF,
              color: WALL.ink,
              display: '-webkit-box',
              WebkitBoxOrient: 'vertical',
              WebkitLineClamp: 2,
              overflow: 'hidden',
            }}
          >
            {proposalSentence(p)}
          </p>
          <p
            className="text-[12px] m-0"
            style={{ fontFamily: MONO, color: WALL.inkFaint }}
          >
            <span style={{ color: WALL.amber }}>{executorName(p.kind)}</span>
            {' · '}
            {formatAgo(createdSeconds(p.created), now)}
          </p>
        </div>
        {buttons && <SlackMirrorButtons />}
      </li>
    ))}
  </ul>
);

/** Verkställt: executions collapsed by what they actually say.
 *
 *  The deployed wall listed "Replace Roborock S6 MaxV main brush" three times,
 *  twice near-identically. Repeating a line three times is the repetition this
 *  UI exists to make visible — so it is one line with a count, the way a loop
 *  is. The count is the loud part. */
export const ExecutedProposals: React.FC<{
  proposals: Proposal[];
  now: number;
  /** Groups to show. The caller passes a wider slice of raw proposals than it
   *  wants lines, because repeats collapse into each other here. */
  limit?: number;
}> = ({ proposals, now, limit }) => {
  const groups = groupProposals(proposals).slice(0, limit ?? undefined);
  return (
    <ul className="flex flex-col gap-3 list-none m-0 p-0">
      {groups.map((g) => (
        <BeliefRow
          key={g.key}
          lines={2}
          meta={
            <>
              <span style={{ color: WALL.sage }}>{executorName(g.latest.kind)}</span>
              {' · '}
              {formatAgo(createdSeconds(g.latest.created), now)}
              {g.count > 1 && ` · senast av ${g.count}`}
            </>
          }
        >
          {g.count > 1 && (
            <span
              className="mr-2 align-baseline"
              style={{ fontFamily: MONO, color: WALL.amber }}
            >
              {g.count}×
            </span>
          )}
          {g.sentence}
        </BeliefRow>
      ))}
    </ul>
  );
};

/** "Tjatar om" — topics the brain has come back to more than once.
 *
 *  The caller filters with `naggingLoops`, which drops single-lap topics: a
 *  subject proposed once is not something anybody is being nagged about, and
 *  the deployed wall listing `1× roborock` said otherwise. */
export const NaggingLoops: React.FC<{ loops: Loop[] }> = ({ loops }) => (
  <ul className="flex flex-col gap-2 list-none m-0 p-0">
    {loops.map((loop) => (
      <li key={loop.topic} className="flex items-baseline gap-2 min-w-0">
        <span className="text-[20px] shrink-0" style={{ fontFamily: MONO, color: WALL.rose }}>
          {loop.laps}×
        </span>
        <span
          className="text-[18px] truncate"
          style={{ fontFamily: SERIF, color: WALL.ink }}
          title={loop.topic}
        >
          {loop.topic}
        </span>
        {loop.pending > 0 && (
          <span
            className="text-[12px] shrink-0"
            style={{ fontFamily: SANS, color: WALL.inkFaint }}
          >
            ({loop.pending} väntar)
          </span>
        )}
      </li>
    ))}
  </ul>
);

export default PendingProposals;
