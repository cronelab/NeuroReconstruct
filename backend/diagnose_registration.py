"""
Diagnostic script — run from backend/ to inspect MRI and CT content.
Usage: python diagnose_registration.py data/recon_XXXX/mri.nii.gz data/recon_XXXX/ct.nii.gz
"""
import sys
import numpy as np
import nibabel as nib
import SimpleITK as sitk

def inspect(path, label):
    print(f"\n{'='*60}")
    print(f"  {label}: {path}")
    print(f"{'='*60}")

    # nibabel view
    nib_img = nib.load(path)
    nib_can = nib.as_closest_canonical(nib_img)
    data = nib_can.get_fdata()
    affine = nib_can.affine

    print(f"  Shape:        {data.shape}")
    print(f"  Dtype:        {data.dtype}")
    print(f"  Voxel sizes:  {np.sqrt((affine[:3,:3]**2).sum(axis=0)).round(3)}")
    print(f"  Origin (RAS): {affine[:3, 3].round(2)}")
    print(f"  Intensity min/max:  {data.min():.1f} / {data.max():.1f}")
    print(f"  Intensity mean/std: {data.mean():.1f} / {data.std():.1f}")
    print(f"  Percentiles [1,5,50,95,99]: "
          f"{np.percentile(data,[1,5,50,95,99]).round(1)}")
    print(f"  Non-zero voxels: {(data != 0).sum()} / {data.size} "
          f"({100*(data!=0).mean():.1f}%)")

    # qform/sform codes
    hdr = nib_img.header
    print(f"  qform_code: {hdr.get('qform_code', 'n/a')}  "
          f"sform_code: {hdr.get('sform_code', 'n/a')}")

    # SimpleITK view
    sitk_img = sitk.ReadImage(path, sitk.sitkFloat32)
    print(f"\n  SimpleITK size:      {sitk_img.GetSize()}")
    print(f"  SimpleITK spacing:   {[round(s,3) for s in sitk_img.GetSpacing()]}")
    print(f"  SimpleITK origin:    {[round(o,2) for o in sitk_img.GetOrigin()]}")
    print(f"  SimpleITK direction: {[round(d,3) for d in sitk_img.GetDirection()]}")

    stats = sitk.StatisticsImageFilter()
    stats.Execute(sitk_img)
    print(f"  SimpleITK intensity min/max: {stats.GetMinimum():.1f} / {stats.GetMaximum():.1f}")
    print(f"  SimpleITK intensity mean:    {stats.GetMean():.1f}")

if len(sys.argv) < 3:
    print("Usage: python diagnose_registration.py <mri.nii.gz> <ct.nii.gz>")
    sys.exit(1)

inspect(sys.argv[1], "MRI")
inspect(sys.argv[2], "CT")

# Check spatial overlap
print(f"\n{'='*60}")
print("  Spatial overlap check")
print(f"{'='*60}")
mri = sitk.ReadImage(sys.argv[1], sitk.sitkFloat32)
ct  = sitk.ReadImage(sys.argv[2], sitk.sitkFloat32)

def get_bounds(img):
    o = np.array(img.GetOrigin())
    sp = np.array(img.GetSpacing())
    sz = np.array(img.GetSize())
    return o, o + sp * sz

mri_lo, mri_hi = get_bounds(mri)
ct_lo,  ct_hi  = get_bounds(ct)
print(f"  MRI bounds (LPS): {mri_lo.round(1)} → {mri_hi.round(1)}")
print(f"  CT  bounds (LPS): {ct_lo.round(1)}  → {ct_hi.round(1)}")
overlap = np.all(mri_hi > ct_lo) and np.all(ct_hi > mri_lo)
print(f"  Bounding boxes overlap: {overlap}")
