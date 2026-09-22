'use client';

import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { type Tone, formatAgo } from '../lib/aiBrain';

/** The shared visual kit for every `/ai-brain` screen.
 *
 *  The wall is a tablet on a wall, read from across the room: warm near-black
 *  ground, one serif voice for what the house understands, a sans for labels
 *  and a mono for every number and identifier so digits do not shimmer between
 *  polls. The palette lives here as plain constants rather than Tailwind theme
 *  tokens: `app/globals.css` belongs to the rest of the site, and these routes
 *  are the only consumers.
 *
 *  Two densities share one language. `density="wall"` is the glanced-at tablet
 *  at 1024×768 — one screen, nothing below the fold, depth behind a link.
 *  `density="read"` is the same palette read up close on a phone, where
 *  scrolling is expected and the detail is the point. Pass the one that matches
 *  the screen; do not invent a third.
 *
 *  This file is owned by the wall stream and is read-only to the knowledge,
 *  loops/charter and agent/system screens: everything they need is exported
 *  here, and anything missing is a request, not a local patch. */

export const WALL = {
  ground: '#12100E',
  raised: '#1A1714',
  ink: '#F4EDE2',
  /** Ink at reading weight for secondary lines. */
  inkDim: 'rgba(244, 237, 226, 0.62)',
  /** Ink for the machine line and other deliberately quiet text. */
  inkFaint: 'rgba(244, 237, 226, 0.38)',
  rule: 'rgba(244, 237, 226, 0.10)',
  amber: '#E0B35F',
  sage: '#8FAE84',
  rose: '#D97A73',
  clay: '#D9895E',
} as const;

/** One font stack for the whole section. The family is loaded by the Google
 *  Fonts link in `app/layout.tsx`; the stack falls back to a system face so
 *  the wall stays legible if that request is slow or blocked.
 *
 *  `SERIF` and `MONO` used to be Fraunces and IBM Plex Mono — a deliberate
 *  three-face mix (serif hero, sans chrome, mono telemetry). Collapsed to one
 *  face on request; the names stay so call sites still say what a given piece
 *  of text *is* (a hero sentence vs. a machine value), even though they now
 *  render identically. */
export const SANS = "'IBM Plex Sans', Inter, system-ui, sans-serif";
export const SERIF = SANS;
export const MONO = SANS;

/** Wall density is the glanced-at tablet; read density is the phone. */
export type Density = 'wall' | 'read';

/** The colour a `Tone` from `lib/aiBrain` wears here. One mapping for every
 *  screen, so a warning is the same amber on the wall and in a detail list. */
export function toneColor(tone: Tone): string {
  switch (tone) {
    case 'ok':
      return WALL.sage;
    case 'warn':
      return WALL.amber;
    case 'error':
      return WALL.rose;
    case 'busy':
      return WALL.clay;
    default:
      return WALL.inkFaint;
  }
}

// ---------------------------------------------------------------- clock

/** The shared clock discipline: run on ai-brain's clock, not the tablet's.
 *
 *  Every timestamp these screens render comes from ai-brain. A wall tablet's
 *  own clock drifts for weeks, which would show a cycle that just ran as
 *  "5 min sedan". Feed this `status.now` each poll and it ticks once a second
 *  against the offset that reading implied.
 *
 *  Returns seconds since the epoch, as ai-brain would report them. */
export function useServerClock(serverNow: number | null | undefined): number {
  // 0 until mounted, deliberately. Seeding from Date.now() renders a clock and
  // a page full of ages into the server HTML, and the client re-renders them a
  // moment later with different text -- a hydration mismatch (React #418) on
  // every screen. Callers treat 0 as "no clock yet" and render a placeholder.
  const [now, setNow] = useState(0);
  // A ref so a new offset does not restart the interval below.
  const offsetRef = useRef(0);

  useEffect(() => {
    if (typeof serverNow !== 'number' || !Number.isFinite(serverNow)) {
      // Still start the clock: an age is better than nothing while the first
      // poll is in flight, and the offset corrects it as soon as it lands.
      setNow((current) => current || Date.now() / 1000);
      return;
    }
    offsetRef.current = serverNow - Date.now() / 1000;
    setNow(Date.now() / 1000 + offsetRef.current);
  }, [serverNow]);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000 + offsetRef.current), 1000);
    return () => clearInterval(id);
  }, []);

  return now;
}

// ---------------------------------------------------------------- atoms

/** Every number and identifier on these screens goes through this. */
export const Mono: React.FC<{ children: React.ReactNode; className?: string }> = ({
  children,
  className = '',
}) => (
  <span className={`tabular-nums ${className}`} style={{ fontFamily: MONO }}>
    {children}
  </span>
);

