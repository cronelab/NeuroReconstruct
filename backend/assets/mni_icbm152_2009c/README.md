# ICBM 2009c MNI152 template (MNI export target)

`mni_icbm152_t1_tal_nlin_asym_09c_brain.nii.gz` is the ICBM 2009c nonlinear
asymmetric MNI152 T1 (Fonov et al., NeuroImage 2011; 1 mm, 193 x 229 x 193,
origin -96, -132, -78), skull-stripped with FreeSurfer 8.2's `mri_synthstrip`
and scaled to uint8. Source: FreeSurfer 8.2
`average/mni_icbm152_nlin_asym_09c/mni_icbm152_t1_tal_nlin_asym_09c.nii.gz`,
originally from
https://www.bic.mni.mcgill.ca/~vfonov/icbm/2009/mni_icbm152_nlin_asym_09c_nifti.zip.
See `COPYING` for the MNI licence.

Its grid is the target of recon-all's SynthMorph registration
(`mri/transforms/synthmorph.1.0mm.1.0mm/`), so both MNI export methods in
`services/mni_registration.py` write `MRI_mni.nii.gz` / `CT_mni.nii.gz` on this
grid. The ANTs fallback registers the skull-stripped patient MRI to it.
