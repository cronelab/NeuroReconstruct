# NeuroReconstruct — sEEG / ECoG Brain Viewer

A web-based tool for creating and viewing virtual 3D brain reconstructions with
surgically implanted sEEG depth electrodes and ECoG grids/strips.

---

## Prerequisites (Windows)

- **Python 3.10+** — https://www.python.org/downloads/
- **Node.js 18+** — https://nodejs.org/
- **Git** (optional, for version control)

> If you don't already have Python, during installation check
> "Add Python to PATH".

---

## Setup — Backend

Open **Command Prompt** or **PowerShell** in the `backend/` folder:

```bat
cd backend

:: Create virtual environment
python -m venv venv

:: Activate it
venv\Scripts\activate

:: Install dependencies
pip install -r requirements.txt

:: Start the server
uvicorn main:app --reload --port 8000
```

The API will be available at http://localhost:8000
API docs (Swagger) at http://localhost:8000/docs

**Default admin account:**
- Username: `admin`
- Password: `changeme`
Change this after first login by registering a new admin user.

---

## Setup — Frontend

Open a **second** Command Prompt in the `frontend/` folder:

```bat
cd frontend

:: Install dependencies
npm install

:: Start dev server
npm start
```

The app will open at http://localhost:3000

---

## First Steps

1. Open http://localhost:3000
2. Log in with `admin` / `changeme`
3. Click **"New Reconstruction"**
4. Upload your de-identified MRI NIfTI file (`.nii` or `.nii.gz`)
5. Optionally upload the CT NIfTI for electrode localization
6. Wait for mesh extraction (background task, ~2–5 min depending on file size)
7. Once status shows **"ready"**, click the reconstruction to view the 3D brain

---

## Architecture

```
backend/
├── main.py                  # FastAPI app + all routes
├── database.py              # SQLAlchemy models + SQLite setup
├── auth.py                  # JWT auth + role management
├── requirements.txt
└── services/
    ├── mesh_extractor.py    # NIfTI → brain mesh (marching cubes)
    └── electrode_service.py # Spline fitting for auto-fill

frontend/
└── src/
    ├── App.jsx              # Root + routing
    ├── store.js             # Zustand global state
    ├── api.js               # Axios API client
    └── components/
        ├── Viewer3D.jsx         # Three.js 3D brain + electrodes
        ├── LayerPanel.jsx       # Opacity + visibility controls
        ├── Header.jsx           # Top bar + share link
        ├── ReconstructionList.jsx   # Patient list + upload
        └── ReconstructionViewer.jsx # Full viewer layout
```

---

## Electrode Types Supported

| Type   | Auto-fill Method                              | Min Manual Points |
|--------|-----------------------------------------------|-------------------|
| Depth  | 3D cubic spline + arc-length interpolation    | 3                 |
| Strip  | 3D spline (same algorithm, larger spacing)    | 3                 |
| Grid   | Bilinear interpolation from corner contacts   | 3                 |

---

## User Roles

| Role    | Can View | Can Create/Edit | Can Add Users |
|---------|----------|-----------------|---------------|
| viewer  | ✓        | ✗               | ✗             |
| editor  | ✓        | ✓               | ✗             |
| admin   | ✓        | ✓               | ✓             |

Register new users at: http://localhost:8000/docs#/default/register_api_auth_register_post

---

## 3D Viewer Controls

| Input         | Action    |
|---------------|-----------|
| Left drag     | Rotate    |
| Right drag    | Pan       |
| Scroll wheel  | Zoom      |
| Click contact | Select    |

---

## Notes on HIPAA / Data Security

- All NIfTI files should be **de-identified before upload** (no DICOM headers, no identifying metadata)
- The SQLite database and uploaded files live in `backend/data/` — keep this folder secure
- For production deployment on AWS, see: `docs/aws-deployment.md` (coming in Phase 2)
- VPN gating will be implemented at the AWS Security Group level in Phase 5

---

## Next Steps (Future Phases)

- [ ] CT slice viewer with electrode clicking (Niivue integration)
- [ ] ANTs co-registration pipeline (MRI + CT auto-alignment)
- [ ] FreeSurfer cortical surface reconstruction
- [ ] AWS deployment with JHU VPN IP allowlisting
- [ ] Multi-user concurrent editing with WebSocket sync
