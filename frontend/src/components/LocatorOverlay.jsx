import React from 'react';

// Small corner thumbnail showing current slice position on a reference view.
// Shared by SliceViewer and FusionSliceViewer.
export default function LocatorOverlay({ reconId, refAxis, lineType, fraction }) {
  const canvasRef = React.useRef(null);
  const bitmapRef = React.useRef(null);
  const pxWmmRef = React.useRef(1);   // physical mm-per-pixel (display width) — anisotropic voxels
  const pxHmmRef = React.useRef(1);   // physical mm-per-pixel (display height)
  const AXIS_COLORS = { sagittal: '#ff6b6b', coronal: '#4fc3f7', axial: '#81c784' };
  const color = AXIS_COLORS[refAxis] || '#fff';

  React.useEffect(() => {
    if (!reconId) return;
    const token = localStorage.getItem('token');
    fetch(`/api/reconstructions/${reconId}/mri-slice?axis=${refAxis}&slice_idx=-1`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} }
    ).then(async res => {
      if (!res.ok) return;
      pxWmmRef.current = parseFloat(res.headers.get('X-Display-Px-Width-Mm') || '1');
      pxHmmRef.current = parseFloat(res.headers.get('X-Display-Px-Height-Mm') || '1');
      const blob = await res.blob();
      bitmapRef.current = await createImageBitmap(blob);
      drawLocator();
    }).catch(() => {});
  }, [reconId, refAxis]);

  const drawLocator = React.useCallback(() => {
    const canvas = canvasRef.current;
    const bm = bitmapRef.current;
    if (!canvas || !bm) return;
    const W = canvas.width, H = canvas.height;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, W, H);

    // Draw reference slice letterboxed, aspect-corrected for anisotropic voxels
    // (scale by physical mm extent, not pixel count)
    const physW = bm.width * pxWmmRef.current;
    const physH = bm.height * pxHmmRef.current;
    const scale = Math.min(W / physW, H / physH);
    const dw = physW * scale;
    const dh = physH * scale;
    const dx = (W - dw) / 2;
    const dy = (H - dh) / 2;
    ctx.globalAlpha = 0.7;
    ctx.drawImage(bm, dx, dy, dw, dh);
    ctx.globalAlpha = 1.0;

    // Draw position line clipped to image bounds
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.shadowColor = color;
    ctx.shadowBlur = 3;
    if (lineType === 'horizontal') {
      const y = Math.round(dy + fraction * dh);
      ctx.beginPath(); ctx.moveTo(dx, y); ctx.lineTo(dx + dw, y); ctx.stroke();
    } else {
      const x = Math.round(dx + fraction * dw);
      ctx.beginPath(); ctx.moveTo(x, dy); ctx.lineTo(x, dy + dh); ctx.stroke();
    }
    ctx.shadowBlur = 0;
  }, [lineType, fraction, color]);

  // Redraw when fraction changes
  React.useEffect(() => { drawLocator(); }, [drawLocator]);

  return (
    <div style={{
      position: 'absolute', bottom: 12, left: 12,
      width: 260, height: 208,
      border: `1px solid ${color}66`,
      borderRadius: 3,
      overflow: 'hidden',
      background: '#000',
      boxShadow: `0 0 6px ${color}33`,
    }}>
      <canvas ref={canvasRef} width={260} height={208} style={{ display: 'block', width: '100%', height: '100%' }} />
    </div>
  );
}
