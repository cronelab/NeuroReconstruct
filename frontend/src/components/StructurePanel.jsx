import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useAppStore } from '../store';
import FreeSurferSource from './FreeSurferSource';
import RegionToggleList from './RegionToggleList';
import { corticalRegionEntries, effectiveAtlas, parcelToggles, structureRegionEntries } from '../regions';

const MONO = 'IBM Plex Mono, monospace';

function ColourButton({ active, onClick, title, children }) {
  return (
    <button onClick={onClick} title={title}
      style={{
        flex: 1, fontSize: 11, padding: '3px 6px', borderRadius: 4, cursor: 'pointer',
        fontFamily: MONO, whiteSpace: 'nowrap',
        background: active ? '#16324a' : 'none',
        border: `1px solid ${active ? '#74C0FC' : '#1e2530'}`,
        color: active ? '#cfe6ff' : '#7a8a99',
      }}>
      {children}
    </button>
  );
}

/**
 * Brain-structure visibility panel: opacity slider and a folded
 * Group -> Side -> Structure region list with a master toggle. The same
 * structureVisible choices drive the 3D structure meshes and the 2D atlas
 * overlay, whose bar (SliceOverlayBar) shows this list too.
 *
 * Shared by the edit-mode right panel (ElectrodeEditor) and the locked/read-only
 * right panel (ReconstructionViewer) so both offer identical viewing options.
 * Structure visibility is read from / written to the global store, so it survives
 * switching between locked and unlocked views.
 */
