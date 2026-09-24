'use client';

import React, { useState } from 'react';
import { type Review, formatAgo, verdictLabel, verdictTone } from '../lib/aiBrain';
import { MONO, Pill, SANS, WALL } from './AiBrainWallTheme';

/** The brain's last verdict on an expert, as a small badge.
 *
 *  `last_review` only exists on `/api/agents/{name}`, and only for an expert —
 *  the brain has no standing to review itself. Every call site already has
 *  the review (or knows it does not), so this component only renders, never
 *  fetches. `findings` is the brain's own sentence about *why*; it is real
 *  content, not a code, so it goes in a title tooltip everywhere space is
 *  tight and can expand inline where there is room. */
export const ReviewBadge: React.FC<{
  review: Review | null | undefined;
  now?: number;
  /** Renders `findings` as an expandable line below the badge instead of only
   *  a hover tooltip — for a header or panel with room to spare. */
  expandable?: boolean;
  className?: string;
}> = ({ review, now, expandable = false, className = '' }) => {
  const [open, setOpen] = useState(false);
  if (!review) return null;

  const tone = verdictTone(review.verdict);
  const age = typeof now === 'number' && now ? formatAgo(review.ts, now) : null;
  const title = [verdictLabel(review.verdict), age, review.findings].filter(Boolean).join(' · ');

  if (!expandable) {
    return (
      <Pill tone={tone} title={title || undefined} className={className}>
        {verdictLabel(review.verdict)}
      </Pill>
    );
  }

  return (
    <span className={`inline-flex flex-col gap-1 min-w-0 ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="inline-flex items-center gap-1 bg-transparent p-0 border-0 cursor-pointer"
        title={review.findings || undefined}
      >
        <Pill tone={tone}>{verdictLabel(review.verdict)}</Pill>
        {age && (
          <span className="text-[11px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
            {age}
          </span>
        )}
      </button>
      {open && review.findings && (
        <p
          className="text-[13px] leading-[1.4] m-0 max-w-[48ch]"
          style={{ fontFamily: SANS, color: WALL.inkDim }}
        >
          {review.findings}
        </p>
      )}
    </span>
  );
};

export default ReviewBadge;
