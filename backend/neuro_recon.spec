# neuro_recon.spec  -- place in /backend, run with: pyinstaller neuro_recon.spec
import os
import glob
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

nibabel_datas    = collect_data_files("nibabel")
skimage_datas    = collect_data_files("skimage")
trimesh_datas    = collect_data_files("trimesh")
scipy_datas      = collect_data_files("scipy")
matplotlib_datas = collect_data_files("matplotlib")
ants_datas       = collect_data_files("ants")
antspynet_datas  = collect_data_files("antspynet")

frontend_build = os.path.join("..", "frontend", "build")

CONDA_BIN = r"C:\Users\DanCandrea\Anaconda3\envs\neuro-recon\Library\bin"

# Bundle only the native DLLs that PyInstaller misses on a clean machine.
# Do NOT include .pyd files here — PyInstaller collects those automatically.
extra_dlls = [
    "sqlite3.dll",
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
    "libbz2.dll",
    "ffi-8.dll",
    "liblzma.dll",
    "zlib.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
    "ucrtbase.dll",
]

binaries = []
for dll in extra_dlls:
    full = os.path.join(CONDA_BIN, dll)
    if os.path.exists(full):
        binaries.append((full, "."))

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=[
        (frontend_build, "frontend_build"),
        ("services", "services"),
    ] + nibabel_datas + skimage_datas + trimesh_datas + scipy_datas + matplotlib_datas + ants_datas + antspynet_datas,
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
        "ants","ants.plotting","antspynet",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["IPython","jupyter","PyQt5","PyQt6","tkinter","nilearn","sklearn"],
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
