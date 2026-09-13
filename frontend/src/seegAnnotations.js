/**
 * Reviewer annotations on a continuous sEEG recording.
 *
 * Shared by the 3D brain view (which shows the marker currently in effect) and the
 * trace panel (which shows the ruler and the jump list), so both place a marker on
 * exactly the same frame.
 *
 * Categories come from `neuroseegread.clinical.edf.classify_annotation`.
 */

export const ANN_COLOR = {
  seizure_onset: '#ff4d5e',
  seizure_end: '#ff9f43',
  eeg: '#a78bfa',
  clinical: '#ffd166',
  system: '#4a5563',
  note: '#7a8a99',
};

export const annColor = (cat) => ANN_COLOR[cat] || ANN_COLOR.note;

// Categories emphasised on the trace canvas; the rest stay faint but reachable.
export const ANN_EMPHASIS = new Set(['seizure_onset', 'seizure_end']);

/** Frame whose timestamp is closest to `t`. `times` is ascending but may be unevenly
 *  decimated, so this scans rather than interpolating. */
export function nearestIndex(times, t) {
  if (!times || !times.length) return -1;
  let best = 0, bd = Infinity;
  for (let i = 0; i < times.length; i++) {
    const d = Math.abs(times[i] - t);
    if (d < bd) { bd = d; best = i; }
  }
  return best;
}

/**
 * Place each annotation on a frame.
 *
 * Continuous recordings only: a trial window is in milliseconds relative to an event,
 * so a recording-clock onset has no meaning there and this returns nothing.
 */
export function buildMarks(times, annotations, timeUnit) {
  if (timeUnit !== 's' || !Array.isArray(annotations) || !times || !times.length) return [];
  const lo = times[0], hi = times[times.length - 1];
  return annotations
    .filter((a) => typeof a.onset === 'number' && a.onset >= lo - 1 && a.onset <= hi + 1)
    .map((a) => ({ ...a, idx: nearestIndex(times, a.onset), color: annColor(a.category) }));
}

/**
 * The marker(s) in effect at `timeIndex`: the most recent at or before the cursor.
 *
 * An annotation is an instant, not a span, so without this it would be visible for the
 * single frame it lands on. Holding the last one until the next takes over turns the
 * track into a readable state line. Several markers can share a timestamp (69 s carries
 * both "Z SZ ONSET" and "tt start?"), so every marker at that instant is returned.
 */
export function activeMarksAt(marks, timeIndex) {
  let at = -1;
  for (const m of marks) if (m.idx <= timeIndex && m.idx > at) at = m.idx;
  return at < 0 ? [] : marks.filter((m) => m.idx === at);
}
