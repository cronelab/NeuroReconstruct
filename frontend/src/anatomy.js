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

// Radius of the plurality-vote search for the nearest-structure fallback. Mirrors
// DEFAULT_SEARCH_RADIUS_MM in the backend's contact_labeling.py: structure-mesh
// vertices beyond this distance are not evidence about where a contact sits, so a
// contact with no structure vertex inside the radius is left unlabelled.
export const MAX_LABEL_DIST_MM = 2;

// Structure a point most likely sits in, by a plurality vote among structure-mesh
// vertices within MAX_LABEL_DIST_MM — the mesh-space analog of the voxel plurality
// vote in the backend's contact_labeling.py. Each vertex within the radius casts
// one vote for its structure; the structure holding the most votes wins, rather
// than the single nearest vertex, so a lone stray vertex of a neighbouring parcel
// does not decide the label. Ties break toward the structure with a vertex closest
// to the point (matching the backend's nearest-voxel tie-break). Considers BOTH
// cortical gyri and subcortical nuclei.
//
// `dist` is the distance to the winning structure's nearest vertex (not the overall
// nearest vertex); `voteShare` is the winner's fraction of the votes, so a decisive
// label can be told apart from a marginal one. Returns null when no structure vertex
// lies within MAX_LABEL_DIST_MM.
//
// Note: mesh vertices are surface samples, not the uniform-volume voxels the backend
// votes over, so a vote here is a surface-sampling share rather than a strict volume
// share — the frontend has no label volume, so vertices are the available candidates.
export function nearestStructure(point, structuresData) {
  if (!structuresData) return null;
  const px = point.x, py = point.y, pz = point.z;
  const r2 = MAX_LABEL_DIST_MM * MAX_LABEL_DIST_MM;

  // Per-structure vote count and nearest squared distance (for the tie-break and
  // the reported distance), plus the total number of voters for vote_share.
  let winner = null;
  let totalVotes = 0;
  for (const key in structuresData) {
    const s = structuresData[key];
    if (!s || !s.vertices) continue;
    const v = s.vertices;
    let votes = 0;
    let minD2 = Infinity;
    for (let i = 0; i < v.length; i += 3) {
      const dx = v[i] - px, dy = v[i + 1] - py, dz = v[i + 2] - pz;
      const d2 = dx * dx + dy * dy + dz * dz;
      if (d2 <= r2) {
        votes++;
        if (d2 < minD2) minD2 = d2;
      }
    }
    if (votes === 0) continue;
    totalVotes += votes;
    // Plurality: more votes wins outright; equal votes break toward the closer
    // structure (smaller nearest-vertex distance).
    if (!winner || votes > winner.votes || (votes === winner.votes && minD2 < winner.minD2)) {
      winner = { key, votes, minD2 };
    }
  }
  if (!winner) return null;

  const s = structuresData[winner.key];
  return {
    key: winner.key,
    label: s.label || winner.key,
    color: s.color || '#e8edf2',
    dist: Math.sqrt(winner.minD2),
    voteShare: winner.votes / totalVotes,
  };
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
// nucleus is preferred when several overlap), otherwise the plurality-vote winner
// among structure vertices within MAX_LABEL_DIST_MM. Returns null when the contact
// is inside nothing and no structure vertex lies within MAX_LABEL_DIST_MM (i.e.
// unlabelled) — the radius gate is enforced inside nearestStructure.
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
  return near ? { ...near, inside: false } : null;
}

const _pt = new THREE.Vector3();

