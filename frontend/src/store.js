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

  // Mesh data cache
  meshData: null,
  setMeshData: (data) => set({ meshData: data }),
  structuresData: null,          // { key: { label, color, vertices, faces, ... } }
  setStructuresData: (data) => set({ structuresData: data }),
  structureVisible: {},          // { key: bool }
  setStructureVisible: (key, v) => set(s => ({ structureVisible: { ...s.structureVisible, [key]: v } })),
}));
