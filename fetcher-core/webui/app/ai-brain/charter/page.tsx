import type { Metadata } from 'next';
import AiBrainCharter from '../../components/AiBrainCharter';

/** `/ai-brain/charter` — målen hjärnan satt själv, driften i hur den beskriver
 *  sig, och nyttan: hur stor del av varven som gav något verkligt.
 *
 *  Server component so the route can carry metadata; the polling lives in the
 *  client component. */

export const metadata: Metadata = {
  title: 'Charter',
};

export default function AiBrainCharterPage() {
  return <AiBrainCharter />;
}
