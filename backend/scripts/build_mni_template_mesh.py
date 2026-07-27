"""
Build the shared MNI152 template brain surface, once, as a committed asset.

Run this at dev time (in the neuro-recon conda env, which has ANTs + skimage):

    python backend/scripts/build_mni_template_mesh.py

It resolves the MNI152 T1 template via the app's existing MNI helper, extracts a
brain surface with the same marching-cubes pipeline used for patient MRIs, and
writes data/mni152_brain_mesh.json. Serving that committed file means the runtime
(including the frozen .exe, which excludes ANTs/TensorFlow) never needs to compute
it. The mesh is origin-centered with its ``center`` recorded; the frontend offsets
MNI electrode coordinates by that center so points and surface share a frame.
"""

import os
import sys

# Allow "from services..." imports whether run from repo root or backend/.
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

from services.mni_registration import get_mni_template_path
from services.mesh_extractor import extract_brain_mesh


def main():
    out_path = os.path.join(_BACKEND, "data", "mni152_brain_mesh.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    template = get_mni_template_path()
    print(f"[MNI-MESH] Template: {template}")
    result = extract_brain_mesh(template, out_path, modality="t1")
    print(f"[MNI-MESH] Wrote {out_path}: "
          f"{result['vertex_count']} verts, {result['face_count']} faces, "
          f"center={result['center']}")


if __name__ == "__main__":
    main()
