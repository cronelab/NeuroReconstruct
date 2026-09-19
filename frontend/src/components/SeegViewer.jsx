import React, { useEffect, useRef, useState, useMemo } from 'react';
import { useAppStore } from '../store';
import {
  listSeeg, uploadSeeg, computeSeegActivity, getMesh, getReconstruction, getStructures,
} from '../api';
import * as THREE from 'three';
import SeegViewer3D, { activityColor } from './SeegViewer3D';
import SeegTracePanel from './SeegTracePanel';
import SeegSliceViews from './SeegSliceViews';
import StructurePanel from './StructurePanel';
import { buildShaftColorMap } from '../seegColors';
import { activeMarksAt, buildMarks } from '../seegAnnotations';
import { isInsideMesh } from '../anatomy';

const BANDS = [
  ['review', 'Review 1–70 (clinical)'],
  ['delta', 'Delta 1–4'], ['theta', 'Theta 4–8'], ['alpha', 'Alpha 8–13'],
  ['beta', 'Beta 13–30'], ['gamma', 'Gamma 30–70'], ['high_gamma', 'High-γ 70–150'],
];
const BAND_KEYS = new Set(BANDS.map(([k]) => k));
const CUSTOM_BAND = '__custom__';
// A custom band travels as "<highpass>-<lowpass>" in Hz, which is what the API takes.
const isCustomBand = (b) => !!b && !BAND_KEYS.has(b);
const parseCustomBand = (b) => {
  const m = /^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$/.exec(b || '');
  return m ? [m[1], m[2]] : ['1', '70'];
};

const panel = {
  width: 380, flexShrink: 0, background: '#0d1015', borderRight: '1px solid #1e2530',
  padding: 16, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 16,
  fontFamily: 'IBM Plex Sans, sans-serif', color: '#c8d4e0',
};
const label = { fontSize: 12, letterSpacing: '0.08em', textTransform: 'uppercase', color: '#7a8a99', marginBottom: 6 };
const numInput = {
  width: 62, padding: '4px 6px', background: '#111418', color: '#e8edf2',
  border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, textAlign: 'right',
  fontFamily: 'IBM Plex Mono, monospace',
};
const seg = (active) => ({
  padding: '5px 10px', fontSize: 13, fontFamily: 'IBM Plex Mono, monospace', cursor: 'pointer',
  border: `1px solid ${active ? '#00d4ff55' : '#2a3340'}`, borderRadius: 4,
  background: active ? '#002233' : 'transparent', color: active ? '#00d4ff' : '#7a8a99',
});

function ColorBar({ domain }) {
  const stops = [-domain, -domain / 2, 0, domain / 2, domain];
  return (
    <div>
      <div style={label}>Activation (baseline z)</div>
      <div style={{
        height: 12, borderRadius: 3,
        background: `linear-gradient(to right, ${activityColor(-domain, domain)}, ${activityColor(0, domain)}, ${activityColor(domain, domain)})`,
      }} />
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4, fontSize: 12, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace' }}>
        {stops.map((s) => <span key={s}>{s > 0 ? '+' : ''}{s.toFixed(0)}</span>)}
      </div>
    </div>
  );
}

// Default display window (ms magnitudes before/after the event) per alignment.
// Stimulus-locked activity trails the stimulus, so it uses a longer post window;
// response-locked activity is roughly symmetric around the response.
const DEFAULT_WINDOW = {
  stimulus: { pre: 500, post: 2000 },
  response: { pre: 1000, post: 1000 },
};

// Client-side cache of fully-computed trial-averaged results (activation map + raw
// voltages), so flipping band / alignment / baseline / window back to a setting
// already viewed loads instantly — no server round-trip and no two-phase fetch flash.
// Keyed including recId (a fresh id per upload makes invalidation trivial); LRU-capped
// to bound memory. Complements the backend result + envelope caches.
// Everything phase 2 fills in. Removing exactly these leaves a map-only result, the
// same shape phase 1 returns -- which is what lets a held map stand in for one.
const TRACE_FIELDS = ['trace_times', 'raw', 'raw_min', 'raw_max', 'raw_filtered',
                      'raw_decimation', 'rate_hz', 'trace_rate_hz', 'trace_nyquist_met'];
const stripTrace = (a) => {
  const out = { ...a };
  for (const k of TRACE_FIELDS) delete out[k];
  return out;
};

const RESULT_CACHE = new Map();
const RESULT_CACHE_MAX = 24;
const cacheKey = (recId, p) => [
  recId, p.mode, p.band, p.align, p.filter_raw,
  p.window_ms?.[0], p.window_ms?.[1], p.baseline_ms?.[0], p.baseline_ms?.[1],
].join('|');
function cacheGet(key) {
  const v = RESULT_CACHE.get(key);
  if (v !== undefined) { RESULT_CACHE.delete(key); RESULT_CACHE.set(key, v); }  // LRU bump
  return v;
}
function cachePut(key, val) {
  RESULT_CACHE.set(key, val);
  while (RESULT_CACHE.size > RESULT_CACHE_MAX) RESULT_CACHE.delete(RESULT_CACHE.keys().next().value);
}

