'use client';

import React, { useCallback, useMemo } from 'react';
import {
  type AgentDetailPlus,
  type FactStat,
  factSentence,
  fetchAgentPlus,
  fetchFactBodies,
  formatAgo,
  humanizeFactName,
} from '../lib/aiBrain';
import useAiBrain from '../hooks/useAiBrain';
import { MONO, SANS, SERIF, WALL } from './AiBrainWallTheme';

/** The beliefs columns: one per agent, each fact rendered as its own sentence,
 *  with the agent's known unknowns (gaps) sitting dimmer among them. */

/** Facts per column. A wall tablet in landscape fits about this many at 20px
 *  before the columns start scrolling, and each one costs an HTTP round trip
 *  for its body — so the cap is a layout decision and a budget at once. */
const FACTS_PER_COLUMN = 6;
const GAPS_PER_COLUMN = 3;

const DETAIL_POLL_MS = 30_000;
/** Fact bodies change on the order of cycles, not seconds. */
const BODY_POLL_MS = 60_000;

function statsByName(stats: FactStat[] | undefined): Map<string, FactStat> {
  const map = new Map<string, FactStat>();
  for (const s of stats ?? []) {
    if (s && typeof s.name === 'string') map.set(s.name, s);
  }
  return map;
}

const Belief: React.FC<{
  sentence: string;
  /** Rendered beneath in mono; "" hides the line. */
  age: string;
  writes?: number;
}> = ({ sentence, age, writes }) => (
  <li className="flex flex-col gap-1">
    <p
      className="text-[20px] leading-[1.35]"
      style={{ fontFamily: SERIF, color: WALL.ink, fontWeight: 400 }}
    >
      {sentence}
    </p>
    {age && (
      <p
        className="text-[12px] tabular-nums"
        style={{ fontFamily: MONO, color: WALL.inkFaint }}
      >
        {age}
        {typeof writes === 'number' && writes > 1 && ` · ${writes}× skriven`}
      </p>
    )}
  </li>
);

const GapItem: React.FC<{ question: string; why: string; age: string }> = ({
  question,
  why,
  age,
}) => (
  <li className="flex flex-col gap-1">
    <p
      className="text-[20px] leading-[1.35] italic"
      style={{ fontFamily: SERIF, color: 'rgba(244, 237, 226, 0.42)' }}
    >
      <span
        className="not-italic text-[12px] uppercase tracking-[0.18em] mr-2 align-middle"
        style={{ fontFamily: SANS, color: WALL.amber, opacity: 0.7 }}
      >
        lucka
      </span>
      {question}
    </p>
    {(why || age) && (
      <p className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
        {[why, age].filter(Boolean).join(' · ')}
      </p>
    )}
  </li>
);

const BeliefColumn: React.FC<{ agent: string; facts: number; now: number }> = ({
  agent,
  facts,
  now,
}) => {
  const detailFetcher = useCallback(
    (signal: AbortSignal) => fetchAgentPlus(agent, signal),
    [agent],
  );
  const detail = useAiBrain<AgentDetailPlus>(detailFetcher, [agent], DETAIL_POLL_MS);

  // fact_stats is not served yet; until it is, facts keep the API's own order
  // and carry no age line rather than an invented one.
  const stats = statsByName(detail.data?.fact_stats);
  const names = detail.data?.fact_names ?? [];

  const shown = useMemo(() => {
    const list = [...names];
    if (stats.size > 0) {
      list.sort((a, b) => (stats.get(b)?.written_at ?? 0) - (stats.get(a)?.written_at ?? 0));
    }
    return list.slice(0, FACTS_PER_COLUMN);
    // `names` is a fresh array each poll; its contents are the real dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [names.join('\u0000'), stats.size]);

  const key = shown.join('\u0000');
  const bodiesFetcher = useCallback(
    (signal: AbortSignal) => fetchFactBodies(agent, shown, signal),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [agent, key],
  );
  const bodies = useAiBrain<Record<string, string>>(
    bodiesFetcher,
    [agent, key],
    BODY_POLL_MS,
    shown.length > 0,
  );

  const gaps = (detail.data?.gaps ?? []).slice(0, GAPS_PER_COLUMN);

  // An agent whose detail has not arrived yet renders its header and nothing
  // else — the column keeps its place in the grid instead of reflowing the row.
  return (
    <div className="flex flex-col gap-4 min-w-0">
      <div className="flex items-baseline gap-3 pb-2" style={{ borderBottom: `1px solid ${WALL.rule}` }}>
        <span
          className="text-[15px] tracking-[0.06em]"
          style={{ fontFamily: MONO, color: WALL.ink }}
        >
          {agent}
        </span>
        <span className="text-[12px] tabular-nums" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
          {facts} fakta
        </span>
      </div>

      <ul className="flex flex-col gap-4 list-none m-0 p-0">
        {shown.map((name) => {
          const stat = stats.get(name);
          // The sentence is the fact's own first prose line; the humanized
          // file name is the honest stand-in while the body is in flight.
          const sentence = factSentence(bodies.data?.[name]) || humanizeFactName(name);
          return (
            <Belief
              key={name}
              sentence={sentence}
              age={stat ? formatAgo(stat.written_at, now) : ''}
              writes={stat?.writes}
            />
          );
        })}

        {gaps.map((gap) => (
          <GapItem
            key={gap.id}
            question={gap.question}
            why={gap.why ?? ''}
            age={formatAgo(gap.opened_at, now)}
          />
        ))}

        {shown.length === 0 && gaps.length === 0 && (
          <li
            className="text-[16px] italic"
            style={{ fontFamily: SERIF, color: WALL.inkFaint }}
          >
            Inget nedskrivet ännu.
          </li>
        )}
      </ul>
    </div>
  );
};

const AiBrainWallBeliefs: React.FC<{
  /** Already narrowed to the columns to show, in display order. */
  agents: { name: string; facts: number }[];
  now: number;
}> = ({ agents, now }) => (
  <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-10">
    {agents.map((a) => (
      <BeliefColumn key={a.name} agent={a.name} facts={a.facts} now={now} />
    ))}
  </div>
);

export default AiBrainWallBeliefs;
