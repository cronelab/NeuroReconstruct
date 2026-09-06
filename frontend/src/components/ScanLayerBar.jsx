import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useAppStore } from '../store';
import { listSecondaryScans, uploadSecondaryScan, deleteSecondaryScan } from '../api';
import { inferModality, scanLabelOrDefault, SCAN_TYPE_PLACEHOLDER } from '../scanTypes';

/**
 * Picks which scans the 2D slice views show, as panes side by side.
 *
 * A reconstruction's PRIMARY MRI (normally T1) is the only scan the pipeline
 * reads — parcellation, mesh, CT coregistration and MNI export all run off it.
 * SECONDARY scans (T2, FLAIR, ...) exist only so structures that read better on
 * another contrast can be looked at here. Each is registered to the primary and
 * stored resampled into its voxel grid, so every layer shares one slice index:
 * the panes scroll together, and the structure overlay and electrode contacts
 * land in the same place in all of them. Comparing them in one glance is also
 * the alignment check — anatomy that does not line up across the panes means
 * that secondary's registration is off.
 *
 * Selection is multiple. At least one pane always stays on.
 */

const STATUS_TEXT = {
  pending:     'queued',
  registering: 'registering…',
  error:       'failed',
};

// Fresh uploads register in the background; poll until they settle.
const POLL_MS = 5000;

