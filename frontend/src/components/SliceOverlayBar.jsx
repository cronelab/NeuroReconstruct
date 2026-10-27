import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useAppStore } from '../store';
import { getStructures } from '../api';
import { corticalRegionEntries, effectiveAtlas, parcelToggles, structureRegionEntries } from '../regions';
import RegionToggleList from './RegionToggleList';

const MONO = 'IBM Plex Mono, monospace';

// One structures request per reconstruction, however many places ask for it.
const inFlight = new Map();
function loadStructures(reconId, token) {
  const key = `${reconId}:${token || ''}`;
  if (!inFlight.has(key)) {
    inFlight.set(key, getStructures(reconId, token)
      .then(r => useAppStore.getState().setStructuresData(r.data, reconId))
      .finally(() => inFlight.delete(key)));
  }
  return inFlight.get(key);
}


/**
 * The 2D slice views' atlas overlay: on/off for the whole overlay, and a
 * folded region list to switch single regions on or off, every one on by
 * default. It follows the Brain panel's mode, so the slices show what the 3D
 * view shows:
 *   Structures mode -- the structure catalog (subcortical and cortical) from
 *     the active parcellation, on the Structures list's choices;
 *   Cortical surface mode -- the surface's own atlas (DKT, or another one under
 *     Other), cortex only, on the Region colours list's choices.
 * Only the whole-overlay switch is 2D's own.
 */
export default function SliceOverlayBar({ reconId, shareToken }) {
  const {
    structuresData, parcellation,
    sliceOverlayOn, setSliceOverlayOn,
    structureVisible, setStructureVisibleMany, toggleAllStructures,
    brainRenderMode, corticalData, corticalColorBy, corticalAtlas,
    corticalRegionOff, setCorticalRegionsOff,
  } = useAppStore();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const panelRef = useRef(null);
  const toggleRef = useRef(null);

  const cortical = brainRenderMode === 'cortical';
  const surface = corticalData && corticalData !== 'unavailable' ? corticalData : null;
  const surfaceAtlas = effectiveAtlas(surface, corticalAtlas);
  const structureEntries = useMemo(() => structureRegionEntries(structuresData), [structuresData]);
  const parcelEntries = useMemo(
    () => (cortical ? corticalRegionEntries(surface, surfaceAtlas) : []),
    [cortical, surface, surfaceAtlas]);

  const entries = cortical ? parcelEntries : structureEntries;
  const toggles = cortical
    ? parcelToggles(parcelEntries, { structureVisible, corticalRegionOff, setStructureVisibleMany, setCorticalRegionsOff })
    : { isOn: k => structureVisible[k] !== false, setOn: setStructureVisibleMany };
  const shown = entries.filter(e => toggles.isOn(e.key)).length;
  const ready = cortical ? !!surface : !!structuresData;
  const plain = cortical && corticalColorBy === 'plain';

  // Close the region panel on a click anywhere else; it floats over the slices.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (panelRef.current?.contains(e.target) || toggleRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const handleLoad = async () => {
    setLoading(true);
    setError('');
    try { await loadStructures(reconId, shareToken); }
    catch (e) { setError('Could not load the atlas'); }
    finally { setLoading(false); }
  };

  const sourceName = parcellation?.parcellation_source === 'freesurfer' ? 'FreeSurfer' : parcellation ? 'Fast' : null;
  const what = cortical
    ? `${surface?.atlases?.[surfaceAtlas]?.name || 'DKT'} · cortex`
    : 'DKT · all structures';
  const quiet = { fontSize: 10, color: '#4a5568', fontFamily: MONO };

  return (
    <div style={{
      flexShrink: 0, position: 'relative',
      // Wraps rather than clips: beside the side panel the slice column can be
      // narrower than this bar.
      display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
      padding: '5px 10px', background: '#0a0c10', borderBottom: '1px solid #1e2530',
    }}>
      <span style={{ ...quiet, letterSpacing: '0.06em' }}>OVERLAY</span>

      {!ready ? (cortical ? (
        <span style={quiet}>Loading the cortical surface…</span>
      ) : (
        <button
          onClick={handleLoad}
          disabled={loading}
          title="Paint the parcellation's regions over the slices"
          style={{ fontSize: 11, color: loading ? '#4a5568' : '#74C0FC', background: 'none', border: '1px solid #1e2530', borderRadius: 3, padding: '3px 8px', cursor: loading ? 'default' : 'pointer', fontFamily: MONO }}
        >
          {loading ? 'Loading atlas…' : '⊕ Load atlas'}
        </button>
      )) : (<>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}
          title="Show or hide the whole atlas overlay; your region choices are kept">
          <input type="checkbox" checked={sliceOverlayOn}
            onChange={e => setSliceOverlayOn(e.target.checked)}
            style={{ width: 13, height: 13, accentColor: '#74C0FC', cursor: 'pointer' }} />
          <span style={{ fontSize: 11, color: sliceOverlayOn ? '#c8d4e0' : '#7a8a99', fontFamily: MONO }}>Atlas</span>
        </label>
        <span style={quiet} title="Follows the Brain panel: Structures or Cortical surface">
          {sourceName ? `${sourceName} ` : ''}{what}
        </span>
        {plain ? (
          <span style={quiet}>Surface is Plain: pick a colour to paint the slices</span>
        ) : (
          <button
            ref={toggleRef}
            onClick={() => setOpen(o => !o)}
            disabled={!sliceOverlayOn}
            title="Turn single regions on or off in the overlay"
            style={{
              fontSize: 11, fontFamily: MONO, padding: '3px 8px', borderRadius: 3,
              cursor: sliceOverlayOn ? 'pointer' : 'default', opacity: sliceOverlayOn ? 1 : 0.5,
              background: open ? '#16324a' : 'transparent', color: open ? '#cfe6ff' : '#7a8a99',
              border: `1px solid ${open ? '#74C0FC' : '#2a3340'}`,
            }}
          >
            Regions {open ? '▾' : '▸'} <span style={{ color: '#4a5568' }}>{shown}/{entries.length}</span>
          </button>
        )}
      </>)}

      {error && <span style={{ fontSize: 11, color: '#ff5252', fontFamily: MONO }}>{error}</span>}

      {open && sliceOverlayOn && ready && !plain && (
        <div ref={panelRef} style={{
          position: 'absolute', top: '100%', left: 8, zIndex: 30,
          width: 380, maxWidth: 'calc(100% - 16px)',
          background: '#111418', border: '1px solid #2a3340', borderRadius: 6,
          padding: '6px 12px 10px', boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
        }}>
          <RegionToggleList
            title="All regions"
            entries={entries}
            isOn={toggles.isOn}
            setOn={toggles.setOn}
            onToggleAll={cortical ? null : () => toggleAllStructures(entries.map(e => e.key))}
            unit="shown"
            note={cortical
              ? 'Same choices as Region colours in the Brain panel.'
              : 'Same choices as Show brain structures in the Brain panel.'}
            defaultOpen
            maxHeight="55vh"
          />
        </div>
      )}
    </div>
  );
}
