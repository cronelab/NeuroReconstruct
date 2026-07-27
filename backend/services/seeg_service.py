"""
sEEG functional-mapping service.

Reads an HDF5 file produced by NeurosEEGRead (the ``neuroseegread`` package),
computes a per-channel activity metric (band power over time), and joins the
channels to a reconstruction's localized electrode contacts *by name* so the
activity can be rendered on a brain surface.

Why the name-join: the NeurosEEGRead h5 carries no electrode coordinates -- only
named channels (e.g. ``LAH1``), their shaft group (``LAH``), channel types, and
raw voltage time-series. Coordinates come from NeuroReconstruct's own electrode
localization (native mm in the DB, and MNI mm in ``export/electrodes_mni.json``),
matched to the h5 channels by shaft name + contact number.

HDF5 schema (relevant parts), from ``neuroseegread/write/hdf5.py``:
  /ieeg/rate_<int>hz/{data (samples x channels, float32 volts),
                      channel_names, channel_types, channel_groups}  (vlen utf-8)
  /trials/{stimulus, start_time, stop_time, ...}   parallel columns, seconds
  /channels/{names, types, rates_hz}               full inventory
  root attrs: subject, block, task, task_title, segment_duration_s, ...

All heavy numeric work uses numpy/scipy (already dependencies); h5py is the only
new dependency. Log output stays ASCII (uvicorn stdout is cp1252 on Windows).
"""

import os
import re
import json

import numpy as np

# Channel types we map onto the brain. Micro-wire and EKG are excluded.
MAPPABLE_TYPES = {"seeg", "scalp_eeg"}

# Frequency bands (Hz). "high_gamma" is the standard functional-mapping band.
BANDS = {
    "delta":      (1.0, 4.0),
    "theta":      (4.0, 8.0),
    "alpha":      (8.0, 13.0),
    "beta":       (13.0, 30.0),
    "gamma":      (30.0, 70.0),
    "high_gamma": (70.0, 150.0),
}

DEFAULT_BAND = "high_gamma"
DEFAULT_WINDOW_MS = (-200.0, 800.0)   # peri-stimulus window (relative to onset)
MAX_OUTPUT_FRAMES = 1500              # cap frames sent to the client per request


# ── H5 parsing ──────────────────────────────────────────────────────────────

def _decode(dset):
    """Read a (possibly vlen-utf8) 1-D string dataset into a list[str]."""
    try:
        return [str(x) for x in dset.asstr()[:]]
    except (AttributeError, TypeError):
        out = []
        for x in dset[:]:
            out.append(x.decode("utf-8") if isinstance(x, bytes) else str(x))
        return out


def _pick_ieeg_group(h5):
    """
    Return (group_name, rate_hz) for the ieeg subgroup to map.

    Prefer the 2000 Hz neural group; otherwise the lowest-rate group present
    (macro sEEG lives at the low rate; 32000 Hz is BNC analog).
    """
    if "ieeg" not in h5:
        raise ValueError("h5 has no /ieeg group -- not a NeurosEEGRead file?")
    ieeg = h5["ieeg"]
    rates = {}
    for name in ieeg:
        m = re.match(r"rate_(\d+)hz$", name)
        if m and "data" in ieeg[name]:
            rates[name] = int(m.group(1))
    if not rates:
        raise ValueError("/ieeg has no rate_<n>hz/data subgroup")
    if any(r == 2000 for r in rates.values()):
        name = next(n for n, r in rates.items() if r == 2000)
    else:
        name = min(rates, key=lambda n: rates[n])
    return name, float(rates[name])


