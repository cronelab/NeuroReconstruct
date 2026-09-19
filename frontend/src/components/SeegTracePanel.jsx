import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { shaftColorOf as shaftColor } from '../seegColors';
import { ANN_EMPHASIS, buildMarks, nearestIndex as nearestFrame } from '../seegAnnotations';

const seg = (active) => ({
  padding: '3px 9px', fontSize: 12, fontFamily: 'IBM Plex Mono, monospace', cursor: 'pointer',
  border: `1px solid ${active ? '#00d4ff55' : '#2a3340'}`, borderRadius: 4,
  background: active ? '#002233' : 'transparent', color: active ? '#00d4ff' : '#7a8a99',
});

// Playback speeds offered per mapping mode, as multiples of real time. A trial window
// spans a couple of seconds, so it plays in slow motion; a continuous recording spans
// minutes, so it plays from real time up.
const SPEEDS = {
  trial: [0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1],
  scroll: [0.25, 0.5, 1, 2, 5, 10, 20, 50],
};

// Units the time base can be typed in, in seconds. A trial window is a few hundred
// milliseconds wide and a continuous recording runs to minutes, so the same field has
// to span both; the unit is the user's choice rather than the mode's.
const UNIT_SEC = { ms: 0.001, s: 1, min: 60 };

// Stable stand-in until the traces arrive, so a fresh [] per render does not re-run the
// canvas draw (which would otherwise happen on every playback frame).
const NO_TIMES = [];

const ROW_H = 22;
const GUTTER = 80;
const RULER_H = 15;
const AXIS_H = 22;

// Tick spacing: the 1/2/5-per-decade step that puts about `target` ticks in a window
// this wide, so the labels stay round numbers at every zoom.
function niceStep(span, target) {
  const raw = span / Math.max(1, target);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const n = raw / mag;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
}
// Enough decimals to tell one tick from the next, and no more.
const tickLabel = (t, step) =>
  t.toFixed(step >= 1 ? 0 : step >= 0.1 ? 1 : step >= 0.01 ? 2 : 3);

