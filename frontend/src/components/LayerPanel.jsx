import React, { useState } from 'react';
import { useAppStore } from '../store';
import { updateShaft } from '../api';

const styles = {
  panel: {
    width: 280,
    height: '100%',
    background: '#111418',
    borderLeft: '1px solid #1e2530',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    fontFamily: 'IBM Plex Sans, sans-serif',
  },
  header: {
    padding: '14px 16px 12px',
    borderBottom: '1px solid #1e2530',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerTitle: {
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: '0.1em',
    color: '#7a8a99',
    textTransform: 'uppercase',
  },
  section: {
    padding: '12px 16px',
    borderBottom: '1px solid #1e2530',
  },
  sectionLabel: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.08em',
    color: '#4a5568',
    textTransform: 'uppercase',
    marginBottom: 10,
  },
  row: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
    marginBottom: 8,
  },
  label: {
    fontSize: 12,
    color: '#7a8a99',
    flex: '0 0 auto',
    minWidth: 60,
  },
  sliderWrap: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  sliderValue: {
    fontSize: 10,
    fontFamily: 'IBM Plex Mono, monospace',
    color: '#4a5568',
    flex: '0 0 30px',
    textAlign: 'right',
  },
  shaftList: {
    flex: 1,
    overflowY: 'auto',
    padding: '8px 0',
  },
  shaftRow: {
    display: 'flex',
    alignItems: 'center',
    padding: '6px 16px',
    gap: 10,
    cursor: 'pointer',
    transition: 'background 0.1s',
  },
  shaftDot: {
    width: 10,
    height: 10,
    borderRadius: '50%',
    flex: '0 0 10px',
    border: '1px solid rgba(255,255,255,0.2)',
  },
  shaftName: {
    flex: 1,
    fontSize: 12,
    color: '#e8edf2',
    fontFamily: 'IBM Plex Mono, monospace',
  },
  shaftType: {
    fontSize: 9,
    color: '#4a5568',
    letterSpacing: '0.06em',
    textTransform: 'uppercase',
  },
  toggle: {
    width: 28,
    height: 16,
    borderRadius: 8,
    position: 'relative',
    cursor: 'pointer',
    border: 'none',
    transition: 'background 0.2s',
    flex: '0 0 28px',
  },
  toggleThumb: {
    position: 'absolute',
    top: 2,
    width: 12,
    height: 12,
    borderRadius: '50%',
    background: 'white',
    transition: 'left 0.2s',
  },
  contactCount: {
    fontSize: 9,
    fontFamily: 'IBM Plex Mono, monospace',
    color: '#4a5568',
    background: '#1c2028',
    padding: '1px 5px',
    borderRadius: 3,
  },
  emptyState: {
    padding: '24px 16px',
    textAlign: 'center',
    color: '#4a5568',
    fontSize: 11,
    lineHeight: 1.6,
  }
};

function OpacitySlider({ label, value, onChange, color }) {
  return (
    <div style={styles.row}>
      <span style={styles.label}>{label}</span>
      <div style={styles.sliderWrap}>
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={value}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          style={{ accentColor: color || 'var(--accent)' }}
        />
        <span style={styles.sliderValue}>{Math.round(value * 100)}%</span>
      </div>
    </div>
  );
}

function ShaftToggle({ on, color, onChange }) {
  return (
    <button
      style={{ ...styles.toggle, background: on ? color : '#1e2530' }}
      onClick={onChange}
    >
      <div style={{ ...styles.toggleThumb, left: on ? 14 : 2 }} />
    </button>
  );
}

