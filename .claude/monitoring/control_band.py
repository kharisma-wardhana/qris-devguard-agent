#!/usr/bin/env python3
"""Control-band monitor. Deterministic, version-controlled, no network.

Reads a baseline window and a recent window of the same metric, scores the
recent points against the baseline's mean and population standard
deviation, and reports the highest response tier any rule triggers.

Tiers:
  0  in control      - do nothing
  1  log only        - write the observation, no agent involvement
  2  diagnose        - invoke Claude read-only; no PR, no writes
  3  propose         - invoke Claude to open a PR or a runbook entry

Rules implemented (a deliberate subset of Western Electric):
  R1 -> tier 3  any point in the window beyond 3 sigma
  R2 -> tier 2  2 of the last 3 points beyond 2 sigma, same side
  R3 -> tier 1  4 of the last 5 points beyond 1 sigma, same side
  R4 -> tier 1  8 consecutive points on the same side of the mean

Rules 5-8 (stratification, mixtures, trends) are NOT implemented. Say so
rather than implying full Western Electric coverage.

Known ceilings, stated rather than hidden:
  - Sigma is estimated as the population stdev of the baseline window. A
    special cause inside the baseline inflates it and desensitises every
    rule. Classic Western Electric uses a moving-range estimate instead.
  - The 8-point minimum baseline is a floor, not a recommendation; the
    usual convention is 20-25 points before sigma means much.
  - Nothing here can tell whether the caller's baseline window overlaps
    the recent window. Overlap is the drift-hiding failure this is meant
    to catch, and preventing it is the producer's job.

Usage:
  control_band.py --baseline b.json --recent r.json   # both: list of numbers
  control_band.py --self-check
"""
import argparse
import json
import statistics
import sys

TIERS = {
    0: "in control",
    1: "log only",
    2: "diagnose read-only",
    3: "propose PR or runbook",
}

MIN_BASELINE = 8


def _numbers(values, label):
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label} must be a non-empty JSON list of numbers")
    out = []
    for i, v in enumerate(values):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{label}[{i}] is not a number: {v!r}")
        out.append(float(v))
    return out


def zscores(baseline, recent):
    """Score recent points against the baseline window's mean and sigma.

    The baseline must not contain the points being judged -- scoring a
    point against a window that includes it hides exactly the drift this
    is here to catch. This function cannot verify that; the caller must.
    """
    baseline = _numbers(baseline, "baseline")
    recent = _numbers(recent, "recent")
    if len(baseline) < MIN_BASELINE:
        raise ValueError(
            f"baseline needs at least {MIN_BASELINE} points to estimate sigma, got {len(baseline)}"
        )
    mu = statistics.fmean(baseline)
    sd = statistics.pstdev(baseline)
    if sd == 0:
        raise ValueError("baseline sigma is zero; metric is constant or mis-sampled")
    return [(x - mu) / sd for x in recent]


def evaluate(z):
    """Highest tier triggered. z is ordered oldest -> newest.

    Returns (tier, action, rule_id, explanation).
    """
    if not z:
        return 0, TIERS[0], "OK", "no data"

    def side(v):
        return 1 if v > 0 else -1

    if any(abs(v) >= 3 for v in z):
        return 3, TIERS[3], "R1", "point in window beyond 3 sigma"

    if len(z) >= 3:
        w = z[-3:]
        for s in (1, -1):
            if sum(1 for v in w if side(v) == s and abs(v) >= 2) >= 2:
                return 2, TIERS[2], "R2", "2 of 3 beyond 2 sigma, same side"

    if len(z) >= 5:
        w = z[-5:]
        for s in (1, -1):
            if sum(1 for v in w if side(v) == s and abs(v) >= 1) >= 4:
                return 1, TIERS[1], "R3", "4 of 5 beyond 1 sigma, same side"

    if len(z) >= 8:
        w = z[-8:]
        for s in (1, -1):
            if all(side(v) == s for v in w):
                return 1, TIERS[1], "R4", "8 consecutive on one side of the mean"

    return 0, TIERS[0], "OK", "no rule triggered"


