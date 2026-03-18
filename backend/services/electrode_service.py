"""
Electrode contact localization via spline fitting.

Key design: contact NUMBER is used as the spline parameter.
If contacts 1, 3, 5 are placed manually, the spline is parameterized
at t=1, 3, 5. Missing contacts are INTERPOLATED between known points
using a cubic spline, and EXTRAPOLATED beyond known points using a
linear continuation of the terminal trajectory — avoiding the curl
that cubic splines produce outside their data range.
"""

import numpy as np
from scipy.interpolate import CubicSpline
from typing import List, Dict, Any


def fit_depth_electrode(
    manual_contacts: List[Dict],  # [{"contact_number": int, "position": [x,y,z]}, ...]
    n_total_contacts: int,
) -> List[Dict[str, Any]]:
    """
    Interpolate missing contacts using a cubic spline, but extrapolate
    contacts beyond the manual range using linear projection so that
    spacing is preserved even when the electrode tip enters a bolt.
    """
    if len(manual_contacts) < 2:
        raise ValueError("Need at least 2 manually placed contacts")

    placed = sorted(manual_contacts, key=lambda c: c["contact_number"])
    t_known = np.array([c["contact_number"] for c in placed], dtype=float)
    pts_known = np.array([c["position"] for c in placed], dtype=float)

    t_min = t_known[0]
    t_max = t_known[-1]
    placed_set = {int(c["contact_number"]) for c in placed}

    # Fit cubic spline over the known range only
    if len(placed) >= 3:
        cs_x = CubicSpline(t_known, pts_known[:, 0], bc_type='not-a-knot')
        cs_y = CubicSpline(t_known, pts_known[:, 1], bc_type='not-a-knot')
        cs_z = CubicSpline(t_known, pts_known[:, 2], bc_type='not-a-knot')
    else:
        # 2 points: linear only
        cs_x = cs_y = cs_z = None

    # ── Global least-squares line fit through ALL manual contacts ──────────
    # Fits x(t), y(t), z(t) each as a linear function of contact number t.
    # This gives the best-estimate trajectory direction and spacing using
    # every manually placed contact, not just the last two.
    # Used for extrapolation beyond the manual range.
    A = np.vstack([t_known, np.ones(len(t_known))]).T  # [t, 1] design matrix
    slope_x, intercept_x = np.linalg.lstsq(A, pts_known[:, 0], rcond=None)[0]
    slope_y, intercept_y = np.linalg.lstsq(A, pts_known[:, 1], rcond=None)[0]
    slope_z, intercept_z = np.linalg.lstsq(A, pts_known[:, 2], rcond=None)[0]

    def global_linear(t):
        return [
            slope_x * t + intercept_x,
            slope_y * t + intercept_y,
            slope_z * t + intercept_z,
        ]

    def interp(t):
        """Spline inside known range, global least-squares line outside."""
        if t < t_min or t > t_max:
            return global_linear(t)
        else:
            if cs_x is not None:
                return [float(cs_x(t)), float(cs_y(t)), float(cs_z(t))]
            else:
                alpha = (t - t_known[0]) / (t_known[-1] - t_known[0])
                return (pts_known[0] + alpha * (pts_known[-1] - pts_known[0])).tolist()

    results = []
    for num in range(1, n_total_contacts + 1):
        if num in placed_set:
            mc = next(c for c in placed if c["contact_number"] == num)
            results.append({
                "contact_number": num,
                "position": mc["position"],
                "is_manual": True,
            })
        else:
            results.append({
                "contact_number": num,
                "position": interp(float(num)),
                "is_manual": False,
            })

    return results


def fit_strip_electrode(
    manual_contacts: List[Dict],
    n_total_contacts: int,
) -> List[Dict[str, Any]]:
    return fit_depth_electrode(manual_contacts, n_total_contacts)


def fit_grid_electrode(
    manual_contacts: List[Dict],
    grid_rows: int,
    grid_cols: int,
) -> List[Dict[str, Any]]:
    n_total = grid_rows * grid_cols

    def num_to_rc(num):
        idx = num - 1
        return idx // grid_cols, idx % grid_cols

    placed = {c["contact_number"]: np.array(c["position"]) for c in manual_contacts}
    placed_rc = {num_to_rc(n): pos for n, pos in placed.items()}

    results = []
    for row in range(grid_rows):
        for col in range(grid_cols):
            num = row * grid_cols + col + 1
            if num in placed:
                results.append({
                    "contact_number": num,
                    "position": placed[num].tolist(),
                    "is_manual": True,
                })
                continue
            pos = _bilinear_interp(row, col, placed_rc)
            results.append({
                "contact_number": num,
                "position": pos,
                "is_manual": False,
            })

    return results


def _bilinear_interp(row, col, placed_rc):
    pts = list(placed_rc.items())
    if not pts:
        return [0.0, 0.0, 0.0]
    weights, positions = [], []
    for (r, c), pos in pts:
        d = np.sqrt((row - r) ** 2 + (col - c) ** 2)
        if d < 1e-6:
            return pos.tolist()
        weights.append(1.0 / d ** 2)
        positions.append(pos)
    weights = np.array(weights) / sum(weights)
    return sum(w * p for w, p in zip(weights, positions)).tolist()


def autofill_contacts(
    manual_contacts: List[Dict],
    electrode_type: str,
    n_total_contacts: int,
    grid_rows: int = 1,
    grid_cols: int = 1,
    spacing_mm: float = 3.5,  # ignored — distances come from actual placements
) -> List[Dict[str, Any]]:
    if len(manual_contacts) < 2:
        raise ValueError("Need at least 2 manually placed contacts to auto-fill")

    if electrode_type == "grid":
        return fit_grid_electrode(manual_contacts, grid_rows, grid_cols)
    elif electrode_type == "strip":
        return fit_strip_electrode(manual_contacts, n_total_contacts)
    else:
        return fit_depth_electrode(manual_contacts, n_total_contacts)
