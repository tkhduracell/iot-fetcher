'use client';

import React from 'react';

/** Shared look of the wall view.
 *
 *  The wall is a tablet on a wall, read from across the room: warm near-black
 *  ground, one serif voice for what the house understands, a sans for labels
 *  and a mono for every number and identifier so digits do not shimmer between
 *  polls. The palette lives here as plain constants rather than Tailwind theme
 *  tokens: `app/globals.css` belongs to the rest of the site, and this view is
 *  the only consumer. */

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
} as const;

/** Font stacks. The families themselves are loaded by the Google Fonts link in
 *  `app/layout.tsx`; each stack falls back to a system face so the wall stays
 *  legible if that request is slow or blocked. */
export const SERIF = "'Fraunces', Georgia, 'Times New Roman', serif";
export const SANS = "'IBM Plex Sans', Inter, system-ui, sans-serif";
export const MONO = "'IBM Plex Mono', ui-monospace, 'SF Mono', monospace";

/** Every number and identifier on the wall goes through this. */
export const Mono: React.FC<{ children: React.ReactNode; className?: string }> = ({
  children,
  className = '',
}) => (
  <span className={`tabular-nums ${className}`} style={{ fontFamily: MONO }}>
    {children}
  </span>
);

/** Section heading: small, spaced, quiet — the content is what carries. */
export const SectionTitle: React.FC<{ children: React.ReactNode; accent?: string }> = ({
  children,
  accent = WALL.inkFaint,
}) => (
  <h2
    className="text-[13px] uppercase tracking-[0.22em] mb-3"
    style={{ fontFamily: SANS, color: accent }}
  >
    {children}
  </h2>
);

/** A section that renders nothing at all when it has nothing to say.
 *
 *  This is the whole graceful-degradation rule of the wall in one place: the
 *  backend endpoints behind several sections are still landing, and a missing
 *  one must cost its own block and nothing more. */
export const Section: React.FC<{
  title: string;
  accent?: string;
  show: boolean;
  children: React.ReactNode;
  className?: string;
}> = ({ title, accent, show, children, className = '' }) => {
  if (!show) return null;
  return (
    <section className={className}>
      <SectionTitle accent={accent}>{title}</SectionTitle>
      {children}
    </section>
  );
};

export default WALL;
