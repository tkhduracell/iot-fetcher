import type { Metadata } from 'next';
import AiBrainWall from '../components/AiBrainWall';

/** `/ai-brain` — the wall. What the house's brain understands now, what waits
 *  on a ✅, what it carried out, what it keeps coming back to, and one line of
 *  machine state.
 *
 *  This route used to be the blue-card supervisor/agent-card/detail page; that
 *  language is gone and everything it could show now has a home of its own:
 *  facts on /ai-brain/knowledge, loops and proposal history on /ai-brain/loops,
 *  goals and identity on /ai-brain/charter, one agent's trace, journal, facts
 *  and inbox on /ai-brain/agent/[name], and the ledger, model chain, settings
 *  and Slack state on /ai-brain/system.
 *
 *  The page is a server component so the route can carry metadata; everything
 *  that polls lives in the client component. */

export const metadata: Metadata = {
  title: 'Hjärnan',
};

export default function AiBrainPage() {
  return <AiBrainWall />;
}
