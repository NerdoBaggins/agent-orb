"""Measuring a recorded clip, and reading a reply's markdown and splitting it into sentences before speaking."""
import re

import numpy as np
from markdown_it import MarkdownIt

SAMPLE_RATE = 16_000
TAP_S = 0.3
GAP_S = 0.02
LOSSY_SHARE = 0.1


def lost_share(samples: np.ndarray) -> float:
    """The share of a clip in runs of exact zeros at least GAP_S long. A live microphone's noise is never exactly zero
    for that long; the browser pads the audio it failed to capture with zeros."""
    if samples.size == 0:
        return 0.0
    edges = np.flatnonzero(np.diff(np.concatenate(([0], (samples == 0).astype(np.int8), [0]))))
    runs = edges[1::2] - edges[::2]
    return float(runs[runs >= GAP_S * SAMPLE_RATE].sum() / samples.size)


def gap_notice(lost: float) -> str:
    return f"your mic audio is arriving with gaps ({lost:.0%} of it lost)"


CODE_BLOCK = "There's a code block here; it's in the transcript."
MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


def _inline_text(token) -> str:
    parts = []
    for child in token.children or ():
        if child.type in ("text", "text_special", "code_inline"):
            parts.append(child.content)
        elif child.type == "image":
            parts.append(_inline_text(child))
        elif child.type in ("softbreak", "hardbreak"):
            parts.append("\n")
    return "".join(parts)


def speakable(text: str) -> str:
    """The words of a reply as they should be heard, read from its markdown: marks dropped, an image read by its alt
    text, a table's rows read as lists of cells, a code block named rather than read."""
    lines: list[str] = []
    row: list[str] | None = None
    for token in MARKDOWN.parse(text):
        if token.type == "inline":
            if row is None:
                lines.append(_inline_text(token))
            else:
                row.append(_inline_text(token).strip())
        elif token.type in ("fence", "code_block"):
            lines.append(CODE_BLOCK)
        elif token.type == "tr_open":
            row = []
        elif token.type == "tr_close":
            lines.append(", ".join(cell for cell in row if cell))
            row = None
    return "\n".join(line.strip() for line in "\n".join(lines).splitlines())


def split_sentences(text: str, min_words: int = 3) -> list[str]:
    """Sentences to render one at a time. A fragment shorter than `min_words` joins its neighbour so a lone
    'Yes.' does not cost a render of its own."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text.strip()) if p.strip()]
    merged: list[str] = []
    carry = ""
    for part in parts:
        if carry:
            part, carry = f"{carry} {part}", ""
        if len(part.split()) < min_words:
            if merged:
                merged[-1] = f"{merged[-1]} {part}"
            else:
                carry = part
        else:
            merged.append(part)
    if carry:
        merged.append(carry)
    return merged