export default function StructurePanel({
  onLoadStructures,
  structureOpacity,
  setStructureOpacity,
  maxHeight = 480,
}) {
  const {
    structuresData,
    structureVisible,
    setStructureVisibleMany,
    toggleAllStructures,
    brainRenderMode,
    setBrainRenderMode,
    corticalColorBy,
    setCorticalColorBy,
    corticalAtlas,
    setCorticalAtlas,
    corticalData,
    parcellation,
    corticalRegionOff,
    setCorticalRegionsOff,
  } = useAppStore();
  const cortical = brainRenderMode === 'cortical';
  const corticalUnavailable = corticalData === 'unavailable';
  const [loading, setLoading] = useState(false);

  const handleLoad = async () => {
    if (loading) return;
    setLoading(true);
    try { await onLoadStructures?.(); } finally { setLoading(false); }
  };

  const hasStructures = structuresData && Object.keys(structuresData).length > 0;
  // A FreeSurfer import ships its own pial surface, so the cortical mode does
  // not have to wait for the structure meshes the fast surface is built from.
  const fsActive = parcellation?.parcellation_source === 'freesurfer';
  const offerModes = hasStructures || fsActive;

  // Switching parcellation source drops the structure meshes (they were built
  // from the other label volume). If they were showing, rebuild them from the
  // new one rather than leaving the user to notice and press Load again.
  const hadStructures = useRef(false);
  useEffect(() => { if (hasStructures) hadStructures.current = true; }, [hasStructures]);
  const reloadPending = useRef(false);
  const loadRef = useRef(handleLoad);
  loadRef.current = handleLoad;
  const handleSourceChanged = useCallback(() => {
    reloadPending.current = hadStructures.current;
    hadStructures.current = false;
  }, []);
  useEffect(() => {
    if (reloadPending.current && !structuresData) {
      reloadPending.current = false;
      loadRef.current();
    }
  }, [structuresData]);

  // Colour choices. DKT is the default and every surface has it; the other
  // atlases a FreeSurfer import carries (Desikan-Killiany, Destrieux) are
  // extras, folded under "Other" until asked for.
  const loadedSurface = corticalData && corticalData !== 'unavailable' ? corticalData : null;
  const otherAtlases = Object.entries(loadedSurface?.atlases || {}).filter(([key]) => key !== 'dkt');
  const surfaceAtlas = effectiveAtlas(loadedSurface, corticalAtlas);
  const colouredBy = corticalColorBy === 'plain' ? 'plain' : surfaceAtlas;
  const otherActive = otherAtlases.some(([key]) => key === colouredBy);
  const [showOther, setShowOther] = useState(false);
  const pickColour = (c) => {
    if (c === 'plain') { setCorticalColorBy('plain'); return; }
    setCorticalAtlas(c);
    setCorticalColorBy('parcellation');
  };
  // The parcels of whichever atlas is colouring the surface, for its region list.
  const regionEntries = useMemo(
    () => corticalRegionEntries(loadedSurface, surfaceAtlas),
    [loadedSurface, surfaceAtlas]);
  // A parcel with a catalog region shares that region's on/off with the
  // Structures list; one without (Destrieux) has its own. The 2D overlay paints
  // whatever this list leaves coloured.
  const { isOn: parcelIsOn, setOn: setParcelsOn } = parcelToggles(regionEntries, {
    structureVisible, corticalRegionOff, setStructureVisibleMany, setCorticalRegionsOff,
  });
  const structureEntries = useMemo(() => structureRegionEntries(structuresData), [structuresData]);
  const allKeys = structureEntries.map(e => e.key);

  return (
    <div style={{ borderBottom: '1px solid #1e2530', padding: '8px 14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <span style={{ fontSize: 13, color: '#e8edf2', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>Brain</span>
        {!structuresData && (
          <button
            onClick={handleLoad}
            disabled={loading}
            style={{ fontSize: 11, color: loading ? '#4a5568' : '#74C0FC', background: 'none', border: '1px solid #1e2530', borderRadius: 4, padding: '3px 8px', cursor: loading ? 'default' : 'pointer', fontFamily: 'IBM Plex Mono, monospace' }}>
            {loading ? 'Computing…' : '⊕ Load'}
          </button>
        )}
      </div>

      <FreeSurferSource onSourceChanged={handleSourceChanged} />

      {/* Brain render mode. The fast cortical surface is derived from the same
          DKT volume the structures come from, so it is only offered once they
          exist -- or straight away when FreeSurfer's own surface is active. */}
      {offerModes && (
        <div style={{ display: 'flex', gap: 4, marginBottom: 8 }}>
          {[['structures', 'Structures'], ['cortical', 'Cortical surface']].map(([mode, label]) => (
            <button key={mode}
              onClick={() => setBrainRenderMode(mode)}
              disabled={mode === 'cortical' && corticalUnavailable}
              title={mode === 'cortical'
                ? (corticalUnavailable
                    ? 'Not available for this reconstruction'
                    : fsActive
                  ? "FreeSurfer's lh/rh pial surfaces: opaque, so contacts inside the brain are hidden"
                  : 'One opaque pial surface: sulci and gyri read clearly, but contacts inside the brain are hidden')
                : 'Nested translucent parcellation meshes; depth contacts stay visible'}
              style={{
                flex: 1, fontSize: 11, padding: '4px 6px', borderRadius: 4, cursor:
                  (mode === 'cortical' && corticalUnavailable) ? 'default' : 'pointer',
                fontFamily: 'IBM Plex Mono, monospace',
                background: brainRenderMode === mode ? '#16324a' : 'none',
                border: `1px solid ${brainRenderMode === mode ? '#74C0FC' : '#1e2530'}`,
                color: (mode === 'cortical' && corticalUnavailable) ? '#3d4757'
                  : brainRenderMode === mode ? '#cfe6ff' : '#7a8a99',
              }}>
              {label}
            </button>
          ))}
        </div>
      )}

      {/* Cortical mode replaces the opacity slider and the structure tree: the
          surface is opaque by design, and colour is the only choice left. */}
      {offerModes && cortical && (<>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
          <span style={{ fontSize: 11, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace' }}>Colour</span>
          <ColourButton active={colouredBy === 'plain'} onClick={() => pickColour('plain')}>Plain</ColourButton>
          <ColourButton active={colouredBy === 'dkt'} onClick={() => pickColour('dkt')}
            title="Desikan-Killiany-Tourville atlas: the default colouring">DKT</ColourButton>
          {otherAtlases.length > 0 && (
            <ColourButton active={otherActive} onClick={() => setShowOther(o => !o)}
              title="More atlases from the FreeSurfer import">
              {otherActive ? loadedSurface.atlases[colouredBy].name : 'Other'} {showOther ? '▾' : '▸'}
            </ColourButton>
          )}
        </div>
        {showOther && otherAtlases.length > 0 && (
          <div style={{ display: 'flex', gap: 6, margin: '0 0 4px 46px' }}>
            {otherAtlases.map(([key, a]) => (
              <ColourButton key={key} active={colouredBy === key} onClick={() => pickColour(key)}>{a.name}</ColourButton>
            ))}
          </div>
        )}
      </>)}

      {/* Per-parcel colour, like the structure tree but folded away by default.
          A parcel switched off draws white rather than disappearing: the surface
          is one mesh, so there is nothing behind it to show through. */}
      {offerModes && cortical && corticalColorBy === 'parcellation' && (
        <RegionToggleList
          title="Region colours"
          entries={regionEntries}
          isOn={parcelIsOn}
          setOn={setParcelsOn}
          unit="coloured"
          note="Unticked regions are drawn white, and left off the 2D slices."
          offSwatch="#e8edf2"
          maxHeight={maxHeight}
        />
      )}

      {hasStructures && !cortical && (<>
        {/* Structure surface transparency — mirrors the MRI opacity slider */}
        {allKeys.length > 0 && setStructureOpacity && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 0 8px', borderBottom: '1px solid #1a1e24' }}>
            <span style={{ fontSize: 11, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace', flexShrink: 0 }}>Opacity</span>
            <input type="range" min={0.05} max={1} step={0.05} value={structureOpacity ?? 0.45}
              onChange={e => setStructureOpacity(parseFloat(e.target.value))}
              style={{ flex: 1, accentColor: '#74C0FC' }} />
            <span style={{ fontSize: 11, fontFamily: 'IBM Plex Mono, monospace', color: '#7a8a99', width: 32, textAlign: 'right', flexShrink: 0 }}>{Math.round((structureOpacity ?? 0.45) * 100)}%</span>
          </div>
        )}

        {/* The header checkbox is the master "show brain structures": hiding
            remembers the selection, so showing again brings that back. */}
        <RegionToggleList
          title="Show brain structures"
          entries={structureEntries}
          isOn={k => structureVisible?.[k] !== false}
          setOn={setStructureVisibleMany}
          onToggleAll={() => toggleAllStructures(allKeys)}
          unit="shown"
          note="Also sets what the 2D atlas overlay shows."
          maxHeight={maxHeight}
        />
      </>)}

      {structuresData && Object.keys(structuresData).length === 0 && (
        <div style={{ fontSize: 11, color: '#4a5568', fontFamily: 'IBM Plex Mono, monospace' }}>No structures found</div>
      )}
    </div>
  );
}