export default function ScanLayerBar({ reconId, shareToken }) {
  const {
    secondaryScans, setSecondaryScans,
    visibleLayers, toggleLayer,
    user,
  } = useAppStore();
  const canEdit = user && (user.role === 'editor' || user.role === 'admin');

  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [scanType, setScanType] = useState('');
  const fileRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const { data } = await listSecondaryScans(reconId, shareToken);
      setSecondaryScans(data || []);
      return data || [];
    } catch (e) {
      return null;
    }
  }, [reconId, shareToken, setSecondaryScans]);

  useEffect(() => { refresh(); }, [refresh]);

  // Poll only while something is still being registered.
  const settling = secondaryScans.some(s => s.status === 'pending' || s.status === 'registering');
  useEffect(() => {
    if (!settling) return;
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [settling, refresh]);

  const handleAdd = useCallback(async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setError('');
    try {
      const label = scanLabelOrDefault(scanType);
      await uploadSecondaryScan(reconId, file, { label, modality: inferModality(label) });
      if (fileRef.current) fileRef.current.value = '';
      setScanType('');
      setAdding(false);
      await refresh();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Upload failed');
    } finally {
      setBusy(false);
    }
  }, [reconId, scanType, refresh]);

  const handleDelete = useCallback(async (scanId) => {
    setBusy(true);
    try {
      await deleteSecondaryScan(reconId, scanId);
      // refresh() -> setSecondaryScans prunes the deleted layer from view.
      await refresh();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Delete failed');
    } finally {
      setBusy(false);
    }
  }, [reconId, refresh]);

  // Nothing to compare and nothing the viewer can add: stay out of the way.
  if (!secondaryScans.length && !canEdit) return null;

  // Turning off the only pane showing would leave nothing to look at, so the
  // store refuses it; say so in the tooltip rather than letting the click
  // silently do nothing.
  const onlyOne = (key) => visibleLayers.length === 1 && visibleLayers[0] === key;

  const chip = (active, disabled) => ({
    fontSize: 11,
    fontWeight: 600,
    fontFamily: 'IBM Plex Mono, monospace',
    letterSpacing: '0.03em',
    padding: '3px 10px',
    borderRadius: 3,
    cursor: disabled ? 'default' : 'pointer',
    background: active ? '#00d4ff' : 'transparent',
    color: active ? '#06121f' : (disabled ? '#3d4855' : '#7a8a99'),
    border: `1px solid ${active ? '#00d4ff' : '#2a3340'}`,
    opacity: disabled ? 0.6 : 1,
  });

  return (
    <div style={{
      flexShrink: 0,
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      padding: '5px 10px',
      background: '#0a0c10',
      borderBottom: '1px solid #1e2530',
      flexWrap: 'wrap',
    }}>
      <span style={{ fontSize: 10, color: '#4a5568', fontFamily: 'IBM Plex Mono, monospace', letterSpacing: '0.06em' }}>
        SHOW
      </span>

      <button
        onClick={() => toggleLayer('primary')}
        title={onlyOne('primary')
          ? 'Primary MRI — the last pane showing, so it cannot be turned off'
          : 'Primary MRI — the scan the parcellation and coregistration are built on'}
        style={chip(visibleLayers.includes('primary'), false)}
      >
        Primary
      </button>

      {secondaryScans.map(scan => {
        const active = visibleLayers.includes(scan.id);
        return (
          <span key={scan.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
            <button
              onClick={() => scan.ready && toggleLayer(scan.id)}
              disabled={!scan.ready}
              title={scan.error || (onlyOne(scan.id)
                ? `${scan.label} — the last pane showing, so it cannot be turned off`
                : scan.filename)}
              style={chip(active, !scan.ready)}
            >
              {scan.label}
              {!scan.ready && (
                <span style={{ marginLeft: 5, fontWeight: 400, color: scan.status === 'error' ? '#ff5252' : '#4a5568' }}>
                  {STATUS_TEXT[scan.status] || scan.status}
                </span>
              )}
            </button>
            {canEdit && (
              <button
                onClick={() => handleDelete(scan.id)}
                disabled={busy}
                title={`Remove ${scan.label}`}
                style={{ fontSize: 11, lineHeight: 1, padding: '2px 4px', background: 'transparent', color: '#3d4855', border: 'none', cursor: 'pointer' }}
              >
                ×
              </button>
            )}
          </span>
        );
      })}

      {canEdit && !adding && (
        <button
          onClick={() => { setAdding(true); setError(''); }}
          title="Add a T2 / FLAIR scan as an extra base layer (does not affect the reconstruction)"
          style={{ fontSize: 11, fontFamily: 'IBM Plex Mono, monospace', padding: '3px 8px', borderRadius: 3, background: 'transparent', color: '#4a5568', border: '1px dashed #2a3340', cursor: 'pointer' }}
        >
          + scan
        </button>
      )}

      {canEdit && adding && (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <input
            type="text"
            value={scanType}
            onChange={e => setScanType(e.target.value)}
            placeholder={SCAN_TYPE_PLACEHOLDER}
            title="What to call this scan in this bar"
            maxLength={64}
            // width is explicit because index.css sets width:100% on every input.
            style={{ width: 110, flex: 'none', fontSize: 11, padding: '3px 8px', background: '#0a0c10', color: '#e8edf2', border: '1px solid #2a3340', borderRadius: 3, fontFamily: 'IBM Plex Mono, monospace' }}
          />
          <input
            ref={fileRef}
            type="file"
            accept=".nii,.nii.gz"
            style={{ fontSize: 11, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace', maxWidth: 220 }}
          />
          <button
            onClick={handleAdd}
            disabled={busy}
            style={{ fontSize: 11, fontWeight: 600, padding: '3px 10px', borderRadius: 3, background: '#002233', color: '#00d4ff', border: '1px solid #00d4ff44', cursor: busy ? 'default' : 'pointer', fontFamily: 'IBM Plex Sans, sans-serif' }}
          >
            {busy ? 'Uploading…' : 'Register'}
          </button>
          <button
            onClick={() => { setAdding(false); setError(''); setScanType(''); }}
            style={{ fontSize: 11, padding: '3px 8px', borderRadius: 3, background: 'transparent', color: '#4a5568', border: '1px solid #2a3340', cursor: 'pointer', fontFamily: 'IBM Plex Sans, sans-serif' }}
          >
            Cancel
          </button>
        </span>
      )}

      {error && (
        <span style={{ fontSize: 11, color: '#ff5252', fontFamily: 'IBM Plex Mono, monospace' }}>{error}</span>
      )}
      {settling && (
        <span style={{ fontSize: 10, color: '#4a5568', fontFamily: 'IBM Plex Mono, monospace' }}>
          registering to primary — a few minutes
        </span>
      )}
      {visibleLayers.length > 1 && (
        <span style={{ fontSize: 10, color: '#4a5568', fontFamily: 'IBM Plex Mono, monospace' }}>
          {visibleLayers.length} panes · scroll together
        </span>
      )}
    </div>
  );
}
