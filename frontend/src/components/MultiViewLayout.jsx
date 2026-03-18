import React, { useState, useCallback, useRef } from 'react';
import SliceViewer from './SliceViewer';
import { useAppStore } from '../store';
import { uploadReconstructionFiles } from '../api';

const VIEWS = [
  { id: '3d',       label: '3D',       icon: '⬡' },
  { id: 'sagittal', label: 'Sagittal', icon: '◧' },
  { id: 'axial',    label: 'Axial',    icon: '⬒' },
  { id: 'coronal',  label: 'Coronal',  icon: '◨' },
];

const AXIS_COLORS = {
  sagittal: '#ff6b6b',
  axial:    '#81c784',
  coronal:  '#4fc3f7',
};

export default function MultiViewLayout({ reconId, viewer3D }) {
  const [activeView, setActiveView] = useState('3d');
  const { reconstruction, setReconstruction } = useAppStore();
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const mriRef = useRef(null);
  const ctRef  = useRef(null);

  const handleUploadFiles = useCallback(async () => {
    const mriFile = mriRef.current?.files?.[0];
    const ctFile  = ctRef.current?.files?.[0];
    if (!mriFile) return;
    setUploading(true);
    setUploadError('');
    try {
      const fd = new FormData();
      fd.append('mri_file', mriFile);
      if (ctFile) fd.append('ct_file', ctFile);
      await uploadReconstructionFiles(reconId, fd);
      setReconstruction({ ...reconstruction, has_mri: true, status: 'processing' });
    } catch (e) {
      setUploadError(e?.response?.data?.detail || e.message || 'Upload failed');
    } finally {
      setUploading(false);
    }
  }, [reconId, reconstruction, setReconstruction]);

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
                {view.id === '3d' ? (
                  <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#2a3340', fontSize: 28 }}>
                    {view.icon}
                  </div>
                ) : (
                  <SliceViewer reconId={reconId} axis={view.id} isThumbnail />
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

        {['sagittal', 'axial', 'coronal'].map(ax => (
          <div key={ax} style={{ position: 'absolute', inset: 0, display: activeView === ax ? 'block' : 'none' }}>
            {reconstruction?.has_mri === false ? (
              <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#0a0c10' }}>
                {ax === 'axial' && (
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14, padding: 32, background: '#111418', border: '1px solid #1e2530', borderRadius: 8, maxWidth: 360 }}>
                    <span style={{ fontSize: 13, color: '#7a8a99', fontFamily: 'IBM Plex Sans, sans-serif' }}>Upload MRI to enable slice viewing</span>
                    <label style={{ width: '100%' }}>
                      <span style={{ fontSize: 11, color: '#4a5568', fontFamily: 'IBM Plex Mono, monospace' }}>MRI (.nii.gz) *</span>
                      <input ref={mriRef} type="file" accept=".nii.gz,.nii" style={{ display: 'block', marginTop: 4, width: '100%', fontSize: 11, color: '#e8edf2', fontFamily: 'IBM Plex Mono, monospace' }} />
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
  );
}
