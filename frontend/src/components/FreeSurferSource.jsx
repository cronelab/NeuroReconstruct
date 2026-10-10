import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useAppStore } from '../store';
import {
  getFreeSurfer, uploadFreeSurfer, deleteFreeSurfer, setParcellationSource, getReconstruction,
} from '../api';

/**
 * Which parcellation this reconstruction uses, and the FreeSurfer upload.
 *
 * FAST is the in-app pipeline: a DKT network labels the T1 and a display
 * surface is built from those labels. FREESURFER is gold-standard recon-all (or
 * FastSurfer) output, run outside the app and uploaded as a zipped subject
 * folder. The backend swaps the active label volume, so structures, contact
 * labels, the slice overlay and the MNI export CSV all follow the toggle; the
 * cortical surface becomes FreeSurfer's real lh/rh pial.
 *
 * Lives at the top of StructurePanel, so it appears wherever that does.
 */

const POLL_MS = 5000;
const MONO = 'IBM Plex Mono, monospace';

function shortVersion(info) {
  if (!info) return '';
  if (info.engine === 'fastsurfer') return info.engine_version || 'FastSurfer';
  const m = /(\d+\.\d+\.\d+)/.exec(info.freesurfer_version || '');
  return `FreeSurfer ${m ? m[1] : ''}`.trim();
}

function describe(info) {
  if (!info) return '';
  const parts = [shortVersion(info)];
  if (info.engine && info.engine !== 'fastsurfer') parts.push(info.engine);
  const when = (info.processed_at || info.imported_at || '').slice(0, 10);
  if (when) parts.push(when);
  if (info.alignment) {
    parts.push(info.alignment.registered
      ? `registered to this MRI (r=${info.alignment.ncc?.toFixed(2)})`
      : `r=${info.alignment.ncc?.toFixed(2)}`);
  }
  return parts.filter(Boolean).join(' · ');
}

