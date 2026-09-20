import type { Metadata } from 'next';
import AiBrainAgentScreen from '../../../components/AiBrainAgentScreen';

/** `/ai-brain/agent/[name]` — one loop up close: its spår, journal, mål &
 *  identitet, fakta and inkorg, with the state the old agent card carried as
 *  the header.
 *
 *  `[name]` is a loop name from `/api/agents`. It is not validated here: the
 *  set of loops is whatever ai-brain is running right now, so the client
 *  component compares the name against the live list and renders a real
 *  "ingen sådan loop" state — with the names that do exist — rather than a
 *  build-time 404 that would go stale the moment an expert is added.
 *
 *  A server component so the route can carry metadata; everything that polls
 *  lives in the client component. */

export async function generateMetadata({
  params,
}: {
  params: Promise<{ name: string }>;
}): Promise<Metadata> {
  const { name } = await params;
  return { title: `${decodeURIComponent(name)} · Hjärnan` };
}

export default async function AiBrainAgentPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = await params;
  return <AiBrainAgentScreen name={decodeURIComponent(name)} />;
}