/** "3 min sedan", in the quiet mono every age on these screens wears.
 *  Renders nothing at all when there is no timestamp — an empty age line is
 *  better than a dash nobody can interpret. */
export const Age: React.FC<{
  at: number | null | undefined;
  now: number;
  /** Extra text after a separating dot, e.g. "4× skriven". */
  suffix?: React.ReactNode;
  className?: string;
}> = ({ at, now, suffix, className = '' }) => {
  // now === 0 is the pre-mount clock (see useServerClock): an age computed
  // against it would read as decades, so the timestamp waits one tick.
  if (at === null || at === undefined || !Number.isFinite(at) || !now) {
    if (!suffix) return null;
    return (
      <span className={`text-[12px] ${className}`} style={{ fontFamily: MONO, color: WALL.inkFaint }}>
        {suffix}
      </span>
    );
  }
  return (
    <span
      className={`text-[12px] tabular-nums ${className}`}
      style={{ fontFamily: MONO, color: WALL.inkFaint }}
    >
      {formatAgo(at, now)}
      {suffix ? <> · {suffix}</> : null}
    </span>
  );
};

/** A small labelled chip. Quiet by default; `tone` gives it a colour. */
export const Pill: React.FC<{
  children: React.ReactNode;
  tone?: Tone;
  /** Override the tone colour outright, for a section accent. */
  color?: string;
  title?: string;
}> = ({ children, tone = 'idle', color, title }) => {
  const c = color ?? toneColor(tone);
  return (
    <span
      title={title}
      className="inline-flex items-center px-2 py-[2px] rounded-full text-[12px] whitespace-nowrap"
      style={{ fontFamily: MONO, color: c, border: `1px solid ${c}`, opacity: 0.85 }}
    >
      {children}
    </span>
  );
};

/** Section heading: small, spaced, quiet — the content is what carries. */
export const SectionTitle: React.FC<{
  children: React.ReactNode;
  accent?: string;
  /** Rendered at the far right of the heading rule, usually a `MoreLink`. */
  action?: React.ReactNode;
}> = ({ children, accent = WALL.inkFaint, action }) => (
  <div className="flex items-baseline justify-between gap-4 mb-2">
    <h2
      className="text-[13px] uppercase tracking-[0.22em] m-0"
      style={{ fontFamily: SANS, color: accent }}
    >
      {children}
    </h2>
    {action}
  </div>
);

/** A section that renders nothing at all when it has nothing to say.
 *
 *  This is the whole graceful-degradation rule in one place: several endpoints
 *  read empty on a freshly deployed brain, and an absent one must cost its own
 *  block and nothing more. On the wall that means the block vanishes; on a
 *  detail screen pass an `empty` node so the reader is told what is missing and
 *  why rather than shown a hole. */
export const Section: React.FC<{
  title: string;
  accent?: string;
  show: boolean;
  children: React.ReactNode;
  className?: string;
  action?: React.ReactNode;
  /** Rendered in place of `children` when `show` is false. Omit to hide. */
  empty?: React.ReactNode;
}> = ({ title, accent, show, children, className = '', action, empty }) => {
  if (!show && !empty) return null;
  return (
    <section className={`min-w-0 ${className}`}>
      <SectionTitle accent={accent} action={action}>
        {title}
      </SectionTitle>
      {show ? children : empty}
    </section>
  );
};

/** What an empty panel says: what is missing, and why it is missing.
 *
 *  `fact_stats`, gaps, loops and usefulness all read empty or thin on a freshly
 *  deployed brain. "Inget ännu" alone reads as a bug; the `why` line is what
 *  turns it back into a state. */
export const EmptyState: React.FC<{
  children: React.ReactNode;
  why?: React.ReactNode;
  className?: string;
}> = ({ children, why, className = '' }) => (
  <div className={`flex flex-col gap-1 ${className}`}>
    <p className="text-[17px] italic m-0" style={{ fontFamily: SERIF, color: WALL.inkDim }}>
      {children}
    </p>
    {why && (
      <p className="text-[12px] m-0" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
        {why}
      </p>
    )}
  </div>
);

/** "fler →" — the wall caps what it shows and puts the rest behind this.
 *  A real `<a href>`, never a clickable div. */
export const MoreLink: React.FC<{
  href: string;
  children?: React.ReactNode;
  className?: string;
}> = ({ href, children = 'fler', className = '' }) => (
  <Link
    href={href}
    className={`text-[13px] no-underline hover:underline whitespace-nowrap ${className}`}
    style={{ fontFamily: SANS, color: WALL.inkDim }}
  >
    {children} →
  </Link>
);

/** Leave this screen — a rounded ✕, right of the header. Every nav-mapped
 *  screen closes back to the wall; the wall itself closes out to the site. */
