/** The only markdown the models actually reach for in a round's text:
 *  `**bold**`, `*italic*` and `` `code` ``. Everything else stays literal. Code wins over
 *  bold, so `**` inside backticks is shown as typed. Italic needs a non-space
 *  just inside each star, so `* bullet` and `2 * 3` stay literal. */
export type InlineSpan = { kind: 'text' | 'bold' | 'italic' | 'code'; text: string };

const PATTERN = /`([^`\n]+)`|\*\*([^*\n](?:[^\n]*?[^*\n])?)\*\*|(?<!\*)\*([^*\s](?:[^*\n]*?[^*\s])?)\*(?!\*)/g;

export const parseInlineMarkdown = (src: string): InlineSpan[] => {
  const spans: InlineSpan[] = [];
  let last = 0;
  for (const m of src.matchAll(PATTERN)) {
    const at = m.index ?? 0;
    if (at > last) spans.push({ kind: 'text', text: src.slice(last, at) });
    spans.push(
      m[1] !== undefined
        ? { kind: 'code', text: m[1] }
        : m[2] !== undefined
          ? { kind: 'bold', text: m[2] }
          : { kind: 'italic', text: m[3] },
    );
    last = at + m[0].length;
  }
  if (last < src.length) spans.push({ kind: 'text', text: src.slice(last) });
  return spans;
};