def self_check():
    """Synthetic series exercising every rule, including its boundaries.

    Each case asserts the rule id as well as the tier -- two rules sharing
    a tier are otherwise indistinguishable, and a case that passes under a
    broken implementation is worthless.
    """
    base = [100.0] * 4 + [90.0, 110.0, 95.0, 105.0]  # mean 100, pstdev 5.5902
    sd = statistics.pstdev(base)

    def at(*sigmas):
        return [100.0 + s * sd for s in sigmas]

    cases = [
        ("steady", at(0.2, -0.1, 0.3, -0.2, 0.1), 0, "OK"),
        # R1: any point in the window, not only the latest.
        ("R1 latest spike", at(0.1, 0.0, -0.1, 0.2, 4.0), 3, "R1"),
        ("R1 mid-window spike", at(0.1, 4.0, -0.1, 0.2, 0.1), 3, "R1"),
        ("R1 negative spike", at(0.1, 0.0, -0.1, 0.2, -3.5), 3, "R1"),
        # Pins pstdev against sample stdev: under stdev this scores 2.81
        # and drops to tier 0.
        ("R1 three sigma by pstdev", at(0.1, 0.0, 0.1, 0.0, 3.0), 3, "R1"),
        ("R1 just under 3 sigma", at(0.1, 0.0, 0.1, 0.0, 2.9), 0, "OK"),
        ("R2 two of three high", at(0.0, 0.1, 2.5, 0.2, 2.4), 2, "R2"),
        ("R2 two of three low", at(0.0, 0.1, -2.5, 0.2, -2.4), 2, "R2"),
        ("R2 not same side", at(0.0, 0.1, 2.5, 0.2, -2.4), 0, "OK"),
        ("R3 four of five high", at(1.5, 1.5, 0.0, 1.5, 1.5), 1, "R3"),
        ("R3 four of five low", at(-1.5, -1.5, 0.0, -1.5, -1.5), 1, "R3"),
        ("R3 three of five only", at(1.5, 1.5, 0.0, 0.1, 1.5), 0, "OK"),
        # Pins R3's same-side condition: 4 of 5 beyond 1 sigma, mixed.
        ("R3 four of five mixed sides", at(1.5, -1.5, 1.5, -1.5, 0.1), 0, "OK"),
        ("R4 eight same side", at(0.5, 0.4, 0.6, 0.3, 0.5, 0.2, 0.4, 0.6), 1, "R4"),
        # Pins the run length at 8: seven points must not trigger.
        ("R4 only seven points", at(0.5, 0.4, 0.6, 0.3, 0.5, 0.2, 0.4), 0, "OK"),
        ("R4 eight then flip", at(0.5, 0.4, 0.6, 0.3, 0.5, 0.2, 0.4, -0.1), 0, "OK"),
        # Pins the run *window* at 8, not merely the point count: the last
        # six are one-sided but the run is broken two points back.
        ("R4 nine with early flip", at(0.5, -0.2, -0.3, 0.4, 0.6, 0.3, 0.5, 0.2, 0.4), 0, "OK"),
    ]

    # Boundary cases scored directly, bypassing the float round-trip
    # through zscores: `at(3.0)` does not land exactly on 3.0.
    zcases = [
        ("R1 exactly 3 sigma", [0.1, 0.0, 3.0], 3, "R1"),
        ("R1 a hair under 3 sigma", [0.1, 0.0, 2.999999], 0, "OK"),
        ("R2 exactly 2 sigma", [0.0, 2.0, 2.0], 2, "R2"),
        ("R3 exactly 1 sigma", [1.0, 1.0, 0.0, 1.0, 1.0], 1, "R3"),
    ]

    failures = 0
    for label, recent, want_tier, want_rule in cases:
        tier, _, rule, why = evaluate(zscores(base, recent))
        if (tier, rule) == (want_tier, want_rule):
            print(f"ok   - {label} -> tier {tier} ({rule})")
        else:
            print(
                f"FAIL - {label}: expected tier {want_tier} ({want_rule}), "
                f"got tier {tier} ({rule}: {why})"
            )
            failures += 1

    for label, z, want_tier, want_rule in zcases:
        tier, _, rule, why = evaluate(z)
        if (tier, rule) == (want_tier, want_rule):
            print(f"ok   - {label} -> tier {tier} ({rule})")
        else:
            print(
                f"FAIL - {label}: expected tier {want_tier} ({want_rule}), "
                f"got tier {tier} ({rule}: {why})"
            )
            failures += 1

    # Input guards. The short baseline varies, so it exercises the length
    # guard and not the sigma guard; the flat one does the reverse.
    guards = [
        ("short baseline", [1.0, 2.0, 3.0, 4.0], [1.0]),
        ("flat baseline", [5.0] * 10, [1.0]),
        ("non-numeric recent", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0], ["x"]),
        ("empty recent", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0], []),
    ]
    for label, bad_baseline, bad_recent in guards:
        try:
            zscores(bad_baseline, bad_recent)
        except ValueError:
            print(f"ok   - {label} rejected")
        else:
            print(f"FAIL - {label} was not rejected")
            failures += 1

    print(f"\n{'self-check failed' if failures else 'self-check passed'}")
    return 1 if failures else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline")
    p.add_argument("--recent")
    p.add_argument("--self-check", action="store_true")
    a = p.parse_args()

    if a.self_check:
        return self_check()
    if not (a.baseline and a.recent):
        p.error("--baseline and --recent are both required")

    try:
        with open(a.baseline) as f:
            baseline = json.load(f)
        with open(a.recent) as f:
            recent = json.load(f)
        tier, action, rule, why = evaluate(zscores(baseline, recent))
    except (OSError, ValueError) as e:
        # Consumers parse stdout as JSON; a traceback would break them.
        print(json.dumps({"error": str(e)}))
        return 1

    print(json.dumps({"tier": tier, "action": action, "rule": rule, "why": why}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
