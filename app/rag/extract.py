"""Text extraction into sections and blocks (p2 spec Behaviour 2.3).

A document becomes a list of Sections. Each Section holds Blocks:
- "text" blocks may be split further by the chunker and are whitespace-normalised;
- "atomic" blocks (fenced code, JSON, Markdown tables) are kept byte-for-byte and never split.

Markdown sections come from headings; PDF sections from numbered heading lines
(`chunking.pdf_heading_pattern` in configs/retrieval.yaml) and keep page numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.rag import config

TEXT, ATOMIC = "text", "atomic"

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.*)$")
_INLINE_WS = re.compile(r"[ \t ]+")


@dataclass
class Block:
    kind: str                    # text | atomic
    text: str
    page: int | None = None


@dataclass
class Section:
    section_id: str
    title: str
    blocks: list[Block] = field(default_factory=list)

    @property
    def pages(self) -> list[int]:
        return sorted({b.page for b in self.blocks if b.page is not None})


@dataclass
class Extraction:
    sections: list[Section]
    page_count: int | None = None
    empty_pages: list[int] = field(default_factory=list)


def normalise_text(text: str) -> str:
    """Collapse runs of spaces/tabs and trim lines. Never applied to atomic blocks."""
    lines = [_INLINE_WS.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


class _SectionIds:
    """Keeps section IDs unique inside one document."""

    def __init__(self) -> None:
        self.seen: dict[str, int] = {}

    def unique(self, sid: str) -> str:
        n = self.seen.get(sid, 0) + 1
        self.seen[sid] = n
        return sid if n == 1 else f"{sid}-{n}"


# ---------------------------------------------------------------- Markdown

def _markdown_blocks(lines: list[str]) -> list[Block]:
    blocks: list[Block] = []
    para: list[str] = []

    def flush_para() -> None:
        if para:
            text = normalise_text("\n".join(para))
            if text:
                blocks.append(Block(TEXT, text))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            flush_para()
            fence = [line]
            i += 1
            while i < len(lines):
                fence.append(lines[i])
                if lines[i].lstrip().startswith("```"):
                    break
                i += 1
            blocks.append(Block(ATOMIC, "\n".join(fence)))
        elif line.lstrip().startswith("|"):
            flush_para()
            table = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                table.append(lines[i].rstrip())
                i += 1
            blocks.append(Block(ATOMIC, "\n".join(table)))
            continue
        elif not line.strip():
            flush_para()
        else:
            para.append(line)
        i += 1
    flush_para()
    return blocks


def extract_markdown(text: str) -> Extraction:
    ids = _SectionIds()
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []          # (heading level, section id) of ancestors
    current = Section("0", "", [])
    body: list[str] = []
    in_fence = False

    def close() -> None:
        current.blocks = _markdown_blocks(body)
        if current.blocks or current.section_id == "0":
            sections.append(current)

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        heading = None if in_fence else _MD_HEADING.match(line)
        if heading is None:
            body.append(line)
            continue
        level, title = len(heading.group(1)), heading.group(2).strip()
        if level == 1:                       # document title: names the preamble section
            current.title = current.title or title
            continue
        close()
        body = []
        while stack and stack[-1][0] >= level:
            stack.pop()
        numbered = _NUMBER.match(title.replace("`", ""))
        if numbered:
            sid = numbered.group(1)
        else:
            parent = stack[-1][1] if stack else ""
            sid = f"{parent}.{slugify(title)}" if parent else slugify(title)
        sid = ids.unique(sid)
        stack.append((level, sid))
        current = Section(sid, title, [])
    close()
    return Extraction([s for s in sections if s.blocks])


# --------------------------------------------------------------------- PDF

def _pdf_line_blocks(lines: list[str], page: int) -> list[Block]:
    """Group a page's body lines; a brace-balanced JSON-looking run is one atomic block."""
    blocks: list[Block] = []
    para: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith(("{", "[")):
            depth, run, j = 0, [], i
            while j < len(lines):
                run.append(lines[j])
                depth += lines[j].count("{") + lines[j].count("[") - lines[j].count("}") - lines[j].count("]")
                j += 1
                if depth <= 0:
                    break
            if depth == 0 and len(run) > 1:
                if para:
                    blocks.append(Block(TEXT, normalise_text("\n".join(para)), page))
                    para = []
                blocks.append(Block(ATOMIC, "\n".join(run), page))
                i = j
                continue
        para.append(line)
        i += 1
    if para:
        text = normalise_text("\n".join(para))
        if text:
            blocks.append(Block(TEXT, text, page))
    return blocks


Line = tuple[str, bool | None]          # (text, is_bold); None = font unknown


def extract_pdf_pages(pages: list[str] | list[list[Line]]) -> Extraction:
    """Sections from per-page text (pages[0] is page 1). Separated from pdfplumber for testing.

    A page is either plain text (font unknown) or a list of (line, is_bold). With
    `pdf_heading_requires_bold`, a line known not to be bold is never a heading: in the real
    ASPI PDFs, headings are bold while table-of-contents rows and numbered list items are not.
    """
    cfg = config.retrieval()["chunking"]
    pattern = re.compile(cfg["pdf_heading_pattern"])
    requires_bold = cfg.get("pdf_heading_requires_bold", False)
    ids = _SectionIds()
    sections: list[Section] = [Section("0", "", [])]
    empty = []
    last_top = 0          # last top-level section number (1, 2, 3 ...)
    parent = ""           # id of the last dotted/top-level section, for numbered sub-items

    def section_id(number_text: str) -> str:
        """`2.1.6` stays as is. A bare `3` is top-level only if it continues 1, 2, 3...;
        otherwise it is a numbered item inside the current section: `2.1.6#3`."""
        nonlocal last_top, parent
        if "." in number_text:
            parent = number_text
            return number_text
        n = int(number_text)
        if n == last_top + 1 or not parent:
            last_top, parent = n, number_text
            return number_text
        return f"{parent}#{n}"

    for number, page in enumerate(pages, start=1):
        lines: list[Line] = [(l, None) for l in page.splitlines()] if isinstance(page, str) else list(page)
        if not any(text.strip() for text, _ in lines):
            empty.append(number)
            continue
        body: list[str] = []
        for raw, bold in lines:
            line = raw.rstrip()
            match = pattern.match(line.strip())
            if match and requires_bold and bold is False:
                match = None
            if match:
                sections[-1].blocks.extend(_pdf_line_blocks(body, number))
                body = []
                title = match.group(2).strip().rstrip(":").strip()
                sections.append(Section(ids.unique(section_id(match.group(1))), title, []))
            else:
                body.append(line)
        sections[-1].blocks.extend(_pdf_line_blocks(body, number))
    return Extraction([s for s in sections if s.blocks], page_count=len(pages), empty_pages=empty)


def extract_pdf(path: Path) -> Extraction:
    import pdfplumber

    pages: list[list[Line]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            lines = []
            for line in page.extract_text_lines(return_chars=True):
                chars = [c for c in line["chars"] if c["text"].strip()]
                bold = sum("Bold" in c.get("fontname", "") for c in chars) > len(chars) / 2 if chars else False
                lines.append((line["text"], bold))
            pages.append(lines)
    return extract_pdf_pages(pages)


def extract(path: Path) -> Extraction:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return extract_markdown(path.read_text(encoding="utf-8"))
    if suffix == ".pdf":
        return extract_pdf(path)
    raise ValueError(f"unsupported file type: {suffix}")
