"""Run a child process and report the peak memory it reached.

The expensive stages of this pipeline (brain extraction, DKT parcellation) run
in child processes so an out-of-memory kill costs one request instead of the
whole site. When one of them is killed the interesting question is always the
same: how close was it, and to what ceiling? Azure's plan-level metric answers
neither -- it samples once a minute, lags by several, and covers the whole VM
rather than this container.

So measure it here instead.
"""
import os
import subprocess
import threading


# One heavy child at a time, process-wide. Mesh extraction and DKT parcellation
# both load TensorFlow and both run to multiple gigabytes; two of them at once do
# not fit in this container, and they are reached by different endpoints, so the
# lock has to be shared rather than one per module.
HEAVY_JOB_LOCK = threading.Lock()


def _peak_rss(pid: int):
    """The child's high-water-mark RSS in bytes, or None if it has exited."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def memory_limit():
    """Bytes this container may use, or None if it cannot be determined.

    A container's own cgroup ceiling is the number that matters; the App Service
    plan's advertised size is shared with the platform's other processes.
    """
    for path in ("/sys/fs/cgroup/memory.max",                    # cgroup v2
                 "/sys/fs/cgroup/memory/memory.limit_in_bytes"):  # cgroup v1
        try:
            with open(path) as f:
                raw = f.read().strip()
        except OSError:
            continue
        if raw == "max":
            break
        try:
            value = int(raw)
        except ValueError:
            continue
        # cgroup v1 spells "unlimited" as a sentinel near 2**63.
        if value < (1 << 62):
            return value

    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def memory_in_use():
    """Bytes this container is currently using, or None."""
    for path in ("/sys/fs/cgroup/memory.current",                 # cgroup v2
                 "/sys/fs/cgroup/memory/memory.usage_in_bytes"):  # cgroup v1
        try:
            with open(path) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            continue
    return None


def memory_available():
    """Bytes still allocatable here, or None if it cannot be determined.

    Used to size work that would otherwise be sized on CPU count alone. Cache
    and other reclaimable pages count as in-use, so this errs low, which is the
    right direction for a limit that is enforced by a kill.
    """
    limit, used = memory_limit(), memory_in_use()
    if limit is None or used is None:
        return None
    return max(0, limit - used)


def describe_limit() -> str:
    limit = memory_limit()
    return f"{limit / 2**30:.2f} GB" if limit else "unknown"


# Called just before a heavy child is spawned, so the parent can release memory
# it is only holding for speed. A parcellation peaks near 13 GB against a 15.62 GB
# container, which leaves the parent almost nothing: whatever it is caching has to
# go first. Registered by main.py; empty everywhere else.
BEFORE_HEAVY_JOB = []


# Called just before a heavy child is spawned, so the parent can release memory
# it is only holding for speed. A parcellation peaks near 13 GB against a 15.62 GB
# container, which leaves the parent almost nothing: whatever it is caching has to
# go first. Registered by main.py; empty everywhere else.
BEFORE_HEAVY_JOB = []


def run_worker(cmd, cwd=None, env=None, poll: float = 1.0):
    """Run cmd to completion. Returns (returncode, peak_rss_bytes or None).

    stdout/stderr are inherited, so the child's log lines land in the same log
    as everything else.
    """
    for release in BEFORE_HEAVY_JOB:
        try:
            release()
        except Exception as e:                      # never block the real work
            print(f"[MEM] pre-job release failed: {e}")

    for release in BEFORE_HEAVY_JOB:
        try:
            release()
        except Exception as e:                      # never block the real work
            print(f"[MEM] pre-job release failed: {e}")

    proc = subprocess.Popen(cmd, cwd=cwd, env=env)
    peak = 0
    stop = threading.Event()

    def sample():
        nonlocal peak
        # VmHWM is the kernel's own high-water mark, so a one-second poll still
        # catches a spike that began and ended between two samples -- VmRSS
        # would not. The exception is the final allocation of a process the OOM
        # killer takes: that one may never be observed, so a reported peak is a
        # lower bound on what the child actually asked for.
        while not stop.wait(poll):
            value = _peak_rss(proc.pid)
            if value and value > peak:
                peak = value

    watcher = threading.Thread(target=sample, daemon=True)
    watcher.start()
    try:
        proc.wait()
    finally:
        stop.set()
        watcher.join(timeout=poll + 1)

    return proc.returncode, (peak or None)


def describe_outcome(returncode: int, peak) -> str:
    """One line about how a finished worker did, for the log."""
    where = f"peak {peak / 2**30:.2f} GB of {describe_limit()}" if peak \
        else f"peak unknown, limit {describe_limit()}"
    if returncode == 0:
        return f"worker finished ({where})"
    # 137 from a shell, -9 from Python's own view of the signal: both mean the
    # kernel killed it, which for this workload means out of memory.
    if returncode in (137, -9):
        return f"worker was killed by the OOM killer ({where})"
    return f"worker exited with {returncode} ({where})"
