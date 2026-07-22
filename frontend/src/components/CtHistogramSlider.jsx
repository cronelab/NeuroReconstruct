import React, { useEffect, useRef, useState, useCallback } from 'react';
import { getCtHistogram } from '../api';

/**
 * CT threshold control backed by a HU-intensity histogram with two draggable
 * handles: a floor (lower HU bound) and a ceiling (upper HU bound). Voxels in
 * the window (floor, ceiling] are rendered as the CT mesh.
 *
 * Controlled component: parent owns `floor` / `ceiling` and gets updates via
 * `onChange(floor, ceiling)`.
 */
const FALLBACK_MIN = -1000;
const FALLBACK_MAX = 3100;
const STEP = 10;        // HU rounding while dragging
const MIN_GAP = 20;     // keep floor at least this far below ceiling
const ACCENT = '#ffdd00';

export default function CtHistogramSlider({ reconId, shareToken, floor, ceiling, onChange }) {
  const [hist, setHist] = useState(null);
  const trackRef = useRef(null);
  const dragRef = useRef(null); // 'floor' | 'ceiling' | null
  const suppressClickRef = useRef(false); // ignore the click that follows a drag

  const domainMin = hist ? hist.hu_min : FALLBACK_MIN;
  const domainMax = hist ? hist.hu_max : FALLBACK_MAX;   // = CT data_max once loaded
  const span = Math.max(1, domainMax - domainMin);

  // A null/open ceiling resolves to the top of the domain (the real data_max).
  const effCeiling = (ceiling == null) ? domainMax : ceiling;

  // Fetch histogram once per reconstruction
  const snappedRef = useRef(false);
  useEffect(() => {
    let cancelled = false;
    getCtHistogram(reconId, shareToken)
      .then(res => { if (!cancelled) setHist(res.data); })
      .catch(() => { /* degrade to a plain dual slider without bars */ });
    return () => { cancelled = true; };
  }, [reconId, shareToken]);

  // Once the real data range is known, anchor an open/default ceiling to the
  // actual data_max so nothing bright is clipped and the handle sits at the
  // right edge. Runs once per load.
  useEffect(() => {
    if (!hist || snappedRef.current) return;
    snappedRef.current = true;
    if ((ceiling == null || ceiling >= FALLBACK_MAX) && ceiling !== hist.hu_max) {
      onChange(floor, hist.hu_max);
    }
    // Intentionally runs only when `hist` first arrives (snappedRef guards re-runs).
  }, [hist]);

  const toPct = useCallback(
    (v) => ((Math.min(domainMax, Math.max(domainMin, v)) - domainMin) / span) * 100,
    [domainMin, domainMax, span]
  );

  const valueFromClientX = useCallback((clientX) => {
    const el = trackRef.current;
    if (!el) return domainMin;
    const rect = el.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    const raw = domainMin + frac * span;
    return Math.round(raw / STEP) * STEP;
  }, [domainMin, span]);

  const startDrag = useCallback((which) => (e) => {
    e.preventDefault();
    dragRef.current = which;
    const onMove = (ev) => {
      if (!dragRef.current) return;
      const v = valueFromClientX(ev.clientX);
      if (dragRef.current === 'floor') {
        onChange(Math.min(v, effCeiling - MIN_GAP), effCeiling);
      } else {
        onChange(floor, Math.max(v, floor + MIN_GAP));
      }
    };
    const onUp = () => {
      dragRef.current = null;
      suppressClickRef.current = true; // the ensuing track click is a drag artifact
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [floor, effCeiling, onChange, valueFromClientX]);

  // Click on track jumps the nearest handle (but not right after a drag)
  const handleTrackClick = useCallback((e) => {
    if (dragRef.current || suppressClickRef.current) { suppressClickRef.current = false; return; }
    const v = valueFromClientX(e.clientX);
    if (Math.abs(v - floor) <= Math.abs(v - effCeiling)) {
      onChange(Math.min(v, effCeiling - MIN_GAP), effCeiling);
    } else {
      onChange(floor, Math.max(v, floor + MIN_GAP));
    }
  }, [floor, effCeiling, onChange, valueFromClientX]);

  // Log-scaled bars so metal (rare, bright) stays visible against air/tissue
  const bars = (() => {
    if (!hist) return null;
    const { bin_edges, counts } = hist;
    const maxLog = Math.max(...counts.map(c => Math.log10(c + 1)), 1);
    return counts.map((c, i) => {
      const x0 = bin_edges[i], x1 = bin_edges[i + 1];
      const inWindow = x1 > floor && x0 <= effCeiling;
      const h = (Math.log10(c + 1) / maxLog) * 100;
      return (
        <rect key={i}
          x={toPct(x0)} width={Math.max(0.001, toPct(x1) - toPct(x0))}
          y={100 - h} height={h}
          fill={inWindow ? ACCENT : '#3a4452'}
          opacity={inWindow ? 0.85 : 0.5} />
      );
    });
  })();

  const floorPct = toPct(floor);
  const ceilPct = toPct(effCeiling);

  const handleStyle = (leftPct, color) => ({
    position: 'absolute', top: -3, left: `${leftPct}%`,
    width: 12, height: 'calc(100% + 6px)', marginLeft: -6,
    cursor: 'ew-resize', zIndex: 3,
    display: 'flex', alignItems: 'stretch',
  });
  const handleBar = (color) => ({
    width: 3, margin: '0 auto', background: color, borderRadius: 2,
    boxShadow: '0 0 3px rgba(0,0,0,0.6)',
  });

  const clampFloor = (v) => Math.max(domainMin, Math.min(v, effCeiling - MIN_GAP));
  const clampCeil = (v) => Math.min(domainMax, Math.max(v, floor + MIN_GAP));

  return (
    <div style={{ width: '100%' }}>
      {/* Histogram + handles */}
      <div
        ref={trackRef}
        onClick={handleTrackClick}
        style={{ position: 'relative', height: 46, cursor: 'pointer',
          background: '#0b0e12', border: '1px solid #1e2530', borderRadius: 4, overflow: 'visible' }}
      >
        <svg viewBox="0 0 100 100" preserveAspectRatio="none"
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
          {bars}
        </svg>
        {/* selected-window overlay */}
        <div style={{ position: 'absolute', top: 0, bottom: 0,
          left: `${floorPct}%`, width: `${Math.max(0, ceilPct - floorPct)}%`,
          background: `${ACCENT}18`, borderLeft: `1px solid ${ACCENT}`, borderRight: `1px solid ${ACCENT}`,
          pointerEvents: 'none', zIndex: 1 }} />
        {/* handles */}
        <div style={handleStyle(floorPct)} onMouseDown={startDrag('floor')} title="Floor (lower HU)">
          <div style={handleBar(ACCENT)} />
        </div>
        <div style={handleStyle(ceilPct)} onMouseDown={startDrag('ceiling')} title="Ceiling (upper HU)">
          <div style={handleBar('#ff8c00')} />
        </div>
      </div>

      {/* Numeric floor / ceiling inputs */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
        <span style={{ fontSize: 11, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace' }}>Floor</span>
        <input type="number" min={domainMin} max={domainMax} step={STEP} value={floor}
          onChange={e => { const v = Number(e.target.value); if (!isNaN(v)) onChange(clampFloor(v), effCeiling); }}
          style={numInput(ACCENT)} />
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 11, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace' }}>Ceiling</span>
        <input type="number" min={domainMin} max={domainMax} step={STEP} value={effCeiling}
          onChange={e => { const v = Number(e.target.value); if (!isNaN(v)) onChange(floor, clampCeil(v)); }}
          style={numInput('#ff8c00')} />
        <span style={{ fontSize: 11, color: '#b0bec5', fontFamily: 'IBM Plex Mono, monospace' }}>HU</span>
      </div>
    </div>
  );
}

const numInput = (color) => ({
  width: 62, textAlign: 'right', fontFamily: 'IBM Plex Mono, monospace', fontSize: 12,
  color, background: '#111418', border: '1px solid #2a3340', borderRadius: 4, padding: '3px 6px',
});
