/** The only markdown the models actually reach for in a round's text:
 *  `**bold**` and `` `code` ``. Everything else stays literal. Code wins over
 *  bold, so `**` inside backticks is shown as typed. */
export type InlineSpan = { kind: 'text' | 'bold' | 'code'; text: string };

const PATTERN = /`([^`\n]+)`|\*\*([^*\n](?:[^\n]*?[^*\n])?)\*\*/g;

export const parseInlineMarkdown = (src: string): InlineSpan[] => {
  const spans: InlineSpan[] = [];
  let last = 0;
  for (const m of src.matchAll(PATTERN)) {
    const at = m.index ?? 0;
    if (at > last) spans.push({ kind: 'text', text: src.slice(last, at) });
    spans.push(m[1] !== undefined ? { kind: 'code', text: m[1] } : { kind: 'bold', text: m[2] });
    last = at + m[0].length;
  }
  if (last < src.length) spans.push({ kind: 'text', text: src.slice(last) });
  return spans;
};
