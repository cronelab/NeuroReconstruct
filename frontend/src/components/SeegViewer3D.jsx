import React, { useMemo, useEffect, useState, useCallback } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls, Html, PerspectiveCamera, Billboard } from '@react-three/drei';
import * as THREE from 'three';
import { buildStructureMeshes, structureAtPoint } from '../anatomy';
import { shaftColorOf } from '../seegColors';

// ── Diverging color scale (blue → white → red), modeled on webfm's dotColorScale ──
// Domain is symmetric [-domain, +domain] in baseline-z units; values clamp.
const NEG = [0x33, 0x77, 0xff];   // blue  (suppression)
const MID = [0xff, 0xff, 0xff];   // white (baseline / zero z)
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

// ── Structure overlay mesh (semi-transparent, non-interactive) ────────────────────
function StructureMesh({ meshData, color, opacity }) {
  const geo = useMemo(() => {
    if (!meshData?.vertices) return null;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(meshData.vertices), 3));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(meshData.faces), 1));
    g.computeVertexNormals();
    return g;
  }, [meshData]);
  if (!geo) return null;
  return (
    <mesh geometry={geo}>
      <meshPhongMaterial color={color} transparent={opacity < 1} opacity={opacity}
        side={THREE.DoubleSide} depthWrite={opacity >= 1} />
    </mesh>
  );
}

// ── Highlight ring (billboard so it always faces the camera) — mirrors Viewer3D ──
function ContactRing({ position, radius, color }) {
  return (
    <group position={position}>
      <Billboard>
        <mesh>
          <ringGeometry args={[radius * 1.7, radius * 2.5, 48]} />
          <meshBasicMaterial color={color} transparent opacity={0.92} side={THREE.DoubleSide} />
        </mesh>
      </Billboard>
    </group>
  );
}

// Neutral color/size for contacts outside the brain when "ignore outside" is on.
const INERT_COLOR = '#5f6b76';

