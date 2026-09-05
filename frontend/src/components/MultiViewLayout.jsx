import React, { useState, useCallback, useRef } from 'react';
import SliceViewer from './SliceViewer';
import FusionSliceViewer from './FusionSliceViewer';
import ScanLayerBar from './ScanLayerBar';
import { useAppStore } from '../store';
import { uploadReconstructionFiles, confirmRegistration, preciseReregister, selectRegistrationCandidate, getReconstruction } from '../api';

const BASE_VIEWS = [
  { id: '3d',       label: '3D',       icon: '⬡' },
  { id: 'sagittal', label: 'Sagittal', icon: '◧' },
  { id: 'axial',    label: 'Axial',    icon: '⬒' },
  { id: 'coronal',  label: 'Coronal',  icon: '◨' },
];

const FUSION_VIEW = { id: 'fusion', label: 'Fusion', icon: '⧉' };

const AXIS_COLORS = {
  sagittal: '#ff6b6b',
  axial:    '#81c784',
  coronal:  '#4fc3f7',
  fusion:   '#ffab40',
};

export default function MultiViewLayout({ reconId, viewer3D, shareToken }) {
  const [activeView, setActiveView] = useState('3d');
  const { reconstruction, setReconstruction } = useAppStore();
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [reRegBusy, setReRegBusy] = useState(false);
  const [reRegError, setReRegError] = useState('');
  const [previewCandidate, setPreviewCandidate] = useState(0);
  const [selectBusy, setSelectBusy] = useState(false);

  // Fusion view is only meaningful when a CT is registered to the MRI.
  const hasFusion = !!reconstruction?.has_ct && !!reconstruction?.has_registration;
  const VIEWS = hasFusion ? [FUSION_VIEW, ...BASE_VIEWS] : BASE_VIEWS;
  const regConfirmed = !!reconstruction?.registration_confirmed;
  // false = the stored transform came from the fast multithreaded path (unverified);
  // true/undefined = deterministic or legacy.
  const regDeterministic = reconstruction?.registration_deterministic !== false;
  // A precise re-run that found >1 distinct MI basin leaves candidates for the
  // reviewer to pick between (no metric can auto-select the correct one).
  const candidates = reconstruction?.registration_candidates || [];
  const awaitingBasin = !!reconstruction?.awaiting_basin_selection && candidates.length > 1;

  const handleConfirmRegistration = useCallback(async (value) => {
    if (!reconstruction || confirmBusy) return;
    setConfirmBusy(true);
    try {
      await confirmRegistration(reconstruction.id, value);
      setReconstruction({ ...reconstruction, registration_confirmed: value });
    } catch (e) {
      // no-op; button stays in prior state
    } finally {
      setConfirmBusy(false);
    }
  }, [reconstruction, confirmBusy, setReconstruction]);

  // "Re-run precise": jittered multi-start that enumerates the distinct MI basins.
  // Kicks off a background job, polls until "ready", then refreshes the store. If
  // >1 basin was found the recon comes back with awaiting_basin_selection and the
  // picker appears; a single basin is applied directly (updated_at bump reloads slices).
  const handleReregister = useCallback(async () => {
    if (!reconstruction || reRegBusy) return;
    const id = reconstruction.id;
    setReRegBusy(true);
    setReRegError('');
    setPreviewCandidate(0);
    try {
      await preciseReregister(id);
      for (let i = 0; i < 400; i++) {                 // ~20 min cap at 3s/poll
        await new Promise(r => setTimeout(r, 3000));
        const { data } = await getReconstruction(id);
        if (data && data.status !== 'registering') {
          setReconstruction(data);
          return;
        }
      }
      setReRegError('Timed out waiting for re-registration.');
    } catch (e) {
      setReRegError('Re-registration failed to start.');
    } finally {
      setReRegBusy(false);
    }
  }, [reconstruction, reRegBusy, setReconstruction]);

  // Apply the reviewer-chosen candidate basin, then refresh the recon (clears
  // awaiting_basin_selection, bumps updated_at → fusion viewer shows the applied one).
  const handleSelectCandidate = useCallback(async (idx) => {
    if (!reconstruction || selectBusy) return;
    const id = reconstruction.id;
    setSelectBusy(true);
    try {
      await selectRegistrationCandidate(id, idx);
      const { data } = await getReconstruction(id);
      if (data) setReconstruction(data);
    } catch (e) {
      // leave the picker up on failure
    } finally {
      setSelectBusy(false);
    }
  }, [reconstruction, selectBusy, setReconstruction]);

  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const mriRef = useRef(null);
  const ctRef  = useRef(null);
  const [mriModality, setMriModality] = useState('t1');

  const handleUploadFiles = useCallback(async () => {
    const mriFile = mriRef.current?.files?.[0];
    const ctFile  = ctRef.current?.files?.[0];
    if (!mriFile) return;
    setUploading(true);
    setUploadError('');
    try {
      const fd = new FormData();
      fd.append('mri_file', mriFile);
      fd.append('mri_modality', mriModality);
      if (ctFile) fd.append('ct_file', ctFile);
      await uploadReconstructionFiles(reconId, fd);
      setReconstruction({ ...reconstruction, has_mri: true, status: 'processing' });
    } catch (e) {
      setUploadError(e?.response?.data?.detail || e.message || 'Upload failed');
    } finally {
      setUploading(false);
    }
  }, [reconId, reconstruction, setReconstruction, mriModality]);

  // Shared slice positions: { axis -> { idx, count } }
  const [slicePositions, setSlicePositions] = useState({
    sagittal: { idx: 0, count: 1 },
    axial:    { idx: 0, count: 1 },
    coronal:  { idx: 0, count: 1 },
  });

  const handleSliceChange = useCallback((axis, idx, count) => {
    setSlicePositions(prev => ({
      ...prev,
      [axis]: { idx, count },
    }));
  }, []);

  // Locator config for each axis:
  // axial    → show coronal thumbnail, horizontal line at axial Z position
  // sagittal → show coronal thumbnail, vertical line at sagittal X position
  // coronal  → show sagittal thumbnail, vertical line at coronal Y position
  const locators = {
    axial: {
      refAxis: 'coronal',
      lineType: 'horizontal',
      // top=superior=high axial idx, so fraction from top = 1 - idx/(count-1)
      fraction: slicePositions.axial.count > 1
        ? 1 - slicePositions.axial.idx / (slicePositions.axial.count - 1)
        : 0.5,
    },
    sagittal: {
      refAxis: 'coronal',
      lineType: 'vertical',
      // coronal display: left=right brain, right=left brain (fliplr)
      // sagittal idx 0=left brain=image right, so fraction = 1 - idx/(count-1)
      fraction: slicePositions.sagittal.count > 1
        ? 1 - slicePositions.sagittal.idx / (slicePositions.sagittal.count - 1)
        : 0.5,
    },
    coronal: {
      refAxis: 'sagittal',
      lineType: 'vertical',
      // sagittal display: left=anterior, right=posterior
      // coronal idx 0=posterior=image right, so fraction = 1 - idx/(count-1)
      fraction: slicePositions.coronal.count > 1
        ? 1 - slicePositions.coronal.idx / (slicePositions.coronal.count - 1)
        : 0.5,
    },
  };

  return (
    <div style={{ flex: 1, display: 'flex', minWidth: 0, minHeight: 0, overflow: 'hidden' }}>

      {/* Left column: view selectors */}
      <div style={{
        flex: '0 0 120px',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        padding: '8px 6px',
        background: '#0a0c10',
        borderRight: '1px solid #1e2530',
        overflowY: 'auto',
      }}>
        {VIEWS.map(view => {
          const isActive = activeView === view.id;
          const accentColor = AXIS_COLORS[view.id] || '#ffdd00';
          return (
            <button
              key={view.id}
              onClick={() => setActiveView(view.id)}
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'stretch',
                padding: 0,
                border: `2px solid ${isActive ? accentColor : '#1e2530'}`,
                borderRadius: 5,
                cursor: 'pointer',
                background: isActive ? '#0d1015' : '#0a0c10',
                overflow: 'hidden',
                boxShadow: isActive ? `0 0 8px ${accentColor}44` : 'none',
                transition: 'all 0.15s',
              }}
              onMouseEnter={e => { if (!isActive) e.currentTarget.style.borderColor = accentColor + '66'; }}
              onMouseLeave={e => { if (!isActive) e.currentTarget.style.borderColor = '#1e2530'; }}
            >
              <div style={{ height: 70, background: '#000', overflow: 'hidden', position: 'relative' }}>
                {(view.id === '3d' || view.id === 'fusion') ? (
                  <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: view.id === 'fusion' ? (isActive ? accentColor : '#5a4420') : '#2a3340', fontSize: 28 }}>
                    {view.icon}
                  </div>
                ) : (
                  <SliceViewer reconId={reconId} axis={view.id} isThumbnail />
                )}
                {view.id === 'fusion' && !regConfirmed && (
                  <div style={{ position: 'absolute', top: 3, right: 3, width: 8, height: 8, borderRadius: '50%', background: '#ffab40', boxShadow: '0 0 4px #ffab40' }} title="Registration not yet reviewed" />
                )}
              </div>
              <div style={{
                padding: '5px 0',
                textAlign: 'center',
                fontSize: 11,
                fontWeight: 600,
                fontFamily: 'IBM Plex Mono, monospace',
                color: isActive ? accentColor : '#4a5568',
                background: isActive ? '#0d1015' : 'transparent',
                letterSpacing: '0.04em',
              }}>
                {view.label}
              </div>
            </button>
          );
        })}
      </div>

      {/* Main view area */}
      <div style={{ flex: 1, position: 'relative', minWidth: 0, background: '#000' }}>
        <div style={{ position: 'absolute', inset: 0, display: activeView === '3d' ? 'block' : 'none' }}>
          {viewer3D}
        </div>

        {hasFusion && (
          <div style={{ position: 'absolute', inset: 0, display: activeView === 'fusion' ? 'flex' : 'none', flexDirection: 'column' }}>
            {awaitingBasin ? (
              /* Basin picker: a precise re-run found >1 distinct registration; the
                 reviewer compares each in the fusion viewer and applies one. */
              <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', background: '#101a2a', borderBottom: '1px solid #4fc3f733' }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#4fc3f7', fontFamily: 'IBM Plex Sans, sans-serif' }}>
                  ⑂ {candidates.length} registrations found
                </span>
                <span style={{ fontSize: 11, color: '#7a8a99', fontFamily: 'IBM Plex Sans, sans-serif' }}>
                  Toggle and pick the one where skull / ventricle / midline edges line up:
                </span>
                {candidates.map((c, i) => (
                  <button
                    key={i}
                    onClick={() => setPreviewCandidate(i)}
                    title={`${c.size} start(s), spread ${c.spread_mm} mm`}
                    style={{ fontSize: 12, fontWeight: 600, padding: '5px 12px', borderRadius: 4, cursor: 'pointer', fontFamily: 'IBM Plex Sans, sans-serif',
                      background: previewCandidate === i ? '#4fc3f7' : 'transparent',
                      color: previewCandidate === i ? '#06121f' : '#4fc3f7',
                      border: '1px solid #4fc3f788' }}
                  >
                    Option {i + 1}
                  </button>
                ))}
                <span style={{ flex: 1 }} />
                <button
                  onClick={() => handleSelectCandidate(previewCandidate)}
                  disabled={selectBusy}
                  style={{ fontSize: 12, fontWeight: 600, padding: '5px 14px', borderRadius: 4, cursor: 'pointer', background: '#0d2a1a', color: '#00e676', border: '1px solid #00e67655', fontFamily: 'IBM Plex Sans, sans-serif', opacity: selectBusy ? 0.6 : 1 }}
                >
                  {selectBusy ? 'Applying…' : `✓ Use Option ${previewCandidate + 1}`}
                </button>
              </div>
            ) : (
            /* Registration review / confirm bar */
            <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 12, padding: '8px 14px', background: regConfirmed ? '#0d2a1a' : '#1a1000', borderBottom: `1px solid ${regConfirmed ? '#00e67633' : '#ffab4033'}` }}>
              <span style={{ fontSize: 12, fontFamily: 'IBM Plex Sans, sans-serif', color: regConfirmed ? '#00e676' : '#ffab40', fontWeight: 600 }}>
                {regConfirmed ? '✓ Registration reviewed & confirmed' : '⚠ Registration not yet reviewed'}
              </span>
              <span style={{ fontSize: 11, color: '#7a8a99', fontFamily: 'IBM Plex Sans, sans-serif', flex: 1 }}>
                {reRegError
                  ? <span style={{ color: '#ff5252' }}>{reRegError}</span>
                  : (!regConfirmed && !regDeterministic)
                    ? 'Fast registration — review carefully. Sweep the MRI↔CT blend; if edges jump, re-run precise.'
                    : 'Sweep the MRI↔CT blend and check that skull, ventricle, and midline edges stay aligned.'}
              </span>
              {reRegBusy ? (
                <span style={{ fontSize: 12, fontWeight: 600, color: '#ffab40', fontFamily: 'IBM Plex Sans, sans-serif' }}>
                  ⏳ Exploring registrations (multi-start)… ~7–8 min
                </span>
              ) : regConfirmed ? (
                <button
                  onClick={() => handleConfirmRegistration(false)}
                  disabled={confirmBusy}
                  style={{ fontSize: 11, padding: '5px 12px', borderRadius: 4, cursor: 'pointer', background: 'transparent', color: '#7a8a99', border: '1px solid #2a3340', fontFamily: 'IBM Plex Sans, sans-serif' }}
                >
                  Un-confirm
                </button>
              ) : (
                <>
                  <button
                    onClick={handleReregister}
                    disabled={confirmBusy}
                    title="Re-run registration with a jittered multi-start and pick between the distinct results (~7–8 min)"
                    style={{ fontSize: 12, fontWeight: 600, padding: '5px 14px', borderRadius: 4, cursor: 'pointer', background: 'transparent', color: '#ffab40', border: '1px solid #ffab4066', fontFamily: 'IBM Plex Sans, sans-serif' }}
                  >
                    ↻ Looks off — Re-run precise
                  </button>
                  <button
                    onClick={() => handleConfirmRegistration(true)}
                    disabled={confirmBusy}
                    style={{ fontSize: 12, fontWeight: 600, padding: '5px 14px', borderRadius: 4, cursor: 'pointer', background: '#0d2a1a', color: '#00e676', border: '1px solid #00e67655', fontFamily: 'IBM Plex Sans, sans-serif', opacity: confirmBusy ? 0.6 : 1 }}
                  >
                    ✓ Looks correct — Confirm
                  </button>
                </>
              )}
            </div>
            )}
            <div style={{ flex: 1, minHeight: 0 }}>
              {activeView === 'fusion' && <FusionSliceViewer reconId={reconId} version={reconstruction?.updated_at} candidate={awaitingBasin ? previewCandidate : undefined} />}
            </div>
          </div>
        )}

        <div style={{
          position: 'absolute', inset: 0,
          display: ['sagittal', 'axial', 'coronal'].includes(activeView) ? 'flex' : 'none',
          flexDirection: 'column',
        }}>
          {reconstruction?.has_mri !== false && (
            <ScanLayerBar reconId={reconId} shareToken={shareToken} />
          )}
          <div style={{ flex: 1, position: 'relative', minWidth: 0, minHeight: 0 }}>
        {['sagittal', 'axial', 'coronal'].map(ax => (
          <div key={ax} style={{ position: 'absolute', inset: 0, display: activeView === ax ? 'block' : 'none' }}>
            {reconstruction?.has_mri === false ? (
              <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#0a0c10' }}>
                {ax === 'axial' && (
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14, padding: 32, background: '#111418', border: '1px solid #1e2530', borderRadius: 8, maxWidth: 360 }}>
                    <span style={{ fontSize: 13, color: '#7a8a99', fontFamily: 'IBM Plex Sans, sans-serif' }}>Upload MRI to enable slice viewing</span>
                    <label style={{ width: '100%' }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <span style={{ fontSize: 11, color: '#4a5568', fontFamily: 'IBM Plex Mono, monospace' }}>MRI (.nii.gz) *</span>
                        <select
                          value={mriModality}
                          onChange={e => setMriModality(e.target.value)}
                          title="MRI contrast — used to select the correct skull-stripping model"
                          style={{ fontSize: 11, background: '#0a0c10', color: '#e8edf2', border: '1px solid #2a3340', borderRadius: 3, fontFamily: 'IBM Plex Mono, monospace' }}
                        >
                          <option value="t1">T1</option>
                          <option value="t2">T2</option>
                        </select>
                      </div>
                      <input ref={mriRef} type="file" accept=".nii.gz,.nii" style={{ display: 'block', marginTop: 4, width: '100%', boxSizing: 'border-box', fontSize: 11, color: '#e8edf2', fontFamily: 'IBM Plex Mono, monospace' }} />
                    </label>
                    <label style={{ width: '100%' }}>
                      <span style={{ fontSize: 11, color: '#4a5568', fontFamily: 'IBM Plex Mono, monospace' }}>CT (.nii.gz) — optional, triggers coregistration</span>
                      <input ref={ctRef} type="file" accept=".nii.gz,.nii" style={{ display: 'block', marginTop: 4, width: '100%', fontSize: 11, color: '#e8edf2', fontFamily: 'IBM Plex Mono, monospace' }} />
                    </label>
                    {uploadError && <span style={{ fontSize: 11, color: '#ff5252', fontFamily: 'IBM Plex Mono, monospace' }}>{uploadError}</span>}
                    <button
                      onClick={handleUploadFiles}
                      disabled={uploading}
                      style={{ padding: '6px 20px', background: '#002233', color: '#00d4ff', border: '1px solid #00d4ff44', borderRadius: 4, fontSize: 12, fontFamily: 'IBM Plex Sans, sans-serif', fontWeight: 600, cursor: uploading ? 'default' : 'pointer', opacity: uploading ? 0.6 : 1 }}
                    >
                      {uploading ? 'Uploading…' : 'Upload & Process'}
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <SliceViewer
                reconId={reconId}
                axis={ax}
                onSliceChange={(idx, count) => handleSliceChange(ax, idx, count)}
                locator={locators[ax]}
              />
            )}
          </div>
        ))}
          </div>
        </div>
      </div>

    </div>
  );
}
