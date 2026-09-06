/** A single news story, normalised across RSS feeds and scraped pages. */
export type NewsItem = {
  title: string;
  /** May be '' — some sources give headlines only. The prompt tolerates it. */
  summary: string;
  url: string;
  /** null when the source publishes no usable timestamp (Sydsvenskan teasers). */
  publishedAt: Date | null;
  /** More than one entry after dedup, so the script can cite both outlets. */
  sources: string[];
  /** Feed categories, lowercased. Used to drop listings that are not news. */
  categories: string[];
};

/**
 * Per-source outcome. A failed source yields empty items and a non-null error
 * rather than throwing — one dead source must never kill the briefing, but the
 * failure still has to reach the UI instead of being silently swallowed.
 */
export type SourceResult = {
  source: string;
  items: NewsItem[];
  error: string | null;
};
