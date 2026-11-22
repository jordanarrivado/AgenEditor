"""Word-timing fallback (roadmap Phase 2c) for speech the recognizer returns without usable word times.

faster-whisper aligns words with its own attention-based timing. For some languages and passages it returns
words with no start/end, zero-length or out-of-order words, or a segment with text but no words at all. Before
this module such words, and whole segments, were dropped from the transcript. Now:

* a run of untimed words between two timed ones is spread over the gap between them, weighted by an estimated
  syllable count, and
* a segment with no usable word times has its words (or its text, split into words, characters or grapheme
  clusters for scripts written without spaces) spread over the segment's own start and end.

Estimated words are marked ``timing="estimated"`` so captions, plans and reviewers can treat them with care.
Nothing here invents words: every estimated word is text the recognizer returned for that segment.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: Scripts written without spaces between words; each character (or grapheme cluster) is one unit.
_NO_SPACE_RANGES = (
    (0x0E00, 0x0EFF),  # Thai, Lao
    (0x1000, 0x109F),  # Myanmar
    (0x1780, 0x17FF),  # Khmer
    (0x3040, 0x30FF),  # Hiragana, Katakana
    (0x3400, 0x4DBF),  # CJK extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0x20000, 0x2FA1F),  # CJK extensions B+
)
#: Syllabic scripts where each written character is about one syllable.
_ONE_SYLLABLE_RANGES = (*_NO_SPACE_RANGES, (0xAC00, 0xD7AF))  # + Hangul syllables
#: Vowel letters (Latin with accents, Cyrillic, Greek); each run of them is about one syllable.
_VOWELS = re.compile(
    "[aeiouy\u00e0\u00e1\u00e2\u00e3\u00e4\u00e5\u00e6\u00e8\u00e9\u00ea\u00eb\u00ec\u00ed\u00ee\u00ef\u00f2\u00f3\u00f4\u00f5\u00f6\u00f8\u00f9\u00fa\u00fb\u00fc\u00fd\u00ff\u0153"
    "\u0430\u0435\u0451\u0438\u043e\u0443\u044b\u044d\u044e\u044f\u0456\u0457\u0454"  # Cyrillic
    "\u03b1\u03b5\u03b7\u03b9\u03bf\u03c5\u03c9\u03ac\u03ad\u03ae\u03af\u03cc\u03cd\u03ce]+",  # Greek
    re.IGNORECASE,
)


def _in(ranges: tuple[tuple[int, int], ...], char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in ranges)


def _is_mark_or_punct(char: str) -> bool:
    return unicodedata.category(char)[0] in {"M", "P"}


def split_tokens(text: str) -> list[str]:
    """Split segment text into word-like units: whitespace words, and one unit per character (with its
    combining marks and trailing punctuation) inside scripts written without spaces."""
    tokens: list[str] = []
    for word in text.split():
        current, current_kind = "", ""
        for char in word:
            if _is_mark_or_punct(char):
                current += char  # stays with the character before it
                continue
            kind = "unit" if _in(_NO_SPACE_RANGES, char) else "word"
            if current and (kind == "unit" or current_kind == "unit"):
                tokens.append(current)
                current = ""
            current += char
            current_kind = kind
        if current:
            tokens.append(current)
    return tokens


def estimate_syllables(token: str) -> int:
    """A rough syllable count used only to share time between words; never shown as a measurement."""
    letters = [c for c in token if c.isalnum()]
    if not letters:
        return 1
    syllabic = sum(1 for c in letters if _in(_ONE_SYLLABLE_RANGES, c))
    if syllabic:
        return syllabic
    groups = len(_VOWELS.findall(token))
    if groups:
        return groups
    return max(1, round(len(letters) / 3))


def spread(tokens: list[str], start_us: int, end_us: int) -> list[tuple[str, int, int]] | None:
    """Share ``[start_us, end_us)`` between tokens by estimated syllables; back to back, integer
    microseconds, every token at least 1 us. ``None`` when the span is too short."""
    if not tokens or end_us - start_us < len(tokens):
        return None
    weights = [estimate_syllables(token) for token in tokens]
    total = sum(weights)
    span = end_us - start_us
    result = []
    done = 0
    cursor = start_us
    for token, weight in zip(tokens, weights, strict=True):
        done += weight
        boundary = start_us + span * done // total
        remaining = len(tokens) - len(result) - 1
        boundary = max(boundary, cursor + 1)
        boundary = min(boundary, end_us - remaining)
        result.append((token, cursor, boundary))
        cursor = boundary
    return result


@dataclass(frozen=True)
class RawWord:
    text: str
    start_us: int | None
    end_us: int | None
    probability: float | None = None


@dataclass(frozen=True)
class TimedWord:
    text: str
    start_us: int
    end_us: int
    probability: float | None
    estimated: bool


def time_segment(
    words: list[RawWord], text: str, segment_start_us: int | None, segment_end_us: int | None
) -> list[TimedWord]:
    """Word times for one recognizer segment, filling gaps by estimate where the recognizer gave none.

    Returns an empty list only when nothing can be placed (no words, no text, or no usable segment span).
    """
    words = [w for w in words if w.text.strip()]
    usable = [w.start_us is not None and w.end_us is not None and w.end_us > w.start_us for w in words]
    # Out-of-order times count as unusable too.
    last_start = -1
    for index, word in enumerate(words):
        if usable[index]:
            if word.start_us < last_start:
                usable[index] = False
            else:
                last_start = word.start_us
    if words and all(usable):
        return [TimedWord(w.text.strip(), w.start_us, w.end_us, w.probability, False) for w in words]

    has_span = (
        segment_start_us is not None and segment_end_us is not None and segment_end_us > segment_start_us
    )
    if words and any(usable):
        timed: list[TimedWord | None] = [
            TimedWord(w.text.strip(), w.start_us, w.end_us, w.probability, False) if ok else None
            for w, ok in zip(words, usable, strict=True)
        ]
        index = 0
        filled = True
        while index < len(words):
            if timed[index] is not None:
                index += 1
                continue
            run_end = index
            while run_end < len(words) and timed[run_end] is None:
                run_end += 1
            before = timed[index - 1].end_us if index > 0 else segment_start_us
            after = timed[run_end].start_us if run_end < len(words) else segment_end_us
            placed = (
                spread([w.text.strip() for w in words[index:run_end]], before, after)
                if before is not None and after is not None
                else None
            )
            if placed is None:
                filled = False
                break
            for offset, (token, start, end) in enumerate(placed):
                timed[index + offset] = TimedWord(token, start, end, words[index + offset].probability, True)
            index = run_end
        if filled:
            return [word for word in timed if word is not None]

    if not has_span:
        return []
    tokens = [w.text.strip() for w in words] if words else split_tokens(text)
    placed = spread(tokens, segment_start_us, segment_end_us)
    if placed is None:
        return []
    probabilities = [w.probability for w in words] if words else [None] * len(placed)
    return [
        TimedWord(token, start, end, probability, True)
        for (token, start, end), probability in zip(placed, probabilities, strict=True)
    ]
