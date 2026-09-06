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
