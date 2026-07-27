import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { shaftColorOf as shaftColor } from '../seegColors';

const seg = (active) => ({
  padding: '3px 9px', fontSize: 10, fontFamily: 'IBM Plex Mono, monospace', cursor: 'pointer',
  border: `1px solid ${active ? '#00d4ff55' : '#2a3340'}`, borderRadius: 4,
  background: active ? '#002233' : 'transparent', color: active ? '#00d4ff' : '#7a8a99',
});

const ROW_H = 22;
const GUTTER = 68;

/**
 * Stacked sEEG trace panel (canvas), correlated with the brain view.
 *
 * data: { channels, groups, times, activity(z), raw, time_unit }
 * A vertical cursor marks the current time index (shared with the brain); dragging
 * it scrubs. Hovering a row cross-highlights the contact on the brain and back.
 */
export default function SeegTracePanel({
  data, signal, setSignal, scope, setScope, shaft, setShaft,
  domain, timeIndex, setTimeIndex, hoveredChannel, setHoveredChannel,
  width: panelW, setWidth: setPanelW, playing, setPlaying, shaftColors,
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

  // Rows to draw: channel index (ci) into the data columns, filtered by scope.
  const rows = useMemo(() => {
    const out = [];
    data.channels.forEach((name, ci) => {
      if (!mappedSet.has(name)) return;                 // mapped channels only
      const group = data.groups?.[ci] || '';
      if (scope === 'shaft' && shaft && group !== shaft) return;
      out.push({ name, group, ci });
    });
    return out;
  }, [data.channels, data.groups, mappedSet, scope, shaft]);

  const nT = data.times.length;
  const isRaw = signal === 'raw';

  // Common gain for raw voltage: 98th percentile of |uV| across shown rows.
  const rawScale = useMemo(() => {
    if (!isRaw) return 1;
    let m = 1e-6;
    const step = Math.max(1, Math.floor(nT / 400));
    for (const r of rows) for (let k = 0; k < nT; k += step) {
      const v = Math.abs(data.raw[k][r.ci]); if (v > m) m = v;
    }
    return m;
  }, [isRaw, rows, data.raw, nT]);

  // ── Track width ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!wrapRef.current) return undefined;
    const ro = new ResizeObserver((e) => setWidth(e[0].contentRect.width));
    ro.observe(wrapRef.current);
    setWidth(wrapRef.current.clientWidth);
    return () => ro.disconnect();
  }, []);

  const canvasH = Math.max(rows.length * ROW_H, 1);

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
    const amp = isRaw ? rawScale : Math.max(domain, 1);
    ctx.font = '10px IBM Plex Mono, monospace';
    ctx.textBaseline = 'middle';

    rows.forEach((r, i) => {
      const yc = i * ROW_H + ROW_H / 2;
      const hot = hoveredChannel === r.name;
      if (hot) { ctx.fillStyle = '#00d4ff14'; ctx.fillRect(0, i * ROW_H, width, ROW_H); }
      // baseline + separator
      ctx.strokeStyle = '#1a2029'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(GUTTER, yc); ctx.lineTo(width, yc); ctx.stroke();
      // label
      ctx.fillStyle = hot ? '#e8edf2' : '#8a97a6';
      ctx.fillText(r.name, 6, yc);
      // trace
      ctx.strokeStyle = shaftColor(r.group, shaftColors); ctx.lineWidth = hot ? 1.6 : 1;
      ctx.globalAlpha = hot ? 1 : 0.85;
      ctx.beginPath();
      let started = false;
      for (let k = 0; k < nT; k += step) {
        const v = isRaw ? data.raw[k][r.ci] : data.activity[k][r.ci];
        const x = GUTTER + (nT > 1 ? (k / (nT - 1)) * plotW : 0);
        const y = yc - Math.max(-1, Math.min(1, v / amp)) * (ROW_H * 0.46);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.globalAlpha = 1;
    });
  }, [data, rows, signal, domain, hoveredChannel, width, canvasH, nT, isRaw, rawScale, shaftColors]);

  // ── Cursor + interaction ───────────────────────────────────────────────────
  const plotW = width - GUTTER;
  const cursorX = GUTTER + (nT > 1 ? (timeIndex / (nT - 1)) * plotW : 0);

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
    const idx = Math.floor(y / ROW_H);
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

  // Editable current-time field: type a timestamp and jump to the nearest frame.
  const [editingTime, setEditingTime] = useState(false);
  const [timeDraft, setTimeDraft] = useState('');
  const commitTime = () => {
    const v = parseFloat(timeDraft);
    if (!isNaN(v) && nT > 0) {
      let best = 0, bd = Infinity;
      for (let i = 0; i < nT; i++) {
        const d = Math.abs(data.times[i] - v);
        if (d < bd) { bd = d; best = i; }
      }
      setPlaying(false); setTimeIndex(best);
    }
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
          <div onClick={() => setSignal('z')} style={seg(signal === 'z')}>z-score</div>
          <div onClick={() => setSignal('raw')} style={seg(signal === 'raw')}>voltage</div>
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
        <div style={{ flex: 1 }} />
        <input
          value={editingTime ? timeDraft : `${tVal}`}
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

      {/* trace body: vertically-scrolling canvas + fixed cursor overlay */}
      <div ref={wrapRef} style={{ flex: 1, position: 'relative', minHeight: 0 }}>
        <div ref={scrollRef} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp}
          onPointerLeave={() => { if (!draggingRef.current) setHoveredChannel(null); }}
          style={{ position: 'absolute', inset: 0, overflowY: 'auto', overflowX: 'hidden', cursor: 'crosshair' }}>
          <canvas ref={canvasRef} style={{ display: 'block' }} />
        </div>
        {/* time cursor (overlay, not scrolled) */}
        <div style={{ position: 'absolute', top: 0, bottom: 0, left: cursorX, width: 1,
          background: '#00d4ff', pointerEvents: 'none', boxShadow: '0 0 6px #00d4ff88' }} />
      </div>
    </div>
  );
}
