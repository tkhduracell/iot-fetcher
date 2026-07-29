package chunk

import "unicode"

// runesPerToken is the rough tiktoken approximation for English text.
// 1 token ~= 4 characters in practice; close enough for budgeting.
const runesPerToken = 4

// whitespaceLookback is how far back from the target cut we'll search for a
// whitespace boundary to split on, in runes.
const whitespaceLookback = 200

// Chunk is a window of text ready for embedding.
type Chunk struct {
	Index int
	Text  string
}

// Split breaks `text` into overlapping windows of ~targetTokens tokens with
// ~overlapTokens tokens of overlap. The token count is approximated as
// runes/4.
//
// The splitter prefers whitespace boundaries: near the target cut, it looks
// back up to whitespaceLookback runes for the last whitespace rune and cuts
// there. If no whitespace is found in that window, it cuts at the rune
// boundary.
//
// Empty input returns nil. Inputs shorter than a single window produce one
// chunk containing the entire string.
//
// Invariants:
//   - targetTokens must be > 0; otherwise returns a single chunk with the
//     entire text (no-op split).
//   - overlapTokens is clamped to [0, targetTokens-1].
func Split(text string, targetTokens, overlapTokens int) []Chunk {
	if text == "" {
		return nil
	}
	if targetTokens <= 0 {
		return []Chunk{{Index: 0, Text: text}}
	}
	if overlapTokens < 0 {
		overlapTokens = 0
	}
	if overlapTokens >= targetTokens {
		overlapTokens = targetTokens - 1
	}

	runes := []rune(text)
	n := len(runes)

	targetRunes := targetTokens * runesPerToken
	overlapRunes := overlapTokens * runesPerToken

	if n <= targetRunes {
		return []Chunk{{Index: 0, Text: text}}
	}

	var chunks []Chunk
	start := 0
	idx := 0
	for start < n {
		end := start + targetRunes
		if end >= n {
			// Final chunk: take everything remaining.
			chunks = append(chunks, Chunk{Index: idx, Text: string(runes[start:])})
			break
		}

		// Prefer to cut at a whitespace boundary within the lookback window.
		cut := end
		lookbackLimit := end - whitespaceLookback
		if lookbackLimit < start+1 {
			lookbackLimit = start + 1
		}
		for i := end; i >= lookbackLimit; i-- {
			if unicode.IsSpace(runes[i]) {
				cut = i
				break
			}
		}

		chunks = append(chunks, Chunk{Index: idx, Text: string(runes[start:cut])})
		idx++

		// Advance start so chunks overlap by overlapRunes.
		next := cut - overlapRunes
		if next <= start {
			// Guarantee forward progress even in degenerate overlap cases.
			next = start + 1
		}
		start = next
	}
	return chunks
}
