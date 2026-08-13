"""
Generate a synthetic NeurosEEGRead-schema HDF5 file for testing the sEEG viewer.

The file matches the real schema written by neuroseegread/write/hdf5.py closely
enough for parse_seeg_h5 / compute_band_activity: /ieeg/rate_2000hz with data,
channel_names/types/groups; a /trials group with stimulus onsets; and a /channels
inventory. A subset of channels gets a task-evoked high-gamma burst after each
trial onset so event-related high-gamma mapping shows a clear post-onset rise.

Usage:
    python backend/scripts/make_fake_seeg_h5.py --out fake.h5 \
        --channels LAH1,LAH2,LAH3,LPH1,LPH2 --active LAH2,LAH3

If --channels is omitted a default montage is used.
"""

import argparse
import numpy as np


def make_fake_h5(out_path, channel_names, active=None, fs=2000.0,
                 duration_s=60.0, n_trials=20, seed=0, channel_groups=None,
                 response_active=None, no_response_trials=()):
    """Write a synthetic NeurosEEGRead-schema h5.

    active:            channels with a high-gamma burst locked to STIMULUS onset.
    response_active:   channels with a high-gamma burst locked to RESPONSE onset
                       (defaults to none). Reaction times jitter per trial, so a
                       response-locked burst smears out under stimulus alignment and
                       vice versa -- which is exactly what the alignment toggle tests.
    no_response_trials: trial indices whose response is left undetected (NaN), to
                       exercise the drop path in response mode.
    """
    import h5py

    rng = np.random.default_rng(seed)
    active = set(active or channel_names[1::2])   # default: every other channel
    response_active = set(response_active or ())
    no_response_trials = set(no_response_trials)
    n = int(duration_s * fs)
    n_ch = len(channel_names)

    # Baseline: pink-ish noise (1/f) so lower bands dominate, plus white noise.
    t = np.arange(n) / fs
    data = rng.standard_normal((n, n_ch)).astype(np.float32) * 20e-6   # ~20 uV
    # Add a slow drift / alpha so non-gamma bands are non-trivial.
    for c in range(n_ch):
        data[:, c] += (10e-6 * np.sin(2 * np.pi * 10 * t + rng.uniform(0, 6.28))).astype(np.float32)

    # Trials evenly spaced, leaving margins for the peri-stimulus window.
    onsets = np.linspace(3.0, duration_s - 3.0, n_trials)
    # Per-trial reaction time (s): spoken response follows the stimulus by a jittered
    # lag. Undetected trials get NaN (no response_onset).
    reaction_times = rng.uniform(0.45, 0.75, n_trials)
    response_onsets = onsets + reaction_times
    for i in no_response_trials:
        response_onsets[i] = np.nan
        reaction_times[i] = np.nan
    hg_freq = 110.0  # within the 70-150 Hz high-gamma band

    def _add_burst(center_s, chan_set):
        c0 = int(center_s * fs)
        burst_len = int(0.5 * fs)                               # 0..500 ms raised-cosine
        k = np.arange(burst_len)
        env = (0.5 - 0.5 * np.cos(2 * np.pi * k / burst_len))  # Hann
        carrier = np.sin(2 * np.pi * hg_freq * (k / fs))
        burst = (env * carrier).astype(np.float32) * 60e-6      # strong, ~60 uV
        s, e = c0, min(c0 + burst_len, n)
        if s >= n:
            return
        for c, name in enumerate(channel_names):
            if name in chan_set:
                data[s:e, c] += burst[: e - s]

    for i, onset in enumerate(onsets):
        _add_burst(onset, active)                              # stimulus-locked burst
        r = response_onsets[i]
        if response_active and np.isfinite(r):
            _add_burst(r, response_active)                     # response-locked burst

    vlen = h5py.string_dtype(encoding="utf-8")
    types = ["seeg"] * n_ch
    # channel_groups is authoritative for the shaft. If not supplied, derive it by
    # stripping the trailing contact number (only correct when shaft names don't
    # themselves end in digits -- pass channel_groups explicitly when they do).
    if channel_groups is None:
        import re as _re
        groups = [_re.sub(r"\d+$", "", nm) for nm in channel_names]
    else:
        groups = list(channel_groups)

    with h5py.File(out_path, "w") as h5:
        h5.attrs["subject"] = "FAKE01"
        h5.attrs["block"] = 1
        h5.attrs["task"] = "word_repetition"
        h5.attrs["task_title"] = "Word Repetition"
        h5.attrs["experimenter"] = "test"
        h5.attrs["segment_duration_s"] = float(duration_s)
        h5.attrs["clock"] = "seconds relative to segment start (neural clock)"
        h5.attrs["detection_quality"] = 1.0

        g = h5.create_group("ieeg/rate_2000hz")
        d = g.create_dataset("data", data=data, compression="gzip", compression_opts=4)
        d.attrs["unit"] = "volts"
        d.attrs["rate_hz"] = float(fs)
        d.attrs["dims"] = "samples x channels"
        g.create_dataset("channel_names", data=np.array(channel_names, dtype=object), dtype=vlen)
        g.create_dataset("channel_types", data=np.array(types, dtype=object), dtype=vlen)
        g.create_dataset("channel_groups", data=np.array(groups, dtype=object), dtype=vlen)

        tg = h5.create_group("trials")
        tg.attrs["n_trials"] = n_trials
        tg.attrs["task"] = "word_repetition"
        tg.create_dataset("stimulus", data=np.array([f"word{i}" for i in range(n_trials)], dtype=object), dtype=vlen)
        tg.create_dataset("onset_source", data=np.array(["photodiode"] * n_trials, dtype=object), dtype=vlen)
        tg.create_dataset("stim_index", data=np.arange(n_trials, dtype=np.int32))
        st = tg.create_dataset("start_time", data=onsets.astype(np.float64))
        st.attrs["unit"] = "seconds"
        sp = tg.create_dataset("stop_time", data=(onsets + 1.0).astype(np.float64))
        sp.attrs["unit"] = "seconds"
        # Response timing (NeurosEEGRead schema): NaN when no response was detected.
        ro = tg.create_dataset("response_onset", data=response_onsets.astype(np.float64))
        ro.attrs["unit"] = "seconds"
        rf = tg.create_dataset("response_offset", data=(response_onsets + 0.3).astype(np.float64))
        rf.attrs["unit"] = "seconds"
        rt = tg.create_dataset("reaction_time", data=reaction_times.astype(np.float64))
        rt.attrs["unit"] = "seconds"
        snr = np.where(np.isfinite(response_onsets), 12.0, np.nan).astype(np.float64)
        rs = tg.create_dataset("response_snr", data=snr)
        rs.attrs["unit"] = "dB"

        cg = h5.create_group("channels")
        cg.create_dataset("names", data=np.array(channel_names, dtype=object), dtype=vlen)
        cg.create_dataset("types", data=np.array(types, dtype=object), dtype=vlen)
        cg.create_dataset("rates_hz", data=np.full(n_ch, fs, dtype=np.float64))

        # /spikes is always present in real files (empty for macro-only recordings).
        sg = h5.create_group("spikes")
        sg.attrs["n_channels"] = 0

    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="fake_seeg.h5")
    ap.add_argument("--channels", default="LAH1,LAH2,LAH3,LAH4,LPH1,LPH2,LPH3,RAH1,RAH2")
    ap.add_argument("--active", default=None, help="channels with a stimulus-locked HG burst")
    ap.add_argument("--response-active", default=None,
                    help="channels with a RESPONSE-locked HG burst (for testing response alignment)")
    ap.add_argument("--no-response-trials", default=None,
                    help="comma-separated trial indices left with no detected response (NaN)")
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--duration", type=float, default=60.0)
    args = ap.parse_args()
    chans = [c.strip() for c in args.channels.split(",") if c.strip()]
    active = [c.strip() for c in args.active.split(",")] if args.active else None
    resp_active = [c.strip() for c in args.response_active.split(",")] if args.response_active else None
    no_resp = ([int(i) for i in args.no_response_trials.split(",") if i.strip() != ""]
               if args.no_response_trials else ())
    path = make_fake_h5(args.out, chans, active=active, response_active=resp_active,
                        no_response_trials=no_resp, n_trials=args.trials, duration_s=args.duration)
    print(f"Wrote {path}: {len(chans)} channels, {args.trials} trials"
          + (f", response-locked on {resp_active}" if resp_active else ""))


if __name__ == "__main__":
    main()
