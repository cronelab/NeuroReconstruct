import React, { useEffect, useState } from 'react';
import { GROUP_ORDER } from '../regions';

const MONO = 'IBM Plex Mono, monospace';

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

const GROUP_NAMES = { cortex: 'regions' };

/**
 * A collapsible on/off list of brain regions, laid out like the structure
 * tree (Group -> Left/Right -> Region) but folded by default: the list itself
 * starts collapsed, and so does each group, so dozens of parcels cost one row
 * until someone wants them. Everything starts ON -- the caller decides what
 * "on" means (a parcel coloured, a region drawn in the overlay).
 *
 *   entries  [{ key, label, color, group, side: 'l' | 'r' | null }]
 *   isOn     key -> bool
 *   setOn    (keys, on) -> void, for one region or a whole group at once
 *   unit     word after the count ("4/62 coloured")
 *   note     one line shown under the header while the list is open
 *   offSwatch  what a region's swatch shows when off -- white where "off"
 *              means drawn white, dim where it means not drawn at all
 *   onToggleAll  replaces the header checkbox's plain all-on/all-off, e.g.
 *              with one that remembers the selection it hid
 */
export default function RegionToggleList({
  title, entries, isOn, setOn, unit = 'on', note = null, defaultOpen = false, maxHeight = 360,
  offSwatch = '#2a3340', onToggleAll = null,
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [expanded, setExpanded] = useState({});

  const stateOf = (list) => {
    const on = list.filter(e => isOn(e.key)).length;
    return { on, checked: list.length > 0 && on === list.length, indeterminate: on > 0 && on < list.length };
  };
  const keysOf = (list) => list.map(e => e.key);
  // A partly-on set turns fully on, as the structure tree's checkboxes do.
  const flip = (list) => setOn(keysOf(list), !stateOf(list).checked);

  const all = stateOf(entries);
  const groups = GROUP_ORDER.filter(g => entries.some(e => e.group === g));

  if (!entries.length) return null;

  return (
    <div style={{ marginTop: 6 }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '4px 0', cursor: 'pointer' }}
      >
        <span style={{ fontSize: 10, color: '#7a8a99', width: 10, textAlign: 'center', flexShrink: 0 }}>{open ? '▾' : '▸'}</span>
        <TriStateCheckbox
          checked={all.checked}
          indeterminate={all.indeterminate}
          onClick={e => e.stopPropagation()}
          onChange={() => (onToggleAll ? onToggleAll() : flip(entries))}
          style={{ accentColor: '#74C0FC' }}
        />
        <span style={{ fontSize: 12, color: '#c8d4e0', fontFamily: MONO, flex: 1 }}>{title}</span>
        <span style={{ fontSize: 10, color: '#4a5568', fontFamily: MONO }}>{all.on}/{entries.length} {unit}</span>
      </div>

      {open && (
        <div style={{ maxHeight, overflowY: 'auto', paddingLeft: 4 }}>
          {note && (
            <div style={{ fontSize: 10.5, color: '#4a5568', fontFamily: MONO, padding: '0 0 6px 17px' }}>{note}</div>
          )}
          {groups.map(group => {
            const items = entries.filter(e => e.group === group);
            const st = stateOf(items);
            const isExpanded = !!expanded[group];
            const midline = items.filter(e => !e.side);
            const left = items.filter(e => e.side === 'l');
            const right = items.filter(e => e.side === 'r');
            return (
              <div key={group} style={{ borderBottom: '1px solid #1a1e24' }}>
                <div
                  onClick={() => setExpanded(x => ({ ...x, [group]: !x[group] }))}
                  style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 0', cursor: 'pointer' }}
                >
                  <span style={{ fontSize: 10, color: '#7a8a99', width: 10, textAlign: 'center', flexShrink: 0 }}>{isExpanded ? '▾' : '▸'}</span>
                  <TriStateCheckbox
                    checked={st.checked}
                    indeterminate={st.indeterminate}
                    onClick={e => e.stopPropagation()}
                    onChange={() => flip(items)}
                    style={{ accentColor: '#74C0FC' }}
                  />
                  <span style={{ fontSize: 11, fontWeight: 600, color: '#c8d4e0', textTransform: 'uppercase', letterSpacing: '0.06em', fontFamily: MONO, flex: 1 }}>
                    {GROUP_NAMES[group] || group}
                  </span>
                  <span style={{ fontSize: 10, color: '#4a5568', fontFamily: MONO }}>{st.on}/{items.length}</span>
                </div>

                {isExpanded && (
                  <div style={{ paddingLeft: 20, paddingBottom: 8 }}>
                    {midline.map(e => (
                      <RegionRow key={e.key} entry={e} on={isOn(e.key)} setOn={setOn} offSwatch={offSwatch} />
                    ))}
                    {(left.length > 0 || right.length > 0) && (
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 10 }}>
                        {[['Left', left], ['Right', right]].map(([side, list]) => {
                          if (!list.length) return <div key={side} />;
                          const ss = stateOf(list);
                          return (
                            <div key={side}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 5, cursor: 'pointer' }}
                                onClick={() => flip(list)}>
                                <TriStateCheckbox
                                  checked={ss.checked}
                                  indeterminate={ss.indeterminate}
                                  onClick={e => e.stopPropagation()}
                                  onChange={() => flip(list)}
                                  style={{ width: 13, height: 13 }} />
                                <span style={{ fontSize: 10, color: '#7a8a99', textTransform: 'uppercase', letterSpacing: '0.05em', fontFamily: MONO }}>{side}</span>
                              </div>
                              <div style={{ paddingLeft: 19 }}>
                                {list.map(e => <RegionRow key={e.key} entry={e} on={isOn(e.key)} setOn={setOn} offSwatch={offSwatch} />)}
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
    </div>
  );
}

function RegionRow({ entry, on, setOn, offSwatch }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 5, cursor: 'pointer' }}>
      <TriStateCheckbox checked={on}
        onChange={e => setOn([entry.key], e.target.checked)}
        style={{ accentColor: entry.color }} />
      <div style={{ width: 11, height: 11, borderRadius: 2, background: on ? entry.color : offSwatch, flexShrink: 0 }} />
      <span style={{ fontSize: 13, color: on ? '#c8d4e0' : '#7a8a99', fontFamily: 'IBM Plex Sans, sans-serif', lineHeight: 1.2 }}>{entry.label}</span>
    </label>
  );
}
