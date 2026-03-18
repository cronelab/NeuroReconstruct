import React, { useState } from 'react';
import { useAppStore } from '../store';
import api, { getShareLink } from '../api';

const s = {
  header: {
    height: 52,
    background: '#111418',
    borderBottom: '1px solid #1e2530',
    display: 'flex',
    alignItems: 'center',
    padding: '0 20px',
    gap: 12,
    flexShrink: 0,
    zIndex: 10,
  },
  logo: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    flexShrink: 0,
  },
  logoIcon: {
    width: 26,
    height: 26,
    borderRadius: '50%',
    background: 'radial-gradient(circle at 35% 35%, #00d4ff, #005566)',
    flexShrink: 0,
  },
  logoText: {
    fontSize: 13,
    fontWeight: 600,
    color: '#e8edf2',
    letterSpacing: '0.03em',
    fontFamily: 'IBM Plex Sans, sans-serif',
  },
  divider: { width: 1, height: 20, background: '#1e2530' },
  spacer: { flex: 1 },
  patientInfo: { display: 'flex', flexDirection: 'column', gap: 2 },
  patientLabel: { fontSize: 11, color: '#e8edf2', fontWeight: 500, fontFamily: 'IBM Plex Sans, sans-serif' },
  patientMeta: { fontSize: 13, color: '#ffffff', fontFamily: 'IBM Plex Mono, monospace' },
  statusBadge: {
    fontSize: 9, fontFamily: 'IBM Plex Mono, monospace',
    padding: '2px 8px', borderRadius: 3, fontWeight: 600,
    letterSpacing: '0.08em', textTransform: 'uppercase',
  },
  btn: {
    padding: '5px 12px', borderRadius: 4, fontSize: 12,
    fontWeight: 600, fontFamily: 'IBM Plex Sans, sans-serif',
    cursor: 'pointer', border: 'none',
  },
  roleChip: {
    fontSize: 10, fontFamily: 'IBM Plex Mono, monospace',
    padding: '2px 7px', borderRadius: 3, background: '#1e2530', color: '#7a8a99',
  },
};

const statusColors = {
  ready:      { bg: '#0d2a1a', color: '#00e676', border: '#004d20' },
  processing: { bg: '#1a1a0d', color: '#ffab40', border: '#4d3a00' },
  pending:    { bg: '#13161c', color: '#7a8a99', border: '#2a3340' },
  error:      { bg: '#2a0d0d', color: '#ff5252', border: '#5a1a1a' },
};

