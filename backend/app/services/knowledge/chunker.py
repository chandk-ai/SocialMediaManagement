"""Paragraph-aware text chunker.

Heuristic:
  1. Split on blank lines into paragraphs.
  2. Greedily pack paragraphs into chunks until target size reached.
  3. If a single paragraph exceeds chunk_size, split on sentence
     boundaries; if a single sentence still exceeds, hard-cut.
  4. Add ``overlap`` characters of the previous chunk's tail to the
     next chunk so retrieval queries that straddle a boundary still
     work.

Why these defaults:
  * 500-char chunks give roughly 100–150 tokens — enough for an
    "example post" and small enough that retrieval is precise.
  * 100-char overlap covers typical sentence boundaries without
    bloating chunk count.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class Chunk:
    idx: int
    text: str


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def chunk_text(
    text: str, *,
    chunk_size: int = 500, overlap: int = 100,
) -> list[Chunk]:
    text = (text or "").strip()
    if not text:
        return []

    # Split on blank lines into paragraphs.
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    pieces: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for p in paragraphs:
        if len(p) > chunk_size:
            # Flush whatever we've accumulated.
            if buf:
                pieces.append(" ".join(buf))
                buf, buf_len = [], 0
            pieces.extend(_split_long_paragraph(p, chunk_size))
            continue
        if buf_len + len(p) + 1 > chunk_size:
            pieces.append(" ".join(buf))
            buf, buf_len = [], 0
        buf.append(p)
        buf_len += len(p) + 1
    if buf:
        pieces.append(" ".join(buf))

    if overlap <= 0 or len(pieces) <= 1:
        return [Chunk(idx=i, text=p) for i, p in enumerate(pieces)]

    # Add tail-overlap from prev chunk to head of next.
    out: list[Chunk] = []
    for i, p in enumerate(pieces):
        if i == 0:
            out.append(Chunk(idx=i, text=p))
            continue
        prev_tail = pieces[i - 1][-overlap:]
        out.append(Chunk(idx=i, text=f"{prev_tail} {p}".strip()))
    return out


def _split_long_paragraph(p: str, chunk_size: int) -> list[str]:
    sentences = _SENTENCE_END.split(p)
    out: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for s in sentences:
        if len(s) > chunk_size:
            if buf:
                out.append(" ".join(buf)); buf, buf_len = [], 0
            # Hard-cut overlong sentence.
            for i in range(0, len(s), chunk_size):
                out.append(s[i:i + chunk_size])
            continue
        if buf_len + len(s) + 1 > chunk_size:
            out.append(" ".join(buf)); buf, buf_len = [], 0
        buf.append(s); buf_len += len(s) + 1
    if buf:
        out.append(" ".join(buf))
    return out
