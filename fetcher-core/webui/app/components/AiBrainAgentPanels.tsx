'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  type AgentDetailPlus,
  type Note,
  factSentence,
  fetchFact,
  fetchFactBodies,
  fetchJournal,
  formatDay,
  freshnessTone,
  humanizeFactName,
  parseJournalLine,
} from '../lib/aiBrain';
import useAiBrain from '../hooks/useAiBrain';
import {
  Age,
  BeliefRow,
  EmptyState,
  MONO,
  MoreLink,
  Pill,
  SANS,
  SERIF,
  SectionTitle,
  WALL,
  toneColor,
} from './AiBrainWallTheme';
import { Pre } from './AiBrainAgentTrace';

/** The four non-trace tabs of one loop's screen: journal, goals & identity,
 *  facts and inbox. Together with `AiBrainAgentTrace` these carry everything
 *  the old `AiBrainDetail` tabs could show, at read density. */

const JOURNAL_POLL_MS = 60_000;
const DAY_CHOICES = [1, 3, 7, 30];
/** Bodies are fetched one request per fact, so the sentence preview is capped.
 *  Beyond this the list still works — a fact opens on demand. */
const PREVIEW_CAP = 20;

/** The day-range selector. Real `<button>`s in a labelled group. */
const DayPicker: React.FC<{ days: number; onChange: (d: number) => void }> = ({
  days,
  onChange,
}) => (
  <div role="group" aria-label="Antal dagar" className="inline-flex gap-2">
    {DAY_CHOICES.map((d) => {
      const active = d === days;
      return (
        <button
          key={d}
          type="button"
          aria-pressed={active}
          onClick={() => onChange(d)}
          className="px-2 py-[2px] rounded-full text-[12px] cursor-pointer whitespace-nowrap"
          style={{
            fontFamily: MONO,
            background: 'transparent',
            color: active ? WALL.ground : WALL.inkDim,
            border: `1px solid ${active ? WALL.amber : WALL.rule}`,
            backgroundColor: active ? WALL.amber : 'transparent',
          }}
        >
          {d} d
        </button>
      );
    })}
  </div>
);

// ---------------------------------------------------------------- journal