export default function LayerPanel({ showMri, setShowMri, mriOpacity, setMriOpacity }) {
  const {
    brainOpacity, setBrainOpacity,
    reconstruction,
    shaftVisibility, setShaftVisible,
    selectedShaftId, setSelectedShaftId,
    isEditorMode,
  } = useAppStore();

  const shafts = reconstruction?.electrode_shafts || [];

  const handleToggleShaft = async (shaft) => {
    const newVisible = !(shaftVisibility[shaft.id] !== false && shaft.visible);
    setShaftVisible(shaft.id, newVisible);
    if (isEditorMode) {
      try {
        await updateShaft(shaft.id, { visible: newVisible });
      } catch (e) {
        console.warn('Failed to persist shaft visibility', e);
      }
    }
  };

  const typeLabel = (t) => ({ depth: 'DEPTH', strip: 'STRIP', grid: 'GRID' }[t] || t.toUpperCase());

  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <span style={styles.headerTitle}>Layers</span>
        <span style={{ fontSize: 10, color: '#4a5568' }}>
          {shafts.length} shaft{shafts.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* MRI brain surface — optional overlay */}
      <div style={styles.section}>
        <div style={styles.sectionLabel}>MRI Brain Surface</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: showMri ? 10 : 0 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', flex: 1 }}>
            <input
              type="checkbox"
              checked={!!showMri}
              onChange={e => setShowMri?.(e.target.checked)}
              style={{ accentColor: '#00d4ff', width: 14, height: 14 }}
            />
            <span style={{ fontSize: 12, color: showMri ? '#e8edf2' : '#7a8a99', transition: 'color 0.2s' }}>
              Show MRI surface
            </span>
          </label>
        </div>
        {showMri && (
          <OpacitySlider
            label="Opacity"
            value={mriOpacity ?? 0.3}
            onChange={v => setMriOpacity?.(v)}
            color="#00d4ff"
          />
        )}
      </div>

      {/* Electrode shafts */}
      <div style={{ ...styles.section, borderBottom: 'none', paddingBottom: 4 }}>
        <div style={styles.sectionLabel}>Electrode Shafts</div>
      </div>

      <div style={styles.shaftList}>
        {shafts.length === 0 ? (
          <div style={styles.emptyState}>
            No electrodes placed yet.
            {isEditorMode && (
              <><br /><br />Use the editor panel to add electrode shafts.</>
            )}
          </div>
        ) : (
          shafts.map((shaft) => {
            const visible = shaftVisibility[shaft.id] !== false && shaft.visible;
            const isSelected = selectedShaftId === shaft.id;
            const contactCount = shaft.contacts?.length || 0;

            return (
              <div
                key={shaft.id}
                style={{
                  ...styles.shaftRow,
                  background: isSelected ? '#1c2028' : 'transparent',
                  opacity: visible ? 1 : 0.45,
                }}
                onClick={() => setSelectedShaftId(isSelected ? null : shaft.id)}
              >
                <div style={{ ...styles.shaftDot, background: shaft.color }} />

                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={styles.shaftName}>{shaft.name}</span>
                    <span style={styles.shaftType}>{typeLabel(shaft.electrode_type)}</span>
                  </div>
                  <div style={{ marginTop: 2 }}>
                    <span style={styles.contactCount}>{contactCount} contacts</span>
                  </div>
                </div>

                <ShaftToggle
                  on={visible}
                  color={shaft.color}
                  onChange={(e) => { e.stopPropagation(); handleToggleShaft(shaft); }}
                />
              </div>
            );
          })
        )}
      </div>

      {/* Selected shaft details */}
      {selectedShaftId && (() => {
        const shaft = shafts.find(s => s.id === selectedShaftId);
        if (!shaft) return null;
        const contacts = [...(shaft.contacts || [])].sort((a, b) => a.contact_number - b.contact_number);

        return (
          <div style={{ borderTop: '1px solid #1e2530', maxHeight: 200, overflowY: 'auto' }}>
            <div style={{ ...styles.section, paddingBottom: 8 }}>
              <div style={styles.sectionLabel}>{shaft.name} — contacts</div>
              {contacts.map(c => (
                <div key={c.contact_number} style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  padding: '3px 0',
                  borderBottom: '1px solid #1a1e24',
                }}>
                  <span style={{ ...styles.label, fontFamily: 'IBM Plex Mono, monospace', fontSize: 11 }}>
                    {shaft.name}{c.contact_number}
                  </span>
                  <span style={{ fontSize: 9, fontFamily: 'IBM Plex Mono, monospace', color: '#4a5568' }}>
                    {c.is_manual ? '● manual' : '○ auto'}
                  </span>
                  {c.x_mm != null && (
                    <span style={{ fontSize: 9, fontFamily: 'IBM Plex Mono, monospace', color: '#4a5568' }}>
                      {c.x_mm.toFixed(1)}, {c.y_mm.toFixed(1)}, {c.z_mm.toFixed(1)}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        );
      })()}
    </div>
  );
}