export default function Header({ onBack, onNavigate }) {
  const { reconstruction, user, logout, isEditorMode, setEditorMode, setReconstruction } = useAppStore();
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showUnlockWarning, setShowUnlockWarning] = useState(false);

  const isLocked = reconstruction?.is_locked || false;
  const isComplete = reconstruction?.is_complete || false;
  const canEdit = user && (user.role === 'editor' || user.role === 'admin');

  const handleShare = async () => {
    if (!reconstruction) return;
    try {
      const res = await getShareLink(reconstruction.id);
      const fullUrl = `${window.location.origin}${res.data.share_url}`;
      await navigator.clipboard.writeText(fullUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (e) {
      alert('Could not copy share link');
    }
  };

  // Mark as complete: is_complete=true, is_locked=true
  const handleMarkComplete = async () => {
    if (!reconstruction || busy) return;
    setBusy(true);
    try {
      await api.patch(`/reconstructions/${reconstruction.id}/status`, { is_complete: true, is_locked: true });
      setReconstruction({ ...reconstruction, is_complete: true, is_locked: true });
      setEditorMode(false);
      // Completed view defaults: MRI on, CT off — signal via store
      if (typeof window.__onMarkComplete === 'function') window.__onMarkComplete();
    } finally { setBusy(false); }
  };

  // Unlock: is_complete=false, is_locked=false → goes back to In Progress
  const handleUnlock = async () => {
    if (!reconstruction || busy) return;
    setBusy(true);
    setShowUnlockWarning(false);
    try {
      await api.patch(`/reconstructions/${reconstruction.id}/status`, { is_complete: false, is_locked: false });
      setReconstruction({ ...reconstruction, is_complete: false, is_locked: false });
      setEditorMode(true);
    } finally { setBusy(false); }
  };

  const status = reconstruction?.status || 'pending';
  const statusStyle = statusColors[status] || statusColors.pending;

  return (
    <div style={s.header}>

      {/* Logo — click to go home */}
      <div
        style={{ ...s.logo, cursor: onBack ? 'pointer' : 'default' }}
        onClick={onBack || undefined}
        title={onBack ? 'Back to home' : ''}
      >
        <div style={s.logoIcon} />
        <span style={s.logoText}>NeuroReconstruct</span>
      </div>

      <div style={s.divider} />

      {reconstruction ? (
        <>
          <div style={s.patientInfo}>
            <span style={s.patientMeta}>ID: {reconstruction.patient_id}</span>
          </div>
          <div style={{ ...s.statusBadge, background: statusStyle.bg, color: statusStyle.color, border: `1px solid ${statusStyle.border}` }}>
            {status === 'processing' && '⟳ '}{status}
          </div>
        </>
      ) : (
        <span style={{ color: '#7a8a99', fontSize: 12 }}>No reconstruction loaded</span>
      )}

      <div style={s.spacer} />

      {/* Actions — only in viewer */}
      {reconstruction && canEdit && (<>

        {/* Edit mode toggle — only when unlocked */}
        {!isLocked && (
          <button
            style={{ ...s.btn, background: isEditorMode ? '#002233' : 'transparent', color: isEditorMode ? '#00d4ff' : '#7a8a99', border: `1px solid ${isEditorMode ? '#00d4ff55' : '#2a3340'}`, cursor: isEditorMode ? 'default' : 'pointer' }}
            onClick={() => { if (!isEditorMode) setEditorMode(true); }}
          >
            {isEditorMode ? '✎ Editing' : '✎ Edit'}
          </button>
        )}

        {/* Mark as Complete — only when unlocked/in-progress */}
        {!isLocked && (
          <button
            disabled={busy}
            onClick={handleMarkComplete}
            style={{ ...s.btn, background: '#0d2a1a', color: '#00e676', border: '1px solid #00e67644', opacity: busy ? 0.5 : 1 }}
          >
            ✓ Mark as Complete
          </button>
        )}

        {/* Unlock — only when locked/completed */}
        {isLocked && !showUnlockWarning && (
          <button
            style={{ ...s.btn, background: '#1a1000', color: '#ffab40', border: '1px solid #ffab4044' }}
            onClick={() => setShowUnlockWarning(true)}
          >
            🔒 Locked — Unlock to Edit
          </button>
        )}

        {/* Inline unlock warning */}
        {isLocked && showUnlockWarning && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, background: '#1a1000', border: '1px solid #ffab4044', borderRadius: 4, padding: '4px 10px' }}>
            <span style={{ fontSize: 11, color: '#ffab40', fontFamily: 'IBM Plex Mono, monospace' }}>
              ⚠ This will move the reconstruction back to In Progress
            </span>
            <button onClick={handleUnlock} disabled={busy} style={{ ...s.btn, padding: '3px 10px', background: '#ffab4022', color: '#ffab40', border: '1px solid #ffab4066', fontSize: 11 }}>
              Confirm Unlock
            </button>
            <button onClick={() => setShowUnlockWarning(false)} style={{ ...s.btn, padding: '3px 8px', background: 'none', color: '#4a5568', border: 'none', fontSize: 11 }}>
              Cancel
            </button>
          </div>
        )}

        {/* Share */}
        <button
          style={{ ...s.btn, background: copied ? '#0d2a1a' : 'transparent', color: copied ? '#00e676' : '#7a8a99', border: `1px solid ${copied ? '#004d20' : '#2a3340'}` }}
          onClick={handleShare}
        >
          {copied ? '✓ Copied' : '⎘ Share'}
        </button>

      </>)}

      <div style={s.divider} />

      {user ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={s.roleChip}>{user.role}</span>
          <span style={{ fontSize: 11, color: '#7a8a99' }}>{user.username}</span>
          <button style={{ ...s.btn, background: 'transparent', color: '#7a8a99', border: 'none', padding: '4px 8px' }} onClick={logout}>⏻</button>
        </div>
      ) : (
        <button style={{ ...s.btn, background: '#002233', color: '#00d4ff', border: '1px solid #00d4ff44' }} onClick={() => onNavigate?.('/login')}>Login</button>
      )}
    </div>
  );
}
