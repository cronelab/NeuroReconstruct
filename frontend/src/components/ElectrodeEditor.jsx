import React, { useState, useEffect, useCallback } from 'react';
import { useAppStore } from '../store';
import { createShaft, autofillShaft, deleteContact, updateShaft, initContacts } from '../api';
import api from '../api';
import CtHistogramSlider from './CtHistogramSlider';
import StructurePanel from './StructurePanel';
import { buildStructureMeshes, structuresCentroid, computeShaftAnatomyLabel } from '../anatomy';

const ELECTRODE_TYPES = [
  { value: 'depth', label: 'Depth (sEEG)' },
  { value: 'strip', label: 'Strip (ECoG)' },
  { value: 'grid',  label: 'Grid (ECoG)'  },
];

const s = {
  // Outer wrapper — fixed height column, bottom bar stays pinned
  panel: {
    width: '100%', flex: 1,
    display: 'flex', flexDirection: 'column',
    overflow: 'hidden',
    fontFamily: 'IBM Plex Sans, sans-serif',
    background: '#111418',
  },
  // Everything above the autofill bar scrolls together
  scrollArea: {
    flex: 1, overflowY: 'auto', overflowX: 'hidden',
    minHeight: 0,
  },
  section: { padding: '14px 18px', borderBottom: '1px solid #1e2530' },
  sectionTitle: {
    fontSize: 15, fontWeight: 600, letterSpacing: '0.08em',
    color: '#7a8a99', textTransform: 'uppercase', marginBottom: 12, display: 'block',
  },
  label: {
    display: 'block', fontSize: 13, color: '#e8edf2',
    marginBottom: 5, letterSpacing: '0.06em', textTransform: 'uppercase',
  },
  row: { display: 'flex', gap: 8, marginBottom: 10, alignItems: 'center' },
  btn: {
    padding: '8px 16px', borderRadius: 4, fontSize: 14, fontWeight: 500,
    cursor: 'pointer', fontFamily: 'IBM Plex Sans, sans-serif',
    transition: 'all 0.15s', border: 'none',
  },
  btnPrimary: { background: '#00d4ff', color: '#0a0c10' },
  btnSuccess: { background: '#0d2a1a', color: '#00e676', border: '1px solid #004d20' },
  btnDisabled: { opacity: 0.4, cursor: 'not-allowed' },
  btnUndo: { background: '#1a1a0d', color: '#ffab40', border: '1px solid #4d3000' },
  shaftList: {},  // no special styles needed — inside scrollArea
  shaftItem: { padding: '10px 12px', borderBottom: '1px solid #1a1e24', cursor: 'pointer', transition: 'background 0.1s' },
  shaftName: { fontSize: 15, fontWeight: 600, fontFamily: 'IBM Plex Mono, monospace', color: '#ffffff' },
  shaftLabel: { fontSize: 13, color: '#e8edf2', fontFamily: 'IBM Plex Sans, sans-serif', marginTop: 1 },
  shaftMeta: { fontSize: 12, color: '#b0bec5', fontFamily: 'IBM Plex Mono, monospace', display: 'flex', gap: 6, marginTop: 2 },
  contactList: {
    maxHeight: 180, overflowY: 'auto',
    background: '#0d1015', margin: '6px 18px',
    borderRadius: 4, border: '1px solid #1a1e24',
  },
  contactRow: {
    display: 'flex', alignItems: 'center',
    padding: '6px 12px', borderBottom: '1px solid #1a1e24',
    fontSize: 13, fontFamily: 'IBM Plex Mono, monospace',
  },
  autofillBar: { padding: '12px 18px', borderTop: '1px solid #1e2530', background: '#0d1015', flexShrink: 0 },
};

