import React, { useEffect, useState, useCallback } from 'react';
import { listReconstructions, createReconstruction, softDeleteReconstruction } from '../api';
import { useAppStore } from '../store';

const font = 'IBM Plex Sans, sans-serif';
const mono = 'IBM Plex Mono, monospace';

function ReconCard({ recon, onSelect, canEdit, onDelete }) {
  const [coregBlock, setCoregBlock] = React.useState(false);
  const contacts = recon.electrode_shafts?.reduce((s, sh) => s + (sh.contacts?.length || 0), 0) ?? 0;
  const shafts = recon.electrode_shafts?.length ?? 0;
  const isCoregistering = recon.status === 'registering';
  const isUploading = recon.status === 'processing' && recon.has_ct;

  return (
    <div
      onClick={() => (isCoregistering || isUploading) ? setCoregBlock(true) : onSelect(recon.id)}
      style={{
        position: 'relative',
        background: '#111418',
        border: `1px solid ${recon.is_complete ? '#00e67622' : '#1e2530'}`,
        borderRadius: 6, padding: 16, cursor: 'pointer', transition: 'all 0.15s',
      }}
      onMouseEnter={e => { e.currentTarget.style.background = '#13161c'; e.currentTarget.style.borderColor = recon.is_complete ? '#00e67644' : '#2a3340'; }}
      onMouseLeave={e => { e.currentTarget.style.background = '#111418'; e.currentTarget.style.borderColor = recon.is_complete ? '#00e67622' : '#1e2530'; }}
    >
      {/* Delete button */}
      {canEdit && (
        <button
          onClick={e => { e.stopPropagation(); onDelete(recon); }}
          title="Move to trash"
          style={{
            position: 'absolute', top: 10, right: 10,
            width: 22, height: 22, borderRadius: '50%',
            background: '#1a1a1a', border: '1px solid #2a3340',
            color: '#4a5568', fontSize: 13, lineHeight: 1,
            cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
            transition: 'all 0.15s', zIndex: 1,
          }}
          onMouseEnter={e => { e.currentTarget.style.background = '#ff5252'; e.currentTarget.style.color = '#fff'; e.currentTarget.style.borderColor = '#ff5252'; }}
          onMouseLeave={e => { e.currentTarget.style.background = '#1a1a1a'; e.currentTarget.style.color = '#4a5568'; e.currentTarget.style.borderColor = '#2a3340'; }}
        >
          ✕
        </button>
      )}

      {/* Title row */}
      <div style={{ marginBottom: 8, paddingRight: 28 }}>
        <div style={{ fontSize: 15, fontWeight: 600, color: '#f0f4f8' }}>ID: {recon.patient_id}</div>
      </div>

      {/* Stats */}
      <div style={{ display: 'flex', gap: 20, marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 600, color: '#f0f4f8', fontFamily: mono }}>{shafts}</div>
          <div style={{ fontSize: 11, color: '#7a8a99', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Shafts</div>
        </div>
        <div>
          <div style={{ fontSize: 20, fontWeight: 600, color: '#f0f4f8', fontFamily: mono }}>{contacts}</div>
          <div style={{ fontSize: 11, color: '#7a8a99', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Contacts</div>
        </div>
      </div>

      {/* Uploading / coregistering indicator */}
      {(isCoregistering || isUploading) && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 7,
          background: '#0d1a2a', border: '1px solid #00d4ff22',
          borderRadius: 4, padding: '5px 10px', marginBottom: 10,
        }}>
          <span style={{
            display: 'inline-block', width: 10, height: 10,
            border: '2px solid #00d4ff44', borderTopColor: '#00d4ff',
            borderRadius: '50%', animation: 'spin 1s linear infinite', flexShrink: 0,
          }} />
          <span style={{ fontSize: 11, color: '#00d4ff', fontFamily: mono, letterSpacing: '0.04em' }}>
            {isCoregistering ? 'Co-registering MRI + CT...' : 'Uploading MRI + CT...'}
          </span>
        </div>
      )}

      {/* Co-reg block popup */}
      {coregBlock && (
        <div
          onClick={e => e.stopPropagation()}
          style={{
            position: 'fixed', inset: 0,
            background: 'rgba(0,0,0,0.75)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 1000,
          }}
        >
          <div style={{
            background: '#111418', border: '1px solid #00d4ff33',
            borderRadius: 8, padding: 28, maxWidth: 360, width: '90%',
            fontFamily: font, textAlign: 'center',
          }}>
            <div style={{ fontSize: 28, marginBottom: 12 }}>⏳</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: '#e8edf2', marginBottom: 10 }}>
              Co-registration in progress
            </div>
            <div style={{ fontSize: 13, color: '#b0bec5', marginBottom: 22, lineHeight: 1.6 }}>
              {isCoregistering
              ? 'MRI and CT are being co-registered. Please wait until this completes before opening the reconstruction.'
              : 'MRI and CT are being uploaded and processed. Please wait until this completes before opening the reconstruction.'}
            </div>
            <button
              onClick={() => setCoregBlock(false)}
              style={{ padding: '8px 24px', background: '#00d4ff', color: '#0a0c10', border: 'none', borderRadius: 4, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: font }}
            >
              OK
            </button>
          </div>
        </div>
      )}

      {/* Footer */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderTop: '1px solid #1a1e24', paddingTop: 10 }}>
        <span style={{ fontSize: 11, color: '#7a8a99', fontFamily: mono }}>
          {new Date(recon.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
        </span>
        {recon.is_complete
          ? <span style={{ fontSize: 11, fontFamily: mono, color: '#00e676' }}>🔒 Locked</span>
          : <span style={{ fontSize: 11, fontFamily: mono, color: '#ffab40' }}>✎ In Progress</span>
        }
      </div>
    </div>
  );
}