// Numeric contact index within a shaft, for natural ordering (E1, E2, ... E10 —
// not E1, E10, E2). Strips the shaft prefix (handles digit-ending shafts like
// "E1" -> "E13" = contact 3) and separators, then reads the leading number.
function contactNum(name, group) {
  const n = String(name).replace(/[\s'\-_]/g, '').toLowerCase();
  const g = String(group || '').replace(/[\s'\-_]/g, '').toLowerCase();
  if (g && n.startsWith(g)) {
    const m = n.slice(g.length).match(/^(\d+)/);
    if (m) return parseInt(m[1], 10);
  }
  const t = n.match(/(\d+)$/);
  return t ? parseInt(t[1], 10) : 0;
}

/**
 * Stacked sEEG trace panel (canvas), correlated with the brain view.
 *
 * data: { channels, groups, times, activity(z), raw, time_unit }
 * A vertical cursor marks the current time index (shared with the brain); dragging
 * it scrubs. Hovering a row cross-highlights the contact on the brain and back.
 */
export default function SeegTracePanel({
  data, signal, setSignal, scope, setScope, shaft, setShaft,
  timeIndex, setTimeIndex, hoveredChannel, setHoveredChannel,
  width: panelW, setWidth: setPanelW, traceGain = 1, setTraceGain,
  timeWindow = null, setTimeWindow, playheadTime = null,
  traceDetail = null, onViewWindow,
  playing, setPlaying, speed = 1, setSpeed, shaftColors,
}) {
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);
  const scrollRef = useRef(null);
  const [width, setWidth] = useState(800);

  // Only channels mapped to a placed contact are shown (unmatched channels have
  // no location on the brain, so a trace for them can't be correlated).
  const mappedSet = useMemo(() => new Set(data.matched || []), [data.matched]);

  const shafts = useMemo(() => {
    const s = [];
    data.channels.forEach((name, ci) => {
      const g = data.groups?.[ci];
      if (g && mappedSet.has(name) && !s.includes(g)) s.push(g);
    });
    return s;
  }, [data.channels, data.groups, mappedSet]);

  // Rows to draw: channel index (ci) into the data columns, filtered by scope and
  // ordered naturally — by shaft (first-appearance order) then numeric contact.
  const rows = useMemo(() => {
    const out = [];
    data.channels.forEach((name, ci) => {
      if (!mappedSet.has(name)) return;                 // mapped channels only
      const group = data.groups?.[ci] || '';
      if (scope === 'shaft' && shaft && group !== shaft) return;
      out.push({ name, group, ci });
    });
    out.sort((a, b) => {
      const ga = shafts.indexOf(a.group), gb = shafts.indexOf(b.group);
      if (ga !== gb) return ga - gb;
      return contactNum(a.name, a.group) - contactNum(b.name, b.group);
    });
    return out;
  }, [data.channels, data.groups, mappedSet, scope, shaft, shafts]);

  // Two time axes: the activation map's frames (`times`, Nyquist-spaced for the band and
  // what the cursor indexes) and the voltage traces' display bins (`trace_times`). They
  // span the same recording, so everything here is placed on screen by time, not index.
  const nT = data.times.length;
  const traceTimes = data.trace_times || NO_TIMES;
  const nR = traceTimes.length;
  // Either voltage view ('filtered' or 'raw') draws from data.raw; they differ only
  // in whether the server bandpassed it, which the fetch layer decides.
  const isVoltage = signal !== 'z';
  // The two-phase fetch fills raw voltages (phase 2) after the activation map
  // (phase 1); until then neither series is here. Guard so voltage mode doesn't index
  // into a missing array. The server sends real samples OR per-bin extremes, never both.
  const hasRows = (a) => Array.isArray(a) && a.length === nR;
  const rawReady = !isVoltage
    || (nR > 0 && (hasRows(data.raw) || (hasRows(data.raw_min) && hasRows(data.raw_max))));

  // Per-bin extremes of the voltage trace (continuous mode): when present the trace
  // is drawn as a filled min..max envelope rather than a polyline through one sample
  // per bin, so a spike keeps its true height at any decimation. Gate on the server's
  // own report, not just array presence -- when the trace was strided (or not reduced
  // at all) raw_min === raw === raw_max, and filling between them would paint a
  // zero-height band, i.e. an invisible trace.
  const hasEnvelope = isVoltage && data.raw_decimation === 'minmax'
    && hasRows(data.raw_min) && hasRows(data.raw_max);

  // Common gain (shared across channels so amplitudes stay comparable): the largest
  // |value| across shown rows for the active signal. Deliberately the data's own
  // peak rather than the colorbar's robust z limit -- tying the traces to `domain`
  // clipped the biggest deflections at the map scale; this shows them in full.
  const traceScale = useMemo(() => {
    if (isVoltage && !rawReady) return 1;
    // When the trace was reduced there is no single-valued series, only the extremes.
    const arr = isVoltage ? (hasEnvelope ? data.raw_max : data.raw) : data.activity;
    if (!arr || !arr.length) return 1;
    let m = isVoltage ? 1e-6 : 1;   // floor so a flat/quiet block doesn't over-amplify
    const n = isVoltage ? nR : nT;
    // Every sample, not a sparse subsample of them. Sampling 400 frames of a
    // Nyquist-rate trace missed the true peak by ~30% on a seizure recording, and
    // anything above the scale it settled on was drawn flat against the clamp below --
    // which is to say the largest deflections, the ones worth seeing, were the ones cut
    // off. A full pass over typed arrays is a few milliseconds and runs once per change.
    const lo = hasEnvelope ? data.raw_min : null;
    const hi = hasEnvelope ? data.raw_max : null;
    for (const r of rows) for (let k = 0; k < n; k++) {
      const v = hasEnvelope
        ? Math.max(Math.abs(lo[k][r.ci]), Math.abs(hi[k][r.ci]))
        : Math.abs(arr[k][r.ci]);
      if (v > m) m = v;
    }
    return m;
  }, [isVoltage, rawReady, rows, data.raw, data.activity, data.raw_min, data.raw_max,
      hasEnvelope, nT, nR]);

  // Time -> x. The axis spans both series, which can end a fraction of a frame apart.
  const [axisT0, axisT1] = useMemo(() => {
    const ends = [data.times[0], data.times[nT - 1], traceTimes[0], traceTimes[nR - 1]]
      .filter((v) => typeof v === 'number');
    return ends.length ? [Math.min(...ends), Math.max(...ends)] : [0, 0];
  }, [data.times, traceTimes, nT, nR]);

  // ── Visible time window ────────────────────────────────────────────────────
  // The x scaler zooms the time axis. It narrows the window that is drawn rather than
  // widening the canvas: a long recording at 50x would be tens of thousands of pixels
  // across, hundreds of MB of bitmap. So the panel draws the window it is zoomed into
  // and pages that window along to follow the cursor, the way a clinical EEG viewer
  // scrolls through a file.
  // The finest time base worth offering: two sample intervals of whichever series is
  // drawn -- one cycle at its own Nyquist rate. Below that there is nothing left to
  // resolve, so a smaller number typed into the field is snapped up to it rather than
  // magnifying a couple of points across the panel. A 1-100 Hz filtered trace arrives
  // at 200 Hz, so its floor is 10 ms.
  const seriesDt = useMemo(() => {
    const axis = isVoltage && nR > 1 ? traceTimes : data.times;
    if (!axis || axis.length < 2) return 0;
    return (axis[axis.length - 1] - axis[0]) / (axis.length - 1);
  }, [isVoltage, traceTimes, nR, data.times]);
  // An unfiltered trace can always be re-fetched for the window at the acquisition
  // rate, so its floor is the sampling interval itself rather than whatever the
  // session-wide view happens to be reduced to. The band-limited series already arrive
  // at their own Nyquist rate and cannot go finer than the axis they came on.
  const nativeDt = data.rate_hz > 0 ? (data.time_unit === 'ms' ? 1000 : 1) / data.rate_hz : 0;
  const dtFloor = (signal === 'raw' && nativeDt > 0) ? nativeDt : seriesDt;
  const minSpan = dtFloor > 0 ? dtFloor * 2 : 0;

  const fullSpan = axisT1 - axisT0;
  // The axis is in the recording's own unit (ms for a trial window, s for a continuous
  // file), so a time base typed in any unit is converted into that before it is used.
  const dataUnitSec = data.time_unit === 'ms' ? 0.001 : 1;
  const winUnit = timeWindow?.unit || (data.time_unit === 'ms' ? 'ms' : 's');
  const winValue = timeWindow?.value ?? null;
  const winSpan = winValue > 0
    ? Math.max(minSpan, (winValue * UNIT_SEC[winUnit]) / dataUnitSec) : 0;
  // A time base longer than the recording is the same as fitting it.
  const viewSpan = winSpan > 0 && winSpan < fullSpan ? winSpan : fullSpan;
  // While playing, the cursor sits at the playhead itself rather than on the frame it
  // last passed: at a few seconds of window, a map frame is several pixels wide and a
  // cursor quantised to frames visibly hops (and at slow speeds, stalls between hops).
  const cursorT = playheadTime != null ? playheadTime
    : (nT > 0 ? data.times[Math.min(timeIndex, nT - 1)] : axisT0);

  // Where the window sits. A pan or a zoom sets the anchor outright; following the
  // cursor happens here, during the render that moves it, rather than in an effect
  // afterwards. A frame's delay would leave the cursor outside the window for that
  // frame -- which unmounts it, and at a short time base, where the window turns over
  // several times a second, that reads as the cursor blinking and stuttering.
  //
  // Running off an edge turns the page rather than re-centring: forwards, the cursor
  // reappears just inside the left with the whole next window ahead of it; backwards,
  // the reverse. Re-centring would throw away half of what was just read, and on
  // playback it looks like the cursor restarting from the middle every page.
  // The anchor lives in a ref, not in state, because the page it turns to has to
  // persist across renders: read from state it would still hold whatever the last pan
  // set, every frame would look like a fresh overshoot, and the window would slide
  // under a cursor pinned to the left edge instead of paging.
  const anchorRef = useRef(axisT0);
  const [, bumpAnchor] = useState(0);
  const setAnchor = (t) => { anchorRef.current = t; bumpAnchor((n) => n + 1); };

  // Only when the cursor itself has moved -- playback, a seek, a marker jump. Running
  // this on every render would undo a deliberate pan the moment it put the cursor out of
  // view, which is why the scrollbar could never travel more than a window from the
  // playhead: the next render dragged it straight back.
  const lastCursorRef = useRef(cursorT);
  const cursorMoved = cursorT !== lastCursorRef.current;
  lastCursorRef.current = cursorT;

  let anchor = anchorRef.current;
  if (cursorMoved && viewSpan > 0 && viewSpan < fullSpan) {
    const margin = viewSpan * 0.02;
    if (cursorT < anchor || cursorT > anchor + viewSpan) {
      // Clean out of the window: a seek, or -- at a short time base and any speed -- a
      // playhead that covers more than a whole window between frames. Centre it. Paging
      // would land wherever the frame happened to fall, and centring degenerates
      // gracefully into a smooth scroll with the cursor held still.
      anchor = cursorT - viewSpan / 2;
    } else if (cursorT > anchor + viewSpan - margin) {
      anchor = cursorT - margin;    // still in view: turn the page
    }
  }
  anchorRef.current = anchor;
  // Clamped on read, so a pan or a zoom can overshoot the ends without special-casing.
  const viewT0 = Math.max(axisT0, Math.min(anchor, axisT1 - viewSpan));
  const viewT1 = viewT0 + viewSpan;

  // Another recording (or another trial window) — back to the start of it.
  useEffect(() => { setAnchor(axisT0); }, [axisT0, axisT1]);   // eslint-disable-line

  // Changing the time base works about the cursor: it is what the eye is on, so it
  // stays put (in the middle of the new window) rather than falling off the edge as the
  // window narrows. An empty or non-positive amount means fit the whole recording.
  const applyWindow = (value, unit) => {
    let v = Number.isFinite(value) && value > 0 ? value : null;
    // Snap up to the data's own resolution, and show the snapped number rather than
    // silently displaying one time base while drawing another.
    if (v !== null && minSpan > 0) {
      const floorInUnit = (minSpan * dataUnitSec) / UNIT_SEC[unit];
      if (v < floorInUnit) v = Math.ceil(floorInUnit * 1000) / 1000;
    }
    const span = v ? (v * UNIT_SEC[unit]) / dataUnitSec : 0;
    setAnchor(cursorT - (span > 0 && span < fullSpan ? span : fullSpan) / 2);
    setTimeWindow?.({ value: v, unit });
  };

  // Typed, not applied per keystroke: "1" on the way to "10" would jump the window.
  const [winDraft, setWinDraft] = useState(winValue == null ? '' : String(winValue));
  useEffect(() => { setWinDraft(winValue == null ? '' : String(winValue)); }, [winValue]);
  const commitWindow = () => applyWindow(parseFloat(winDraft), winUnit);

  // ── Track width ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!wrapRef.current) return undefined;
    const ro = new ResizeObserver((e) => setWidth(e[0].contentRect.width));
    ro.observe(wrapRef.current);
    setWidth(wrapRef.current.clientWidth);
    return () => ro.disconnect();
  }, []);

  // Vertical zoom: scale row spacing and amplitude together so amplified traces
  // spread apart instead of overlapping. The canvas grows and its container scrolls.
  const rowH = ROW_H * traceGain;
  const canvasH = Math.max(rows.length * rowH, 1);

  // Time -> x, within the visible window. Everything on screen — traces, grid, cursor,
  // markers — is placed through this one function, so nothing can drift apart.
  const plotW = width - GUTTER;
  const xOfTime = useCallback((t) => (
    GUTTER + (viewSpan > 0 ? ((t - viewT0) / viewSpan) * plotW : 0)
  ), [viewT0, viewSpan, plotW]);

  const ticks = useMemo(() => {
    if (!(viewSpan > 0)) return [];
    const step = niceStep(viewSpan, 6);
    const first = Math.ceil(viewT0 / step) * step;
    const out = [];
    for (let i = 0; i < 64; i++) {
      const t = first + i * step;
      if (t > viewT1 + step * 1e-6) break;
      out.push({ t, label: tickLabel(t, step) });
    }
    return out;
  }, [viewT0, viewT1, viewSpan]);

  // What is on screen, for whoever fetches trace detail for it.
  useEffect(() => { onViewWindow?.(viewT0, viewT1); }, [viewT0, viewT1, onViewWindow]);

  // ── Draw traces ────────────────────────────────────────────────────────────
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv || width < 2) return;
    // Browsers cap a canvas at 16384 px a side; this one is as tall as every row, so a
    // high y gain on a long montage sails past that and the canvas comes back blank.
    // Trading device pixels for height keeps it drawing -- slightly softer, not gone.
    const MAX_PX = 16384;
    const dpr = Math.min(window.devicePixelRatio || 1,
                         Math.max(1, MAX_PX / Math.max(1, canvasH)));
    // Only on a real size change. This canvas is as tall as the whole stack (every row
    // at every gain), so assigning width/height -- which reallocates and clears the
    // bitmap -- costs megabytes of memory traffic, and playback redraws it many times a
    // second. clearRect alone is far cheaper.
    const pxW = Math.round(width * dpr), pxH = Math.round(canvasH * dpr);
    if (cv.width !== pxW || cv.height !== pxH) {
      cv.width = pxW; cv.height = pxH;
      cv.style.width = `${width}px`; cv.style.height = `${canvasH}px`;
    }
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, canvasH);

    // Time grid, on the same ticks the axis below is labelled with.
    ctx.strokeStyle = '#2f3a47'; ctx.lineWidth = 1;
    ticks.forEach(({ t }) => {
      const x = Math.round(xOfTime(t)) + 0.5;
      if (x < GUTTER) return;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvasH); ctx.stroke();
    });

    // Detail traces for the window on screen, when the whole-session trace is below
    // the signal's own rate (raw, in practice). Anything the detail does not cover --
    // mid-pan, before the request lands -- falls back to the overview, so the panel
    // never blanks.
    const det = isVoltage && traceDetail && Array.isArray(traceDetail.times)
      && traceDetail.times.length > 1
      && traceDetail.t0 <= viewT0 + 1e-6 && traceDetail.t1 >= viewT1 - 1e-6
      ? traceDetail : null;
    const src = det || data;
    const useEnv = isVoltage && (det ? det.raw_decimation === 'minmax' : hasEnvelope);
    const ts = isVoltage ? (det ? det.times : traceTimes) : data.times;
    const n = ts.length;
    // Only the samples inside the window, plus one either side so a polyline enters and
    // leaves the edges instead of stopping short of them. Strided from there to about
    // one point per pixel column, so zooming in costs no more drawing than zooming out.
    const k0 = n > 0 ? Math.max(0, nearestFrame(ts, viewT0) - 1) : 0;
    const k1 = n > 0 ? Math.min(n - 1, nearestFrame(ts, viewT1) + 1) : -1;
    const step = Math.max(1, Math.floor((k1 - k0 + 1) / Math.max(plotW, 1)));
    const amp = traceScale;
    ctx.font = '12px IBM Plex Mono, monospace';
    ctx.textBaseline = 'middle';

    rows.forEach((r, i) => {
      const yc = i * rowH + rowH / 2;
      const hot = hoveredChannel === r.name;
      if (hot) { ctx.fillStyle = '#00d4ff14'; ctx.fillRect(0, i * rowH, width, rowH); }
      // baseline + separator
      ctx.strokeStyle = '#1a2029'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(GUTTER, yc); ctx.lineTo(width, yc); ctx.stroke();
      // label
      ctx.fillStyle = hot ? '#e8edf2' : '#8a97a6';
      ctx.fillText(r.name, 6, yc);
      // trace (voltage traces are skipped until phase 2 fills data.raw)
      if (!(isVoltage && !rawReady) && k1 >= k0) {
        const color = shaftColor(r.group, shaftColors);
        const xAt = (k) => xOfTime(ts[k]);
        const yAt = (v) => yc - Math.max(-1, Math.min(1, v / amp)) * (rowH * 0.8);

        // Clip to the plot HORIZONTALLY: the margin sample either side of the window
        // falls outside it, and at high zoom that is far outside -- over the channel
        // labels. Vertically it must stay open: a trace swings +-0.8 of a row about its
        // centre, i.e. 1.6 rows tall, so clipping it to its own row would cut off
        // everything above 62.5% of full scale -- exactly the peaks worth seeing.
        ctx.save();
        ctx.beginPath();
        ctx.rect(GUTTER, 0, Math.max(0, width - GUTTER), canvasH);
        ctx.clip();

        if (useEnv) {
          // Filled min..max envelope: the upper bound left-to-right, the lower bound
          // back again. Shows the full excursion each pixel column spans.
          ctx.fillStyle = color;
          ctx.globalAlpha = hot ? 0.95 : 0.8;
          ctx.beginPath();
          for (let k = k0; k <= k1; k += step) ctx.lineTo(xAt(k), yAt(src.raw_max[k][r.ci]));
          for (let k = k0 + Math.floor((k1 - k0) / step) * step; k >= k0; k -= step) {
            ctx.lineTo(xAt(k), yAt(src.raw_min[k][r.ci]));
          }
          ctx.closePath();
          ctx.fill();
          // ...and a line down the middle of it. A band only a fraction of a pixel tall
          // covers only that fraction, so a quiet stretch of an otherwise identical
          // signal draws visibly fainter than the same trace as a polyline. The centre
          // line gives it the same weight as a line everywhere, and where the band is
          // tall it reads as the trace inside its own excursion.
          ctx.strokeStyle = color; ctx.lineWidth = hot ? 1.6 : 1;
          ctx.globalAlpha = hot ? 1 : 0.85;
          ctx.beginPath();
          let mstarted = false;
          for (let k = k0; k <= k1; k += step) {
            const y = yAt((src.raw_min[k][r.ci] + src.raw_max[k][r.ci]) * 0.5);
            if (!mstarted) { ctx.moveTo(xAt(k), y); mstarted = true; } else ctx.lineTo(xAt(k), y);
          }
          ctx.stroke();
        } else {
          ctx.strokeStyle = color; ctx.lineWidth = hot ? 1.6 : 1;
          ctx.globalAlpha = hot ? 1 : 0.85;
          ctx.beginPath();
          let started = false;
          for (let k = k0; k <= k1; k += step) {
            const v = isVoltage ? src.raw[k][r.ci] : data.activity[k][r.ci];
            const x = xAt(k);
            const y = yAt(v);
            if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
          }
          ctx.stroke();
        }
        ctx.restore();
      }
    });

    if (isVoltage && !rawReady) {
      ctx.fillStyle = '#7a8a99'; ctx.font = '13px IBM Plex Sans, sans-serif';
      ctx.fillText('Loading voltages…', GUTTER + 8, 12);
    }
  }, [data, rows, signal, hoveredChannel, width, canvasH, rowH, nT, nR, traceTimes, isVoltage,
      rawReady, hasEnvelope, traceScale, shaftColors, plotW, xOfTime, ticks, viewT0, viewT1,
      traceDetail]);

  // ── Cursor + interaction ───────────────────────────────────────────────────
  const cursorX = nT > 0 ? xOfTime(cursorT) : GUTTER;
  // Zoomed in, the cursor can be outside the window for the instant before it pages along.
  const cursorVisible = cursorX >= GUTTER - 0.5 && cursorX <= width;

  // Event marker: a faint fixed line at t=0 (the alignment event — stimulus or
  // response onset). Only meaningful for trial mode, whose window spans t<0..t>0.
  const zeroX = data.time_unit === 'ms' && nT > 0 && viewT0 <= 0 && viewT1 >= 0 ? xOfTime(0) : -1;

  // x -> the map frame nearest that time (the cursor always sits on a map frame).
  const timeFromX = useCallback((clientX) => {
    const rect = scrollRef.current.getBoundingClientRect();
    const x = clientX - rect.left;
    const frac = Math.max(0, Math.min(1, (x - GUTTER) / Math.max(plotW, 1)));
    return nearestFrame(data.times, viewT0 + frac * viewSpan);
  }, [plotW, data.times, viewT0, viewSpan]);

  const draggingRef = useRef(false);
  const onDown = (e) => {
    if (e.clientX - scrollRef.current.getBoundingClientRect().left < GUTTER) return;
    draggingRef.current = true; setPlaying(false); setTimeIndex(timeFromX(e.clientX));
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const onMove = (e) => {
    if (draggingRef.current) { setTimeIndex(timeFromX(e.clientX)); return; }
    // Hover row → cross-highlight
    const rect = scrollRef.current.getBoundingClientRect();
    const y = e.clientY - rect.top + scrollRef.current.scrollTop;
    const idx = Math.floor(y / rowH);
    setHoveredChannel(idx >= 0 && idx < rows.length ? rows[idx].name : null);
  };
  const onUp = () => { draggingRef.current = false; };

  // Drag the time axis to pan the window. Only does anything when zoomed in -- at 1x
  // the window is the whole recording and there is nowhere to pan to.
  const panRef = useRef(null);
  const onAxisDown = (e) => {
    if (!(viewSpan < fullSpan)) return;
    setPlaying(false);            // or the playhead would pull the window straight back
    panRef.current = { x: e.clientX, t0: viewT0 };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const onAxisMove = (e) => {
    if (!panRef.current) return;
    const dx = e.clientX - panRef.current.x;
    setAnchor(panRef.current.t0 - (dx / Math.max(plotW, 1)) * viewSpan);
  };
  const onAxisUp = () => { panRef.current = null; };

  // ── Resize (drag the left edge horizontally) ───────────────────────────────
  const onResizeDown = (e) => {
    e.preventDefault();
    const startX = e.clientX; const startW = panelW;
    const move = (ev) => setPanelW(Math.max(280, Math.min(760, startW + (startX - ev.clientX))));
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  };

  const tUnit = data.time_unit || 'ms';
  const tVal = data.times[timeIndex];
  // Continuous (seconds) timestamps show one decimal; trial (ms) rounds to the
  // nearest whole millisecond.
  const tDisplay = typeof tVal !== 'number' ? `${tVal}`
    : tUnit === 's' ? tVal.toFixed(1) : tVal.toFixed(0);

  // Reviewer markers placed on frames by the shared helper, then given an x here --
  // the frame mapping must match the brain view's exactly, the pixel position is ours.
  // A marker outside the visible window keeps its place in the jump list -- that list
  // is how you reach one that is off screen -- so this flags them rather than dropping them.
  const marks = useMemo(() => (
    buildMarks(data.times, data.annotations, tUnit)
      .map((m) => {
        const x = xOfTime(data.times[m.idx]);
        return { ...m, x, visible: x >= GUTTER - 0.5 && x <= width };
      })
  ), [data.annotations, data.times, tUnit, xOfTime, width]);

  const [showEvents, setShowEvents] = useState(true);
  // Height of the event list. Draggable divider, so a long marker track can be opened
  // up without giving up the traces.
  const [eventsH, setEventsH] = useState(150);
  // Jumping to a marker lands it a fifth of the way into the window: enough run-up to
  // see what led in, with the rest of the window given to what follows.
  const jumpTo = (idx) => {
    setPlaying(false);
    setTimeIndex(idx);
    const t = data.times[idx];
    if (typeof t === 'number') setAnchor(t - viewSpan * 0.2);
  };

  const onEventsResizeDown = (e) => {
    e.preventDefault();
    const startY = e.clientY; const startH = eventsH;
    // Cap against what the split actually has to share, so dragging up cannot collapse
    // the traces to nothing: the list may take everything above a 90px trace floor.
    const available = (wrapRef.current?.clientHeight ?? 0) + startH;
    const maxH = Math.max(48, available - 90);
    const move = (ev) => setEventsH(Math.max(48, Math.min(maxH, startH + (startY - ev.clientY))));
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  // Editable current-time field: type a timestamp and jump to the nearest frame.
  const [editingTime, setEditingTime] = useState(false);
  const [timeDraft, setTimeDraft] = useState('');
  const commitTime = () => {
    const v = parseFloat(timeDraft);
    if (!isNaN(v) && nT > 0) { setPlaying(false); setTimeIndex(nearestFrame(data.times, v)); }
    setEditingTime(false);
  };

  return (
    <div style={{ width: panelW, flexShrink: 0, height: '100%', background: '#0d1015',
      borderLeft: '1px solid #1e2530', display: 'flex', flexDirection: 'column', position: 'relative' }}>
      {/* resize handle (left edge) */}
      <div onPointerDown={onResizeDown}
        style={{ position: 'absolute', top: 0, bottom: 0, left: -3, width: 6, cursor: 'ew-resize', zIndex: 5 }} />

      {/* header controls */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px',
        borderBottom: '1px solid #161b22', flexShrink: 0, flexWrap: 'wrap' }}>
        <button onClick={() => setPlaying(!playing)} style={{ ...seg(playing), padding: '4px 10px' }}>
          {playing ? '❚❚' : '▶'}
        </button>
        <select value={speed} onChange={(e) => setSpeed?.(parseFloat(e.target.value))}
          title="Playback speed, as a multiple of real time"
          style={{ width: 'auto', padding: '3px 4px', background: '#111418', color: '#c8d4e0',
            border: '1px solid #2a3340', borderRadius: 4, fontSize: 12, fontFamily: 'IBM Plex Mono, monospace' }}>
          {(SPEEDS[data.mode] || SPEEDS.scroll).map((s) => <option key={s} value={s}>{s}×</option>)}
        </select>
        <div style={{ display: 'flex', gap: 4 }}>
          <div onClick={() => setSignal('z')} style={seg(signal === 'z')}
            title="Band-power z-score over the whole recording">z-score</div>
          <div onClick={() => setSignal('filtered')} style={seg(signal === 'filtered')}
            title="Voltage bandpassed to the selected frequency band">filtered</div>
          <div onClick={() => setSignal('raw')} style={seg(signal === 'raw')}
            title="Voltage as recorded, no bandpass">raw</div>
        </div>
        <div style={{ width: 1, height: 16, background: '#1e2530' }} />
        <div style={{ display: 'flex', gap: 4 }}>
          <div onClick={() => setScope('all')} style={seg(scope === 'all')}>all</div>
          <div onClick={() => { setScope('shaft'); if (!shaft && shafts.length) setShaft(shafts[0]); }}
            style={seg(scope === 'shaft')}>shaft</div>
        </div>
        {scope === 'shaft' && (
          <select value={shaft || ''} onChange={(e) => setShaft(e.target.value)}
            style={{ padding: '3px 6px', background: '#111418', color: '#c8d4e0',
              border: '1px solid #2a3340', borderRadius: 4, fontSize: 12, fontFamily: 'IBM Plex Mono, monospace' }}>
            {shafts.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
        <div style={{ width: 1, height: 16, background: '#1e2530' }} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }} title="Vertical trace scale">
          <span style={{ fontSize: 12, color: '#7a8a99' }}>y</span>
          <input type="number" min={0.5} max={20} step={0.5} value={traceGain}
            onChange={(e) => {
              const v = parseFloat(e.target.value);
              if (!Number.isNaN(v)) setTraceGain?.(Math.min(50, Math.max(0.1, v)));
            }}
            style={{ width: 52, padding: '3px 6px', background: '#111418', color: '#e8edf2',
              border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, textAlign: 'right',
              fontFamily: 'IBM Plex Mono, monospace' }} />
          <span style={{ fontSize: 12, color: '#7a8a99' }}>×</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}
          title="Time base: how much of the recording is on screen. Leave it empty to fit the whole recording. The window follows the cursor.">
          <span style={{ fontSize: 12, color: '#7a8a99' }}>↔</span>
          <input type="number" min={0} step="any" value={winDraft} placeholder="fit"
            onChange={(e) => setWinDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { commitWindow(); e.currentTarget.blur(); } }}
            onBlur={commitWindow}
            style={{ width: 56, padding: '3px 6px', background: '#111418', color: '#e8edf2',
              border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, textAlign: 'right',
              fontFamily: 'IBM Plex Mono, monospace' }} />
          <select value={winUnit}
            onChange={(e) => applyWindow(parseFloat(winDraft), e.target.value)}
            style={{ width: 'auto', padding: '3px 4px', background: '#111418', color: '#c8d4e0',
              border: '1px solid #2a3340', borderRadius: 4, fontSize: 12,
              fontFamily: 'IBM Plex Mono, monospace' }}>
            {Object.keys(UNIT_SEC).map((u) => <option key={u} value={u}>{u}</option>)}
          </select>
          <div onClick={() => applyWindow(null, winUnit)} style={seg(winValue == null)}
            title="Show the whole recording">all</div>
        </div>
        {marks.length > 0 && (
          <>
            <div style={{ width: 1, height: 16, background: '#1e2530' }} />
            <div onClick={() => setShowEvents(!showEvents)} style={seg(showEvents)}
              title="Show the reviewer's marker list">
              events {marks.length}
            </div>
          </>
        )}
        <div style={{ flex: 1 }} />
        <input
          value={editingTime ? timeDraft : tDisplay}
          onFocus={() => { setEditingTime(true); setTimeDraft(String(tVal)); }}
          onChange={(e) => setTimeDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { commitTime(); e.currentTarget.blur(); } }}
          onBlur={commitTime}
          title="Type a time and press Enter to jump to it"
          style={{ width: 68, padding: '3px 6px', background: '#111418', color: '#e8edf2',
            border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, textAlign: 'right',
            fontFamily: 'IBM Plex Mono, monospace' }} />
        <span style={{ fontSize: 13, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace' }}>
          {tUnit === 'ms' ? 'ms' : 's'}
        </span>
        <span style={{ fontSize: 12, color: '#7a8a99' }}>{rows.length} ch</span>
      </div>

      {/* annotation ruler: a clickable tick per reviewer marker, aligned to the plot.
          A sibling of the scroll area rather than an overlay, so it never covers a
          trace row and never swallows a scrub drag. */}
      {marks.length > 0 && (
        <div style={{ height: RULER_H, position: 'relative', flexShrink: 0,
          background: '#0a0d11', borderBottom: '1px solid #161b22' }}>
          {marks.filter((m) => m.visible).map((m, i) => (
            <div key={`t${i}`} onClick={() => jumpTo(m.idx)}
              title={`${m.onset.toFixed(1)}s — ${m.text}`
                + (m.channels?.length ? `  [${m.channels.join(', ')}]` : '')}
              style={{ position: 'absolute', left: m.x - 4, top: 0, width: 9, height: RULER_H,
                display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
              <div style={{ width: 0, height: 0, borderLeft: '4px solid transparent',
                borderRight: '4px solid transparent', borderTop: `6px solid ${m.color}` }} />
            </div>
          ))}
        </div>
      )}

      {/* trace body: vertically-scrolling canvas + fixed cursor overlay */}
      <div ref={wrapRef} style={{ flex: 1, position: 'relative', minHeight: 0 }}>
        <div ref={scrollRef} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp}
          onPointerLeave={() => { if (!draggingRef.current) setHoveredChannel(null); }}
          style={{ position: 'absolute', inset: 0, overflowY: 'auto', overflowX: 'hidden', cursor: 'crosshair' }}>
          <canvas ref={canvasRef} style={{ display: 'block' }} />
        </div>
        {/* marker lines: faint for context, emphasised + labelled for seizure onset/end */}
        {marks.filter((m) => m.visible).map((m, i) => (
          <div key={`m${i}`} style={{ position: 'absolute', top: 0, bottom: 0, left: m.x, width: 1,
            background: m.color, opacity: ANN_EMPHASIS.has(m.category) ? 0.6 : 0.16,
            pointerEvents: 'none' }} />
        ))}
        {/* The annotation currently in effect is shown over the 3D brain view, not
            here -- see SeegViewer. */}
        {/* t=0 event marker (overlay, not scrolled) — stimulus/response onset */}
        {zeroX >= GUTTER && (
          <>
            <div style={{ position: 'absolute', top: 0, bottom: 0, left: zeroX, width: 1,
              background: 'repeating-linear-gradient(#8a97a6 0 4px, transparent 4px 8px)',
              opacity: 0.6, pointerEvents: 'none' }} />
            <div style={{ position: 'absolute', top: 2, left: zeroX + 3, fontSize: 12, color: '#8a97a6',
              fontFamily: 'IBM Plex Mono, monospace', pointerEvents: 'none' }}>0</div>
          </>
        )}
        {/* time cursor (overlay, not scrolled) */}
        {cursorVisible && (
          <div style={{ position: 'absolute', top: 0, bottom: 0, left: cursorX, width: 1,
            background: '#00d4ff', pointerEvents: 'none', boxShadow: '0 0 6px #00d4ff88' }} />
        )}
      </div>

      {/* time axis: labelled ticks for the window on screen (the traces carry the same
          ticks as a grid), draggable to pan when zoomed in */}
      <div onPointerDown={onAxisDown} onPointerMove={onAxisMove} onPointerUp={onAxisUp}
        title={viewSpan < fullSpan ? 'Drag to pan the time window' : undefined}
        style={{ height: AXIS_H, position: 'relative', flexShrink: 0, overflow: 'hidden',
          background: '#0a0d11', borderTop: '1px solid #161b22', userSelect: 'none',
          cursor: viewSpan < fullSpan ? 'ew-resize' : 'default' }}>
        {ticks.map(({ t, label }) => {
          const x = xOfTime(t);
          if (x < GUTTER - 0.5 || x > width) return null;
          return (
            <React.Fragment key={t}>
              <div style={{ position: 'absolute', left: x, top: 0, width: 1, height: 6,
                background: '#6b7a8a' }} />
              <div style={{ position: 'absolute', left: x, top: 5, transform: 'translateX(-50%)',
                fontSize: 12, color: '#9aa7b4', fontFamily: 'IBM Plex Mono, monospace',
                whiteSpace: 'nowrap' }}>{label}</div>
            </React.Fragment>
          );
        })}
        <div style={{ position: 'absolute', left: 6, top: 5, fontSize: 12, color: '#4a5568',
          fontFamily: 'IBM Plex Mono, monospace' }}>{tUnit}</div>
        {cursorVisible && (
          <div style={{ position: 'absolute', left: cursorX, top: 0, width: 1, height: AXIS_H,
            background: '#00d4ff', opacity: 0.7, pointerEvents: 'none' }} />
        )}
      </div>

      {/* Position in the recording, and the way to move through it -- the horizontal
          counterpart of the trace stack's own scrollbar. Inert at a time base of `fit`,
          where the window already holds everything. */}
      <div style={{ height: 16, flexShrink: 0, display: 'flex', alignItems: 'center',
        padding: `0 6px 0 ${GUTTER}px`, background: '#0a0d11',
        borderTop: '1px solid #161b22' }}>
        <input
          type="range"
          min={axisT0}
          max={Math.max(axisT0, axisT1 - viewSpan)}
          step="any"
          value={viewT0}
          disabled={!(viewSpan < fullSpan)}
          onChange={(e) => { setPlaying(false); setAnchor(parseFloat(e.target.value)); }}
          title={viewSpan < fullSpan ? 'Scroll through the recording' : undefined}
          style={{ width: '100%', height: 10, cursor: viewSpan < fullSpan ? 'pointer' : 'default',
            accentColor: '#00d4ff', background: 'transparent',
            opacity: viewSpan < fullSpan ? 1 : 0.35 }} />
      </div>

      {/* draggable divider between the traces and the event list */}
      {showEvents && marks.length > 0 && (
        <div onPointerDown={onEventsResizeDown}
          title="Drag to resize the event list"
          style={{ height: 6, flexShrink: 0, cursor: 'ns-resize', background: '#0d1015',
            borderTop: '1px solid #1e2530', display: 'flex', alignItems: 'center',
            justifyContent: 'center' }}>
          <div style={{ width: 28, height: 2, borderRadius: 1, background: '#2a3340' }} />
        </div>
      )}

      {/* jump list: click a marker to move the cursor there */}
      {showEvents && marks.length > 0 && (
        <div style={{ height: eventsH, overflowY: 'auto', flexShrink: 0,
          background: '#0a0d11' }}>
          {marks.map((m, i) => {
            const active = m.idx === timeIndex;
            return (
              <div key={`e${i}`} onClick={() => jumpTo(m.idx)}
                onMouseEnter={() => m.channels?.length && setHoveredChannel(m.channels[0])}
                onMouseLeave={() => setHoveredChannel(null)}
                title={m.text}
                style={{ display: 'flex', alignItems: 'baseline', gap: 7, padding: '3px 10px',
                  cursor: 'pointer', fontSize: 12, fontFamily: 'IBM Plex Mono, monospace',
                  background: active ? '#00d4ff14' : 'transparent',
                  borderLeft: `2px solid ${active ? '#00d4ff' : 'transparent'}` }}>
                <span style={{ color: '#7a8a99', width: 46, textAlign: 'right', flexShrink: 0 }}>
                  {m.onset.toFixed(0)}s
                </span>
                <span style={{ width: 6, height: 6, borderRadius: 3, background: m.color,
                  flexShrink: 0, alignSelf: 'center' }} />
                <span style={{ color: active ? '#e8edf2' : '#c8d4e0', whiteSpace: 'nowrap',
                  overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.text}</span>
                {m.channels?.length > 0 && (
                  <span style={{ color: '#7a8a99', flexShrink: 0, marginLeft: 'auto' }}>
                    {m.channels.join(' ')}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
