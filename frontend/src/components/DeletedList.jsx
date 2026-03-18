import React, { useEffect, useState, useCallback } from 'react';
import { listDeletedReconstructions, permanentlyDeleteReconstruction, restoreReconstruction } from '../api';
import { useAppStore } from '../store';

const font = 'IBM Plex Sans, sans-serif';
const mono = 'IBM Plex Mono, monospace';

export default function DeletedList({ onBack }) {
  const { user } = useAppStore();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);

  const isAdmin = user?.role === 'admin';
  const canEdit = user?.role === 'editor' || user?.role === 'admin';

  const load = useCallback(async () => {
    try {
      const res = await listDeletedReconstructions();
      setItems(res.data);
    } catch (e) {
      console.error('Failed to load deleted reconstructions', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleRestore = async (id) => {
    try {
      await restoreReconstruction(id);
      setItems(prev => prev.filter(r => r.id !== id));
    } catch (e) {
      alert('Failed to restore: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handlePermanentDelete = async (id) => {
    setBusy(true);
    try {
      await permanentlyDeleteReconstruction(id);
      setItems(prev => prev.filter(r => r.id !== id));
      setConfirm(null);
    } catch (e) {
      alert('Failed to delete: ' + (e.response?.data?.detail || e.message));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '28px 36px', fontFamily: font, background: '#0a0c10' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 32 }}>
        <button
          onClick={onBack}
          style={{ background: 'none', border: '1px solid #2a3340', borderRadius: 4, color: '#b0bec5', padding: '7px 16px', fontSize: 13, cursor: 'pointer', fontFamily: font }}
        >
          ← Back
        </button>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 600, color: '#e8edf2', letterSpacing: '-0.01em', marginBottom: 4 }}>
            🗑 Deleted
          </h1>
          <p style={{ fontSize: 13, color: '#7a8a99' }}>
            {isAdmin
              ? 'Restore or permanently delete reconstructions and their data files.'
              : 'Restore reconstructions to In Progress, or contact an admin to permanently delete.'}
          </p>
        </div>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: '#7a8a99' }}>Loading...</div>
      ) : items.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 80, border: '1px dashed #1e2530', borderRadius: 8 }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>🗑</div>
          <div style={{ fontSize: 14, color: '#7a8a99' }}>No deleted reconstructions</div>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
          {items.map(recon => (
            <div key={recon.id} style={{
              position: 'relative',
              background: '#111418',
              border: '1px solid #2a1a1a',
              borderRadius: 6, padding: 16,
            }}>
              {/* Recover button — top left */}
              {canEdit && (
                <button
                  onClick={() => handleRestore(recon.id)}
                  title="Recover to In Progress"
                  style={{
                    position: 'absolute', top: 10, left: 10,
                    width: 22, height: 22, borderRadius: '50%',
                    background: '#1a1a1a', border: '1px solid #2a3340',
                    color: '#4a5568', fontSize: 13, lineHeight: 1,
                    cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.background = '#00e676'; e.currentTarget.style.color = '#0a0c10'; e.currentTarget.style.borderColor = '#00e676'; }}
                  onMouseLeave={e => { e.currentTarget.style.background = '#1a1a1a'; e.currentTarget.style.color = '#4a5568'; e.currentTarget.style.borderColor = '#2a3340'; }}
                >
                  ↩
                </button>
              )}

              {/* Permanent delete button — top right, admin only */}
              {isAdmin && (
                <button
                  onClick={() => setConfirm(recon.id)}
                  title="Permanently delete"
                  style={{
                    position: 'absolute', top: 10, right: 10,
                    width: 22, height: 22, borderRadius: '50%',
                    background: '#1a1a1a', border: '1px solid #2a3340',
                    color: '#4a5568', fontSize: 13, lineHeight: 1,
                    cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.background = '#ff5252'; e.currentTarget.style.color = '#fff'; e.currentTarget.style.borderColor = '#ff5252'; }}
                  onMouseLeave={e => { e.currentTarget.style.background = '#1a1a1a'; e.currentTarget.style.color = '#4a5568'; e.currentTarget.style.borderColor = '#2a3340'; }}
                >
                  ✕
                </button>
              )}

              {/* Title row — padded on both sides for the two buttons */}
              <div style={{ marginBottom: 8, paddingLeft: canEdit ? 28 : 0, paddingRight: isAdmin ? 28 : 0 }}>
                <div style={{ fontSize: 15, fontWeight: 600, color: '#f0f4f8', marginBottom: 3 }}>{recon.label}</div>
                <div style={{ fontSize: 12, fontFamily: mono, color: '#b0bec5' }}>{recon.patient_id}</div>
              </div>

              {/* Footer */}
              <div style={{ borderTop: '1px solid #1a1e24', paddingTop: 10, display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ fontSize: 12, color: '#7a8a99', fontFamily: mono }}>
                  Created: {new Date(recon.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                </span>
                <span style={{ fontSize: 12, color: '#ff525499', fontFamily: mono }}>
                  Deleted: {new Date(recon.deleted_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Permanent delete confirmation dialog */}
      {confirm !== null && (
        <div style={{
          position: 'fixed', inset: 0,
          background: 'rgba(0,0,0,0.75)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 1000,
        }}>
          <div style={{
            background: '#111418', border: '1px solid #ff525444',
            borderRadius: 8, padding: 28, maxWidth: 380, width: '90%',
            fontFamily: font,
          }}>
            <div style={{ fontSize: 18, fontWeight: 600, color: '#e8edf2', marginBottom: 10 }}>
              Permanently delete?
            </div>
            <div style={{ fontSize: 13, color: '#b0bec5', marginBottom: 24, lineHeight: 1.6 }}>
              This will permanently delete the reconstruction and all associated MRI, CT, and electrode data.
              <strong style={{ color: '#ff5252', display: 'block', marginTop: 8 }}>This cannot be undone.</strong>
            </div>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setConfirm(null)}
                disabled={busy}
                style={{ padding: '8px 20px', background: 'transparent', color: '#b0bec5', border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, cursor: 'pointer', fontFamily: font }}
              >
                Cancel
              </button>
              <button
                onClick={() => handlePermanentDelete(confirm)}
                disabled={busy}
                style={{ padding: '8px 20px', background: '#ff5252', color: '#fff', border: 'none', borderRadius: 4, fontSize: 13, fontWeight: 600, cursor: busy ? 'not-allowed' : 'pointer', fontFamily: font, opacity: busy ? 0.6 : 1 }}
              >
                {busy ? 'Deleting...' : 'Yes, Delete Permanently'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