// ── Activity-driven contact sphere ────────────────────────────────────────────────
// The hover tooltip + ring are rendered once by the parent (electrode-centric hover,
// matching Viewer3D); this just reports enter/leave and glows when active.
// `inert` contacts (outside the brain) keep their position but ignore the z-score:
// fixed neutral color and fixed radius, no color/size driven by activation.
function ActivityContact({ position, value, label, group, domain, baseRadius, hiliteColor, regionOf, active, inert, onEnter, onLeave }) {
  const color = inert ? INERT_COLOR : activityColor(value, domain);
  // Radius grows with activation magnitude, mirroring webfm's |value|-scaling.
  const radius = inert ? baseRadius : baseRadius * (0.7 + Math.min(1.6, Math.abs(value || 0) / domain));
  const enter = (e) => {
    e.stopPropagation();
    document.body.style.cursor = 'pointer';
    onEnter?.({ name: label, group, pos: position, radius, value, inert,
      color: hiliteColor, region: regionOf ? regionOf(position) : null });
  };
  const leave = () => { document.body.style.cursor = 'default'; onLeave?.(); };
  return (
    <group position={position}>
      <mesh onPointerOver={enter} onPointerOut={leave}>
        <sphereGeometry args={[radius, 16, 16]} />
        <meshPhysicalMaterial color={color} opacity={inert ? 0.75 : 1} transparent={inert}
          emissive={active ? hiliteColor : '#000'}
          emissiveIntensity={active ? 0.7 : (inert ? 0.05 : 0.2)} roughness={0.25} metalness={0.5} />
      </mesh>
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
 * 3D functional-mapping scene (native brain).
 *
 * Props:
 *   meshData        native surface {vertices, faces, bounds}
 *   contacts        [{ name, pos:[x,y,z], value }]  — value at the current time index
 *   domain          color-scale half-range in baseline-z units
 *   structuresData   { key: {label, color, vertices, faces, group} } or null
 *   structureVisible { key: bool } — per-structure visibility (undefined = shown)
 *   structureOpacity
 *   brainOpacity     native-brain surface opacity
 *   hoveredChannel   channel name to highlight (from the trace viewer), or null
 *   onHoverContact   (name|null) => void  — report the hovered contact to the parent
 */
export default function SeegViewer3D({
  meshData, contacts = [], domain = 6, brainOpacity = 0.4,
  structuresData = null, structureVisible = {}, structureOpacity = 0.4,
  shaftColors = {}, hoveredChannel = null, onHoverContact, loading, loadingMessage,
}) {
  const baseRadius = 1.8;

  // Non-rendered meshes for the contact→structure raycast (electrode-centric hover).
  const structMeshes = useMemo(() => buildStructureMeshes(structuresData), [structuresData]);
  const regionOf = useCallback((pos) => {
    if (!structuresData) return null;
    return structureAtPoint(new THREE.Vector3(pos[0], pos[1], pos[2]), structuresData, structMeshes);
  }, [structuresData, structMeshes]);

  // Directly-hovered contact (from pointer). Reported up for the trace cross-highlight.
  const [hovered, setHovered] = useState(null);   // { name, pos, radius, color, region, value }
  const handleEnter = useCallback((info) => { setHovered(info); onHoverContact?.(info.name); }, [onHoverContact]);
  const handleLeave = useCallback(() => { setHovered(null); onHoverContact?.(null); }, [onHoverContact]);

  // Effective highlight: the directly-hovered contact, else the one the trace panel
  // is hovering (hoveredChannel) — so hover works both ways, like Viewer3D.
  const effHover = useMemo(() => {
    if (hovered) return hovered;
    if (hoveredChannel) {
      const c = contacts.find((x) => x.name === hoveredChannel);
      if (c) return {
        name: c.name, pos: c.pos, value: c.value, inert: c.inside === false,
        radius: (c.inside === false ? baseRadius : baseRadius * (0.7 + Math.min(1.6, Math.abs(c.value || 0) / domain))),
        color: shaftColorOf(c.group, shaftColors), region: regionOf(c.pos),
      };
    }
    return null;
  }, [hovered, hoveredChannel, contacts, domain, shaftColors, regionOf]);

  return (
    <div style={{ width: '100%', height: '100%', background: '#0a0c10', position: 'relative' }}>
      <Canvas gl={{ antialias: true, alpha: false, logarithmicDepthBuffer: true }}>
        <PerspectiveCamera makeDefault fov={45} position={[0, 0, 300]} />
        <CameraSetup meshData={meshData} />
        <SceneLights />

        {loading && <LoadingOverlay message={loadingMessage} />}

        {meshData && !loading && <BrainSurface meshData={meshData} opacity={brainOpacity} />}

        {structuresData && Object.entries(structuresData).map(([key, s]) => (
          s.vertices && structureVisible?.[key] !== false
            ? <StructureMesh key={key} meshData={s} color={s.color || '#6a7a8a'} opacity={structureOpacity} />
            : null
        ))}

        {!loading && contacts.map((c) => (
          <ActivityContact key={c.name} position={c.pos} value={c.value} label={c.name} group={c.group}
            domain={domain} baseRadius={baseRadius} regionOf={regionOf}
            hiliteColor={shaftColorOf(c.group, shaftColors)}
            active={effHover?.name === c.name} inert={c.inside === false}
            onEnter={handleEnter} onLeave={handleLeave} />
        ))}

        {/* Electrode-centric highlight ring (shaft color), matching Viewer3D. */}
        {effHover && <ContactRing position={effHover.pos} radius={effHover.radius} color={effHover.color} />}

        <OrbitControls enablePan enableZoom enableRotate
          zoomSpeed={1.2} panSpeed={0.8} rotateSpeed={0.6}
          mouseButtons={{ LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN }}
        />
      </Canvas>

      {/* Electrode-centric hover tooltip — fixed top-right, matches Viewer3D. */}
      {effHover && (
        <div style={{
          position: 'absolute', top: 16, right: 16, pointerEvents: 'none',
          background: 'rgba(10,12,16,0.92)', border: `1px solid ${effHover.color}`,
          borderRadius: 4, padding: '8px 14px', maxWidth: 280,
          fontFamily: 'IBM Plex Mono, monospace', boxShadow: `0 0 12px ${effHover.color}44`,
        }}>
          <div style={{ color: effHover.color, fontSize: 15, fontWeight: 600 }}>
            {effHover.name}
            <span style={{ color: '#9fb3c8', fontWeight: 400, marginLeft: 8 }}>
              {effHover.value >= 0 ? '+' : ''}{(effHover.value || 0).toFixed(2)} z
            </span>
          </div>
          {effHover.inert && (
            <div style={{ color: '#c8975a', fontSize: 12, marginTop: 3 }}>outside brain · not scored</div>
          )}
          {effHover.region ? (
            effHover.region.inside ? (
              <div style={{ color: effHover.region.color, fontSize: 13, marginTop: 3 }}>{effHover.region.label}</div>
            ) : (
              <div style={{ marginTop: 3 }}>
                <div style={{ color: effHover.region.color, fontSize: 13 }}>{effHover.region.label}</div>
                <span style={{ color: '#7a8a99', fontSize: 12 }}>nearest · {effHover.region.dist.toFixed(1)} mm</span>
              </div>
            )
          ) : (
            <span style={{ color: '#7a8a99', fontSize: 13 }}>unlabelled</span>
          )}
        </div>
      )}

      <div style={{
        position: 'absolute', bottom: 12, left: '50%', transform: 'translateX(-50%)',
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
