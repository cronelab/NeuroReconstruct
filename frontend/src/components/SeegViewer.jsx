import React, { useEffect, useRef, useState, useMemo } from 'react';
import { useAppStore } from '../store';
import {
  listSeeg, uploadSeeg, computeSeegActivity, getMniTemplateMesh, getMesh, getReconstruction,
} from '../api';
import SeegViewer3D, { activityColor } from './SeegViewer3D';

const BANDS = [
  ['delta', 'Delta 1–4'], ['theta', 'Theta 4–8'], ['alpha', 'Alpha 8–13'],
  ['beta', 'Beta 13–30'], ['gamma', 'Gamma 30–70'], ['high_gamma', 'High-γ 70–150'],
];

const panel = {
  width: 320, flexShrink: 0, background: '#0d1015', borderRight: '1px solid #1e2530',
  padding: 16, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 16,
  fontFamily: 'IBM Plex Sans, sans-serif', color: '#c8d4e0',
};
const label = { fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase', color: '#7a8a99', marginBottom: 6 };
const seg = (active) => ({
  padding: '5px 10px', fontSize: 11, fontFamily: 'IBM Plex Mono, monospace', cursor: 'pointer',
  border: `1px solid ${active ? '#00d4ff55' : '#2a3340'}`, borderRadius: 4,
  background: active ? '#002233' : 'transparent', color: active ? '#00d4ff' : '#7a8a99',
});

function ColorBar({ domain }) {
  const stops = [-domain, -domain / 2, 0, domain / 2, domain];
  return (
    <div>
      <div style={label}>Activation (baseline z)</div>
      <div style={{
        height: 12, borderRadius: 3,
        background: `linear-gradient(to right, ${activityColor(-domain, domain)}, ${activityColor(0, domain)}, ${activityColor(domain, domain)})`,
      }} />
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4, fontSize: 9, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace' }}>
        {stops.map((s) => <span key={s}>{s > 0 ? '+' : ''}{s.toFixed(0)}</span>)}
      </div>
    </div>
  );
}

export default function SeegViewer({ reconId, onBack }) {
  const {
    reconstruction, setReconstruction,
    seegRecordings, setSeegRecordings, seegRecordingId, setSeegRecordingId,
    seegActivity, setSeegActivity, seegBand, setSeegBand,
    seegPre, setSeegPre, seegPost, setSeegPost,
    seegSurface, setSeegSurface, seegTemplateMesh, setSeegTemplateMesh,
    seegTimeIndex, setSeegTimeIndex, seegPlaying, setSeegPlaying,
  } = useAppStore();

  const [nativeMesh, setNativeMesh] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [computing, setComputing] = useState(false);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);

  // Draft window inputs — applied to the store (which triggers recompute) only on
  // commit, so typing doesn't fire a recompute on every keystroke.
  const [preDraft, setPreDraft] = useState(seegPre);
  const [postDraft, setPostDraft] = useState(seegPost);
  useEffect(() => { setPreDraft(seegPre); setPostDraft(seegPost); }, [seegPre, seegPost]);
  const windowDirty = preDraft !== seegPre || postDraft !== seegPost;
  const applyWindow = () => {
    const pre = Math.max(10, Math.min(2000, Math.round(Number(preDraft) || 0)));
    const post = Math.max(50, Math.min(4000, Math.round(Number(postDraft) || 0)));
    setSeegPre(pre); setSeegPost(post);
  };

  // ── Initial load: reconstruction, recordings, surfaces ────────────────────────
  useEffect(() => {
    if (!reconId) return;
    getReconstruction(reconId).then((r) => setReconstruction(r.data)).catch(() => {});
    listSeeg(reconId).then((r) => {
      setSeegRecordings(r.data);
      if (r.data.length && !seegRecordingId) setSeegRecordingId(r.data[0].id);
    }).catch(() => {});
    getMesh(reconId).then((r) => setNativeMesh(r.data)).catch(() => setNativeMesh(null));
    if (!seegTemplateMesh) {
      getMniTemplateMesh().then((r) => setSeegTemplateMesh(r.data)).catch(() => {});
    }
  }, [reconId]);

  // ── Compute activity whenever recording / band / window changes ───────────────
  useEffect(() => {
    if (!reconId || !seegRecordingId) return;
    setComputing(true);
    setError(null);
    setSeegPlaying(false);
    computeSeegActivity(reconId, seegRecordingId, { band: seegBand, window_ms: [-seegPre, seegPost] })
      .then((r) => setSeegActivity(r.data))
      .catch((e) => { setError(e?.response?.data?.detail || 'Could not compute activity'); setSeegActivity(null); })
      .finally(() => setComputing(false));
  }, [reconId, seegRecordingId, seegBand, seegPre, seegPost]);

  // If MNI coords aren't available for this recording, force native surface.
  const hasMni = seegActivity?.has_mni;
  useEffect(() => {
    if (seegSurface === 'mni' && seegActivity && !hasMni) setSeegSurface('native');
  }, [hasMni, seegActivity]);

  // ── Playback ──────────────────────────────────────────────────────────────────
  const nFrames = seegActivity?.times?.length || 0;
  useEffect(() => {
    if (!seegPlaying || nFrames < 2) return undefined;
    const id = setInterval(() => {
      const { seegTimeIndex: t } = useAppStore.getState();
      setSeegTimeIndex((t + 1) % nFrames);
    }, 60);
    return () => clearInterval(id);
  }, [seegPlaying, nFrames]);

  // ── Domain (color-scale half-range): robust max of |activity| ─────────────────
  const domain = useMemo(() => {
    if (!seegActivity?.activity?.length) return 6;
    let m = 0;
    for (const row of seegActivity.activity) for (const v of row) m = Math.max(m, Math.abs(v));
    return Math.max(3, Math.min(15, Math.round(m)));
  }, [seegActivity]);

  // ── Resolve contacts for the current surface + time index ─────────────────────
  const surfaceMesh = seegSurface === 'mni' ? seegTemplateMesh : nativeMesh;
  const contacts = useMemo(() => {
    if (!seegActivity || !surfaceMesh) return [];
    const frame = seegActivity.activity[seegTimeIndex] || [];
    const coordsMap = seegSurface === 'mni' ? seegActivity.coords_mni : seegActivity.coords_native;
    const center = seegSurface === 'mni' ? (seegTemplateMesh?.center || [0, 0, 0]) : [0, 0, 0];
    const out = [];
    seegActivity.channels.forEach((name, i) => {
      const c = coordsMap[name];
      if (!c) return;
      out.push({ name, value: frame[i] ?? 0, pos: [c[0] - center[0], c[1] - center[1], c[2] - center[2]] });
    });
    return out;
  }, [seegActivity, surfaceMesh, seegSurface, seegTimeIndex, seegTemplateMesh]);

  const handleUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const r = await uploadSeeg(reconId, file);
      const list = await listSeeg(reconId);
      setSeegRecordings(list.data);
      setSeegRecordingId(r.data.id);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Upload failed');
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const timeLabel = seegActivity ? `${seegActivity.times[seegTimeIndex]} ms` : '';

  const matchedN = seegActivity?.matched?.length || 0;
  const unmatchedN = seegActivity?.unmatched_channels?.length || 0;

  return (
    <div style={{ flex: 1, display: 'flex', minWidth: 0, height: '100%' }}>
      {/* ── Control panel ── */}
      <div style={panel}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 600, color: '#e8edf2' }}>sEEG Functional Mapping</div>
          <div style={{ fontSize: 11, color: '#7a8a99', marginTop: 2 }}>
            {reconstruction ? `Patient ${reconstruction.patient_id}` : '—'}
          </div>
        </div>

        {/* Upload */}
        <div>
          <div style={label}>Recording (NeurosEEGRead .h5)</div>
          <input ref={fileRef} type="file" accept=".h5,.hdf5" onChange={handleUpload} style={{ display: 'none' }} />
          <button onClick={() => fileRef.current?.click()} disabled={uploading}
            style={{ ...seg(false), width: '100%', textAlign: 'left', opacity: uploading ? 0.6 : 1 }}>
            {uploading ? '⟳ Uploading…' : '⤒ Upload h5'}
          </button>
          {seegRecordings.length > 0 && (
            <select value={seegRecordingId || ''} onChange={(e) => setSeegRecordingId(Number(e.target.value))}
              style={{ width: '100%', marginTop: 8, padding: '5px 8px', background: '#111418', color: '#c8d4e0',
                border: '1px solid #2a3340', borderRadius: 4, fontSize: 11, fontFamily: 'IBM Plex Mono, monospace' }}>
              {seegRecordings.map((r) => (
                <option key={r.id} value={r.id}>{r.task || r.filename}</option>
              ))}
            </select>
          )}
        </div>

        {/* Band */}
        <div>
          <div style={label}>Frequency band</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {BANDS.map(([key, txt]) => (
              <div key={key} onClick={() => setSeegBand(key)} style={seg(seegBand === key)}>{txt}</div>
            ))}
          </div>
        </div>

        {/* Trial-averaged mapping — peri-stimulus window */}
        <div>
          <div style={label}>Mapping mode</div>
          <div style={{ fontSize: 11, color: '#c8d4e0', fontFamily: 'IBM Plex Mono, monospace', marginBottom: 12 }}>
            Trial-averaged
            <span style={{ color: '#4a5568', marginLeft: 8 }}>· stimulus-locked, averaged across trials</span>
          </div>
          <div style={label}>Peri-stimulus window (ms)</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ fontSize: 10, color: '#7a8a99' }}>−</span>
              <input type="number" min={10} max={2000} step={50} value={preDraft}
                onChange={(e) => setPreDraft(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && applyWindow()}
                style={{ width: 56, padding: '4px 6px', background: '#111418', color: '#c8d4e0',
                  border: '1px solid #2a3340', borderRadius: 4, fontSize: 11, fontFamily: 'IBM Plex Mono, monospace' }} />
            </div>
            <span style={{ fontSize: 10, color: '#4a5568' }}>to</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ fontSize: 10, color: '#7a8a99' }}>+</span>
              <input type="number" min={50} max={4000} step={50} value={postDraft}
                onChange={(e) => setPostDraft(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && applyWindow()}
                style={{ width: 56, padding: '4px 6px', background: '#111418', color: '#c8d4e0',
                  border: '1px solid #2a3340', borderRadius: 4, fontSize: 11, fontFamily: 'IBM Plex Mono, monospace' }} />
            </div>
            <button onClick={applyWindow} disabled={!windowDirty}
              style={{ ...seg(windowDirty), padding: '4px 10px', opacity: windowDirty ? 1 : 0.4,
                cursor: windowDirty ? 'pointer' : 'default' }}>Apply</button>
          </div>
          <div style={{ fontSize: 10, color: '#7a8a99', marginTop: 5 }}>
            −pre = baseline (pre-onset) · +post = activation shown (post-onset)
          </div>
        </div>

        {/* Surface */}
        <div>
          <div style={label}>Brain surface</div>
          <div style={{ display: 'flex', gap: 6 }}>
            <div onClick={() => hasMni && setSeegSurface('mni')}
              style={{ ...seg(seegSurface === 'mni'), flex: 1, textAlign: 'center', opacity: hasMni ? 1 : 0.4, cursor: hasMni ? 'pointer' : 'not-allowed' }}
              title={hasMni ? '' : 'Run "Export to MNI" on this reconstruction first'}>MNI152</div>
            <div onClick={() => setSeegSurface('native')}
              style={{ ...seg(seegSurface === 'native'), flex: 1, textAlign: 'center' }}>Native</div>
          </div>
          {!hasMni && seegActivity && (
            <div style={{ fontSize: 10, color: '#ffab40', marginTop: 6 }}>
              No MNI coordinates — run “Export to MNI” in the reconstruction viewer to enable the atlas surface.
            </div>
          )}
        </div>

        <ColorBar domain={domain} />

        {/* Coverage */}
        {seegActivity && (
          <div>
            <div style={label}>Channel coverage</div>
            <div style={{ fontSize: 11, fontFamily: 'IBM Plex Mono, monospace' }}>
              <span style={{ color: '#00e676' }}>{matchedN} mapped</span>
              {unmatchedN > 0 && <span style={{ color: '#ffab40', marginLeft: 10 }}>{unmatchedN} unmatched</span>}
            </div>
            {unmatchedN > 0 && (
              <div style={{ fontSize: 10, color: '#7a8a99', marginTop: 6, wordBreak: 'break-word' }}>
                No contact for: {seegActivity.unmatched_channels.slice(0, 24).join(', ')}
                {unmatchedN > 24 ? ` +${unmatchedN - 24} more` : ''}
              </div>
            )}
            <div style={{ fontSize: 10, color: '#7a8a99', marginTop: 4 }}>
              {seegActivity.n_trials} trials averaged
            </div>
          </div>
        )}

        {error && <div style={{ fontSize: 11, color: '#ff8a80' }}>{error}</div>}

        <div style={{ flex: 1 }} />
        {onBack && (
          <button onClick={onBack} style={{ ...seg(false), textAlign: 'center' }}>← Back to reconstruction</button>
        )}
      </div>

      {/* ── Viewer + scrubber ── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <div style={{ flex: 1, position: 'relative' }}>
          <SeegViewer3D
            meshData={surfaceMesh}
            contacts={contacts}
            domain={domain}
            loading={computing || !surfaceMesh}
            loadingMessage={computing ? 'Computing band activity…' : 'Loading surface…'}
          />
          {seegActivity && seegSurface === 'mni' && (
            <div style={{ position: 'absolute', top: 12, left: 12, fontSize: 10, color: '#7a8a99',
              fontFamily: 'IBM Plex Mono, monospace', background: '#0d1015aa', padding: '4px 8px', borderRadius: 3 }}>
              MNI152 template · {seegActivity.band}
            </div>
          )}
        </div>

        {/* Time scrubber */}
        {seegActivity && nFrames > 0 && (
          <div style={{ height: 56, background: '#0d1015', borderTop: '1px solid #1e2530',
            display: 'flex', alignItems: 'center', gap: 14, padding: '0 18px' }}>
            <button onClick={() => setSeegPlaying(!seegPlaying)}
              style={{ ...seg(seegPlaying), padding: '6px 12px' }}>
              {seegPlaying ? '❚❚ Pause' : '▶ Play'}
            </button>
            <input type="range" min={0} max={nFrames - 1} value={seegTimeIndex}
              onChange={(e) => { setSeegPlaying(false); setSeegTimeIndex(Number(e.target.value)); }}
              style={{ flex: 1, accentColor: '#00d4ff' }} />
            <span style={{ minWidth: 78, textAlign: 'right', fontSize: 12, color: '#e8edf2',
              fontFamily: 'IBM Plex Mono, monospace' }}>{timeLabel}</span>
            <span style={{ fontSize: 10, color: '#7a8a99', fontFamily: 'IBM Plex Mono, monospace' }}>peri-stimulus</span>
          </div>
        )}
      </div>
    </div>
  );
}
