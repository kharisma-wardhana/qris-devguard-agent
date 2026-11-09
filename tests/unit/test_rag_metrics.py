"""Recall@k and MRR@k against hand-computed cases (p2 spec AC #13 harness)."""

import pytest

from app.rag import metrics as m

A, B, C, D = ("d", "1"), ("d", "2"), ("e", "1"), ("e", "2")


def test_first_hit_rank():
    assert m.first_hit_rank([C, A, B], {A}, 5) == 2
    assert m.first_hit_rank([C, D, B], {A}, 5) is None
    assert m.first_hit_rank([C, D, A], {A}, 2) is None                  # hit beyond k
    assert m.first_hit_rank([C, B, A], {A, B}, 5) == 2                   # several gold: first one counts


def test_recall_at_k():
    runs = [([A, C], {A}),                # hit at 1
            ([C, D, B], {B}),             # hit at 3
            ([C, D], {A})]                # miss
    assert m.recall_at_k(runs, 1) == pytest.approx(1 / 3)
    assert m.recall_at_k(runs, 3) == pytest.approx(2 / 3)
    assert m.recall_at_k([], 5) == 0.0


def test_mrr_at_k():
    runs = [([A, C], {A}), ([C, D, B], {B}), ([C, D], {A})]
    assert m.mrr_at_k(runs, 10) == pytest.approx((1 + 1 / 3 + 0) / 3)
    assert m.mrr_at_k(runs, 2) == pytest.approx(1 / 3)                 # rank-3 hit falls outside k=2


# --- p3: ranking metrics (spec AC #4) ---------------------------------------

def test_mrr_at_5_rank_1_rank_3_and_miss():
    assert m.mrr_at_k([([A, C], {A})], 5) == 1.0
    assert m.mrr_at_k([([C, D, A], {A})], 5) == pytest.approx(1 / 3)
    assert m.mrr_at_k([([C, D], {A})], 5) == 0.0


def test_ndcg_single_gold_section():
    assert m.ndcg_one([A, C], {A}, 5) == pytest.approx(1.0)
    assert m.ndcg_one([C, D, A], {A}, 5) == pytest.approx(1 / m.math.log2(4))     # rank 3
    assert m.ndcg_one([C, D], {A}, 5) == 0.0


def test_ndcg_two_gold_sections():
    ideal = 1 + 1 / m.math.log2(3)
    assert m.ndcg_one([A, B, C], {A, B}, 5) == pytest.approx(1.0)
    assert m.ndcg_one([A, C, B], {A, B}, 5) == pytest.approx((1 + 1 / m.math.log2(4)) / ideal)


def test_ndcg_counts_each_gold_section_once():
    """Repeated chunks from one long gold section must not inflate nDCG."""
    assert m.ndcg_one([A, A, A, A, A], {A, B}, 5) == pytest.approx(1 / (1 + 1 / m.math.log2(3)))
    assert m.ndcg_one([A, A, A], {A}, 5) == pytest.approx(1.0)


def test_ndcg_at_k_averages_and_respects_k():
    runs = [([A], {A}), ([C, D, C, D, C, A], {A})]
    assert m.ndcg_at_k(runs, 5) == pytest.approx(0.5)              # second query's hit is at rank 6
    assert m.ndcg_at_k([], 5) == 0.0
