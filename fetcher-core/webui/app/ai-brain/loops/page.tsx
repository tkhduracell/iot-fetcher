import type { Metadata } from 'next';
import AiBrainLoops from '../../components/AiBrainLoops';

/** `/ai-brain/loops` — slingorna: ämnen hjärnan kommer tillbaka till, varv för
 *  varv, med hela förslagshistoriken och vad upprepningen har kostat.
 *
 *  Server component so the route can carry metadata; the polling lives in the
 *  client component. */

export const metadata: Metadata = {
  title: 'Slingor',
};

export default function AiBrainLoopsPage() {
  return <AiBrainLoops />;
}
