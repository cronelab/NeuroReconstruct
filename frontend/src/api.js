import axios from 'axios';

const api = axios.create({ baseURL: '/api' });

// Attach JWT token from localStorage if present
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// ── Auth ──────────────────────────────────────────────────────────────────────
export const login = (username, password) => {
  const form = new URLSearchParams();
  form.append('username', username);
  form.append('password', password);
  return api.post('/auth/login', form, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
  });
};

export const getMe = () => api.get('/auth/me');
export const registerUser = (data) => api.post('/auth/register', data);

// ── Reconstructions ───────────────────────────────────────────────────────────
export const listReconstructions = () => api.get('/reconstructions');

export const createReconstruction = (formData) =>
  api.post('/reconstructions', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
  });

export const getReconstruction = (id, token) =>
  api.get(`/reconstructions/${id}${token ? `?token=${token}` : ''}`);

export const getMesh = (id, token) =>
  api.get(`/reconstructions/${id}/mesh${token ? `?token=${token}` : ''}`);

export const getShareLink = (id) => api.get(`/reconstructions/${id}/share-link`);

// ── Trash ─────────────────────────────────────────────────────────────────────
export const softDeleteReconstruction = (id) =>
  api.patch(`/reconstructions/${id}/soft-delete`);

export const restoreReconstruction = (id) =>
  api.patch(`/reconstructions/${id}/restore`);

export const listDeletedReconstructions = () =>
  api.get('/reconstructions/deleted');

export const permanentlyDeleteReconstruction = (id) =>
  api.delete(`/reconstructions/${id}/permanent`);

// ── Electrodes ────────────────────────────────────────────────────────────────
export const createShaft = (reconId, data) =>
  api.post(`/reconstructions/${reconId}/shafts`, data);

export const addContact = (shaftId, data) =>
  api.post(`/shafts/${shaftId}/contacts`, data);

export const autofillShaft = (shaftId, data) =>
  api.post(`/shafts/${shaftId}/autofill`, data);

export const updateShaft = (shaftId, data) =>
  api.patch(`/shafts/${shaftId}`, data);

export const deleteContact = (shaftId, contactNumber) =>
  api.delete(`/shafts/${shaftId}/contacts/${contactNumber}`);

export const initContacts = (shaftId) =>
  api.post(`/shafts/${shaftId}/init-contacts`);

export const snapToBlob = (reconId, worldPos, threshold) =>
  api.post(`/reconstructions/${reconId}/snap-to-blob`, { world_pos: worldPos, threshold });

export const getCtHistogram = (reconId, token) =>
  api.get(`/reconstructions/${reconId}/ct-histogram${token ? `?token=${token}` : ""}`);

export const getStructures = (id, token) =>
  api.get(`/reconstructions/${id}/structures${token ? `?token=${token}` : ""}`);

// Pial-like cortical surface for the 3D viewer's cortical render mode. Built on
// demand from the T1 plus the DKT label volume, so it 404s until structures have
// been computed for this reconstruction -- callers treat that as "mode not
// available here", not as an error.
export const getCorticalSurface = (id, token) =>
  api.get(`/reconstructions/${id}/cortical-surface${token ? `?token=${token}` : ""}`);

export const confirmRegistration = (id, confirmed) =>
  api.patch(`/reconstructions/${id}/registration-confirm`, { confirmed });

// Re-run CT→MRI registration when the fast result looks poor. 'precise' runs a
// jittered multi-start and enumerates the distinct MI basins (up to 2) for the
// reviewer to pick from; 'deterministic' does a single-threaded reproducible
// re-run. Runs in the background; poll the recon until status returns to "ready".
export const preciseReregister = (id) =>
  api.post(`/reconstructions/${id}/reregister?mode=precise`);

export const reregisterDeterministic = (id) =>
  api.post(`/reconstructions/${id}/reregister?mode=deterministic`);

// Apply a reviewer-chosen candidate basin from a precise re-run.
export const selectRegistrationCandidate = (id, idx) =>
  api.post(`/reconstructions/${id}/registration-candidates/${idx}/select`);

// ── MNI export pipeline ─────────────────────────────────────────────────────────
export const startMniExport = (id) =>
  api.post(`/reconstructions/${id}/export`);

