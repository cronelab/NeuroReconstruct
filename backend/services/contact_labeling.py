"""
Contact -> brain-structure labeling.

Reports which patient-specific brain structure each electrode contact sits in,
using the DKT/deep_atropos label volume that ``structure_extractor`` caches as
``structures_cortical.nii.gz`` (native MRI space, no atlas registration).

Depth (sEEG) contacts frequently land in white matter between gyri -- on real
data only ~40-50% of contacts fall directly inside a labeled structure. Rather
than reporting those as simply "unlabeled", we look at the neighbourhood and
report the structure that occupies most of it, with the distance to it, so a
contact reads as e.g. "1.4 mm from Left Hippocampus".

Assignment rules:
  * contact voxel carries a catalog label -> that structure,
    ``distance_mm = 0``, ``vote_share = 1.0``
  * otherwise -> **plurality vote** among all catalog-labeled voxels within
    the search radius; the label holding the most voxels wins. Ties break
    toward the label with a voxel closest to the contact. ``vote_share`` is
    the winner's fraction of the votes, so a 0.9 assignment can be told apart
    from a marginal 0.35 one. ``distance_mm`` is the distance to the *winning*
    structure, not to the overall nearest voxel.
  * no catalog voxel within the radius -> unassigned

Voting rather than nearest-voxel because a single stray voxel of a neighbouring
parcel should not decide the label. All voxels have equal volume, so a vote
count is a volume share of the neighbourhood.

The search radius is deliberately tight (2 mm): a structure several millimetres
away is weak evidence about where a contact actually sits, and leaving such a
contact unassigned is more honest than naming a region it is not in. Contacts
with no structure inside the radius get an empty structure/distance and keep
whatever the voxel actually contains in ``voxel_content``.

Log output must stay ASCII (uvicorn stdout is cp1252 on Windows).
"""

import os

import numpy as np

from services.structure_extractor import ALL_STRUCTURES

# Labels that appear in the segmentation but are not anatomical structures in
# the catalog. Reported verbatim in the voxel_content column so nothing is lost.
NON_CATALOG_LABELS = {
    0:  "White matter / unlabeled",
    2:  "Left cerebral white matter",
    41: "Right cerebral white matter",
    4:  "Left lateral ventricle",
    43: "Right lateral ventricle",
    5:  "Left inferior lateral ventricle",
    44: "Right inferior lateral ventricle",
    14: "3rd ventricle",
    15: "4th ventricle",
    24: "CSF",
    31: "Left choroid plexus",
    63: "Right choroid plexus",
    77: "White matter hypointensity",
}

DEFAULT_SEARCH_RADIUS_MM = 2.0


def get_label_volume_path(recon_dir: str) -> str:
    """Path to the cached DKT label volume for a reconstruction."""
    return os.path.join(recon_dir, "structures_cortical.nii.gz")


def build_label_lookup() -> dict:
    """Map raw label index -> (structure_key, human label, group)."""
    lut = {}
    for key, info in ALL_STRUCTURES.items():
        for idx in info["labels"]:
            lut[int(idx)] = (key, info["label"], info["group"])
    return lut


