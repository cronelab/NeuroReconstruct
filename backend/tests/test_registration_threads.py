"""
Tests for how concurrent registrations share SimpleITK's thread count.
Self-contained: run directly with

    python backend/tests/test_registration_threads.py

(No pytest dependency in the neuro-recon env.) Builds its own volume, so it
needs no patient data and runs in a few seconds.

A registration's thread count can only be set through SimpleITK's process-wide
default (see _itk_threads in services/registration.py), and several
registrations can run in the web process at once. Each used to raise the default
and reset it to 1 when it finished, so the first to finish dropped every other
run to a single thread -- a precise re-run slowed about five-fold partway
through when an unrelated upload's registration completed.

What these cover:

  1. Runs wanting the same count share it until the LAST of them leaves.
  2. A run wanting a different count waits its turn, in arrival order.
  3. Real registrations side by side each run on the count they asked for,
     and a single-threaded one never overlaps a multithreaded one.
  4. A single-threaded registration repeats bit for bit. Metric sampling used to
     be seeded from the clock (SetMetricSamplingPercentage's default), so the
     "deterministic" re-run was nothing of the kind: three of PY26N013 landed up
     to 17 mm apart.
"""

import concurrent.futures
import os
import sys
import tempfile
import threading
import time

import numpy as np
import SimpleITK as sitk

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

import services.registration as registration   # noqa: E402
from services.registration import _itk_threads, register_ct_to_mri   # noqa: E402

SHAPE = (80, 72, 64)
SPACING = 2.0


def _default():
    return sitk.ProcessObject.GetGlobalDefaultNumberOfThreads()


def _until(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.005)


def _phantom_path(d):
    """A head-like volume with asymmetric features, written to d."""
    zz, yy, xx = np.mgrid[:SHAPE[0], :SHAPE[1], :SHAPE[2]].astype(np.float32)
    cz, cy, cx = [s / 2 for s in SHAPE]

    def ell(dz, dy, dx, rz, ry, rx):
        return (((zz - cz - dz) / rz) ** 2 +
                ((yy - cy - dy) / ry) ** 2 +
                ((xx - cx - dx) / rx) ** 2) <= 1.0

    vol = np.zeros(SHAPE, dtype=np.float32)
    vol[ell(0, 0, 0, 30, 26, 22) & ~ell(0, 0, 0, 27, 23, 19)] = 120.0
    vol[ell(0, 0, 0, 27, 23, 19)] = 600.0
    vol[ell(3, -5, 0, 8, 5, 4)] = 150.0
    vol[ell(-9, 8, 7, 5, 5, 5)] = 300.0
    vol += np.random.default_rng(0).normal(0, 4, SHAPE).astype(np.float32) * (vol > 0)

    img = sitk.GetImageFromArray(vol)
    img.SetSpacing((SPACING,) * 3)
    img.SetOrigin(tuple(-n * SPACING / 2 for n in reversed(SHAPE)))
    path = os.path.join(d, "head.nii.gz")
    sitk.WriteImage(img, path)
    return path


def _hold(n, entered, release, seen=None, name=None):
    with _itk_threads(n):
        if seen is not None:
            seen.append((name, _default()))
        entered.set()
        release.wait(10)


def test_shared_count_is_held_until_the_last_run_leaves():
    """The reported bug: the first run to finish reset the count under the other."""
    a_in, a_go, b_in, b_go = (threading.Event() for _ in range(4))
    a = threading.Thread(target=_hold, args=(8, a_in, a_go))
    b = threading.Thread(target=_hold, args=(8, b_in, b_go))
    a.start()
    assert a_in.wait(5)
    b.start()
    assert b_in.wait(5), "a run wanting the same count should share it, not wait"
    b_go.set()
    b.join(5)
    assert _default() == 8, f"count dropped to {_default()} while the other run was still going"
    a_go.set()
    a.join(5)
    assert _default() == 1, "count not restored after the last run left"
    print("test_shared_count_is_held_until_the_last_run_leaves OK")


