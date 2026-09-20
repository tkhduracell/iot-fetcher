import type { Metadata } from 'next';
import AiBrainSystemScreen from '../../components/AiBrainSystemScreen';

/** `/ai-brain/system` — the machine behind the wall: the day's ledger keys and
 *  what they have spent, the model chain, the loops and their heartbeats, the
 *  rate limits, Slack, the pause file, the memory root and the raw proposal
 *  ledger.
 *
 *  This is where the old blue-card `AiBrainSupervisor` went. Nothing it could
 *  show is missing here; the quota bars changed direction — see the component.
 *
 *  A server component so the route can carry metadata; the polling lives in
 *  the client component. */

export const metadata: Metadata = {
  title: 'System · Hjärnan',
};

export default function AiBrainSystemPage() {
  return <AiBrainSystemScreen />;
}
