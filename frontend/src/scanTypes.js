// How a secondary scan's type is described.
//
// The user types it freely — the point of secondary scans is to bring in
// whatever sequence shows the structure best, and a fixed list cannot express
// an SWI or a T2 SPACE. Whatever is typed becomes the scan's label in the
// viewer's SCAN bar.
//
// The backend also stores a coarse modality from a small fixed set, so classify
// the typed text rather than asking for the same thing twice. Shared by both
// places a scan can be added: the New Reconstruction form and the SCAN bar.

export const SCAN_TYPE_PLACEHOLDER = 'T2 FLAIR';

// Matches SecondaryScan.modality's accepted values; anything else is "other",
// which is what that value is for.
export function inferModality(label) {
  const t = (label || '').toLowerCase();
  // Diffusion maps first: "DTI FA" would otherwise fall through to 'other',
  // and a derived map is registered differently from an anatomical scan.
  // Colour FA before plain FA, which "color FA" also matches. The backend
  // re-checks this against the file itself, so it is only a first guess.
  const isFa = /\bfa\b/.test(t) || t.includes('anisotropy');
  if (/\bdec\b/.test(t) || (isFa && /colou?r|\brgb\b/.test(t))) return 'colorfa';
  if (isFa) return 'fa';
  if (/\badc\b/.test(t) || t.includes('diffusivity')) return 'adc';
  // FLAIR before T2: "T2 FLAIR" matches both, and FLAIR is the more specific.
  if (t.includes('flair')) return 'flair';
  if (t.includes('t2')) return 't2';
  if (/\bpd\b/.test(t)) return 'pd';
  return 'other';
}

// A scan still needs *a* name if the field was left empty.
export function scanLabelOrDefault(label) {
  return (label || '').trim() || 'Secondary';
}

// Whether this kind of layer registers via a separate reference volume. A
// derived diffusion map has little anatomy for mutual information to lock onto,
// so the b=0 of the same diffusion run is registered instead and its transform
// carried across -- exact, because the two share a voxel grid.
export function needsReference(modality) {
  return modality === 'fa' || modality === 'colorfa' || modality === 'adc';
}

// Whether a layer's pixels are colour that carries meaning -- a direction-
// encoded FA map, where hue IS the fibre direction. Structures are drawn over
// such a layer as white outlines instead of coloured fills, so the parcellation
// never reads as a direction or hides one.
export function isColorLayer(modality) {
  return modality === 'colorfa';
}