def label_contacts(label_volume_path: str, contacts_world_ras,
                   search_radius_mm: float = DEFAULT_SEARCH_RADIUS_MM) -> list:
    """
    Label each contact with the structure it sits in (or the nearest one).

    Args:
        label_volume_path: cached ``structures_cortical.nii.gz``
        contacts_world_ras: (N, 3) contact positions in patient world RAS mm
        search_radius_mm: how far to look for a structure when the contact
                          voxel itself is unlabeled (white matter)

    Returns a list of dicts, one per contact, with keys:
        structure, structure_key, group, distance_mm, voxel_content
    """
    import nibabel as nib

    img = nib.load(label_volume_path)
    data = np.asarray(img.dataobj)
    # Label volumes are integer-valued but may be stored as float
    data = np.rint(data).astype(np.int32)
    affine = img.affine
    inv_affine = np.linalg.inv(affine)
    shape = np.array(data.shape)
    lut = build_label_lookup()

    # Voxel size per axis (columns of the affine may include rotation)
    vox_mm = np.linalg.norm(affine[:3, :3], axis=0)
    # Half-box in voxels needed to cover search_radius_mm on each axis
    half = np.maximum(np.ceil(search_radius_mm / np.maximum(vox_mm, 1e-6)), 1).astype(int)

    # Voxels belonging to a catalog structure -- the only candidates for
    # the nearest-structure fallback (ventricles/CSF/WM are not structures).
    catalog_labels = np.array(sorted(lut.keys()), dtype=np.int32)
    is_catalog = np.isin(data, catalog_labels)

    out = []
    pts = np.asarray(contacts_world_ras, dtype=np.float64).reshape(-1, 3)
    for p in pts:
        hom = np.array([p[0], p[1], p[2], 1.0])
        ijk = np.rint(inv_affine @ hom).astype(int)[:3]

        if np.any(ijk < 0) or np.any(ijk >= shape):
            out.append({
                "structure": "", "structure_key": "", "group": "",
                "distance_mm": None, "vote_share": None,
                "voxel_content": "Outside segmentation FOV",
            })
            continue

        raw = int(data[tuple(ijk)])
        if raw in lut:
            # Direct hit: the contact is inside this structure, no vote needed.
            key, label, group = lut[raw]
            out.append({
                "structure": label, "structure_key": key, "group": group,
                "distance_mm": 0.0, "vote_share": 1.0, "voxel_content": label,
            })
            continue

        voxel_content = NON_CATALOG_LABELS.get(raw, f"Unknown (label {raw})")

        # Fallback: plurality vote among catalog-labeled voxels within the radius.
        # Voting rather than nearest-voxel because a single stray voxel of a
        # neighbouring parcel should not decide the label -- the label that
        # occupies most of the neighbourhood is the better answer. Since all
        # voxels have equal volume, a vote count is a volume share.
        unassigned = {
            "structure": "", "structure_key": "", "group": "",
            "distance_mm": None, "vote_share": None, "voxel_content": voxel_content,
        }

        lo = np.maximum(ijk - half, 0)
        hi = np.minimum(ijk + half + 1, shape)
        sub_mask = is_catalog[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]
        if not sub_mask.any():
            out.append(unassigned)
            continue

        # World-space distance to each candidate (affine may be oblique), then
        # restrict the box of candidates to an actual sphere of search_radius_mm.
        idx = np.argwhere(sub_mask) + lo
        idx_hom = np.concatenate([idx, np.ones((len(idx), 1))], axis=1)
        world = (affine @ idx_hom.T).T[:, :3]
        d = np.linalg.norm(world - p, axis=1)
        within = d <= search_radius_mm
        if not within.any():
            out.append(unassigned)
            continue

        idx, d = idx[within], d[within]
        voter_labels = data[idx[:, 0], idx[:, 1], idx[:, 2]]

        uniq, counts = np.unique(voter_labels, return_counts=True)
        top = counts.max()
        tied = uniq[counts == top]
        if len(tied) == 1:
            winner = int(tied[0])
        else:
            # Deterministic tie-break: among tied labels, the one with a voxel
            # closest to the contact wins.
            winner = int(min(tied, key=lambda L: d[voter_labels == L].min()))

        key, label, group = lut[winner]
        out.append({
            "structure": label, "structure_key": key, "group": group,
            # distance to the winning structure, not to the overall nearest voxel
            "distance_mm": round(float(d[voter_labels == winner].min()), 2),
            "vote_share": round(float(top) / len(voter_labels), 2),
            "voxel_content": voxel_content,
        })

    return out


def write_contact_structure_csv(csv_path: str, contacts: list, labels: list) -> dict:
    """
    Write ``electrodes_structures.csv``.

    contacts: dicts with shaft_name / contact_number (same order as labels)
    labels:   output of ``label_contacts``

    Returns summary counts for the manifest/logs.
    """
    import csv as _csv

    n_inside = n_near = n_none = 0
    with open(csv_path, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["shaft_name", "contact_number", "structure", "group",
                    "distance_mm", "vote_share", "voxel_content"])
        for c, lab in zip(contacts, labels):
            d = lab["distance_mm"]
            if d == 0.0:
                n_inside += 1
            elif d is None:
                n_none += 1
            else:
                n_near += 1
            vs = lab.get("vote_share")
            w.writerow([
                c.get("shaft_name", ""), c.get("contact_number"),
                lab["structure"], lab["group"],
                "" if d is None else f"{d:.2f}",
                "" if vs is None else f"{vs:.2f}",
                lab["voxel_content"],
            ])

    return {"inside_structure": n_inside,
            "near_structure": n_near,
            "unassigned": n_none}
