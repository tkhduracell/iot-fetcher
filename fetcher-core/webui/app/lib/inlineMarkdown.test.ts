import { describe, it, expect } from 'vitest';
import { parseInlineMarkdown as p } from './inlineMarkdown';

describe('parseInlineMarkdown', () => {
  it('leaves plain text alone', () => {
    expect(p('hej')).toEqual([{ kind: 'text', text: 'hej' }]);
  });
  it('parses bold and code', () => {
    expect(p('a **b** c `d`')).toEqual([
      { kind: 'text', text: 'a ' },
      { kind: 'bold', text: 'b' },
      { kind: 'text', text: ' c ' },
      { kind: 'code', text: 'd' },
    ]);
  });
  it('keeps ** inside code literal', () => {
    expect(p('`x**y**`')).toEqual([{ kind: 'code', text: 'x**y**' }]);
  });
  it('ignores unclosed and cross-line markers', () => {
    expect(p('**a\nb** `c')).toEqual([{ kind: 'text', text: '**a\nb** `c' }]);
  });
});
