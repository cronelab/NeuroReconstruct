import { create } from 'zustand';

export const useAppStore = create((set, get) => ({
  // Auth
  user: null,
  token: localStorage.getItem('token'),
  setUser: (user) => set({ user }),
  setToken: (token) => {
    localStorage.setItem('token', token || '');
    set({ token });
  },
  logout: () => {
    localStorage.removeItem('token');
    set({ user: null, token: null });
  },

  // Current reconstruction
  reconstruction: null,
  setReconstruction: (r) => set({ reconstruction: r }),

  // Viewer state
  brainOpacity: 0.6,
  setBrainOpacity: (v) => set({ brainOpacity: v }),

  contactScale: 1.0,
  setContactScale: (v) => set({ contactScale: v }),

  shaftVisibility: {},  // { shaftId: bool }
  setShaftVisible: (id, visible) =>
    set((s) => ({ shaftVisibility: { ...s.shaftVisibility, [id]: visible } })),

  selectedShaftId: null,
  setSelectedShaftId: (id) => set({ selectedShaftId: id }),

  selectedContactId: null,
  setSelectedContactId: (id) => set({ selectedContactId: id }),

  // Which contact NUMBER slot is active for placement (1-based)
  activeContactNumber: null,
  setActiveContactNumber: (n) => set({ activeContactNumber: n }),

  // Editor mode
  isEditorMode: false,
  setEditorMode: (v) => set({ isEditorMode: v }),

  // "Place contacts" mode — when true, clicking the CT places contacts and the
  // structure/electrode hover interactions are suppressed. Placement is disabled
  // in the hover modes. Toggled by the editor's "Place contacts" button.
  placeMode: false,
  setPlaceMode: (v) => set({ placeMode: v }),

  // Mesh data cache
  meshData: null,
  setMeshData: (data) => set({ meshData: data }),
  structuresData: null,          // { key: { label, color, vertices, faces, ... } }
  setStructuresData: (data) => set({ structuresData: data }),
  structureVisible: {},          // { key: bool }
  setStructureVisible: (key, v) => set(s => ({ structureVisible: { ...s.structureVisible, [key]: v } })),
  setStructureVisibleMany: (keys, v) =>
    set(s => ({ structureVisible: { ...s.structureVisible, ...Object.fromEntries(keys.map(k => [k, v])) } })),

  // ── sEEG functional mapping (decoupled from reconstruction/editor state) ──────
  seegRecordings: [],            // [{ id, task, filename, uploaded_at }]
  setSeegRecordings: (r) => set({ seegRecordings: r }),
  seegRecordingId: null,         // currently selected recording id
  setSeegRecordingId: (id) => set({ seegRecordingId: id }),
  // Computed activity payload from the backend:
  //   { channels, times, activity[frame][ch], coords_native, coords_mni,
  //     matched, unmatched_channels, unmatched_contacts, has_mni, mode, band }
  seegActivity: null,
  setSeegActivity: (a) => set({ seegActivity: a, seegTimeIndex: 0 }),
  seegBand: 'high_gamma',
  setSeegBand: (b) => set({ seegBand: b }),
  // Trial-averaged peri-stimulus window (ms magnitudes before/after onset).
  seegPre: 200,
  setSeegPre: (v) => set({ seegPre: v }),
  seegPost: 800,
  setSeegPost: (v) => set({ seegPost: v }),
  seegTimeIndex: 0,
  setSeegTimeIndex: (i) => set({ seegTimeIndex: i }),
  seegPlaying: false,
  setSeegPlaying: (v) => set({ seegPlaying: v }),
}));
