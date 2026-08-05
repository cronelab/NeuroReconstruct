import React, { useState, useEffect } from 'react';
import { useAppStore } from '../store';
import { setParcellationSource } from '../api';

// Checkbox that can render a dash (indeterminate) when only some descendants are checked
export function TriStateCheckbox({ checked, indeterminate, onChange, onClick, style }) {
  const ref = React.useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = !!indeterminate && !checked;
  }, [indeterminate, checked]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      onChange={onChange}
      onClick={onClick}
      style={{ width: 15, height: 15, flexShrink: 0, cursor: 'pointer', ...style }}
    />
  );
}

const GROUP_ORDER = ['subcortical', 'frontal', 'temporal', 'parietal', 'occipital', 'cingulate'];

/**
 * Brain-structure visibility panel: master toggle, opacity slider, and a
 * hierarchical Group -> Side -> Structure tri-state tree.
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
    setStructureVisible,
    setStructureVisibleMany,
    reconstruction,
    setReconstruction,
    setStructuresData,
  } = useAppStore();
  const [loading, setLoading] = useState(false);
  const [switching, setSwitching] = useState(false);

  const handleLoad = async () => {
    if (loading) return;
    setLoading(true);
    try { await onLoadStructures?.(); } finally { setLoading(false); }
  };

  // Which MRI drives the parcellation. Only offered when a 2nd MRI was uploaded.
  // Switching invalidates the cached structures server-side; drop them locally and
  // reload so the tree rebuilds from the newly-selected source.
  const parcSource = reconstruction?.parcellation_source || 'main';
  const handleSourceChange = async (newSource) => {
    if (switching || newSource === parcSource || !reconstruction?.id) return;
    setSwitching(true);
    try {
      await setParcellationSource(reconstruction.id, newSource);
      setReconstruction({ ...reconstruction, parcellation_source: newSource });
      setStructuresData(null);
      await onLoadStructures?.();
    } catch (e) {
      console.error('Failed to switch parcellation source', e);
    } finally {
      setSwitching(false);
    }
  };

  const hasStructures = structuresData && Object.keys(structuresData).length > 0;
  const allKeys = hasStructures
    ? Object.entries(structuresData).filter(([, s]) => s.vertices).map(([k]) => k)
    : [];

  const stateOf = (list) => {
    const vis = list.map(([k]) => structureVisible?.[k] !== false);
    const allOn = vis.length > 0 && vis.every(v => v);
    const allOff = vis.every(v => !v);
    return { checked: allOn, indeterminate: !allOn && !allOff };
  };
  const keysOf = (list) => list.map(([k]) => k);
  const toggleKeys = (keys, v) => setStructureVisibleMany(keys, v);

  const aVis = allKeys.map(k => structureVisible?.[k] !== false);
  const aAllOn = aVis.length > 0 && aVis.every(v => v);
  const aAllOff = aVis.every(v => !v);
  const allState = { checked: aAllOn, indeterminate: !aAllOn && !aAllOff };

  return (
    <div style={{ borderBottom: '1px solid #1e2530', padding: '8px 14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <span style={{ fontSize: 13, color: '#e8edf2', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>Structures</span>
        {!structuresData && (
          <button
            onClick={handleLoad}
            disabled={loading}
            style={{ fontSize: 11, color: loading ? '#4a5568' : '#74C0FC', background: 'none', border: '1px solid #1e2530', borderRadius: 4, padding: '3px 8px', cursor: loading ? 'default' : 'pointer', fontFamily: 'IBM Plex Mono, monospace' }}>
            {loading ? 'Computing…' : '⊕ Load'}
          </button>
        )}
      </div>

      {reconstruction?.has_mri2 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace' }}>Parcellate from</span>
          {[['main', 'Main MRI'], ['secondary', '2nd MRI']].map(([val, lbl]) => (
            <button key={val}
              onClick={() => handleSourceChange(val)}
              disabled={switching}
              title={val === 'secondary' ? 'Use the uploaded 2nd MRI (e.g. pre-op) for cortical parcellation' : 'Use the main reconstruction MRI'}
              style={{
                fontSize: 10, padding: '2px 7px', borderRadius: 4,
                cursor: switching ? 'default' : 'pointer',
                fontFamily: 'IBM Plex Mono, monospace',
                border: '1px solid ' + (parcSource === val ? '#74C0FC' : '#1e2530'),
                background: parcSource === val ? '#12202c' : 'none',
                color: parcSource === val ? '#74C0FC' : '#7a8a99',
              }}>
              {lbl}
            </button>
          ))}
          {switching && <span style={{ fontSize: 10, color: '#4a5568', fontFamily: 'IBM Plex Mono, monospace' }}>switching…</span>}
        </div>
      )}

      {hasStructures && (
        <div style={{ maxHeight, overflowY: 'auto' }}>
          {/* Master toggle — show/hide all brain structures (subcortical + cortical) at once */}
          {allKeys.length > 0 && (
            <label style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '4px 0 8px', marginBottom: 6, borderBottom: '1px solid #1a1e24', cursor: 'pointer' }}>
              <TriStateCheckbox
                checked={allState.checked}
                indeterminate={allState.indeterminate}
                onChange={e => setStructureVisibleMany(allKeys, e.target.checked)}
                style={{ accentColor: '#74C0FC' }}
              />
              <span style={{ fontSize: 12, color: '#c8d4e0', fontFamily: 'IBM Plex Mono, monospace' }}>Show brain structures</span>
            </label>
          )}

          {/* Structure surface transparency — mirrors the MRI opacity slider */}
          {allKeys.length > 0 && setStructureOpacity && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 0 8px', marginBottom: 6, borderBottom: '1px solid #1a1e24' }}>
              <span style={{ fontSize: 11, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace', flexShrink: 0 }}>Opacity</span>
              <input type="range" min={0.05} max={1} step={0.05} value={structureOpacity ?? 0.45}
                onChange={e => setStructureOpacity(parseFloat(e.target.value))}
                style={{ flex: 1, accentColor: '#74C0FC' }} />
              <span style={{ fontSize: 11, fontFamily: 'IBM Plex Mono, monospace', color: '#7a8a99', width: 32, textAlign: 'right', flexShrink: 0 }}>{Math.round((structureOpacity ?? 0.45) * 100)}%</span>
            </div>
          )}

          {GROUP_ORDER.filter(g =>
            Object.values(structuresData).some(s => s.group === g && s.vertices)
          ).map(group => {
            const entries = Object.entries(structuresData).filter(([, s]) => s.group === group && s.vertices);
            if (!entries.length) return null;
            const leftEntries = entries.filter(([k]) => k.endsWith('_l'));
            const rightEntries = entries.filter(([k]) => k.endsWith('_r'));
            const midline = entries.filter(([k]) => !k.endsWith('_l') && !k.endsWith('_r'));

            const groupState = stateOf(entries);
            // Subsections show only when the group has at least one visible structure
            // (checked or indeterminate). A fully-unchecked group is collapsed.
            const expanded = groupState.checked || groupState.indeterminate;

            return (
              <div key={group} style={{ marginBottom: 6, borderBottom: '1px solid #1a1e24' }}>
                {/* Level 1 — Group. Row/checkbox toggles the whole group; the
                    subsection tree follows the checked state. */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 0', cursor: 'pointer' }}
                  onClick={() => toggleKeys(keysOf(entries), !groupState.checked)}>
                  <span style={{ fontSize: 10, color: '#7a8a99', width: 10, textAlign: 'center', flexShrink: 0 }}>{expanded ? '▾' : '▸'}</span>
                  <TriStateCheckbox
                    checked={groupState.checked}
                    indeterminate={groupState.indeterminate}
                    onClick={e => e.stopPropagation()}
                    onChange={e => toggleKeys(keysOf(entries), e.target.checked)}
                    style={{ accentColor: '#74C0FC' }}
                  />
                  <span style={{ fontSize: 11, fontWeight: 600, color: '#c8d4e0', textTransform: 'uppercase', letterSpacing: '0.06em', fontFamily: 'IBM Plex Mono, monospace', flex: 1 }}>
                    {group}
                  </span>
                  <span style={{ fontSize: 10, color: '#4a5568', fontFamily: 'IBM Plex Mono, monospace' }}>{entries.length}</span>
                </div>

                {expanded && (
                  <div style={{ paddingLeft: 20, paddingBottom: 8 }}>
                    {/* Midline structures — full width */}
                    {midline.map(([key, s]) => {
                      const checked = structureVisible?.[key] !== false;
                      return (
                        <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 5 }}>
                          <TriStateCheckbox checked={checked}
                            onChange={e => setStructureVisible(key, e.target.checked)}
                            style={{ accentColor: s.color }} />
                          <div style={{ width: 11, height: 11, borderRadius: 2, background: s.color, flexShrink: 0 }} />
                          <span style={{ fontSize: 13, color: '#c8d4e0', fontFamily: 'IBM Plex Sans, sans-serif' }}>{s.label}</span>
                        </div>
                      );
                    })}
                    {/* Level 2 — Side (Left/Right), Level 3 — individual structures */}
                    {(leftEntries.length > 0 || rightEntries.length > 0) && (
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 10 }}>
                        {[['Left', leftEntries], ['Right', rightEntries]].map(([side, sideEntries]) => {
                          if (!sideEntries.length) return <div key={side} />;
                          const sideState = stateOf(sideEntries);
                          return (
                            <div key={side}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 5, cursor: 'pointer' }}
                                onClick={() => toggleKeys(keysOf(sideEntries), !sideState.checked)}>
                                <TriStateCheckbox
                                  checked={sideState.checked}
                                  indeterminate={sideState.indeterminate}
                                  onClick={e => e.stopPropagation()}
                                  onChange={e => toggleKeys(keysOf(sideEntries), e.target.checked)}
                                  style={{ width: 13, height: 13 }} />
                                <span style={{ fontSize: 10, color: '#7a8a99', textTransform: 'uppercase', letterSpacing: '0.05em', fontFamily: 'IBM Plex Mono, monospace' }}>{side}</span>
                              </div>
                              <div style={{ paddingLeft: 19 }}>
                                {sideEntries.map(([key, s]) => {
                                  const checked = structureVisible?.[key] !== false;
                                  const label = s.label.replace(/^(Left|Right)\s+/i, '');
                                  return (
                                    <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 5 }}>
                                      <TriStateCheckbox checked={checked}
                                        onChange={e => setStructureVisible(key, e.target.checked)}
                                        style={{ accentColor: s.color }} />
                                      <div style={{ width: 11, height: 11, borderRadius: 2, background: s.color, flexShrink: 0 }} />
                                      <span style={{ fontSize: 13, color: '#c8d4e0', fontFamily: 'IBM Plex Sans, sans-serif', lineHeight: 1.2 }}>{label}</span>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {structuresData && Object.keys(structuresData).length === 0 && (
        <div style={{ fontSize: 11, color: '#4a5568', fontFamily: 'IBM Plex Mono, monospace' }}>No structures found</div>
      )}
    </div>
  );
}
