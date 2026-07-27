// sEEG shaft colors are taken from the reconstruction's own electrode shafts
// (shaft.color), so a shaft reads as the SAME color here as in the reconstruction
// and electrode-editor viewers. Channels are joined to shafts by normalized name.

const FALLBACK = '#8aa0b4';

function normShaft(s) {
  return String(s || '').replace(/[\s'\-_]/g, '').toLowerCase();
}

// Build { normalizedShaftName: color } from reconstruction.electrode_shafts.
export function buildShaftColorMap(shafts) {
  const map = {};
  (shafts || []).forEach((sh) => {
    if (sh?.name) map[normShaft(sh.name)] = sh.color || FALLBACK;
  });
  return map;
}

// Color for a channel's shaft group, looked up in the map built above.
export function shaftColorOf(group, colorMap) {
  return (colorMap && colorMap[normShaft(group)]) || FALLBACK;
}
