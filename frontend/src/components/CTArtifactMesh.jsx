import React, { useMemo, useState } from 'react';
import * as THREE from 'three';

/**
 * Renders the CT threshold mesh as a white semi-transparent surface.
 * Opacity scales with the HU threshold:
 *   low threshold (full head) → very transparent so anatomy is visible but not overwhelming
 *   high threshold (metal only) → fully opaque so electrodes are crisp and distinct
 */
export default function CTArtifactMesh({ meshData, isEditorMode, onContactPlaced, selectedShaft, opacity = 0.5, activeContactNumber }) {
  const [hovered, setHovered] = useState(false);

  const geometry = useMemo(() => {
    if (!meshData?.vertices?.length) return null;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(meshData.vertices), 3));
    geo.setIndex(new THREE.BufferAttribute(new Uint32Array(meshData.faces), 1));
    geo.computeVertexNormals();
    return geo;
  }, [meshData]);

  console.log('[CTArtifactMesh] rendering, opacity=', opacity, 'geo=', geometry ? 'ok' : 'null', 'meshData=', meshData?.vertices?.length);
  if (!geometry) return null;

  const canPlace = isEditorMode && !!selectedShaft && activeContactNumber != null;

  const handleClick = (e) => {
    if (!canPlace) return;
    e.stopPropagation();
    const { x, y, z } = e.point;
    onContactPlaced?.({ x, y, z });
  };

  return (
    <mesh
      geometry={geometry}
      onClick={handleClick}
      onPointerOver={(e) => {
        e.stopPropagation();
        setHovered(true);
        document.body.style.cursor = canPlace ? 'crosshair' : 'default';
      }}
      onPointerOut={() => {
        setHovered(false);
        document.body.style.cursor = 'default';
      }}
      renderOrder={1}
    >
      <meshPhysicalMaterial
        color="#ffffff"
        emissive="#ffffff"
        emissiveIntensity={hovered && canPlace ? 0.3 : 0.0}
        roughness={0.4}
        metalness={0.1}
        transparent={true}
        opacity={hovered && canPlace ? Math.min(1, opacity + 0.15) : opacity}
        side={THREE.DoubleSide}
        depthWrite={opacity > 0.8}
      />
    </mesh>
  );
}
