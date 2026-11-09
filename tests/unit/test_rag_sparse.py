"""Identifier-preserving BM25 encoder (p2 spec AC #5)."""

import pytest

from app.rag import sparse

IDENTIFIERS = {
    "X-SIGNATURE": "x-signature",
    "X-TIMESTAMP": "x-timestamp",
    "2007300": "2007300",
    "4017300": "4017300",
    "SHA256withRSA": "sha256withrsa",
    "/v1.0/access-token/b2b": "v1.0/access-token/b2b",
    "HMAC_SHA512": "hmac_sha512",
}


@pytest.mark.parametrize("raw, term", IDENTIFIERS.items())
def test_identifier_is_one_term(raw, term):
    assert sparse.tokenize(f"Header {raw}, then text.") == ["header", term, "then", "text"]


@pytest.mark.parametrize("raw", IDENTIFIERS)
def test_identifier_matches_between_query_and_document(raw):
    doc = sparse.encode_document(f"Kirim {raw} pada request. Lainnya tidak relevan.")
    query = sparse.encode_query(f"apa arti {raw}?")
    assert sparse.term_index(IDENTIFIERS[raw]) in set(doc.indices) & set(query.indices)


def test_document_weights_follow_bm25():
    text = "token token token other"
    k1, b, avgdl = 1.2, 0.75, 10.0
    enc = sparse.encode_document(text, k1=k1, b=b, avg_doc_len=avgdl)
    weights = dict(zip(enc.indices, enc.values))
    dl = 4

    def bm25(tf):
        return tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))

    assert weights[sparse.term_index("token")] == pytest.approx(bm25(3))
    assert weights[sparse.term_index("other")] == pytest.approx(bm25(1))


def test_query_weights_are_one_per_distinct_term():
    enc = sparse.encode_query("signature signature timestamp")
    assert sorted(enc.values) == [1.0, 1.0]


def test_hashing_is_stable_and_indices_sorted():
    assert sparse.term_index("x-signature") == sparse.term_index("x-signature")
    assert 0 <= sparse.term_index("x-signature") < 2 ** 32
    enc = sparse.encode_document("b a c a")
    assert enc.indices == sorted(enc.indices)


def test_no_stemming_indonesian_affixes_stay_distinct():
    assert sparse.tokenize("tanda tangan penandatanganan") == ["tanda", "tangan", "penandatanganan"]


def test_empty_text_gives_empty_vector():
    assert sparse.encode_document("").indices == []
    assert sparse.encode_query("  ... ").indices == []