export default function ReconstructionList({ onSelect, onTrash }) {
  const { user } = useAppStore();
  const [reconstructions, setReconstructions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ patient_id: '', mri_file: null, ct_file: null, ct_preregistered: false });
  const [confirmDelete, setConfirmDelete] = useState(null); // recon object pending soft delete
  const [deleting, setDeleting] = useState(false);

  const canEdit = user && (user.role === 'editor' || user.role === 'admin');

  const load = useCallback(async () => {
    try {
      const res = await listReconstructions();
      setReconstructions(res.data);
    } catch (e) {
      console.error('Failed to load reconstructions', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const poll = setInterval(load, 10000);
    return () => clearInterval(poll);
  }, [load]);

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!form.mri_file) return;
    setCreating(true);
    try {
      const fd = new FormData();
      fd.append('patient_id', form.patient_id);
      fd.append('label', form.patient_id);
      fd.append('mri_file', form.mri_file);
      if (form.ct_file) fd.append('ct_file', form.ct_file);
      fd.append('ct_preregistered', form.ct_preregistered ? 'true' : 'false');
      const res = await createReconstruction(fd);
      const newRecon = res.data;
      // Optimistically block the card immediately on upload:
      // - pre-registered CT: show 'Uploading' spinner (processing)
      // - unregistered CT: show 'Co-registering' spinner (registering)
      if (form.ct_file && !form.ct_preregistered) newRecon.status = 'registering';
      else if (form.ct_file && form.ct_preregistered) newRecon.status = 'processing';
      setReconstructions(prev => [newRecon, ...prev]);
      setShowCreate(false);
      setForm({ patient_id: '', mri_file: null, ct_file: null, ct_preregistered: false });
    } catch (e) {
      alert('Failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setCreating(false);
    }
  };

  const handleSoftDelete = async () => {
    if (!confirmDelete) return;
    setDeleting(true);
    try {
      await softDeleteReconstruction(confirmDelete.id);
      setReconstructions(prev => prev.filter(r => r.id !== confirmDelete.id));
      setConfirmDelete(null);
    } catch (e) {
      alert('Failed to delete: ' + (e.response?.data?.detail || e.message));
    } finally {
      setDeleting(false);
    }
  };

  const completed = reconstructions.filter(r => r.is_complete);
  const inProgress = reconstructions.filter(r => !r.is_complete);

  const SectionHeader = ({ title, count, color }) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
      <span style={{ fontSize: 12, fontWeight: 600, color, textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: mono }}>{title}</span>
      <span style={{ fontSize: 11, color: '#4a5568', fontFamily: mono }}>({count})</span>
      <div style={{ flex: 1, height: 1, background: '#1e2530' }} />
    </div>
  );

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '28px 36px', fontFamily: font, background: '#0a0c10', position: 'relative' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 32 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 600, color: '#e8edf2', letterSpacing: '-0.01em', marginBottom: 4 }}>Electrode Reconstructions</h1>
          <p style={{ fontSize: 12, color: '#4a5568' }}>{reconstructions.length} patient{reconstructions.length !== 1 ? 's' : ''} · de-identified</p>
        </div>
        {canEdit && (
          <button onClick={() => setShowCreate(p => !p)} style={{ padding: '8px 18px', background: showCreate ? '#002233' : '#00d4ff', color: showCreate ? '#00d4ff' : '#0a0c10', border: `1px solid ${showCreate ? '#00d4ff44' : 'transparent'}`, borderRadius: 4, fontSize: 13, fontWeight: 600, fontFamily: font, cursor: 'pointer' }}>
            {showCreate ? '✕ Cancel' : '+ New Reconstruction'}
          </button>
        )}
      </div>

      {/* Create form */}
      {showCreate && (
        <div style={{ background: '#111418', border: '1px solid #1e2530', borderRadius: 6, padding: 20, marginBottom: 28 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: '#e8edf2', marginBottom: 18, letterSpacing: '0.02em', fontFamily: font }}>New Reconstruction</div>
          <form onSubmit={handleCreate}>
            <div style={{ marginBottom: 14 }}>
              <label style={{ display: 'block', fontSize: 13, color: '#c8d0da', marginBottom: 6, fontWeight: 600 }}>Patient ID</label>
              <input value={form.patient_id} onChange={e => setForm(p => ({ ...p, patient_id: e.target.value }))} placeholder="e.g. PT001" required />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 18 }}>
              <div>
                <label style={{ display: 'block', fontSize: 13, color: '#c8d0da', marginBottom: 6, fontWeight: 600 }}>MRI NIfTI (.nii / .nii.gz) *</label>
                <input type="file" accept=".nii,.nii.gz" onChange={e => setForm(p => ({ ...p, mri_file: e.target.files[0] }))} required style={{ padding: '4px 8px', fontSize: 12 }} />
              </div>
              <div>
                <label style={{ display: 'block', fontSize: 13, color: '#c8d0da', marginBottom: 6, fontWeight: 600 }}>CT NIfTI (.nii / .nii.gz) <span style={{ color: '#4a5568', fontWeight: 400 }}>— optional</span></label>
                <input type="file" accept=".nii,.nii.gz" onChange={e => setForm(p => ({ ...p, ct_file: e.target.files[0] }))} style={{ padding: '4px 8px', fontSize: 12 }} />
              </div>
            </div>
            {form.ct_file && (
              <div style={{ marginBottom: 14, display: 'flex', alignItems: 'center', gap: 10 }}>
                <input
                  type="checkbox"
                  id="ct_prereg"
                  checked={form.ct_preregistered}
                  onChange={e => setForm(p => ({ ...p, ct_preregistered: e.target.checked }))}
                  style={{ width: 15, height: 15, cursor: 'pointer', accentColor: '#00d4ff' }}
                />
                <label htmlFor="ct_prereg" style={{ fontSize: 13, color: '#c8d0da', fontWeight: 500, cursor: 'pointer' }}>
                  CT is already co-registered to MRI
                  <span style={{ fontSize: 11, color: '#4a5568', marginLeft: 6 }}>(skips auto-registration)</span>
                </label>
              </div>
            )}
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <button type="submit" disabled={creating} style={{ padding: '8px 20px', background: creating ? '#002233' : '#00d4ff', color: creating ? '#00d4ff' : '#0a0c10', border: 'none', borderRadius: 4, fontSize: 12, fontWeight: 600, fontFamily: font, cursor: creating ? 'not-allowed' : 'pointer' }}>
                {creating ? 'Uploading...' : 'Upload & Process'}
              </button>
              <span style={{ fontSize: 11, color: '#4a5568' }}>Mesh extraction runs in background (~2–5 min)</span>
            </div>
          </form>
        </div>
      )}

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: '#4a5568' }}>Loading...</div>
      ) : reconstructions.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 80, border: '1px dashed #1e2530', borderRadius: 8, color: '#4a5568' }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>🧠</div>
          <div style={{ fontSize: 14, color: '#7a8a99' }}>No reconstructions yet</div>
          {canEdit && <div style={{ fontSize: 12, marginTop: 8 }}>Click "New Reconstruction" to get started</div>}
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 0, alignItems: 'flex-start', minHeight: 200 }}>

          {/* LEFT: In Progress */}
          <div style={{ flex: '0 0 320px', borderRight: '1px solid #1e2530', paddingRight: 28 }}>
            <SectionHeader title="In Progress" count={inProgress.length} color="#ffab40" />
            {inProgress.length === 0 ? (
              <div style={{ padding: '24px 0', textAlign: 'center', color: '#2a3340', fontSize: 12 }}>None in progress</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {inProgress.map(r => <ReconCard key={r.id} recon={r} onSelect={onSelect} canEdit={canEdit} onDelete={setConfirmDelete} />)}
              </div>
            )}
          </div>

          {/* RIGHT: Completed */}
          <div style={{ flex: 1, paddingLeft: 28 }}>
            <SectionHeader title="Completed" count={completed.length} color="#00e676" />
            {completed.length === 0 ? (
              <div style={{ padding: '24px 0', textAlign: 'center', color: '#2a3340', fontSize: 12 }}>No completed reconstructions</div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
                {completed.map(r => <ReconCard key={r.id} recon={r} onSelect={onSelect} canEdit={canEdit} onDelete={setConfirmDelete} />)}
              </div>
            )}
          </div>

        </div>
      )}

      {/* Trash link — bottom left */}
      <div
        onClick={onTrash}
        style={{
          position: 'fixed', bottom: 24, left: 24,
          display: 'flex', alignItems: 'center', gap: 7,
          color: '#7a8a99', fontSize: 15, fontFamily: mono, fontWeight: 600,
          cursor: 'pointer', transition: 'color 0.15s',
        }}
        onMouseEnter={e => e.currentTarget.style.color = '#b0bec5'}
        onMouseLeave={e => e.currentTarget.style.color = '#7a8a99'}
      >
        <span style={{ fontSize: 16 }}>🗑</span>
        <span>Deleted</span>
      </div>

      {/* Soft-delete confirmation dialog */}
      {confirmDelete && (
        <div style={{
          position: 'fixed', inset: 0,
          background: 'rgba(0,0,0,0.75)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 1000,
        }}>
          <div style={{
            background: '#111418', border: '1px solid #2a3340',
            borderRadius: 8, padding: 28, maxWidth: 360, width: '90%',
            fontFamily: font,
          }}>
            <div style={{ fontSize: 18, fontWeight: 600, color: '#e8edf2', marginBottom: 10 }}>
              Move to Deleted?
            </div>
            <div style={{ fontSize: 13, color: '#b0bec5', marginBottom: 6, lineHeight: 1.6 }}>
              <strong style={{ color: '#e8edf2' }}>{confirmDelete.label}</strong>
              <span style={{ color: '#4a5568', fontFamily: mono, fontSize: 11, marginLeft: 8 }}>{confirmDelete.patient_id}</span>
            </div>
            <div style={{ fontSize: 12, color: '#4a5568', marginBottom: 24 }}>
              This reconstruction will be moved to Deleted. It can be permanently deleted from there.
            </div>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setConfirmDelete(null)}
                disabled={deleting}
                style={{ padding: '8px 20px', background: 'transparent', color: '#7a8a99', border: '1px solid #2a3340', borderRadius: 4, fontSize: 13, cursor: 'pointer', fontFamily: font }}
              >
                No, Keep It
              </button>
              <button
                onClick={handleSoftDelete}
                disabled={deleting}
                style={{ padding: '8px 20px', background: '#1a1010', color: '#ff5252', border: '1px solid #ff525444', borderRadius: 4, fontSize: 13, fontWeight: 600, cursor: deleting ? 'not-allowed' : 'pointer', fontFamily: font, opacity: deleting ? 0.6 : 1 }}
              >
                {deleting ? 'Moving...' : 'Yes, Move to Deleted'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
