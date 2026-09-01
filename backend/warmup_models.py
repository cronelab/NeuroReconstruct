"""
Build-time warm-up: pull every pretrained network and template the pipeline
needs into the image layer.

Run from the Dockerfile. Without it the first reconstruction uploaded after each
container start pays a several-hundred-MB download in the middle of a request,
and fails outright if egress is restricted.

The ANTs MNI152 template doubles as the warm-up subject: it is a real T1 head,
it ships with ANTs, and mni_registration.py needs it at runtime anyway.

Each model runs in its OWN subprocess. TensorFlow does not return memory to the
OS between model loads, so running all three in one interpreter accumulates past
8 GB and the build agent OOM-kills it (exit 137) -- even though no single model
peaks above ~2.7 GB. One process per model keeps the high-water mark at the cost
of re-importing TensorFlow three times.
"""
import os
import subprocess
import sys

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

# mesh_extractor.py calls brain_extraction with modality t1 or t2 depending on
# the MRI the user uploads; structure_extractor.py calls DKT parcellation.
TASKS = ("t1", "t2", "dkt")


def run_task(task):
    """Exercise one model so its weights land in the cache. Child process."""
    import ants
    import antspynet

    img = ants.image_read(ants.get_ants_data("mni"))
    if task in ("t1", "t2"):
        antspynet.brain_extraction(img, modality=task, verbose=False)
    else:
        antspynet.desikan_killiany_tourville_labeling(
            img, do_preprocessing=True, verbose=False)


def main():
    print("[WARMUP] Fetching MNI152 template...", flush=True)
    import ants
    print(f"[WARMUP] Template: {ants.get_ants_data('mni')}", flush=True)

    for task in TASKS:
        print(f"[WARMUP] {task}...", flush=True)
        result = subprocess.run([sys.executable, __file__, "--task", task])
        if result.returncode != 0:
            hint = " (137 = OOM-killed; the build agent needs more memory)"                 if result.returncode == 137 else ""
            raise SystemExit(
                f"[WARMUP] FAILED: {task} exited {result.returncode}{hint}")

    cache = os.path.join(os.path.expanduser("~"), ".keras")
    n = sum(len(files) for _, _, files in os.walk(cache))
    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _, files in os.walk(cache) for f in files) / 1e6
    print(f"[WARMUP] Cached {n} file(s), {size:.0f} MB under {cache}", flush=True)
    if n == 0:
        raise SystemExit("[WARMUP] FAILED: no weights cached -- runtime would download mid-request")
    print("[WARMUP] Done.", flush=True)


if __name__ == "__main__":
    if "--task" in sys.argv:
        run_task(sys.argv[sys.argv.index("--task") + 1])
    else:
        main()
