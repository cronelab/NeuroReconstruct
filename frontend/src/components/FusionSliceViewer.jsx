import React, { useEffect, useRef, useState, useCallback } from 'react';

/**
 * Registration-QA fusion viewer.
 *
 * Composites the MRI slice (grayscale base) with the CT resampled into the same
 * MRI slice plane (from /fusion-slice, pixel-aligned with /mri-slice). A blend
 * slider fades between MRI-only and CT-only so the reviewer can sweep the fade
 * and check that bone/ventricle/skull edges stay put — a jump means the
 * registration is off there.
 *
 * The CT is tinted amber (not grayscale) so at intermediate blend it reads as a
 * distinct layer over the MRI rather than a muddy average.
 */
const AXES = [
  { id: 'axial',    label: 'Axial' },
  { id: 'coronal',  label: 'Coronal' },
  { id: 'sagittal', label: 'Sagittal' },
];

const CT_TINT = [255, 48, 48]; // red

function FusionCanvas({ reconId, axis }) {
  const canvasRef = useRef(null);
  const mriCacheRef = useRef(new Map());   // idx -> ImageBitmap
  const ctCacheRef = useRef(new Map());    // idx -> ImageBitmap (may be null if unavailable)
  const ctTintedRef = useRef(new Map());   // idx -> tinted ImageBitmap
  const sliceIdxRef = useRef(0);
  const sliceCountRef = useRef(1);
  const pxWmmRef = useRef(1);   // physical mm-per-pixel (display width) — for anisotropic voxels
  const pxHmmRef = useRef(1);   // physical mm-per-pixel (display height)

  const [blend, setBlend] = useState(0.5);   // 0 = MRI only, 1 = CT only
  const [sliceLabel, setSliceLabel] = useState({ idx: 0, count: 1 });
  const [status, setStatus] = useState('loading');
  const [errorMsg, setErrorMsg] = useState('');
  const blendRef = useRef(blend);
  blendRef.current = blend;

  const authHeaders = () => {
    const token = localStorage.getItem('token');
    return token ? { Authorization: `Bearer ${token}` } : {};
  };

  // Build an amber-tinted bitmap from a grayscale CT bitmap (luminance -> alpha over tint)
  const tintCt = useCallback(async (bitmap) => {
    const off = document.createElement('canvas');
    off.width = bitmap.width;
    off.height = bitmap.height;
    const octx = off.getContext('2d');
    octx.drawImage(bitmap, 0, 0);
    const img = octx.getImageData(0, 0, off.width, off.height);
    const d = img.data;
    for (let i = 0; i < d.length; i += 4) {
      const lum = d[i]; // grayscale: r=g=b
      d[i] = CT_TINT[0];
      d[i + 1] = CT_TINT[1];
      d[i + 2] = CT_TINT[2];
      d[i + 3] = lum; // brighter CT = more opaque tint
    }
    octx.putImageData(img, 0, 0);
    return await createImageBitmap(off);
  }, []);

  const doDraw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const W = canvas.offsetWidth, H = canvas.offsetHeight;
    if (W === 0 || H === 0) return;
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, W, H);

    const idx = sliceIdxRef.current;
    const mri = mriCacheRef.current.get(idx);
    const ctTint = ctTintedRef.current.get(idx);
    if (!mri) return;

    // Aspect-correct for anisotropic voxels: scale by physical mm extent, not pixel count
    const physW = mri.width * pxWmmRef.current;
    const physH = mri.height * pxHmmRef.current;
    const scale = Math.min(W / physW, H / physH);
    const dw = physW * scale, dh = physH * scale;
    const dx = (W - dw) / 2, dy = (H - dh) / 2;

    const b = blendRef.current;
    // MRI base fades out as blend -> 1
    ctx.globalAlpha = Math.min(1, 1 - b + 0.15); // keep a little MRI even at full CT for context
    ctx.drawImage(mri, dx, dy, dw, dh);
    // CT tint fades in as blend -> 1
    if (ctTint) {
      ctx.globalAlpha = b;
      ctx.drawImage(ctTint, dx, dy, dw, dh);
    }
    ctx.globalAlpha = 1;

    // Labels
    ctx.fillStyle = '#ffab40';
    ctx.font = 'bold 13px IBM Plex Mono, monospace';
    ctx.fillText('FUSION · ' + axis.toUpperCase(), 12, 24);
    ctx.fillStyle = '#4a5568';
    ctx.font = '11px IBM Plex Mono, monospace';
    ctx.fillText(`${idx + 1} / ${sliceCountRef.current}`, 12, 42);
    if (!ctTint) {
      ctx.fillStyle = '#ff5252';
      ctx.fillText('CT unavailable at this slice', 12, 60);
    }
  }, [axis]);

  const fetchSlice = useCallback(async (idx) => {
    const clamped = Math.max(0, Math.min(sliceCountRef.current - 1, idx));
    sliceIdxRef.current = clamped;

    if (mriCacheRef.current.has(clamped)) {
      setSliceLabel({ idx: clamped, count: sliceCountRef.current });
      setStatus('ok');
      doDraw();
      // still make sure CT is fetched
      if (!ctCacheRef.current.has(clamped)) fetchCt(clamped);
      return;
    }

    try {
      const mriRes = await fetch(
        `/api/reconstructions/${reconId}/mri-slice?axis=${axis}&slice_idx=${clamped}`,
        { headers: authHeaders() }
      );
      if (!mriRes.ok) {
        setErrorMsg(`MRI ${mriRes.status}`);
        setStatus('error');
        return;
      }
      const count = parseInt(mriRes.headers.get('X-Slice-Count') || '1');
      const actual = parseInt(mriRes.headers.get('X-Slice-Index') || '0');
      pxWmmRef.current = parseFloat(mriRes.headers.get('X-Display-Px-Width-Mm') || '1');
      pxHmmRef.current = parseFloat(mriRes.headers.get('X-Display-Px-Height-Mm') || '1');
      sliceCountRef.current = count;
      sliceIdxRef.current = actual;
      const mriBitmap = await createImageBitmap(await mriRes.blob());
      mriCacheRef.current.set(actual, mriBitmap);
      setSliceLabel({ idx: actual, count });
      setStatus('ok');
      doDraw();
      fetchCt(actual);
    } catch (e) {
      setErrorMsg(e.message);
      setStatus('error');
    }
  }, [reconId, axis, doDraw]); // eslint-disable-line

  const fetchCt = useCallback(async (idx) => {
    if (ctCacheRef.current.has(idx)) return;
    ctCacheRef.current.set(idx, null); // mark in-flight/attempted
    try {
      const res = await fetch(
        `/api/reconstructions/${reconId}/fusion-slice?axis=${axis}&slice_idx=${idx}`,
        { headers: authHeaders() }
      );
      if (!res.ok) return;
      const bitmap = await createImageBitmap(await res.blob());
      ctCacheRef.current.set(idx, bitmap);
      const tinted = await tintCt(bitmap);
      ctTintedRef.current.set(idx, tinted);
      if (sliceIdxRef.current === idx) doDraw();
    } catch (_) { /* ignore — CT stays unavailable for this slice */ }
  }, [reconId, axis, tintCt, doDraw]);

  // Initial + axis change
  useEffect(() => {
    mriCacheRef.current.clear();
    ctCacheRef.current.clear();
    ctTintedRef.current.clear();
    sliceIdxRef.current = 0;
    sliceCountRef.current = 1;
    setStatus('loading');
    // center slice
    (async () => {
      try {
        const mriRes = await fetch(
          `/api/reconstructions/${reconId}/mri-slice?axis=${axis}&slice_idx=-1`,
          { headers: authHeaders() }
        );
        if (!mriRes.ok) { setErrorMsg(`MRI ${mriRes.status}`); setStatus('error'); return; }
        const count = parseInt(mriRes.headers.get('X-Slice-Count') || '1');
        const actual = parseInt(mriRes.headers.get('X-Slice-Index') || '0');
        pxWmmRef.current = parseFloat(mriRes.headers.get('X-Display-Px-Width-Mm') || '1');
        pxHmmRef.current = parseFloat(mriRes.headers.get('X-Display-Px-Height-Mm') || '1');
        sliceCountRef.current = count;
        sliceIdxRef.current = actual;
        const mriBitmap = await createImageBitmap(await mriRes.blob());
        mriCacheRef.current.set(actual, mriBitmap);
        setSliceLabel({ idx: actual, count });
        setStatus('ok');
        doDraw();
        fetchCt(actual);
      } catch (e) { setErrorMsg(e.message); setStatus('error'); }
    })();
  }, [reconId, axis]); // eslint-disable-line

  useEffect(() => { doDraw(); }, [blend, doDraw]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const obs = new ResizeObserver(() => doDraw());
    obs.observe(canvas);
    return () => obs.disconnect();
  }, [doDraw]);

  const handleWheel = useCallback((e) => {
    e.preventDefault();
    fetchSlice(sliceIdxRef.current + (e.deltaY > 0 ? 1 : -1));
  }, [fetchSlice]);

  const handleScrollbar = useCallback((e) => {
    fetchSlice(parseInt(e.target.value));
  }, [fetchSlice]);

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative', background: '#000', display: 'flex', flexDirection: 'column' }}>
      <div style={{ flex: 1, position: 'relative', display: 'flex', minHeight: 0 }}>
        <canvas
          ref={canvasRef}
          style={{ flex: 1, height: '100%', display: 'block', minWidth: 0 }}
          onWheel={handleWheel}
        />
        {!isNaN(sliceLabel.count) && sliceLabel.count > 1 && (
          <div style={{ width: 18, height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#0d1117', borderLeft: '1px solid #1e2733' }}>
            <input
              type="range"
              min={0}
              max={Math.max(0, sliceLabel.count - 1)}
              value={sliceLabel.idx}
              onChange={handleScrollbar}
              style={{ writingMode: 'vertical-lr', direction: 'rtl', width: 14, height: 'calc(100% - 24px)', cursor: 'pointer', accentColor: '#ffab40', background: 'transparent' }}
            />
          </div>
        )}
        {status === 'loading' && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#4a5568', fontSize: 11, fontFamily: 'IBM Plex Mono, monospace' }}>Loading fusion…</div>
        )}
        {status === 'error' && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: '#ff5252', fontSize: 11, fontFamily: 'IBM Plex Mono, monospace', padding: 20, textAlign: 'center' }}>
            <div style={{ marginBottom: 6 }}>⚠ Could not load fusion slice</div>
            <div style={{ color: '#4a5568', fontSize: 10 }}>{errorMsg}</div>
          </div>
        )}
      </div>

      {/* Blend slider */}
      <div style={{ flexShrink: 0, background: '#0d1117', borderTop: '1px solid #1e2733', padding: '8px 14px', display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ fontSize: 10, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace', width: 34 }}>MRI</span>
        <input
          type="range" min={0} max={1} step={0.01}
          value={blend}
          onChange={e => setBlend(parseFloat(e.target.value))}
          style={{ flex: 1, accentColor: '#ff5252', cursor: 'pointer' }}
        />
        <span style={{ fontSize: 10, color: '#ff5252', fontFamily: 'IBM Plex Mono, monospace', width: 28, textAlign: 'right' }}>CT</span>
      </div>
    </div>
  );
}

export default function FusionSliceViewer({ reconId }) {
  const [axis, setAxis] = useState('axial');
  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', background: '#000' }}>
      <div style={{ flexShrink: 0, display: 'flex', gap: 4, padding: '6px 8px', background: '#0a0c10', borderBottom: '1px solid #1e2530' }}>
        {AXES.map(a => (
          <button
            key={a.id}
            onClick={() => setAxis(a.id)}
            style={{
              fontSize: 11, fontFamily: 'IBM Plex Mono, monospace', fontWeight: 600,
              padding: '4px 12px', borderRadius: 4, cursor: 'pointer',
              background: axis === a.id ? '#1a1000' : 'transparent',
              color: axis === a.id ? '#ffab40' : '#7a8a99',
              border: `1px solid ${axis === a.id ? '#ffab4055' : '#1e2530'}`,
            }}
          >
            {a.label}
          </button>
        ))}
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        <FusionCanvas reconId={reconId} axis={axis} />
      </div>
    </div>
  );
}
