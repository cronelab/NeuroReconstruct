import React, { useState, useCallback, useMemo } from 'react';
import SliceViewer from './SliceViewer';
import { useAppStore } from '../store';

/**
 * View switcher for the sEEG viewer's centre pane: the 3D brain, or one of the three
 * MRI slice planes with the electrode contacts on it.
 *
 * The same selector column and locator arrangement as the reconstruction viewer's
 * MultiViewLayout, without any of its editing workflow (upload, fusion, registration
 * review) -- none of which belongs in a functional-mapping session. Slice panes share
 * their position across axes, so switching planes lands on the same anatomy.
 *
 * The 3D view stays mounted while a slice is shown, so its WebGL context, camera and
 * the running playback are all still there when you switch back.
 */

const VIEWS = [
  { id: '3d',       label: '3D',       icon: '⬡', color: '#ffdd00' },
  { id: 'sagittal', label: 'Sagittal', icon: '◧', color: '#ff6b6b' },
  { id: 'axial',    label: 'Axial',    icon: '⬒', color: '#81c784' },
  { id: 'coronal',  label: 'Coronal',  icon: '◨', color: '#4fc3f7' },
];

const AXES = ['sagittal', 'axial', 'coronal'];

export default function SeegSliceViews({
  reconId, viewer3D, activityContacts, activityDomain, shaftColors,
  hoveredChannel, onHoverContact,
}) {
  const { reconstruction } = useAppStore();
  const [activeView, setActiveView] = useState('3d');
  // Planes opened at least once. A pane mounts on first use rather than up front --
  // three slice viewers prefetching from the first paint would compete with the
  // activity computation, which is what the user is actually waiting for -- and then
  // stays mounted, so coming back to a plane does not re-fetch what it already holds.
  const [opened, setOpened] = useState({});
  const openView = useCallback((id) => {
    setActiveView(id);
    if (id !== '3d') setOpened((o) => (o[id] ? o : { ...o, [id]: true }));
  }, []);

  // Shared slice positions: { axis -> { idx, count } }
  const [slicePositions, setSlicePositions] = useState({
    sagittal: { idx: 0, count: 1 },
    axial:    { idx: 0, count: 1 },
    coronal:  { idx: 0, count: 1 },
  });
  const handleSliceChange = useCallback((axis, idx, count) => {
    setSlicePositions((prev) => (
      prev[axis].idx === idx && prev[axis].count === count
        ? prev
        : { ...prev, [axis]: { idx, count } }
    ));
  }, []);

  // Where this plane sits, drawn as a line on a thumbnail of a perpendicular one.
  // Identical geometry to the reconstruction viewer (see MultiViewLayout for why each
  // fraction is inverted).
  const locators = useMemo(() => ({
    axial: {
      refAxis: 'coronal', lineType: 'horizontal',
      fraction: slicePositions.axial.count > 1
        ? 1 - slicePositions.axial.idx / (slicePositions.axial.count - 1) : 0.5,
    },
    sagittal: {
      refAxis: 'coronal', lineType: 'vertical',
      fraction: slicePositions.sagittal.count > 1
        ? 1 - slicePositions.sagittal.idx / (slicePositions.sagittal.count - 1) : 0.5,
    },
    coronal: {
      refAxis: 'sagittal', lineType: 'vertical',
      fraction: slicePositions.coronal.count > 1
        ? 1 - slicePositions.coronal.idx / (slicePositions.coronal.count - 1) : 0.5,
    },
  }), [slicePositions]);

  // No MRI uploaded yet: there are no slices to show, so only the 3D view is offered
  // (uploading is done in the reconstruction viewer, not here).
  const hasMri = reconstruction?.has_mri !== false;
  const views = hasMri ? VIEWS : VIEWS.slice(0, 1);

  return (
    <div style={{ flex: 1, display: 'flex', minWidth: 0, minHeight: 0 }}>
      {/* view selector */}
      <div style={{ flex: '0 0 92px', display: 'flex', flexDirection: 'column', gap: 4,
        padding: '8px 6px', background: '#0a0c10', borderRight: '1px solid #1e2530',
        overflowY: 'auto' }}>
        {views.map((view) => {
          const isActive = activeView === view.id;
          return (
            <button key={view.id} onClick={() => openView(view.id)}
              style={{ display: 'flex', flexDirection: 'column', alignItems: 'stretch', padding: 0,
                border: `2px solid ${isActive ? view.color : '#1e2530'}`, borderRadius: 5,
                cursor: 'pointer', background: isActive ? '#0d1015' : '#0a0c10', overflow: 'hidden',
                boxShadow: isActive ? `0 0 8px ${view.color}44` : 'none', transition: 'all 0.15s' }}
              onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.borderColor = `${view.color}66`; }}
              onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.borderColor = '#1e2530'; }}>
              <div style={{ height: 54, background: '#000', overflow: 'hidden' }}>
                {view.id === '3d' ? (
                  <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center',
                    justifyContent: 'center', color: isActive ? view.color : '#2a3340', fontSize: 24 }}>
                    {view.icon}
                  </div>
                ) : (
                  <SliceViewer reconId={reconId} axis={view.id} isThumbnail />
                )}
              </div>
              <div style={{ padding: '4px 0', textAlign: 'center', fontSize: 12, fontWeight: 600,
                fontFamily: 'IBM Plex Mono, monospace', letterSpacing: '0.04em',
                color: isActive ? view.color : '#4a5568',
                background: isActive ? '#0d1015' : 'transparent' }}>
                {view.label}
              </div>
            </button>
          );
        })}
      </div>

      {/* the view itself */}
      <div style={{ flex: 1, position: 'relative', minWidth: 0, background: '#000' }}>
        <div style={{ position: 'absolute', inset: 0,
          display: activeView === '3d' ? 'block' : 'none' }}>
          {viewer3D}
        </div>
        {hasMri && AXES.map((ax) => (
          <div key={ax} style={{ position: 'absolute', inset: 0,
            display: activeView === ax ? 'block' : 'none' }}>
            {opened[ax] && (
              <SliceViewer
                reconId={reconId}
                axis={ax}
                syncSliceIdx={slicePositions[ax].count > 1 ? slicePositions[ax].idx : null}
                onSliceChange={(idx, count) => handleSliceChange(ax, idx, count)}
                locator={locators[ax]}
                /* Only the plane on screen follows the playhead. A hidden pane holding
                   the same array would redraw itself dozens of times a second for
                   nobody. */
                activityContacts={activeView === ax ? activityContacts : null}
                activityDomain={activityDomain}
                shaftColors={shaftColors}
                hoveredChannel={hoveredChannel}
                onHoverContact={onHoverContact}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