export const CloseButton: React.FC<{ href?: string; label?: string }> = ({
  href = '/ai-brain',
  label = 'Stäng',
}) => (
  <Link
    href={href}
    aria-label={label}
    title={label}
    className="flex items-center justify-center w-7 h-7 rounded-full no-underline shrink-0"
    style={{ fontFamily: SANS, color: WALL.inkDim, border: `1px solid ${WALL.inkFaint}` }}
  >
    ✕
  </Link>
);

// ---------------------------------------------------------------- belief row

/** One belief, gap or conclusion: the sentence is the content.
 *
 *  The sentence is clamped to two lines rather than trusted to be short — the
 *  wall's failure mode was a fact body pasted four lines deep, and a character
 *  cut alone cannot know the column width. `lines={0}` turns the clamp off for
 *  a detail screen that wants the whole body. */
export const BeliefRow: React.FC<{
  children: React.ReactNode;
  /** The quiet mono line beneath: age, write count, source. */
  meta?: React.ReactNode;
  /** Small uppercase word before the sentence, e.g. "lucka". */
  kicker?: string;
  kickerColor?: string;
  /** Dimmed and italic — for a question rather than a statement. */
  muted?: boolean;
  density?: Density;
  /** Lines before the clamp bites; 0 disables it. */
  lines?: number;
  /** Renders the row as a link to its own detail. */
  href?: string;
}> = ({
  children,
  meta,
  kicker,
  kickerColor = WALL.amber,
  muted = false,
  density = 'wall',
  lines = 2,
  href,
}) => {
  const size = density === 'wall' ? 'text-[19px]' : 'text-[18px]';
  const clamp: React.CSSProperties =
    lines > 0
      ? {
          display: '-webkit-box',
          WebkitBoxOrient: 'vertical',
          WebkitLineClamp: lines,
          overflow: 'hidden',
        }
      : {};

  const sentence = (
    <p
      className={`${size} leading-[1.34] m-0 ${muted ? 'italic' : ''}`}
      style={{
        fontFamily: SERIF,
        color: muted ? WALL.inkDim : WALL.ink,
        fontWeight: 400,
        ...clamp,
      }}
    >
      {kicker && (
        <span
          className="not-italic text-[11px] uppercase tracking-[0.18em] mr-2 align-middle"
          style={{ fontFamily: SANS, color: kickerColor, opacity: 0.8 }}
        >
          {kicker}
        </span>
      )}
      {children}
    </p>
  );

  return (
    <li className="flex flex-col gap-[2px] min-w-0 list-none">
      {href ? (
        <Link href={href} className="no-underline" style={{ color: 'inherit' }}>
          {sentence}
        </Link>
      ) : (
        sentence
      )}
      {meta && (
        <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
          {meta}
        </span>
      )}
    </li>
  );
};

// ---------------------------------------------------------------- approvals

/** Approve/reject as they appear on every screen.
 *
 *  Deliberately inert: the ai-brain API is read-only by design and approval
 *  happens by reacting in Slack. Real disabled `<button>`s rather than
 *  decorations, so the affordance is honest, focusable and explains itself —
 *  pressing nothing is the point. */
