'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  type CycleTrace,
  type ParsedResult,
  type RoundTrace,
  type ToolCall,
  type ToolResult,
  decodeEscapes,
  fetchTrace,
  formatAgo,
  formatClock,
  parseResultPreview,
  shortModel,
  splitLongFields,
  splitPreviewSuffix,
  statusTone,
  summarizeArgs,
  summarizeResult,
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

/** Characters per second the thinking text types itself out at. Fast enough
 *  that a long thought is not a wait, slow enough to read as thinking rather
 *  than as a paint glitch. */
const TYPE_CPS = 90;
/** One tick per frame is wasted work for text this slow; 20/s is plenty. */
const TYPE_TICK_MS = 50;

/** How much of `text` to show right now, typing it out on mount.
 *
 *  Nothing here is really streaming: the round arrived complete, over SSE or a
 *  poll. This replays it as if it were not, because watching the house think
 *  is the point of the feed — a wall of finished text says the same thing
 *  without ever looking alive.
 *
 *  Re-reveals only when the text itself changes, so a re-render (a sibling
 *  round arriving, a poll landing) never restarts a thought mid-sentence.
 *  Honours `prefers-reduced-motion` by showing everything at once. */
export function useTypewriter(text: string, animate = true): string {
  const [shown, setShown] = useState(text.length);
  const doneRef = useRef<string | null>(null);

  useEffect(() => {
    if (doneRef.current === text) {
      setShown(text.length);
      return;
    }
    const reduced =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (!animate || reduced || text.length === 0) {
      doneRef.current = text;
      setShown(text.length);
      return;
    }

    setShown(0);
    const step = Math.max(1, Math.round((TYPE_CPS * TYPE_TICK_MS) / 1000));
    const id = setInterval(() => {
      setShown((n) => {
        const next = n + step;
        if (next >= text.length) {
          clearInterval(id);
          doneRef.current = text;
          return text.length;
        }
        return next;
      });
    }, TYPE_TICK_MS);
    return () => clearInterval(id);
  }, [text, animate]);

  return text.slice(0, shown);
}

/** Text clamped to two lines by default, click to read the rest — the
 *  model's own words (a round's `thinking` or `text`) before its tool calls,
 *  or during a live typewriter reveal. */
/** Cheap pre-check for whether `text` is even worth measuring: short,
 *  newline-free text can never overflow a two-line clamp regardless of
 *  container width, so this skips the layout read below for the common case
 *  (a short tool-call arg summary, a one-line note) without waiting a frame.
 *  ~160 chars is roughly two lines at this font size in the narrowest column
 *  this renders in; a false positive here just costs one extra
 *  scrollHeight/clientHeight comparison, never a wrong answer. */
function likelyOverflows(text: string): boolean {
  if (text.length > 160) return true;
  return (text.match(/\n/g)?.length ?? 0) > 1;
}

export const ClampedText: React.FC<{ children: string; italic?: boolean; color?: string }> = ({
  children,
  italic = false,
  color = WALL.ink,
}) => {
  const [expanded, setExpanded] = useState(false);
  // null = not yet measured. Starts from the cheap pre-check so short text
  // never shows a "mer" button it would immediately prove unnecessary; the
  // layout effect below then confirms (or corrects) it against the real
  // rendered height.
  const [overflows, setOverflows] = useState(() => likelyOverflows(children));
  const ref = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (expanded) return;
    const el = ref.current;
    if (!el) return;
    // `-webkit-line-clamp` clips scrollHeight along with clientHeight in
    // this engine -- once the clamp is active, both read the same 2-line
    // height regardless of how much text is actually behind it, so
    // comparing them here would always say "no overflow". Measuring instead
    // against a fixed 2-line pixel threshold (line-height x 2, read from the
    // element's own computed style so a font-size change elsewhere in this
    // file cannot silently desync it) works because the *content* behind the
    // clamp is still laid out at full height for line-height purposes; only
    // the box's own reported height is clipped.
    const lineHeight = parseFloat(getComputedStyle(el).lineHeight);
    if (!Number.isFinite(lineHeight) || lineHeight <= 0) return;
    const twoLines = lineHeight * 2;
    setOverflows(el.scrollHeight > twoLines + 1); // +1: sub-pixel rounding
  }, [children, expanded]);

  return (
    <div className="flex flex-col items-start gap-1 min-w-0">
      <pre
        ref={ref}
        className={`text-[12px] leading-[1.55] whitespace-pre-wrap break-words m-0 w-full ${italic ? 'italic' : ''}`}
        style={{
          fontFamily: MONO,
          color,
          ...(expanded
            ? {}
            : {
                display: '-webkit-box',
                WebkitBoxOrient: 'vertical',
                WebkitLineClamp: 2,
                overflow: 'hidden',
              }),
        }}
      >
        {children}
      </pre>
      {overflows && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
          className="cursor-pointer bg-transparent border-0 p-0 underline text-[11px]"
          style={{ fontFamily: SANS, color: WALL.amber }}
        >
          {expanded ? 'mindre' : 'mer'}
        </button>
      )}
    </div>
  );
};

