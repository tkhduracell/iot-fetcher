'use client';

import React from 'react';
import { type Loop, type Proposal, formatAgo } from '../lib/aiBrain';
import { MONO, SANS, SERIF, WALL } from './AiBrainWallTheme';

/** The three action blocks of the wall: what waits on a Slack ✅, what was
 *  actually carried out, and what the brain keeps coming back to. */

/** Which executor runs a proposal kind. The kind *is* the executor entry point
 *  in ai-brain (`Executors.run`), so this only gives it a name a room can read;
 *  an unknown kind falls through to the raw identifier rather than being hidden. */
const EXECUTOR_LABEL: Record<string, string> = {
  sonos_say: 'Sonos',
  ha_todo_add: 'Att göra-listan',
  ha_service: 'Home Assistant',
};

export function executorName(kind: string): string {
  return EXECUTOR_LABEL[kind] ?? kind;
}

/** Proposal timestamps are ISO strings; everything else on the wall is epoch
 *  seconds. Unparseable input yields null so `formatAgo` shows a dash. */
export function createdSeconds(created: string | undefined): number | null {
  if (!created) return null;
  const ms = Date.parse(created);
  return Number.isFinite(ms) ? ms / 1000 : null;
}

const EXECUTED_STATUSES = new Set(['executed', 'executing']);

export function isPending(p: Proposal): boolean {
  return p.status === 'pending';
}

export function isExecuted(p: Proposal): boolean {
  return EXECUTED_STATUSES.has(p.status);
}

/** The one line a proposal is about: its payload's own text where the kind has
 *  one, else the topic. Keeps the wall showing the thing, not the JSON. */
export function proposalSentence(p: Proposal): string {
  const payload = (p.payload ?? {}) as Record<string, unknown>;
  for (const key of ['text', 'item', 'entity_id']) {
    const value = payload[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return p.topic || p.kind || p.id;
}

/** Approve/reject as they appear on the wall.
 *
 *  Deliberately inert: the ai-brain API is read-only and approval happens by
 *  reacting in Slack. They are real disabled buttons rather than decorations so
 *  the affordance is honest and reachable — pressing nothing is the point. */
const MirrorButtons: React.FC = () => (
  <div className="flex items-center gap-2 shrink-0">
    <button
      type="button"
      disabled
      title="Godkänn med ✅ i Slack"
      className="px-3 py-1 rounded-full text-[13px] cursor-default"
      style={{
        fontFamily: SANS,
        color: WALL.sage,
        border: `1px solid ${WALL.sage}`,
        opacity: 0.75,
        background: 'transparent',
      }}
    >
      ✅ Slack
    </button>
    <button
      type="button"
      disabled
      title="Avslå med ❌ i Slack"
      className="px-3 py-1 rounded-full text-[13px] cursor-default"
      style={{
        fontFamily: SANS,
        color: WALL.rose,
        border: `1px solid ${WALL.rose}`,
        opacity: 0.6,
        background: 'transparent',
      }}
    >
      ❌
    </button>
  </div>
);

export const PendingProposals: React.FC<{ proposals: Proposal[]; now: number }> = ({
  proposals,
  now,
}) => (
  <ul className="flex flex-col gap-5 list-none m-0 p-0">
    {proposals.map((p) => (
      <li key={p.id} className="flex items-start justify-between gap-6">
        <div className="min-w-0 flex flex-col gap-1">
          <p className="text-[22px] leading-[1.3]" style={{ fontFamily: SERIF, color: WALL.ink }}>
            {proposalSentence(p)}
          </p>
          {p.reason && (
            <p className="text-[15px]" style={{ fontFamily: SANS, color: WALL.inkDim }}>
              {p.reason}
            </p>
          )}
          <p className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
            {executorName(p.kind)} · {formatAgo(createdSeconds(p.created), now)}
          </p>
        </div>
        <MirrorButtons />
      </li>
    ))}
  </ul>
);

export const ExecutedProposals: React.FC<{ proposals: Proposal[]; now: number }> = ({
  proposals,
  now,
}) => (
  <ul className="flex flex-col gap-4 list-none m-0 p-0">
    {proposals.map((p) => (
      <li key={p.id} className="flex flex-col gap-1">
        <p className="text-[20px] leading-[1.3]" style={{ fontFamily: SERIF, color: WALL.ink }}>
          {proposalSentence(p)}
        </p>
        <p className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
          <span style={{ color: WALL.sage }}>{executorName(p.kind)}</span>
          {' · '}
          {formatAgo(createdSeconds(p.created), now)}
          {p.result ? ` · ${p.result}` : ''}
        </p>
      </li>
    ))}
  </ul>
);

/** "Tjatar om" — topics proposed more than once, loudest lap count first.
 *  Fed by `/api/loops`, which does not exist yet; the caller hides the block
 *  while that is so. */
export const NaggingLoops: React.FC<{ loops: Loop[] }> = ({ loops }) => (
  <ul className="flex flex-wrap gap-x-8 gap-y-3 list-none m-0 p-0">
    {loops.map((loop) => (
      <li key={loop.topic} className="flex items-baseline gap-2">
        <span className="text-[26px]" style={{ fontFamily: MONO, color: WALL.amber }}>
          {loop.laps}×
        </span>
        <span className="text-[20px]" style={{ fontFamily: SERIF, color: WALL.ink }}>
          {loop.topic}
        </span>
        {loop.pending > 0 && (
          <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
            ({loop.pending} väntar)
          </span>
        )}
      </li>
    ))}
  </ul>
);

export default PendingProposals;
