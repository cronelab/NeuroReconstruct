import React, { useMemo, useEffect, useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import { useAppStore } from '../store';
import { decodeCorticalSurface } from '../meshCodec';
import { getCorticalSurface } from '../api';

// ── Cortical surface render mode ──────────────────────────────────────────────
// One opaque pial-like mesh, shaded so sulci and gyri read clearly, optionally
// recolored by DKT parcellation. Shared by Viewer3D and SeegViewer3D.
//
// Colour is baked into a vertex attribute on the CPU rather than computed in a
// shader: the modulation is static per vertex, so a shader would buy nothing and
// cost an onBeforeCompile hook to maintain.

// Depth beyond which a fundus is fully dark. Measured sulcal depth on real data
// runs 0 mm at a gyral crown to ~25 mm in the insula, but the median is ~4 mm and
// p75 ~14 mm, so ramping over the full range would leave almost the whole surface
// white. 8 mm puts the contrast where ordinary sulci actually live; the gamma
// lifts the shallow end so gyral crowns stay distinct from their own banks.
// Measured depth distribution on real data: p25 0.9 mm, p50 3.3 mm, p75 11.2 mm.
// Ramping to 5 mm rather than the full 25 mm range puts the whole contrast budget
// on the banks that are actually visible from outside -- the deep fundi are
// occluded by their own gyri, so spending range on them just washes out the rest.
const DEPTH_FULL_MM = 3.5;
const DEPTH_GAMMA = 0.55;
const DEPTH_FLOOR = 0.14;     // darkest shade; 1.0 = undarkened. Low on purpose:
// after SHADE_GAIN and the tone curve this lands a fundus near 0.33 sRGB against
// a near-white crown, which is the contrast that makes depth read at a glance.
// r3f defaults to ACES filmic tone mapping, which rolls 1.0 linear off to roughly
// 0.8 sRGB, so a crown authored at pure white would render mid-grey on its own.
// Vertex colours are linear floats with no 0..1 clamp, so the ramp is lifted
// above 1 to put crowns at white after the curve -- cheaper and less invasive
// than overriding tone mapping for one mode and recompiling every material.
//
// Keep this barely above 1. The lighting rig below already multiplies by ~1.4 on
// a surface facing the key light, so a larger gain does not make crowns any
// whiter, it only pushes the shallow half of the depth ramp past the clip point:
// at 1.55 everything under ~1.3 mm of depth rendered identically white and the
// folds went flat. What buys white crowns AND dark fundi is a short ramp
// (DEPTH_FULL_MM) rather than a big gain.
const SHADE_GAIN = 1.15;
const UNLABELED = [0.62, 0.65, 0.69];

// Per-vertex shade factor in [DEPTH_FLOOR, 1] from the uint8 sulcal depth.
function shadeFactors(sulc, sulcRangeMm) {
  const [lo, hi] = sulcRangeMm;
  const span = (hi - lo) || 1;
  const out = new Float32Array(sulc.length);
  for (let i = 0; i < sulc.length; i++) {
    const mm = lo + (sulc[i] / 255) * span;
    const d = Math.pow(Math.min(mm / DEPTH_FULL_MM, 1), DEPTH_GAMMA);
    out[i] = (1 - (1 - DEPTH_FLOOR) * d) * SHADE_GAIN;
  }
  return out;
}

// Parcel id -> linear RGB. THREE.Color.setStyle applies the sRGB->linear
// conversion under ColorManagement; writing hex/255 straight into a
// BufferAttribute would not, and the parcels would not match the swatches
// StructurePanel renders from the same catalog.
function parcelPalette(parcelColors) {
  const c = new THREE.Color();
  const out = new Map();
  for (const key in parcelColors) {
    c.setStyle(parcelColors[key]);
    out.set(Number(key), [c.r, c.g, c.b]);
  }
  return out;
}

function writeColors(attr, { sulc, parcel, sulcRangeMm, parcelColors }, colorBy) {
  const shade = shadeFactors(sulc, sulcRangeMm);
  const arr = attr.array;
  if (colorBy === 'parcellation') {
    const pal = parcelPalette(parcelColors);
    for (let i = 0; i < shade.length; i++) {
      const rgb = pal.get(parcel ? parcel[i] : 0) || UNLABELED;
      const s = shade[i];
      arr[i * 3] = rgb[0] * s;
      arr[i * 3 + 1] = rgb[1] * s;
      arr[i * 3 + 2] = rgb[2] * s;
    }
  } else {
    for (let i = 0; i < shade.length; i++) {
      const s = shade[i];
      arr[i * 3] = s; arr[i * 3 + 1] = s; arr[i * 3 + 2] = s;
    }
  }
  attr.needsUpdate = true;
}

// A key light parented to the camera. With only fixed directionals some orbit
// angles go flat or backlit, which is exactly when folds stop reading.
function HeadLight({ intensity = 0.9 }) {
  const ref = useRef();
  const { camera } = useThree();
  useFrame(() => {
    if (ref.current) {
      ref.current.position.copy(camera.position);
      ref.current.target.position.set(0, 0, 0);
      ref.current.target.updateMatrixWorld();
    }
  });
  return (
    <>
      <directionalLight ref={ref} intensity={intensity} />
      <hemisphereLight args={['#dfe9f5', '#20262e', 0.5]} />
      <ambientLight intensity={0.3} />
    </>
  );
}

// Both canvases render this surface, and both mount at once (MultiViewLayout
// hides inactive panes with display:none rather than unmounting, so the WebGL
// context survives). Without a shared in-flight guard they would each fire the
// same multi-megabyte request. The decoded result is cached in the store, so the
// typed arrays exist once no matter how many canvases draw them.
const inFlight = new Map();

/**
 * Lazily load the cortical surface when the mode is switched on. Returns
 * { loading, unavailable } -- `unavailable` means this reconstruction has no DKT
 * volume yet, so the caller should hide the mode rather than show it broken.
 */
export function useCorticalSurfaceData(reconId, token) {
  const { brainRenderMode, corticalData, setCorticalData } = useAppStore();
  const [loading, setLoading] = React.useState(false);
  const active = brainRenderMode === 'cortical';

  useEffect(() => {
    if (!active || !reconId || corticalData) return undefined;
    let cancelled = false;
    const key = `${reconId}:${token || ''}`;
    if (!inFlight.has(key)) {
      inFlight.set(key, getCorticalSurface(reconId, token)
        .then(r => decodeCorticalSurface(r.data))
        .catch(err => (err?.response?.status === 404 ? 'unavailable' : Promise.reject(err)))
        .finally(() => inFlight.delete(key)));
    }
    setLoading(true);
    inFlight.get(key)
      .then(d => { if (!cancelled) setCorticalData(d, reconId); })
      .catch(err => {
        console.error('[cortical] load failed', err);
        if (!cancelled) setCorticalData('unavailable', reconId);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [active, reconId, token, corticalData, setCorticalData]);

  return {
    loading: loading && !corticalData,
    unavailable: corticalData === 'unavailable',
    data: corticalData && corticalData !== 'unavailable' ? corticalData : null,
  };
}

/**
 * Props:
 *   data       decoded payload from meshCodec.decodeCorticalSurface
 *   colorBy    'plain' | 'parcellation'
 *   onHover    ({ label, color }|null) => void  — parcel under the cursor
 *   interactive  attach pointer handlers (off in place mode)
 */
export default function CorticalSurface({ data, colorBy = 'plain', onHover, interactive = true }) {
  const meshRef = useRef();

  const geometry = useMemo(() => {
    if (!data?.positions) return null;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(data.positions, 3));
    g.setIndex(new THREE.BufferAttribute(data.indices, 1));
    g.setAttribute('color', new THREE.BufferAttribute(
      new Float32Array(data.positions.length), 3));
    g.computeVertexNormals();
    g.computeBoundingSphere();
    g.computeBoundingBox();
    return g;
  }, [data]);

  // Recolor in place. Allocating a fresh BufferAttribute on every toggle would
  // orphan a GPU buffer each time, and this one is ~1.5 MB.
  useEffect(() => {
    if (geometry && data) writeColors(geometry.getAttribute('color'), data, colorBy);
  }, [geometry, data, colorBy]);

  // Three.js does not garbage-collect GPU buffers. At 250k faces this is the
  // first mesh in the app large enough that leaking it on every mode switch
  // would matter.
  useEffect(() => () => geometry?.dispose(), [geometry]);

  const handleMove = React.useCallback((e) => {
    if (!onHover || !data?.parcel || !e.face) return;
    e.stopPropagation();
    const id = data.parcel[e.face.a];
    onHover(id ? {
      label: data.parcelLabels?.[id] || `label ${id}`,
      color: data.parcelColors?.[id] || '#c8d4e0',
    } : null);
  }, [onHover, data]);

  const handleOut = React.useCallback(() => onHover?.(null), [onHover]);

  if (!geometry) return null;
  return (
    <>
      <HeadLight />
      <mesh
        ref={meshRef}
        geometry={geometry}
        onPointerMove={interactive ? handleMove : undefined}
        onPointerOut={interactive ? handleOut : undefined}
      >
        {/* Opaque by design: depth contacts are occluded, which is what makes
            the nested-transparency flicker impossible in this mode. */}
        <meshStandardMaterial vertexColors roughness={0.82} metalness={0.02}
          side={THREE.FrontSide} />
      </mesh>
    </>
  );
}
