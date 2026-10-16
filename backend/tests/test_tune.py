"""Detector precision with few hits is pulled toward the pooled rate, not toward 1/2."""
from flowtrace.eval.tune import shrunk_precision


def test_rare_detector_is_pulled_to_the_pooled_rate():
    p = shrunk_precision(true=2, hits=2, pooled=0.02, prior_hits=20)
    assert 0.02 < p < 0.15


def test_well_measured_detector_keeps_its_rate():
    p = shrunk_precision(true=202, hits=664, pooled=0.02, prior_hits=20)
    assert abs(p - 202 / 664) < 0.01
