"""
Tests for services.seeg_service. Self-contained: run directly with

    python backend/tests/test_seeg_service.py

(No pytest dependency in the neuro-recon env.) Uses the synthetic h5 generator.
"""

import base64
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
        raw = np.array(out["raw"])                          # (trace frames, channels) uV ERP
        times = np.array(out["times"])                      # ms, full peri-event window
        assert out["align"] == "stimulus"
        assert act.shape[0] == len(times)
        assert act.shape[1] == len(chans)
        assert raw.shape == (len(out["trace_times"]), len(chans)) and np.isfinite(raw).all()
        assert out["map_nyquist_met"]
        # Output now spans the full window (default start -500 ms); baseline separate.
        assert -505 <= times.min() <= -495, times.min()
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
        t = np.array(out["times"]); tt = np.array(out["trace_times"])
        assert act.shape == (len(t), len(chans))
        assert raw.shape == (len(tt), len(chans))
        assert np.isfinite(act).all() and np.isfinite(raw).all()
        assert t.min() >= 0 and t.max() <= 30.0
        # Map frames at the high-gamma envelope's Nyquist stride: 2000 Hz / (2 * 80 Hz) -> 12.
        assert np.allclose(np.diff(t), 12 / 2000.0, atol=1e-4)
        assert out["map_nyquist_met"] and out["map_rate_hz"] >= out["map_nyquist_hz"]
        assert len(tt) <= S.TRACE_FRAMES_SCROLL
    print("ok test_continuous_traces")


def test_envelope_nyquist_step():
    # The envelope of a [lo, hi] band spans 0..(hi - lo) Hz, so the stride is fs / 2(hi-lo).
    assert S.envelope_nyquist_step(2000.0, (1.0, 70.0)) == 14       # 142.9 Hz >= 138 Hz
    assert S.envelope_nyquist_step(2000.0, (70.0, 150.0)) == 12     # 166.7 Hz >= 160 Hz
    assert S.envelope_nyquist_step(2000.0, (1.0, 4.0)) == 333       # 6.006 Hz >= 6 Hz
    for band in S.BANDS.values():
        step = S.envelope_nyquist_step(2000.0, band)
        assert 2000.0 / step >= 2 * (band[1] - band[0])
    # A band past what the rate can represent clamps; a degenerate one needs every sample.
    assert S.envelope_nyquist_step(200.0, (150.0, 300.0)) == 1
    print("ok test_envelope_nyquist_step")


def test_map_budget_fallback_lowpasses():
    # Within budget the Nyquist stride stands; past it the stride widens and each bin is
    # averaged, so a fast oscillation is attenuated rather than aliased into a slow one.
    assert S.map_step(600_000, 79, 2000.0, (1.0, 70.0), budget=10**8) == (14, 14)
    step, nyq = S.map_step(600_000, 79, 2000.0, (1.0, 70.0), budget=79 * 1000)
    assert nyq == 14 and step == 600 and np.ceil(600_000 / step) * 79 <= 79 * 1000
    fs, n = 2000.0, 60_000
    # 51 Hz, so the strided samples do not happen to land on zero crossings.
    x = np.sin(2 * np.pi * 51.0 * np.arange(n) / fs)[:, None].astype(np.float32)
    point = x[::600]                                     # what plain striding would show
    binned = S._reduce_map(x, 600, 14)
    assert np.abs(binned).max() < 0.05 < np.abs(point).max()
    print("ok test_map_budget_fallback_lowpasses")


