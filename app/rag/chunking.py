"""Section-first chunking with stable IDs (p2 spec Behaviour 2.4-2.5, AC #2, #3).

Rules:
- Chunks never cross a section boundary.
- Atomic blocks (code, JSON, tables) are never split; one larger than `max_tokens`
  becomes a chunk on its own.
- Text is packed up to `target_tokens`, never past `max_tokens` (estimated as
  characters / chars_per_token). An over-long text block is split at line, sentence,
  then word boundaries.
- Overlap (`overlap_tokens`) is copied only from the tail of a text block, only into
  the next chunk of the same section, and only if it still fits under `max_tokens`.
- Every chunk starts with its section heading line, so it reads on its own.
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from dataclasses import dataclass

from app.rag import config
from app.rag.extract import ATOMIC, TEXT, Block, Section

CHUNK_NAMESPACE = uuid.UUID("6f1c9a52-3b8e-4d4b-9a51-2f0c7d6e8a13")   # fixed: IDs stay stable


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    document_version: str
    section_id: str
    section_title: str
    position: int                  # index of the chunk inside its section
    page_start: int | None
    page_end: int | None
    text: str
    text_hash: str


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / config.retrieval()["chunking"]["chars_per_token"])


def chunk_id(document_id: str, version: str, section_id: str, position: int) -> str:
    return str(uuid.uuid5(CHUNK_NAMESPACE, f"{document_id}|{version}|{section_id}|{position}"))


def _split_text(text: str, max_chars: int) -> list[str]:
    """Split one text block into pieces of at most max_chars at the gentlest boundary available."""
    if len(text) <= max_chars:
        return [text]
    for sep in ("\n", r"(?<=[.!?])\s+", " "):
        parts = [p for p in re.split(sep, text) if p]
        if len(parts) > 1:
            out: list[str] = []
            cur = ""
            joiner = "\n" if sep == "\n" else " "
            for part in parts:
                candidate = f"{cur}{joiner}{part}" if cur else part
                if len(candidate) <= max_chars:
                    cur = candidate
                else:
                    if cur:
                        out.append(cur)
                    cur = part
            if cur:
                out.append(cur)
            return [piece for p in out for piece in _split_text(p, max_chars)]
    return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]   # one unbroken token


def _tail(text: str, max_chars: int) -> str:
    """The last <= max_chars characters, starting at a word boundary."""
    if len(text) <= max_chars:
        return text
    cut = text[-max_chars:]
    space = cut.find(" ")
    return cut[space + 1:] if 0 <= space < len(cut) - 1 else cut


def chunk_section(section: Section, document_id: str, version: str) -> list[Chunk]:
    cfg = config.retrieval()["chunking"]
    cpt = cfg["chars_per_token"]
    heading = section.title or section.section_id
    head = f"{heading}\n\n"
    body_max = cfg["max_tokens"] * cpt - len(head)
    body_target = cfg["target_tokens"] * cpt - len(head)
    overlap = cfg["overlap_tokens"] * cpt

    units: list[Block] = []
    for block in section.blocks:
        if block.kind == ATOMIC:
            units.append(block)
        else:
            # split long text at the target size, leaving room under max for the overlap
            units.extend(Block(TEXT, piece, block.page) for piece in _split_text(block.text, body_target))

    groups: list[list[Block]] = []
    current: list[Block] = []
    size = 0
    for unit in units:
        add = len(unit.text) + (2 if current else 0)
        if current and (size + add > body_max or size >= body_target):
            groups.append(current)
            current, size = [], 0
            add = len(unit.text)
        current.append(unit)
        size += add
    if current:
        groups.append(current)

    chunks: list[Chunk] = []
    for position, group in enumerate(groups):
        parts = [b.text for b in group]
        if position > 0:
            prev_last = groups[position - 1][-1]
            if prev_last.kind == TEXT:
                tail = _tail(prev_last.text, overlap)
                if len(tail) + 2 + sum(len(p) + 2 for p in parts) - 2 <= body_max:
                    parts = [tail, *parts]
        text = head + "\n\n".join(parts)
        pages = [b.page for b in group if b.page is not None]
        chunks.append(Chunk(
            chunk_id=chunk_id(document_id, version, section.section_id, position),
            document_id=document_id,
            document_version=version,
            section_id=section.section_id,
            section_title=section.title,
            position=position,
            page_start=min(pages) if pages else None,
            page_end=max(pages) if pages else None,
            text=text,
            text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        ))
    return chunks


def chunk_document(sections: list[Section], document_id: str, version: str) -> list[Chunk]:
    return [c for s in sections for c in chunk_section(s, document_id, version)]