def test_a_different_count_waits_its_turn():
    seen = []
    ev = {k: (threading.Event(), threading.Event()) for k in "ABC"}   # (entered, release)
    runs = {k: threading.Thread(target=_hold, args=(n, *ev[k], seen, k))
            for k, n in (("A", 8), ("B", 1), ("C", 8))}

    runs["A"].start()
    assert ev["A"][0].wait(5)
    runs["B"].start()
    _until(lambda: len(registration._threads_queue) == 1)    # B is in line behind A
    runs["C"].start()
    _until(lambda: len(registration._threads_queue) == 2)    # ...and C behind B
    assert not ev["B"][0].is_set(), "a 1-thread run started while an 8-thread run held the count"
    assert not ev["C"][0].is_set(), "a later 8-thread run jumped the queue"

    ev["A"][1].set()
    assert ev["B"][0].wait(5)
    assert not ev["C"][0].is_set()
    ev["B"][1].set()
    assert ev["C"][0].wait(5)
    ev["C"][1].set()
    for t in runs.values():
        t.join(5)
    assert seen == [("A", 8), ("B", 1), ("C", 8)], seen
    assert _default() == 1
    print("test_a_different_count_waits_its_turn OK")


def test_registrations_run_on_the_count_they_asked_for():
    """What the web process does: registrations of different counts at once."""
    runs, lock = [], threading.Lock()
    impl = registration._register_ct_to_mri_impl

    def spy(mri, ct, out, threads, init_jitter=None):
        start, t0 = _default(), time.perf_counter()
        try:
            impl(mri, ct, out, threads, init_jitter)
        except RuntimeError:
            # The CT settings drift on same-modality data and can wander off the
            # phantom entirely; only the thread count it ran on matters here.
            pass
        end, t1 = _default(), time.perf_counter()
        with lock:
            runs.append((threads, start, end, t0, t1))

    registration._register_ct_to_mri_impl = spy
    try:
        with tempfile.TemporaryDirectory() as d:
            head = _phantom_path(d)
            with concurrent.futures.ThreadPoolExecutor(3) as pool:
                jobs = [pool.submit(register_ct_to_mri, head, head, os.path.join(d, f"{i}.npy"), n)
                        for i, n in enumerate((8, 1, 8))]
                for j in jobs:
                    j.result()
    finally:
        registration._register_ct_to_mri_impl = impl

    assert sorted(r[0] for r in runs) == [1, 8, 8], runs
    for threads, start, end, _, _ in runs:
        assert start == end == threads, f"asked for {threads}, ran on {start}->{end}"
    single = next(r for r in runs if r[0] == 1)
    for r in runs:
        if r[0] == 8:
            assert r[4] <= single[3] or r[3] >= single[4], \
                "a single-threaded run overlapped a multithreaded one"
    assert _default() == 1
    print("test_registrations_run_on_the_count_they_asked_for OK")


def test_single_threaded_registration_repeats_exactly():
    with tempfile.TemporaryDirectory() as d:
        head = _phantom_path(d)
        img = sitk.ReadImage(head, sitk.sitkFloat32)
        pose = sitk.Euler3DTransform()
        pose.SetRotation(np.radians(1.0), 0.0, 0.0)
        pose.SetTranslation((2.0, 0.0, 0.0))
        moved = os.path.join(d, "moved.nii.gz")
        sitk.WriteImage(sitk.Resample(img, img, pose, sitk.sitkLinear, 0.0), moved)

        first, second = (register_ct_to_mri(head, moved, os.path.join(d, f"{i}.npy"), 1) for i in "ab")
        # Identity would mean nothing was optimised, and would repeat trivially.
        assert not np.allclose(first, np.eye(4)), "phantom did not register"
        assert np.array_equal(first, second), f"single-threaded runs differ:\n{first}\nvs\n{second}"
    print("test_single_threaded_registration_repeats_exactly OK")


if __name__ == "__main__":
    test_shared_count_is_held_until_the_last_run_leaves()
    test_a_different_count_waits_its_turn()
    test_registrations_run_on_the_count_they_asked_for()
    test_single_threaded_registration_repeats_exactly()
    print("\nAll registration-thread tests passed.")