export const AgentJournal: React.FC<{ agent: string; knownDays: string[] }> = ({
  agent,
  knownDays,
}) => {
  const [days, setDays] = useState(3);
  const fetcher = useCallback(
    (signal: AbortSignal) => fetchJournal(agent, days, signal),
    [agent, days],
  );
  const { data, error, initialLoading } = useAiBrain(fetcher, [agent, days], JOURNAL_POLL_MS);

  const entries = data?.entries ?? [];

  return (
    <div className="flex flex-col gap-3 min-w-0">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <DayPicker days={days} onChange={setDays} />
        <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
          {knownDays.length} {knownDays.length === 1 ? 'dag' : 'dagar'} på disk
          {knownDays.length > 0 ? ` · senast ${formatDay(knownDays[knownDays.length - 1])}` : ''}
        </span>
      </div>

      {initialLoading && !data && (
        <EmptyState why={`hämtar ${days} dagar från /api/agents/…/journal`}>
          Läser journalen…
        </EmptyState>
      )}

      {error && !data && (
        <EmptyState why={error.message}>Kunde inte hämta journalen.</EmptyState>
      )}

      {data && entries.length === 0 && (
        <EmptyState
          why={
            knownDays.length === 0
              ? 'loopen har aldrig skrivit en journalrad'
              : `inget skrivet de senaste ${days} dygnen — prova ett längre spann`
          }
        >
          Inga journalrader i perioden.
        </EmptyState>
      )}

      {entries.map((entry) => (
        <div key={entry.date} className="flex flex-col gap-1 min-w-0">
          <div
            className="text-[12px] uppercase tracking-[0.18em] tabular-nums"
            style={{ fontFamily: SANS, color: WALL.inkFaint }}
          >
            {entry.date}
          </div>
          <div
            className="rounded px-3 py-2 flex flex-col gap-1 min-w-0"
            style={{ background: WALL.raised }}
          >
            {(entry.lines ?? []).map((line, i) => {
              const { time, text } = parseJournalLine(line);
              return (
                <div key={i} className="flex gap-3 min-w-0">
                  <span
                    className="text-[12px] tabular-nums shrink-0"
                    style={{ fontFamily: MONO, color: WALL.inkFaint }}
                  >
                    {time ?? ''}
                  </span>
                  <span
                    className="text-[14px] leading-[1.45] break-words min-w-0"
                    style={{ fontFamily: SANS, color: WALL.ink }}
                  >
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

// ------------------------------------------------------- goals & identity

/** "Mål & Identitet" — the two documents the loop keeps about itself, as
 *  served. Drift over time lives on /ai-brain/charter; this is the current
 *  body, verbatim, which is what the old tab showed. */
export const AgentCharterDocs: React.FC<{ detail: AgentDetailPlus }> = ({ detail }) => {
  const goalRevisions = detail.goals_history?.length ?? 0;
  const identityRevisions = detail.identity_history?.length ?? 0;

  return (
    <div className="flex flex-col gap-5 min-w-0">
      <section className="min-w-0">
        <SectionTitle
          accent={WALL.amber}
          action={<MoreLink href="/ai-brain/charter">charter</MoreLink>}
        >
          Mål · goals.md
        </SectionTitle>
        {detail.goals ? (
          <div className="rounded px-3 py-2 min-w-0" style={{ background: WALL.raised }}>
            <Pre>{detail.goals}</Pre>
          </div>
        ) : (
          <EmptyState why="loopen har inte skrivit goals.md">Inga mål satta.</EmptyState>
        )}
        {goalRevisions > 0 && (
          <p className="text-[12px] mt-1 m-0" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
            {goalRevisions} tidigare {goalRevisions === 1 ? 'version' : 'versioner'}
          </p>
        )}
      </section>

      <section className="min-w-0">
        <SectionTitle accent={WALL.sage}>Identitet · identity.md</SectionTitle>
        {detail.identity ? (
          <div className="rounded px-3 py-2 min-w-0" style={{ background: WALL.raised }}>
            <Pre>{detail.identity}</Pre>
          </div>
        ) : (
          <EmptyState why="loopen har inte skrivit identity.md">
            Ingen identitet nedskriven.
          </EmptyState>
        )}
        {identityRevisions > 0 && (
          <p className="text-[12px] mt-1 m-0" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
            {identityRevisions} tidigare {identityRevisions === 1 ? 'version' : 'versioner'}
          </p>
        )}
      </section>

      {(detail.gaps?.length ?? 0) > 0 && (
        <section className="min-w-0">
          <SectionTitle accent={WALL.rose}>Öppna luckor</SectionTitle>
          <ul className="flex flex-col gap-3 list-none m-0 p-0">
            {(detail.gaps ?? []).map((gap) => (
              <BeliefRow
                key={gap.id}
                density="read"
                muted
                kicker="lucka"
                kickerColor={WALL.rose}
                lines={0}
                meta={gap.why || undefined}
              >
                {gap.question}
              </BeliefRow>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
};

// ------------------------------------------------------------------ facts

/** "Fakta" — the loop's fact files. The list is what it knows; a body opens on
 *  demand, exactly as the old tab did, with a sentence preview above it so the
 *  list reads as beliefs rather than file names. */
export const AgentFacts: React.FC<{
  agent: string;
  factNames: string[];
  detail: AgentDetailPlus;
  now: number;
}> = ({ agent, factNames, detail, now }) => {
  const [bodies, setBodies] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mountedRef = useRef(true);
  const openRef = useRef<string | null>(null);
  const inFlightRef = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      mountedRef.current = false;
      inFlightRef.current?.abort();
    },
    [],
  );

  // The names arrive as an array that is rebuilt on every poll, so the effect
  // keys off their joined text rather than the array identity.
  const namesKey = factNames.join('\u0000');

  // Preview bodies for the first few facts, so the list shows sentences.
  // Failures are silent: a fact that will not load costs its preview line, not
  // the list — it can still be opened, which reports its own error.
  useEffect(() => {
    const names = namesKey ? namesKey.split('\u0000').slice(0, PREVIEW_CAP) : [];
    if (names.length === 0) return;
    const controller = new AbortController();
    fetchFactBodies(agent, names, controller.signal)
      .then((map) => {
        if (!controller.signal.aborted) setBodies((prev) => ({ ...prev, ...map }));
      })
      .catch(() => {
        // Aborted on unmount, or every fact failed — the names still render.
      });
    return () => controller.abort();
  }, [agent, namesKey]);

  const show = async (name: string) => {
    inFlightRef.current?.abort();
    if (open === name) {
      openRef.current = null;
      setOpen(null);
      return;
    }
    openRef.current = name;
    setOpen(name);
    setError(null);
    if (bodies[name] !== undefined) return;

    const controller = new AbortController();
    inFlightRef.current = controller;
    try {
      const result = await fetchFact(agent, name, controller.signal);
      if (!mountedRef.current || openRef.current !== name) return;
      setBodies((prev) => ({ ...prev, [result.name]: result.body }));
    } catch (e) {
      if (!mountedRef.current || openRef.current !== name || controller.signal.aborted) return;
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  if (factNames.length === 0) {
    return (
      <EmptyState why="loopen har inte kallat write_fact — den skriver ned saker först när den lärt sig något">
        Loopen har inga fakta ännu.
      </EmptyState>
    );
  }

  const statsByName = new Map((detail.fact_stats ?? []).map((s) => [s.name, s]));

  return (
    <div className="flex flex-col gap-3 min-w-0">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
          {factNames.length} {factNames.length === 1 ? 'fakta' : 'fakta'}
          {statsByName.size === 0 ? ' · ingen skrivhistorik serverad' : ''}
        </span>
        <MoreLink href="/ai-brain/knowledge">all kunskap</MoreLink>
      </div>

      <ul className="flex flex-col gap-2 list-none m-0 p-0">
        {factNames.map((name) => {
          const stat = statsByName.get(name);
          const body = bodies[name];
          const sentence = factSentence(body, 160);
          const isOpen = open === name;
          return (
            <li key={name} className="flex flex-col gap-1 min-w-0">
              <button
                type="button"
                onClick={() => show(name)}
                aria-expanded={isOpen}
                className="text-left cursor-pointer bg-transparent border-0 p-0 min-w-0 flex flex-col gap-[2px]"
              >
                <span
                  className="text-[12px] break-words"
                  style={{
                    fontFamily: MONO,
                    color: stat ? toneColor(freshnessTone(stat.written_at, now)) : WALL.inkFaint,
                  }}
                >
                  {isOpen ? '▾' : '▸'} {name}
                </span>
                <span
                  className="text-[18px] leading-[1.4] break-words"
                  style={{ fontFamily: SERIF, color: WALL.ink }}
                >
                  {sentence || humanizeFactName(name)}
                </span>
              </button>
              {stat && (
                <Age
                  at={stat.written_at}
                  now={now}
                  suffix={`${stat.writes}× skriven`}
                />
              )}
              {isOpen && (
                <div
                  className="rounded px-3 py-2 overflow-x-auto min-w-0"
                  style={{ background: WALL.raised }}
                >
                  {error ? (
                    <EmptyState why={error}>Kunde inte hämta faktan.</EmptyState>
                  ) : body === undefined ? (
                    <EmptyState why={`hämtar ${name}`}>Läser…</EmptyState>
                  ) : (
                    <Pre>{body}</Pre>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
};

// ------------------------------------------------------------------ inbox

/** "Inkorg" — unread notes other loops have left for this one. */
export const AgentInbox: React.FC<{ notes: Note[]; unread: number }> = ({ notes, unread }) => {
  if (notes.length === 0) {
    return (
      <EmptyState why="inga olästa lappar — en loop lämnar en med send_note, och den försvinner härifrån när loopen läst den">
        Inkorgen är tom.
      </EmptyState>
    );
  }

  return (
    <div className="flex flex-col gap-3 min-w-0">
      <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
        {unread} {unread === 1 ? 'oläst lapp' : 'olästa lappar'}
      </span>
      {notes.map((note) => (
        <article
          key={note.file}
          className="rounded px-3 py-2 flex flex-col gap-1 min-w-0"
          style={{ background: WALL.raised }}
        >
          <header className="flex items-baseline gap-2 flex-wrap">
            <Pill color={WALL.clay} title={note.file}>
              {note.sender}
            </Pill>
            <span
              className="text-[12px] tabular-nums"
              style={{ fontFamily: MONO, color: WALL.inkFaint }}
            >
              {note.created}
            </span>
          </header>
          <Pre>{note.body}</Pre>
        </article>
      ))}
    </div>
  );
};
