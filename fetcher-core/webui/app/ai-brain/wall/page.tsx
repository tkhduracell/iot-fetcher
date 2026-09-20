import { permanentRedirect } from 'next/navigation';

/** `/ai-brain/wall` → `/ai-brain` (308).
 *
 *  The wall lived here for one release and the tablet on the kitchen wall is
 *  bookmarked to it, so the route stays as a permanent redirect rather than
 *  becoming a 404 nobody is standing next to. Nothing renders — the redirect
 *  throws. */

export default function AiBrainWallRedirect(): never {
  permanentRedirect('/ai-brain');
}
