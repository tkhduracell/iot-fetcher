import type { Metadata } from 'next';
import AiBrainKnowledge from '../../components/AiBrainKnowledge';

/** `/ai-brain/knowledge` — the whole fact base, read up close.
 *
 *  The wall's "Vet om huset" column links here: it shows three loops' newest
 *  four facts, this shows every fact every loop has written, plus the luckor,
 *  the facts that are starting to rot and each loop's distance from the
 *  40-fact compaction cap.
 *
 *  Server component so the route carries metadata; everything that polls lives
 *  in the client component. */

export const metadata: Metadata = {
  title: 'Kunskap · Hjärnan',
};

export default function AiBrainKnowledgePage() {
  return <AiBrainKnowledge />;
}