def parse_seeg_h5(path: str) -> dict:
    """
    Read metadata + mappable-channel index from a NeurosEEGRead h5.

    Returns a dict with:
      rate_hz, group_name, n_samples,
      channels: [{name, type, group, col}]  (mappable channels only, col into data)
      trials:   [{stimulus, start_time, stop_time}]
      attrs:    {subject, task, task_title, block, segment_duration_s}
    Does NOT load the full signal array (that happens in compute_band_activity).
    """
    import h5py

    with h5py.File(path, "r") as h5:
        group_name, rate_hz = _pick_ieeg_group(h5)
        grp = h5["ieeg"][group_name]
        data = grp["data"]
        n_samples = int(data.shape[0])
        n_channels = int(data.shape[1]) if data.ndim == 2 else 0

        names = _decode(grp["channel_names"]) if "channel_names" in grp else []
        types = _decode(grp["channel_types"]) if "channel_types" in grp else []
        groups = _decode(grp["channel_groups"]) if "channel_groups" in grp else []

        # Pad missing parallel arrays so indexing is always safe.
        def _at(lst, i, default=""):
            return lst[i] if i < len(lst) else default

        channels = []
        for col in range(n_channels):
            ctype = _at(types, col).lower()
            if ctype not in MAPPABLE_TYPES:
                continue
            channels.append({
                "name": _at(names, col, f"ch{col}"),
                "type": ctype,
                "group": _at(groups, col),
                "col": col,
            })

        trials = []
        if "trials" in h5:
            tg = h5["trials"]
            stim = _decode(tg["stimulus"]) if "stimulus" in tg else []
            start = tg["start_time"][:] if "start_time" in tg else np.array([])
            stop = tg["stop_time"][:] if "stop_time" in tg else np.array([])
            for i in range(len(start)):
                trials.append({
                    "stimulus": stim[i] if i < len(stim) else "",
                    "start_time": float(start[i]),
                    "stop_time": float(stop[i]) if i < len(stop) else None,
                })

        def _attr(key, default=None):
            v = h5.attrs.get(key, default)
            if isinstance(v, bytes):
                return v.decode("utf-8")
            if isinstance(v, np.generic):
                return v.item()
            return v

        attrs = {
            "subject": _attr("subject"),
            "task": _attr("task"),
            "task_title": _attr("task_title"),
            "block": _attr("block"),
            "segment_duration_s": _attr("segment_duration_s"),
        }

    return {
        "rate_hz": rate_hz,
        "group_name": group_name,
        "n_samples": n_samples,
        "channels": channels,
        "trials": trials,
        "attrs": attrs,
    }


# ── Signal processing ────────────────────────────────────────────────────────

def _band_envelope(sig: np.ndarray, fs: float, band: tuple) -> np.ndarray:
    """
    Analytic-signal amplitude (Hilbert envelope) of ``sig`` bandpassed to ``band``.

    sig: (n_samples, n_channels) float. Returns same shape, non-negative envelope.
    """
    from scipy.signal import butter, filtfilt, hilbert

    lo, hi = band
    nyq = fs / 2.0
    hi = min(hi, nyq * 0.99)
    lo = max(lo, 0.1)
    if lo >= hi:
        # Degenerate band for this sampling rate -> fall back to |signal|.
        return np.abs(sig).astype(np.float32)

    b, a = butter(4, [lo / nyq, hi / nyq], btype="band")
    # filtfilt/hilbert operate along axis 0 (time).
    filtered = filtfilt(b, a, sig, axis=0)
    env = np.abs(hilbert(filtered, axis=0))
    return env.astype(np.float32)