export default function SeegViewer({ reconId, onBack }) {
  const {
    reconstruction, setReconstruction,
    seegRecordings, setSeegRecordings, seegRecordingId, setSeegRecordingId,
    seegActivity, setSeegActivity, seegBand, setSeegBand,
    seegPre, setSeegPre, seegPost, setSeegPost,
    seegAlign, setSeegAlign, seegBaseStart, setSeegBaseStart, seegBaseEnd, setSeegBaseEnd,
    seegMode, setSeegMode,
    seegTraceSignal, setSeegTraceSignal, seegTraceScope, setSeegTraceScope,
    seegTraceShaft, setSeegTraceShaft, seegTracePanelW, setSeegTracePanelW,
    seegTraceGain, setSeegTraceGain, seegTraceWindow, setSeegTraceWindow,
    seegBrainOpacity, setSeegBrainOpacity, seegStructureOpacity, setSeegStructureOpacity,
    seegIgnoreOutside, setSeegIgnoreOutside, seegColorLimit, setSeegColorLimit,
    seegTimeIndex, setSeegTimeIndex, seegPlaying, setSeegPlaying,
    seegPlaySpeed, setSeegPlaySpeed, seegLiveValues, seegPlayheadTime, setSeegPlayhead,
    // Structures live in the global store so the shared StructurePanel (master
    // toggle + hierarchical tri-state + opacity) drives both this view and the
    // main reconstruction viewer consistently.
    structuresData, setStructuresData, structureVisible,
    // The 2D slice panes read the surface from the store (they place contacts relative
    // to the mesh centre), so the mesh this view loads goes in there rather than only
    // into local state.
    setMeshData,
  } = useAppStore();

  const [nativeMesh, setNativeMesh] = useState(null);
  // Voltage for the window on screen, at that window's own resolution. Only fetched
  // when the whole-session trace could not reach the signal's rate (see below).
  const [traceDetail, setTraceDetail] = useState(null);
  const [viewWindow, setViewWindow] = useState(null);
  const detailSeqRef = useRef(0);
  const [hoveredChannel, setHoveredChannel] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [computing, setComputing] = useState(false);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);
  const reqSeqRef = useRef(0);   // guards against stale two-phase responses
  const mapKeyRef = useRef(null);  // settings the map in the store was computed for

  // Custom bandpass cutoffs. Held as drafts and committed on blur/Enter so typing
  // "1" on the way to "15" doesn't kick off a recompute per keystroke.
  const [hpDraft, setHpDraft] = useState(() => parseCustomBand(seegBand)[0]);
  const [lpDraft, setLpDraft] = useState(() => parseCustomBand(seegBand)[1]);
  const [bandError, setBandError] = useState(null);

  const commitCustomBand = () => {
    const hp = parseFloat(hpDraft), lp = parseFloat(lpDraft);
    if (!Number.isFinite(hp) || !Number.isFinite(lp) || !(hp > 0 && hp < lp)) {
      setBandError('Need 0 < high-pass < low-pass.');
      return;
    }
    setBandError(null);
    const spec = `${hp}-${lp}`;
    if (spec !== seegBand) setSeegBand(spec);
  };

  // Adopt a recording's natural defaults when the *kind* of recording changes, so a
  // clinical EDF opens at 1-70 Hz in Continuous mode rather than inheriting a task
  // recording's high-gamma / trial settings (a clinical file has no trials at all, so
  // trial mode would simply error). An explicitly-typed custom band is left alone.
  const lastKindRef = useRef(null);
  useEffect(() => {
    const rec = seegRecordings.find((r) => r.id === seegRecordingId);
    if (!rec) return;
    const kind = rec.recording_kind || 'task';
    if (lastKindRef.current === kind) return;
    lastKindRef.current = kind;
    if (rec.default_band && !isCustomBand(seegBand)) setSeegBand(rec.default_band);
    // A clinical recording is one continuous event with no trials to average; a task
    // recording is the opposite, so each opens in the mode that actually fits it.
    setSeegMode(kind === 'clinical' ? 'scroll' : 'trial');
  }, [seegRecordingId, seegRecordings, seegBand, setSeegBand, setSeegMode]);

  // Reviewer annotations, and the one in effect at the cursor. Placed on frames by
  // the same helper the trace panel uses, so the brain readout and the trace ruler can
  // never disagree about which frame a marker belongs to.
  const annMarks = useMemo(
    () => buildMarks(seegActivity?.times, seegActivity?.annotations, seegActivity?.time_unit),
    [seegActivity],
  );
  const activeMarks = useMemo(
    () => activeMarksAt(annMarks, seegTimeIndex),
    [annMarks, seegTimeIndex],
  );

  // Draft window inputs — applied to the store (which triggers recompute) only on
  // commit, so typing doesn't fire a recompute on every keystroke.
  const [preDraft, setPreDraft] = useState(seegPre);
  const [postDraft, setPostDraft] = useState(seegPost);
  useEffect(() => { setPreDraft(seegPre); setPostDraft(seegPost); }, [seegPre, seegPost]);
  const windowDirty = preDraft !== seegPre || postDraft !== seegPost;
  const applyWindow = () => {
    const pre = Math.max(10, Math.min(2000, Math.round(Number(preDraft) || 0)));
    const post = Math.max(50, Math.min(4000, Math.round(Number(postDraft) || 0)));
    setSeegPre(pre); setSeegPost(post);
  };

  // Baseline window drafts (both <= 0, relative to stimulus onset), applied on commit.
  const [baseStartDraft, setBaseStartDraft] = useState(seegBaseStart);
  const [baseEndDraft, setBaseEndDraft] = useState(seegBaseEnd);
  useEffect(() => { setBaseStartDraft(seegBaseStart); setBaseEndDraft(seegBaseEnd); },
    [seegBaseStart, seegBaseEnd]);
  const baselineDirty = Number(baseStartDraft) !== seegBaseStart || Number(baseEndDraft) !== seegBaseEnd;
  const applyBaseline = () => {
    // Clamp to a valid pre-stimulus window: end <= 0 and start strictly < end.
    const end = Math.min(0, Math.round(Number(baseEndDraft) || 0));
    const start = Math.max(-4000, Math.min(end - 10, Math.round(Number(baseStartDraft) || 0)));
    setSeegBaseStart(start); setSeegBaseEnd(end);
  };

  // Switch alignment, applying the target mode's default display window — but only
  // when the current window is still the source mode's default, so a window the user
  // deliberately set is preserved across toggles.
  const handleAlign = (a) => {
    if (a === seegAlign) return;
    const src = DEFAULT_WINDOW[seegAlign];
    if (seegPre === src.pre && seegPost === src.post) {
      const dst = DEFAULT_WINDOW[a];
      setSeegPre(dst.pre); setSeegPost(dst.post);
    }
    setSeegAlign(a);
  };

  // ── Initial load: reconstruction, recordings, surfaces ────────────────────────
  useEffect(() => {
    if (!reconId) return;
    getReconstruction(reconId).then((r) => setReconstruction(r.data)).catch(() => {});
    listSeeg(reconId).then((r) => {
      setSeegRecordings(r.data);
      if (r.data.length && !seegRecordingId) setSeegRecordingId(r.data[0].id);
    }).catch(() => {});
    getMesh(reconId).then((r) => { setNativeMesh(r.data); setMeshData(r.data); })
      .catch(() => setNativeMesh(null));
  }, [reconId]);

  // Structures load on demand via the StructurePanel's "Load" button (matches the
  // main viewer); reuses whatever is already cached in the store.
  const handleLoadStructures = async () => {
    const r = await getStructures(reconId);
    setStructuresData(r.data || {});
  };

  // ── Compute activity whenever recording / band / window changes ───────────────
  // Two-phase fetch: phase 1 asks for the activation map only (include_raw=false),
  // which returns fast so the brain re-colors and the spinner clears; phase 2 then
  // fetches the raw voltages to fill the trace panel. reqSeqRef discards responses
  // from a request that a newer switch has superseded.
  // Only the 'raw' signal wants the unfiltered trace; 'z' and 'filtered' both use the
  // band, so the fetch keys on this rather than on the signal and switching between
  // those two needs no request at all.
  const filterRaw = seegTraceSignal !== 'raw';
  // The map does not depend on which trace the panel draws -- it is always built from
  // the band envelope -- so everything here except filter_raw decides it. Toggling
  // raw <-> filtered would otherwise re-fetch a byte-identical 9 MB matrix.
  const mapKey = JSON.stringify([reconId, seegRecordingId, seegBand, seegMode, seegAlign,
                                 seegPre, seegPost, seegBaseStart, seegBaseEnd]);
  useEffect(() => {
    if (!reconId || !seegRecordingId) return undefined;
    const seq = ++reqSeqRef.current;
    const params = {
      band: seegBand, mode: seegMode, align: seegAlign,
      filter_raw: filterRaw,
      window_ms: [-seegPre, seegPost],
      baseline_ms: [seegBaseStart, seegBaseEnd],
    };
    setError(null);
    setSeegPlaying(false);
    setTraceDetail(null);

    // Cache hit: revisiting a computed setting loads instantly (full result, raw
    // included) with no fetch. Only trial-mode results are cached (see RESULT_CACHE).
    const key = seegMode === 'trial' ? cacheKey(seegRecordingId, params) : null;
    if (key) {
      const cached = cacheGet(key);
      if (cached) {
        setSeegActivity(cached);
        mapKeyRef.current = mapKey;
        setComputing(false);
        return undefined;
      }
    }

    // Only the trace changed: keep the map already in the store and skip phase 1.
    // Its trace fields go, though -- they belong to the signal being switched away
    // from, and drawing them under the new label until phase 2 lands would be a lie.
    const held = mapKeyRef.current === mapKey ? useAppStore.getState().seegActivity : null;
    setComputing(!held);
    const phase1 = held
      ? Promise.resolve({ data: stripTrace(held) })
      : computeSeegActivity(reconId, seegRecordingId, { ...params, include_raw: false });
    phase1
      .then((r) => {
        if (seq !== reqSeqRef.current) return;                 // superseded
        setSeegActivity(r.data);
        mapKeyRef.current = mapKey;
        setComputing(false);                                   // map is ready
        // Phase 2: raw voltages for the trace panel (map stays put on failure). The map
        // is already here and is the bulk of the payload, so this asks for traces only.
        computeSeegActivity(reconId, seegRecordingId,
          { ...params, include_raw: true, include_activity: false })
          .then((r2) => {
            if (seq !== reqSeqRef.current) return;
            // setSeegActivity has no functional-updater form; read latest from the store.
            const cur = useAppStore.getState().seegActivity;
            if (!cur) return;
            // Phase 2 carries the voltage trace on its own display axis: the series plus
            // its per-bin extremes (continuous mode) — all of which the panel needs.
            const full = {
              ...cur,
              trace_times: r2.data.trace_times,
              raw: r2.data.raw,
              raw_min: r2.data.raw_min,
              raw_max: r2.data.raw_max,
              raw_filtered: r2.data.raw_filtered,
              raw_decimation: r2.data.raw_decimation,
              rate_hz: r2.data.rate_hz,
              trace_rate_hz: r2.data.trace_rate_hz,
              trace_nyquist_met: r2.data.trace_nyquist_met,
            };
            setSeegActivity(full);
            if (key) cachePut(key, full);   // cache the full result for instant re-switch
          })
          .catch(() => {});
      })
      .catch((e) => {
        if (seq !== reqSeqRef.current) return;
        setError(e?.response?.data?.detail || 'Could not compute activity');
        setSeegActivity(null);
        mapKeyRef.current = null;
        setComputing(false);
      });
    return undefined;
  }, [reconId, seegRecordingId, seegBand, filterRaw, seegMode, seegAlign, seegPre, seegPost,
      seegBaseStart, seegBaseEnd, mapKey]);

  // ── Trace detail for the visible window ───────────────────────────────────────
  // Only worth asking for when the whole-session trace came back below the signal's own
  // rate. Raw always does -- it would need 47 million samples for a five-minute
  // recording -- so the session view sends the true high/low of each bin instead, and
  // spending the same rule on a few seconds gets real samples back. A filtered trace
  // usually arrives at its Nyquist rate already and never asks, but not always: high
  // gamma needs 333 Hz, and on a long recording with a wide montage (an eight-minute,
  // 115-channel session is 19M values) that is past the ceiling too. So this keys on
  // what the response reported, not on which signal was requested.
  const onViewWindow = useMemo(() => (t0, t1) => {
    setViewWindow((p) => (p && p[0] === t0 && p[1] === t1 ? p : [t0, t1]));
  }, []);

  const needsDetail = seegActivity?.trace_nyquist_met === false;
  useEffect(() => {
    if (!needsDetail || !viewWindow || seegMode !== 'scroll' || !reconId || !seegRecordingId) {
      setTraceDetail(null);
      return undefined;
    }
    const [t0, t1] = viewWindow;
    // Beyond about half a minute the session view is already as fine as the screen,
    // so a request would cost bytes and buy nothing.
    if (!(t1 > t0) || t1 - t0 > 30) { setTraceDetail(null); return undefined; }
    const seq = ++detailSeqRef.current;
    // Debounced: a drag would otherwise fire a request a frame.
    const timer = setTimeout(() => {
      computeSeegActivity(reconId, seegRecordingId, {
        band: seegBand, mode: 'scroll', align: seegAlign, filter_raw: filterRaw,
        window_ms: [-seegPre, seegPost], baseline_ms: [seegBaseStart, seegBaseEnd],
        include_raw: true, include_activity: false, trace_window_s: [t0, t1],
      }).then((r) => {
        if (seq !== detailSeqRef.current) return;              // superseded by a newer window
        setTraceDetail({
          t0, t1, times: r.data.trace_times, raw: r.data.raw,
          raw_min: r.data.raw_min, raw_max: r.data.raw_max,
          raw_decimation: r.data.raw_decimation, rate: r.data.trace_rate_hz,
        });
      }).catch(() => {});
    }, 250);
    return () => clearTimeout(timer);
  }, [needsDetail, viewWindow, seegMode, reconId, seegRecordingId, seegBand, seegAlign,
      filterRaw, seegPre, seegPost, seegBaseStart, seegBaseEnd]);

  // ── Playback ──────────────────────────────────────────────────────────────────
  // Playback runs on the recording's clock, not the frame count: a playhead advances by
  // wall time x speed, once per displayed frame, and the brain shows the value AT the
  // playhead -- not merely the nearest map frame. The map is sampled at its band
  // envelope's Nyquist rate, so what lies between two frames is recoverable:
  //   - slower than the map rate (the playhead moves less than a frame per display
  //     frame): interpolate between the frames either side, so a slow band or slow
  //     speed glides instead of stepping;
  //   - faster (several map frames pass per display frame): average the frames passed.
  //     The display refresh is itself a sampler, and showing one frame out of each run
  //     would alias -- activity flickering in and out depending on which frame is hit.
  const nFrames = seegActivity?.times?.length || 0;
  const playMode = seegActivity?.mode === 'trial' ? 'trial' : 'scroll';
  const playSpeed = seegPlaySpeed[playMode];
  const mapTimes = seegActivity?.times;
  const mapValues = seegActivity?.activity_values;
  const mapUnit = seegActivity?.time_unit;
  useEffect(() => {
    const times = mapTimes, values = mapValues;
    if (!seegPlaying || !times || times.length < 2 || !values) return undefined;
    const perSecond = playSpeed * (mapUnit === 'ms' ? 1000 : 1);
    const last = times.length - 1;
    const nCh = values.length / times.length;
    // Last frame at or before t (times are ascending).
    const frameAt = (t) => {
      let lo = 0, hi = last;
      while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (times[mid] <= t) lo = mid; else hi = mid - 1; }
      return lo;
    };
    // Seeking pauses playback, so the playhead only needs seeding from the cursor here.
    let playhead = times[Math.min(useAppStore.getState().seegTimeIndex, last)];
    let prevFrame = frameAt(playhead);
    let prevWall = performance.now();

    const tick = () => {
      const now = performance.now();
      playhead += ((now - prevWall) / 1000) * perSecond;
      prevWall = now;
      let wrapped = false;
      if (playhead > times[last]) { playhead = times[0]; wrapped = true; }   // loop
      const k = frameAt(playhead);
      const out = new Float32Array(nCh);
      if (wrapped || k - prevFrame <= 1) {
        const k1 = Math.min(k + 1, last);
        const span = times[k1] - times[k];
        const f = span > 0 ? Math.min(1, (playhead - times[k]) / span) : 0;
        const a = k * nCh, b = k1 * nCh;
        for (let c = 0; c < nCh; c++) out[c] = values[a + c] + (values[b + c] - values[a + c]) * f;
      } else {
        for (let j = prevFrame + 1; j <= k; j++) {
          const a = j * nCh;
          for (let c = 0; c < nCh; c++) out[c] += values[a + c];
        }
        const n = k - prevFrame;
        for (let c = 0; c < nCh; c++) out[c] /= n;
      }
      prevFrame = k;
      setSeegPlayhead(k, out, playhead);
    };

    // One step per displayed frame, with a timer standing in whenever requestAnimationFrame
    // is throttled -- which it routinely is: in an unfocused window, and in the desktop
    // app's own browser pane, rAF can drop to a few frames a second or stop altogether.
    // The timer runs at display rate rather than as a coarse safety net, because a
    // ~10 Hz playhead is visibly steppy once the traces are zoomed in. Either clock
    // yields to the other, so this is still at most one step per displayed frame, and if
    // the main thread cannot keep up the timer is delayed with it.
    let raf = 0, lastTick = performance.now();
    const onFrame = () => { tick(); lastTick = performance.now(); raf = requestAnimationFrame(onFrame); };
    raf = requestAnimationFrame(onFrame);
    const watchdog = setInterval(() => {
      if (performance.now() - lastTick > 24) { tick(); lastTick = performance.now(); }
    }, 16);
    return () => { cancelAnimationFrame(raf); clearInterval(watchdog); };
  }, [seegPlaying, mapTimes, mapValues, mapUnit, playSpeed]);

  // Shaft colors come from the reconstruction's own shafts, so a shaft is the same
  // color here as in the reconstruction / electrode-editor viewers.
  const shaftColors = useMemo(
    () => buildShaftColorMap(reconstruction?.electrode_shafts), [reconstruction]);

  const surfaceMesh = nativeMesh;

  // Raycastable native-brain mesh for the inside/outside contact test. DoubleSide
  // so isInsideMesh's ray-crossing count is correct (matches anatomy.js).
  const brainRaycastMesh = useMemo(() => {
    if (!nativeMesh?.vertices) return null;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(nativeMesh.vertices), 3));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(nativeMesh.faces), 1));
    g.computeVertexNormals(); g.computeBoundingBox(); g.computeBoundingSphere();
    const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
    m.updateMatrixWorld(true);
    return m;
  }, [nativeMesh]);

  // Which mapped contacts lie inside the brain surface. Only computed when the
  // "ignore outside" option is on (the raycast is otherwise unnecessary).
  const insideByName = useMemo(() => {
    const map = {};
    if (!seegIgnoreOutside || !brainRaycastMesh || !seegActivity?.coords_native) return map;
    for (const [name, c] of Object.entries(seegActivity.coords_native)) {
      map[name] = isInsideMesh(new THREE.Vector3(c[0], c[1], c[2]), brainRaycastMesh);
    }
    return map;
  }, [seegIgnoreOutside, brainRaycastMesh, seegActivity?.coords_native]);

  // ── Domain (color-scale half-range): 99th percentile of |activity| ────────────
  // A robust upper bound: using the raw max lets a single extreme contact compress
  // the color range for everyone else, so use the 99th percentile of |z| instead.
  // Contacts outside the brain are excluded when "ignore outside" is on. No floor
  // is applied, so quiet recordings get a tight scale; a manual limit
  // (seegColorLimit) overrides this auto value when set.
  // A Nyquist-spaced map can hold millions of values, so this reads the flat typed array
  // and, past ~1M, a regular subsample of frames: the 99th percentile of that many draws
  // is stable to well under the 0.1 z the result is rounded to.
  const autoValues = seegActivity?.activity_values;
  const autoChannels = seegActivity?.channels;
  const autoDomain = useMemo(() => {
    if (!autoValues?.length || !autoChannels?.length) return 6;
    const nCh = autoChannels.length;
    const nFrames = autoValues.length / nCh;
    const keep = autoChannels.map((c) => !(seegIgnoreOutside && insideByName[c] === false));
    const nKeep = keep.filter(Boolean).length;
    if (!nKeep) return 6;
    const frameStep = Math.max(1, Math.ceil((nFrames * nKeep) / 1e6));
    const vals = new Float32Array(Math.ceil(nFrames / frameStep) * nKeep);
    let n = 0;
    for (let k = 0; k < nFrames; k += frameStep) {
      const a = k * nCh;
      for (let i = 0; i < nCh; i++) if (keep[i]) vals[n++] = Math.abs(autoValues[a + i]);
    }
    const sorted = vals.subarray(0, n).sort();          // typed-array sort is numeric
    const p99 = sorted[Math.floor(0.99 * (n - 1))];
    // No floor or ceiling — the scale tracks the data; use the manual limit to
    // tame outliers. The guard only handles the all-zero case.
    if (p99 <= 0) return 6;
    return Math.round(p99 * 10) / 10;
  }, [autoValues, autoChannels, seegIgnoreOutside, insideByName]);

  const domain = (seegColorLimit != null && seegColorLimit > 0) ? seegColorLimit : autoDomain;

  // ── Resolve contacts (native brain space) at the current time index ───────────
  const contacts = useMemo(() => {
    if (!seegActivity || !surfaceMesh) return [];
    // While playing, the value at the playhead itself (between frames); otherwise the frame.
    const frame = (seegLiveValues && seegLiveValues.length === seegActivity.channels.length)
      ? seegLiveValues : (seegActivity.activity[seegTimeIndex] || []);
    const coordsMap = seegActivity.coords_native;
    const out = [];
    seegActivity.channels.forEach((name, i) => {
      const c = coordsMap[name];
      if (!c) return;
      // inside=true unless the "ignore outside" test classified it as outside.
      const inside = !(seegIgnoreOutside && insideByName[name] === false);
      out.push({ name, group: seegActivity.groups?.[i] || '', value: frame[i] ?? 0,
        pos: [c[0], c[1], c[2]], inside });
    });
    return out;
  }, [seegActivity, surfaceMesh, seegTimeIndex, seegLiveValues, seegIgnoreOutside, insideByName]);

  const handleUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const r = await uploadSeeg(reconId, file);
      const list = await listSeeg(reconId);
      setSeegRecordings(list.data);
      setSeegRecordingId(r.data.id);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Upload failed');
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const matchedN = seegActivity?.matched?.length || 0;
  const unmatchedN = seegActivity?.unmatched_channels?.length || 0;

  return (
    <div style={{ flex: 1, display: 'flex', minWidth: 0, height: '100%' }}>
      {/* ── Control panel ── */}
      <div style={panel}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 600, color: '#e8edf2' }}>sEEG Functional Mapping</div>
          <div style={{ fontSize: 13, color: '#7a8a99', marginTop: 2 }}>
            {reconstruction ? `Patient ${reconstruction.patient_id}` : '—'}
          </div>
        </div>

        {/* Upload */}
        <div>
          <div style={label}>Recording (NeurosEEGRead .h5)</div>
          <input ref={fileRef} type="file" accept=".h5,.hdf5" onChange={handleUpload} style={{ display: 'none' }} />
          <button onClick={() => fileRef.current?.click()} disabled={uploading}
            style={{ ...seg(false), width: '100%', textAlign: 'left', opacity: uploading ? 0.6 : 1 }}>
            {uploading ? '⟳ Uploading…' : '⤒ Upload h5'}
          </button>
          {seegRecordings.length > 0 && (
            <select value={seegRecordingId || ''} onChange={(e) => setSeegRecordingId(Number(e.target.value))}
              style={{ width: '100%', marginTop: 8, padding: '5px 8px', background: '#111418', color: '#c8d4e0',
                border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, fontFamily: 'IBM Plex Mono, monospace' }}>
              {seegRecordings.map((r) => (
                <option key={r.id} value={r.id}>{r.task || r.filename}</option>
              ))}
            </select>
          )}
        </div>

        {/* Band */}
        <div>
          <div style={label}>Frequency band</div>
          <select value={isCustomBand(seegBand) ? CUSTOM_BAND : seegBand}
            onChange={(e) => {
              const v = e.target.value;
              setSeegBand(v === CUSTOM_BAND ? `${hpDraft}-${lpDraft}` : v);
            }}
            style={{ width: '100%', padding: '6px 8px', background: '#111418', color: '#c8d4e0',
              border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, fontFamily: 'IBM Plex Mono, monospace' }}>
            {BANDS.map(([key, txt]) => <option key={key} value={key}>{txt}</option>)}
            <option value={CUSTOM_BAND}>Custom bandpass…</option>
          </select>

          {isCustomBand(seegBand) && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8 }}>
              <input type="number" min={0.1} step={0.5} value={hpDraft}
                onChange={(e) => setHpDraft(e.target.value)} onBlur={commitCustomBand}
                onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
                title="High-pass cutoff (Hz)"
                style={numInput} />
              <span style={{ fontSize: 13, color: '#7a8a99' }}>–</span>
              <input type="number" min={0.2} step={5} value={lpDraft}
                onChange={(e) => setLpDraft(e.target.value)} onBlur={commitCustomBand}
                onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
                title="Low-pass cutoff (Hz)"
                style={numInput} />
              <span style={{ fontSize: 13, color: '#7a8a99' }}>Hz</span>
            </div>
          )}
          {bandError && (
            <div style={{ fontSize: 12, color: '#ff8a80', marginTop: 6 }}>{bandError}</div>
          )}
          {/* How many frames a second the brain is animated at. It is set from the band
              (see the Nyquist reasoning in seeg_service.envelope_nyquist_step), but the
              reader only needs the rate itself -- and to be told when a long, many-channel
              recording had to be smoothed to fit inside the payload budget. */}
          {seegActivity?.map_rate_hz != null && (
            <div style={{ fontSize: 12, marginTop: 6, fontFamily: 'IBM Plex Mono, monospace',
              color: seegActivity.map_nyquist_met ? '#7a8a99' : '#ffb74d' }}
              title={seegActivity.map_nyquist_met
                ? 'Frames per second of recording in the brain view. Fast enough to follow everything this band can change at.'
                : `Too many frames to send at the ${seegActivity.map_nyquist_hz.toFixed(0)} Hz this band can change at, so each frame averages its own span: fast changes are smoothed rather than lost to aliasing.`}>
              frame rate {seegActivity.map_rate_hz.toFixed(1)} Hz
              {seegActivity.map_nyquist_met ? '' : ' · smoothed to fit'}
            </div>
          )}
          {/* What the voltage trace itself is: real samples at or above the rate the
              signal needs, or -- when that would not fit -- the true high/low of each
              bin, which draws as a band rather than a waveform. */}
          {seegActivity?.trace_rate_hz != null && seegTraceSignal !== 'z' && (
            <div style={{ fontSize: 12, marginTop: 3, fontFamily: 'IBM Plex Mono, monospace',
              color: seegActivity.trace_nyquist_met ? '#7a8a99' : '#ffb74d' }}
              title={seegActivity.trace_nyquist_met
                ? 'Every point drawn is a real sample, at or above the rate this signal needs. Zoom in as far as you like.'
                : 'Too many samples to send at full rate, so each bin carries its true highest and lowest value. Spikes keep their height, but the trace reads as a band rather than a waveform.'}>
              trace {(traceDetail?.rate ?? seegActivity.trace_rate_hz).toFixed(1)} Hz
              {(traceDetail ? traceDetail.raw_decimation === 'none'
                            : seegActivity.trace_nyquist_met)
                ? ' · true samples' : ' · high/low per bin'}
              {traceDetail ? ' · this window' : ''}
            </div>
          )}

        </div>

        {/* Mapping mode */}
        <div>
          <div style={label}>Mapping mode</div>
          <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
            <div onClick={() => setSeegMode('trial')} style={{ ...seg(seegMode === 'trial'), flex: 1, textAlign: 'center' }}>Trial-averaged</div>
            <div onClick={() => setSeegMode('scroll')} style={{ ...seg(seegMode === 'scroll'), flex: 1, textAlign: 'center' }}>Continuous</div>
          </div>

          {seegMode === 'scroll' ? (
            <div style={{ fontSize: 12, color: '#7a8a99' }}>
              Continuous recording · band-power z-score over the whole session
              {seegActivity?.band_hz
                ? `, ${seegActivity.band_hz[0]}–${seegActivity.band_hz[1]} Hz.`
                : '.'}
            </div>
          ) : (<>
          {/* Alignment event */}
          <div style={label}>Align trials to</div>
          <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
            <div onClick={() => handleAlign('stimulus')}
              style={{ ...seg(seegAlign === 'stimulus'), flex: 1, textAlign: 'center' }}>Stimulus onset</div>
            <div onClick={() => handleAlign('response')}
              style={{ ...seg(seegAlign === 'response'), flex: 1, textAlign: 'center' }}>Response onset</div>
          </div>

          {/* Display window (± the alignment event) */}
          <div style={label}>Display window (± event, ms)</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ fontSize: 12, color: '#7a8a99' }}>−</span>
              <input type="number" min={10} max={2000} step={50} value={preDraft}
                onChange={(e) => setPreDraft(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && applyWindow()}
                style={{ width: 62, padding: '4px 6px', background: '#111418', color: '#c8d4e0',
                  border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, fontFamily: 'IBM Plex Mono, monospace' }} />
            </div>
            <span style={{ fontSize: 12, color: '#4a5568' }}>to</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ fontSize: 12, color: '#7a8a99' }}>+</span>
              <input type="number" min={50} max={4000} step={50} value={postDraft}
                onChange={(e) => setPostDraft(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && applyWindow()}
                style={{ width: 62, padding: '4px 6px', background: '#111418', color: '#c8d4e0',
                  border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, fontFamily: 'IBM Plex Mono, monospace' }} />
            </div>
            <button onClick={applyWindow} disabled={!windowDirty}
              style={{ ...seg(windowDirty), padding: '4px 10px', opacity: windowDirty ? 1 : 0.4,
                cursor: windowDirty ? 'pointer' : 'default' }}>Apply</button>
          </div>
          <div style={{ fontSize: 12, color: '#7a8a99', marginTop: 5 }}>
            Shown around {seegAlign === 'response' ? 'the response' : 'stimulus onset'} (t=0). Full window is displayed.
          </div>

          {/* Baseline window (always relative to stimulus onset) */}
          <div style={{ ...label, marginTop: 12 }}>Baseline for z-score (pre-stimulus, ms)</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="number" max={0} step={50} value={baseStartDraft}
              onChange={(e) => setBaseStartDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && applyBaseline()}
              style={{ width: 62, padding: '4px 6px', background: '#111418', color: '#c8d4e0',
                border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, fontFamily: 'IBM Plex Mono, monospace' }} />
            <span style={{ fontSize: 12, color: '#4a5568' }}>to</span>
            <input type="number" max={0} step={50} value={baseEndDraft}
              onChange={(e) => setBaseEndDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && applyBaseline()}
              style={{ width: 62, padding: '4px 6px', background: '#111418', color: '#c8d4e0',
                border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, fontFamily: 'IBM Plex Mono, monospace' }} />
            <button onClick={applyBaseline} disabled={!baselineDirty}
              style={{ ...seg(baselineDirty), padding: '4px 10px', opacity: baselineDirty ? 1 : 0.4,
                cursor: baselineDirty ? 'pointer' : 'default' }}>Apply</button>
          </div>
          <div style={{ fontSize: 12, color: '#7a8a99', marginTop: 5 }}>
            Always measured before <span style={{ color: '#c8d4e0' }}>stimulus</span> onset (both ≤ 0), even when aligned to the response.
          </div>
          </>)}
        </div>

        <div>
          <ColorBar domain={domain} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8 }}>
            <span style={{ fontSize: 12, color: '#7a8a99' }}>Limit ±z</span>
            <input type="number" min={0.1} step={0.5} placeholder={String(autoDomain)}
              value={seegColorLimit ?? ''}
              onChange={(e) => {
                const v = e.target.value;
                setSeegColorLimit(v === '' ? null : Math.max(0.1, Number(v)));
              }}
              style={{ width: 62, padding: '4px 6px', background: '#111418', color: '#c8d4e0',
                border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, fontFamily: 'IBM Plex Mono, monospace' }} />
            <button onClick={() => setSeegColorLimit(null)} disabled={seegColorLimit == null}
              style={{ ...seg(false), padding: '4px 10px', opacity: seegColorLimit == null ? 0.4 : 1,
                cursor: seegColorLimit == null ? 'default' : 'pointer' }}>Auto</button>
          </div>
          <div style={{ fontSize: 12, color: '#7a8a99', marginTop: 4 }}>
            {seegColorLimit != null ? 'Manual color limit' : `Auto · 99th percentile of |z| (±${autoDomain})`}
          </div>
        </div>

        {/* Brain surface opacity */}
        <div>
          <div style={label}>Brain surface opacity</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <input type="range" min={0} max={1} step={0.05} value={seegBrainOpacity}
              onChange={(e) => setSeegBrainOpacity(parseFloat(e.target.value))}
              style={{ flex: 1, accentColor: '#00d4ff' }} />
            <span style={{ fontSize: 13, fontFamily: 'IBM Plex Mono, monospace', color: '#7a8a99', width: 34, textAlign: 'right' }}>
              {Math.round(seegBrainOpacity * 100)}%
            </span>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, cursor: 'pointer' }}>
            <input type="checkbox" checked={seegIgnoreOutside}
              onChange={(e) => setSeegIgnoreOutside(e.target.checked)}
              style={{ accentColor: '#00d4ff', width: 14, height: 14 }} />
            <span style={{ fontSize: 13, color: '#c8d4e0' }}>Ignore contacts outside brain</span>
          </label>
          {seegIgnoreOutside && (
            <div style={{ fontSize: 12, color: '#7a8a99', marginTop: 4 }}>
              Outside contacts stay in place but aren’t colored/scaled by z or used for the color limit.
            </div>
          )}
        </div>

        {/* Brain structures — master toggle, opacity, hierarchical tri-state tree
            (shared with the reconstruction viewer). Hover a contact to name its
            structure. */}
        <div style={{ margin: '0 -16px', borderTop: '1px solid #1e2530' }}>
          <StructurePanel
            onLoadStructures={handleLoadStructures}
            structureOpacity={seegStructureOpacity}
            setStructureOpacity={setSeegStructureOpacity}
            maxHeight={320}
          />
        </div>

        {/* Coverage */}
        {seegActivity && (
          <div>
            <div style={label}>Channel coverage</div>
            <div style={{ fontSize: 13, fontFamily: 'IBM Plex Mono, monospace' }}>
              <span style={{ color: '#00e676' }}>{matchedN} mapped</span>
              {unmatchedN > 0 && <span style={{ color: '#ffab40', marginLeft: 10 }}>{unmatchedN} unmatched</span>}
            </div>
            {unmatchedN > 0 && (
              <div style={{ fontSize: 12, color: '#7a8a99', marginTop: 6, wordBreak: 'break-word' }}>
                No contact for: {seegActivity.unmatched_channels.slice(0, 24).join(', ')}
                {unmatchedN > 24 ? ` +${unmatchedN - 24} more` : ''}
              </div>
            )}
            <div style={{ fontSize: 12, color: '#7a8a99', marginTop: 4 }}>
              {seegActivity.mode === 'scroll'
                ? 'continuous recording'
                : `${seegActivity.n_trials} trials averaged · aligned to ${seegActivity.align === 'response' ? 'response' : 'stimulus'} onset`}
            </div>
            {seegActivity.mode !== 'scroll' && seegActivity.align === 'response' && seegActivity.n_no_response > 0 && (
              <div style={{ fontSize: 12, color: '#ffab40', marginTop: 2 }}>
                {seegActivity.n_no_response} trial{seegActivity.n_no_response === 1 ? '' : 's'} dropped (no response detected)
              </div>
            )}
          </div>
        )}

        {error && <div style={{ fontSize: 13, color: '#ff8a80' }}>{error}</div>}

        <div style={{ flex: 1 }} />
        {onBack && (
          <button onClick={onBack} style={{ ...seg(false), textAlign: 'center' }}>← Back to reconstruction</button>
        )}
      </div>

      {/* ── Brain + MRI slice planes (center) ── */}
      <SeegSliceViews reconId={reconId}
        activityContacts={contacts} activityDomain={domain} shaftColors={shaftColors}
        hoveredChannel={hoveredChannel} onHoverContact={setHoveredChannel}
        viewer3D={(
      <div style={{ position: 'absolute', inset: 0 }}>
        <SeegViewer3D
          meshData={surfaceMesh}
          contacts={contacts}
          domain={domain}
          brainOpacity={seegBrainOpacity}
          structuresData={structuresData}
          structureVisible={structureVisible}
          structureOpacity={seegStructureOpacity}
          shaftColors={shaftColors}
          hoveredChannel={hoveredChannel}
          onHoverContact={setHoveredChannel}
          loading={computing || !surfaceMesh}
          loadingMessage={computing ? 'Computing band activity…' : 'Loading surface…'}
        />
        {seegActivity && (
          <div style={{ position: 'absolute', top: 12, left: 12, display: 'flex',
            flexDirection: 'column', gap: 6, alignItems: 'flex-start',
            maxWidth: 'calc(100% - 24px)', pointerEvents: 'none' }}>
            <div style={{ fontSize: 12, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace',
              background: '#0d1015aa', padding: '4px 8px', borderRadius: 3 }}>
              Native brain · {seegActivity.band_hz
                ? `${seegActivity.band_hz[0]}–${seegActivity.band_hz[1]} Hz`
                : seegActivity.band}
            </div>
            {/* The reviewer's annotation currently in effect. Held until the next one
                takes over, so scrubbing through a seizure reads as a running commentary
                rather than a label that blinks past on one frame. */}
            {activeMarks.map((m, i) => (
              <div key={`am${i}`} style={{ display: 'flex', alignItems: 'baseline', gap: 8,
                background: '#0d1015e6', borderLeft: `3px solid ${m.color}`,
                padding: '5px 10px', borderRadius: 3 }}>
                <span style={{ fontSize: 12, color: '#7a8a99',
                  fontFamily: 'IBM Plex Mono, monospace' }}>{m.onset.toFixed(0)}s</span>
                <span style={{ fontSize: 14, color: m.color, fontWeight: 500,
                  fontFamily: 'IBM Plex Mono, monospace' }}>{m.text}</span>
                {m.channels?.length > 0 && (
                  <span style={{ fontSize: 12, color: '#8a97a6',
                    fontFamily: 'IBM Plex Mono, monospace' }}>{m.channels.join(' ')}</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
      )} />

      {/* ── Trace panel (right) — stacked traces with synced time cursor ── */}
      {seegActivity && nFrames > 0 && (
        <SeegTracePanel
          data={seegActivity}
          signal={seegTraceSignal} setSignal={setSeegTraceSignal}
          scope={seegTraceScope} setScope={setSeegTraceScope}
          shaft={seegTraceShaft} setShaft={setSeegTraceShaft}
          timeIndex={seegTimeIndex} setTimeIndex={setSeegTimeIndex}
          hoveredChannel={hoveredChannel} setHoveredChannel={setHoveredChannel}
          width={seegTracePanelW} setWidth={setSeegTracePanelW}
          traceGain={seegTraceGain} setTraceGain={setSeegTraceGain}
          playheadTime={seegPlaying ? seegPlayheadTime : null}
          traceDetail={traceDetail} onViewWindow={onViewWindow}
          timeWindow={seegTraceWindow[playMode]}
          setTimeWindow={(v) => setSeegTraceWindow(playMode, v)}
          playing={seegPlaying} setPlaying={setSeegPlaying}
          speed={playSpeed} setSpeed={(v) => setSeegPlaySpeed(playMode, v)}
          shaftColors={shaftColors}
        />
      )}
    </div>
  );
}
