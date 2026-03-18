import React, { useEffect, useState, useCallback, useRef } from 'react';
import { getReconstruction, getMesh, getStructures } from '../api';
import MultiViewLayout from './MultiViewLayout';
import { useAppStore } from '../store';
import Viewer3D from './Viewer3D';
import LayerPanel from './LayerPanel';
import ElectrodeEditor from './ElectrodeEditor';
import api, { snapToBlob } from '../api';

function StructureRow({ structKey, s, structureVisible, setStructureVisible, stripHemisphere }) {
  const label = stripHemisphere ? s.label.replace(/^(Left|Right)\s+/i, '') : s.label;
  const checked = structureVisible?.[structKey] !== false;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 5 }}>
      <input type="checkbox"
        checked={checked}
        onChange={e => setStructureVisible(structKey, e.target.checked)}
        style={{ accentColor: s.color, width: 15, height: 15, flexShrink: 0, cursor: 'pointer' }} />
      <div style={{ width: 11, height: 11, borderRadius: 2, background: s.color, flexShrink: 0 }} />
      <span style={{ fontSize: 13, color: '#c8d4e0', fontFamily: 'IBM Plex Sans, sans-serif', lineHeight: 1.2 }}>{label}</span>
    </div>
  );
}

export default function ReconstructionViewer({ reconId, shareToken }) {
  const {
    setReconstruction, reconstruction,
    setMeshData,
    setShaftVisible,
    isEditorMode,
    setEditorMode,
    structuresData, setStructuresData,
    structureVisible, setStructureVisible,
    selectedShaftId,
    activeContactNumber,
    setActiveContactNumber,
  } = useAppStore();
  // Use local state for isLocked so it starts false (not stale from store) until fresh fetch completes
  const [isLocked, setIsLocked] = React.useState(false);
  // Register callback for mark-complete display defaults
  React.useEffect(() => {
    window.__onMarkComplete = () => {
      setShowMri(true);
      setShowCt(false);
    };
    return () => { window.__onMarkComplete = null; };
  }, []);

  // Keep in sync if header changes lock state; hide MRI when unlocking
  React.useEffect(() => {
    const locked = reconstruction?.is_locked || false;
    setIsLocked(prev => {
      if (prev && !locked) setShowMri(false); // transitioning unlock → hide MRI
      return locked;
    });
  }, [reconstruction?.is_locked]);

  const [loading, setLoading] = useState(true);
  const [meshLoading, setMeshLoading] = useState(false);
  const [loadingMessage, setLoadingMessage] = useState('Loading reconstruction...');
  const [error, setError] = useState(null);
  const [pollingForMesh, setPollingForMesh] = useState(false);

  // CT threshold mesh state
  const [ctMeshData, setCtMeshData] = useState(null);
  const [ctMeshLoading, setCtMeshLoading] = useState(false);
  const [currentThreshold, setCurrentThreshold] = useState(0);
  const [undoStack, setUndoStack] = useState([]); // [{shaftId, contactNumber}]
  const [showMri, setShowMri] = useState(false);
  const [showStructures, setShowStructures] = useState(false);
  const [structuresLoading, setStructuresLoading] = useState(false);
  const [mriOpacity, setMriOpacity] = useState(0.3);
  const [showCt, setShowCt] = useState(false);
  const [ctOpacityOverride, setCtOpacityOverride] = useState(0.6);
  const [rightWidth, setRightWidth] = useState(480);
  const rightDragging = React.useRef(false);

  const handleRightDividerMouseDown = React.useCallback((e) => {
    e.preventDefault();
    rightDragging.current = true;
    const startX = e.clientX;
    const startWidth = rightWidth;
    const onMove = (ev) => {
      if (!rightDragging.current) return;
      // Dragging left edge of right panel: moving left = wider
      setRightWidth(Math.max(300, Math.min(700, startWidth - (ev.clientX - startX))));
    };
    const onUp = () => {
      rightDragging.current = false;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [rightWidth]);

  // ── Load reconstruction ──────────────────────────────────────────────────
  const loadReconstruction = useCallback(async () => {
    try {
      const res = await getReconstruction(reconId, shareToken);
      const recon = res.data;
      setReconstruction(recon);
      const locked = recon.is_complete || false;
      setIsLocked(locked);
      if (locked) {
        setShowMri(true);
        setShowCt(false);
      } else {
        setShowMri(false);
        setEditorMode(true); // In Progress → jump straight into edit mode
      }
      recon.electrode_shafts?.forEach(s => setShaftVisible(s.id, s.visible));
      return recon;
    } catch (e) {
      setError('Failed to load reconstruction: ' + (e.response?.data?.detail || e.message));
      return null;
    }
  }, [reconId, shareToken]);

  useEffect(() => {
    const init = async () => {
      const recon = await loadReconstruction();
      if (!recon) { setLoading(false); return; }

      if (recon.status === 'ready' && recon.has_mesh) {
        await loadBrainMesh();
      } else if (recon.status === 'processing') {
        setLoadingMessage('Brain mesh is being processed...');
        setPollingForMesh(true);
      } else if (recon.status === 'error') {
        setError('Mesh extraction failed. Please re-upload the MRI.');
      }

      setLoading(false);
    };
    init();
  }, [reconId, shareToken]);

  // Poll for mesh readiness
  useEffect(() => {
    if (!pollingForMesh) return;
    const interval = setInterval(async () => {
      const recon = await loadReconstruction();
      if (recon?.status === 'ready' && recon.has_mesh) {
        setPollingForMesh(false);
        await loadBrainMesh();
      } else if (recon?.status === 'error') {
        setPollingForMesh(false);
        setError('Mesh extraction failed.');
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [pollingForMesh]);

  const loadBrainMesh = async () => {
    setMeshLoading(true);
    setLoadingMessage('Loading brain mesh...');
    try {
      const res = await getMesh(reconId, shareToken);
      setMeshData(res.data);
    } catch (e) {
      console.warn('Could not load mesh', e);
    } finally {
      setMeshLoading(false);
    }
  };

  // ── CT threshold mesh ────────────────────────────────────────────────────
  const requestIdRef = useRef(0);
  const thresholdDebounceRef = useRef(null);
  const autoLoadedRef = useRef(false);

  const loadCtMesh = useCallback(async (threshold) => {
    const myId = ++requestIdRef.current;
    setCtMeshLoading(true);
    try {
      const params = new URLSearchParams({ threshold });
      if (shareToken) params.set('token', shareToken);
      const res = await api.get(`/reconstructions/${reconId}/ct-threshold-mesh?${params}`);
      if (myId !== requestIdRef.current) return;
      const d = res.data;
      console.log('[CT Mesh]', { empty: d.empty, vertices: d.vertices?.length, faces: d.faces?.length, threshold });
      setCtMeshData(d.empty ? null : d);
    } catch (e) {
      console.warn('CT mesh failed', e);
    } finally {
      setCtMeshLoading(false);
    }
  }, [reconId, shareToken]);

  // Auto-load CT mesh as soon as editor mode is active and CT is available.
  // Depends on reconstruction so it fires once reconstruction has loaded,
  // and again if the user toggles editor mode.
  const DEFAULT_THRESHOLD = 0;

  // Load CT when editor mode activated OR when a locked recon with CT first loads
  useEffect(() => {
    if (reconstruction?.has_ct && (isEditorMode || isLocked)) {
      loadCtMesh(DEFAULT_THRESHOLD);
    }
  }, [isEditorMode, isLocked, reconstruction?.id]);

  // Keep showCt in sync with editor mode
  useEffect(() => {
    if (isEditorMode) setShowCt(true);
  }, [isEditorMode]);

  const loadStructures = useCallback(async () => {
    if (structuresData || structuresLoading) return;
    setStructuresLoading(true);
    try {
      const res = await getStructures(reconId, shareToken);
      setStructuresData(res.data);
    } catch (e) { console.warn('Structures load failed', e); }
    finally { setStructuresLoading(false); }
  }, [reconId, shareToken, structuresData, structuresLoading]);

  const handleThresholdChange = useCallback((threshold) => {
    setCurrentThreshold(threshold);
    // Debounce CT mesh reload — avoids flooding backend while sliding
    if (thresholdDebounceRef.current) clearTimeout(thresholdDebounceRef.current);
    thresholdDebounceRef.current = setTimeout(() => {
      loadCtMesh(threshold);
    }, 400);
  }, [loadCtMesh]);

  // ── Place contact from 3D click ──────────────────────────────────────────
  const handleContactPlaced = useCallback(async ({ x, y, z }) => {
    if (isLocked) return;
    const shaft = reconstruction?.electrode_shafts?.find(s => s.id === selectedShaftId);
    if (!shaft || activeContactNumber == null) return;

    try {
      // Snap to blob centroid before saving
      let sx = x, sy = y, sz = z;
      console.log('[CLICK] raw click pos:', x.toFixed(2), y.toFixed(2), z.toFixed(2));
      console.log('[CLICK] has_ct:', reconstruction?.has_ct, 'threshold:', currentThreshold, 'reconId:', reconId);
      if (reconstruction?.has_ct) {
        try {
          const snapRes = await snapToBlob(reconId, [x, y, z], currentThreshold);
          const orig = [sx, sy, sz];
          [sx, sy, sz] = snapRes.data.snapped_position;
          const dist = Math.sqrt((sx-orig[0])**2 + (sy-orig[1])**2 + (sz-orig[2])**2);
          console.log('[SNAP] result:', sx.toFixed(2), sy.toFixed(2), sz.toFixed(2), `moved ${dist.toFixed(2)}mm`);
        } catch (e) {
          console.warn('[SNAP] failed:', e.response?.data || e.message);
        }
      } else {
        console.warn('[SNAP] skipped — no CT');
      }

      await api.post(`/shafts/${shaft.id}/contacts`, {
        contact_number: activeContactNumber,
        x: sx, y: sy, z: sz,
        is_manual: true,
        is_world_mm: true,
      });
      setUndoStack(prev => [...prev, { shaftId: shaft.id, contactNumber: activeContactNumber }]);

      // Advance to next unplaced contact
      const updatedRecon = await loadReconstruction();
      const updatedShaft = updatedRecon?.electrode_shafts?.find(s => s.id === selectedShaftId);
      if (updatedShaft) {
        const placed = new Set((updatedShaft.contacts || []).filter(c => c.x_mm != null).map(c => c.contact_number));
        const n = updatedShaft.n_total_contacts || 12;
        const nextUnplaced = Array.from({length: n}, (_, i) => i + 1).find(n => !placed.has(n));
        setActiveContactNumber(nextUnplaced ?? null);
      }
    } catch (e) {
      console.error('Failed to place contact', e);
    }
  }, [reconstruction, selectedShaftId, activeContactNumber, loadReconstruction, setActiveContactNumber]);

  const handleUndo = useCallback(async () => {
    if (undoStack.length === 0) return;
    const last = undoStack[undoStack.length - 1];
    try {
      await api.delete(`/shafts/${last.shaftId}/contacts/${last.contactNumber}`);
      setUndoStack(prev => prev.slice(0, -1));
      await loadReconstruction();
    } catch (e) {
      console.error('Undo failed', e);
    }
  }, [undoStack, loadReconstruction]);

  if (error) {
    return (
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
        flexDirection: 'column', gap: 12, color: '#ff5252',
        fontFamily: 'IBM Plex Sans, sans-serif',
      }}>
        <div style={{ fontSize: 28 }}>⚠</div>
        <div style={{ fontSize: 13 }}>{error}</div>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minWidth: 700 }}>

      {/* Multi-view layout: left selector column + main view */}
      <MultiViewLayout reconId={reconId} viewer3D={
        <div style={{ width: '100%', height: '100%', position: 'relative' }}>
          <Viewer3D
            loading={loading || meshLoading || pollingForMesh}
            loadingMessage={loadingMessage}
            ctMeshData={isLocked ? (showCt ? ctMeshData : null) : ctMeshData}
            ctMeshLoading={ctMeshLoading}
            onContactPlaced={handleContactPlaced}
            showMri={showMri}
            mriOpacity={mriOpacity}
            ctThreshold={currentThreshold}
            ctOpacityOverride={isLocked ? ctOpacityOverride : null}
            activeContactNumber={isLocked ? null : activeContactNumber}
            structuresData={structuresData}
            structureVisible={structureVisible}
          />
          {pollingForMesh && !loading && (
            <div style={{ position: 'absolute', top: 16, left: '50%', transform: 'translateX(-50%)', background: '#111418', border: '1px solid #1a1a0d', borderRadius: 4, padding: '8px 16px', fontSize: 11, color: '#ffab40', fontFamily: 'IBM Plex Mono, monospace', display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#ffab40', animation: 'pulse 1.5s ease infinite' }} />
              Processing brain mesh…
            </div>
          )}
          {ctMeshLoading && (
            <div style={{ position: 'absolute', top: 16, right: 16, background: '#111418cc', border: '1px solid #ffdd0044', borderRadius: 4, padding: '5px 12px', fontSize: 10, color: '#ffdd00', fontFamily: 'IBM Plex Mono, monospace' }}>
              ⟳ Rendering CT threshold…
            </div>
          )}
          {reconstruction && !loading && (
            <div style={{ position: 'absolute', bottom: 16, left: 16, background: '#111418cc', backdropFilter: 'blur(8px)', border: '1px solid #1e2530', borderRadius: 4, padding: '6px 12px', fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#7a8a99' }}>
              {reconstruction.electrode_shafts?.length || 0} shafts · {reconstruction.electrode_shafts?.reduce((sum, s) => sum + (s.contacts?.length || 0), 0)} contacts
            </div>
          )}
        </div>
      } />

      {/* Right panel */}
      {isEditorMode ? (<>
        {/* Draggable resize handle for right panel */}
        <div
          onMouseDown={handleRightDividerMouseDown}
          style={{
            flex: '0 0 5px', cursor: 'col-resize',
            background: '#1e2530', zIndex: 10,
            transition: 'background 0.15s',
          }}
          onMouseEnter={e => e.currentTarget.style.background = '#00d4ff44'}
          onMouseLeave={e => e.currentTarget.style.background = '#1e2530'}
        />
        <div style={{
          width: rightWidth,
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          background: '#111418',
          overflow: 'hidden',
        }}>
          <ElectrodeEditor
            reconId={reconId}
            onShaftsUpdated={loadReconstruction}
            onThresholdChange={handleThresholdChange}
            hasCtFile={reconstruction?.has_ct || false}
            showMri={showMri}
            setShowMri={setShowMri}
            mriOpacity={mriOpacity}
            setMriOpacity={setMriOpacity}
            hasMesh={!!reconstruction?.has_mesh}
            onUndo={handleUndo}
            undoAvailable={undoStack.length > 0}
            activeContactNumber={activeContactNumber}
            setActiveContactNumber={setActiveContactNumber}
            currentThreshold={currentThreshold}
            isLocked={isLocked}
            onLoadStructures={loadStructures}
            showStructures={showStructures}
            setShowStructures={setShowStructures}
          />
        </div>
      </>) : (
        <div style={{ width: rightWidth, flexShrink: 0, display: 'flex', flexDirection: 'column', background: '#111418', overflow: 'hidden' }}>
          {/* Locked view: MRI + CT controls */}
          <div style={{ padding: '14px 16px', borderBottom: '1px solid #1e2530' }}>
            <div style={{ fontSize: 10, fontWeight: 600, color: '#7a8a99', textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: 'IBM Plex Mono, monospace', marginBottom: 12 }}>Display</div>

            {/* MRI */}
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <input type="checkbox" checked={showMri} onChange={e => setShowMri(e.target.checked)} style={{ accentColor: '#00d4ff', width: 13, height: 13 }} />
                <span style={{ fontSize: 12, color: showMri ? '#e8edf2' : '#7a8a99', fontFamily: 'IBM Plex Sans, sans-serif' }}>MRI Brain Surface</span>
              </div>
              {showMri && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, paddingLeft: 21 }}>
                  <input type="range" min={0.05} max={1} step={0.05} value={mriOpacity} onChange={e => setMriOpacity(parseFloat(e.target.value))} style={{ flex: 1, accentColor: '#00d4ff' }} />
                  <span style={{ fontSize: 11, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace', width: 32, textAlign: 'right' }}>{Math.round(mriOpacity * 100)}%</span>
                </div>
              )}
            </div>

            {/* CT */}
            {reconstruction?.has_ct && (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <input type="checkbox" checked={showCt} onChange={e => { const checked = e.target.checked; setShowCt(checked); if (checked && !ctMeshData) loadCtMesh(currentThreshold || 1500); }} style={{ accentColor: '#ffab40', width: 13, height: 13 }} />
                  <span style={{ fontSize: 12, color: showCt ? '#e8edf2' : '#7a8a99', fontFamily: 'IBM Plex Sans, sans-serif' }}>CT Electrodes</span>
                </div>
                {showCt && (
                  <div style={{ paddingLeft: 21 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                      <input type="range" min={-1000} max={3000} step={50} value={currentThreshold}
                        onChange={e => { const v = parseInt(e.target.value); handleThresholdChange(v); }}
                        style={{ flex: 1, accentColor: '#ffab40' }} />
                      <span style={{ fontSize: 11, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace', width: 46, textAlign: 'right' }}>{currentThreshold} HU</span>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Structures */}
          {structuresData && Object.keys(structuresData).length > 0 && (
            <div style={{ padding: '10px 16px', borderBottom: '1px solid #1e2530', maxHeight: 480, overflowY: 'auto' }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#7a8a99', textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: 'IBM Plex Mono, monospace', marginBottom: 10 }}>Structures</div>
              {['subcortical', 'frontal', 'temporal', 'parietal', 'occipital', 'cingulate'].filter(g =>
                Object.values(structuresData).some(s => s.group === g && s.vertices)
              ).map(group => {
                const entries = Object.entries(structuresData).filter(([,s]) => s.group === group && s.vertices);
                if (!entries.length) return null;
                const leftEntries  = entries.filter(([k]) => k.endsWith('_l'));
                const rightEntries = entries.filter(([k]) => k.endsWith('_r'));
                const midline      = entries.filter(([k]) => !k.endsWith('_l') && !k.endsWith('_r'));
                const selectAll  = () => entries.forEach(([k]) => setStructureVisible(k, true));
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
                    {/* Midline structures span full width */}
                    {midline.map(([key, s]) => (
                      <StructureRow key={key} structKey={key} s={s}
                        structureVisible={structureVisible} setStructureVisible={setStructureVisible}
                        stripHemisphere={false} />
                    ))}
                    {/* Bilateral structures in two columns */}
                    {(leftEntries.length > 0 || rightEntries.length > 0) && (
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 10 }}>
                        <div>
                          <div style={{ fontSize: 10, color: '#7a8a99', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 5, fontFamily: 'IBM Plex Mono, monospace' }}>Left</div>
                          {leftEntries.map(([key, s]) => (
                            <StructureRow key={key} structKey={key} s={s}
                              structureVisible={structureVisible} setStructureVisible={setStructureVisible}
                              stripHemisphere={true} />
                          ))}
                        </div>
                        <div>
                          <div style={{ fontSize: 10, color: '#7a8a99', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 5, fontFamily: 'IBM Plex Mono, monospace' }}>Right</div>
                          {rightEntries.map(([key, s]) => (
                            <StructureRow key={key} structKey={key} s={s}
                              structureVisible={structureVisible} setStructureVisible={setStructureVisible}
                              stripHemisphere={true} />
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {!structuresData && (
            <div style={{ padding: '10px 16px', borderBottom: '1px solid #1e2530' }}>
              <button onClick={loadStructures} disabled={structuresLoading}
                style={{ fontSize: 11, color: structuresLoading ? '#4a5568' : '#74C0FC', background: 'none', border: '1px solid #1e2530', borderRadius: 4, padding: '4px 10px', cursor: structuresLoading ? 'default' : 'pointer', fontFamily: 'IBM Plex Mono, monospace' }}>
                {structuresLoading ? 'Computing structures…' : '⊕ Load Structures'}
              </button>
            </div>
          )}

          {/* Shaft list — read only */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#e8edf2', textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: 'IBM Plex Mono, monospace', marginBottom: 10 }}>Electrodes</div>
            {(reconstruction?.electrode_shafts || []).map(shaft => (
              <div key={shaft.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', borderBottom: '1px solid #1a1e24' }}>
                <div style={{ width: 13, height: 13, borderRadius: '50%', background: shaft.color, flexShrink: 0, border: '1px solid rgba(255,255,255,0.2)' }} />
                <span style={{ fontSize: 16, color: '#ffffff', fontFamily: 'IBM Plex Sans, sans-serif', flex: 1 }}>{shaft.name}</span>
                {shaft.label && <span style={{ fontSize: 14, color: '#e8edf2', fontFamily: 'IBM Plex Sans, sans-serif' }}>{shaft.label}</span>}
                <span style={{ fontSize: 13, color: '#e8edf2', fontFamily: 'IBM Plex Mono, monospace' }}>{(shaft.contacts || []).filter(c => c.x_mm != null).length}c</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
