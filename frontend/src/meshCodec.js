// Decoding for mesh payloads that ship as base64 typed arrays.
//
// Every other mesh in the app is a flat JSON list of numbers, which is fine at
// the 120k faces of mesh.json but wasteful for the cortical surface: 250k faces
// is 6.5 MB as JSON text against 3.1 MB gzipped base64, and the browser has to
// parse a million-element number array either way. The sEEG viewer already sends
// base64 typed arrays for the same reason (commit 988e7ff).
//
// decodeTyped passes plain arrays straight through, so a caller can hand it
// either shape without branching and a future payload can change encoding
// without touching the call sites.

const CTORS = {
  float32: Float32Array,
  uint32: Uint32Array,
  uint16: Uint16Array,
  uint8: Uint8Array,
};

// base64 -> typed array. Returns null for null/undefined so optional per-vertex
// attributes stay optional.
export function decodeTyped(value, dtype) {
  if (value == null) return null;
  const Ctor = CTORS[dtype];
  if (!Ctor) throw new Error(`decodeTyped: unsupported dtype ${dtype}`);
  if (typeof value !== 'string') return value instanceof Ctor ? value : Ctor.from(value);

  const bin = atob(value);
  // Copy through a byte view: the decoded string is latin-1, one char per byte.
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Ctor(bytes.buffer, bytes.byteOffset, bytes.byteLength / Ctor.BYTES_PER_ELEMENT);
}

/**
 * Decode a cortical-surface payload into typed arrays ready for BufferAttributes.
 *
 * Returns { positions, indices, sulc, parcel, sulcRangeMm, parcelColors, bounds,
 *           center, stats, vertexCount, faceCount, source, atlases }
 * where `sulc` is uint8 over `sulcRangeMm` (it only drives a shading ramp, so
 * 1/255 of the range is far finer than the eye resolves).
 *
 * `atlases` maps atlas key -> { name, parcel, colors, labels }. The fast surface
 * has only 'dkt' (its top-level parcel); a FreeSurfer import adds its
 * `parcellations` (Desikan, Destrieux) alongside.
 */
export function decodeCorticalSurface(payload) {
  if (!payload) return null;
  const parcel = decodeTyped(payload.parcel, 'uint16');
  const parcelColors = payload.parcel_colors || {};
  const parcelLabels = payload.parcel_labels || {};
  const atlases = {
    dkt: { name: payload.atlas_name || 'DKT', parcel, colors: parcelColors, labels: parcelLabels },
  };
  for (const [key, p] of Object.entries(payload.parcellations || {})) {
    atlases[key] = {
      name: p.name || key,
      parcel: decodeTyped(p.parcel, 'uint16'),
      colors: p.colors || {},
      labels: p.labels || {},
    };
  }
  return {
    positions: decodeTyped(payload.vertices, 'float32'),
    indices: decodeTyped(payload.faces, 'uint32'),
    sulc: decodeTyped(payload.sulc, 'uint8'),
    parcel,
    sulcRangeMm: payload.sulc_range_mm || [0, 1],
    parcelColors,
    parcelLabels,
    atlases,
    source: payload.source || 'fast',
    bounds: payload.bounds,
    center: payload.center,
    stats: payload.stats || {},
    vertexCount: payload.vertex_count,
    faceCount: payload.face_count,
  };
}