export const downloadMniExport = (id) =>
  api.get(`/reconstructions/${id}/export/download`, { responseType: 'blob' });

export const uploadReconstructionFiles = (reconId, formData) =>
  api.post(`/reconstructions/${reconId}/files`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 600000,
  });

// ── Secondary MRI scans (extra slice-viewer base layers) ────────────────────────
// A secondary (T2, FLAIR, ...) is registered to the primary MRI and stored
// resampled into its grid, so the slice viewer renders it through the ordinary
// /mri-slice route with a scan_id. Nothing else in the pipeline sees it.
export const listSecondaryScans = (reconId, token) =>
  api.get(`/reconstructions/${reconId}/secondary-scans${token ? `?token=${token}` : ''}`);

export const uploadSecondaryScan = (reconId, file, { label, modality } = {}) => {
  const form = new FormData();
  form.append('file', file);
  form.append('label', label || '');
  form.append('modality', modality || 't2');
  return api.post(`/reconstructions/${reconId}/secondary-scans`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 600000,
  });
};

export const deleteSecondaryScan = (reconId, scanId) =>
  api.delete(`/reconstructions/${reconId}/secondary-scans/${scanId}`);

// ── sEEG functional mapping ─────────────────────────────────────────────────────
export const uploadSeeg = (reconId, file) => {
  const form = new FormData();
  form.append('file', file);
  return api.post(`/reconstructions/${reconId}/seeg`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
  });
};

export const listSeeg = (reconId) =>
  api.get(`/reconstructions/${reconId}/seeg`);

// The activation map arrives as base64 int16 (`activity_b64`, see the backend's
// encode_activity_response), because at the band's Nyquist rate it can run to millions
// of values. Decode it into one Float32Array and hand consumers a per-frame view of it,
// so `activity[frame][channel]` reads exactly as the old nested JSON arrays did.
// Int16Array reads the platform byte order; every browser platform is little-endian,
// matching the '<i2' the server writes.
function b64ToInt16(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Int16Array(bytes.buffer, 0, bin.length >> 1);
}

// One flat Float32Array plus a row view per frame, so a matrix of millions of values
// costs one allocation instead of a boxed number per sample.
function scaleToRows(q, scale, nFrames, nCh) {
  const values = new Float32Array(q.length);
  for (let i = 0; i < q.length; i++) values[i] = q[i] * scale;
  const rows = new Array(nFrames);
  for (let k = 0; k < nFrames; k++) rows[k] = values.subarray(k * nCh, (k + 1) * nCh);
  return { values, rows };
}

/**
 * Decode the binary matrices in an activity response: the activation map, and whichever
 * voltage series the server sent (real samples as `raw`, or the per-bin extremes as
 * `raw_min`/`raw_max` when the trace had to be reduced). A response may carry either,
 * both or neither -- the two-phase fetch asks for one at a time.
 */
function decodeSeegActivity(d) {
  if (!d || typeof d !== 'object') return d;
  const out = { ...d };
  if (typeof d.activity_b64 === 'string' && d.activity_shape) {
    const [nFrames, nCh] = d.activity_shape;
    const { values, rows } = scaleToRows(b64ToInt16(d.activity_b64), d.activity_scale,
                                         nFrames, nCh);
    out.activity = rows;
    out.activity_values = values;
    delete out.activity_b64;
  }
  if (Array.isArray(d.trace_keys) && Array.isArray(d.trace_shape)) {
    const [nBins, nCh] = d.trace_shape;
    for (const key of d.trace_keys) {
      const b64 = d[`${key}_b64`];
      if (typeof b64 !== 'string') continue;
      out[key] = scaleToRows(b64ToInt16(b64), d.trace_scale, nBins, nCh).rows;
      delete out[`${key}_b64`];
    }
  }
  return out;
}

export const computeSeegActivity = (reconId, recId,
  { band, mode, align, window_ms, baseline_ms, include_raw, include_activity, filter_raw,
    trace_window_s } = {}) =>
  api.post(`/reconstructions/${reconId}/seeg/${recId}/activity`,
    { band, mode, align, window_ms, baseline_ms, include_raw, include_activity, filter_raw,
      trace_window_s },
    { timeout: 300000 })
    .then((r) => ({ ...r, data: decodeSeegActivity(r.data) }));

export default api;
