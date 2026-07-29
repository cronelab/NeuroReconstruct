"""
Tests for services.seeg_service. Self-contained: run directly with

    python backend/tests/test_seeg_service.py

(No pytest dependency in the neuro-recon env.) Uses the synthetic h5 generator.
"""

import os
import sys
import tempfile

import numpy as np

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)
sys.path.insert(0, os.path.join(_BACKEND, "scripts"))

from services import seeg_service as S           # noqa: E402
from make_fake_seeg_h5 import make_fake_h5        # noqa: E402


def test_parse():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.h5")
        chans = ["LAH1", "LAH2", "LPH1"]
        make_fake_h5(p, chans, active=["LAH2"], n_trials=10, duration_s=30)
        meta = S.parse_seeg_h5(p)
        assert meta["rate_hz"] == 2000.0, meta["rate_hz"]
        assert [c["name"] for c in meta["channels"]] == chans
        assert [c["group"] for c in meta["channels"]] == ["LAH", "LAH", "LPH"]
        assert len(meta["trials"]) == 10
        assert meta["attrs"]["task"] == "word_repetition"
    print("ok test_parse")


def test_name_join():
    # Channel 'LAH1' should match shaft 'LAH' / 'lah' / "L'AH" / 'L-A-H' etc.
    channels = [{"name": "LAH1", "group": "LAH"}, {"name": "LAH2", "group": "LAH"},
                {"name": "RX9", "group": "RX"}]
    native = [
        {"shaft_name": "lah", "contact_number": 1, "x_mm": 1.0, "y_mm": 2.0, "z_mm": 3.0},
        {"shaft_name": "L-A-H", "contact_number": 2, "x_mm": 4.0, "y_mm": 5.0, "z_mm": 6.0},
        {"shaft_name": "ZZ", "contact_number": 7, "x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
    ]
    mni = [{"shaft_name": "LAH", "contact_number": 1, "x_mni": -20.0, "y_mni": -10.0, "z_mni": -15.0}]
    j = S.join_channels_to_contacts(channels, native, mni)
    assert j["coords_native"]["LAH1"] == [1.0, 2.0, 3.0]
    assert j["coords_native"]["LAH2"] == [4.0, 5.0, 6.0]     # separator/case normalized
    assert j["coords_mni"]["LAH1"] == [-20.0, -10.0, -15.0]
    assert "RX9" in j["unmatched_channels"]                  # no contact for RX9
    assert "ZZ7" in j["unmatched_contacts"]                  # no channel for ZZ7
    assert set(j["matched"]) == {"LAH1", "LAH2"}
    print("ok test_name_join")


def test_group_disambiguation():
    # Shaft 'E1' contact 1 -> channel 'E11'. With the group provided, it must
    # resolve to ('e1', 1), NOT ('e', 11).
    assert S._group_and_number("E11", "E1") == ("e1", 1)
    assert S._group_and_number("E110", "E1") == ("e1", 10)
    # Join must map channel E11 (group E1) to shaft E1 contact 1, and NOT collide
    # with a hypothetical shaft E contact 11.
    channels = [{"name": "E11", "group": "E1"}, {"name": "E12", "group": "E1"}]
    native = [
        {"shaft_name": "E1", "contact_number": 1, "x_mm": 1.0, "y_mm": 1.0, "z_mm": 1.0},
        {"shaft_name": "E1", "contact_number": 2, "x_mm": 2.0, "y_mm": 2.0, "z_mm": 2.0},
    ]
    j = S.join_channels_to_contacts(channels, native, None)
    assert j["coords_native"]["E11"] == [1.0, 1.0, 1.0]
    assert j["coords_native"]["E12"] == [2.0, 2.0, 2.0]
    assert not j["unmatched_channels"]
    print("ok test_group_disambiguation")


def test_split_channel_name():
    assert S._split_channel_name("LAH1") == ("lah", 1)
    assert S._split_channel_name("L'AH12") == ("lah", 12)
    assert S._split_channel_name("EKG") == (None, None)
    print("ok test_split_channel_name")


def test_event_activity_rise():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.h5")
        chans = ["LAH1", "LAH2", "LAH3", "LPH1"]
        active = ["LAH2", "LAH3"]
        make_fake_h5(p, chans, active=active, n_trials=20, duration_s=60)
        out = S.compute_band_activity(p, band="high_gamma")
        assert out["channels"] == chans
        assert out["mode"] == "trial" and out["time_unit"] == "ms"
        assert out["groups"] == ["LAH", "LAH", "LAH", "LPH"]
        act = np.array(out["activity"])                     # (frames, channels)
        raw = np.array(out["raw"])                          # (frames, channels) uV ERP
        times = np.array(out["times"])                      # ms, post-stimulus only
        assert act.shape[0] == len(times)
        assert act.shape[1] == len(chans)
        assert raw.shape == act.shape and np.isfinite(raw).all()
        # Output is post-stimulus only (t >= 0); the baseline is not returned.
        assert times.min() >= 0
        # Active channels show a strong positive baseline-z in the burst (0..400 ms);
        # since z is normalized to the pre-onset baseline, that means z well above 0.
        post = act[(times > 20) & (times < 400)].mean(axis=0)
        for ch in active:
            assert post[chans.index(ch)] > 2.0, f"{ch}: post z={post[chans.index(ch)]:.2f} not > 2"
        # An inactive channel stays near baseline (small |z|) and well below active.
        assert post[chans.index("LAH1")] < 1.5
        assert post[chans.index("LAH2")] > post[chans.index("LAH1")] + 1.0
    print("ok test_event_activity_rise")


def test_degenerate_channel_finite():
    # A flat / all-zero channel must not produce NaN/Inf (would break JSON output).
    import h5py
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.h5")
        chans = ["LAH1", "LAH2", "LAH3"]
        make_fake_h5(p, chans, active=["LAH2"], n_trials=15, duration_s=40)
        with h5py.File(p, "r+") as h5:
            h5["ieeg/rate_2000hz/data"][:, 0] = 0.0   # zero out LAH1
        out = S.compute_band_activity(p, band="high_gamma")
        act = np.array(out["activity"])
        assert np.isfinite(act).all(), "non-finite values leaked through"
    print("ok test_degenerate_channel_finite")


def test_continuous_traces():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.h5")
        chans = ["LAH1", "LAH2", "LPH1"]
        make_fake_h5(p, chans, active=["LAH2"], n_trials=15, duration_s=30)
        out = S.compute_continuous_traces(p, band="high_gamma")
        assert out["mode"] == "scroll" and out["time_unit"] == "s"
        assert out["channels"] == chans
        act = np.array(out["activity"]); raw = np.array(out["raw"])
        t = np.array(out["times"])
        assert act.shape == raw.shape == (len(t), len(chans))
        assert np.isfinite(act).all() and np.isfinite(raw).all()
        assert t.min() >= 0 and t.max() <= 30.0
        assert act.shape[0] <= 2500
    print("ok test_continuous_traces")


def test_env_cache():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.h5")
        chans = ["LAH1", "LAH2", "LAH3"]
        make_fake_h5(p, chans, active=["LAH2"], n_trials=15, duration_s=30)
        cache = S._env_cache_path(p, "high_gamma")
        assert not os.path.exists(cache)
        out1 = S.compute_band_activity(p, band="high_gamma")     # miss -> writes cache
        assert os.path.exists(cache), "envelope cache not written"
        mt = os.path.getmtime(cache)
        out2 = S.compute_band_activity(p, band="high_gamma")     # hit -> same result
        assert out1["activity"] == out2["activity"]
        # A different mode for the SAME (file, band) reuses the cache, not rewrites it.
        S.compute_continuous_traces(p, band="high_gamma")
        assert os.path.getmtime(cache) == mt, "cache was rewritten instead of reused"
        # A different band uses a separate cache entry.
        assert S._env_cache_path(p, "beta") != cache
    print("ok test_env_cache")


def test_window_param():
    # The peri-stimulus window controls the time axis span.
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.h5")
        chans = ["LAH1", "LAH2"]
        make_fake_h5(p, chans, active=["LAH2"], n_trials=15, duration_s=40)
        out = S.compute_band_activity(p, band="high_gamma", window_ms=(-100.0, 500.0))
        t = np.array(out["times"])
        # Displayed time course is post-stimulus only: 0 .. end.
        assert 0 <= t.min() < 5, t.min()
        assert 495 < t.max() <= 500.5, t.max()
        assert np.array(out["activity"]).shape[0] == len(t)
    print("ok test_window_param")


if __name__ == "__main__":
    test_parse()
    test_split_channel_name()
    test_group_disambiguation()
    test_name_join()
    test_degenerate_channel_finite()
    test_continuous_traces()
    test_env_cache()
    test_window_param()
    test_event_activity_rise()
    print("\nALL PASSED")