export const SlackMirrorButtons: React.FC<{ compact?: boolean }> = ({ compact = false }) => (
  <div className="flex items-center gap-2 shrink-0">
    <button
      type="button"
      disabled
      title="Godkänn med ✅ i Slack — den här vyn är skrivskyddad"
      className={`rounded-full cursor-default ${compact ? 'px-2 py-[1px] text-[12px]' : 'px-3 py-1 text-[13px]'}`}
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
      title="Avslå med ❌ i Slack — den här vyn är skrivskyddad"
      className={`rounded-full cursor-default ${compact ? 'px-2 py-[1px] text-[12px]' : 'px-3 py-1 text-[13px]'}`}
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

// ---------------------------------------------------------------- chrome

/** The routes the nav offers, in reading order. Exported so a screen can mark
 *  itself current without re-declaring the map. */
export const WALL_NAV: { href: string; label: string }[] = [
  { href: '/ai-brain', label: 'Väggen' },
  { href: '/ai-brain/feed', label: 'Flöde' },
  { href: '/ai-brain/knowledge', label: 'Kunskap' },
  { href: '/ai-brain/loops', label: 'Slingor' },
  { href: '/ai-brain/charter', label: 'Charter' },
  { href: '/ai-brain/system', label: 'System' },
];

/** The one nav. Current route is marked, not linked away from. */
export const WallNav: React.FC<{ current?: string; className?: string }> = ({
  current,
  className = '',
}) => (
  <nav className={`flex items-baseline gap-4 min-w-0 ${className}`} aria-label="AI-hjärnan">
    {WALL_NAV.map((item) => {
      const active = item.href === current;
      return (
        <Link
          key={item.href}
          href={item.href}
          aria-current={active ? 'page' : undefined}
          className="text-[12px] uppercase tracking-[0.18em] no-underline hover:underline whitespace-nowrap"
          style={{ fontFamily: SANS, color: active ? WALL.ink : WALL.inkFaint }}
        >
          {item.label}
        </Link>
      );
    })}
  </nav>
);

/** Machine conditions, sitting with the telemetry where they belong.
 *
 *  A cycle status is never the hero. "tom budget" is a condition of the
 *  machine, so it renders here in 12px beside the machine line rather than in
 *  38px serif as what the house understands. */
export const ConditionStrip: React.FC<{
  conditions: { id: string; label: string; tone: Tone }[];
  className?: string;
}> = ({ conditions, className = '' }) => {
  if (conditions.length === 0) return null;
  return (
    <div className={`flex flex-wrap items-center gap-2 ${className}`}>
      {conditions.map((c) => (
        <Pill key={c.id} tone={c.tone}>
          {c.label}
        </Pill>
      ))}
    </div>
  );
};

/** The 12px machine line: everything deliberately unreadable from across the
 *  room, for the person standing at the tablet.
 *
 *  Clamped to two lines. The deployed wall wrapped to three because it printed
 *  ledger keys with no traffic at all — drop those at the source with
 *  `activeLedgerKeys` and keep this as the backstop. */
export const MachineLine: React.FC<{
  bits: string[];
  className?: string;
  /** Lines before the clamp bites. */
  lines?: number;
}> = ({ bits, className = '', lines = 2 }) => (
  <p
    className={`text-[12px] leading-[1.5] m-0 ${className}`}
    style={{
      fontFamily: MONO,
      color: WALL.inkFaint,
      display: '-webkit-box',
      WebkitBoxOrient: 'vertical',
      WebkitLineClamp: lines,
      overflow: 'hidden',
    }}
    title={bits.join('  ·  ')}
  >
    {bits.join('  ·  ') || 'ai-brain: inga data'}
  </p>
);

/** The page shell every `/ai-brain` screen sits in.
 *
 *  Owns the ground colour, the nav, the header line and the footer rule, so no
 *  screen re-declares them and they cannot drift apart.
 *
 *  `density="wall"` also owns the wall's hard promise: at ≥1024 the shell is
 *  exactly one viewport tall with its overflow hidden, so nothing important can
 *  quietly end up below the fold. Content that does not fit must be cut and
 *  linked to, not scrolled to. Below 1024 (a phone held up to the tablet) it
 *  goes back to `min-h-screen` and scrolls normally. `density="read"` always
 *  scrolls — those screens are read up close. */
export const WallShell: React.FC<{
  children: React.ReactNode;
  density?: Density;
  /** Left of the header, next to the nav. Only needed when the screen isn't
   *  one of `WALL_NAV`'s routes — the nav already highlights the current one,
   *  so a nav-mapped screen doesn't repeat its own name here. */
  title?: string;
  /** Route to mark in the nav, e.g. '/ai-brain/loops'. */
  current?: string;
  /** Right of the header — the clock and state on the wall. */
  headerRight?: React.ReactNode;
  /** Far right of the header — a `CloseButton` out of this screen. */
  close?: React.ReactNode;
  /** The footer: conditions and the machine line. */
  footer?: React.ReactNode;
}> = ({ children, density = 'wall', title, current, headerRight, close, footer }) => {
  const wall = density === 'wall';
  return (
    <main
      className={
        wall
          ? 'min-h-screen lg:h-screen lg:overflow-hidden w-full px-6 lg:px-8 py-4 lg:py-5 flex flex-col gap-3'
          : 'min-h-screen w-full px-5 sm:px-8 py-5 flex flex-col gap-5'
      }
      style={{ background: WALL.ground, color: WALL.ink, fontFamily: SANS }}
    >
      <header className="flex items-center justify-between gap-6 shrink-0 min-w-0">
        <div className="flex items-baseline gap-5 min-w-0">
          {title && (
            <h1
              className={`${wall ? 'text-[18px]' : 'text-[16px]'} tracking-[0.28em] uppercase m-0 whitespace-nowrap`}
              style={{ fontFamily: SANS, color: WALL.inkDim, fontWeight: 500 }}
            >
              {title}
            </h1>
          )}
          <WallNav current={current} className="hidden md:flex" />
        </div>
        <div className="flex items-center gap-4 shrink-0">
          {headerRight}
          {close}
        </div>
      </header>

      {children}

      {footer && (
        <footer
          className="mt-auto pt-2 shrink-0 flex flex-col gap-2"
          style={{ borderTop: `1px solid ${WALL.rule}` }}
        >
          {footer}
        </footer>
      )}
    </main>
  );
};

export default WALL;
