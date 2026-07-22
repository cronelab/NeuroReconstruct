import * as THREE from 'three';

// ── Anatomical labeling helpers ───────────────────────────────────────────────
// Shared by the 3D viewer's electrode-centric hover and the editor's
// "auto-label from anatomy" action, so both derive a contact's region the same way.
//
// All structure meshes and contact coordinates live in the same Three.js
// mesh-centred space (structuresData[key].vertices vs contact.x_mm/y_mm/z_mm).

const _rc = new THREE.Raycaster();
const _box = new THREE.Box3();

// Point-in-mesh test: cast from far outside toward the point and count crossings
// before reaching it. Casting from outside (not inside) avoids back-face culling
// issues; a majority vote across 3 axes tolerates degenerate mesh edges/vertices.
export function isInsideMesh(point, mesh) {
  // Quick bbox reject
  _box.setFromObject(mesh);
  if (!_box.containsPoint(point)) return false;

  const OFFSET = 500;
  const axes = [
    [new THREE.Vector3(point.x + OFFSET, point.y, point.z), new THREE.Vector3(-1, 0, 0)],
    [new THREE.Vector3(point.x, point.y + OFFSET, point.z), new THREE.Vector3( 0, -1, 0)],
    [new THREE.Vector3(point.x, point.y, point.z + OFFSET), new THREE.Vector3( 0, 0, -1)],
  ];
  let votes = 0;
  for (const [origin, dir] of axes) {
    _rc.set(origin, dir);
    const hits = _rc.intersectObject(mesh, false).filter(h => h.distance < OFFSET - 0.01);
    if (hits.length % 2 === 1) votes++;
  }
  return votes >= 2;
}

// A contact is labeled with the nearest structure only if it is within this many
// mm of that structure's surface; beyond it the contact is left unlabelled.
export const MAX_LABEL_DIST_MM = 2;

// Closest structure to a point (nearest mesh vertex ≈ nearest surface), across
// BOTH cortical gyri and subcortical nuclei.
export function nearestStructure(point, structuresData) {
  if (!structuresData) return null;
  const px = point.x, py = point.y, pz = point.z;
  let best = null;
  let bestD2 = Infinity;
  for (const key in structuresData) {
    const s = structuresData[key];
    if (!s || !s.vertices) continue;
    const v = s.vertices;
    let localMin = Infinity;
    for (let i = 0; i < v.length; i += 3) {
      const dx = v[i] - px, dy = v[i + 1] - py, dz = v[i + 2] - pz;
      const d2 = dx * dx + dy * dy + dz * dz;
      if (d2 < localMin) localMin = d2;
    }
    if (localMin < bestD2) {
      bestD2 = localMin;
      best = { key, label: s.label || key, color: s.color || '#e8edf2' };
    }
  }
  return best ? { ...best, dist: Math.sqrt(bestD2) } : null;
}

// Build (non-rendered) Three.js meshes for every structure with geometry, keyed
// by structure key, so isInsideMesh can raycast them. Dispose geometries when done.
//
// The material MUST be double-sided: isInsideMesh counts ray/surface crossings to
// decide inside vs outside, and Three.js's raycaster culls back faces for the
// default (FrontSide) material — which silently breaks the crossing count. The
// rendered structure meshes in the viewer use DoubleSide for the same reason.
const _insideMat = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide });
export function buildStructureMeshes(structuresData) {
  const meshes = new Map();
  if (!structuresData) return meshes;
  for (const key in structuresData) {
    const s = structuresData[key];
    if (!s || !s.vertices || !s.faces) continue;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(s.vertices), 3));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(s.faces), 1));
    g.computeVertexNormals();
    g.computeBoundingSphere();
    g.computeBoundingBox();
    const m = new THREE.Mesh(g, _insideMat);
    m.updateMatrixWorld(true);
    meshes.set(key, m);
  }
  return meshes;
}

// Anatomical centre of the structures — used to tell an electrode's deep end
// (nearer the centre) from its superficial/insertion end (nearer the surface).
export function structuresCentroid(structuresData) {
  let sx = 0, sy = 0, sz = 0, n = 0;
  for (const key in structuresData || {}) {
    const v = structuresData[key]?.vertices;
    if (!v) continue;
    for (let i = 0; i < v.length; i += 30) { // subsample every 10th vertex
      sx += v[i]; sy += v[i + 1]; sz += v[i + 2]; n++;
    }
  }
  return n ? new THREE.Vector3(sx / n, sy / n, sz / n) : new THREE.Vector3(0, 0, 0);
}

// Which structure a contact sits in: an enclosing structure if any (a subcortical
// nucleus is preferred when several overlap), otherwise the nearest cortical or
// subcortical structure but only within MAX_LABEL_DIST_MM. Returns null when the
// contact is inside nothing and more than MAX_LABEL_DIST_MM from every structure
// (i.e. unlabelled).
export function structureAtPoint(point, structuresData, meshes) {
  const insideKeys = [];
  meshes.forEach((mesh, key) => {
    if (mesh && isInsideMesh(point, mesh)) insideKeys.push(key);
  });
  if (insideKeys.length) {
    const pick = insideKeys.find(k => structuresData[k]?.group === 'subcortical') || insideKeys[0];
    const s = structuresData[pick];
    return { key: pick, label: s?.label || pick, color: s?.color || '#e8edf2', inside: true, dist: 0 };
  }
  const near = nearestStructure(point, structuresData);
  return near && near.dist <= MAX_LABEL_DIST_MM ? { ...near, inside: false } : null;
}

const _pt = new THREE.Vector3();

// "insertion region-target region" label for a shaft:
//   insertion = region of the most SUPERFICIAL contact that has a label
//   target    = region of the most DEEP contact that has a label
// Every placed contact is resolved to a region (structureAtPoint); contacts that
// are unlabelled (inside nothing and >MAX_LABEL_DIST_MM from any structure) are
// skipped, so the two ends are the shallowest/deepest anatomically-labeled
// contacts rather than the physical tip/entry (which often sit in white matter).
// Depth is measured by distance to the anatomical centre (nearer = deeper).
// Returns null when no placed contact resolves to a label.
export function computeShaftAnatomyLabel(shaft, structuresData, meshes, centroid) {
  const contacts = (shaft.contacts || [])
    .filter(c => c.x_mm != null && c.y_mm != null && c.z_mm != null);
  if (contacts.length === 0) return null;

  const d2 = (c) => {
    const dx = c.x_mm - centroid.x, dy = c.y_mm - centroid.y, dz = c.z_mm - centroid.z;
    return dx * dx + dy * dy + dz * dz;
  };

  let deep = null, superficial = null; // among labeled contacts only
  for (const c of contacts) {
    const region = structureAtPoint(_pt.set(c.x_mm, c.y_mm, c.z_mm), structuresData, meshes);
    if (!region) continue; // unlabelled contact — skip
    const dd = d2(c);
    if (!deep || dd < deep.dd) deep = { label: region.label, dd };        // nearest centre = deepest
    if (!superficial || dd > superficial.dd) superficial = { label: region.label, dd }; // farthest = most superficial
  }
  if (!deep) return null; // no contact resolved to a label

  return `${superficial.label}-${deep.label}`;
}
