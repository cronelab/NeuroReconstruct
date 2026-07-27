import React, { useMemo, useEffect, useState } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls, Html, PerspectiveCamera } from '@react-three/drei';
import * as THREE from 'three';

// ── Diverging color scale (blue → grey → red), modeled on webfm's dotColorScale ──
// Domain is symmetric [-domain, +domain] in baseline-z units; values clamp.
const NEG = [0x33, 0x77, 0xff];   // blue  (suppression)
const MID = [0x60, 0x66, 0x70];   // grey  (baseline)
const POS = [0xff, 0x44, 0x33];   // red   (activation)

function lerp(a, b, t) { return a + (b - a) * t; }
function rgb(c) { return `rgb(${c[0]|0},${c[1]|0},${c[2]|0})`; }

export function activityColor(value, domain = 6) {
  const t = Math.max(-1, Math.min(1, (value || 0) / domain));
  if (t >= 0) return rgb([lerp(MID[0], POS[0], t), lerp(MID[1], POS[1], t), lerp(MID[2], POS[2], t)]);
  const u = -t;
  return rgb([lerp(MID[0], NEG[0], u), lerp(MID[1], NEG[1], u), lerp(MID[2], NEG[2], u)]);
}

// ── Brain surface — same BufferGeometry pattern as Viewer3D.BrainMesh ─────────────
function BrainSurface({ meshData, opacity }) {
  const geometry = useMemo(() => {
    if (!meshData) return null;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(meshData.vertices), 3));
    geo.setIndex(new THREE.BufferAttribute(new Uint32Array(meshData.faces), 1));
    geo.computeVertexNormals();
    return geo;
  }, [meshData]);
  if (!geometry) return null;
  return (
    <mesh geometry={geometry}>
      <meshPhysicalMaterial color="#b8c4cc" opacity={opacity} transparent={opacity < 1}
        side={THREE.DoubleSide} roughness={0.7} metalness={0.05} depthWrite={opacity > 0.5} />
    </mesh>
  );
}

// ── Activity-driven contact sphere ────────────────────────────────────────────────
function ActivityContact({ position, value, label, domain, baseRadius }) {
  const [hovered, setHovered] = useState(false);
  const color = activityColor(value, domain);
  // Radius grows with activation magnitude, mirroring webfm's |value|-scaling.
  const radius = baseRadius * (0.7 + Math.min(1.6, Math.abs(value || 0) / domain));
  return (
    <group position={position}>
      <mesh
        onPointerOver={(e) => { e.stopPropagation(); setHovered(true); document.body.style.cursor = 'pointer'; }}
        onPointerOut={() => { setHovered(false); document.body.style.cursor = 'default'; }}
      >
        <sphereGeometry args={[radius, 16, 16]} />
        <meshPhysicalMaterial color={color} emissive={color}
          emissiveIntensity={hovered ? 0.7 : 0.35} roughness={0.25} metalness={0.5} />
      </mesh>
      {hovered && (
        <Html distanceFactor={80} center style={{ pointerEvents: 'none' }}>
          <div style={{
            background: 'rgba(10,12,16,0.95)', border: `1px solid ${color}`,
            borderRadius: 4, padding: '3px 8px',
            fontFamily: 'IBM Plex Mono, monospace', fontSize: 11, color: '#e8edf2',
            whiteSpace: 'nowrap', boxShadow: `0 0 8px ${color}66`,
          }}>
            {label}
            <span style={{ color: '#7a8a99', marginLeft: 6 }}>
              {value >= 0 ? '+' : ''}{(value || 0).toFixed(2)}z
            </span>
          </div>
        </Html>
      )}
    </group>
  );
}

function SceneLights() {
  return (
    <>
      <ambientLight intensity={0.5} />
      <directionalLight position={[100, 100, 100]} intensity={0.8} />
      <directionalLight position={[-100, -100, 50]} intensity={0.3} />
      <pointLight position={[0, 0, 150]} intensity={0.4} color="#cce8ff" />
    </>
  );
}

function CameraSetup({ meshData }) {
  const { camera } = useThree();
  useEffect(() => {
    if (!meshData?.bounds) return;
    const { min, max } = meshData.bounds;
    const size = Math.max(Math.abs(max[0] - min[0]), Math.abs(max[1] - min[1]), Math.abs(max[2] - min[2]));
    camera.position.set(0, -size * 1.8, 0);
    camera.up.set(0, 0, 1);
    camera.near = 0.1;
    camera.far = size * 20;
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
  }, [meshData, camera]);
  return null;
}

function LoadingOverlay({ message }) {
  return (
    <Html center style={{ pointerEvents: 'none' }}>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, color: '#7a8a99', fontFamily: 'IBM Plex Sans, sans-serif' }}>
        <div style={{ width: 32, height: 32, border: '2px solid #1e2530', borderTop: '2px solid #00d4ff', borderRadius: '50%', animation: 'spin 1s linear infinite' }} />
        <span style={{ fontSize: 12 }}>{message || 'Loading...'}</span>
      </div>
    </Html>
  );
}

/**
 * 3D functional-mapping scene.
 *
 * Props:
 *   meshData   surface {vertices, faces, bounds}
 *   contacts   [{ name, pos:[x,y,z], value }]  — value at the current time index
 *   domain     color-scale half-range in baseline-z units
 *   brainOpacity, loading, loadingMessage
 */
export default function SeegViewer3D({ meshData, contacts = [], domain = 6, brainOpacity = 0.35, loading, loadingMessage }) {
  // Depth sEEG contacts are small; a fixed base radius reads well on both surfaces.
  const baseRadius = 1.8;
  return (
    <div style={{ width: '100%', height: '100%', background: '#0a0c10', position: 'relative' }}>
      <Canvas gl={{ antialias: true, alpha: false, logarithmicDepthBuffer: true }}>
        <PerspectiveCamera makeDefault fov={45} position={[0, 0, 300]} />
        <CameraSetup meshData={meshData} />
        <SceneLights />

        {loading && <LoadingOverlay message={loadingMessage} />}

        {meshData && !loading && <BrainSurface meshData={meshData} opacity={brainOpacity} />}

        {!loading && contacts.map((c) => (
          <ActivityContact key={c.name} position={c.pos} value={c.value} label={c.name}
            domain={domain} baseRadius={baseRadius} />
        ))}

        <OrbitControls enablePan enableZoom enableRotate
          zoomSpeed={1.2} panSpeed={0.8} rotateSpeed={0.6}
          mouseButtons={{ LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN }}
        />
      </Canvas>

      <div style={{
        position: 'absolute', bottom: 16, left: '50%', transform: 'translateX(-50%)',
        display: 'flex', gap: 20, color: '#4a5568',
        fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, letterSpacing: '0.05em', pointerEvents: 'none',
      }}>
        <span>LEFT DRAG — rotate</span>
        <span>RIGHT DRAG — pan</span>
        <span>SCROLL — zoom</span>
      </div>
    </div>
  );
}
