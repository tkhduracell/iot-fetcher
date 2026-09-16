import { describe, it, expect } from 'vitest';
import { cueParagraphs } from './display';

describe('cueParagraphs', () => {
  it('starts a paragraph at every cue', () => {
    expect(cueParagraphs('[rappt] Ett. [torrt] Två.')).toEqual([
      { cue: '[rappt]', text: 'Ett.' },
      { cue: '[torrt]', text: 'Två.' },
    ]);
  });

  it('keeps uncued text as its own paragraph', () => {
    expect(cueParagraphs('En allvarlig nyhet utan markör. [lugnt] Och sen.')).toEqual([
      { cue: null, text: 'En allvarlig nyhet utan markör.' },
      { cue: '[lugnt]', text: 'Och sen.' },
    ]);
  });

  it('handles a transcript with no cues at all', () => {
    expect(cueParagraphs('Bara text.')).toEqual([{ cue: null, text: 'Bara text.' }]);
  });

  it('keeps a trailing cue with no text after it', () => {
    expect(cueParagraphs('[rappt] Ett. [torrt]')).toEqual([
      { cue: '[rappt]', text: 'Ett.' },
      { cue: '[torrt]', text: '' },
    ]);
  });

  it('does not drop text when two cues follow each other', () => {
    expect(cueParagraphs('[rappt] [torrt] Två.')).toEqual([
      { cue: '[rappt]', text: '' },
      { cue: '[torrt]', text: 'Två.' },
    ]);
  });

  it('ignores surrounding whitespace', () => {
    expect(cueParagraphs('  [glatt]   Hej.  ')).toEqual([{ cue: '[glatt]', text: 'Hej.' }]);
  });
});