def test_encode_activity_response():
    act = np.array([[0.0, -1.234], [3.456, 400.0]], np.float32)
    res = {"channels": ["A1", "A2"], "times": [0.0, 1.0], "activity": act,
           "trace_times": [0.0], "raw": np.array([[1.23456, 2.0]], np.float32),
           "raw_min": np.zeros((0, 2), np.float32), "raw_max": np.zeros((0, 2), np.float32)}
    out = S.encode_activity_response(res)
    assert not any(isinstance(v, np.ndarray) for v in out.values())
    q = np.frombuffer(base64.b64decode(out["activity_b64"]), "<i2").reshape(out["activity_shape"])
    assert np.allclose(q * out["activity_scale"], np.clip(act, -327.67, 327.67), atol=0.006)
    assert out["raw"] == [[1.23, 2.0]] and out["raw_min"] == []
    # The viewer's trace-only fetch omits the map and its axis.
    slim = S.encode_activity_response(res, include_activity=False)
    assert "activity_b64" not in slim and "times" not in slim and "trace_times" in slim
    print("ok test_encode_activity_response")


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
        assert np.array_equal(out1["activity"], out2["activity"])
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
        # Displayed time course spans the full window: start .. end.
        assert -105 < t.min() <= -95, t.min()
        # The map's last frame falls within one Nyquist-spaced frame of the last sample
        # (499.5 ms at 2 kHz).
        frame_ms = 1000.0 / out["map_rate_hz"]
        assert 499.5 - frame_ms - 1e-3 <= t.max() <= 500.5, t.max()
        assert 495 < max(out["trace_times"]) <= 500.5
        assert np.array(out["activity"]).shape[0] == len(t)
    print("ok test_window_param")


def test_response_alignment():
    # LAH2 bursts at STIMULUS onset; LRESP1 bursts at RESPONSE onset. Response-aligned
    # averaging should reveal LRESP1's burst near t=0 (the response), and drop the one
    # trial with no detected response.
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.h5")
        chans = ["LAH1", "LAH2", "LRESP1"]
        make_fake_h5(p, chans, active=["LAH2"], response_active=["LRESP1"],
                     n_trials=20, duration_s=60, no_response_trials=[19])
        out = S.compute_band_activity(p, band="high_gamma", align="response",
                                      window_ms=(-800.0, 800.0))
        assert out["align"] == "response"
        assert out["n_no_response"] >= 1, out["n_no_response"]      # trial 19 dropped
        assert out["n_trials"] == 19, out["n_trials"]
        act = np.array(out["activity"]); times = np.array(out["times"])
        assert times.min() < 0 < times.max()                        # full window
        near0 = act[(times > -100) & (times < 400)].mean(axis=0)    # around the response
        assert near0[chans.index("LRESP1")] > 2.0, near0[chans.index("LRESP1")]

        # A file with NO responses at all -> response mode raises.
        p2 = os.path.join(d, "g.h5")
        make_fake_h5(p2, chans, active=["LAH2"], n_trials=10, duration_s=30,
                     no_response_trials=list(range(10)))
        try:
            S.compute_band_activity(p2, band="high_gamma", align="response")
            assert False, "expected ValueError for a file with no responses"
        except ValueError as e:
            assert "response" in str(e).lower(), str(e)
    print("ok test_response_alignment")


def test_stimulus_relative_baseline():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.h5")
        chans = ["LAH1", "LAH2", "LRESP1"]
        make_fake_h5(p, chans, active=["LAH2"], response_active=["LRESP1"],
                     n_trials=20, duration_s=60)
        # An explicit pre-stimulus baseline window is accepted and stays finite even
        # when the display is aligned to the response.
        out = S.compute_band_activity(p, band="high_gamma", align="response",
                                      window_ms=(-800.0, 800.0),
                                      baseline_ms=(-500.0, -100.0))
        assert np.isfinite(np.array(out["activity"])).all()
        # Stimulus mode is unchanged whether baseline is omitted (defaults to the whole
        # pre-stimulus interval [window[0], 0]) or passed explicitly as that interval.
        a = S.compute_band_activity(p, band="high_gamma", window_ms=(-500.0, 1500.0))
        b = S.compute_band_activity(p, band="high_gamma", window_ms=(-500.0, 1500.0),
                                    baseline_ms=(-500.0, 0.0))
        assert np.array_equal(a["activity"], b["activity"])
    print("ok test_stimulus_relative_baseline")


if __name__ == "__main__":
    test_parse()
    test_split_channel_name()
    test_group_disambiguation()
    test_name_join()
    test_degenerate_channel_finite()
    test_continuous_traces()
    test_envelope_nyquist_step()
    test_map_budget_fallback_lowpasses()
    test_encode_activity_response()
    test_env_cache()
    test_window_param()
    test_event_activity_rise()
    test_response_alignment()
    test_stimulus_relative_baseline()
    print("\nALL PASSED")
