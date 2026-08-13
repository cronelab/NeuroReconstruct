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
import time
from collections import OrderedDict

import numpy as np

# Stage-timing / diagnostic logging is gated behind an env flag so normal runs stay
# quiet. Set SEEG_DEBUG=1 (or true/yes/on) to print [SEEG] timings to stdout.
_DEBUG = os.environ.get("SEEG_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _dbg(msg: str):
    if _DEBUG:
        print(msg)


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
DEFAULT_WINDOW_MS = (-500.0, 2000.0)  # peri-stimulus window (relative to onset)
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
            # response_onset (NeurosEEGRead, seconds; NaN when no spoken response was
            # detected) drives response-locked alignment. Absent in older h5 files.
            resp = tg["response_onset"][:] if "response_onset" in tg else np.array([])
            for i in range(len(start)):
                r = float(resp[i]) if i < len(resp) else float("nan")
                trials.append({
                    "stimulus": stim[i] if i < len(stim) else "",
                    "start_time": float(start[i]),
                    "stop_time": float(stop[i]) if i < len(stop) else None,
                    "response_onset": (r if np.isfinite(r) else None),
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
    # filtfilt/hilbert operate along axis 0 (time). hilbert FFTs at exactly
    # n_samples; padding to a fast length was measured to perturb the displayed
    # z-score by up to ~0.9 near recording edges, so we keep the exact length.
    t0 = time.perf_counter()
    filtered = filtfilt(b, a, sig, axis=0)
    t1 = time.perf_counter()
    env = np.abs(hilbert(filtered, axis=0))
    t2 = time.perf_counter()
    _dbg(f"[SEEG] band envelope: filtfilt {t1 - t0:.2f}s + hilbert {t2 - t1:.2f}s "
          f"(n={filtered.shape[0]}, {sig.shape[1]} ch)")
    return env.astype(np.float32)


# Band-power envelope cache. The filtfilt+Hilbert envelope is the dominant cost and
# depends only on (h5 file, band) -- not on the peri-stimulus window or trial/scroll
# mode -- so it is cached on disk next to the recording and reused across requests.
ENV_CACHE_DIRNAME = ".envcache"


def _env_cache_path(h5_path: str, band: str) -> str:
    return os.path.join(os.path.dirname(h5_path), ENV_CACHE_DIRNAME,
                        f"{os.path.basename(h5_path)}.{band}.npz")


def _load_or_compute_envelope(h5_path: str, band: str, meta: dict):
    """
    Band envelope, cached on disk keyed by (h5 file, band).

    On a cache MISS the full signal is read here (the filtfilt+Hilbert needs it)
    and returned so the caller can reuse it for the raw ERP -- avoiding a second
    read. On a HIT the signal is NOT read at all.

    Returns (env, full_sig_or_None). full_sig is None on a cache hit.

    The cache stores the source file's size + mtime; a changed/replaced file
    (different size, mtime, or channel count) misses and is recomputed. Cache
    read/write failures degrade silently to a fresh computation.
    """
    cache = _env_cache_path(h5_path, band)
    shape = (meta["n_samples"], len(meta["channels"]))
    try:
        st = os.stat(h5_path)
        if os.path.exists(cache):
            t0 = time.perf_counter()
            with np.load(cache, allow_pickle=False) as d:
                if (int(d["src_size"]) == st.st_size
                        and abs(float(d["src_mtime"]) - st.st_mtime) < 1e-3
                        and tuple(d["shape"]) == shape):
                    env = d["env"]
                    _dbg(f"[SEEG] env cache hit ({band}): loaded in {time.perf_counter() - t0:.2f}s")
                    return env, None
    except Exception as e:
        _dbg(f"[SEEG] env cache read miss ({band}): {e}")

    _dbg(f"[SEEG] env cache miss ({band}): reading full signal + computing envelope")
    sig = _read_full_signal(h5_path, meta)
    env = _band_envelope(sig, meta["rate_hz"], BANDS[band])
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        st = os.stat(h5_path)
        np.savez(cache, env=env, shape=np.array(sig.shape, dtype=np.int64),
                 src_size=np.int64(st.st_size), src_mtime=np.float64(st.st_mtime))
    except Exception as e:
        _dbg(f"[SEEG] env cache write failed ({band}): {e}")
    return env, sig


# ── Mappable-channel signal reads ─────────────────────────────────────────────
# The ieeg data is gzip-compressed and chunked across channels (e.g. 3 ch/chunk),
# so reads are decompression-bound. Three access patterns, cheapest first:
#   * _read_windows  -- only the epoch row-ranges (trial mode, env cached)
#   * _read_strided  -- every step-th sample (continuous mode, env cached)
#   * _read_full     -- the whole block (needed to compute the envelope on a miss)
# In every case we read full-width rows then subset columns in numpy: h5py fancy
# COLUMN indexing (data[:, cols]) is ~10x slower than reading rows + numpy subset.

def _read_full_signal(h5_path: str, meta: dict) -> np.ndarray:
    """Contiguous read of the whole mappable-channel block, (n_samples, n_ch) float32."""
    import h5py
    cols = [c["col"] for c in meta["channels"]]
    with h5py.File(h5_path, "r") as h5:
        data = h5["ieeg"][meta["group_name"]]["data"]
        return data[:][:, cols].astype(np.float32, copy=False)


def _read_windows(h5_path: str, meta: dict, windows) -> list:
    """Lazily read only the given [s, e) sample windows for mappable channels.

    Returns a list of (e-s, n_ch) float32 arrays aligned to ``windows``. Reading
    only the epoch row-ranges avoids decompressing the whole recording when the
    envelope is already cached and only the raw ERP epochs are needed.
    """
    import h5py
    cols = [c["col"] for c in meta["channels"]]
    out = []
    with h5py.File(h5_path, "r") as h5:
        d = h5["ieeg"][meta["group_name"]]["data"]
        for s, e in windows:
            out.append(d[s:e][:, cols].astype(np.float32))
    return out


def _read_strided(h5_path: str, meta: dict, step: int) -> np.ndarray:
    """Lazily read every ``step``-th sample for mappable channels (continuous view).

    Continuous decimation spans the whole recording, so this still touches every
    gzip chunk, but it skips materializing + decimating the full 300+ MB array,
    which is ~30% faster than a full read.
    """
    import h5py
    cols = [c["col"] for c in meta["channels"]]
    with h5py.File(h5_path, "r") as h5:
        d = h5["ieeg"][meta["group_name"]]["data"]
        return d[::step][:, cols].astype(np.float32)


def compute_band_activity(path: str, band: str = DEFAULT_BAND,
                          window_ms: tuple = DEFAULT_WINDOW_MS,
                          baseline_ms: tuple = None,
                          align: str = "stimulus",
                          max_frames: int = MAX_OUTPUT_FRAMES,
                          include_raw: bool = True) -> dict:
    """
    Compute the event-related display activity matrix for one h5 recording.

    Epochs each channel around a per-trial alignment event over ``window_ms``
    [start, end] (start < 0 < end, relative to the event), z-scores each epoch to a
    baseline taken *before stimulus onset*, averages across trials, and returns the
    FULL window (both pre- and post-event frames).

    align:       'stimulus' -> epoch around ``/trials/start_time`` (stimulus onset);
                 'response' -> epoch around ``/trials/response_onset`` (spoken-
                 response onset), skipping trials with no detected response.
    band:        key in BANDS.
    window_ms:   peri-event display window in ms [start, end], start < 0 < end,
                 relative to the alignment event.
    baseline_ms: baseline window in ms [start, end], both <= 0, ALWAYS relative to
                 STIMULUS onset (start_time) -- not the alignment event. Aligning to
                 the response makes the pre-response interval a bad baseline (it holds
                 stimulus-evoked activity at variable RT lags), so the baseline stays
                 anchored to the stimulus. Defaults to [window_ms[0], 0].

    Returns:
      channels: [name, ...]              (mappable channels, order matches columns)
      groups:   [shaft, ...]             per-channel shaft name
      times:    [t_ms, ...]              time in ms relative to the event (may be < 0)
      activity: [[v, ...], ...]          shape (n_frames, n_channels), baseline z
      raw:      [[uV, ...], ...]         trial-averaged, baseline-corrected ERP (uV)
      mode ('trial'), time_unit ('ms'), band, align, n_trials, n_no_response
    """
    if band not in BANDS:
        raise ValueError(f"unknown band {band!r}; options: {sorted(BANDS)}")
    if align not in ("stimulus", "response"):
        raise ValueError(f"unknown align {align!r}; options: ['stimulus', 'response']")
    if baseline_ms is None:
        baseline_ms = (window_ms[0], 0.0)
    meta = parse_seeg_h5(path)
    fs = meta["rate_hz"]
    names = [c["name"] for c in meta["channels"]]
    groups = [c["group"] for c in meta["channels"]]
    if not meta["channels"]:
        return {"channels": [], "groups": [], "times": [], "activity": [], "raw": [],
                "mode": "trial", "time_unit": "ms", "band": band, "align": align,
                "n_trials": 0, "n_no_response": 0}

    if not meta["trials"]:
        raise ValueError("no trials in h5 -- trial-averaged mapping needs /trials onsets")

    t_env = time.perf_counter()
    env, full_sig = _load_or_compute_envelope(path, band, meta)
    _dbg(f"[SEEG] trial: envelope ready in {time.perf_counter() - t_env:.2f}s")

    w0, w1 = window_ms[0] / 1000.0, window_ms[1] / 1000.0     # seconds
    pre = int(round(-w0 * fs)) if w0 < 0 else 0
    post = int(round(w1 * fs))
    n_pst = pre + post
    pst_times_ms = (np.arange(-pre, post) / fs) * 1000.0
    # Baseline offsets (in samples) relative to STIMULUS onset. Both are typically
    # <= 0 (pre-stimulus); they index off the stimulus sample, not the event.
    b0off = int(round(baseline_ms[0] / 1000.0 * fs))
    b1off = int(round(baseline_ms[1] / 1000.0 * fs))

    n_ch = env.shape[1]
    n_samp = env.shape[0]

    # ── Per-trial windows: display epoch around the alignment event + baseline
    # epoch around the stimulus. A trial is dropped when either range falls outside
    # the segment, or (response mode) it has no detected response. ────────────────
    disp_windows, base_windows = [], []
    n_no_response = 0
    for t in meta["trials"]:
        stim = t["start_time"]
        if align == "response":
            anchor = t.get("response_onset")
            if anchor is None:
                n_no_response += 1
                continue
        else:
            anchor = stim
        c = int(round(anchor * fs))
        s, e = c - pre, c + post
        bc = int(round(stim * fs))
        bs, be = bc + b0off, bc + b1off
        if s < 0 or e > n_samp or bs < 0 or be > n_samp or be <= bs:
            continue
        disp_windows.append((s, e))
        base_windows.append((bs, be))

    if not disp_windows:
        if align == "response" and n_no_response == len(meta["trials"]):
            raise ValueError("recording has no response onsets")
        raise ValueError("all trial epochs fell outside the segment -- check onsets/window")

    # Raw voltage for the ERP is only needed for the trace panel, and reading it is
    # the slow part on a cache hit. When include_raw is False the caller wants just
    # the activation map, so we skip the raw read entirely (see compute_activity).
    raw_disp = raw_base = None
    if include_raw:
        t_raw = time.perf_counter()
        if full_sig is not None:
            raw_disp = [full_sig[s:e] for s, e in disp_windows]
            raw_base = [full_sig[s:e] for s, e in base_windows]
            raw_src = "reused full signal"
        else:
            raw_disp = _read_windows(path, meta, disp_windows)
            raw_base = _read_windows(path, meta, base_windows)
            raw_src = "lazy epoch reads"
        _dbg(f"[SEEG] trial: raw epochs ({len(disp_windows)}) via {raw_src} in {time.perf_counter() - t_raw:.2f}s")

    acc = np.zeros((n_pst, n_ch), dtype=np.float64)          # z-score envelope
    acc_raw = np.zeros((n_pst, n_ch), dtype=np.float64) if include_raw else None

    for i, (s, e) in enumerate(disp_windows):
        bs, be = base_windows[i]
        epoch = env[s:e]                                    # (n_pst, n_ch)
        base = env[bs:be]                                   # pre-stimulus baseline
        acc += (epoch - base.mean(axis=0)) / (base.std(axis=0) + 1e-9)
        if include_raw:
            acc_raw += raw_disp[i] - raw_base[i].mean(axis=0)  # baseline-corrected
    used = len(disp_windows)
    # Degenerate channels (flat / all-zero signal) can yield NaN/Inf, which is not
    # JSON-serializable -- map them to 0 (neutral / baseline).
    avg = np.nan_to_num(acc / used, nan=0.0, posinf=0.0, neginf=0.0)
    avg_raw = (np.nan_to_num(acc_raw / used, nan=0.0, posinf=0.0, neginf=0.0) * 1e6
               if include_raw else None)  # uV

    # Decimate the (full-window) time axis if very long.
    n_out = len(pst_times_ms)
    if n_out > max_frames:
        sel = np.arange(0, n_out, int(np.ceil(n_out / max_frames)))
        avg, pst_times_ms = avg[sel], pst_times_ms[sel]
        if include_raw:
            avg_raw = avg_raw[sel]

    return {
        "channels": names,
        "groups": groups,
        "times": [round(float(t), 2) for t in pst_times_ms],
        "activity": np.round(avg, 3).tolist(),
        "raw": np.round(avg_raw, 2).tolist() if include_raw else [],
        "mode": "trial", "time_unit": "ms", "band": band, "align": align,
        "n_trials": used, "n_no_response": n_no_response,
    }


def compute_continuous_traces(path: str, band: str = DEFAULT_BAND,
                              max_frames: int = 2500,
                              include_raw: bool = True) -> dict:
    """
    Continuous (scrollable) traces over the whole recording.

    Returns per-channel band-power z-score (normalized to the whole-recording
    envelope mean/SD) and raw voltage, both decimated to <= max_frames.

    Returns the same shape as compute_band_activity with mode='scroll',
    time_unit='s', and n_trials=0.
    """
    if band not in BANDS:
        raise ValueError(f"unknown band {band!r}; options: {sorted(BANDS)}")
    meta = parse_seeg_h5(path)
    fs = meta["rate_hz"]
    names = [c["name"] for c in meta["channels"]]
    groups = [c["group"] for c in meta["channels"]]
    if not meta["channels"]:
        return {"channels": [], "groups": [], "times": [], "activity": [], "raw": [],
                "mode": "scroll", "time_unit": "s", "band": band, "n_trials": 0}

    t_env = time.perf_counter()
    env, full_sig = _load_or_compute_envelope(path, band, meta)
    _dbg(f"[SEEG] scroll: envelope ready in {time.perf_counter() - t_env:.2f}s")
    # z-score over the whole recording; env stats are cheap on the (cached) envelope.
    z = np.nan_to_num((env - env.mean(axis=0)) / (env.std(axis=0) + 1e-9),
                      nan=0.0, posinf=0.0, neginf=0.0)

    n = env.shape[0]
    step = int(np.ceil(n / max_frames)) if n > max_frames else 1
    idx = np.arange(0, n, step)
    times_s = idx / fs

    # Decimated raw voltage (trace panel only). Skipped when the caller wants just
    # the activation map (include_raw False). Otherwise reuse the full signal if the
    # env miss already read it, else a strided lazy read.
    raw = None
    if include_raw:
        t_raw = time.perf_counter()
        if full_sig is not None:
            raw = full_sig[idx]
            raw_src = "reused full signal"
        else:
            raw = _read_strided(path, meta, step)[:len(idx)]
            raw_src = "strided lazy read"
        _dbg(f"[SEEG] scroll: decimated raw via {raw_src} in {time.perf_counter() - t_raw:.2f}s")

    return {
        "channels": names,
        "groups": groups,
        "times": [round(float(t), 4) for t in times_s],
        "activity": np.round(z[idx], 3).tolist(),
        "raw": np.round(raw * 1e6, 2).tolist() if include_raw else [],   # uV
        "mode": "scroll", "time_unit": "s", "band": band, "n_trials": 0,
    }


# ── In-process result cache + cached dispatch ────────────────────────────────
# Computed activity/trace responses are a pure function of (file identity, mode,
# band, window, include_raw, max_frames). Memoize them in an LRU so flipping back
# to a previously-viewed recording+settings is instant. Entries are small (the
# decimated matrices), so a few dozen cost only tens of MB of RAM -- no disk.

_RESULT_CACHE = OrderedDict()
_RESULT_CACHE_MAX = 32


def _file_identity(path: str):
    try:
        st = os.stat(path)
        return (st.st_size, round(st.st_mtime, 3))
    except OSError:
        return None


def _cache_get(key):
    val = _RESULT_CACHE.get(key)
    if val is not None:
        _RESULT_CACHE.move_to_end(key)
    return val


def _cache_put(key, val):
    _RESULT_CACHE[key] = val
    _RESULT_CACHE.move_to_end(key)
    while len(_RESULT_CACHE) > _RESULT_CACHE_MAX:
        _RESULT_CACHE.popitem(last=False)


def clear_result_cache():
    """Drop all memoized results (used by tests)."""
    _RESULT_CACHE.clear()


def compute_activity(path: str, *, mode: str = "trial", band: str = DEFAULT_BAND,
                     window_ms=None, baseline_ms=None, align: str = "stimulus",
                     include_raw: bool = True, max_frames: int = None) -> dict:
    """
    Cached dispatch for trial/scroll activity.

    include_raw=False returns just the activation map (``raw`` empty), skipping the
    slow raw-voltage read so the map can render fast; a follow-up include_raw=True
    call fills in the trace-panel voltages. Results are memoized per distinct key
    (which for trial mode includes align + baseline so each alignment/baseline
    setting is cached separately).
    """
    if mode not in ("trial", "scroll"):
        raise ValueError(f"unknown mode {mode!r}; options: ['trial', 'scroll']")
    if band not in BANDS:
        raise ValueError(f"unknown band {band!r}; options: {sorted(BANDS)}")

    ident = _file_identity(path)
    if mode == "trial":
        window = tuple(window_ms) if window_ms else DEFAULT_WINDOW_MS
        baseline = tuple(baseline_ms) if baseline_ms else (window[0], 0.0)
        mf = max_frames if max_frames is not None else MAX_OUTPUT_FRAMES
        base = (os.path.abspath(path), ident, "trial", band, window, baseline, align, mf)
    else:
        mf = max_frames if max_frames is not None else 2500
        base = (os.path.abspath(path), ident, "scroll", band, mf)

    hit = _cache_get(base + (include_raw,))
    if hit is not None:
        _dbg(f"[SEEG] result cache hit ({mode}, {band}, raw={include_raw})")
        return hit
    # A cached full result (with raw) also satisfies a raw=False request.
    if not include_raw:
        full = _cache_get(base + (True,))
        if full is not None:
            _dbg(f"[SEEG] result cache hit ({mode}, {band}, reused full for raw=False)")
            return full

    if mode == "trial":
        result = compute_band_activity(path, band, window, baseline_ms=baseline,
                                       align=align, max_frames=mf, include_raw=include_raw)
    else:
        result = compute_continuous_traces(path, band, max_frames=mf, include_raw=include_raw)
    _cache_put(base + (include_raw,), result)
    return result


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
