"""
Reset a bad ct_to_mri.npy transform to identity.
Run from backend/ when CT was already co-registered to MRI.

Usage: python reset_transform.py data/recon_XXXX
"""
import sys, os, numpy as np

recon_dir = sys.argv[1] if len(sys.argv) > 1 else None
if not recon_dir:
    print("Usage: python reset_transform.py data/recon_XXXX")
    sys.exit(1)

path = os.path.join(recon_dir, "ct_to_mri.npy")
np.save(path, np.eye(4))
print(f"✓ Saved identity transform to {path}")

# Also clear the CT mesh cache so it re-renders with identity
cache_dir = os.path.join(recon_dir, "ct_cache")
if os.path.isdir(cache_dir):
    import shutil
    shutil.rmtree(cache_dir)
    print(f"✓ Cleared CT mesh cache at {cache_dir}")
