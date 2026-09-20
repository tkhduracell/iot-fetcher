'use client';

import React from 'react';
import { MONO, SANS, WALL } from './AiBrainWallTheme';

/** Loop filter and free-text filter for the knowledge screen.
 *
 *  Real `<button>`s and a real `<input>` with a real `<label>` — the fact base
 *  is long enough that filtering is how it is read, and a clickable div would
 *  make that unreachable from a keyboard. */

export const ALL_LOOPS = '';

const AiBrainKnowledgeFilters: React.FC<{
  /** Loop names in display order, with their fact counts. */
  loops: { name: string; facts: number }[];
  loop: string;
  onLoop: (name: string) => void;
  query: string;
  onQuery: (value: string) => void;
  /** How many facts the current filter leaves, for the input's hint. */
  shown: number;
  total: number;
}> = ({ loops, loop, onLoop, query, onQuery, shown, total }) => {
  const chip = (active: boolean): React.CSSProperties => ({
    fontFamily: MONO,
    color: active ? WALL.ground : WALL.inkDim,
    background: active ? WALL.amber : 'transparent',
    border: `1px solid ${active ? WALL.amber : WALL.rule}`,
  });

  return (
    <div className="flex flex-col gap-3 min-w-0">
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filtrera på loop">
        <button
          type="button"
          onClick={() => onLoop(ALL_LOOPS)}
          aria-pressed={loop === ALL_LOOPS}
          className="px-3 py-1 rounded-full text-[12px] cursor-pointer whitespace-nowrap"
          style={chip(loop === ALL_LOOPS)}
        >
          alla loopar {total}
        </button>
        {loops.map((l) => (
          <button
            key={l.name}
            type="button"
            onClick={() => onLoop(l.name)}
            aria-pressed={loop === l.name}
            className="px-3 py-1 rounded-full text-[12px] cursor-pointer whitespace-nowrap"
            style={chip(loop === l.name)}
          >
            {l.name} {l.facts}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-baseline gap-3">
        <label
          htmlFor="fakta-filter"
          className="text-[12px] uppercase tracking-[0.18em]"
          style={{ fontFamily: SANS, color: WALL.inkFaint }}
        >
          Sök i påståenden
        </label>
        <input
          id="fakta-filter"
          type="search"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="t.ex. pool, garmin, temperatur"
          className="flex-1 min-w-[12rem] max-w-[28rem] px-3 py-1 rounded text-[14px] outline-none"
          style={{
            fontFamily: MONO,
            color: WALL.ink,
            background: WALL.raised,
            border: `1px solid ${WALL.rule}`,
          }}
        />
        <span className="text-[12px] tabular-nums" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
          {shown} av {total} fakta
        </span>
      </div>
    </div>
  );
};

export default AiBrainKnowledgeFilters;
