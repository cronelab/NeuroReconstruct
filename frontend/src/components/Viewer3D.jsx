import React, { useMemo, useEffect, useState } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls, Line, Html, PerspectiveCamera, Billboard } from '@react-three/drei';
import * as THREE from 'three';
import { useAppStore } from '../store';
import CTArtifactMesh from './CTArtifactMesh';
import { isInsideMesh, structureAtPoint } from '../anatomy';

// ── Brain Mesh ────────────────────────────────────────────────────────────────
function BrainMesh({ meshData, opacity }) {
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

// ── Single contact — sphere ──────────────────────────────────────────────────
function Contact({ position, color, label, isManual, isSelected, onClick, radius = 1.5, onHover, onUnhover }) {
  const [hovered, setHovered] = useState(false);
  return (
    <group position={position}>
      <mesh
        onClick={(e) => { e.stopPropagation(); onClick?.(); }}
        onPointerOver={(e) => { e.stopPropagation(); setHovered(true); document.body.style.cursor = 'pointer'; onHover?.(); }}
        onPointerOut={() => { setHovered(false); document.body.style.cursor = 'default'; onUnhover?.(); }}
      >
        <sphereGeometry args={[radius, 16, 16]} />
        <meshPhysicalMaterial
          color={isSelected ? '#ffffff' : color}
          emissive={hovered || isSelected ? color : '#000000'}
          emissiveIntensity={isSelected ? 0.9 : hovered ? 0.5 : isManual ? 0.3 : 0.1}
          roughness={0.2} metalness={0.6}
        />
      </mesh>
      {(hovered || isSelected) && (
        <Html distanceFactor={80} center style={{ pointerEvents: 'none' }}>
          <div style={{
            background: 'rgba(10,12,16,0.95)', border: `1px solid ${color}`,
            borderRadius: 4, padding: '3px 8px',
            fontFamily: 'IBM Plex Mono, monospace', fontSize: 11, color: '#e8edf2',
            whiteSpace: 'nowrap', boxShadow: `0 0 8px ${color}44`,
          }}>
            {label}
            <span style={{ color: '#4a5568', fontSize: 9, marginLeft: 6 }}>{isManual ? 'manual' : 'auto'}</span>
          </div>
        </Html>
      )}
    </group>
  );
}

// ── Electrode shaft — spheres + connecting line ───────────────────────────────
function ElectrodeShaft({ shaft, onContactHover, onContactUnhover }) {
  const { selectedContactId, setSelectedContactId, shaftVisibility, contactScale } = useAppStore();
  const visible = shaftVisibility[shaft.id] !== false && shaft.visible;
  if (!visible || !shaft.contacts?.length) return null;

  const contacts = [...shaft.contacts]
    .filter(c => c.x_mm != null)
    .sort((a, b) => a.contact_number - b.contact_number);
  if (!contacts.length) return null;

  const color = shaft.color || '#00ff88';
  const linePoints = contacts.map(c => new THREE.Vector3(c.x_mm, c.y_mm, c.z_mm));

  // Sphere radius: depth = small (1.2mm), surface ECoG = larger (2.5mm), scaled by user preference
  const isDepth = shaft.electrode_type === 'depth';
  const radius = (isDepth ? 1.2 : 2.5) * (contactScale ?? 1.0);

  return (
    <group>
      {linePoints.length > 1 && (
        <Line points={linePoints} color={color} lineWidth={1.5} opacity={0.5} transparent />
      )}
      {contacts.map(contact => {
        const contactId = `${shaft.id}-${contact.contact_number}`;
        const pos = [contact.x_mm, contact.y_mm, contact.z_mm];
        const label = `${shaft.name}${contact.contact_number}${shaft.label ? ` (${shaft.label})` : ''}`;
        const isSelected = selectedContactId === contactId;
        return (
          <Contact key={contactId}
            position={pos} color={color} label={label}
            isManual={contact.is_manual} isSelected={isSelected}
            radius={radius}
            onClick={() => setSelectedContactId(isSelected ? null : contactId)}
            onHover={() => onContactHover?.(contactId, pos, color, label, radius)}
            onUnhover={() => onContactUnhover?.(contactId)}
          />
        );
      })}
    </group>
  );
}

// ── Contact highlight ring (billboard so it faces camera) ────────────────────
function ContactRing({ position, color, radius }) {
  const inner = radius * 1.7;
  const outer = radius * 2.5;
  return (
    <group position={position}>
      <Billboard>
        <mesh>
          <ringGeometry args={[inner, outer, 48]} />
          <meshBasicMaterial color={color} transparent opacity={0.92} side={THREE.DoubleSide} />
        </mesh>
      </Billboard>
    </group>
  );
}

// ── Scene lights ──────────────────────────────────────────────────────────────
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

// ── Camera setup ──────────────────────────────────────────────────────────────

function StructureMesh({ meshData, color, structKey, structLabel, opacity = 0.45, visible = true, interactive = true, onHover, onUnhover, onRegister }) {
  const meshRef = React.useRef();
  const geo = React.useMemo(() => {
    if (!meshData) return null;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(meshData.vertices), 3));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(meshData.faces), 1));
    g.computeVertexNormals();
    g.computeBoundingSphere();
    g.computeBoundingBox();
    return g;
  }, [meshData]);

  // Register/unregister this mesh with the parent so electrode-centric mode can
  // test contacts against every visible structure, not just the hovered one.
  React.useEffect(() => {
    if (!geo) return undefined;
    onRegister?.(structKey, meshRef.current);
    return () => onRegister?.(structKey, null);
  }, [geo, structKey, onRegister]);

  if (!geo) return null;
  return (
    // `visible` toggles whether the mesh is DRAWN, but the mesh always stays in the
    // scene so isInsideMesh can raycast it (Mesh.raycast ignores visibility) — this
    // is what lets electrode-centric hover name a structure even when it's hidden.
    //
    // Pointer handlers are attached only in structure-centric mode AND only while the
    // mesh is visible: in electrode-centric mode we want NO handlers so the small
    // contact spheres win the hover (r3f only raycasts objects that have handlers),
    // and a hidden structure must not intercept hover in structure-centric mode.
    <mesh ref={meshRef} geometry={geo} visible={visible}
      onPointerOver={interactive && visible ? (e) => { e.stopPropagation(); onHover?.(structKey, color, structLabel, meshRef.current); } : undefined}
      onPointerOut={interactive && visible ? (e) => { e.stopPropagation(); onUnhover?.(); } : undefined}
    >
      <meshPhongMaterial color={color} transparent opacity={opacity} side={THREE.DoubleSide} depthWrite={false} />
    </mesh>
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

// ── Loading overlay ───────────────────────────────────────────────────────────
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

// ── Main export ───────────────────────────────────────────────────────────────
// Point-in-mesh and nearest-cortical helpers live in ../anatomy (shared with the
// editor's auto-label action so both derive a contact's region identically).

export default function Viewer3D({ loading, loadingMessage, ctMeshData, ctMeshLoading, onContactPlaced, showMri, mriOpacity, ctThreshold, ctOpacityOverride, activeContactNumber, structuresData, structureVisible, structureOpacity }) {
  const { meshData, brainOpacity, reconstruction, isEditorMode, selectedShaftId, shaftVisibility, contactScale, placeMode } = useAppStore();
  const [hoveredStruct, setHoveredStruct] = React.useState(null);

  // Interaction mode has three states:
  //   'structure' → hover a structure surface, highlight the contacts inside it
  //   'electrode' → hover a contact, name the structure(s) it resides in
  //   'place'     → placeMode is on: click the CT to place contacts; hover is off
  // hoverMode selects between the two hover modes; placeMode (store) overrides both.
  const [hoverMode, setHoverMode] = React.useState('structure');
  const hoverModeRef = React.useRef(hoverMode);
  hoverModeRef.current = hoverMode;
  const placeModeRef = React.useRef(placeMode);
  placeModeRef.current = placeMode;
  const [hoveredContact, setHoveredContact] = React.useState(null);

  // Placement is only permitted in place mode; hovers are only active outside it.
  const activeMode = placeMode ? 'place' : hoverMode;

  // Leaving hover state behind when place mode turns on would strand a tooltip/ring.
  React.useEffect(() => {
    if (placeMode) { setHoveredStruct(null); setHoveredContact(null); }
  }, [placeMode]);

  // Registry of currently-rendered (visible) structure meshes, keyed by structure.
  // Populated by StructureMesh via onRegister; consulted in electrode-centric mode.
  const structMeshesRef = React.useRef(new Map());
  const registerStructMesh = React.useCallback((key, mesh) => {
    if (mesh) structMeshesRef.current.set(key, mesh);
    else structMeshesRef.current.delete(key);
  }, []);

  const structuresDataRef = React.useRef(structuresData);
  structuresDataRef.current = structuresData;

  const handleContactHover = React.useCallback((contactId, pos, color, label, radius) => {
    if (placeModeRef.current || hoverModeRef.current !== 'electrode') return;
    const p = new THREE.Vector3(pos[0], pos[1], pos[2]);
    const sd = structuresDataRef.current || {};
    // Region the contact sits in: enclosing structure, else nearest within 2 mm,
    // else null (unlabelled). Same resolution the auto-label uses.
    const region = structureAtPoint(p, sd, structMeshesRef.current);
    setHoveredContact({ id: contactId, pos, color, label, radius, region });
  }, []);

  const handleContactUnhover = React.useCallback(() => setHoveredContact(null), []);

  const handleStructureHover = React.useCallback((key, color, label, mesh) => {
    if (placeModeRef.current || hoverModeRef.current !== 'structure') return;
    if (!mesh) return;
    mesh.updateMatrixWorld(true);
    // Read directly from store state — bypasses any closure/ref staleness
    const { reconstruction: recon, shaftVisibility: sv, contactScale: cs } = useAppStore.getState();
    const allShafts = recon?.electrode_shafts || [];

    const rings = [];
    allShafts.forEach(shaft => {
      if (sv[shaft.id] === false || !shaft.visible) return;
      const isDepth = shaft.electrode_type === 'depth';
      const r = (isDepth ? 1.2 : 2.5) * (cs ?? 1.0);
      (shaft.contacts || []).forEach(c => {
        if (c.x_mm == null) return;
        if (isInsideMesh(new THREE.Vector3(c.x_mm, c.y_mm, c.z_mm), mesh)) {
          rings.push({ id: `${shaft.id}-${c.contact_number}`, pos: [c.x_mm, c.y_mm, c.z_mm], color: shaft.color, radius: r });
        }
      });
    });
    setHoveredStruct({ key, color, label, rings });
  }, []);

  const handleStructureUnhover = React.useCallback(() => setHoveredStruct(null), []);

  const shafts = reconstruction?.electrode_shafts || [];
  const selectedShaft = shafts.find(s => s.id === selectedShaftId) || null;

  // Contact placement works by clicking the CT mesh, which is only interactive in
  // place mode. In the hover modes the CT mesh is inert so hovers reach structures
  // and contacts, and placement is disabled.

  const ctOpacity = ctOpacityOverride != null
    ? ctOpacityOverride
    : ctThreshold != null
      ? (ctThreshold < 400 ? 0.6 : Math.min(1.0, 0.7 + (ctThreshold - 400) / 8666))
      : 0.6;

  return (
    <div style={{ width: '100%', height: '100%', background: '#0a0c10', position: 'relative' }}>
      <Canvas gl={{ antialias: true, alpha: false, logarithmicDepthBuffer: true }}>
        <PerspectiveCamera makeDefault fov={45} position={[0, 0, 300]} />
        <CameraSetup meshData={meshData} />
        <SceneLights />

        {loading && <LoadingOverlay message={loadingMessage} />}

        {/* MRI brain surface — optional */}
        {meshData && !loading && showMri && (
          <BrainMesh meshData={meshData} opacity={mriOpacity ?? brainOpacity} />
        )}

        {/* CT threshold mesh */}
        {/* Render every structure that has geometry, even when hidden: electrode-
            centric hover tests contacts against these meshes, so they must stay in
            the scene (registered + raycastable) regardless of the visibility toggle.
            Hidden ones are simply not drawn (visible=false). */}
        {structuresData && Object.entries(structuresData).map(([key, s]) =>
          s.vertices ? (
            <StructureMesh key={key} meshData={s} color={s.color}
              structKey={key} structLabel={s.label} opacity={structureOpacity ?? 0.45}
              visible={structureVisible?.[key] !== false}
              interactive={activeMode === 'structure'}
              onRegister={registerStructMesh}
              onHover={handleStructureHover} onUnhover={handleStructureUnhover} />
          ) : null
        )}

        {/* Contact highlight rings — structure-centric: all contacts inside hovered structure */}
        {hoveredStruct && hoveredStruct.rings.map(c => (
          <ContactRing key={c.id} position={c.pos} color={c.color} radius={c.radius} />
        ))}
        {/* Contact highlight ring — electrode-centric: the single hovered contact */}
        {hoveredContact && (
          <ContactRing position={hoveredContact.pos} color={hoveredContact.color} radius={hoveredContact.radius ?? 1.5} />
        )}
        {ctMeshData && (
          <CTArtifactMesh
            meshData={ctMeshData}
            isEditorMode={isEditorMode}
            onContactPlaced={onContactPlaced}
            selectedShaft={selectedShaft}
            opacity={ctOpacity}
            activeContactNumber={activeContactNumber}
            raycastable={placeMode}
          />
        )}

        {/* Electrode shafts */}
        {shafts.map(shaft => (
          <ElectrodeShaft key={shaft.id} shaft={shaft}
            onContactHover={handleContactHover} onContactUnhover={handleContactUnhover} />
        ))}

        <OrbitControls enablePan enableZoom enableRotate
          zoomSpeed={1.2} panSpeed={0.8} rotateSpeed={0.6}
          mouseButtons={{ LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN }}
        />
      </Canvas>

      {/* Place-mode badge — placement is active, hover is suspended */}
      {placeMode ? (
        <div
          style={{
            position: 'absolute', top: isEditorMode ? 56 : 16, left: 16,
            background: '#0d2a1acc', backdropFilter: 'blur(8px)',
            border: '1px solid #00e67655', borderRadius: 4, padding: '6px 12px',
            fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, pointerEvents: 'none',
            color: '#00e676', display: 'flex', alignItems: 'center', gap: 8,
          }}
        >
          <span>◎ PLACE CONTACTS MODE</span>
        </div>
      ) : structuresData && Object.keys(structuresData).length > 0 && (
        /* Hover-mode toggle — only meaningful once structures are loaded */
        <button
          onClick={() => {
            setHoveredStruct(null);
            setHoveredContact(null);
            setHoverMode(m => (m === 'structure' ? 'electrode' : 'structure'));
          }}
          title={hoverMode === 'structure'
            ? 'Structure-centric: hover a structure to highlight the contacts inside it'
            : 'Electrode-centric: hover a contact to name the structure it sits in'}
          style={{
            position: 'absolute', top: isEditorMode ? 56 : 16, left: 16,
            background: '#111418cc', backdropFilter: 'blur(8px)',
            border: '1px solid #2a3646', borderRadius: 4, padding: '6px 12px',
            fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, cursor: 'pointer',
            color: '#c8d4e0', display: 'flex', alignItems: 'center', gap: 8,
          }}
        >
          <span style={{ color: '#7a8a99' }}>Hover</span>
          <span style={{ color: '#74C0FC' }}>
            {hoverMode === 'structure' ? 'structure → contacts' : 'contact → structure'}
          </span>
          <span style={{ color: '#4a5568' }}>⇄</span>
        </button>
      )}

      {/* Electrode-centric hover tooltip — which structure(s) the contact sits in */}
      {hoveredContact && (
        <div style={{
          position: 'absolute', top: 16, right: 16, pointerEvents: 'none',
          background: 'rgba(10,12,16,0.92)', border: `1px solid ${hoveredContact.color}`,
          borderRadius: 4, padding: '6px 12px', maxWidth: 260,
          fontFamily: 'IBM Plex Mono, monospace', fontSize: 16.5,
          boxShadow: `0 0 12px ${hoveredContact.color}44`,
        }}>
          <div style={{ color: hoveredContact.color, marginBottom: 4 }}>
            {hoveredContact.label}
          </div>
          {hoveredContact.region ? (
            hoveredContact.region.inside ? (
              <div style={{ color: hoveredContact.region.color, fontSize: 15 }}>{hoveredContact.region.label}</div>
            ) : (
              <div>
                <div style={{ color: hoveredContact.region.color, fontSize: 15 }}>{hoveredContact.region.label}</div>
                <span style={{ color: '#7a8a99', fontSize: 13.5 }}>
                  nearest · {hoveredContact.region.dist.toFixed(1)} mm
                </span>
              </div>
            )
          ) : (
            <span style={{ color: '#7a8a99' }}>unlabelled</span>
          )}
        </div>
      )}

      {/* Structure hover tooltip */}
      {hoveredStruct && (
        <div style={{
          position: 'absolute', top: 16, right: 16, pointerEvents: 'none',
          background: 'rgba(10,12,16,0.92)', border: `1px solid ${hoveredStruct.color}`,
          borderRadius: 4, padding: '6px 12px',
          fontFamily: 'IBM Plex Mono, monospace', fontSize: 16.5,
          boxShadow: `0 0 12px ${hoveredStruct.color}44`,
        }}>
          <span style={{ color: hoveredStruct.color }}>{hoveredStruct.label}</span>
          <span style={{ color: '#7a8a99', marginLeft: 10 }}>
            {hoveredStruct.rings.length === 0
              ? 'no contacts inside'
              : `${hoveredStruct.rings.length} contact${hoveredStruct.rings.length !== 1 ? 's' : ''} inside`}
          </span>
        </div>
      )}

      {/* Control hints */}
      <div style={{
        position: 'absolute', bottom: 16, left: '50%', transform: 'translateX(-50%)',
        display: 'flex', gap: 20, color: '#4a5568',
        fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, letterSpacing: '0.05em', pointerEvents: 'none',
      }}>
        <span>LEFT DRAG — rotate</span>
        <span>RIGHT DRAG — pan</span>
        <span>SCROLL — zoom</span>
        {isEditorMode && placeMode && selectedShaft && activeContactNumber != null && <span style={{ color: '#ffdd00' }}>CLICK ELECTRODE — place {selectedShaft?.name}{activeContactNumber}</span>}
      </div>

      {/* Active shaft indicator */}
      {isEditorMode && (
        <div style={{
          position: 'absolute', top: 16, left: 16,
          background: '#111418cc', backdropFilter: 'blur(8px)',
          border: `1px solid ${selectedShaft ? selectedShaft.color : '#1e2530'}`,
          borderRadius: 4, padding: '6px 12px',
          fontFamily: 'IBM Plex Mono, monospace', fontSize: 10,
          color: selectedShaft ? selectedShaft.color : '#7a8a99',
          transition: 'border-color 0.2s, color 0.2s',
        }}>
          {selectedShaft
            ? activeContactNumber != null
              ? `Placing ${selectedShaft.name}${activeContactNumber}${selectedShaft.label ? ` (${selectedShaft.label})` : ''}`
              : `${selectedShaft.name}${selectedShaft.label ? ` — ${selectedShaft.label}` : ''} · tap a contact number →`
            : 'Select a shaft in the editor panel →'}
        </div>
      )}

      {/* CT loading indicator */}
      
    </div>
  );
}
