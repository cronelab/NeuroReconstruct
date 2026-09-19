import { create } from 'zustand';

// Drop layers whose scan has gone away or stopped being renderable, and never
// leave the viewer with no pane to draw.
function pruneLayers(layers, scans) {
  const kept = layers.filter(k => k === 'primary' || scans.some(sc => sc.id === k && sc.ready));
  return kept.length ? kept : ['primary'];
}

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
  setReconstruction: (r) => set((s) => {
    // The slice viewer's layer choice belongs to a reconstruction: scan ids are
    // per-reconstruction, so carrying one across would have the viewer ask the
    // new reconstruction for another one's scan and get a 404. Seed the list
    // from the payload at the same time, so the SCAN bar is right on first paint
    // instead of after its own fetch returns.
    const changed = r?.id !== s.reconstruction?.id;
    if (!changed) return { reconstruction: r };
    return {
      reconstruction: r,
      secondaryScans: r?.secondary_scans || [],
      visibleLayers: ['primary'],
    };
  }),

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

  // ── Slice-viewer layers ──────────────────────────────────────────────────────
  // Which scans the 2D slice views draw, as panes side by side: 'primary' for
  // the primary MRI, otherwise a secondary scan's id. Shared across all three
  // axes, so a choice made once applies to sagittal, axial and coronal alike.
  //
  // This is membership, not order -- MultiViewLayout lays the panes out
  // primary-first and then in scan order, so toggling one off and back on does
  // not shuffle the others. Every layer is stored resampled into the primary's
  // grid, so one slice index means the same anatomy in all of them, which is
  // what lets the panes scroll together and share one structure overlay.
  secondaryScans: [],            // [{ id, label, modality, status, ready, error }]
  setSecondaryScans: (scans) => set((s) => ({
    secondaryScans: scans,
    visibleLayers: pruneLayers(s.visibleLayers, scans),
  })),
  visibleLayers: ['primary'],
  setVisibleLayers: (layers) => set({ visibleLayers: layers.length ? layers : ['primary'] }),
  toggleLayer: (key) => set((s) => {
    const next = s.visibleLayers.includes(key)
      ? s.visibleLayers.filter(k => k !== key)
      : [...s.visibleLayers, key];
    // Turning off the last pane would leave nothing to look at; keep it on.
    return { visibleLayers: next.length ? next : s.visibleLayers };
  }),

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
  // The cursor survives a new payload on the same time axis (a band or signal change,
  // or phase 2 filling in the voltages) and resets only when the axis itself changes.
  setSeegActivity: (a) => set((s) => {
    const prev = s.seegActivity?.times, next = a?.times;
    const sameAxis = prev && next && prev.length === next.length
      && prev[0] === next[0] && prev[prev.length - 1] === next[next.length - 1];
    return { seegActivity: a, seegTimeIndex: sameAxis ? s.seegTimeIndex : 0,
      seegLiveValues: null, seegPlayheadTime: null };
  }),
  seegBand: 'high_gamma',
  setSeegBand: (b) => set({ seegBand: b }),
  // Trial-averaged peri-EVENT display window (ms magnitudes before/after the align event).
  seegPre: 500,
  setSeegPre: (v) => set({ seegPre: v }),
  seegPost: 2000,
  setSeegPost: (v) => set({ seegPost: v }),
  // Alignment event for trial epochs: 'stimulus' (stimulus onset) | 'response' (spoken-response onset).
  seegAlign: 'stimulus',
  setSeegAlign: (a) => set({ seegAlign: a }),
  // Z-score baseline window (ms, both <= 0), ALWAYS relative to stimulus onset.
  seegBaseStart: -500,
  setSeegBaseStart: (v) => set({ seegBaseStart: v }),
  seegBaseEnd: 0,
  setSeegBaseEnd: (v) => set({ seegBaseEnd: v }),
  seegMode: 'trial',             // 'trial' (trial-averaged) | 'scroll' (continuous)
  setSeegMode: (m) => set({ seegMode: m }),
  // 'z' band-power z-score | 'filtered' voltage bandpassed to the selected band |
  // 'raw' voltage as recorded. The last two differ only by the server-side filter.
  seegTraceSignal: 'z',
  setSeegTraceSignal: (s) => set({ seegTraceSignal: s }),
  seegTraceScope: 'all',         // 'all' | 'shaft'
  setSeegTraceScope: (s) => set({ seegTraceScope: s }),
  seegTraceShaft: null,          // selected shaft when scope === 'shaft'
  setSeegTraceShaft: (s) => set({ seegTraceShaft: s }),
  seegTracePanelW: 400,          // right trace-panel width (px), user-resizable
  setSeegTracePanelW: (w) => set({ seegTracePanelW: w }),
  seegTraceGain: 1,              // vertical zoom of the trace stack (row height + amplitude)
  setSeegTraceGain: (g) => set({ seegTraceGain: g }),
  // Time base of the trace panel: how much of the recording is on screen at once, in
  // that mode's own unit (ms for a trial window, s for a continuous recording).
  // null = fit the whole thing. Held per mode, like the playback speed, because the two
  // are orders of magnitude apart.
  // { value, unit }: the amount as typed and the unit it was typed in, kept apart so
  // the field reads back exactly as entered. value null = fit the whole recording.
  // A trial window is a couple of seconds and is meant to be read whole; a continuous
  // recording is minutes long, where ten seconds a screen is how EEG is read.
  seegTraceWindow: {
    trial: { value: null, unit: 'ms' },
    scroll: { value: 10, unit: 's' },
  },
  setSeegTraceWindow: (mode, w) => set((s) => ({
    seegTraceWindow: { ...s.seegTraceWindow, [mode]: w },
  })),
  seegBrainOpacity: 0.4,         // native-brain surface opacity in the sEEG view
  setSeegBrainOpacity: (v) => set({ seegBrainOpacity: v }),
  seegIgnoreOutside: true,       // render contacts outside the brain mesh inert (default on)
  setSeegIgnoreOutside: (v) => set({ seegIgnoreOutside: v }),
  seegColorLimit: null,          // manual color-scale half-range (±z); null = auto
  setSeegColorLimit: (v) => set({ seegColorLimit: v }),
  seegStructureOpacity: 0.4,     // structure-overlay opacity in the sEEG view
  setSeegStructureOpacity: (v) => set({ seegStructureOpacity: v }),
  seegTimeIndex: 0,
  // A seek puts the brain back on a whole frame, so it drops any playback in-between values.
  setSeegTimeIndex: (i) => set({ seegTimeIndex: i, seegLiveValues: null, seegPlayheadTime: null }),
  // During playback: the per-channel values for the instant actually on screen, which
  // generally falls between frames (see SeegViewer's playback loop). null = show the frame.
  seegLiveValues: null,
  // The instant on screen, on the recording's own clock. The frame index alone is too
  // coarse to draw a cursor with: a 140 Hz map steps 7 ms at a time, which is a visible
  // jump once the trace panel is zoomed into a few seconds. null = not playing.
  seegPlayheadTime: null,
  setSeegPlayhead: (i, values, t) => set({
    seegTimeIndex: i, seegLiveValues: values, seegPlayheadTime: t,
  }),
  seegPlaying: false,
  setSeegPlaying: (v) => set({ seegPlaying: v }),
  // Playback speed as a multiple of real time, held per mapping mode: a trial window
  // is a couple of seconds and wants slow motion, a continuous recording is minutes long.
  seegPlaySpeed: { trial: 0.05, scroll: 2 },
  setSeegPlaySpeed: (mode, v) => set((s) => ({ seegPlaySpeed: { ...s.seegPlaySpeed, [mode]: v } })),
}));