def compute_band_activity(path: str, band: str = DEFAULT_BAND,
                          window_ms: tuple = DEFAULT_WINDOW_MS,
                          baseline_ms: tuple = None,
                          max_frames: int = MAX_OUTPUT_FRAMES) -> dict:
    """
    Compute the event-related display activity matrix for one h5 recording.

    Epochs each channel around every ``/trials`` onset over the peri-stimulus
    ``window_ms`` [start, end] (start < 0 < end, relative to onset), z-scores each
    epoch to its pre-onset baseline, and averages across trials.

    band:        key in BANDS.
    window_ms:   peri-stimulus window in ms [start, end], start < 0 < end.
    baseline_ms: baseline window in ms [start, end]; defaults to the entire
                 pre-stimulus interval [window_ms[0], 0].

    Returns:
      channels: [name, ...]              (mappable channels, order matches columns)
      times:    [t_ms, ...]              peri-stimulus time in ms
      activity: [[v, ...], ...]          shape (n_frames, n_channels), baseline z
      band, n_trials
    """
    import h5py

    if band not in BANDS:
        raise ValueError(f"unknown band {band!r}; options: {sorted(BANDS)}")
    if baseline_ms is None:
        baseline_ms = (window_ms[0], 0.0)
    meta = parse_seeg_h5(path)
    fs = meta["rate_hz"]
    cols = [c["col"] for c in meta["channels"]]
    names = [c["name"] for c in meta["channels"]]
    if not cols:
        return {"channels": [], "times": [], "activity": [],
                "band": band, "n_trials": len(meta["trials"])}

    with h5py.File(path, "r") as h5:
        # Load only the mappable columns (h5py fancy-indexing needs sorted, unique).
        data = h5["ieeg"][meta["group_name"]]["data"]
        order = np.argsort(cols)
        sorted_cols = list(np.array(cols)[order])
        sig_sorted = data[:, sorted_cols].astype(np.float32)
        # Restore requested channel order.
        inv = np.argsort(order)
        sig = sig_sorted[:, inv]

    env = _band_envelope(sig, fs, BANDS[band])

    # ── Trial-averaged: epoch around trial onsets ─────────────────────────────
    trials = meta["trials"]
    onsets = np.array([t["start_time"] for t in trials], dtype=np.float64)
    if len(onsets) == 0:
        raise ValueError("no trials in h5 -- event-related mapping needs /trials onsets")

    w0, w1 = window_ms[0] / 1000.0, window_ms[1] / 1000.0     # seconds
    pre = int(round(-w0 * fs)) if w0 < 0 else 0
    post = int(round(w1 * fs))
    n_pst = pre + post
    pst_times_ms = (np.arange(-pre, post) / fs) * 1000.0

    n_ch = env.shape[1]
    n_samp = env.shape[0]
    acc = np.zeros((n_pst, n_ch), dtype=np.float64)
    used = 0
    b0 = int(round(baseline_ms[0] / 1000.0 * fs)) + pre   # index into epoch
    b1 = int(round(baseline_ms[1] / 1000.0 * fs)) + pre
    b0, b1 = max(0, b0), max(1, min(n_pst, b1))

    for onset in onsets:
        c = int(round(onset * fs))
        s, e = c - pre, c + post
        if s < 0 or e > n_samp:
            continue  # epoch runs off the segment edge
        epoch = env[s:e]                                    # (n_pst, n_ch)
        base = epoch[b0:b1]
        mu = base.mean(axis=0)
        sd = base.std(axis=0) + 1e-9
        acc += (epoch - mu) / sd
        used += 1

    if used == 0:
        raise ValueError("all trial epochs fell outside the segment -- check onsets")
    avg = acc / used                                        # trial-averaged z
    # Degenerate channels (flat / all-zero signal) can yield NaN/Inf, which is not
    # JSON-serializable -- map them to 0 (neutral / baseline).
    avg = np.nan_to_num(avg, nan=0.0, posinf=0.0, neginf=0.0)

    # Decimate peri-stimulus time if very long.
    if n_pst > max_frames:
        step = int(np.ceil(n_pst / max_frames))
        sel = np.arange(0, n_pst, step)
        avg = avg[sel]
        pst_times_ms = pst_times_ms[sel]

    return {
        "channels": names,
        "times": [round(float(t), 2) for t in pst_times_ms],
        "activity": np.round(avg, 3).tolist(),
        "band": band, "n_trials": used,
    }


# ── Channel <-> contact name join ────────────────────────────────────────────

def _split_channel_name(name: str):
    """
    'LAH1' -> ('lah', 1). Strips separators (' - space) and lowercases the group.
    Returns (group, number) or (None, None) if no trailing number.
    """
    m = re.match(r"^(.*?)(\d+)\s*$", str(name).strip())
    if not m:
        return None, None
    group = re.sub(r"[\s'\-_]", "", m.group(1)).lower()
    return group, int(m.group(2))


