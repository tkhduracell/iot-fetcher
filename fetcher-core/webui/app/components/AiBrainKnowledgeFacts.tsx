'use client';

import React, { useState } from 'react';
import {
  type FactStat,
  factSentence,
  freshnessTone,
  humanizeFactName,
} from '../lib/aiBrain';
import { Age, BeliefRow, MONO, SANS, WALL, toneColor } from './AiBrainWallTheme';

/** One fact as the knowledge screen reads it.
 *
 *  The wall shows a belief as a sentence and stops there. Here the same
 *  sentence is the headline, but everything the wall had no room for hangs off
 *  it: which loop wrote it, when, how many times it has been rewritten, and —
 *  on demand — the body the sentence was distilled from.
 *
 *  That last part is not decoration. Fact bodies on the deployed brain are long
 *  technical paragraphs, so `factSentence` is always a lossy summary; the row
 *  has to be able to admit what it left out. The claim stays readable at a
 *  glance (two lines, serif, 18px) and the paste lives behind a real
 *  `<button>`. */

export type FactRowData = {
  /** The loop that owns the fact. */
  agent: string;
  /** The fact's file name, which is its identity. */
  name: string;
  /** Undefined until `fact_stats` carries this fact. */
  stat?: FactStat;
  /** Undefined while the body is in flight, or when it was not fetched. */
  body?: string;
};

/** The claim a row shows: the fact's own first statement, or — while the body
 *  is in flight or unreadable — its humanized file name. Exported so the text
 *  filter matches exactly what the reader sees. */
export function claimOf(row: FactRowData): string {
  return factSentence(row.body, 200) || humanizeFactName(row.name);
}

const FactRow: React.FC<{
  row: FactRowData;
  now: number;
  /** Shown as a kicker when the list spans more than one loop. */
  showAgent?: boolean;
}> = ({ row, now, showAgent = true }) => {
  const [open, setOpen] = useState(false);
  const stat = row.stat;
  const tone = freshnessTone(stat?.written_at, now);
  const id = `fakta-${row.agent}-${row.name}`.replace(/[^\w-]/g, '_');

  return (
    <>
      <BeliefRow
        density="read"
        lines={open ? 0 : 2}
        kicker={showAgent ? row.agent : undefined}
        kickerColor={toneColor(tone)}
        meta={
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span style={{ color: WALL.inkFaint }}>{row.name}</span>
            {stat ? (
              <Age
                at={stat.written_at}
                now={now}
                /* writes > 1 is the repetition signal: a fact the brain keeps
                   rewriting is one it keeps changing its mind about. */
                suffix={stat.writes > 1 ? `${stat.writes}× skriven` : '1× skriven'}
              />
            ) : (
              <span style={{ color: WALL.inkFaint }}>ingen skrivtid</span>
            )}
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              aria-controls={id}
              className="text-[12px] underline cursor-pointer bg-transparent p-0"
              style={{ fontFamily: SANS, color: WALL.inkDim, border: 'none' }}
            >
              {open ? 'dölj texten' : 'hela texten'}
            </button>
          </span>
        }
      >
        {claimOf(row)}
      </BeliefRow>

      {open && (
        <li id={id} className="list-none min-w-0">
          <pre
            className="text-[13px] leading-[1.55] whitespace-pre-wrap break-words m-0 mt-1 p-3 rounded"
            style={{
              fontFamily: MONO,
              color: WALL.inkDim,
              background: WALL.raised,
              border: `1px solid ${WALL.rule}`,
            }}
          >
            {row.body?.trim() || 'Texten har inte hämtats — ai-brain svarade inte på det här faktat.'}
          </pre>
        </li>
      )}
    </>
  );
};

/** The fact list itself. Ordering is the caller's — this only renders. */
const AiBrainKnowledgeFacts: React.FC<{
  rows: FactRowData[];
  now: number;
  showAgent?: boolean;
}> = ({ rows, now, showAgent = true }) => (
  <ul className="flex flex-col gap-4 list-none m-0 p-0 min-w-0">
    {rows.map((row) => (
      <FactRow key={`${row.agent}/${row.name}`} row={row} now={now} showAgent={showAgent} />
    ))}
  </ul>
);

export default AiBrainKnowledgeFacts;
