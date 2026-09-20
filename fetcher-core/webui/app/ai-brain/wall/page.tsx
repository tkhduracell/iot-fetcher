import type { Metadata } from 'next';
import AiBrainWall from '../../components/AiBrainWall';

/** Always-on wall tablet view of the ai-brain. The page itself is a server
 *  component so the route can carry metadata; everything that polls lives in
 *  the client component below. */

export const metadata: Metadata = {
  title: 'Hjärnan · vägg',
};

export default function AiBrainWallPage() {
  return <AiBrainWall />;
}
