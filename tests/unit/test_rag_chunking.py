"""Extraction and chunking (p2 spec AC #2, chunk part of #4, #3 IDs)."""

import re
from pathlib import Path

import pytest

from app.rag import chunking, config, extract

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = sorted((ROOT / "knowledge_base" / "synthetic").glob("*.md"))
CFG = config.retrieval()["chunking"]
MAX_CHARS = CFG["max_tokens"] * CFG["chars_per_token"]


def make_pdf(pages: list[list[str]]) -> bytes:
    """A minimal valid PDF with one text line per entry, built from raw PDF syntax."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    objects: list[bytes] = []
    n_pages = len(pages)
    page_ids = [4 + 2 * i for i in range(n_pages)]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, lines in enumerate(pages):
        ops = "BT /F1 11 Tf 14 TL 50 800 Td " + " ".join(f"({esc(l)}) Tj T*" for l in lines) + " ET"
        stream = ops.encode("latin-1")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                       f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_ids[i] + 1} 0 R >>".encode())
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for num, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def atomic_blocks_in_source(text: str) -> list[str]:
    fences = re.findall(r"^```.*?^```", text, flags=re.M | re.S)
    tables = ["\n".join(m.rstrip() for m in t.strip("\n").splitlines())
              for t in re.findall(r"(?:^\|.*\n?)+", text, flags=re.M)]
    return fences + tables


# ------------------------------------------------------------- synthetic docs

@pytest.mark.parametrize("path", SYNTHETIC, ids=lambda p: p.stem)
def test_synthetic_chunks_respect_max_unless_single_atomic(path):
    ext = extract.extract(path)
    for chunk in chunking.chunk_document(ext.sections, path.stem, "0.1.0"):
        if len(chunk.text) > MAX_CHARS:
            body = chunk.text.split("\n\n", 1)[1]
            assert body.startswith(("```", "|")), chunk.section_id


@pytest.mark.parametrize("path", SYNTHETIC, ids=lambda p: p.stem)
def test_every_code_block_and_table_is_unbroken_in_exactly_one_chunk(path):
    source = path.read_text(encoding="utf-8")
    chunks = chunking.chunk_document(extract.extract(path).sections, path.stem, "0.1.0")
    blocks = atomic_blocks_in_source(source)
    assert blocks, "synthetic docs contain code blocks and tables"
    for block in blocks:
        holders = [c for c in chunks if block in c.text]
        assert len(holders) == 1, block[:60]


@pytest.mark.parametrize("path", SYNTHETIC, ids=lambda p: p.stem)
def test_section_ids_come_from_real_headings(path):
    source = path.read_text(encoding="utf-8")
    headings = [h.strip() for h in re.findall(r"^#{2,6}\s+(.*)$", source, flags=re.M)]
    for chunk in chunking.chunk_document(extract.extract(path).sections, path.stem, "0.1.0"):
        if chunk.section_id == "0":
            assert "(SYNTHETIC — not an official ASPI/BI document)" in chunk.section_title
        else:
            assert chunk.section_title in headings
            assert chunk.text.startswith(chunk.section_title + "\n\n")


def test_chunk_ids_and_hashes_are_stable_across_runs():
    path = SYNTHETIC[0]
    a = chunking.chunk_document(extract.extract(path).sections, "doc", "0.1.0")
    b = chunking.chunk_document(extract.extract(path).sections, "doc", "0.1.0")
    assert [(c.chunk_id, c.text_hash) for c in a] == [(c.chunk_id, c.text_hash) for c in b]
    assert len({c.chunk_id for c in a}) == len(a)
    assert chunking.chunk_id("doc", "0.2.0", "1.1", 0) != chunking.chunk_id("doc", "0.1.0", "1.1", 0)


# ----------------------------------------------------------- crafted inputs

def sentences(prefix: str, n: int) -> str:
    return " ".join(f"{prefix} sentence number {i} describes the timestamp rule." for i in range(n))


def test_long_section_splits_with_overlap_only_inside_the_section():
    md = f"## 1.1 First\n\n{sentences('Alpha', 160)}\n\n## 1.2 Second\n\n{sentences('Beta', 5)}\n"
    chunks = chunking.chunk_document(extract.extract_markdown(md).sections, "doc", "1")
    first = [c for c in chunks if c.section_id == "1.1"]
    second = [c for c in chunks if c.section_id == "1.2"]
    assert len(first) >= 2 and len(second) == 1
    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    for prev, nxt in zip(first, first[1:]):
        prev_body = prev.text.split("\n\n", 1)[1]
        nxt_body = nxt.text.split("\n\n", 1)[1]
        overlap = nxt_body.split("\n\n", 1)[0]
        assert prev_body.endswith(overlap), "next chunk starts with the previous chunk's tail"
        assert 0 < len(overlap) <= CFG["overlap_tokens"] * CFG["chars_per_token"]
    assert "Alpha" not in second[0].text, "no overlap across sections"


def test_oversized_atomic_block_is_its_own_chunk_and_unbroken():
    big = "```json\n" + "\n".join(f'  "field{i}": "value value value value value",' for i in range(150)) + "\n```"
    md = f"## 2 Payload\n\nIntro line.\n\n{big}\n\nOutro line.\n"
    chunks = chunking.chunk_document(extract.extract_markdown(md).sections, "doc", "1")
    holders = [c for c in chunks if big in c.text]
    assert len(holders) == 1
    assert holders[0].text == f"2 Payload\n\n{big}"
    assert len(holders[0].text) > MAX_CHARS


def test_table_kept_whole():
    table = "| Code | Meaning |\n|---|---|\n" + "\n".join(f"| `40047{i:02d}` | Reason {i} |" for i in range(40))
    md = f"## 3 Codes\n\n{sentences('Gamma', 30)}\n\n{table}\n"
    chunks = chunking.chunk_document(extract.extract_markdown(md).sections, "doc", "1")
    assert sum(table in c.text for c in chunks) == 1


def test_single_giant_paragraph_is_split_within_max():
    md = "## 4 Wall\n\n" + "word " * 3000 + "\n"
    chunks = chunking.chunk_document(extract.extract_markdown(md).sections, "doc", "1")
    assert len(chunks) >= 4
    assert all(len(c.text) <= MAX_CHARS for c in chunks)


def test_headings_inside_code_fences_are_not_sections():
    md = "## 5 Example\n\n```\n## not a heading\n```\n"
    sections = extract.extract_markdown(md).sections
    assert [s.section_id for s in sections] == ["5"]


def test_normalisation_keeps_identifiers_but_collapses_spaces():
    md = "## 6 Headers\n\nSend   X-SIGNATURE\tand  2007300 with SHA256withRSA.\n"
    chunk = chunking.chunk_document(extract.extract_markdown(md).sections, "doc", "1")[0]
    assert "Send X-SIGNATURE and 2007300 with SHA256withRSA." in chunk.text


# ---------------------------------------------------------------------- PDF

def test_pdf_pages_and_numbered_headings(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(make_pdf([
        ["1. Pendahuluan", "Dokumen ini menjelaskan standar teknis.", "Diterbitkan 12 Mei 2024"],
        [],
        ["4.2 Signature Generation", "Use SHA256withRSA over the string to sign.", "X-SIGNATURE carries the value."],
        ["Continued text on page four about X-TIMESTAMP."],
    ]))
    ext = extract.extract(pdf)
    assert ext.page_count == 4
    assert ext.empty_pages == [2]
    by_id = {s.section_id: s for s in ext.sections}
    assert set(by_id) == {"1", "4.2"}, "the date line is not a heading"
    assert by_id["1"].title == "Pendahuluan"
    assert by_id["4.2"].pages == [3, 4]
    chunks = chunking.chunk_document(ext.sections, "doc", "1.0.2")
    sig = [c for c in chunks if c.section_id == "4.2"]
    assert (sig[0].page_start, sig[-1].page_end) == (3, 4)
    assert "SHA256withRSA" in sig[0].text


def test_pdf_json_run_is_atomic():
    lines = ["2 Contoh Request", "Contoh body:", "{", '  "amount": {', '    "value": "10000.00"', "  }", "}", "Selesai."]
    ext = extract.extract_pdf_pages(["\n".join(lines)])
    kinds = [(b.kind, b.text.splitlines()[0]) for b in ext.sections[0].blocks]
    assert ("atomic", "{") in kinds
