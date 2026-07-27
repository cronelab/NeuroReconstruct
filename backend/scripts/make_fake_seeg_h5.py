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
                 duration_s=60.0, n_trials=20, seed=0, channel_groups=None):
    import h5py

    rng = np.random.default_rng(seed)
    active = set(active or channel_names[1::2])   # default: every other channel
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
    hg_freq = 110.0  # within the 70-150 Hz high-gamma band

    for onset in onsets:
        c0 = int(onset * fs)
        # Post-onset high-gamma burst: 0..500 ms, raised-cosine envelope.
        burst_len = int(0.5 * fs)
        k = np.arange(burst_len)
        env = (0.5 - 0.5 * np.cos(2 * np.pi * k / burst_len))  # Hann
        carrier = np.sin(2 * np.pi * hg_freq * (k / fs))
        burst = (env * carrier).astype(np.float32) * 60e-6      # strong, ~60 uV
        s, e = c0, min(c0 + burst_len, n)
        for c, name in enumerate(channel_names):
            if name in active:
                data[s:e, c] += burst[: e - s]

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
    ap.add_argument("--active", default=None, help="comma-separated channels with the HG burst")
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--duration", type=float, default=60.0)
    args = ap.parse_args()
    chans = [c.strip() for c in args.channels.split(",") if c.strip()]
    active = [c.strip() for c in args.active.split(",")] if args.active else None
    path = make_fake_h5(args.out, chans, active=active, n_trials=args.trials, duration_s=args.duration)
    print(f"Wrote {path}: {len(chans)} channels, {args.trials} trials")


if __name__ == "__main__":
    main()
