'use client';

import React from 'react';
import {
  type Loop,
  type LoopProposal,
  type Tone,
  createdSeconds,
  executorName,
  formatDay,
} from '../lib/aiBrain';
import {
  Age,
  MONO,
  Mono,
  Pill,
  SANS,
  SERIF,
  SlackMirrorButtons,
  WALL,
  toneColor,
} from './AiBrainWallTheme';

/** One slinga, read up close: how many laps it has run, over what span, what
 *  came of each lap, and what the whole thing has cost.
 *
 *  The point of the screen is the cost. A topic proposed four times and
 *  rejected four times has burnt four cycles of a budget that runs out daily
 *  and produced nothing — so the outcome split is loud, the verdict sentence is
 *  in the serif voice, and the proposal history is shown in full, oldest first,
 *  so the repetition is visible as a sequence rather than as a number. */

/** Colour family for a proposal status inside a loop.
 *
 *  `statusTone` in lib/aiBrain maps *cycle* statuses; a proposal's vocabulary
 *  is a different one (pending/approved/executed/rejected), so it is mapped
 *  here rather than stretching that function's meaning. */
export function proposalTone(status: string | null | undefined): Tone {
  switch ((status ?? '').toLowerCase()) {
    case 'approved':
    case 'executed':
    case 'executing':
    case 'done':
      return 'ok';
    case 'pending':
      return 'warn';
    case 'rejected':
    case 'denied':
    case 'failed':
      return 'error';
    default:
      return 'idle';
  }
}

const STATUS_LABEL: Record<string, string> = {
  pending: 'väntar',
  approved: 'godkänt',
  executed: 'verkställt',
  executing: 'körs',
  rejected: 'avslaget',
  denied: 'avslaget',
  failed: 'misslyckades',
};

export function statusLabel(status: string | null | undefined): string {
  const s = (status ?? '').toLowerCase();
  return STATUS_LABEL[s] ?? (s || 'okänt');
}

/** The one sentence that says what this loop has cost.
 *
 *  Kept here rather than inline so the wording is one thing: a loop that keeps
 *  being rejected is the case this screen exists for, and it must not be
 *  softened into "3 varv" with no verdict. */
export function costSentence(loop: Loop): string {
  const laps = loop.laps ?? 0;
  const { approved = 0, rejected = 0, pending = 0 } = loop;
  if (laps <= 1) return 'Föreslaget en gång — ingen slinga ännu.';
  if (approved === 0 && rejected >= laps) {
    return `Föreslaget ${laps} gånger, avslaget ${rejected} gånger. Varje varv kostade ett anrop och gav ingenting.`;
  }
  if (approved === 0 && pending > 0) {
    return `Föreslaget ${laps} gånger utan att något blivit godkänt — ${pending} väntar fortfarande på ✅.`;
  }
  if (approved > 0 && rejected > 0) {
    return `${laps} varv: ${approved} godkända, ${rejected} avslagna. Hjärnan har inte lärt sig var gränsen går.`;
  }
  if (approved >= laps) {
    return `${laps} varv, alla godkända — det här är en rutin snarare än ett tjat.`;
  }
  return `${laps} varv om samma sak.`;
}

/** Proposals oldest first — the order the repetition reads in. */
export function historyOldestFirst(proposals: LoopProposal[] | undefined): LoopProposal[] {
  return [...(proposals ?? [])].sort((a, b) => {
    const ta = createdSeconds(a.created);
    const tb = createdSeconds(b.created);
    if (ta === null && tb === null) return 0;
    if (ta === null) return -1;
    if (tb === null) return 1;
    return ta - tb;
  });
}

/** The outcome split as counts, only showing buckets that have members. */
const Split: React.FC<{ loop: Loop }> = ({ loop }) => {
  const buckets: { key: string; label: string; n: number; color: string }[] = [
    { key: 'pending', label: 'väntar', n: loop.pending ?? 0, color: WALL.amber },
    { key: 'approved', label: 'godkända', n: loop.approved ?? 0, color: WALL.sage },
    { key: 'rejected', label: 'avslagna', n: loop.rejected ?? 0, color: WALL.rose },
  ].filter((b) => b.n > 0);

  if (buckets.length === 0) {
    return (
      <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
        inget utfall registrerat
      </span>
    );
  }

  return (
    <div className="flex flex-wrap items-baseline gap-3">
      {buckets.map((b) => (
        <span key={b.key} className="text-[13px]" style={{ fontFamily: SANS, color: WALL.inkDim }}>
          <Mono className="text-[15px]">{b.n}</Mono>{' '}
          <span style={{ color: b.color }}>{b.label}</span>
        </span>
      ))}
    </div>
  );
};

