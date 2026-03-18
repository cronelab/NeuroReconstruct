import React, { useState, useEffect, useCallback } from 'react';
import { useAppStore } from '../store';
import { createShaft, autofillShaft, deleteContact, updateShaft, initContacts } from '../api';
import api from '../api';

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
  reconId,
  isLocked = false, onShaftsUpdated, onThresholdChange, hasCtFile,
  showMri, setShowMri, mriOpacity, setMriOpacity, hasMesh,
  onUndo, undoAvailable,
  activeContactNumber, setActiveContactNumber,
  currentThreshold,
  showStructures, setShowStructures, onLoadStructures,
}) {
  const { reconstruction, selectedShaftId, setSelectedShaftId, structuresData, structureVisible, setStructureVisible } = useAppStore();
  const [localStructuresLoading, setLocalStructuresLoading] = useState(false);

  const [huThreshold, setHuThreshold] = useState(0);
  const debouncedThreshold = useDebounce(huThreshold, 400);

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

  const [editingShaft, setEditingShaft] = useState(null); // shaft being inline-edited
  const [autofilling, setAutofilling] = useState(false);
  const [autofillMsg, setAutofillMsg] = useState('');
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

  useEffect(() => { onThresholdChange?.(huThreshold); }, []);
  useEffect(() => { onThresholdChange?.(debouncedThreshold); }, [debouncedThreshold]);

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

  const handleUpdateShaftField = async (shaft, field, value) => {
    if (isLocked) return;
    try {
      await updateShaft(shaft.id, { [field]: value });
      await onShaftsUpdated?.();
    } catch (e) {
      console.error('Failed to update shaft', e);
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

  const handleDeleteContact = async (shaftId, contactNumber) => {
    if (isLocked) return;
    try {
      await deleteContact(shaftId, contactNumber);
      await onShaftsUpdated?.();
    } catch (e) {
      console.error(e);
    }
  };

  const handleDeleteShaft = async (shaftId, shaftName) => {
    if (isLocked) return;
    if (!window.confirm(`Delete electrode shaft "${shaftName}" and all its contacts? This cannot be undone.`)) return;
    try {
      await api.delete(`/reconstructions/shafts/${shaftId}`);
      if (selectedShaftId === shaftId) {
        setSelectedShaftId(null);
        setActiveContactNumber?.(null);
      }
      await onShaftsUpdated?.();
    } catch (e) {
      alert('Failed to delete shaft: ' + (e.response?.data?.detail || e.message));
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
          <div style={{ padding: '8px 14px', display: 'flex', alignItems: 'center', gap: 8, borderBottom: hasMesh ? '1px solid #1a2030' : 'none' }}>
            <span style={{ fontSize: 13, color: '#e8edf2', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', flexShrink: 0 }}>CT</span>
            <input type="range" min={-1000} max={3000} step={50} value={huThreshold}
              onChange={e => setHuThreshold(Number(e.target.value))}
              style={{ flex: 1, accentColor: '#ffdd00' }} />
            <input type="number" min={-1000} max={3000} step={50} value={huThreshold}
              onChange={e => { const v = Math.max(-1000, Math.min(3000, Number(e.target.value))); if (!isNaN(v)) setHuThreshold(v); }}
              style={{ width: 68, textAlign: 'right', fontFamily: 'IBM Plex Mono, monospace', fontSize: 13, color: '#ffdd00',
                background: '#111418', border: '1px solid #2a3340', borderRadius: 4, padding: '3px 6px' }} />
            <span style={{ fontSize: 13, color: '#b0bec5', fontFamily: 'IBM Plex Mono, monospace', flexShrink: 0 }}>HU</span>
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
      {hasMesh && (
        <div style={{ borderBottom: '1px solid #1e2530', padding: '8px 14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <span style={{ fontSize: 13, color: '#e8edf2', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>Structures</span>
            {!structuresData && (
              <button
                onClick={async () => {
                  if (localStructuresLoading) return;
                  setLocalStructuresLoading(true);
                  try { await onLoadStructures?.(); } finally { setLocalStructuresLoading(false); }
                }}
                disabled={localStructuresLoading}
                style={{ fontSize: 11, color: localStructuresLoading ? '#4a5568' : '#74C0FC', background: 'none', border: '1px solid #1e2530', borderRadius: 4, padding: '3px 8px', cursor: localStructuresLoading ? 'default' : 'pointer', fontFamily: 'IBM Plex Mono, monospace' }}>
                {localStructuresLoading ? 'Computing…' : '⊕ Load'}
              </button>
            )}
          </div>
          {structuresData && Object.keys(structuresData).length > 0 && (
            <div style={{ maxHeight: 480, overflowY: 'auto' }}>
              {['subcortical', 'frontal', 'temporal', 'parietal', 'occipital', 'cingulate'].filter(g =>
                Object.values(structuresData).some(s => s.group === g && s.vertices)
              ).map(group => {
                const entries = Object.entries(structuresData).filter(([,s]) => s.group === group && s.vertices);
                if (!entries.length) return null;
                const leftEntries  = entries.filter(([k]) => k.endsWith('_l'));
                const rightEntries = entries.filter(([k]) => k.endsWith('_r'));
                const midline      = entries.filter(([k]) => !k.endsWith('_l') && !k.endsWith('_r'));
                const selectAll   = () => entries.forEach(([k]) => setStructureVisible(k, true));
                const deselectAll = () => entries.forEach(([k]) => setStructureVisible(k, false));
                const btnBase = { fontSize: 11, background: 'none', border: '1px solid #2a3440', borderRadius: 3, padding: '2px 9px', cursor: 'pointer', fontFamily: 'IBM Plex Mono, monospace' };
                return (
                  <div key={group} style={{ marginBottom: 14 }}>
                    {/* Group header */}
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                      <div style={{ fontSize: 11, fontWeight: 600, color: '#7a8a99', textTransform: 'uppercase', letterSpacing: '0.06em', fontFamily: 'IBM Plex Mono, monospace' }}>{group}</div>
                      <div style={{ display: 'flex', gap: 5 }}>
                        <button onClick={selectAll}   style={{ ...btnBase, color: '#74C0FC' }}>All</button>
                        <button onClick={deselectAll} style={{ ...btnBase, color: '#7a8a99' }}>None</button>
                      </div>
                    </div>
                    {/* Midline structures — full width */}
                    {midline.map(([key, s]) => {
                      const checked = structureVisible?.[key] !== false;
                      return (
                        <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 5 }}>
                          <input type="checkbox" checked={checked}
                            onChange={e => setStructureVisible(key, e.target.checked)}
                            style={{ accentColor: s.color, width: 15, height: 15, flexShrink: 0, cursor: 'pointer' }} />
                          <div style={{ width: 11, height: 11, borderRadius: 2, background: s.color, flexShrink: 0 }} />
                          <span style={{ fontSize: 13, color: '#c8d4e0', fontFamily: 'IBM Plex Sans, sans-serif' }}>{s.label}</span>
                        </div>
                      );
                    })}
                    {/* Bilateral structures in two columns */}
                    {(leftEntries.length > 0 || rightEntries.length > 0) && (
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 10 }}>
                        {[['Left', leftEntries], ['Right', rightEntries]].map(([side, sideEntries]) => (
                          <div key={side}>
                            <div style={{ fontSize: 10, color: '#7a8a99', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 5, fontFamily: 'IBM Plex Mono, monospace' }}>{side}</div>
                            {sideEntries.map(([key, s]) => {
                              const checked = structureVisible?.[key] !== false;
                              const label = s.label.replace(/^(Left|Right)\s+/i, '');
                              return (
                                <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 5 }}>
                                  <input type="checkbox" checked={checked}
                                    onChange={e => setStructureVisible(key, e.target.checked)}
                                    style={{ accentColor: s.color, width: 15, height: 15, flexShrink: 0, cursor: 'pointer' }} />
                                  <div style={{ width: 11, height: 11, borderRadius: 2, background: s.color, flexShrink: 0 }} />
                                  <span style={{ fontSize: 13, color: '#c8d4e0', fontFamily: 'IBM Plex Sans, sans-serif', lineHeight: 1.2 }}>{label}</span>
                                </div>
                              );
                            })}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {structuresData && Object.keys(structuresData).length === 0 && (
            <div style={{ fontSize: 11, color: '#4a5568', fontFamily: 'IBM Plex Mono, monospace' }}>No structures found</div>
          )}
        </div>
      )}

      {/* ── SHAFT HEADER ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 14px', borderBottom: '1px solid #1e2530', flexShrink: 0, background: '#111418' }}>
        <span style={{ ...s.sectionTitle, marginBottom: 0 }}>Electrode Shafts</span>
        <div style={{ display: 'flex', gap: 6 }}>
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
          const isEditing = editingShaft === shaft.id;
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
                <button
                  onClick={e => { e.stopPropagation(); handleDeleteShaft(shaft.id, shaft.name); }}
                  style={{ background: 'none', border: 'none', color: '#ff525488', cursor: 'pointer', fontSize: 18, padding: '0 4px', lineHeight: 1 }}
                  title="Delete shaft"
                >✕</button>
              </div>

              <div style={s.shaftMeta}>
                <span>{contacts.length} placed · {manual} manual</span>
              </div>

              {/* Inline edit when selected */}
              {isSelected && (
                <div style={{ marginTop: 8 }} onClick={e => e.stopPropagation()}>
                  <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
                    <div style={{ flex: '0 0 60px' }}>
                      <label style={s.label}>Prefix</label>
                      <input defaultValue={shaft.name}
                        onBlur={e => handleUpdateShaftField(shaft, 'name', e.target.value.toUpperCase())}
                        style={{ width: '100%' }} />
                    </div>
                    <div style={{ flex: 1 }}>
                      <label style={s.label}>Label</label>
                      <input defaultValue={shaft.label || ''}
                        placeholder="e.g. Left Amygdala"
                        onBlur={e => handleUpdateShaftField(shaft, 'label', e.target.value)}
                        style={{ width: '100%' }} />
                    </div>
                    <div style={{ flex: '0 0 auto' }}>
                      <label style={s.label}>Color</label>
                      <ColorPicker value={shaft.color} onChange={hex => handleUpdateShaftField(shaft, 'color', hex)} />
                    </div>
                  </div>

                </div>
              )}
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
    </div>
  );
}
