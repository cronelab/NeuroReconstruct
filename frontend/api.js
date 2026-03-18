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
    timeout: 300000, // 5 min for large uploads
  });

export const getReconstruction = (id, token) =>
  api.get(`/reconstructions/${id}${token ? `?token=${token}` : ''}`);

export const getMesh = (id, token) =>
  api.get(`/reconstructions/${id}/mesh${token ? `?token=${token}` : ''}`);

export const getShareLink = (id) => api.get(`/reconstructions/${id}/share-link`);

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

export default api;