export default function FreeSurferSource({ onSourceChanged }) {
  const {
    activeReconId, reconstruction, setReconstruction, user,
    parcellation, setParcellation,
    setStructuresData, setCorticalData, setCorticalAtlas,
  } = useAppStore();
  const canEdit = user && (user.role === 'editor' || user.role === 'admin');
  const reconId = activeReconId ?? reconstruction?.id;
  const fileRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(null);   // 0..1, or null if unknown
  const [error, setError] = useState('');

  // Seed from the reconstruction payload (which share-link viewers also get),
  // then refresh from the endpoint when signed in.
  useEffect(() => {
    if (!reconId) return;
    if (reconstruction?.id === reconId && reconstruction.parcellation_source) {
      setParcellation({
        parcellation_source: reconstruction.parcellation_source,
        freesurfer: reconstruction.freesurfer ?? null,
      }, reconId);
    }
  }, [reconId, reconstruction, setParcellation]);

  const refresh = useCallback(async () => {
    if (!reconId || !user) return null;
    try {
      const { data } = await getFreeSurfer(reconId);
      setParcellation(data, reconId);
      return data;
    } catch (e) {
      return null;        // 409 before the brain mesh exists: nothing to show yet
    }
  }, [reconId, user, setParcellation]);

  useEffect(() => { refresh(); }, [refresh]);

  // Everything built from the label volume is now stale on the client too.
  const afterSourceChange = useCallback(async () => {
    setStructuresData(null, reconId);
    setCorticalData(null, reconId);
    setCorticalAtlas('dkt');
    onSourceChanged?.();
    try {
      const { data } = await getReconstruction(reconId);
      if (useAppStore.getState().reconstruction?.id === reconId) setReconstruction(data);
    } catch (e) { /* the export badge just refreshes later */ }
  }, [reconId, setStructuresData, setCorticalData, setCorticalAtlas, onSourceChanged, setReconstruction]);

  const fs = parcellation?.freesurfer;
  const source = parcellation?.parcellation_source || 'fast';
  const processing = fs?.state === 'processing';
  const fsReady = !!fs?.ready;

  // Poll while an import runs; when it lands (it activates itself) refresh the
  // views that were built from the previous parcellation.
  const wasProcessing = useRef(false);
  useEffect(() => {
    if (!processing) {
      if (wasProcessing.current) {
        wasProcessing.current = false;
        if (fs?.state === 'ready') afterSourceChange();
      }
      return undefined;
    }
    wasProcessing.current = true;
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [processing, fs?.state, refresh, afterSourceChange]);

  const handleUpload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setUploading(true);
    setError('');
    setProgress(null);
    try {
      const { data } = await uploadFreeSurfer(reconId, file, setProgress);
      setParcellation(data, reconId);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Upload failed');
    } finally {
      if (fileRef.current) fileRef.current.value = '';
      setBusy(false);
      setUploading(false);
      setProgress(null);
    }
  };

  const handleSource = async (next) => {
    if (next === source || busy) return;
    setBusy(true);
    setError('');
    try {
      const { data } = await setParcellationSource(reconId, next);
      setParcellation(data, reconId);
      await afterSourceChange();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Switch failed');
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async () => {
    if (busy || !window.confirm('Remove the uploaded FreeSurfer outputs and go back to the fast parcellation?')) return;
    setBusy(true);
    setError('');
    try {
      const wasActive = source === 'freesurfer';
      const { data } = await deleteFreeSurfer(reconId);
      setParcellation(data, reconId);
      if (wasActive) await afterSourceChange();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Remove failed');
    } finally {
      setBusy(false);
    }
  };

  if (!reconId) return null;
  // Viewers with nothing imported: the fast source is implied, say nothing.
  if (!canEdit && !fs) return null;

  const btn = (active, disabled) => ({
    flex: 1, fontSize: 11, padding: '3px 6px', borderRadius: 4, fontFamily: MONO,
    cursor: disabled ? 'default' : 'pointer',
    background: active ? '#16324a' : 'none',
    border: `1px solid ${active ? '#74C0FC' : '#1e2530'}`,
    color: disabled ? '#3d4757' : active ? '#cfe6ff' : '#7a8a99',
  });

  return (
    <div style={{ marginBottom: 8, paddingBottom: 8, borderBottom: '1px solid #1a1e24' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 11, color: '#7a8a99', fontFamily: MONO, flexShrink: 0 }}
          title="Which parcellation the structures, contact labels and cortical surface come from">
          Source
        </span>
        <button
          onClick={() => canEdit && handleSource('fast')}
          disabled={busy || processing || !canEdit}
          title="In-app DKT network: fast, display-quality surface"
          style={btn(source === 'fast', busy || processing || !canEdit)}>
          Fast
        </button>
        <button
          onClick={() => canEdit && fsReady && handleSource('freesurfer')}
          disabled={busy || processing || !canEdit || !fsReady}
          title={fsReady ? 'Uploaded FreeSurfer outputs: real pial surfaces and recon-all parcellation'
            : 'Upload FreeSurfer outputs to enable'}
          style={btn(source === 'freesurfer', busy || processing || !canEdit || !fsReady)}>
          FreeSurfer
        </button>
      </div>

      {processing && (
        <div style={{ fontSize: 11, color: '#74C0FC', fontFamily: MONO, marginTop: 6, animation: 'pulse 1.6s infinite' }}>
          Importing FreeSurfer outputs…
        </div>
      )}
      {!processing && fsReady && (
        <div style={{ fontSize: 10.5, color: '#7a8a99', fontFamily: MONO, marginTop: 6, lineHeight: 1.35 }}
          title={fs.info?.freesurfer_version || ''}>
          {describe(fs.info)}
        </div>
      )}
      {!processing && fs?.state === 'error' && fs.message && (
        <div style={{ fontSize: 11, color: '#ff8787', fontFamily: MONO, marginTop: 6, lineHeight: 1.35 }}>
          Import failed: {fs.message}
        </div>
      )}
      {error && (
        <div style={{ fontSize: 11, color: '#ff8787', fontFamily: MONO, marginTop: 6, lineHeight: 1.35 }}>{error}</div>
      )}

      {canEdit && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
          <input ref={fileRef} type="file" accept=".zip,application/zip" style={{ display: 'none' }}
            onChange={handleUpload} />
          <button
            onClick={() => fileRef.current?.click()}
            disabled={busy || processing}
            title="A zipped recon-all or FastSurfer subject folder (e.g. from `fspipe export`)"
            style={{ fontSize: 11, color: (busy || processing) ? '#4a5568' : '#74C0FC', background: 'none',
              border: '1px solid #1e2530', borderRadius: 4, padding: '3px 8px', fontFamily: MONO,
              cursor: (busy || processing) ? 'default' : 'pointer' }}>
            {uploading
              ? (progress != null ? `Uploading ${Math.round(progress * 100)}%` : 'Uploading…')
              : fs ? '⇪ Replace FreeSurfer' : '⇪ Upload FreeSurfer (.zip)'}
          </button>
          {fs && !processing && (
            <button onClick={handleRemove} disabled={busy}
              style={{ fontSize: 11, color: '#7a8a99', background: 'none', border: 'none',
                fontFamily: MONO, cursor: busy ? 'default' : 'pointer', padding: 0 }}>
              Remove
            </button>
          )}
        </div>
      )}
    </div>
  );
}
