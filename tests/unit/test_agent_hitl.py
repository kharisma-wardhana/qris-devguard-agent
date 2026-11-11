"""p6 agent changes: evidence ordering (AC #12) and the HITL graph (AC #3, #6, #9, #14)."""

from __future__ import annotations

from app.agent.graph import order_evidence


def c(rank, doc, section):
    return {"citation_id": f"CIT-{rank:06d}", "rank": rank, "document_id": doc, "section_id": section}


SNAP = "snap-technical-security-standard"
DATA = "snap-data-technical-spec"
MPM = "synthetic-qris-mpm-service-spec"
SOP = "synthetic-qris-troubleshooting-sop"

# The live flow-1 pool (p5 agent-flow-report): 2.1.5 sat at ranks 7, 10, 11 behind 6.1 and 2.1.6#2.
FLOW1 = [c(5, DATA, "6.1"), c(6, SNAP, "2.1.6#2"), c(7, SNAP, "2.1.5"), c(9, SNAP, "2.1.6"), c(10, SNAP, "2.1.5"),
         c(11, SNAP, "2.1.5")]


def ids(chunks):
    return [x["rank"] for x in chunks]


def test_flow1_profile_puts_2_1_5_first():
    out = order_evidence(FLOW1, api_family="snap", endpoint_id=None, profile="snap-asymmetric-access-token-v1")
    assert ids(out) == [7, 10, 11, 5, 6, 9]


def test_endpoint_section_prefix_is_bounded():
    pool = [c(1, SOP, "1"), c(2, MPM, "20.x"), c(3, MPM, "3.request-fields"), c(4, MPM, "2.request-fields"), c(5, MPM, "2")]
    out = order_evidence(pool, api_family=None, endpoint_id="qr-mpm-generate", profile=None)
    assert ids(out) == [4, 5, 2, 3, 1]                     # "20.x" is not section 2


def test_family_only_puts_family_documents_first():
    pool = [c(1, SOP, "1"), c(2, MPM, "3.request-fields"), c(3, SOP, "2"), c(4, MPM, "1.3")]
    assert ids(order_evidence(pool, api_family="qris-mpm", endpoint_id=None, profile=None)) == [2, 4, 1, 3]


def test_no_family_no_endpoint_keeps_fused_order():
    assert ids(order_evidence(FLOW1, api_family=None, endpoint_id=None, profile=None)) == ids(FLOW1)


def test_unknown_profile_or_endpoint_falls_back_to_family():
    out = order_evidence(FLOW1, api_family="snap", endpoint_id=None, profile="synthetic-snap-symmetric-v1")
    assert ids(out) == ids(FLOW1)                          # every chunk is a SNAP-family doc: fused order kept
