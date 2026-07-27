# neuro_recon.spec  -- place in /backend, run with: pyinstaller neuro_recon.spec
import os
import sys
import glob
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

nibabel_datas    = collect_data_files("nibabel")
skimage_datas    = collect_data_files("skimage")
trimesh_datas    = collect_data_files("trimesh")
scipy_datas      = collect_data_files("scipy")
matplotlib_datas = collect_data_files("matplotlib")
ants_datas       = collect_data_files("ants")
# antspynet is deliberately NOT collected -- see the excludes list below.

frontend_build = os.path.join("..", "frontend", "build")

# Resolve the active conda env from the interpreter running PyInstaller, so the
# build works on any machine. Requires running under the neuro-recon env.
CONDA_BIN = os.path.join(sys.prefix, "Library", "bin")

# Bundle only the native DLLs that PyInstaller misses on a clean machine.
# Do NOT include .pyd files here — PyInstaller collects those automatically.
extra_dlls = [
    "sqlite3.dll",
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
    "libbz2.dll",
    "ffi-8.dll",
    # _ctypes.pyd / pyexpat.pyd link against the *unversioned* names, which
    # PyInstaller's dependency scan does not resolve -- bundle them explicitly
    # or the frozen exe dies at startup ("DLL load failed importing _ctypes").
    "ffi.dll",
    "libexpat.dll",
    "liblzma.dll",
    "zlib.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
    "ucrtbase.dll",
]

if not os.path.isdir(CONDA_BIN):
    raise SystemExit(
        f"Cannot find the conda Library\\bin directory at:\n  {CONDA_BIN}\n"
        "Activate the neuro-recon env before running PyInstaller "
        "(conda activate neuro-recon)."
    )

binaries = []
missing = []
for dll in extra_dlls:
    full = os.path.join(CONDA_BIN, dll)
    if os.path.exists(full):
        binaries.append((full, "."))
    else:
        missing.append(dll)

# Not fatal — DLL names vary between conda builds, and some resolve from the
# system instead. Surface them so a clean-machine failure is traceable.
if missing:
    print(f"[spec] WARNING: not bundled from {CONDA_BIN}: {', '.join(missing)}")

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=[
        (frontend_build, "frontend_build"),
        ("services", "services"),
    ] + nibabel_datas + skimage_datas + trimesh_datas + scipy_datas + matplotlib_datas + ants_datas,
    hiddenimports=[
        "uvicorn.logging","uvicorn.loops","uvicorn.loops.auto","uvicorn.loops.asyncio",
        "uvicorn.protocols","uvicorn.protocols.http","uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl","uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto","uvicorn.lifespan","uvicorn.lifespan.on",
        "starlette.routing","starlette.staticfiles",
        "multipart","multipart.multipart",
        "sqlalchemy.dialects.sqlite","sqlalchemy.dialects.sqlite.aiosqlite",
        "sqlalchemy.ext.asyncio","aiosqlite",
        "passlib","passlib.handlers","passlib.handlers.bcrypt","bcrypt",
        "jose","jose.jwt","jose.exceptions",
        "cryptography","cryptography.hazmat.primitives.asymmetric",
        "cryptography.hazmat.backends.openssl",
        "nibabel","nibabel.loadsave","nibabel.nifti1","nibabel.nifti2",
        "nibabel.orientations","nibabel.affines","nibabel.funcs",
        "nibabel.filebasedimages","nibabel.filename_parser",
        "nibabel.spm2analyze","nibabel.analyze","nibabel.spatialimages",
        "skimage","skimage.measure",
        "skimage.measure._marching_cubes_lewiner",
        "skimage.measure._marching_cubes_classic",
        "scipy.ndimage","scipy.ndimage._ni_support",
        "scipy.interpolate","scipy.interpolate._fitpack_impl",
        "scipy.spatial","scipy.sparse","scipy.linalg",
        "PIL","PIL.Image","PIL.ImageOps",
        "trimesh","trimesh.primitives","trimesh.creation","trimesh.smoothing",
        "numpy","numpy.core","numpy.core._multiarray_umath",
        "numpy.lib.stride_tricks",
        "anyio","anyio._backends._asyncio","h11","pydantic",
        "SimpleITK",
        "matplotlib","matplotlib.pyplot","matplotlib.backends",
        "matplotlib.backends.backend_agg",
        # ants only -- antspynet would pull in the whole TensorFlow stack.
        "ants","ants.plotting",
        # h5py -- required at runtime by the sEEG functional-mapping feature.
        "h5py","h5py.defs","h5py.utils","h5py._proxy","h5py.h5ac",
    ],
    hookspath=[],
    runtime_hooks=[],
    # antspynet (deep-learning skull stripping / parcellation) and its
    # TensorFlow stack are excluded: they account for the bulk of the bundle
    # size and the frozen build never reaches them. main.py borrows a donor
    # mesh and a cached structures volume when frozen, and every antspynet
    # import in services/ is function-local and guarded, so a missing module
    # degrades to the documented fallback instead of raising. CT->MRI
    # coregistration is SimpleITK-only and is unaffected; plain `ants` is
    # still bundled for the MNI export path.
    excludes=["IPython","jupyter","PyQt5","PyQt6","tkinter","nilearn","sklearn",
              "tensorflow","keras","antspynet","tensorboard"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="NeuroReconstruct",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    icon=None,
)
