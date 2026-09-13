import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { shaftColorOf as shaftColor } from '../seegColors';
import { ANN_EMPHASIS, buildMarks, nearestIndex as nearestFrame } from '../seegAnnotations';

const seg = (active) => ({
  padding: '3px 9px', fontSize: 10, fontFamily: 'IBM Plex Mono, monospace', cursor: 'pointer',
  border: `1px solid ${active ? '#00d4ff55' : '#2a3340'}`, borderRadius: 4,
  background: active ? '#002233' : 'transparent', color: active ? '#00d4ff' : '#7a8a99',
});

const ROW_H = 22;
const GUTTER = 68;
const RULER_H = 15;

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
  playing, setPlaying, shaftColors,
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

  const nT = data.times.length;
  // Either voltage view ('filtered' or 'raw') draws from data.raw; they differ only
  // in whether the server bandpassed it, which the fetch layer decides.
  const isVoltage = signal !== 'z';
  // The two-phase fetch fills raw voltages (phase 2) after the activation map
  // (phase 1); until then data.raw is empty. Guard so voltage mode doesn't index
  // into a missing array.
  const rawReady = !isVoltage || (Array.isArray(data.raw) && data.raw.length === nT);

  // Per-bin extremes of the voltage trace (continuous mode): when present the trace
  // is drawn as a filled min..max envelope rather than a polyline through one sample
  // per bin, so a spike keeps its true height at any decimation. Gate on the server's
  // own report, not just array presence -- when the trace was strided (or not reduced
  // at all) raw_min === raw === raw_max, and filling between them would paint a
  // zero-height band, i.e. an invisible trace.
  const hasEnvelope = isVoltage && data.raw_decimation === 'minmax'
    && Array.isArray(data.raw_min) && data.raw_min.length === nT
    && Array.isArray(data.raw_max) && data.raw_max.length === nT;

  // Common gain (shared across channels so amplitudes stay comparable): the largest
  // |value| across shown rows for the active signal. Deliberately the data's own
  // peak rather than the colorbar's robust z limit -- tying the traces to `domain`
  // clipped the biggest deflections at the map scale; this shows them in full.
  const traceScale = useMemo(() => {
    if (isVoltage && !rawReady) return 1;
    const arr = isVoltage ? data.raw : data.activity;
    if (!arr || !arr.length) return 1;
    let m = isVoltage ? 1e-6 : 1;   // floor so a flat/quiet block doesn't over-amplify
    const step = Math.max(1, Math.floor(nT / 400));
    // Scale to the extremes actually drawn, or the envelope clips at the midpoint scale.
    const lo = hasEnvelope ? data.raw_min : null;
    const hi = hasEnvelope ? data.raw_max : null;
    for (const r of rows) for (let k = 0; k < nT; k += step) {
      const v = hasEnvelope
        ? Math.max(Math.abs(lo[k][r.ci]), Math.abs(hi[k][r.ci]))
        : Math.abs(arr[k][r.ci]);
      if (v > m) m = v;
    }
    return m;
  }, [isVoltage, rawReady, rows, data.raw, data.activity, data.raw_min, data.raw_max,
      hasEnvelope, nT]);

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

  // ── Draw traces ────────────────────────────────────────────────────────────
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv || width < 2) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = width * dpr; cv.height = canvasH * dpr;
    cv.style.width = `${width}px`; cv.style.height = `${canvasH}px`;
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, canvasH);

    const plotW = width - GUTTER;
    const step = Math.max(1, Math.floor(nT / plotW));
    const amp = traceScale;
    ctx.font = '10px IBM Plex Mono, monospace';
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
      if (!(isVoltage && !rawReady)) {
        const color = shaftColor(r.group, shaftColors);
        const xAt = (k) => GUTTER + (nT > 1 ? (k / (nT - 1)) * plotW : 0);
        const yAt = (v) => yc - Math.max(-1, Math.min(1, v / amp)) * (rowH * 0.8);

        if (hasEnvelope) {
          // Filled min..max envelope: the upper bound left-to-right, the lower bound
          // back again. Shows the full excursion each pixel column spans.
          ctx.fillStyle = color;
          ctx.globalAlpha = hot ? 0.95 : 0.75;
          ctx.beginPath();
          for (let k = 0; k < nT; k += step) ctx.lineTo(xAt(k), yAt(data.raw_max[k][r.ci]));
          for (let k = nT - 1 - ((nT - 1) % step); k >= 0; k -= step) {
            ctx.lineTo(xAt(k), yAt(data.raw_min[k][r.ci]));
          }
          ctx.closePath();
          ctx.fill();
        } else {
          ctx.strokeStyle = color; ctx.lineWidth = hot ? 1.6 : 1;
          ctx.globalAlpha = hot ? 1 : 0.85;
          ctx.beginPath();
          let started = false;
          for (let k = 0; k < nT; k += step) {
            const v = isVoltage ? data.raw[k][r.ci] : data.activity[k][r.ci];
            const x = xAt(k);
            const y = yAt(v);
            if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
          }
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
      }
    });

    if (isVoltage && !rawReady) {
      ctx.fillStyle = '#7a8a99'; ctx.font = '11px IBM Plex Sans, sans-serif';
      ctx.fillText('Loading voltages…', GUTTER + 8, 12);
    }
  }, [data, rows, signal, hoveredChannel, width, canvasH, rowH, nT, isVoltage, rawReady,
      hasEnvelope, traceScale, shaftColors]);

  // ── Cursor + interaction ───────────────────────────────────────────────────
  const plotW = width - GUTTER;
  const cursorX = GUTTER + (nT > 1 ? (timeIndex / (nT - 1)) * plotW : 0);

  // Event marker: a faint fixed line at t=0 (the alignment event — stimulus or
  // response onset). Only meaningful for trial mode, whose window spans t<0..t>0.
  const zeroIdx = useMemo(() => {
    if (data.time_unit !== 'ms' || nT === 0) return -1;
    let best = -1, bd = Infinity;
    for (let i = 0; i < nT; i++) {
      const d = Math.abs(data.times[i]);
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }, [data.times, data.time_unit, nT]);
  const zeroX = zeroIdx >= 0 ? GUTTER + (nT > 1 ? (zeroIdx / (nT - 1)) * plotW : 0) : -1;

  const timeFromX = useCallback((clientX) => {
    const rect = scrollRef.current.getBoundingClientRect();
    const x = clientX - rect.left;
    const frac = Math.max(0, Math.min(1, (x - GUTTER) / Math.max(plotW, 1)));
    return Math.round(frac * (nT - 1));
  }, [plotW, nT]);

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
  const marks = useMemo(() => (
    buildMarks(data.times, data.annotations, tUnit)
      .map((m) => ({ ...m, x: GUTTER + (nT > 1 ? (m.idx / (nT - 1)) * plotW : 0) }))
  ), [data.annotations, data.times, tUnit, nT, plotW]);

  const [showEvents, setShowEvents] = useState(true);
  // Height of the event list. Draggable divider, so a long marker track can be opened
  // up without giving up the traces.
  const [eventsH, setEventsH] = useState(150);
  const jumpTo = useCallback((idx) => { setPlaying(false); setTimeIndex(idx); },
    [setPlaying, setTimeIndex]);

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
              border: '1px solid #2a3340', borderRadius: 4, fontSize: 10, fontFamily: 'IBM Plex Mono, monospace' }}>
            {shafts.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
        <div style={{ width: 1, height: 16, background: '#1e2530' }} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }} title="Vertical trace scale">
          <span style={{ fontSize: 10, color: '#7a8a99' }}>y</span>
          <input type="number" min={0.5} max={20} step={0.5} value={traceGain}
            onChange={(e) => {
              const v = parseFloat(e.target.value);
              if (!Number.isNaN(v)) setTraceGain?.(Math.min(50, Math.max(0.1, v)));
            }}
            style={{ width: 46, padding: '3px 6px', background: '#111418', color: '#e8edf2',
              border: '1px solid #2a3340', borderRadius: 4, fontSize: 11, textAlign: 'right',
              fontFamily: 'IBM Plex Mono, monospace' }} />
          <span style={{ fontSize: 10, color: '#7a8a99' }}>×</span>
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
          style={{ width: 60, padding: '3px 6px', background: '#111418', color: '#e8edf2',
            border: '1px solid #2a3340', borderRadius: 4, fontSize: 11, textAlign: 'right',
            fontFamily: 'IBM Plex Mono, monospace' }} />
        <span style={{ fontSize: 11, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace' }}>
          {tUnit === 'ms' ? 'ms' : 's'}
        </span>
        <span style={{ fontSize: 10, color: '#7a8a99' }}>{rows.length} ch</span>
      </div>

      {/* annotation ruler: a clickable tick per reviewer marker, aligned to the plot.
          A sibling of the scroll area rather than an overlay, so it never covers a
          trace row and never swallows a scrub drag. */}
      {marks.length > 0 && (
        <div style={{ height: RULER_H, position: 'relative', flexShrink: 0,
          background: '#0a0d11', borderBottom: '1px solid #161b22' }}>
          {marks.map((m, i) => (
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
        {marks.map((m, i) => (
          <div key={`m${i}`} style={{ position: 'absolute', top: 0, bottom: 0, left: m.x, width: 1,
            background: m.color, opacity: ANN_EMPHASIS.has(m.category) ? 0.6 : 0.16,
            pointerEvents: 'none' }} />
        ))}
        {/* The annotation currently in effect is shown over the 3D brain view, not
            here -- see SeegViewer. */}
        {/* t=0 event marker (overlay, not scrolled) — stimulus/response onset */}
        {zeroX >= 0 && (
          <>
            <div style={{ position: 'absolute', top: 0, bottom: 0, left: zeroX, width: 1,
              background: 'repeating-linear-gradient(#8a97a6 0 4px, transparent 4px 8px)',
              opacity: 0.6, pointerEvents: 'none' }} />
            <div style={{ position: 'absolute', top: 2, left: zeroX + 3, fontSize: 9, color: '#8a97a6',
              fontFamily: 'IBM Plex Mono, monospace', pointerEvents: 'none' }}>0</div>
          </>
        )}
        {/* time cursor (overlay, not scrolled) */}
        <div style={{ position: 'absolute', top: 0, bottom: 0, left: cursorX, width: 1,
          background: '#00d4ff', pointerEvents: 'none', boxShadow: '0 0 6px #00d4ff88' }} />
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
                  cursor: 'pointer', fontSize: 10.5, fontFamily: 'IBM Plex Mono, monospace',
                  background: active ? '#00d4ff14' : 'transparent',
                  borderLeft: `2px solid ${active ? '#00d4ff' : 'transparent'}` }}>
                <span style={{ color: '#7a8a99', width: 40, textAlign: 'right', flexShrink: 0 }}>
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