/** The two resolutions the design asks for, rendered as what they are today:
 *  inert.
 *
 *  Each one is a real disabled `<button>` that says in full what it *would* do
 *  and where the decision actually has to be made. The ai-brain API is
 *  read-only by design — there is no write path to hang these on — so a button
 *  that posted nothing and then congratulated you would be a lie about the
 *  system. Saying "säg det i Slack" is the honest version. */
const Resolutions: React.FC = () => (
  <div className="flex flex-col gap-2">
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled
        title="Skulle skriva en rad i constitution.md: hjärnan får aldrig föreslå det här igen. Vyn är skrivskyddad — säg det i Slack."
        className="rounded-full px-3 py-1 text-[13px] cursor-default text-left"
        style={{
          fontFamily: SANS,
          color: WALL.rose,
          border: `1px solid ${WALL.rose}`,
          opacity: 0.6,
          background: 'transparent',
        }}
      >
        Aldrig mer det här
      </button>
      <button
        type="button"
        disabled
        title="Skulle skriva om målet i goals.md: behåll insikten, sluta föreslå åtgärden. Vyn är skrivskyddad — säg det i Slack."
        className="rounded-full px-3 py-1 text-[13px] cursor-default text-left"
        style={{
          fontFamily: SANS,
          color: WALL.amber,
          border: `1px solid ${WALL.amber}`,
          opacity: 0.6,
          background: 'transparent',
        }}
      >
        Behåll insikten, sluta föreslå
      </button>
    </div>
    <p className="text-[12px] m-0" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
      båda är avstängda här · <span style={{ color: WALL.rose }}>aldrig mer</span> skriver
      constitution.md, <span style={{ color: WALL.amber }}>behåll insikten</span> skriver goals.md —
      introspektions-API:t är skrivskyddat, så säg det i Slack så länge
    </p>
  </div>
);

export const AiBrainLoopCard: React.FC<{ loop: Loop; now: number }> = ({ loop, now }) => {
  const history = historyOldestFirst(loop.proposals);
  const kinds = loop.kinds ?? [];
  const span =
    loop.first_at && loop.last_at && loop.first_at !== loop.last_at
      ? `${formatDay(loop.first_at)} – ${formatDay(loop.last_at)}`
      : formatDay(loop.last_at || loop.first_at);

  return (
    <article
      className="flex flex-col gap-3 py-5 min-w-0"
      style={{ borderTop: `1px solid ${WALL.rule}` }}
    >
      <header className="flex items-baseline justify-between gap-4 min-w-0">
        <div className="flex items-baseline gap-3 min-w-0">
          <span
            className="text-[26px] tabular-nums shrink-0"
            style={{ fontFamily: MONO, color: WALL.rose }}
            title={`${loop.laps} varv`}
          >
            {loop.laps}×
          </span>
          <h3
            className="text-[22px] m-0 min-w-0 break-words"
            style={{ fontFamily: SERIF, color: WALL.ink, fontWeight: 400 }}
          >
            {loop.topic}
          </h3>
        </div>
        {loop.pending > 0 && <SlackMirrorButtons compact />}
      </header>

      <p
        className="text-[18px] leading-[1.4] m-0 max-w-[62ch]"
        style={{ fontFamily: SERIF, color: WALL.inkDim }}
      >
        {costSentence(loop)}
      </p>

      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2">
        <Split loop={loop} />
        <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
          {span}
        </span>
        <Age at={createdSeconds(loop.last_at)} now={now} suffix="senaste varvet" />
        {kinds.length > 0 && (
          <span className="flex flex-wrap items-center gap-2">
            {kinds.map((k) => (
              <Pill key={k} color={WALL.inkDim} title={`kind: ${k}`}>
                {executorName(k)}
              </Pill>
            ))}
          </span>
        )}
      </div>

      {history.length > 0 ? (
        <ol className="flex flex-col gap-1 list-none m-0 p-0">
          {history.map((p, i) => {
            const tone = proposalTone(p.status);
            return (
              <li key={p.id} className="flex items-baseline gap-3 min-w-0 text-[13px]">
                <Mono className="shrink-0 w-[2.5ch] text-right" >
                  <span style={{ color: WALL.inkFaint }}>{i + 1}</span>
                </Mono>
                <Mono className="shrink-0" >
                  <span style={{ color: WALL.inkFaint }}>{formatDay(p.created)}</span>
                </Mono>
                <span
                  className="shrink-0 text-[12px] uppercase tracking-[0.14em]"
                  style={{ fontFamily: SANS, color: toneColor(tone) }}
                >
                  {statusLabel(p.status)}
                </span>
                <span
                  className="min-w-0 truncate"
                  style={{ fontFamily: SANS, color: WALL.inkDim }}
                  title={p.result || undefined}
                >
                  {p.result || executorName(p.kind)}
                </span>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="text-[12px] m-0" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
          inga förslag kvar på disk för det här ämnet — bara räkningen
        </p>
      )}

      <Resolutions />
    </article>
  );
};

export default AiBrainLoopCard;
