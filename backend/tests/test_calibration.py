"""Calibration turns the raw model score into a probability: monotone, inside [0, 1]."""
import numpy as np

from flowtrace.ml.calibration import calibrate, fit


def test_fit_is_monotone_and_maps_into_unit_interval():
    rng = np.random.default_rng(0)
    p = rng.random(5000)
    y = (rng.random(5000) < p ** 3).astype(int)
    c = fit(p, y)
    assert c["x"] == sorted(c["x"]) and c["y"] == sorted(c["y"])
    out = calibrate(np.array([-1.0, 0.0, 0.5, 1.0, 2.0]), {"calibration": c})
    assert ((out >= 0) & (out <= 1)).all() and (np.diff(out) >= 0).all()


def test_calibrated_values_track_observed_rates():
    rng = np.random.default_rng(1)
    p = rng.random(20000)
    y = (rng.random(20000) < p ** 3).astype(int)
    out = calibrate(np.array([0.5, 0.9]), {"calibration": fit(p, y)})
    assert abs(out[0] - 0.125) < 0.05 and abs(out[1] - 0.729) < 0.06
