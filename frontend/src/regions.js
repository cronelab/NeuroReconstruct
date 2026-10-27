// Region lists for the per-region on/off toggles: the cortical surface's
// parcel colours and the 2D slice atlas overlay. Grouped Group -> Side -> Region
// like the structure tree, with the catalog's lobe groups
// (backend/services/structure_extractor.py).

export const GROUP_ORDER = ['subcortical', 'frontal', 'temporal', 'parietal', 'occipital', 'cingulate', 'cortex'];

// Desikan and DKT parcels carry FreeSurfer ids 1000 + i (left) and 2000 + i
// (right), i being the aparc index (APARC_NAMES in freesurfer_import.py). The
// lobe per index is the catalog's; bankssts and the poles, which the catalog's
// DKT subset lacks, go where Desikan-Killiany puts them. Destrieux ids sit
// outside this range and have no lobe here, so they fall under 'cortex'.
const APARC_GROUP = [
  null, 'temporal', 'cingulate', 'frontal', null, 'occipital', 'temporal', 'temporal',
  'parietal', 'temporal', 'cingulate', 'occipital', 'frontal', 'occipital', 'frontal',
  'temporal', 'temporal', 'frontal', 'frontal', 'frontal', 'frontal', 'occipital',
  'parietal', 'cingulate', 'frontal', 'parietal', 'cingulate', 'frontal', 'frontal',
  'parietal', 'temporal', 'parietal', 'frontal', 'temporal', 'temporal', 'temporal',
];

// The aparc names, same order (APARC_NAMES in freesurfer_import.py). The
// structure catalog keys its cortical regions '<name>_l' / '<name>_r', so these
// turn a DKT or Desikan parcel id into the catalog key the 2D overlay and the
// Structures list use for the same region.
const APARC_NAMES = [
  'unknown', 'bankssts', 'caudalanteriorcingulate', 'caudalmiddlefrontal',
  'corpuscallosum', 'cuneus', 'entorhinal', 'fusiform', 'inferiorparietal',
  'inferiortemporal', 'isthmuscingulate', 'lateraloccipital', 'lateralorbitofrontal',
  'lingual', 'medialorbitofrontal', 'middletemporal', 'parahippocampal', 'paracentral',
  'parsopercularis', 'parsorbitalis', 'parstriangularis', 'pericalcarine', 'postcentral',
  'posteriorcingulate', 'precentral', 'precuneus', 'rostralanteriorcingulate',
  'rostralmiddlefrontal', 'superiorfrontal', 'superiorparietal', 'superiortemporal',
  'supramarginal', 'frontalpole', 'temporalpole', 'transversetemporal', 'insula',
];

// Catalog key for a cortical-surface parcel, or null when it has none
// (Destrieux parcels, and the few Desikan regions the catalog lacks). Only
// parcels with a key share their on/off with the Structures list and the 2D
// overlay; the rest switch the surface colour alone.
export function structureKeyOf(parcelId) {
  if (!(parcelId >= 1000 && parcelId < 3000)) return null;
  const name = APARC_NAMES[parcelId % 1000];
  if (!name || name === 'unknown' || name === 'corpuscallosum') return null;
  return `${name}_${parcelId < 2000 ? 'l' : 'r'}`;
}

const sideOf = (label, id) => {
  if (/^left\b/i.test(label)) return 'l';
  if (/^right\b/i.test(label)) return 'r';
  if ((id >= 1000 && id < 2000) || (id >= 11000 && id < 12000)) return 'l';
  if ((id >= 2000 && id < 3000) || (id >= 12000 && id < 13000)) return 'r';
  return null;
};

// The side column already says Left/Right; Destrieux names read better without
// their underscores.
const bare = (label) => label.replace(/^(Left|Right)[\s_-]+/i, '').replace(/_/g, ' ');

const byLabel = (a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: 'base' });

// The parcellation a cortical-surface colour actually uses: the chosen atlas
// when this surface has it, else DKT (every surface has that one). Must agree
// with CorticalSurface's atlasOf, or the list would toggle parcels nobody sees.
export function effectiveAtlas(corticalData, atlas) {
  return corticalData?.atlases?.[atlas] ? atlas : 'dkt';
}

// Keys are '<atlas>:<parcel id>', so switching atlas never carries one atlas's
// choices onto another's unrelated ids. `structureKey` is the parcel's catalog
// key where it has one (see structureKeyOf).
export function corticalRegionEntries(corticalData, atlasKey) {
  const atlas = corticalData?.atlases?.[atlasKey];
  if (!atlas) return [];
  return Object.entries(atlas.labels || {}).map(([k, label]) => {
    const id = Number(k);
    const lobe = id >= 1000 && id < 3000 ? APARC_GROUP[id % 1000] : null;
    return {
      key: `${atlasKey}:${id}`,
      label: bare(label),
      color: atlas.colors?.[k] || '#8a96a3',
      group: lobe || 'cortex',
      side: sideOf(label, id),
      structureKey: structureKeyOf(id),
    };
  }).sort(byLabel);
}

// Whether a cortical parcel is coloured: its catalog region's on/off when it
// has one (shared with the Structures list), otherwise its own flag.
export function parcelOn(atlasKey, parcelId, structureVisible, corticalRegionOff) {
  const key = structureKeyOf(parcelId);
  return key ? structureVisible?.[key] !== false : !corticalRegionOff?.[`${atlasKey}:${parcelId}`];
}

// isOn / setOn for a list of cortical parcel entries, writing each parcel's
// choice where parcelOn reads it. The surface colouring, the Region colours
// list and the 2D overlay all go through this, so they cannot disagree.
export function parcelToggles(entries, store) {
  const keyOf = new Map(entries.map(e => [e.key, e.structureKey]));
  return {
    isOn: (k) => {
      const sk = keyOf.get(k);
      return sk ? store.structureVisible?.[sk] !== false : !store.corticalRegionOff?.[k];
    },
    setOn: (keys, on) => {
      const shared = keys.map(k => keyOf.get(k)).filter(Boolean);
      const own = keys.filter(k => !keyOf.get(k));
      if (shared.length) store.setStructureVisibleMany(shared, on);
      if (own.length) store.setCorticalRegionsOff(own, !on);
    },
  };
}

// The parcel ids the slices should paint in the cortical-surface mode: those
// coloured on the surface. Null when the surface is Plain (nothing to paint)
// or not loaded yet.
export function coloredParcelIds(store) {
  const data = store.corticalData;
  if (!data || data === 'unavailable' || store.corticalColorBy !== 'parcellation') return null;
  const atlasKey = effectiveAtlas(data, store.corticalAtlas);
  const labels = data.atlases?.[atlasKey]?.labels || {};
  return {
    atlas: atlasKey,
    ids: Object.keys(labels).map(Number)
      .filter(id => parcelOn(atlasKey, id, store.structureVisible, store.corticalRegionOff)),
  };
}

// The 2D overlay draws structures_cortical.nii.gz by catalog key, so its list
// is the structure catalog -- the same entries the Structures tree shows.
export function structureRegionEntries(structuresData) {
  return Object.entries(structuresData || {})
    .filter(([, s]) => s.vertices)
    .map(([key, s]) => ({
      key,
      label: bare(s.label),
      color: s.color,
      group: s.group || 'cortex',
      side: key.endsWith('_l') ? 'l' : key.endsWith('_r') ? 'r' : null,
    }));
}