/** What the model reasoned before it answered, typed out as it is read.
 *
 *  Dimmed and italic, above the answer: this is the model talking to itself,
 *  and it must never read as something the house is telling you. Clamped to
 *  two lines like any other thought text — expanding while still typing
 *  simply shows the same in-progress string unclamped. */
export const ThinkingView: React.FC<{ thinking: string; animate?: boolean }> = ({
  thinking,
  animate = true,
}) => {
  const shown = useTypewriter(thinking, animate);
  const typing = shown.length < thinking.length;
  return (
    <div className="pl-3 flex flex-col gap-[1px]" style={{ borderLeft: `2px solid ${WALL.inkFaint}` }}>
      <div
        className="text-[12px] uppercase tracking-[0.18em]"
        style={{ fontFamily: SANS, color: WALL.inkFaint }}
      >
        tänker
      </div>
      <ClampedText italic color={WALL.inkFaint}>
        {typing ? `${shown}▌` : shown}
      </ClampedText>
    </div>
  );
};

/** A key/value pair in the expanded view's small property list — everything
 *  in a parsed result that is not one of the long multi-line string fields
 *  `ExpandedBody` renders separately. */
const KeyValueRow: React.FC<{ name: string; value: unknown }> = ({ name, value }) => {
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  return (
    <div className="text-[12px] leading-[1.5] break-words" style={{ fontFamily: MONO, color: WALL.inkDim }}>
      <span style={{ color: WALL.inkFaint }}>{name}:</span> {text}
    </div>
  );
};

/** ~20 lines of monospace before the box scrolls instead of growing — long
 *  enough to read a real body, short enough that one huge result cannot push
 *  the rest of the round off screen.
 *
 *  `decode` controls whether `decodeEscapes` runs on `text` first: a string
 *  that came out of `JSON.parse` (the parsed-result path) is *already*
 *  unescaped — its `\n` is a real newline, not two characters — and running
 *  `decodeEscapes` on it again would wrongly turn a literal `\n` that
 *  appears in actual source code (e.g. a `code_read` body containing a
 *  Python string literal `"\n"`) into a second real newline. Only the
 *  raw-text fallback (a preview that never reached `JSON.parse` at all,
 *  because a truncation cut it mid-token) is still escaped and needs it. */
const ExpandedTextBlock: React.FC<{ text: string; decode?: boolean }> = ({
  text,
  decode = false,
}) => (
  <div
    className="overflow-auto rounded px-2 py-1"
    style={{ background: WALL.ground, maxHeight: '20.5em' }}
  >
    <Pre>{decode ? decodeEscapes(text) : text}</Pre>
  </div>
);

/** The parsed, pretty-printed form of a tool result: long text fields as
 *  scrollable monospace blocks, everything else as a small key/value list,
 *  and the `…[+N]` truncation note carried over verbatim rather than
 *  re-derived. Falls back to the raw text, monospace and escape-decoded,
 *  when the body did not parse as JSON at all. */
const ExpandedBody: React.FC<{ parsed: ParsedResult }> = ({ parsed }) => {
  if (!parsed.parsed || parsed.json === null || typeof parsed.json !== 'object') {
    // The suffix is stripped here even in the raw-text fallback: a `…[+N]`
    // cut landing mid-JSON-string still means "N characters were dropped",
    // and that is worth saying as the same note rather than left glued onto
    // unparsed text as a stray trailer. This text never reached JSON.parse,
    // so it is still escaped -- decode=true is correct here.
    const { body, droppedChars } = splitPreviewSuffix(parsed.raw);
    return (
      <div className="flex flex-col gap-1">
        <ExpandedTextBlock text={body} decode />
        {droppedChars !== null && <TruncationNote droppedChars={droppedChars} />}
      </div>
    );
  }

  if (Array.isArray(parsed.json)) {
    return (
      <div className="flex flex-col gap-1">
        {parsed.json.map((item, i) => (
          <KeyValueRow key={i} name={String(i)} value={item} />
        ))}
        {parsed.droppedChars !== null && (
          <TruncationNote droppedChars={parsed.droppedChars} />
        )}
      </div>
    );
  }

  // splitLongFields expects an already-JSON.parse'd object, which parsed.json
  // is here -- see its own doc comment for why that matters.
  const { long: longFields, short: shortFields } = splitLongFields(
    parsed.json as Record<string, unknown>,
  );

  return (
    <div className="flex flex-col gap-2">
      {shortFields.length > 0 && (
        <div className="flex flex-col gap-[1px]">
          {shortFields.map(([k, v]) => (
            <KeyValueRow key={k} name={k} value={v} />
          ))}
        </div>
      )}
      {longFields.map(([k, v]) => (
        <div key={k} className="flex flex-col gap-[1px]">
          <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
            {k}:
          </span>
          <ExpandedTextBlock text={v} />
        </div>
      ))}
      {parsed.droppedChars !== null && <TruncationNote droppedChars={parsed.droppedChars} />}
    </div>
  );
};

