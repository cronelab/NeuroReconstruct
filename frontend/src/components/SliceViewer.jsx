import React, { useEffect, useRef, useState, useCallback } from 'react';
import { useAppStore } from '../store';
import LocatorOverlay from './LocatorOverlay';

const AXIS_INFO = {
  sagittal: { label: 'Sagittal', color: '#ff6b6b' },
  coronal:  { label: 'Coronal',  color: '#4fc3f7' },
  axial:    { label: 'Axial',    color: '#81c784' },
};

const PREFETCH_AHEAD = 10;
const PREFETCH_BEHIND = 4;
const MAX_CONCURRENT = 6; // throttle simultaneous requests
const CONTACT_THICKNESS_MM = 4.0; // show contacts within ±this many mm of slice plane

export default function SliceViewer({ reconId, axis = 'axial', isThumbnail = false, onSliceChange, locator }) {
  const { reconstruction, meshData, structuresData, structureVisible } = useAppStore();
  const canvasRef = useRef(null);

  // Structure overlay cache + current bitmap
  const overlayRef = useRef(null);
  const overlayCacheRef = useRef(new Map());   // slice_idx -> ImageBitmap
  const overlayInFlightRef = useRef(new Set());

  // MRI affine (inverse) and volume shape — received from backend headers once
  const invAffineRef = useRef(null);   // flat 16-element array (row-major)
  const volShapeRef  = useRef(null);   // [nx, ny, nz]

  // cache: Map<sliceIdx, { bitmap, planeNormal, planeOffset, voxelSize, sliceIdx }>
  const cacheRef = useRef(new Map());
  const inFlightRef = useRef(new Set());
  const queueRef = useRef([]); // pending prefetch indices
  const activeCountRef = useRef(0);
  const pendingCbRef = useRef(new Map()); // idx -> [onDone,...] waiting on an in-flight fetch

  const sliceIdxRef = useRef(0);
  const sliceCountRef = useRef(1);
  const scrollDirRef = useRef(1);
  const currentEntryRef = useRef(null); // { bitmap, planeNormal, planeOffset, voxelSize, sliceIdx }

  const [renderTick, setRenderTick] = useState(0);
  const [sliceLabel, setSliceLabel] = useState({ idx: 0, count: 1 });
  const [status, setStatus] = useState('loading');
  const [errorMsg, setErrorMsg] = useState('');
  const info = AXIS_INFO[axis];

  const triggerDraw = useCallback(() => setRenderTick(t => t + 1), []);

  const fetchOverlay = useCallback(async (idx) => {
    if (!reconId || isThumbnail) return;
    if (overlayInFlightRef.current.has(idx)) return;
    if (overlayCacheRef.current.has(idx)) {
      overlayRef.current = overlayCacheRef.current.get(idx);
      triggerDraw();
      return;
    }
    overlayInFlightRef.current.add(idx);
    try {
      const token = localStorage.getItem('token');
      // Build visible keys param from current store state
      const { structuresData: sd, structureVisible: sv } = useAppStore.getState();
      const visibleKeys = sd
        ? Object.keys(sd).filter(k => sd[k].vertices && sv[k] !== false).join(',')
        : '';
      if (!visibleKeys) { overlayRef.current = null; triggerDraw(); return; }

      const res = await fetch(
        `/api/reconstructions/${reconId}/structure-slice?axis=${axis}&slice_idx=${idx}&visible=${encodeURIComponent(visibleKeys)}`,
        { headers: token ? { Authorization: `Bearer ${token}` } : {} }
      );
      if (res.ok) {
        const blob = await res.blob();
        const bitmap = await createImageBitmap(blob);
        overlayCacheRef.current.set(idx, bitmap);
        if (sliceIdxRef.current === idx) {
          overlayRef.current = bitmap;
          triggerDraw();
        }
      }
    } catch (_) { /* ignore */ } finally {
      overlayInFlightRef.current.delete(idx);
    }
  }, [reconId, axis, isThumbnail, triggerDraw]);

  // Draw — reads from refs only
  const doDraw = useCallback(() => {
    const canvas = canvasRef.current;
    const entry = currentEntryRef.current;
    if (!canvas) return;
    const W = canvas.offsetWidth;
    const H = canvas.offsetHeight;
    if (W === 0 || H === 0) return;
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, W, H);
    if (!entry?.bitmap) return;

    const { bitmap, planeNormal, planeOffset } = entry;
    const sliceSpacingMm = entry.voxelSize || 1;
    // Aspect-correct for anisotropic voxels: scale by physical mm extent, not pixel count
    const pxW = entry.pxWidthMm || 1;
    const pxH = entry.pxHeightMm || 1;
    const physW = bitmap.width * pxW;
    const physH = bitmap.height * pxH;
    const scale = Math.min(W / physW, H / physH);
    const dw = physW * scale;
    const dh = physH * scale;
    const dx = (W - dw) / 2;
    const dy = (H - dh) / 2;
    ctx.drawImage(bitmap, dx, dy, dw, dh);

    // Structure overlay
    const overlay = overlayRef.current;
    if (!isThumbnail && overlay) {
      ctx.globalAlpha = 0.45;
      ctx.drawImage(overlay, dx, dy, dw, dh);
      ctx.globalAlpha = 1.0;
    }

    if (!isThumbnail) {
      // Crosshairs
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1;
      ctx.strokeStyle = 'rgba(255,255,255,0.12)';
      ctx.beginPath(); ctx.moveTo(W/2, 0); ctx.lineTo(W/2, H); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, H/2); ctx.lineTo(W, H/2); ctx.stroke();
      ctx.setLineDash([]);

      // Electrode contacts — only show those near this slice plane
      const shafts = reconstruction?.electrode_shafts || [];
      const meshCenter = meshData?.center || [0, 0, 0];
      const iA = invAffineRef.current;    // flat 16-element row-major inverse affine
      const vShape = volShapeRef.current; // [nx, ny, nz]
      const drawnIdx = entry.sliceIdx != null ? entry.sliceIdx : sliceIdxRef.current;

      // Voxel index (0..n-1) → normalised position within the drawn rect, for a
      // display axis the backend flipped. drawImage spreads the image's n pixel
      // *columns* evenly over the rect, so pixel j is centred at (j+0.5)/n — not
      // at j/(n-1), which would put the outermost voxel centres on the rect's edge
      // and stretch the overlay by n/(n-1) about the centre.
      const flipNorm = (v, n) => (n - 0.5 - v) / n;

      shafts.forEach(shaft => {
        (shaft.contacts || []).forEach(c => {
          if (c.x_mm == null) return;

          // Convert Three.js (mesh-centred) → world RAS
          const wx = c.x_mm + meshCenter[0];
          const wy = c.y_mm + meshCenter[1];
          const wz = c.z_mm + meshCenter[2];

          let canX, canY, dist;
          if (iA && vShape) {
            // Voxel coordinates via inverse affine  (row-major 4×4)
            const vx = iA[0]*wx + iA[1]*wy + iA[2]*wz + iA[3];
            const vy = iA[4]*wx + iA[5]*wy + iA[6]*wz + iA[7];
            const vz = iA[8]*wx + iA[9]*wy + iA[10]*wz + iA[11];
            const [nx, ny, nz] = vShape;

            // Filter: only contacts near the current slice plane. Do this in voxel
            // space — never by comparing the contact's world x/y/z against
            // X-Slice-World-Coord, which is just the plane's centre and drifts across
            // the plane on an oblique volume (measured: up to 26 slices out on this
            // dataset's axial view). The voxel-index difference is exact for any
            // affine, and is the same quantity the X-Slice-Plane-* headers express;
            // we use the voxel form here because vx/vy/vz are needed anyway.
            const vAxis = axis === 'sagittal' ? vx : axis === 'coronal' ? vy : vz;
            dist = Math.abs(vAxis - drawnIdx) * sliceSpacingMm;
            if (dist > CONTACT_THICKNESS_MM) return;

            // Map to normalised display coords using the same rot90+fliplr the backend applies:
            //   axial:    display[row=vy, col=vx]  img size (ny, nx)
            //   sagittal: display[row=vz, col=vy]  img size (nz, ny)
            //   coronal:  display[row=vz, col=vx]  img size (nz, nx)
            // rot90(k=1) + fliplr inverts both in-plane axes:
            //   axial:    display[row=ny-1-vy, col=nx-1-vx]
            //   sagittal: display[row=nz-1-vz, col=ny-1-vy]
            //   coronal:  display[row=nz-1-vz, col=nx-1-vx]
            let normX, normY;
            if (axis === 'axial')    { normX = flipNorm(vx, nx); normY = flipNorm(vy, ny); }
            if (axis === 'sagittal') { normX = flipNorm(vy, ny); normY = flipNorm(vz, nz); }
            if (axis === 'coronal')  { normX = flipNorm(vx, nx); normY = flipNorm(vz, nz); }
            canX = dx + normX * dw;
            canY = dy + normY * dh;
          } else {
            // No inverse affine yet, so the in-plane position is unknown — park the
            // marker at the centre. The plane test is still exact: the plane headers
            // give the perpendicular mm distance directly, no affine needed.
            dist = planeNormal
              ? Math.abs(planeNormal[0]*wx + planeNormal[1]*wy + planeNormal[2]*wz - planeOffset)
              : 0;
            if (dist > CONTACT_THICKNESS_MM) return;
            canX = dx + dw / 2;
            canY = dy + dh / 2;
          }

          const alpha = Math.max(0.3, 1 - dist / CONTACT_THICKNESS_MM);
          const radius = dist < 1.5 ? 5 : 3.5;

          ctx.beginPath();
          ctx.arc(canX, canY, radius, 0, Math.PI*2);
          ctx.fillStyle = shaft.color + Math.round(alpha * 255).toString(16).padStart(2, '0');
          ctx.fill();
          ctx.strokeStyle = '#000';
          ctx.lineWidth = 1;
          ctx.stroke();
        });
      });

      // Labels
      ctx.fillStyle = info.color;
      ctx.font = 'bold 13px IBM Plex Mono, monospace';
      ctx.fillText(info.label.toUpperCase(), 12, 24);
      ctx.fillStyle = '#4a5568';
      ctx.font = '11px IBM Plex Mono, monospace';
      ctx.fillText(`${sliceIdxRef.current + 1} / ${sliceCountRef.current}`, 12, 42);
    } else {
      ctx.fillStyle = info.color;
      ctx.font = 'bold 9px IBM Plex Mono, monospace';
      ctx.fillText(info.label.slice(0,3).toUpperCase(), 4, 12);
    }
  }, [axis, isThumbnail, reconstruction, meshData, info]);

  useEffect(() => { doDraw(); }, [renderTick, doDraw]);

  // Resize observer
  useEffect(() => {
    if (isThumbnail) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const obs = new ResizeObserver(() => triggerDraw());
    obs.observe(canvas);
    return () => obs.disconnect();
  }, [isThumbnail, triggerDraw]);

  // Process prefetch queue respecting MAX_CONCURRENT
  const processQueue = useCallback(() => {
    while (queueRef.current.length > 0 && activeCountRef.current < MAX_CONCURRENT) {
      const idx = queueRef.current.shift();
      if (idx < 0 || idx >= sliceCountRef.current) continue;
      if (cacheRef.current.has(idx) || inFlightRef.current.has(idx)) continue;
      doFetch(idx, null); // eslint-disable-line
    }
  }, []); // eslint-disable-line

  const doFetch = useCallback(async (idx, onDone) => {
    if (!reconId) return;
    if (idx >= 0 && cacheRef.current.has(idx)) {
      onDone?.(cacheRef.current.get(idx), idx);
      return;
    }
    if (inFlightRef.current.has(idx)) {
      // Already loading (e.g. a prefetch). Attach this waiter so dragging onto
      // an in-flight slice still displays it once the fetch resolves, instead
      // of silently dropping the request.
      if (onDone) {
        const arr = pendingCbRef.current.get(idx) || [];
        arr.push(onDone);
        pendingCbRef.current.set(idx, arr);
      }
      return;
    }
    inFlightRef.current.add(idx);
    activeCountRef.current++;
    try {
      const token = localStorage.getItem('token');
      const res = await fetch(
        `/api/reconstructions/${reconId}/mri-slice?axis=${axis}&slice_idx=${idx}`,
        { headers: token ? { Authorization: `Bearer ${token}` } : {} }
      );
      if (!res.ok) {
        const text = await res.text();
        setErrorMsg(`${res.status}: ${text.slice(0, 120)}`);
        setStatus('error');
        return;
      }
      const count = parseInt(res.headers.get('X-Slice-Count') || '1');
      const actual = parseInt(res.headers.get('X-Slice-Index') || '0');
      const voxelSize = parseFloat(res.headers.get('X-Voxel-Size-Mm') || '1');
      // Exact slice plane: |planeNormal · P - planeOffset| = mm from world point P
      // to this slice. Unlike X-Slice-World-Coord this holds for oblique volumes.
      const pn = res.headers.get('X-Slice-Plane-Normal');
      const planeNormal = pn ? JSON.parse(pn) : null;
      const planeOffset = parseFloat(res.headers.get('X-Slice-Plane-Offset') || 'NaN');
      const pxWidthMm = parseFloat(res.headers.get('X-Display-Px-Width-Mm') || '1');
      const pxHeightMm = parseFloat(res.headers.get('X-Display-Px-Height-Mm') || '1');
      sliceCountRef.current = count;
      // Parse affine + shape once (they don't change per slice)
      if (!invAffineRef.current) {
        const ia = res.headers.get('X-Volume-Inv-Affine');
        const vs = res.headers.get('X-Volume-Shape');
        if (ia) invAffineRef.current = JSON.parse(ia);
        if (vs) volShapeRef.current  = JSON.parse(vs);
      }

      const blob = await res.blob();
      const bitmap = await createImageBitmap(blob);
      const entry = { bitmap, planeNormal, planeOffset, voxelSize, pxWidthMm, pxHeightMm, sliceIdx: actual };
      if (actual >= 0) cacheRef.current.set(actual, entry);
      onDone?.(entry, actual);
      const waiters = pendingCbRef.current.get(idx);
      if (waiters) { pendingCbRef.current.delete(idx); waiters.forEach(cb => cb(entry, actual)); }
    } catch (e) {
      setErrorMsg(e.message);
      setStatus('error');
    } finally {
      inFlightRef.current.delete(idx);
      pendingCbRef.current.delete(idx); // clear any waiters stranded by an error
      activeCountRef.current = Math.max(0, activeCountRef.current - 1);
      processQueue();
    }
  }, [reconId, axis, processQueue]);

  const goToSlice = useCallback((idx) => {
    const clamped = Math.max(0, Math.min(sliceCountRef.current - 1, idx));
    sliceIdxRef.current = clamped;

    // Move the scrollbar/label to the requested slice NOW, synchronously —
    // the input is controlled by sliceLabel.idx, so if we waited for the async
    // image to load the thumb would snap back to the previous value mid-drag.
    setSliceLabel({ idx: clamped, count: sliceCountRef.current });
    onSliceChange?.(clamped, sliceCountRef.current);

    // Point the overlay at THIS slice (cached bitmap or nothing) so we never
    // paint a different slice's shading over the new base while it loads.
    if (useAppStore.getState().structuresData) {
      overlayRef.current = overlayCacheRef.current.get(clamped) || null;
    }

    const showEntry = (entry, actual) => {
      if (sliceIdxRef.current !== actual) return; // superseded by a newer scroll
      currentEntryRef.current = entry;
      setStatus('ok');
      triggerDraw();
    };

    if (cacheRef.current.has(clamped)) {
      showEntry(cacheRef.current.get(clamped), clamped);
    } else {
      triggerDraw(); // redraw immediately (clears stale overlay) while the base loads
      doFetch(clamped, showEntry);
    }

    // Prefetch neighbors — add to queue
    const dir = scrollDirRef.current;
    for (let i = 1; i <= PREFETCH_AHEAD; i++) queueRef.current.push(clamped + i * dir);
    for (let i = 1; i <= PREFETCH_BEHIND; i++) queueRef.current.push(clamped - i * dir);
    processQueue();

    // Fetch structure overlay for this slice if structures are loaded
    if (useAppStore.getState().structuresData) fetchOverlay(clamped);
  }, [doFetch, triggerDraw, processQueue, onSliceChange, fetchOverlay]);

  // When structuresData loads/unloads or visibility changes, clear cache and re-fetch
  useEffect(() => {
    if (isThumbnail) return;
    overlayCacheRef.current.clear();
    overlayInFlightRef.current.clear();
    overlayRef.current = null;
    if (structuresData) {
      fetchOverlay(sliceIdxRef.current);
    } else {
      triggerDraw();
    }
  }, [structuresData, structureVisible, fetchOverlay, isThumbnail, triggerDraw]);

  // Initial load
  useEffect(() => {
    cacheRef.current.clear();
    inFlightRef.current.clear();
    queueRef.current = [];
    activeCountRef.current = 0;
    currentEntryRef.current = null;
    sliceIdxRef.current = 0;
    sliceCountRef.current = 1;
    setStatus('loading');

    // Ask backend to pre-render all slices for this axis in background
    const token = localStorage.getItem('token');
    fetch(`/api/reconstructions/${reconId}/prerender-slices?axis=${axis}`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }).catch(() => {});

    doFetch(-1, (entry, actual) => {
      sliceIdxRef.current = actual;
      currentEntryRef.current = entry;
      setSliceLabel({ idx: actual, count: sliceCountRef.current });
      setStatus('ok');
      triggerDraw();
      onSliceChange?.(actual, sliceCountRef.current);
      for (let i = 1; i <= PREFETCH_AHEAD; i++) {
        queueRef.current.push(actual + i);
        queueRef.current.push(actual - i);
      }
      processQueue();
    });
  }, [reconId, axis]); // eslint-disable-line

  const handleWheel = useCallback((e) => {
    if (isThumbnail) return;
    e.preventDefault();
    const dir = e.deltaY > 0 ? 1 : -1;
    scrollDirRef.current = dir;
    goToSlice(sliceIdxRef.current + dir);
  }, [isThumbnail, goToSlice]);

  const handleScrollbar = useCallback((e) => {
    if (isThumbnail) return;
    const idx = parseInt(e.target.value);
    scrollDirRef.current = idx > sliceIdxRef.current ? 1 : -1;
    goToSlice(idx);
  }, [isThumbnail, goToSlice]);

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative', background: '#000', display: 'flex' }}>
      <canvas
        ref={canvasRef}
        style={{ flex: 1, height: '100%', display: 'block', cursor: isThumbnail ? 'pointer' : 'default', minWidth: 0 }}
        onWheel={handleWheel}
      />
      {!isThumbnail && (
        <div style={{ width: 18, height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#0d1117', borderLeft: '1px solid #1e2733' }}>
          <input
            type="range"
            min={0}
            max={Math.max(0, sliceLabel.count - 1)}
            value={sliceLabel.idx}
            onChange={handleScrollbar}
            style={{
              writingMode: 'vertical-lr',
              direction: 'rtl',
              width: 14,
              height: 'calc(100% - 24px)',
              cursor: 'pointer',
              accentColor: info.color,
              background: 'transparent',
            }}
          />
        </div>
      )}
      {status === 'loading' && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#4a5568', fontSize: 11, fontFamily: 'IBM Plex Mono, monospace' }}>
          {isThumbnail ? info.label.slice(0,3) : `Loading ${info.label}…`}
        </div>
      )}
      {status === 'error' && !isThumbnail && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: '#ff5252', fontSize: 11, fontFamily: 'IBM Plex Mono, monospace', padding: 20, textAlign: 'center' }}>
          <div style={{ marginBottom: 6 }}>⚠ Could not load {info.label} slice</div>
          <div style={{ color: '#4a5568', fontSize: 10 }}>{errorMsg}</div>
        </div>
      )}
      {status === 'ok' && !isThumbnail && (
        <div style={{ position: 'absolute', bottom: 8, right: 8, fontSize: 10, color: '#2a3340', fontFamily: 'IBM Plex Mono, monospace' }}>
          scroll · {sliceLabel.idx + 1} / {sliceLabel.count}
        </div>
      )}
      {!isThumbnail && locator && status === 'ok' && (
        <LocatorOverlay
          reconId={reconId}
          refAxis={locator.refAxis}
          lineType={locator.lineType}
          fraction={locator.fraction}
        />
      )}
    </div>
  );
}
