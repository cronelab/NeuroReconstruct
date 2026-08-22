"""
Build-time warm-up: pull every pretrained network and template the pipeline
needs into the image layer.

Run from the Dockerfile. Without it the first reconstruction uploaded after each
container start pays a several-hundred-MB download in the middle of a request,
and fails outright if egress is restricted.

The ANTs MNI152 template doubles as the warm-up subject: it is a real T1 head,
it ships with ANTs, and mni_registration.py needs it at runtime anyway.
"""
import os

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

import ants
import antspynet

print("[WARMUP] Fetching MNI152 template...", flush=True)
mni_path = ants.get_ants_data("mni")
img = ants.image_read(mni_path)
print(f"[WARMUP] Template: {mni_path}", flush=True)

# mesh_extractor.py calls brain_extraction with modality t1 or t2 depending on
# the MRI the user uploads -- cache both networks.
for modality in ("t1", "t2"):
    print(f"[WARMUP] brain_extraction(modality={modality})...", flush=True)
    antspynet.brain_extraction(img, modality=modality, verbose=False)

# structure_extractor.py -> DKT cortical parcellation.
print("[WARMUP] desikan_killiany_tourville_labeling()...", flush=True)
antspynet.desikan_killiany_tourville_labeling(img, do_preprocessing=True, verbose=False)

cache = os.path.join(os.path.expanduser("~"), ".keras")
n = sum(len(files) for _, _, files in os.walk(cache))
size = sum(os.path.getsize(os.path.join(r, f))
           for r, _, files in os.walk(cache) for f in files) / 1e6
print(f"[WARMUP] Cached {n} file(s), {size:.0f} MB under {cache}", flush=True)
if n == 0:
    raise SystemExit("[WARMUP] FAILED: no weights cached -- runtime would download mid-request")
print("[WARMUP] Done.", flush=True)
