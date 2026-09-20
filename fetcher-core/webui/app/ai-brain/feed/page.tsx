import type { Metadata } from 'next';
import AiBrainFeed from '../../components/AiBrainFeed';

/** `/ai-brain/feed` — every loop's rounds merged into one chronological
 *  stream: what the house is thinking about right now, across all six loops,
 *  in the order it happened.
 *
 *  Server component so the route can carry metadata; the polling lives in the
 *  client component. */

export const metadata: Metadata = {
  title: 'Flöde',
};

export default function AiBrainFeedPage() {
  return <AiBrainFeed />;
}
