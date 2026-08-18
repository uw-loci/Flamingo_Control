"""Narrowing the Z sweep must never quietly shorten the acquisition.

Z travel is 61% of an LED overview tile (10.0 s of 16.28 s, measured over 140
tiles on 2026-08-17) and reducing the plane COUNT does not touch it -- the
subsample still spans the full range. Reducing the RANGE is the only lever, and
most of the range is empty: a bounding box is drawn around the whole sample but
any one tile sees a slice of it.

The risk is entirely one-sided. The overview's per-tile Z edges become the
laser acquisition's Z range, so a band that clips the sample does not produce a
slower overview -- it produces a truncated acquisition, discovered later, and
unrecoverable without re-running the sample. Every test here exists to pin the
conservative direction: no evidence means no narrowing, and content reaching
the edge of a narrowed band means sweep it again.

Run: python3 -m pytest tests/test_adaptive_z_band.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.utils.adaptive_z_band import (  # noqa: E402
    ContentExtent,
    ZBand,
    adaptation_is_not_paying,
    content_extent,
    describe_saving,
    full_band,
    neighbour_extents,
    predict_band,
    should_resweep,
)

Z_MIN, Z_MAX = 14.0, 24.0  # the measured 10 mm box
FULL = full_band(Z_MIN, Z_MAX)


def planes(*, lo=Z_MIN, hi=Z_MAX, n=21, content=(), peak=500.0, floor=2.0):
    """(z, focus_score) per plane, with `content` naming the sampled span."""
    step = (hi - lo) / (n - 1)
    out = []
    for i in range(n):
        z = lo + i * step
        inside = any(a <= z <= b for a, b in content)
        out.append((z, peak if inside else floor))
    return out


class TestFindingTheSampleInASweep:
    def test_content_is_bounded_by_where_the_focus_scores_are(self):
        extent = content_extent(planes(content=[(17.0, 19.0)]), FULL)
        assert extent.has_content
        assert extent.z_low == pytest.approx(17.0, abs=0.6)
        assert extent.z_high == pytest.approx(19.0, abs=0.6)

    def test_a_sweep_with_no_structure_reports_no_content(self):
        # Judged against the scan's brightest tile, not its own noise peak.
        # Against its own, every plane would clear the threshold and this tile
        # would report a full-depth extent that widens every neighbour's band.
        extent = content_extent(
            planes(content=[], floor=3.0), FULL, scan_peak_score=500.0
        )
        assert not extent.has_content

    def test_a_dim_but_real_tile_still_counts(self):
        extent = content_extent(
            planes(content=[(18.0, 20.0)], peak=120.0, floor=1.0),
            FULL,
            scan_peak_score=500.0,
        )
        assert extent.has_content

    def test_no_planes_is_not_an_exception(self):
        assert not content_extent([], FULL).has_content

    def test_all_zero_scores_report_nothing(self):
        assert not content_extent([(15.0, 0.0), (16.0, 0.0)], FULL).has_content

    def test_content_spanning_the_whole_sweep_is_still_content(self):
        extent = content_extent(planes(content=[(Z_MIN, Z_MAX)]), FULL)
        assert extent.has_content


class TestEdgeContactIsAFailureToMeasure:
    """The whole safety argument. A clipped band means an unknown Z extent."""

    BAND = ZBand(z_min=17.0, z_max=20.0, full=False)

    def test_content_running_to_the_bottom_of_a_narrow_band_is_flagged(self):
        extent = content_extent(
            planes(lo=17.0, hi=20.0, n=13, content=[(17.0, 18.5)]), self.BAND
        )
        assert extent.touches_low_edge
        assert should_resweep(extent, self.BAND)

    def test_content_running_to_the_top_is_flagged(self):
        extent = content_extent(
            planes(lo=17.0, hi=20.0, n=13, content=[(18.5, 20.0)]), self.BAND
        )
        assert extent.touches_high_edge
        assert should_resweep(extent, self.BAND)

    def test_content_comfortably_inside_is_not_flagged(self):
        extent = content_extent(
            planes(lo=17.0, hi=20.0, n=13, content=[(18.0, 19.0)]), self.BAND
        )
        assert not extent.touches_an_edge
        assert not should_resweep(extent, self.BAND)

    def test_the_box_edge_is_not_a_band_edge(self):
        # Content at the bottom of the FULL range means the bounding box is too
        # small. Sweeping the full range again cannot discover anything more, so
        # it must not be mistaken for a clipped band.
        extent = content_extent(planes(content=[(Z_MIN, 16.0)]), FULL)
        assert not extent.touches_an_edge
        assert not should_resweep(extent, FULL)

    def test_an_empty_narrow_band_is_re_swept_not_believed(self):
        # An empty band and a MISSED band are the same picture: a sample that
        # stepped clean out of the prediction leaves exactly these planes
        # behind. Believing "found nothing" here loses the whole tile -- the
        # scan-loop test caught this with a sample that jumps 4 mm between
        # columns, where the narrow band contained no sample at all and the
        # earlier rule raised nothing.
        extent = content_extent(
            planes(lo=17.0, hi=20.0, content=[], floor=1.0),
            self.BAND,
            scan_peak_score=500.0,
        )
        assert not extent.has_content
        assert should_resweep(extent, self.BAND)

    def test_an_empty_full_sweep_is_believed(self):
        # After the full range found nothing, the tile really is empty and
        # sweeping it a third time discovers nothing.
        extent = content_extent(
            planes(content=[], floor=1.0), FULL, scan_peak_score=500.0
        )
        assert not should_resweep(extent, FULL)

    def test_edge_contact_is_judged_against_the_planes_actually_swept(self):
        # A capped sweep does not reach the arithmetic ends of its band.
        # Comparing against those would report every tile as clear of the edges
        # and disable the safety net entirely.
        band = ZBand(z_min=17.0, z_max=20.0, full=False)
        coarse = [(17.4, 500.0), (18.2, 500.0), (19.0, 5.0), (19.8, 5.0)]
        assert content_extent(coarse, band).touches_low_edge


class TestPredictingTheNextBand:
    A = ContentExtent(z_low=17.0, z_high=19.0)
    B = ContentExtent(z_low=18.0, z_high=21.0)

    def test_the_band_spans_every_neighbour_plus_margin(self):
        band = predict_band([self.A, self.B], z_min=Z_MIN, z_max=Z_MAX, margin_mm=0.5)
        assert band.z_min == pytest.approx(16.5)
        assert band.z_max == pytest.approx(21.5)
        assert not band.full

    def test_no_neighbours_means_no_narrowing(self):
        # The first tile of a scan, and every tile whose neighbours found
        # nothing. Narrowing on no evidence is the one guess this must not make.
        assert predict_band([], z_min=Z_MIN, z_max=Z_MAX).full

    def test_neighbours_without_content_are_not_evidence(self):
        assert predict_band(
            [ContentExtent(), ContentExtent()], z_min=Z_MIN, z_max=Z_MAX
        ).full

    def test_the_band_never_leaves_the_bounding_box(self):
        band = predict_band(
            [ContentExtent(z_low=Z_MIN + 0.1, z_high=Z_MAX - 0.1)],
            z_min=Z_MIN,
            z_max=Z_MAX,
            margin_mm=5.0,
        )
        assert band.z_min >= Z_MIN and band.z_max <= Z_MAX

    def test_a_band_as_wide_as_the_box_is_reported_as_full(self):
        # So edge contact is not then treated as a clipped measurement.
        band = predict_band(
            [ContentExtent(z_low=Z_MIN, z_high=Z_MAX)], z_min=Z_MIN, z_max=Z_MAX
        )
        assert band.full

    def test_a_pinpoint_neighbour_still_gets_a_usable_band(self):
        band = predict_band(
            [ContentExtent(z_low=19.0, z_high=19.0)],
            z_min=Z_MIN,
            z_max=Z_MAX,
            margin_mm=0.0,
            min_band_mm=1.0,
        )
        assert band.depth_mm == pytest.approx(1.0)
        assert band.z_min < 19.0 < band.z_max

    def test_narrowing_actually_saves_travel(self):
        band = predict_band([self.A], z_min=Z_MIN, z_max=Z_MAX, margin_mm=0.5)
        assert band.depth_mm < (Z_MAX - Z_MIN)


class TestNeighboursAreGridAdjacentNotScanAdjacent:
    def test_every_scanned_neighbour_is_collected(self):
        extents = {
            (1, 1): ContentExtent(z_low=1.0, z_high=2.0),  # y - 1
            (1, 3): ContentExtent(z_low=3.0, z_high=4.0),  # y + 1
            (2, 2): ContentExtent(z_low=5.0, z_high=6.0),  # x + 1
            (4, 9): ContentExtent(z_low=7.0, z_high=8.0),  # far away
        }
        found = neighbour_extents(extents, 1, 2)
        assert len(found) == 3
        assert all(e.has_content for e in found)

    def test_an_unscanned_neighbour_contributes_nothing(self):
        # Half the neighbourhood is always ahead of the scan.
        assert neighbour_extents({(0, 0): ContentExtent()}, 0, 1) == [ContentExtent()]

    def test_a_diagonal_is_not_a_neighbour(self):
        extents = {(0, 0): ContentExtent(z_low=1.0, z_high=2.0)}
        assert neighbour_extents(extents, 1, 1) == []

    def test_the_first_tile_has_none(self):
        assert neighbour_extents({}, 0, 0) == []


class TestGivingUpWhenItDoesNotFit:
    """Samples this heuristic does not fit must cost time, not data.

    Judged on the travel actually spent, not on how often tiles were re-swept.
    The re-sweep rate is a proxy and a misleading one: a sample with half its
    tiles empty re-sweeps half the time and still saves a quarter of the
    travel, because the misses are cheap narrow bands and the rest are real
    savings. Measured on a 10x14 grid, judging by rate threw that away.
    """

    DEPTH = 10.0

    def test_a_run_saving_nothing_is_stopped(self):
        assert adaptation_is_not_paying(20, 20 * self.DEPTH, self.DEPTH)

    def test_a_run_costing_more_than_the_baseline_is_stopped(self):
        assert adaptation_is_not_paying(20, 25 * self.DEPTH, self.DEPTH)

    def test_a_run_saving_well_keeps_adapting(self):
        assert not adaptation_is_not_paying(20, 4 * self.DEPTH, self.DEPTH)

    def test_a_modest_saving_is_still_a_saving(self):
        # 25% less travel over a 38-minute angle is nine minutes. Worth keeping.
        assert not adaptation_is_not_paying(20, 15 * self.DEPTH, self.DEPTH)

    def test_judgement_waits_for_enough_tiles(self):
        # Two unlucky tiles at the start of a 140-tile scan are not a verdict.
        assert not adaptation_is_not_paying(2, 2 * self.DEPTH, self.DEPTH)

    def test_no_tiles_is_not_a_verdict(self):
        assert not adaptation_is_not_paying(0, 0.0, self.DEPTH)

    def test_a_zero_depth_box_is_not_a_verdict(self):
        assert not adaptation_is_not_paying(20, 0.0, 0.0)


class TestTheSummaryIsHonest:
    def test_it_reports_travel_against_the_baseline(self):
        text = describe_saving(10.0, [3.0] * 10, tiles=10, resweeps=0)
        assert "70% less Z travel" in text

    def test_the_baseline_is_tiles_not_sweeps(self):
        # A re-swept tile appears TWICE in the sweep list. Sizing the baseline
        # from that list inflates it by exactly the re-sweeps, so the runs that
        # saved least would report the largest saving. Ten tiles, five of them
        # re-swept at full depth: 80 mm against a 100 mm baseline, not 150.
        text = describe_saving(10.0, [3.0] * 10 + [10.0] * 5, tiles=10, resweeps=5)
        assert "against 100.0 mm" in text
        assert "20% less Z travel" in text

    def test_resweeps_are_named_not_hidden(self):
        # They ARE the cost of being safe, and a reader comparing runs needs to
        # see how much of the time went on them.
        assert "5 re-sweep(s)" in describe_saving(10.0, [3.0], tiles=10, resweeps=5)

    def test_nothing_swept_says_so(self):
        assert "no tiles swept" in describe_saving(10.0, [], 0, 0)