def _norm_group(name: str) -> str:
    return re.sub(r"[\s'\-_]", "", str(name)).lower()


def _group_and_number(name: str, group: str = ""):
    """
    Resolve (normalized shaft, contact number) for a channel.

    Prefer the h5's ``channel_groups`` value (the real shaft) when present -- this
    disambiguates montages whose shaft names themselves end in digits (e.g. shaft
    ``E1`` contact ``1`` => channel ``E11``, which a naive digit-split would read
    as shaft ``E`` contact ``11``). Falls back to splitting trailing digits when no
    group is given.
    """
    name_s = str(name).strip()
    if group:
        gn = _norm_group(group)
        norm_name = re.sub(r"[\s'\-_]", "", name_s).lower()
        if gn and norm_name.startswith(gn):
            m = re.match(r"(\d+)$", norm_name[len(gn):])
            if m:
                return gn, int(m.group(1))
    return _split_channel_name(name_s)


def join_channels_to_contacts(channels: list, native_contacts: list,
                              mni_rows: list = None) -> dict:
    """
    Match h5 channels to reconstruction contacts by shaft name + contact number.

    channels:        parse_seeg_h5()['channels'] (each has name/group)
    native_contacts: [{shaft_name, contact_number, x_mm, y_mm, z_mm}]
    mni_rows:        electrodes_mni.json rows [{shaft_name, contact_number, x_mni, ...}] or None

    Returns:
      coords_native: {channel_name: [x, y, z]}   (mesh-centered mm)
      coords_mni:    {channel_name: [x, y, z]}   (true MNI RAS mm) or {}
      matched:       [channel_name, ...]         channels placed in >=1 space
      unmatched_channels: [channel_name, ...]    h5 channels with no contact
      unmatched_contacts: ["SHAFT#", ...]        contacts with no channel
    """
    nat = {}
    for c in native_contacts:
        if c.get("x_mm") is None:
            continue
        key = (_norm_group(c.get("shaft_name", "")), int(c["contact_number"]))
        nat[key] = [float(c["x_mm"]), float(c["y_mm"]), float(c["z_mm"])]

    mni = {}
    for r in (mni_rows or []):
        key = (_norm_group(r.get("shaft_name", "")), int(r["contact_number"]))
        mni[key] = [float(r["x_mni"]), float(r["y_mni"]), float(r["z_mni"])]

    coords_native, coords_mni, matched, unmatched_channels = {}, {}, [], []
    matched_keys = set()
    for ch in channels:
        group, num = _group_and_number(ch.get("name", ""), ch.get("group", ""))
        if group is None:
            unmatched_channels.append(ch["name"])
            continue
        key = (group, num)
        hit = False
        if key in nat:
            coords_native[ch["name"]] = nat[key]
            hit = True
        if key in mni:
            coords_mni[ch["name"]] = mni[key]
            hit = True
        if hit:
            matched.append(ch["name"])
            matched_keys.add(key)
        else:
            unmatched_channels.append(ch["name"])

    unmatched_contacts = []
    seen = set()
    for c in native_contacts:
        if c.get("x_mm") is None:
            continue
        key = (_norm_group(c.get("shaft_name", "")), int(c["contact_number"]))
        if key not in matched_keys and key not in seen:
            seen.add(key)
            unmatched_contacts.append(f"{c.get('shaft_name', '')}{c['contact_number']}")

    return {
        "coords_native": coords_native,
        "coords_mni": coords_mni,
        "matched": matched,
        "unmatched_channels": unmatched_channels,
        "unmatched_contacts": unmatched_contacts,
    }


def load_mni_rows(recon_dir: str):
    """Load ``export/electrodes_mni.json`` if the MNI export has been run, else None."""
    p = os.path.join(recon_dir, "export", "electrodes_mni.json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)