// Principal axis (unit direction) of a set of points — the shaft's trajectory,
// as the dominant eigenvector of the contacts' 3x3 covariance. Found by power
// iteration seeded with the first→last chord, which for a roughly-collinear
// contact string already points along the shaft, so it converges in a few steps.
// Returns a zero direction for a single/degenerate point (caller handles it).
function principalAxis(points) {
  const n = points.length;
  let mx = 0, my = 0, mz = 0;
  for (const p of points) { mx += p.x; my += p.y; mz += p.z; }
  mx /= n; my /= n; mz /= n;

  // Symmetric covariance accumulators.
  let cxx = 0, cyy = 0, czz = 0, cxy = 0, cxz = 0, cyz = 0;
  for (const p of points) {
    const dx = p.x - mx, dy = p.y - my, dz = p.z - mz;
    cxx += dx * dx; cyy += dy * dy; czz += dz * dz;
    cxy += dx * dy; cxz += dx * dz; cyz += dy * dz;
  }

  // Seed with the chord between the first and last contact.
  let vx = points[n - 1].x - points[0].x;
  let vy = points[n - 1].y - points[0].y;
  let vz = points[n - 1].z - points[0].z;
  let len = Math.hypot(vx, vy, vz);
  if (len < 1e-9) return { origin: new THREE.Vector3(mx, my, mz), dir: new THREE.Vector3() };
  vx /= len; vy /= len; vz /= len;

  for (let it = 0; it < 16; it++) {
    const nx = cxx * vx + cxy * vy + cxz * vz;
    const ny = cxy * vx + cyy * vy + cyz * vz;
    const nz = cxz * vx + cyz * vy + czz * vz;
    len = Math.hypot(nx, ny, nz);
    if (len < 1e-9) break; // covariance annihilates v (all points coincident)
    vx = nx / len; vy = ny / len; vz = nz / len;
  }
  return { origin: new THREE.Vector3(mx, my, mz), dir: new THREE.Vector3(vx, vy, vz) };
}

// "insertion region-target region" label for a shaft:
//   insertion = region of the most SUPERFICIAL contact that has a label
//   target    = region of the most DEEP (tip) contact that has a label
// The two ends are found from the shaft's own trajectory: contacts are projected
// onto their principal axis, and the two extremes of that projection are the
// physical ends — not the lowest/highest contact number, and not the contacts
// nearest/farthest from the anatomical centre (which can pick a mid-shaft contact
// when the shaft does not point at the centre). The anatomical centre is used only
// to orient the axis: the tip is the trajectory end that sits deeper in the brain,
// i.e. the extreme nearer the centre.
//
// Every placed contact is resolved to a region (structureAtPoint); unlabelled
// contacts (inside nothing and >MAX_LABEL_DIST_MM from any structure — typically
// white matter) are skipped, so a tip sitting in white matter falls back to the
// deepest labeled contact along the trajectory rather than leaving the shaft
// unlabelled. Returns null when no placed contact resolves to a label.
export function computeShaftAnatomyLabel(shaft, structuresData, meshes, centroid) {
  const contacts = (shaft.contacts || [])
    .filter(c => c.x_mm != null && c.y_mm != null && c.z_mm != null);
  if (contacts.length === 0) return null;

  const pts = contacts.map(c => new THREE.Vector3(c.x_mm, c.y_mm, c.z_mm));
  const axis = principalAxis(pts);
  const proj = pts.map(p => _pt.copy(p).sub(axis.origin).dot(axis.dir));

  // Trajectory extremes, then orient so larger `depth` = deeper (toward the tip).
  // The tip end is whichever projection extreme sits nearer the anatomical centre.
  let iLo = 0, iHi = 0;
  for (let i = 1; i < proj.length; i++) {
    if (proj[i] < proj[iLo]) iLo = i;
    if (proj[i] > proj[iHi]) iHi = i;
  }
  const hiIsDeep = pts[iHi].distanceToSquared(centroid) < pts[iLo].distanceToSquared(centroid);
  const depth = (i) => (hiIsDeep ? proj[i] : -proj[i]);

  let deep = null, superficial = null; // among labeled contacts only
  for (let i = 0; i < contacts.length; i++) {
    const region = structureAtPoint(pts[i], structuresData, meshes);
    if (!region) continue; // unlabelled contact — skip
    const dep = depth(i);
    if (!deep || dep > deep.dep) deep = { label: region.label, dep };            // deepest along trajectory = tip
    if (!superficial || dep < superficial.dep) superficial = { label: region.label, dep }; // shallowest = insertion
  }
  if (!deep) return null; // no contact resolved to a label

  return `${superficial.label}-${deep.label}`;
}