const TruncationNote: React.FC<{ droppedChars: number }> = ({ droppedChars }) => (
  <p className="text-[12px] m-0 italic" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
    trunkerad, {droppedChars.toLocaleString('sv-SE')} tecken till
  </p>
);

/** One tool call, collapsed to a single scannable line by default:
 *  `→ code_read  pool-pump-planner/vm.go:200–250   ✓ 51 rader`. Clicking it
 *  expands the args and the pretty-printed result beneath — this is the
 *  entire fix for the old two-block-per-call, wall-of-escaped-JSON trace. */
export const ToolCallLine: React.FC<{ call: ToolCall; result?: ToolResult }> = ({
  call,
  result,
}) => {
  const [expanded, setExpanded] = useState(false);
  const argsSummary = summarizeArgs(call.name, call.args);
  const { ok, status, size } = result
    ? summarizeResult(call.name, result)
    : { ok: true, status: '', size: '' };
  const parsed = result ? parseResultPreview(result.result_preview ?? '') : null;
  // A name+args-derived id collides whenever two calls in the same round
  // share both (e.g. two code_read calls after a retry) -- useId is unique
  // per mounted instance regardless of content.
  const detailId = React.useId();

  return (
    <div className="flex flex-col gap-1 min-w-0">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={detailId}
        onClick={() => setExpanded((v) => !v)}
        className="flex items-baseline gap-2 flex-wrap text-left cursor-pointer bg-transparent border-0 p-0 min-w-0 w-full"
      >
        <span className="text-[12px] whitespace-nowrap" style={{ fontFamily: MONO, color: WALL.amber }}>
          → {call.name}
        </span>
        {argsSummary && (
          <span className="text-[12px] break-words" style={{ fontFamily: MONO, color: WALL.inkDim }}>
            {argsSummary}
          </span>
        )}
        {result && (
          <span
            className="text-[12px] whitespace-nowrap"
            style={{ fontFamily: MONO, color: ok ? WALL.sage : WALL.rose }}
          >
            {ok ? `✓ ${size}` : `✗ ${status}`}
          </span>
        )}
        <span
          className="text-[11px] ml-auto shrink-0"
          style={{ fontFamily: SANS, color: WALL.inkFaint }}
          aria-hidden
        >
          {expanded ? '▾' : '▸'}
        </span>
      </button>

      {expanded && (
        <div
          id={detailId}
          className="pl-3 flex flex-col gap-2 min-w-0"
          style={{ borderLeft: `2px solid ${WALL.amber}` }}
        >
          {Object.entries(call.args ?? {}).length > 0 && (
            <div className="flex flex-col gap-[1px]">
              {Object.entries(call.args ?? {}).map(([k, v]) => (
                <KeyValueRow key={k} name={k} value={v} />
              ))}
            </div>
          )}
          {parsed && (
            <div className="flex flex-col gap-1">
              <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.sage }}>
                ← {call.name}
              </span>
              <ExpandedBody parsed={parsed} />
            </div>
          )}
        </div>
      )}
    </div>
  );
};

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

      <div className="flex flex-col gap-1 min-w-0">
        {round.thinking && <ThinkingView thinking={round.thinking} />}
        {round.text && <ClampedText>{round.text}</ClampedText>}
        {calls.map((call, j) => (
          // Calls and results are appended in lockstep in loop.py (one
          // result per call, same order, same round) -- index is the pairing
          // key, since neither carries an id of its own across the wire.
          <ToolCallLine key={`${call.name}-${j}`} call={call} result={results[j]} />
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