// Debounce hook
function useDebounce(value, delay) {
  const [deb, setDeb] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDeb(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return deb;
}



// ── Named color palette ────────────────────────────────────────────────────────
const COLOR_PALETTE = [
  // Reds
  { name: 'Crimson',      hex: '#dc143c' },
  { name: 'Tomato',       hex: '#ff4500' },
  { name: 'Coral',        hex: '#ff6b6b' },
  { name: 'Salmon',       hex: '#fa8072' },
  { name: 'Rose',         hex: '#ff007f' },
  // Oranges
  { name: 'Orange',       hex: '#ff8c00' },
  { name: 'Amber',        hex: '#ffbf00' },
  { name: 'Gold',         hex: '#ffd700' },
  { name: 'Tangerine',    hex: '#f28500' },
  { name: 'Peach',        hex: '#ffb347' },
  // Yellows
  { name: 'Yellow',       hex: '#ffff00' },
  { name: 'Lemon',        hex: '#fff44f' },
  { name: 'Butter',       hex: '#fce883' },
  { name: 'Maize',        hex: '#fbec5d' },
  // Greens
  { name: 'Lime',         hex: '#32cd32' },
  { name: 'Mint',         hex: '#00e676' },
  { name: 'Emerald',      hex: '#50c878' },
  { name: 'Forest',       hex: '#228b22' },
  { name: 'Sage',         hex: '#8fbc8f' },
  { name: 'Olive',        hex: '#808000' },
  { name: 'Chartreuse',   hex: '#7fff00' },
  // Cyans / Teals
  { name: 'Cyan',         hex: '#00d4ff' },
  { name: 'Aqua',         hex: '#00ffff' },
  { name: 'Teal',         hex: '#008080' },
  { name: 'Turquoise',    hex: '#40e0d0' },
  { name: 'Seafoam',      hex: '#2e8b57' },
  // Blues
  { name: 'Sky',          hex: '#87ceeb' },
  { name: 'Cornflower',   hex: '#6495ed' },
  { name: 'Blue',         hex: '#1e90ff' },
  { name: 'Cobalt',       hex: '#0047ab' },
  { name: 'Navy',         hex: '#003087' },
  { name: 'Periwinkle',   hex: '#ccccff' },
  { name: 'Steel',        hex: '#4682b4' },
  // Purples / Violets
  { name: 'Lavender',     hex: '#b57bee' },
  { name: 'Violet',       hex: '#8a2be2' },
  { name: 'Purple',       hex: '#9400d3' },
  { name: 'Indigo',       hex: '#4b0082' },
  { name: 'Plum',         hex: '#cc0080' },
  { name: 'Mauve',        hex: '#e0b0ff' },
  { name: 'Magenta',      hex: '#ff00ff' },
  { name: 'Fuchsia',      hex: '#ff44cc' },
  // Pinks
  { name: 'Hot Pink',     hex: '#ff69b4' },
  { name: 'Blush',        hex: '#ffb6c1' },
  { name: 'Flamingo',     hex: '#fc8eac' },
  // Neutrals / Metallics
  { name: 'White',        hex: '#f0f0f0' },
  { name: 'Silver',       hex: '#c0c0c0' },
  { name: 'Platinum',     hex: '#e5e4e2' },
  { name: 'Champagne',    hex: '#f7e7ce' },
  { name: 'Bronze',       hex: '#cd7f32' },
];

// Swatched color picker component
function ColorPicker({ value, onChange }) {
  const [open, setOpen] = React.useState(false);
  const [popupStyle, setPopupStyle] = React.useState({});
  const btnRef = React.useRef(null);
  const current = COLOR_PALETTE.find(c => c.hex === value) || { name: 'Custom', hex: value };

  const handleOpen = () => {
    if (!open && btnRef.current) {
      const rect = btnRef.current.getBoundingClientRect();
      const popupWidth = 8 * 28 + 7 * 4 + 20; // 8 cols * 28px + gaps + padding
      const popupHeight = Math.ceil(COLOR_PALETTE.length / 8) * 32 + 20;
      const left = Math.min(rect.left, window.innerWidth - popupWidth - 8);
      const top = rect.bottom + window.scrollY + 4;
      const flipUp = rect.bottom + popupHeight > window.innerHeight;
      setPopupStyle({
        position: 'fixed',
        left: Math.max(8, left),
        top: flipUp ? rect.top - popupHeight - 4 : rect.bottom + 4,
        zIndex: 9999,
      });
    }
    setOpen(p => !p);
  };

  // Close on outside click
  React.useEffect(() => {
    if (!open) return;
    const handler = (e) => { if (btnRef.current && !btnRef.current.closest('[data-colorpicker]')?.contains(e.target)) setOpen(false); };
    window.addEventListener('mousedown', handler);
    return () => window.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div data-colorpicker="1" style={{ position: 'relative' }}>
      <button
        ref={btnRef}
        type="button"
        onClick={handleOpen}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          background: '#0d1015', border: '1px solid #2a3340',
          borderRadius: 4, padding: '4px 10px', cursor: 'pointer',
          fontFamily: 'IBM Plex Sans, sans-serif',
        }}
      >
        <div style={{ width: 14, height: 14, borderRadius: '50%', background: value, border: '2px solid rgba(255,255,255,0.2)', flexShrink: 0 }} />
        <span style={{ fontSize: 14, color: '#ffffff' }}>{current.name}</span>
        <span style={{ fontSize: 12, color: '#b0bec5' }}>▾</span>
      </button>
      {open && (
        <div style={{
          ...popupStyle,
          background: '#1a1e24', border: '1px solid #2a3340', borderRadius: 6,
          padding: 10,
          display: 'grid', gridTemplateColumns: 'repeat(8, 28px)', gap: 4,
          boxShadow: '0 8px 32px #000c',
          width: 'max-content',
        }}>
          {COLOR_PALETTE.map(c => (
            <button
              key={c.hex}
              type="button"
              title={c.name}
              onClick={() => { onChange(c.hex); setOpen(false); }}
              style={{
                width: 28, height: 28, borderRadius: 4,
                background: c.hex,
                border: c.hex === value ? '3px solid #fff' : '2px solid rgba(255,255,255,0.1)',
                cursor: 'pointer', padding: 0,
                boxShadow: c.hex === value ? '0 0 6px #fff8' : 'none',
                transition: 'transform 0.1s',
              }}
              onMouseEnter={e => e.currentTarget.style.transform = 'scale(1.2)'}
              onMouseLeave={e => e.currentTarget.style.transform = 'scale(1)'}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Contact selector sub-component ────────────────────────────────────────────
function ContactSelector({ shaft, activeContactNumber, setActiveContactNumber, onDeleteContact, isLocked = false }) {
  const { contactScale, setContactScale } = useAppStore();
  const n = shaft.n_total_contacts || 12;
  const placedMap = {};
  (shaft.contacts || []).forEach(c => { if (c.x_mm != null) placedMap[c.contact_number] = c; });

  const isGrid = shaft.electrode_type === 'grid';
  const rows = isGrid ? (shaft.grid_rows || 4) : 1;
  const cols = isGrid ? (shaft.grid_cols || 4) : n;
  // For grids, use rows*cols as true total — n_total_contacts may be stale
  const total = isGrid ? rows * cols : n;

  const availableWidth = 280;
  const btnSize = isGrid
    ? Math.max(22, Math.min(38, Math.floor((availableWidth - (cols - 1) * 4) / cols)))
    : Math.max(26, Math.min(38, Math.floor((availableWidth - (Math.min(n, 12) - 1) * 4) / Math.min(n, 12))));
  const btnFontSize = btnSize < 28 ? 9 : 11;

  return (
    <div style={{ padding: '12px 14px' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontSize: 15, fontWeight: 600, color: '#ffffff', fontFamily: 'IBM Plex Mono, monospace' }}>
          {shaft.name}{shaft.label ? ` — ${shaft.label}` : ''}
        </span>
        <span style={{ fontSize: 13, color: '#b0bec5', fontFamily: 'IBM Plex Mono, monospace' }}>
          {Object.keys(placedMap).length}/{total}
        </span>
      </div>

      {isGrid && (
        <div style={{ fontSize: 12, color: '#b0bec5', fontFamily: 'IBM Plex Mono, monospace', marginBottom: 6 }}>
          {rows} × {cols} grid
        </div>
      )}

      {/* Contact grid */}
      <div style={{ marginBottom: 10, overflowX: 'auto' }}>
        {Array.from({ length: rows }, (_, row) => (
          <div key={row} style={{ display: 'flex', gap: 3, marginBottom: 3, flexWrap: isGrid ? 'nowrap' : 'wrap' }}>
            {Array.from({ length: cols }, (_, col) => {
              const num = row * cols + col + 1;
              if (num > total) return null;
              const placed = !!placedMap[num];
              const isActive = activeContactNumber === num;
              const c = placedMap[num];
              return (
                <div key={num} style={{ position: 'relative' }}>
                  <button
                    onClick={() => setActiveContactNumber?.(isActive ? null : num)}
                    title={placed
                      ? `${shaft.name}${num}: ${c.x_mm?.toFixed(1)}, ${c.y_mm?.toFixed(1)}, ${c.z_mm?.toFixed(1)}`
                      : `Place ${shaft.name}${num}`}
                    style={{
                      width: btnSize, height: btnSize,
                      borderRadius: 3, fontSize: btnFontSize, fontWeight: 600,
                      fontFamily: 'IBM Plex Mono, monospace',
                      cursor: 'pointer', transition: 'all 0.12s', padding: 0,
                      border: isActive ? `2px solid ${shaft.color}` : placed ? `1px solid ${shaft.color}88` : '1px solid #2a3340',
                      background: isActive ? shaft.color : placed ? `${shaft.color}33` : '#0d1015',
                      color: isActive ? '#0a0c10' : placed ? shaft.color : '#4a5568',
                      boxShadow: isActive ? `0 0 6px ${shaft.color}99` : 'none',
                    }}
                  >
                    {num}
                  </button>
                  {placed && !isActive && (
                    <div style={{
                      position: 'absolute', bottom: 1, right: 1,
                      width: 3, height: 3, borderRadius: '50%',
                      background: shaft.color, pointerEvents: 'none',
                    }} />
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      {/* Status hint */}
      <div style={{ fontSize: 13, fontFamily: 'IBM Plex Mono, monospace', color: '#7a8a99', minHeight: 18, marginBottom: 6 }}>
        {activeContactNumber != null
          ? placedMap[activeContactNumber]
            ? `${shaft.name}${activeContactNumber} placed — click to re-place`
            : `Click CT to place ${shaft.name}${activeContactNumber}`
          : 'Tap a contact number to place it'}
      </div>

      {/* Delete active contact — hidden when locked */}
      {!isLocked && activeContactNumber != null && placedMap[activeContactNumber] && (
        <button
          onClick={() => onDeleteContact(shaft.id, activeContactNumber)}
          style={{ padding: '3px 10px', background: 'none', border: '1px solid #ff525444', borderRadius: 4, color: '#ff5252cc', fontSize: 13, cursor: 'pointer', fontFamily: 'IBM Plex Mono, monospace' }}
        >
          ✕ Remove {shaft.name}{activeContactNumber}
        </button>
      )}

      {/* ── Contact Size ── */}
      <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid #1e2530' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
          <span style={{ fontSize: 13, color: '#e8edf2', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', fontFamily: 'IBM Plex Sans, sans-serif' }}>Contact Size</span>
          <span style={{ fontSize: 13, color: '#00d4ff', fontFamily: 'IBM Plex Mono, monospace' }}>{contactScale.toFixed(1)}×</span>
        </div>
        <input
          type="range" min={0.3} max={3.0} step={0.1}
          value={contactScale}
          onChange={e => setContactScale(parseFloat(e.target.value))}
          style={{ width: '100%', accentColor: '#00d4ff' }}
        />
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#4a5568', fontFamily: 'IBM Plex Mono, monospace', marginTop: 2 }}>
          <span>Small</span>
          <span>Default</span>
          <span>Large</span>
        </div>
      </div>
    </div>
  );
}

export default function ElectrodeEditor({
  reconId, shareToken,
  isLocked = false, onShaftsUpdated, onThresholdChange, hasCtFile,
  showMri, setShowMri, mriOpacity, setMriOpacity, hasMesh,
  onUndo, undoAvailable,
  activeContactNumber, setActiveContactNumber,
  currentThreshold,
  showStructures, setShowStructures, onLoadStructures,
  structureOpacity, setStructureOpacity,
}) {
  const { reconstruction, selectedShaftId, setSelectedShaftId, structuresData, structureVisible, setStructureVisible, setStructureVisibleMany, placeMode, setPlaceMode } = useAppStore();

  // CT HU window: floor (lower bound) + ceiling (upper bound). Ceiling defaults
  // to the top of the range = open top (includes all bright metal), matching
  // the previous floor-only behavior.
  const [huFloor, setHuFloor] = useState(2000);
  const [huCeiling, setHuCeiling] = useState(3100);
  const debFloor = useDebounce(huFloor, 400);
  const debCeiling = useDebounce(huCeiling, 400);

  const [showNewShaft, setShowNewShaft] = useState(false);
  const [newShaft, setNewShaft] = useState({
    name: '',
    label: '',
    electrode_type: 'depth',
    color: '#00ff88',
    n_total_contacts: 12,
    spacing_mm: 3.5,
    grid_rows: 4,
    grid_cols: 4,
    contact_diameter_mm: 0.8,
  });

  const [editingShaft, setEditingShaft] = useState(null); // { id, name, label, color } draft being edited
  const [savingShaftEdit, setSavingShaftEdit] = useState(false);
  const [editShaftError, setEditShaftError] = useState(null);
  const [shaftToDelete, setShaftToDelete] = useState(null); // { id, name } pending delete
  const [deletingShaft, setDeletingShaft] = useState(false);
  const [deleteShaftError, setDeleteShaftError] = useState(null);
  const [autofilling, setAutofilling] = useState(false);
  const [autofillMsg, setAutofillMsg] = useState('');
  const [labelingBusy, setLabelingBusy] = useState(false);
  const [labelMsg, setLabelMsg] = useState('');
  const [leftWidth, setLeftWidth] = React.useState(150);
  const isDragging = React.useRef(false);

  const handleDividerMouseDown = React.useCallback((e) => {
    e.preventDefault();
    isDragging.current = true;
    const startX = e.clientX;
    const startWidth = leftWidth;
    const onMove = (ev) => {
      if (!isDragging.current) return;
      setLeftWidth(Math.max(100, Math.min(320, startWidth + (ev.clientX - startX))));
    };
    const onUp = () => {
      isDragging.current = false;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [leftWidth]);

  const shafts = reconstruction?.electrode_shafts || [];
  const selectedShaft = shafts.find(s => s.id === selectedShaftId);
  const manualContacts = selectedShaft?.contacts?.filter(c => c.is_manual && c.x_mm != null) || [];
  const canAutofill = manualContacts.length >= 2;

  useEffect(() => { onThresholdChange?.(huFloor, huCeiling); }, []);
  useEffect(() => { onThresholdChange?.(debFloor, debCeiling); }, [debFloor, debCeiling]);

  // Keyboard undo
  useEffect(() => {
    const handler = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'z') {
        e.preventDefault();
        onUndo?.();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onUndo]);

  const handleCreateShaft = async () => {
    if (isLocked) return;
    if (!newShaft.name.trim()) return;
    try {
      const res = await createShaft(reconId, {
        name: newShaft.name,
        label: newShaft.label || null,
        electrode_type: newShaft.electrode_type,
        color: newShaft.color,
        n_total_contacts: newShaft.n_total_contacts,
        spacing_mm: newShaft.spacing_mm,
        grid_rows: newShaft.grid_rows,
        grid_cols: newShaft.grid_cols,
        contact_diameter_mm: newShaft.contact_diameter_mm,
      });
      // Initialize empty contact slots for this shaft
      await initContacts(res.data.id);
      setShowNewShaft(false);
      setNewShaft(p => ({ ...p, name: '', label: '' }));
      await onShaftsUpdated?.();
      // Auto-select shaft and first contact
      setSelectedShaftId(res.data.id);
      setActiveContactNumber?.(1);
    } catch (e) {
      alert('Failed to create shaft: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleSaveShaftEdit = async () => {
    if (isLocked || !editingShaft) return;
    setSavingShaftEdit(true);
    setEditShaftError(null);
    try {
      await updateShaft(editingShaft.id, {
        name: (editingShaft.name || '').toUpperCase().trim(),
        label: (editingShaft.label ?? '').trim(),
        color: editingShaft.color,
      });
      setEditingShaft(null);
      await onShaftsUpdated?.();
    } catch (e) {
      setEditShaftError(e.response?.data?.detail || e.message || 'Update failed');
    } finally {
      setSavingShaftEdit(false);
    }
  };

  const handleAutofill = async () => {
    if (isLocked) return;
    if (!selectedShaft || !canAutofill) return;
    setAutofilling(true);
    setAutofillMsg('Fitting spline...');
    try {
      const placedContacts = (selectedShaft.contacts || [])
        .filter(c => c.x_mm != null && c.is_manual)
        .sort((a, b) => a.contact_number - b.contact_number);
      const manualContacts = placedContacts.map(c => ({
        contact_number: c.contact_number,
        position: [c.x_mm, c.y_mm, c.z_mm],
      }));
      await autofillShaft(selectedShaft.id, {
        manual_contacts: manualContacts,
        n_total_contacts: selectedShaft.n_total_contacts || 12,
        electrode_type: selectedShaft.electrode_type,
        grid_rows: selectedShaft.grid_rows,
        grid_cols: selectedShaft.grid_cols,
        hu_threshold: currentThreshold ?? null,
      });
      setAutofillMsg('✓ Autofill complete');
      await onShaftsUpdated?.();
    } catch (e) {
      setAutofillMsg('✗ Failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setAutofilling(false);
      setTimeout(() => setAutofillMsg(''), 4000);
    }
  };

  // Auto-label each depth shaft as "insertion region-target region", where the
  // target is the structure at the deepmost contact and the insertion is the
  // structure at the most superficial contact. Overwrites existing labels.
  const handleAutofillLabels = async () => {
    if (isLocked || labelingBusy) return;
    if (!structuresData || Object.keys(structuresData).length === 0) {
      setLabelMsg('✗ Load brain structures first');
      setTimeout(() => setLabelMsg(''), 4000);
      return;
    }
    const depthShafts = shafts.filter(sh => sh.electrode_type === 'depth');
    if (depthShafts.length === 0) {
      setLabelMsg('✗ No depth electrodes to label');
      setTimeout(() => setLabelMsg(''), 4000);
      return;
    }
    setLabelingBusy(true);
    setLabelMsg('Deriving anatomy…');
    let meshes = null;
    try {
      meshes = buildStructureMeshes(structuresData);
      const centroid = structuresCentroid(structuresData);
      let updated = 0, skipped = 0;
      for (const sh of depthShafts) {
        const label = computeShaftAnatomyLabel(sh, structuresData, meshes, centroid);
        if (!label) { skipped++; continue; }
        await updateShaft(sh.id, { label });
        updated++;
      }
      await onShaftsUpdated?.();
      setLabelMsg(`✓ Labeled ${updated} electrode${updated !== 1 ? 's' : ''}${skipped ? ` · ${skipped} skipped (no contacts)` : ''}`);
    } catch (e) {
      setLabelMsg('✗ Failed: ' + (e.response?.data?.detail || e.message || 'error'));
    } finally {
      if (meshes) meshes.forEach(m => m.geometry.dispose());
      setLabelingBusy(false);
      setTimeout(() => setLabelMsg(''), 5000);
    }
  };

  const handleDeleteContact = async (shaftId, contactNumber) => {
    if (isLocked) return;
    try {
      await deleteContact(shaftId, contactNumber);
      await onShaftsUpdated?.();
    } catch (e) {
      console.error(e);
    }
  };

  // Shaft deletion uses an in-app confirmation modal (not window.confirm, which
  // browsers silently suppress once dialogs are blocked — making delete appear
  // to do nothing). `shaftToDelete` holds { id, name } for the pending delete.
  const confirmDeleteShaft = async () => {
    if (!shaftToDelete) return;
    setDeletingShaft(true);
    setDeleteShaftError(null);
    try {
      await api.delete(`/reconstructions/shafts/${shaftToDelete.id}`);
      if (selectedShaftId === shaftToDelete.id) {
        setSelectedShaftId(null);
        setActiveContactNumber?.(null);
      }
      setShaftToDelete(null);
      await onShaftsUpdated?.();
    } catch (e) {
      setDeleteShaftError(e.response?.data?.detail || e.message || 'Delete failed');
    } finally {
      setDeletingShaft(false);
    }
  };

  return (
    <div style={s.panel}>

      {/* ── LOCKED BANNER ── */}
      {isLocked && (
        <div style={{ flexShrink: 0, background: '#1a1000', borderBottom: '1px solid #ffab4033', padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 14 }}>🔒</span>
          <span style={{ fontSize: 13, color: '#ffab40', fontFamily: 'IBM Plex Mono, monospace' }}>
            Reconstruction is locked — unlock from the home page to edit
          </span>
        </div>
      )}

      {/* ── TOP BAR: CT + MRI compact controls ── */}
      <div style={{ flexShrink: 0, borderBottom: '1px solid #1e2530', background: '#0d1015' }}>
        {hasCtFile && (
          <div style={{ padding: '8px 14px', borderBottom: hasMesh ? '1px solid #1a2030' : 'none' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span style={{ fontSize: 13, color: '#e8edf2', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', flexShrink: 0 }}>CT</span>
              <span style={{ fontSize: 11, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace' }}>HU window</span>
            </div>
            <CtHistogramSlider
              reconId={reconId}
              shareToken={shareToken}
              floor={huFloor}
              ceiling={huCeiling}
              onChange={(f, c) => { setHuFloor(f); setHuCeiling(c); }}
            />
          </div>
        )}
        {hasMesh && (
          <div style={{ padding: '6px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 13, color: '#e8edf2', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', flexShrink: 0 }}>MRI</span>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', flexShrink: 0 }}>
              <input type="checkbox" checked={showMri} onChange={e => setShowMri(e.target.checked)}
                style={{ accentColor: '#00d4ff', width: 13, height: 13 }} />
              <span style={{ fontSize: 13, color: showMri ? '#ffffff' : '#b0bec5' }}>Show</span>
            </label>
            {showMri && (<>
              <input type="range" min={0} max={1} step={0.05} value={mriOpacity}
                onChange={e => setMriOpacity(parseFloat(e.target.value))}
                style={{ flex: 1, accentColor: '#00d4ff' }} />
              <span style={{ fontSize: 13, fontFamily: 'IBM Plex Mono, monospace', color: '#7a8a99', flexShrink: 0 }}>{Math.round(mriOpacity * 100)}%</span>
            </>)}
          </div>
        )}
      </div>

      {/* ── STRUCTURES ── */}
      {/* Shared with the locked/read-only right panel so both offer identical options */}
      {hasMesh && (
        <StructurePanel
          onLoadStructures={onLoadStructures}
          structureOpacity={structureOpacity}
          setStructureOpacity={setStructureOpacity}
        />
      )}

      {/* ── SHAFT HEADER ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', rowGap: 6, padding: '8px 14px', borderBottom: '1px solid #1e2530', flexShrink: 0, background: '#111418' }}>
        <span style={{ ...s.sectionTitle, marginBottom: 0 }}>Electrode Shafts</span>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end', flex: '1 1 100%', minWidth: 0 }}>
          {!isLocked && (
            <button
              onClick={() => setPlaceMode(!placeMode)}
              title={placeMode
                ? 'Place-contacts mode is ON — click the CT to place contacts. Click to turn off and re-enable hover.'
                : 'Turn on place-contacts mode to place contacts by clicking the CT. Hover interactions are disabled while it is on.'}
              style={{ ...s.btn, padding: '4px 10px',
                background: placeMode ? '#0d2a1a' : 'transparent',
                color: placeMode ? '#00e676' : '#7a8a99',
                border: `1px solid ${placeMode ? '#00e67655' : '#2a3340'}`,
                cursor: 'pointer' }}
            >
              {placeMode ? '◎ Placing' : '◎ Place contacts'}
            </button>
          )}
          {!isLocked && (
            <button
              onClick={handleAutofillLabels}
              disabled={labelingBusy}
              title="Auto-fill each depth electrode's label as “insertion region-target region” from the brain structures (overwrites existing labels). Requires loaded structures."
              style={{ ...s.btn, padding: '4px 10px', background: '#1a1522', color: labelingBusy ? '#7a8a99' : '#c8a2ff', border: '1px solid #3a2a52', cursor: labelingBusy ? 'default' : 'pointer' }}
            >
              {labelingBusy ? 'Labeling…' : '🏷 Auto-label'}
            </button>
          )}
          {undoAvailable && (
            <button
              style={{ ...s.btn, ...s.btnUndo, padding: '4px 10px' }}
              onClick={onUndo}
              title="Undo last contact (Ctrl+Z)"
            >
              ↩ Undo
            </button>
          )}
          <button
            style={{ ...s.btn, ...s.btnPrimary, padding: '4px 10px' }}
            onClick={() => !isLocked && setShowNewShaft(p => !p)}
            disabled={isLocked}
            style={{ ...s.btn, ...s.btnPrimary, padding: '4px 10px', opacity: isLocked ? 0.3 : 1, cursor: isLocked ? 'not-allowed' : 'pointer' }}
          >
            {showNewShaft ? '✕' : '+ Shaft'}
          </button>
        </div>
      </div>
      {labelMsg && (
        <div style={{ padding: '6px 14px', fontSize: 12, fontFamily: 'IBM Plex Mono, monospace', color: labelMsg.startsWith('✗') ? '#ff8a80' : '#c8a2ff', borderBottom: '1px solid #1e2530', background: '#0d1015', flexShrink: 0 }}>
          {labelMsg}
        </div>
      )}

      {/* ── New shaft form ────────────────────────────────── */}
      {showNewShaft && (
        <div style={{ ...s.section, background: '#0d1015' }}>

          {/* Name + Label */}
          <div style={s.row}>
            <div style={{ flex: '0 0 80px' }}>
              <label style={s.label}>Prefix</label>
              <input
                value={newShaft.name}
                onChange={e => setNewShaft(p => ({ ...p, name: e.target.value.toUpperCase() }))}
                placeholder="e.g. LA" maxLength={6}
                onKeyDown={e => e.key === 'Enter' && handleCreateShaft()}
                style={{ width: '100%' }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={s.label}>Full Label</label>
              <input
                value={newShaft.label}
                onChange={e => setNewShaft(p => ({ ...p, label: e.target.value }))}
                placeholder="e.g. Left Amygdala"
                onKeyDown={e => e.key === 'Enter' && handleCreateShaft()}
                style={{ width: '100%' }}
              />
            </div>
          </div>

          {/* Type + Color */}
          <div style={s.row}>
            <div style={{ flex: 1 }}>
              <label style={s.label}>Type</label>
              <select
                value={newShaft.electrode_type}
                onChange={e => {
                  setNewShaft(p => ({ ...p, electrode_type: e.target.value }));
                }}
                style={{ width: '100%' }}
              >
                {ELECTRODE_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div style={{ flex: '0 0 auto' }}>
              <label style={s.label}>Color</label>
              <ColorPicker value={newShaft.color} onChange={hex => setNewShaft(p => ({ ...p, color: hex }))} />
            </div>
          </div>

          {/* Dimensions — depth: contacts+spacing, strip: cols+spacing, grid: rows×cols+spacing */}
          {newShaft.electrode_type === 'depth' && (
            <div style={s.row}>
              <div style={{ flex: 1 }}>
                <label style={s.label}>Contacts</label>
                <input type="number" min={1} max={30} value={newShaft.n_total_contacts}
                  onChange={e => setNewShaft(p => ({ ...p, n_total_contacts: parseInt(e.target.value) }))}
                  style={{ width: '100%' }} />
              </div>
            </div>
          )}

          {newShaft.electrode_type === 'strip' && (
            <div style={s.row}>
              <div style={{ flex: 1 }}>
                <label style={s.label}>Contacts (1 row)</label>
                <input type="number" min={1} max={64} value={newShaft.n_total_contacts}
                  onChange={e => setNewShaft(p => ({
                    ...p,
                    n_total_contacts: parseInt(e.target.value),
                    grid_rows: 1,
                    grid_cols: parseInt(e.target.value),
                  }))}
                  style={{ width: '100%' }} />
              </div>
            </div>
          )}

          {newShaft.electrode_type === 'grid' && (
            <>
              <div style={s.row}>
                <div style={{ flex: 1 }}>
                  <label style={s.label}>Rows</label>
                  <input type="number" min={1} max={16} value={newShaft.grid_rows}
                    onChange={e => {
                      const r = parseInt(e.target.value) || 1;
                      setNewShaft(p => ({ ...p, grid_rows: r, n_total_contacts: r * p.grid_cols }));
                    }}
                    style={{ width: '100%' }} />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={s.label}>Cols</label>
                  <input type="number" min={1} max={16} value={newShaft.grid_cols}
                    onChange={e => {
                      const c = parseInt(e.target.value) || 1;
                      setNewShaft(p => ({ ...p, grid_cols: c, n_total_contacts: p.grid_rows * c }));
                    }}
                    style={{ width: '100%' }} />
                </div>

              </div>
              <div style={{ fontSize: 12, color: '#e8edf2', marginBottom: 8, fontFamily: 'IBM Plex Mono, monospace' }}>
                {newShaft.grid_rows} × {newShaft.grid_cols} = {newShaft.grid_rows * newShaft.grid_cols} contacts total
              </div>
            </>
          )}



          <button style={{ ...s.btn, ...s.btnPrimary, width: '100%', marginTop: 4 }} onClick={handleCreateShaft}>
            Create Shaft
          </button>
        </div>
      )}

      {/* ── MAIN AREA: two columns ── */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>

        {/* LEFT: shaft list */}
        <div style={{ flex: `0 0 ${leftWidth}px`, overflowY: 'auto', background: '#0a0c10' }}>
        {shafts.length === 0 ? (
          <div style={{ padding: 20, textAlign: 'center', color: '#b0bec5', fontSize: 13, lineHeight: 1.6 }}>
            {hasCtFile
              ? 'Adjust the threshold above, then click "+ Shaft" and click on the bright regions.'
              : 'Click "+ Shaft" to add an electrode shaft.'}
          </div>
        ) : shafts.map(shaft => {
          const isSelected = shaft.id === selectedShaftId;
          const contacts = shaft.contacts || [];
          const manual = contacts.filter(c => c.is_manual).length;

          return (
            <div key={shaft.id}
              style={{ ...s.shaftItem, background: isSelected ? '#1c2028' : 'transparent' }}
              onClick={() => {
                if (isSelected) { setSelectedShaftId(null); setActiveContactNumber?.(null); }
                else {
                  setSelectedShaftId(shaft.id);
                  // Find first unplaced contact
                  const placed = new Set((shaft.contacts || []).filter(c => c.x_mm != null).map(c => c.contact_number));
                  const n = shaft.n_total_contacts || 12;
                  const first = Array.from({length: n}, (_, i) => i+1).find(n => !placed.has(n)) ?? 1;
                  setActiveContactNumber?.(first);
                }
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
                {/* Color swatch — click to open color picker */}
                <div onClick={e => e.stopPropagation()}>
                  <div style={{ width: 12, height: 12, borderRadius: '50%', background: shaft.color, flexShrink: 0, border: '2px solid rgba(255,255,255,0.2)' }} />
                </div>
                <span style={s.shaftName}>{shaft.name}</span>
                {shaft.label && <span style={s.shaftLabel}>{shaft.label}</span>}
                <span style={{ ...s.shaftMeta, marginLeft: 'auto', fontSize: 12 }}>{shaft.electrode_type.toUpperCase()}</span>
                {!isLocked && (
                  <button
                    onClick={e => { e.stopPropagation(); setEditShaftError(null); setEditingShaft({ id: shaft.id, name: shaft.name || '', label: shaft.label || '', color: shaft.color }); }}
                    style={{ background: 'none', border: 'none', color: '#7a8a99', cursor: 'pointer', fontSize: 14, padding: '0 4px', lineHeight: 1 }}
                    title="Edit shaft"
                  >✎</button>
                )}
                {!isLocked && (
                  <button
                    onClick={e => { e.stopPropagation(); setDeleteShaftError(null); setShaftToDelete({ id: shaft.id, name: shaft.name }); }}
                    style={{ background: 'none', border: 'none', color: '#ff525488', cursor: 'pointer', fontSize: 18, padding: '0 4px', lineHeight: 1 }}
                    title="Delete shaft"
                  >✕</button>
                )}
              </div>

              <div style={s.shaftMeta}>
                <span>{contacts.length} placed · {manual} manual</span>
              </div>
            </div>
          );
        })}
        </div>{/* end shaft list */}

        {/* Draggable divider */}
        <div
          onMouseDown={handleDividerMouseDown}
          style={{
            flex: '0 0 5px', cursor: 'col-resize',
            background: '#1e2530',
            transition: 'background 0.15s',
          }}
          onMouseEnter={e => e.currentTarget.style.background = '#00d4ff44'}
          onMouseLeave={e => e.currentTarget.style.background = '#1e2530'}
        />

        {/* RIGHT: contact selector */}
        <div style={{ flex: 1, overflowY: 'auto', background: '#111418' }}>
          {selectedShaft
            ? <ContactSelector
                shaft={selectedShaft}
                activeContactNumber={activeContactNumber}
                setActiveContactNumber={setActiveContactNumber}
                onDeleteContact={handleDeleteContact}
                isLocked={isLocked}
              />
            : <div style={{ padding: '24px 16px', color: '#b0bec5', fontSize: 13, textAlign: 'center', lineHeight: 2 }}>
                ← Select a shaft
              </div>
          }
        </div>{/* end right col */}
      </div>{/* end main area */}

      {/* ── Autofill ──────────────────────────────────────── */}
      {selectedShaft && (
        <div style={s.autofillBar}>
          <div style={{ fontSize: 13, color: '#e8edf2', marginBottom: 6, fontFamily: 'IBM Plex Mono, monospace' }}>
            {autofillMsg || (canAutofill ? `Ready — ${manualContacts.length} placed manually` : `Place ${2 - manualContacts.length} more to enable autofill`)}
          </div>
          <div style={{ height: 3, background: '#1e2530', borderRadius: 2, marginBottom: 8, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${Math.min(100, (manualContacts.length / 2) * 100)}%`, background: '#00d4ff', borderRadius: 2, transition: 'width 0.3s' }} />
          </div>
          <button
            style={{ ...s.btn, ...s.btnSuccess, width: '100%', ...(!canAutofill || autofilling || isLocked ? s.btnDisabled : {}) }}
            disabled={!canAutofill || autofilling || isLocked}
            onClick={handleAutofill}
          >
            {autofilling ? '⟳ Fitting spline...' : '⚡ Autofill Remaining Contacts'}
          </button>
        </div>
      )}

      {/* ── Edit-shaft dialog ─────────────────────────────────── */}
      {editingShaft && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }}>
          <div style={{
            background: '#111418', border: '1px solid #2a3340', borderRadius: 8,
            padding: 28, maxWidth: 400, width: '90%', fontFamily: 'IBM Plex Sans, sans-serif',
          }}>
            <div style={{ fontSize: 18, fontWeight: 600, color: '#e8edf2', marginBottom: 18 }}>
              Edit electrode shaft
            </div>

            <label style={s.label}>Name (prefix)</label>
            <input
              value={editingShaft.name}
              onChange={e => setEditingShaft(sh => ({ ...sh, name: e.target.value.toUpperCase() }))}
              onKeyDown={e => { if (e.key === 'Enter' && editingShaft.name.trim()) handleSaveShaftEdit(); }}
              autoFocus
              style={{ width: '100%', marginBottom: 14, boxSizing: 'border-box' }}
            />

            <label style={s.label}>Full label</label>
            <input
              value={editingShaft.label}
              placeholder="e.g. Left Amygdala"
              onChange={e => setEditingShaft(sh => ({ ...sh, label: e.target.value }))}
              onKeyDown={e => { if (e.key === 'Enter' && editingShaft.name.trim()) handleSaveShaftEdit(); }}
              style={{ width: '100%', marginBottom: 14, boxSizing: 'border-box' }}
            />

            <label style={s.label}>Color</label>
            <div style={{ marginBottom: 8 }}>
              <ColorPicker value={editingShaft.color} onChange={hex => setEditingShaft(sh => ({ ...sh, color: hex }))} />
            </div>

            {editShaftError && (
              <div style={{ fontSize: 12, color: '#ff5252', marginBottom: 16, fontFamily: 'IBM Plex Mono, monospace' }}>
                {editShaftError}
              </div>
            )}

            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 18 }}>
              <button
                onClick={() => { setEditingShaft(null); setEditShaftError(null); }}
                disabled={savingShaftEdit}
                style={{ padding: '8px 20px', background: 'transparent', color: '#7a8a99', border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, cursor: savingShaftEdit ? 'not-allowed' : 'pointer', fontFamily: 'IBM Plex Sans, sans-serif' }}
              >
                Cancel
              </button>
              <button
                onClick={handleSaveShaftEdit}
                disabled={savingShaftEdit || !editingShaft.name.trim()}
                style={{ padding: '8px 20px', background: '#0d2233', color: '#00d4ff', border: '1px solid #00d4ff44', borderRadius: 4, fontSize: 13, fontWeight: 600, cursor: (savingShaftEdit || !editingShaft.name.trim()) ? 'not-allowed' : 'pointer', fontFamily: 'IBM Plex Sans, sans-serif', opacity: (savingShaftEdit || !editingShaft.name.trim()) ? 0.6 : 1 }}
              >
                {savingShaftEdit ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Delete-shaft confirmation dialog ──────────────────── */}
      {shaftToDelete && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }}>
          <div style={{
            background: '#111418', border: '1px solid #2a3340', borderRadius: 8,
            padding: 28, maxWidth: 360, width: '90%', fontFamily: 'IBM Plex Sans, sans-serif',
          }}>
            <div style={{ fontSize: 18, fontWeight: 600, color: '#e8edf2', marginBottom: 10 }}>
              Delete electrode shaft?
            </div>
            <div style={{ fontSize: 13, color: '#b0bec5', marginBottom: 24, lineHeight: 1.6 }}>
              Shaft <strong style={{ color: '#e8edf2' }}>{shaftToDelete.name}</strong> and all
              its contacts will be permanently removed. This cannot be undone.
            </div>
            {deleteShaftError && (
              <div style={{ fontSize: 12, color: '#ff5252', marginBottom: 16, fontFamily: 'IBM Plex Mono, monospace' }}>
                {deleteShaftError}
              </div>
            )}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button
                onClick={() => { setShaftToDelete(null); setDeleteShaftError(null); }}
                disabled={deletingShaft}
                style={{ padding: '8px 20px', background: 'transparent', color: '#7a8a99', border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, cursor: deletingShaft ? 'not-allowed' : 'pointer', fontFamily: 'IBM Plex Sans, sans-serif' }}
              >
                Cancel
              </button>
              <button
                onClick={confirmDeleteShaft}
                disabled={deletingShaft}
                style={{ padding: '8px 20px', background: '#1a1010', color: '#ff5252', border: '1px solid #ff525444', borderRadius: 4, fontSize: 13, fontWeight: 600, cursor: deletingShaft ? 'not-allowed' : 'pointer', fontFamily: 'IBM Plex Sans, sans-serif', opacity: deletingShaft ? 0.6 : 1 }}
              >
                {deletingShaft ? 'Deleting…' : 'Delete Shaft'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
